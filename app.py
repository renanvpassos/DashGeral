import streamlit as st
import pandas as pd
import requests
import unicodedata
import io
from datetime import date, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
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
    "https://docs.google.com/spreadsheets/d/1mH3TIpm23KkNK-JODDwfd8Igqm1ZtvIeQRUTJAHLZVI/edit?gid=0#gid=0", #KERING
    "https://docs.google.com/spreadsheets/d/1CSX4tQoZsspQ0GmVHuzt5h0ABc28Bdd_DqyPR-rGNns/edit?gid=0#gid=0", #ZARA
    "https://docs.google.com/spreadsheets/d/11xDf-tkye_MeVOh_Re5_Piby9_AdVNv-_TOJyqEk9rQ/edit?usp=sharing", #PRADA
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

def unificar_colunas_mesmo_nome(df):
    if df.empty:
        return df

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

def processar_aba(sheet, sheet_id, headers, alvos):
    title = sheet["properties"]["title"]
    sheet_id_gid = sheet["properties"]["sheetId"]

    csv_url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv&gid={sheet_id_gid}"
    try:
        resp_csv = requests.get(csv_url, headers=headers, timeout=10)
        if resp_csv.status_code != 200:
            return None

        content = resp_csv.content.decode('utf-8', errors='ignore')
        lines = content.splitlines()

        if len(lines) < 2:
            return None

        rows = [line.split(',') for line in lines]

        header_idx = -1
        for i, row in enumerate(rows[:15]):
            row_norm = [normalizar_texto(cell.replace('"', '')) for cell in row]
            if any("STATUS" in cell for cell in row_norm) or any("REFERENCIA" in cell for cell in row_norm):
                header_idx = i
                break

        if header_idx == -1:
            return None

        csv_data = "\n".join(lines[header_idx:])
        df = pd.read_csv(io.StringIO(csv_data), dtype=str, on_bad_lines='skip')

        if df.empty:
            return None

        col_map = {}
        for col_orig in df.columns:
            col_norm = normalizar_texto(col_orig)
            for chave_norm, nome_padrao in alvos.items():
                if chave_norm in col_norm:
                    col_map[col_orig] = nome_padrao
                    break

        if col_map:
            df_filtrado = df[list(col_map.keys())].copy()
            
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
            df_filtrado = unificar_colunas_mesmo_nome(df_filtrado)
            df_filtrado["ORIGEM"] = f"{sheet_id} - {title}"
            return df_filtrado
    except Exception:
        return None
    return None

def processar_planilha_unica(url, headers, alvos):
    sheet_id = extrair_spreadsheet_id(url)
    meta_url = f"https://sheets.googleapis.com/v4/spreadsheets/{sheet_id}?fields=sheets.properties"
    try:
        resp_meta = requests.get(meta_url, headers=headers, timeout=10)
        if resp_meta.status_code != 200:
            return []

        sheets_info = resp_meta.json().get("sheets", [])
        dfs_planilha = []

        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = [executor.submit(processar_aba, sheet, sheet_id, headers, alvos) for sheet in sheets_info]
            for future in as_completed(futures):
                res = future.result()
                if res is not None and not res.empty:
                    dfs_planilha.append(res)
        return dfs_planilha
    except Exception:
        return []

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

    with ThreadPoolExecutor(max_workers=len(urls)) as executor:
        futures = [executor.submit(processar_planilha_unica, url, headers, alvos) for url in urls]
        for future in as_completed(futures):
            res_dfs = future.result()
            if res_dfs:
                dados_totais.extend(res_dfs)

    if dados_totais:
        df_concat = pd.concat(dados_totais, ignore_index=True, sort=False)
        return df_concat.drop_duplicates()
    return pd.DataFrame()

# --- BARRA LATERAL (PAINEL DE CONTROLE) ---
st.sidebar.header("⚙️ Painel de Controle")

# Valores padrão inicializados no Session State se não existirem
if "filtro_status" not in st.session_state:
    st.session_state["filtro_status"] = "AGUARDANDO DIGITAÇÃO"
