import streamlit as st
import pandas as pd
import gspread
import unicodedata
from google.oauth2.service_account import Credentials

st.set_page_config(page_title="Monitor de Etapas de Trabalho", layout="wide")
st.title("📊 Monitor de Etapas de Trabalho")

# Conexão com Google Sheets (O cliente de conexão permanece em cache leve para performance)
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
    # Adicione aqui mais links de planilhas se necessário
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

# REMOVIDO O @st.cache_data PARA FORÇAR A LEITURA EM TEMPO REAL
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

                # Localiza a linha do cabeçalho
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

# --- BARRA LATERAL ---
st.sidebar.header("Painel de Controle")

# Botão manual para recarregar caso queira atualizar sem mudar de menu
if st.sidebar.button("🔄 Recarregar Dados Agora"):
    st.rerun()

status_selecionado = st.sidebar.selectbox("Filtrar por Status:", ["EM DIGITAÇÃO", "DIGITADO", "FINALIZADO", "TODOS"], index=0)

# --- EXECUÇÃO DA LEITURA (RODA A CADA AÇÃO/MUDANÇA DE MENU) ---
with st.spinner("Puxando dados atualizados do Google Sheets..."):
    df_completo = processar_planilhas_em_tempo_real(PLANILHAS_URLS)

if df_completo.empty:
    st.info("Nenhum dado localizado. Verifique se as planilhas contêm os termos de busca no cabeçalho.")
else:
    cols_status = [c for c in df_completo.columns if "STATUS" in c]

    def checar_status_linha(row, termo):
        for col in cols_status:
            val = normalizar_texto(row[col])
            if termo in val:
                return True
        return False

    em_dig = df_completo[df_completo.apply(lambda r: checar_status_linha(r, "EM DIGITA"), axis=1)]
    digitado = df_completo[df_completo.apply(lambda r: checar_status_linha(r, "DIGITADO") and not checar_status_linha(r, "EM DIGITA"), axis=1)]
    finalizado = df_completo[df_completo.apply(lambda r: checar_status_linha(r, "FINALIZAD"), axis=1)]

    # Métricas
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total Registros", len(df_completo))
    c2.metric("Em Digitação", len(em_dig))
    c3.metric("Digitado", len(digitado))
    c4.metric("Finalizado", len(finalizado))

    st.markdown("---")

    if status_selecionado == "EM DIGITAÇÃO":
        df_exibir = em_dig
    elif status_selecionado == "DIGITADO":
        df_exibir = digitado
    elif status_selecionado == "FINALIZADO":
        df_exibir = finalizado
    else:
        df_exibir = df_completo

    st.subheader(f"📌 Registros Encontrados - {status_selecionado}")

    df_exibir = df_exibir.dropna(how="all")

    st.dataframe(
        df_exibir,
        use_container_width=True,
        hide_index=True
    )
