import streamlit as st
import pandas as pd
import gspread
import unicodedata
from datetime import datetime, date, timedelta
from google.oauth2.service_account import Credentials

st.set_page_config(page_title="Monitor de Etapas de Trabalho", layout="wide")
st.title("📊 Monitor de Etapas de Trabalho")

# Conexão com Google Sheets
@st.cache_resource
def conectar_google_sheets():
    scopes = ["https://www.googleapis.com/auth/spreadsheets.readonly"]
    credentials_info = st.secrets["gcp_service_account"]
    credentials = Credentials.from_service_account_info(credentials_info, scopes=scopes)
    return gspread.authorize(credentials)

try:
    client = conectar_google_sheets()
except Exception:
    st.error("Erro ao conectar com as credenciais do Google Sheets. Verifique os Secrets.")
    st.stop()

PLANILHAS_URLS = [
    "https://docs.google.com/spreadsheets/d/1ym-kHhuaW1pD5KNXzrmgY2QaUSol339R4fCHdGRS3K8/edit?usp=sharing",
    "https://docs.google.com/spreadsheets/d/1zRkVSttkkpqekEdXjGPlz3-Dl7NzgqnkbGioJGuAdRY/edit?usp=sharing",
]

def normalizar_texto(texto):
    """Remove acentos, espaços extras e converte para maiúsculas."""
    if not isinstance(texto, str):
        texto = str(texto)
    texto = unicodedata.normalize('NFD', texto).encode('ascii', 'ignore').decode('utf-8')
    return texto.strip().upper()

def renomear_duplicadas(colunas):
    """Trata colunas duplicadas adicionando sufixo incremental: STATUS, STATUS_2..."""
    vistos = {}
    novas_colunas = []
    for col in colunas:
        col_clean = col.strip()
        if col_clean in vistos:
            vistos[col_clean] += 1
            novas_colunas.append(f"{col_clean}_{vistos[col_clean]}")
        else:
            vistos[col_clean] = 1
            novas_colunas.append(col_clean)
    return novas_colunas

def processar_planilhas_em_tempo_real(urls):
    dados_totais = []

    alvos = {
        "REFERENCIA": "REFERÊNCIA",
        "DIGITADOR": "DIGITADOR",
        "STATUS": "STATUS",
        "IMPORTADOR": "IMPORTADOR",
        "DATA ATUALIZACAO": "DATA ATUALIZAÇÃO",
        "ENVIO P/ DIGITACAO": "ENVIO P/ DIGITAÇÃO",
        "ENVIO PARA DIGITACAO": "ENVIO P/ DIGITAÇÃO"
    }

    for url in urls:
        try:
            doc = client.open_by_url(url)
            for worksheet in doc.worksheets():
                rows = worksheet.get_all_values()
                if not rows or len(rows) < 2:
                    continue

                header_idx = -1
                for i, row in enumerate(rows[:15]):
                    row_norm = [normalizar_texto(cell) for cell in row]
                    if any("STATUS" in cell for cell in row_norm) or any("REFERENCIA" in cell for cell in row_norm):
                        header_idx = i
                        break

                if header_idx == -1:
                    continue

                raw_header = rows[header_idx]
                data_rows = rows[header_idx + 1:]

                header_renomeado = renomear_duplicadas(raw_header)
                df = pd.DataFrame(data_rows, columns=header_renomeado)

                col_map = {}
                for col_orig in df.columns:
                    col_norm = normalizar_texto(col_orig)
                    for chave_norm, nome_padrao in alvos.items():
                        if chave_norm in col_norm:
                            sufixo = col_orig.replace(col_orig.split('_')[0], '') if '_' in col_orig else ''
                            col_map[col_orig] = f"{nome_padrao}{sufixo}"
                            break

                if col_map:
                    df = df.rename(columns=col_map)
                    cols_para_manter = list(col_map.values())
                    df_filtrado = df[cols_para_manter].copy()
                    df_filtrado["ORIGEM"] = f"{doc.title} - {worksheet.title}"
                    dados_totais.append(df_filtrado)

        except Exception as err:
            st.warning(f"Aviso ao ler a planilha ({url}): {err}")

    if dados_totais:
        return pd.concat(dados_totais, ignore_index=True, sort=False)
    return pd.DataFrame()

# --- BARRA LATERAL (PAINEL DE CONTROLE) ---
st.sidebar.header("⚙️ Painel de Controle")

if st.sidebar.button("🔄 Recarregar Dados Agora"):
    st.rerun()

# 1. Filtro por Status
status_selecionado = st.sidebar.selectbox(
    "Filtrar por Status:", 
    ["EM DIGITAÇÃO", "DIGITADO", "FINALIZADO", "TODOS"], 
    index=0
)