if "filtro_periodo" not in st.session_state:
    st.session_state["filtro_periodo"] = "Hoje"
if "filtro_intervalo" not in st.session_state:
    st.session_state["filtro_intervalo"] = (date.today() - timedelta(days=7), date.today())

# Callback para recarregar dados e resetar filtros ao estado padrão
def recarregar_e_resetar():
    st.cache_data.clear()
    st.session_state["filtro_status"] = "AGUARDANDO DIGITAÇÃO"
    st.session_state["filtro_periodo"] = "Hoje"
    st.session_state["filtro_intervalo"] = (date.today() - timedelta(days=7), date.today())

st.sidebar.button("🔄 Recarregar Dados Agora", on_click=recarregar_e_resetar, type="primary")

st.sidebar.markdown("---")

# Formulário para agrupar os filtros e só aplicar após a confirmação
with st.sidebar.form(key="form_filtros"):
    st.subheader("🔍 Filtros de Busca")
    
    status_opcoes = ["AGUARDANDO DIGITAÇÃO", "EM DIGITAÇÃO", "DIGITADO", "FINALIZADO", "TODOS"]
    status_selecionado = st.selectbox("Filtrar por Status:", status_opcoes, key="filtro_status")

    opcao_periodo = st.selectbox(
        "Selecione o intervalo:",
        ["Hoje", "Últimos 7 dias", "Últimos 30 dias", "Este Mês", "Todo o tempo", "Personalizado"],
        key="filtro_periodo"
    )

    hoje = date.today()
    
    # Exibe o seletor de datas APENAS se "Personalizado" estiver selecionado
    if opcao_periodo == "Personalizado":
        intervalo_personalizado = st.date_input("Escolha o período (se personalizado):", value=(hoje - timedelta(days=7), hoje), key="filtro_intervalo")
    else:
        intervalo_personalizado = None

    btn_aplicar_filtros = st.form_submit_button("✅ Aplicar Filtros", use_container_width=True)

# Lógica das datas
data_inicio = None
data_fim = None

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
    if isinstance(intervalo_personalizado, tuple) and len(intervalo_personalizado) == 2:
        data_inicio, data_fim = intervalo_personalizado

# --- EXECUÇÃO E FILTRAGEM ---
with st.spinner("Puxando dados das planilhas em paralelo..."):
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

    def checar_referencia_valida(row):
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

        if opcao_periodo == "Todo o tempo":
            return df

        datas_ref = df.apply(lambda r: extrair_data_ref(r, col_fonte_data), axis=1)
        mascara_data_valida = datas_ref.notnull()

        if data_inicio and data_fim:
            mascara_periodo = (datas_ref >= data_inicio) & (datas_ref <= data_fim)
            return df[mascara_data_valida & mascara_periodo]

        return df

    # 1. AGUARDANDO DIGITAÇÃO
    df_com_ref = df_completo[df_completo.apply(checar_referencia_valida, axis=1)]
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

    # SELEÇÃO PELO MENU LATERAL
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

    txt_periodo = f"({data_inicio.strftime('%d/%m/%Y')} até {data_fim.strftime('%d/%m/%Y')})" if data_inicio and data_fim and opcao_periodo != "Todo o tempo" else ""
    st.subheader(f"📌 Registros - {status_selecionado} {txt_periodo}")

    # ORDENAÇÃO DE COLUNAS
    colunas_ordenadas = ["REFERÊNCIA", "IMPORTADOR", "DIGITADOR", "STATUS", "ENVIO P/ DIGITAÇÃO", "DATA ATUALIZAÇÃO", "ORIGEM"]
    colunas_finais = [col for col in colunas_ordenadas if col in df_exibir.columns]

    df_exibir_final = df_exibir[colunas_finais].drop_duplicates()

    st.dataframe(
        df_exibir_final,
        use_container_width=True,
        hide_index=True
    )
