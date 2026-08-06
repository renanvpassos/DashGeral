import streamlit as st
import pandas as pd
import gspread
import unicodedata
from google.oauth2.service_account import Credentials

st.set_page_config(page_title="Monitor de Etapas de Trabalho", layout="wide")
st.title("📊 Monitor de Etapas de Trabalho")

# Função de conexão
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
]

def normalizar_texto(texto):
    """Remove acentos, espaços extras e converte para maiúsculas."""
    if not isinstance(texto, str):
        texto = str(texto)
    texto = unicodedata.normalize('NFD', texto).encode('ascii', 'ignore').decode('utf-8')
    return texto.strip().upper()

@st.cache_data(ttl=300)
def processar_planilhas(urls):
    dados_totais = []

    # Dicionário mapeando o nome padronizado para o nome final desejado
    colunas_alvo = {
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

                # 1. Encontra a linha do cabeçalho (procura por STATUS ou REFERENCIA nas 10 primeiras linhas)
                header_idx = -1
                for i, row in enumerate(rows[:10]):
                    row_norm = [normalizar_texto(cell) for cell in row]
                    if any("STATUS" in cell for cell in row_norm) or any("REFERENCIA" in cell for cell in row_norm):
                        header_idx = i
                        break

                if header_idx == -1:
                    continue

                # 2. Cria o DataFrame
                raw_header = rows[header_idx]
                df = pd.DataFrame(rows[header_idx + 1:], columns=raw_header)

                # --- CORREÇÃO DO ERRO ---
                # Remove colunas com nomes exatamente iguais (mantém apenas a primeira ocorrência)
                df = df.loc[:, ~df.columns.duplicated()].copy()

                # 3. Mapeamento das colunas encontradas
                col_map = {}
                for col_original in df.columns:
                    col_norm = normalizar_texto(col_original)
                    for chave_norm, nome_final in colunas_alvo.items():
                        if chave_norm in col_norm and nome_final not in col_map.values():
                            col_map[col_original] = nome_final
                            break

                if "STATUS" in col_map.values():
                    df = df.rename(columns=col_map)
                    
                    # Seleciona apenas as colunas mapeadas
                    colunas_presentes = [c for c in list(set(colunas_alvo.values())) if c in df.columns]
                    df_filtrado = df[colunas_presentes].copy()

                    # Adiciona origem
                    df_filtrado["ORIGEM"] = f"{doc.title} - {worksheet.title}"
                    
                    # Garante que STATUS seja tratado como Series (1 única coluna)
                    status_col = df_filtrado["STATUS"]
                    if isinstance(status_col, pd.DataFrame):
                        status_col = status_col.iloc[:, 0]

                    # Filtra linhas onde o status não seja vazio
                    df_filtrado = df_filtrado[status_col.astype(str).str.strip() != ""]
                    
                    dados_totais.append(df_filtrado)

        except Exception as err:
            st.warning(f"Erro ao processar a planilha {url}: {err}")

    if dados_totais:
        # Combina os DataFrames e remove colunas duplicadas resultantes do concat se houver
        df_final = pd.concat(dados_totais, ignore_index=True)
        return df_final.loc[:, ~df_final.columns.duplicated()]
    return pd.DataFrame()

# Processamento dos dados
with st.spinner("Lendo e cruzando dados das planilhas..."):
    df_completo = processar_planilhas(PLANILHAS_URLS)

if df_completo.empty:
    st.info("Nenhum dado encontrado. Certifique-se de que as planilhas contêm colunas com 'STATUS' ou 'REFERÊNCIA'.")
else:
    # Garante que a coluna STATUS seja tratada corretamente como Series
    status_series = df_completo["STATUS"]
    if isinstance(status_series, pd.DataFrame):
        status_series = status_series.iloc[:, 0]

    # Cria coluna de status normalizada
    df_completo["STATUS_NORM"] = status_series.apply(normalizar_texto)

    # Métricas
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total de Registros", len(df_completo))
    c2.metric("Em Digitação", len(df_completo[df_completo["STATUS_NORM"].str.contains("EM DIGITA", na=False)]))
    c3.metric("Digitado", len(df_completo[df_completo["STATUS_NORM"].str.contains("DIGITADO", na=False) & ~df_completo["STATUS_NORM"].str.contains("EM DIGITA", na=False)]))
    c4.metric("Finalizado", len(df_completo[df_completo["STATUS_NORM"].str.contains("FINALIZAD", na=False)]))

    st.markdown("---")

    # Filtro de Status na barra lateral
    status_opcoes = ["TODOS", "EM DIGITAÇÃO", "DIGITADO", "FINALIZADO"]
    status_selecionado = st.sidebar.selectbox("Filtrar por Status:", status_opcoes, index=1)

    if status_selecionado == "EM DIGITAÇÃO":
        df_exibir = df_completo[df_completo["STATUS_NORM"].str.contains("EM DIGITA", na=False)]
    elif status_selecionado == "DIGITADO":
        df_exibir = df_completo[df_completo["STATUS_NORM"].str.contains("DIGITADO", na=False) & ~df_completo["STATUS_NORM"].str.contains("EM DIGITA", na=False)]
    elif status_selecionado == "FINALIZADO":
        df_exibir = df_completo[df_completo["STATUS_NORM"].str.contains("FINALIZAD", na=False)]
    else:
        df_exibir = df_completo

    st.subheader(f"📌 Registros - {status_selecionado}")

    # Colunas em ordem de preferência
    colunas_ordenadas = ["ORIGEM", "REFERÊNCIA", "IMPORTADOR", "DIGITADOR", "STATUS", "ENVIO P/ DIGITAÇÃO", "DATA ATUALIZAÇÃO"]
    colunas_finais = [col for col in colunas_ordenadas if col in df_exibir.columns]

    st.dataframe(
        df_exibir[colunas_finais],
        use_container_width=True,
        hide_index=True
    )
