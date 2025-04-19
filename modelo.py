import streamlit as st
import unicodedata
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime
import time, re, openai, gspread

# ===== CONFIGURAÇÕES =====
st.set_page_config(page_title="Bem​‑vindo ao SIMULAMAX – Simulador Médico IA",
                   page_icon="🧪", layout="wide")

openai.api_key = st.secrets["openai"]["api_key"]
ASSISTANT_ID           = st.secrets["assistants"]["default"]
ASSISTANT_PEDIATRIA_ID = st.secrets["assistants"]["pediatria"]
ASSISTANT_EMERGENCIAS_ID = st.secrets["assistants"]["emergencias"]

# Google Sheets
scope = ["https://spreadsheets.google.com/feeds",
         "https://www.googleapis.com/auth/drive"]
creds = ServiceAccountCredentials.from_json_keyfile_dict(
            dict(st.secrets["google_credentials"]), scope)
client_gspread = gspread.authorize(creds)

# ===== PLANILHAS SEGURAS =====
try:
    planilha_logs = client_gspread.open("LogsSimulador")
    LOG_SHEET = planilha_logs.worksheet("Pagina1")  # ajuste se for "Página1"
    # st.write("✅ LogsSimulador carregado. Abas:", [ws.title for ws in planilha_logs.worksheets()]))
except Exception as e:
    st.error(f"❌ Erro ao acessar planilha LogsSimulador: {e}")
    st.stop()

try:
    NOTA_SHEET = client_gspread.open("notasSimulador").sheet1
except Exception as e:
    st.error(f"❌ Erro ao acessar planilha notasSimulador: {e}")
    st.stop()

try:
    LOGIN_SHEET = client_gspread.open("LoginSimulador").sheet1
except Exception as e:
    st.error(f"❌ Erro ao acessar planilha LoginSimulador: {e}")
    st.stop()

# ===== ESTADO =====
DEFAULTS = {
    "logado": False,
    "thread_id": None,
    "historico": "",
    "consulta_finalizada": False,
    "media_usuario": 0.0,
    "run_em_andamento": False,
    "especialidade_atual": ""
}
for k, v in DEFAULTS.items():
    st.session_state.setdefault(k, v)

# ===== FUNÇÕES AUXILIARES =====
def remover_acentos(txt):
    return ''.join(c for c in unicodedata.normalize('NFD', txt)
                   if unicodedata.category(c) != 'Mn')

def validar_credenciais(user, pwd):
    dados = LOGIN_SHEET.get_all_records()
    for linha in dados:
        chaves = {k.strip().lower(): v for k, v in linha.items()}
        if (chaves.get("usuario", "").strip().lower() == user.strip().lower() and
            chaves.get("senha", "").strip() == pwd.strip()):
            return True
    return False

def contar_casos_usuario(user):
    dados = LOG_SHEET.get_all_records()
    return sum(1 for l in dados if l.get("usuario", "").lower() == user.lower())

def calcular_media_usuario(user):
    dados = NOTA_SHEET.get_all_records()
    notas = [float(l["nota"]) for l in dados
             if l.get("usuario", "").lower() == user.lower()]
    return round(sum(notas) / len(notas), 2) if notas else 0.0

def registrar_caso(user, texto, especialidade):
    datahora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    resumo = texto[:300].replace("\n", " ").strip()
    LOG_SHEET.append_row([user, datahora, resumo, especialidade],
                         value_input_option="USER_ENTERED")

def salvar_nota_usuario(user, nota):
    datahora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    NOTA_SHEET.append_row([user, str(nota), datahora],
                          value_input_option="USER_ENTERED")

def extrair_nota(resp):
    m = re.search(r"nota\s*[:\-]?\s*(\d+(?:[.,]\d+)?)", resp, re.I)
    return float(m.group(1).replace(",", ".")) if m else None

def obter_ultimos_resumos(user, especialidade, n=10):
    dados = LOG_SHEET.get_all_records()
    historico = [l for l in dados
                 if l.get("usuario", "").lower() == user.lower()
                 and l.get("assistente", "").lower() == especialidade.lower()]
    ult = historico[-n:]
    return [l.get("resumo", "")[:250] for l in ult]

def aguardar_run(tid):
    while True:
        runs = openai.beta.threads.runs.list(thread_id=tid).data
        if not runs or runs[0].status != "in_progress":
            break
        time.sleep(0.8)

def renderizar_historico():
    if not st.session_state.thread_id: return
    msgs = openai.beta.threads.messages.list(
        thread_id=st.session_state.thread_id).data
    for m in sorted(msgs, key=lambda x: x.created_at):
        if not hasattr(m, "content") or not m.content:
            continue
        conteudo = m.content[0].text.value
        if any(p in conteudo.lower() for p in ["iniciar nova simula", "evite repetir", "casos anteriores"]):
            continue
        hora = datetime.fromtimestamp(m.created_at).strftime("%H:%M")
        avatar = "👨‍⚕️" if m.role == "user" else "🧑‍⚕️"
        with st.chat_message(m.role, avatar=avatar):
            st.markdown(conteudo)
            st.caption(f"⏰ {hora}")
