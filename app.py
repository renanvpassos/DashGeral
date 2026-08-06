import streamlit as st
import pandas as pd
import gspread
import unicodedata
from google.oauth2.service_account import Credentials

st.set_page_config(page_title="Monitor de Etapas de Trabalho", layout="wide")
st.title("📊 Monitor de Etapas de Trabalho")

# Conexão via Secrets do Streamlit Cloud
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

# Adicione aqui todas as URLs das suas planilhas
PLANILHAS_URLS = [
    "https://docs.google.com/spreadsheets/d/1ym-kHhuaW1pD5KNXzrmgY2QaUSol339R4fCHdGRS3K8/edit?usp=sharing",
    # "https://docs.google.com/spreadsheets/d/OUTRA_PLANILHA/edit",
]

def normalizar_texto(texto):
    """Remove acentos, espaços extras e converte para maiúsculas."""
    if not isinstance(texto, str):
        texto = str(texto)
    texto = unicodedata.normalize('NFD', texto).encode('ascii', 'ignore').decode('utf-8')
    return texto.strip().upper()

def renomear_duplicadas(colunas):
    """Trata colunas duplicadas adicionando sufixo incremental: STATUS, STATUS_2, STATUS_3..."""
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

@st.cache_data(ttl=300)
def processar_planilhas(urls):
    dados_totais = []

    # Lista de termos chave para identificar as colunas desejadas
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

                # 1. Localiza a linha do cabeçalho
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

                # 2. Renomeia colunas duplicadas no cabeçalho original
                header_renomeado = renomear_duplicadas(raw_header)
                df = pd.DataFrame(data_rows, columns=header_renomeado)

                # 3. Mapeia e identifica todas as colunas de interesse
                col_map = {}
                for col_orig in df.columns:
                    col_norm = normalizar_texto(col_orig)
                    for chave_norm, nome_padrao in alvos.items():
                        if chave_norm in col_norm:
                            # Preserva a informação se for duplicada (ex: STATUS_2)
                            sufixo = col_orig.replace(col_orig.split('_')[0], '') if '_' in col_orig else ''
                            col_map[col_orig] = f"{nome_padrao}{sufixo}"
                            break

                if col_map:
                    df = df.rename(columns=col_map)
                    
                    # Seleciona apenas as colunas mapeadas
                    cols_para_manter = list(col_map.values())
                    df_filtrado = df[cols_para_manter].copy()
                    
                    # Adiciona origem (Nome do arquivo + Aba)
                    df_filtrado["ORIGEM"] = f"{doc.title} - {worksheet.title}"
                    dados_totais.append(df_filtrado)

        except Exception as err:
            st.warning(f"Aviso ao ler a planilha ({url}): {err}")

    if dados_totais:
        # Junta todas as tabelas de todas as abas e planilhas
        return pd.concat(dados_totais, ignore_index=True, sort=False)
    return pd.DataFrame()

# Executa o carregamento dos dados
with st.spinner("Lendo todas as planilhas e consolidando colunas..."):
    df_completo = processar_planilhas(PLANILHAS_URLS)

if df_completo.empty:
    st.info("Nenhum dado localizado. Verifique os links das planilhas ou se os cabeçalhos contêm os termos de busca.")
else:
    # Identifica todas as colunas referentes ao STATUS (ex: STATUS, STATUS_2, etc.)
    cols_status = [c for c in df_completo.columns if "STATUS" in c]

    # Função para verificar se algum dos status da linha corresponde ao filtro
    def checar_status_linha(row, termo):
        for col in cols_status:
            val = normalizar_texto(row[col])
            if termo in val:
                return True
        return False

    # Filtros para contagem das métricas
    em_dig = df_completo[df_completo.apply(lambda r: checar_status_linha(r, "EM DIGITA"), axis=1)]
    digitado = df_completo[df_completo.apply(lambda r: checar_status_linha(r, "DIGITADO") and not checar_status_linha(r, "EM DIGITA"), axis=1)]
    finalizado = df_completo[df_completo.apply(lambda r: checar_status_linha(r, "FINALIZAD"), axis=1)]

    # Métricas gerais no topo
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total Registros", len(df_completo))
    c2.metric("Em Digitação", len(em_dig))
    c3.metric("Digitado", len(digitado))
    c4.metric("Finalizado", len(finalizado))

    st.markdown("---")

    # Menu lateral
    status_selecionado = st.sidebar.selectbox("Filtrar por Status:", ["EM DIGITAÇÃO", "DIGITADO", "FINALIZADO", "TODOS"], index=0)

    if status_selecionado == "EM DIGITAÇÃO":
        df_exibir = em_dig
    elif status_selecionado == "DIGITADO":
        df_exibir = digitado
    elif status_selecionado == "FINALIZADO":
        df_exibir = finalizado
    else:
        df_exibir = df_completo

    st.subheader(f"📌 Registros Encontrados - {status_selecionado}")

    # Remove linhas completamente vazias no DataFrame de exibição
    df_exibir = df_exibir.dropna(how="all")

    st.dataframe(
        df_exibir,
        use_container_width=True,
        hide_index=True
    )
