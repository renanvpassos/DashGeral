import streamlit as st
import pandas as pd
import requests
import unicodedata
import io
from datetime import date, timedelta
from google.oauth2.service_account import Credentials
import google.auth.transport.requests

st.set_page_config(page_title="Monitor de Etapas de Trabalho", layout="wide")
st.title("📊 Monitor de Etapas de Trabalho")

# Obtém Token de Acesso da Service Account
@st.cache_resource
def obter_access_token():
    scopes = ["https://www.googleapis.com/auth/spreadsheets.readonly"]
    credentials_info = st.secrets["gcp_service_account"]
    credentials = Credentials.from_service_account_info(credentials_info, scopes=scopes)
    request = google.auth.transport.requests.Request()
    credentials.refresh(request)
    return credentials.token

PLANILHAS_URLS = [
    "https://docs.google.com/spreadsheets/d/1ym-kHhuaW1pD5KNXzrmgY2QaUSol339R4fCHdGRS3K8/edit?usp=sharing", #ROCHE
    "https://docs.google.com/spreadsheets/d/1zRkVSttkkpqekEdXjGPlz3-Dl7NzgqnkbGioJGuAdRY/edit?usp=sharing", #RENAN
    "https://docs.google.com/spreadsheets/d/1uJzArQ8oF19s2yYQD3BFoNeaZW_xPMdD1RvdSIWnGR8/edit?usp=sharing", #VALERIA
    "https://docs.google.com/spreadsheets/d/1Q0BMTebNMSEyGqTwuQjy2r6nLeSNQE7oIhEntpUhQAA/edit?gid=0#gid=0", #SALVADOR LENNON
    "https://docs.google.com/spreadsheets/d/10P8YgNIqxox-MqDA63DnO5yKAueAQ5GgJONDH2fu9-8/edit?gid=0#gid=0", #RIO LENNON
    "https://docs.google.com/spreadsheets/d/1gNeE9CY8KLaI7DOajWFJcGmZ-UuS4ME8firbFkovNS4/edit?usp=sharing", #ABB
]

def extrair_spreadsheet_id(url):
    if "/d/" in url:
        return url.split("/d/")[1].split("/")[0]
    return url

def normalizar_texto(texto):
    if not isinstance(texto, str):
        texto = str(texto)
    texto = unicodedata.normalize('NFD', texto).encode('ascii', 'ignore').decode('utf-8')
    return texto.strip().upper()

def consolidar_colunas_duplicadas(df):
    """
    Agrupa colunas que possuem o mesmo nome base (ex: vários 'STATUS' ou 'IMPORTADOR')
    e combina seus valores linha a linha, mantendo apenas 1 coluna final para cada campo.
    """
    if df.empty:
        return df

    # Identifica nomes base removendo sufixos numéricos (ex: STATUS_1 -> STATUS)
    cols_base = {}
    for col in df.columns:
        if col == "ORIGEM":
            continue
        nome_base = col.split('_')[0] if '_' in col and col.split('_')[-1].isdigit() else col
        if nome_base not in cols_base:
            cols_base[nome_base] = []
        cols_base[nome_base].append(col)

    df_consolidado = pd.DataFrame(index=df.index)

    for nome_base, lista_cols in cols_base.items():
        if len(lista_cols) == 1:
            df_consolidado[nome_base] = df[lista_cols[0]]
        else:
            # Se houver mais de uma coluna com o mesmo nome, combina pegando o primeiro valor preenchido
            def pegar_primeiro_valido(row):
                for c in lista_cols:
                    val = str(row[c]).strip().replace('"', '')
                    if val != "" and val.lower() not in ["none", "nan", "null"]:
                        return row[c]
                return ""
            df_consolidado[nome_base] = df.apply(pegar_primeiro_valido, axis=1)

    if "ORIGEM" in df.columns:
        df_consolidado["ORIGEM"] = df["ORIGEM"]

    return df_consolidado