st.sidebar.markdown("---")

# 2. Filtro por Período
st.sidebar.subheader("📅 Período")
opcao_periodo = st.sidebar.selectbox(
    "Selecione o intervalo:",
    ["Todo o tempo", "Hoje", "Últimos 7 dias", "Últimos 30 dias", "Este Mês", "Personalizado"],
    index=0
)

data_inicio = None
data_fim = None
hoje = date.today()

if opcao_periodo == "Hoje":
    data_inicio = hoje
    data_fim = hoje
elif opcao_periodo == "Últimos 7 dias":
    data_inicio = hoje - timedelta(days=7)
    data_fim = hoje
elif opcao_periodo == "Últimos 30 dias":
    data_inicio = hoje - timedelta(days=30)
    data_fim = hoje
elif opcao_periodo == "Este Mês":
    data_inicio = hoje.replace(day=1)
    data_fim = hoje
elif opcao_periodo == "Personalizado":
    intervalo = st.sidebar.date_input("Escolha o período:", value=(hoje - timedelta(days=7), hoje))
    if isinstance(intervalo, tuple) and len(intervalo) == 2:
        data_inicio, data_fim = intervalo

# --- EXECUÇÃO E FILTRAGEM ---
with st.spinner("Puxando dados atualizados do Google Sheets..."):
    df_completo = processar_planilhas_em_tempo_real(PLANILHAS_URLS)

if df_completo.empty:
    st.info("Nenhum dado localizado. Verifique se as planilhas contêm os termos de busca no cabeçalho.")
else:
    # Identifica colunas referentes a datas e converte para datetime
    cols_data = [c for c in df_completo.columns if "DATA ATUALIZAÇÃO" in c or "ENVIO P/ DIGITAÇÃO" in c]
    
    # Cria uma coluna de data principal tratada para o filtro
    def extrair_data_valida(row):
        for c in cols_data:
            val = str(row[c]).strip()
            if val:
                # Tenta converter strings comuns de data no formato BR (dd/mm/yyyy)
                try:
                    return pd.to_datetime(val, dayfirst=True, errors='coerce').date()
                except Exception:
                    continue
        return None

    df_completo["DATA_FILTRO"] = df_completo.apply(extrair_data_valida, axis=1)

    # Aplicação do Filtro de Período (se selecionado)
    if opcao_periodo != "Todo o tempo" and data_inicio and data_fim:
        df_periodo = df_completo[
            (df_completo["DATA_FILTRO"] >= data_inicio) & 
            (df_completo["DATA_FILTRO"] <= data_fim)
        ]
    else:
        df_periodo = df_completo.copy()

    # Identifica colunas de status
    cols_status = [c for c in df_periodo.columns if "STATUS" in c]

    def checar_status_linha(row, termo):
        for col in cols_status:
            val = normalizar_texto(row[col])
            if termo in val:
                return True
        return False

    # Filtros calculados SOBRE O PERÍODO SELECIONADO para atualizar os totais
    em_dig = df_periodo[df_periodo.apply(lambda r: checar_status_linha(r, "EM DIGITA"), axis=1)]
    digitado = df_periodo[df_periodo.apply(lambda r: checar_status_linha(r, "DIGITADO") and not checar_status_linha(r, "EM DIGITA"), axis=1)]
    finalizado = df_periodo[df_periodo.apply(lambda r: checar_status_linha(r, "FINALIZAD"), axis=1)]

    # --- MÉTRICAS REATIVAS (ATUALIZAM COM O PERÍODO) ---
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total Registros", len(df_periodo))
    c2.metric("Em Digitação", len(em_dig))
    c3.metric("Digitado", len(digitado))
    c4.metric("Finalizado", len(finalizado))

    st.markdown("---")

    # Seleção da tabela de exibição
    if status_selecionado == "EM DIGITAÇÃO":
        df_exibir = em_dig
    elif status_selecionado == "DIGITADO":
        df_exibir = digitado
    elif status_selecionado == "FINALIZADO":
        df_exibir = finalizado
    else:
        df_exibir = df_periodo

    # Título dinâmico indicando período e status
    txt_periodo = f"({data_inicio.strftime('%d/%m/%Y')} até {data_fim.strftime('%d/%m/%Y')})" if data_inicio and data_fim and opcao_periodo != "Todo o tempo" else "(Todo o tempo)"
    st.subheader(f"📌 Registros - {status_selecionado} {txt_periodo}")

    # Remove a coluna temporária usada no filtro de data da visualização final
    df_exibir_clean = df_exibir.drop(columns=["DATA_FILTRO"], errors="ignore").dropna(how="all")

    st.dataframe(
        df_exibir_clean,
        use_container_width=True,
        hide_index=True
    )
