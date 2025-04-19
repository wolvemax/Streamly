from pathlib import Path

codigo_atualizado = """
import streamlit as st
import unicodedata
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime
import time
import openai
import gspread
import re

# ======= CONFIGURAÇÕES =======
st.set_page_config(page_title="Bem vindo ao SIMULAMAX - Simulador Médico IA", page_icon="🩺", layout="wide")

openai.api_key = st.secrets["openai"]["api_key"]
ASSISTANT_ID = st.secrets["assistants"]["default"]
ASSISTANT_PEDIATRIA_ID = st.secrets["assistants"]["pediatria"]
ASSISTANT_EMERGENCIAS_ID = st.secrets["assistants"]["emergencias"]

scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
google_creds = dict(st.secrets["google_credentials"])
creds = ServiceAccountCredentials.from_json_keyfile_dict(google_creds, scope)
client_gspread = gspread.authorize(creds)

# ======= GARANTIR ESTADO INICIAL =======
REQUIRED_KEYS = ["logado", "thread_id", "historico", "consulta_finalizada", "prompt_inicial", "media_usuario", "run_em_andamento", "especialidade"]
for key in REQUIRED_KEYS:
    if key not in st.session_state:
        st.session_state[key] = False if key == "logado" else None

# ======= FUNÇÕES UTILITÁRIAS =======
def remover_acentos(texto):
    return ''.join((c for c in unicodedata.normalize('NFD', texto) if unicodedata.category(c) != 'Mn'))

def normalizar_chave(chave):
    return remover_acentos(chave.strip().lower())

def normalizar(texto):
    return ''.join((c for c in unicodedata.normalize('NFD', str(texto)) if unicodedata.category(c) != 'Mn')).lower().strip()

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
        sheet = client_gspread.open("LogsSimulador").worksheets()[0]
        dados = sheet.get_all_records()
        return sum(1 for linha in dados if str(linha.get("usuario", "")).strip().lower() == usuario.lower())
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
    resumo = texto[:300].replace("\\n", " ").strip()
    assistente = st.session_state.get("especialidade", "desconhecido")
    sheet.append_row([usuario, datahora, resumo, assistente], value_input_option="USER_ENTERED")

def obter_ultimos_resumos(usuario, especialidade, n=10):
    try:
        sheet = client_gspread.open("LogsSimulador").worksheet("Pagina1")
        dados = sheet.get_all_records()
        historico = [linha for linha in dados if str(linha.get("usuario", "")).strip().lower() == usuario.lower() and str(linha.get("assistente", "")).lower() == especialidade.lower()]
        ultimos = historico[-n:]
        resumos = [linha.get("resumo", "")[:250] for linha in ultimos if linha.get("resumo", "")]
        return resumos
    except Exception as e:
        st.warning(f"Erro ao obter resumos de casos anteriores: {e}")
        return []

# ======= ESCOLHA DA ESPECIALIDADE =======
especialidade = st.radio("Especialidade:", ["PSF", "Pediatria", "Emergências"])
st.session_state["especialidade"] = especialidade
assistant_id_usado = {
    "PSF": ASSISTANT_ID,
    "Pediatria": ASSISTANT_PEDIATRIA_ID,
    "Emergências": ASSISTANT_EMERGENCIAS_ID
}.get(especialidade, ASSISTANT_ID)

# ======= INICIAR NOVA SIMULAÇÃO =======
if st.button("➕ Nova Simulação"):
    st.session_state.historico = ""
    st.session_state.consulta_finalizada = False
    st.session_state.thread_id = openai.beta.threads.create().id

    resumos = obter_ultimos_resumos(st.session_state.usuario, especialidade)
    contexto_resumos = "\\n\\n".join(resumos) if resumos else "Nenhum caso anterior registrado."

    prompt_simulacao = f"""
Casos anteriores do estudante {st.session_state.usuario} na especialidade {especialidade}:

{contexto_resumos}

Gere um novo caso clínico completamente diferente desses acima. Evite repetir os padrões anteriores de diagnóstico, queixa, conduta e estrutura.
    \""".strip()

    run = openai.beta.threads.runs.create(
        thread_id=st.session_state.thread_id,
        assistant_id=assistant_id_usado,
        instructions=prompt_simulacao
    )
    with st.spinner("Gerando paciente..."):
        while True:
            status = openai.beta.threads.runs.retrieve(thread_id=st.session_state.thread_id, run_id=run.id)
            if status.status == "completed":
                break
            time.sleep(1)