# Cache de 120 segundos
@st.cache_data(ttl=120, show_spinner=False)
def processar_planilhas_otimizado(urls):
    dados_totais = []
    token = obter_access_token()
    headers = {"Authorization": f"Bearer {token}"}

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
        sheet_id = extrair_spreadsheet_id(url)
        meta_url = f"https://sheets.googleapis.com/v4/spreadsheets/{sheet_id}?fields=sheets.properties"
        resp_meta = requests.get(meta_url, headers=headers)
        
        if resp_meta.status_code != 200:
            st.warning(f"Não foi possível acessar a planilha ID: {sheet_id}")
            continue

        sheets_info = resp_meta.json().get("sheets", [])

        for sheet in sheets_info:
            title = sheet["properties"]["title"]
            sheet_id_gid = sheet["properties"]["sheetId"]

            csv_url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv&gid={sheet_id_gid}"
            resp_csv = requests.get(csv_url, headers=headers)

            if resp_csv.status_code != 200:
                continue

            content = resp_csv.content.decode('utf-8', errors='ignore')
            lines = content.splitlines()

            if len(lines) < 2:
                continue

            rows = [line.split(',') for line in lines]

            header_idx = -1
            for i, row in enumerate(rows[:15]):
                row_norm = [normalizar_texto(cell.replace('"', '')) for cell in row]
                if any("STATUS" in cell for cell in row_norm) or any("REFERENCIA" in cell for cell in row_norm):
                    header_idx = i
                    break

            if header_idx == -1:
                continue

            csv_data = "\n".join(lines[header_idx:])
            df = pd.read_csv(io.StringIO(csv_data), dtype=str, on_bad_lines='skip')

            if df.empty:
                continue

            col_map = {}
            for col_orig in df.columns:
                col_norm = normalizar_texto(col_orig)
                for chave_norm, nome_padrao in alvos.items():
                    if chave_norm in col_norm:
                        col_map[col_orig] = nome_padrao
                        break

            if col_map:
                # Seleciona as colunas desejadas e renomeia
                df_filtrado = df[list(col_map.keys())].copy()
                
                # Gera sufixos temporários para colunas do mesmo tipo para evitar colisão inicial
                novas_cols = []
                contagem = {}
                for col in col_map.values():
                    if col not in contagem:
                        contagem[col] = 1
                        novas_cols.append(col)
                    else:
                        contagem[col] += 1
                        novas_cols.append(f"{col}_{contagem[col]}")
                
                df_filtrado.columns = novas_cols

                # Unifica todas as colunas duplicadas em 1 única coluna por tipo
                df_filtrado = consolidar_colunas_duplicadas(df_filtrado)

                df_filtrado["ORIGEM"] = f"{sheet_id} - {title}"
                dados_totais.append(df_filtrado)

    if dados_totais:
        df_concat = pd.concat(dados_totais, ignore_index=True, sort=False)
        return consolidar_colunas_duplicadas(df_concat)
    return pd.DataFrame()

# --- BARRA LATERAL (PAINEL DE CONTROLE) ---
st.sidebar.header("⚙️ Painel de Controle")

if st.sidebar.button("🔄 Recarregar Dados Agora"):
    st.cache_data.clear()
    st.rerun()

status_opcoes = ["AGUARDANDO DIGITAÇÃO", "EM DIGITAÇÃO", "DIGITADO", "FINALIZADO", "TODOS"]
status_selecionado = st.sidebar.selectbox("Filtrar por Status:", status_opcoes, index=0)

st.sidebar.markdown("---")

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
with st.spinner("Puxando dados das planilhas de forma otimizada..."):
    df_completo = processar_planilhas_otimizado(PLANILHAS_URLS)

if df_completo.empty:
    st.info("Nenhum dado localizado. Verifique se as URLs e abas possuem colunas válidas.")
