from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime, timezone
import streamlit as st
import gspread
import openai
import re
import time

# ========== CONFIGURAÇÕES ==========
st.set_page_config(page_title="Simulador Médico IA", page_icon="🩺", layout="wide")
openai.api_key = st.secrets["openai"]["api_key"]

ASSISTANT_ID = st.secrets["assistants"]["default"]
ASSISTANT_PEDIATRIA_ID = st.secrets["assistants"]["pediatria"]
ASSISTANT_EMERGENCIAS_ID = st.secrets["assistants"]["emergencias"]

# ========== GOOGLE SHEETS ==========
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
creds = ServiceAccountCredentials.from_json_keyfile_dict(dict(st.secrets["google_credentials"]), scope)
client_gspread = gspread.authorize(creds)

def carregar_planilha(nome, aba="Pagina1"):
    try:
        return client_gspread.open(nome).worksheet(aba)
    except Exception as e:
        st.error(f"⚠️ Erro ao carregar a planilha '{nome}' / aba '{aba}'")
        st.error(f"Detalhes: {e}")
        return None

LOG_SHEET = carregar_planilha("LogsSimulador")
NOTA_SHEET = carregar_planilha("notasSimulador", "Sheet1")
LOGIN_SHEET = carregar_planilha("LoginSimulador", "Sheet1")

# ========== ESTADO ==========
DEFAULTS = {
    "logado": False,
    "thread_id": None,
    "historico": "",
    "consulta_finalizada": False,
    "media_usuario": 0.0,
    "especialidade_atual": "",
    "resposta_final": "",
}
for k, v in DEFAULTS.items():
    st.session_state.setdefault(k, v)

# ========== FUNÇÕES ==========
def validar_credenciais(user, pwd):
    try:
        dados = LOGIN_SHEET.get_all_records()
        for linha in dados:
            if (
                linha.get("usuario", "").strip().lower() == user.strip().lower()
                and linha.get("senha", "").strip() == pwd.strip()
            ):
                return True
    except Exception as e:
        st.error(f"⚠️ Erro ao acessar a planilha de login: {e}")
    return False

def contar_casos_usuario(user):
    if not LOG_SHEET:
        return 0
    dados = LOG_SHEET.get_all_records()
    return sum(1 for l in dados if l.get("usuario", "").lower() == user.lower())

def calcular_media_usuario(user):
    if not NOTA_SHEET:
        return 0.0
    dados = NOTA_SHEET.get_all_records()
    notas = [float(l["nota"]) for l in dados if l.get("usuario", "").lower() == user.lower()]
    return round(sum(notas) / len(notas), 2) if notas else 0.0

def registrar_caso(user, texto, especialidade):
    if LOG_SHEET:
        datahora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        resumo = texto[:300].replace("\n", " ").strip()
        try:
            LOG_SHEET.append_row([user, datahora, resumo, especialidade], value_input_option="USER_ENTERED")
        except Exception as e:
            st.error(f"Erro ao salvar no LOG: {e}")

def salvar_nota_usuario(user, nota):
    if NOTA_SHEET:
        datahora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        try:
            NOTA_SHEET.append_row([user, str(nota), datahora], value_input_option="USER_ENTERED")
        except Exception as e:
            st.error(f"Erro ao salvar nota: {e}")

def extrair_nota(resp):
    m = re.search(r"nota\s*[:\-]?\s*(\d+(?:[.,]\d+)?)", resp, re.I)
    return float(m.group(1).replace(",", ".")) if m else None

def obter_ultimos_resumos(user, especialidade, n=10):
    if not LOG_SHEET:
        return []
    dados = LOG_SHEET.get_all_records()
    historico = [l for l in dados if l.get("usuario", "").lower() == user.lower()
                 and l.get("especialidade", "").lower() == especialidade.lower()]
    ult = historico[-n:]
    return [l.get("resumo", "")[:250] for l in ult]

def aguardar_run(tid):
    while True:
        runs = openai.beta.threads.runs.list(thread_id=tid).data
        if not runs or runs[0].status != "in_progress":
            break
        time.sleep(0.8)

def renderizar_historico():
    if not st.session_state.thread_id:
        return
    msgs = openai.beta.threads.messages.list(thread_id=st.session_state.thread_id).data
    for m in sorted(msgs, key=lambda x: x.created_at):
        if not m.content or not hasattr(m.content[0], "text"):
            continue
        content = m.content[0].text.value
        if "Iniciar nova simulação clínica" in content or "Gerar prontuário completo" in content:
            continue
        hora = datetime.fromtimestamp(m.created_at).strftime("%H:%M")
        avatar = "👨‍⚕️" if m.role == "user" else "🧑‍⚕️"
        with st.chat_message(m.role, avatar=avatar):
            st.markdown(content)
            st.caption(f"⏰ {hora}")

