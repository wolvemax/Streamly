import streamlit as st
import unicodedata
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime
import time
import openai
import gspread
import re

# ======= CONFIGURAÇÕES =======
st.set_page_config(page_title="Simulador Médico IA", page_icon="🩺", layout="wide")

openai.api_key = st.secrets["openai"]["api_key"]
ASSISTANT_ID = st.secrets["assistants"]["default"]
ASSISTANT_PEDIATRIA_ID = st.secrets["assistants"]["pediatria"]
ASSISTANT_EMERGENCIAS_ID = st.secrets["assistants"]["emergencias"]

scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
google_creds = dict(st.secrets["google_credentials"])
creds = ServiceAccountCredentials.from_json_keyfile_dict(google_creds, scope)
client_gspread = gspread.authorize(creds)

# ======= ESTADO INICIAL =======
for key in ["logado", "thread_id", "historico", "consulta_finalizada", "prompt_inicial", "media_usuario", "especialidade"]:
    if key not in st.session_state:
        st.session_state[key] = None

# ======= FUNÇÕES =======
def remover_acentos(texto):
    return ''.join(c for c in unicodedata.normalize('NFD', texto) if unicodedata.category(c) != 'Mn')

def normalizar_chave(chave):
    return remover_acentos(chave.strip().lower())

def validar_credenciais(usuario, senha):
    try:
        sheet = client_gspread.open("LoginSimulador").sheet1
        dados = sheet.get_all_records()
        for linha in dados:
            linha_normalizada = {normalizar_chave(k): v.strip() for k, v in linha.items() if isinstance(v, str)}
            if linha_normalizada.get("usuario") == usuario and linha_normalizada.get("senha") == senha:
                return True
        return False
    except Exception as e:
        st.error(f"Erro ao validar login: {e}")
        return False

def contar_casos_usuario(usuario):
    try:
        sheet = client_gspread.open("LogsSimulador").worksheet("Pagina1")
        dados = sheet.get_all_records()
        return sum(1 for linha in dados if linha.get("usuario", "").strip().lower() == usuario.lower())
    except:
        return 0

def calcular_media_usuario(usuario):
    try:
        sheet = client_gspread.open("notasSimulador").sheet1
        dados = sheet.get_all_records()
        notas = [float(l["nota"]) for l in dados if str(l.get("usuario", "")).strip().lower() == usuario.lower()]
        return round(sum(notas) / len(notas), 2) if notas else 0.0
    except:
        return 0.0

def registrar_caso(usuario, texto):
    sheet = client_gspread.open("LogsSimulador").worksheet("Pagina1")
    datahora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    resumo = texto[:300].replace("\n", " ").strip()
    assistente = st.session_state.get("especialidade", "desconhecido")
    sheet.append_row([usuario, datahora, resumo, assistente], value_input_option="USER_ENTERED")

def salvar_nota_usuario(usuario, nota):
    sheet = client_gspread.open("notasSimulador").sheet1
    datahora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    sheet.append_row([usuario, str(nota), datahora], value_input_option="USER_ENTERED")

def extrair_nota(texto):
    try:
        match = re.search(r"nota\s*[:\-]?\s*(\d+(?:[.,]\d+)?)(?:\s*/?\s*10)?", texto, re.IGNORECASE)
        if not match:
            match = re.search(r"(\d+(?:[.,]\d+)?)\s*/\s*10", texto)
        if match:
            return float(match.group(1).replace(",", "."))
    except:
        pass
    return None

def obter_ultimos_resumos(usuario, especialidade, n=10):
    try:
        sheet = client_gspread.open("LogsSimulador").worksheet("Pagina1")
        dados = sheet.get_all_records()
        historico = [linha for linha in dados if linha.get("usuario", "").strip().lower() == usuario.lower() and linha.get("assistente", "").lower() == especialidade.lower()]
        ultimos = historico[-n:]
        resumos = [linha.get("resumo", "")[:250] for linha in ultimos if linha.get("resumo", "")]
        return resumos
    except:
        return []

def renderizar_historico():
    mensagens = openai.beta.threads.messages.list(thread_id=st.session_state.thread_id).data
    for msg in sorted(mensagens, key=lambda x: x.created_at):
        conteudo = msg.content[0].text.value.strip()
        if any(ocultar in conteudo.lower() for ocultar in [
            "iniciar nova simulação clínica",
            "evite repetir os seguintes casos",
            "casos anteriores usados pelo estudante",
            "considere os seguintes casos"
        ]):
            continue
        avatar = "👨‍⚕️" if msg.role == "user" else "🧑‍⚕️"
        hora = datetime.fromtimestamp(msg.created_at).strftime("%H:%M")
        with st.chat_message(msg.role, avatar=avatar):
            st.markdown(conteudo)
            st.caption(f"⏰ {hora}")