else:
    def extrair_data_coluna(val):
        val_str = str(val).strip().replace('"', '')
        if val_str and val_str.lower() not in ["none", "nan", "null"]:
            try:
                dt = pd.to_datetime(val_str, dayfirst=True, errors='coerce')
                if pd.notnull(dt):
                    return dt.date()
            except Exception:
                return None
        return None

    def checar_referencia_preenchida(row):
        if "REFERÊNCIA" in row:
            val = str(row["REFERÊNCIA"]).strip().replace('"', '')
            return val != "" and val.lower() not in ["none", "nan", "null"]
        return False

    def checar_status_vazio(row):
        if "STATUS" in row:
            val = str(row["STATUS"]).strip().replace('"', '')
            return val == "" or val.lower() in ["none", "nan"]
        return True

    def checar_status_termo(row, termo):
        if "STATUS" in row:
            val = normalizar_texto(row["STATUS"])
            return termo in val
        return False

    def extrair_data_ref(row, col_nome):
        if col_nome in row:
            return extrair_data_coluna(row[col_nome])
        return None

    def filtrar_por_periodo(df, col_fonte_data):
        if df.empty or col_fonte_data not in df.columns:
            return df

        datas_ref = df.apply(lambda r: extrair_data_ref(r, col_fonte_data), axis=1)
        mascara_data_valida = datas_ref.notnull()

        if opcao_periodo != "Todo o tempo" and data_inicio and data_fim:
            mascara_periodo = (datas_ref >= data_inicio) & (datas_ref <= data_fim)
            return df[mascara_data_valida & mascara_periodo]

        return df[mascara_data_valida]

    # 1. AGUARDANDO DIGITAÇÃO (Referência Preenchida + Status Vazio + Data Envio Preenchida)
    df_com_ref = df_completo[df_completo.apply(checar_referencia_preenchida, axis=1)]
    df_aguardando = df_com_ref[df_com_ref.apply(checar_status_vazio, axis=1)]
    df_aguardando_filtrado = filtrar_por_periodo(df_aguardando, "ENVIO P/ DIGITAÇÃO")

    # 2. EM DIGITAÇÃO
    df_em_dig = df_completo[df_completo.apply(lambda r: checar_status_termo(r, "EM DIGITA"), axis=1)]
    df_em_dig_filtrado = filtrar_por_periodo(df_em_dig, "DATA ATUALIZAÇÃO")

    # 3. DIGITADO
    df_digitado = df_completo[df_completo.apply(lambda r: checar_status_termo(r, "DIGITADO") and not checar_status_termo(r, "EM DIGITA"), axis=1)]
    df_digitado_filtrado = filtrar_por_periodo(df_digitado, "DATA ATUALIZAÇÃO")

    # 4. FINALIZADO
    df_finalizado = df_completo[df_completo.apply(lambda r: checar_status_termo(r, "FINALIZAD"), axis=1)]
    df_finalizado_filtrado = filtrar_por_periodo(df_finalizado, "DATA ATUALIZAÇÃO")

    total_registros_periodo = len(df_aguardando_filtrado) + len(df_em_dig_filtrado) + len(df_digitado_filtrado) + len(df_finalizado_filtrado)

    # MÉTRICAS
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Aguardando", len(df_aguardando_filtrado))
    c2.metric("Em Digitação", len(df_em_dig_filtrado))
    c3.metric("Digitado", len(df_digitado_filtrado))
    c4.metric("Finalizado", len(df_finalizado_filtrado))
    c5.metric("Total Registros", total_registros_periodo)

    st.markdown("---")

    if status_selecionado == "AGUARDANDO DIGITAÇÃO":
        df_exibir = df_aguardando_filtrado
    elif status_selecionado == "EM DIGITAÇÃO":
        df_exibir = df_em_dig_filtrado
    elif status_selecionado == "DIGITADO":
        df_exibir = df_digitado_filtrado
    elif status_selecionado == "FINALIZADO":
        df_exibir = df_finalizado_filtrado
    else:
        df_exibir = pd.concat([df_aguardando_filtrado, df_em_dig_filtrado, df_digitado_filtrado, df_finalizado_filtrado], ignore_index=True)

    txt_periodo = f"({data_inicio.strftime('%d/%m/%Y')} até {data_fim.strftime('%d/%m/%Y')})" if data_inicio and data_fim and opcao_periodo != "Todo o tempo" else "(Com data preenchida)"
    st.subheader(f"📌 Registros - {status_selecionado} {txt_periodo}")

    df_exibir_clean = df_exibir.dropna(how="all")

    # Garante a ordem limpa e exata das colunas principais na exibição
    colunas_ordenadas = ["REFERÊNCIA", "IMPORTADOR", "DIGITADOR", "STATUS", "ENVIO P/ DIGITAÇÃO", "DATA ATUALIZAÇÃO", "ORIGEM"]
    colunas_finais = [col for col in colunas_ordenadas if col in df_exibir_clean.columns]

    st.dataframe(
        df_exibir_clean[colunas_finais],
        use_container_width=True,
        hide_index=True
    )
