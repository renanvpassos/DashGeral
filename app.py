import streamlit as st
import pandas as pd
import gspread
from google.oauth2.service_account import Credentials

# 1. Configurações de página no Streamlit
st.set_page_config(page_title="Monitor de Etapas de Trabalho", layout="wide")
st.title("📊 Monitor de Etapas de Trabalho")

# 2. Conexão com Google Sheets via Secrets do Streamlit Cloud
@st.cache_resource
def conectar_google_sheets():
    # Define os escopos necessários
    scopes = ["https://www.googleapis.com/auth/spreadsheets.readonly"]
    
    # Carrega as credenciais vindas do st.secrets
    credentials_info = st.secrets["gcp_service_account"]
    credentials = Credentials.from_service_account_info(credentials_info, scopes=scopes)
    
    client = gspread.authorize(credentials)
    return client

try:
    client = conectar_google_sheets()
except Exception as e:
    st.error("Erro ao conectar com as credenciais do Google Sheets. Verifique os Secrets.")
    st.stop()

# 3. Lista de URLs das planilhas (pode vir de um input do usuário ou de um arquivo)
PLANILHAS_URLS = [
    "https://docs.google.com/spreadsheets/d/1zRkVSttkkpqekEdXjGPlz3-Dl7NzgqnkbGioJGuAdRY/edit?usp=sharing",
    "https://docs.google.com/spreadsheets/d/1ym-kHhuaW1pD5KNXzrmgY2QaUSol339R4fCHdGRS3K8/edit?usp=sharing",
]

@st.cache_data(ttl=300)  # Reutiliza o cache por 5 minutos
def processar_planilhas(urls):
    dados_totais = []

    for url in urls:
        try:
            doc = client.open_by_url(url)
            # Percorre todas as abas da planilha
            for worksheet in doc.worksheets():
                # 1. Pega todas as linhas brutas (evita o erro do header do gspread)
                rows = worksheet.get_all_values()
                if not rows or len(rows) < 2:  # Se estiver vazia ou só tiver cabeçalho
                    continue

                # 2. Transforma em DataFrame usando a primeira linha como cabeçalho
                header = [str(col).strip() for col in rows[0]]
                df = pd.DataFrame(rows[1:], columns=header)

                # 3. Trata e remove colunas vazias ou sem nome
                df = df.loc[:, df.columns != ""]
                df = df.loc[:, ~df.columns.str.startswith("Unnamed")]

                # 4. Remove colunas com nomes duplicados mantendo apenas a primeira
                df = df.loc[:, ~df.columns.duplicated()]

                # Ajuste dos nomes para busca
                colunas_necessarias = ["Status", "Importador", "Digitador", "Data Atualização"]
                
                # Verifica se todas as colunas necessárias estão presentes
                if all(col in df.columns for col in colunas_necessarias):
                    df["Origem"] = f"{doc.title} - {worksheet.title}"
                    dados_totais.append(df)

        except Exception as err:
            st.warning(f"Não foi possível ler a planilha: {url}. Erro: {err}")

    if dados_totais:
        return pd.concat(dados_totais, ignore_index=True)
    return pd.DataFrame()

# Executa a busca de dados
with st.spinner("Lendo planilhas do Google Sheets..."):
    df_completo = processar_planilhas(PLANILHAS_URLS)

if df_completo.empty:
    st.info("Nenhum dado encontrado ou estrutura de colunas incompatível.")
else:
    # Padronização de texto na coluna Status para evitar falhas de digitação/espaços
    df_completo["Status_Clean"] = df_completo["Status"].astype(str).str.strip().str.upper()

    # 4. Filtro principal para "EM DIGITAÇÃO"
    df_em_digitacao = df_completo[df_completo["Status_Clean"] == "EM DIGITAÇÃO"]

    # Exibição das métricas rápidas
    col1, col2, col3 = st.columns(3)
    col1.metric("Em Digitação", len(df_em_digitacao))
    col2.metric("Digitado", len(df_completo[df_completo["Status_Clean"] == "DIGITADO"]))
    col3.metric("Finalizado", len(df_completo[df_completo["Status_Clean"] == "FINALIZADO"]))

    st.markdown("---")
    st.subheader("📌 Tarefas em Digitação")

    if not df_em_digitacao.empty:
        # Seleciona e exibe apenas as colunas solicitadas
        colunas_exibicao = ["Origem", "Importador", "Digitador", "Data Atualização", "Status"]
        st.dataframe(
            df_em_digitacao[colunas_exibicao],
            use_container_width=True,
            hide_index=True
        )
    else:
        st.success("Nenhuma tarefa pendente 'Em Digitação' no momento!")
