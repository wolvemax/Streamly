import streamlit as st
from streamlit_webrtc import webrtc_streamer, AudioProcessorBase, WebRtcMode
import av
import tempfile
import openai
import os
import time
import re
import unicodedata
import numpy as np
from datetime import datetime, timezone
from pydub import AudioSegment
from scipy.io.wavfile import write
import gspread
from oauth2client.service_account import ServiceAccountCredentials

# ===== CONFIGURAÇÕES =====
st.set_page_config(page_title="Simulador Médico IA", page_icon="🩺", layout="wide")
openai.api_key = st.secrets["openai"]["api_key"]

# Google Sheets
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
creds = ServiceAccountCredentials.from_json_keyfile_dict(dict(st.secrets["google_credentials"]), scope)
client_gspread = gspread.authorize(creds)

@st.cache_data(ttl=60)
def carregar_login():
    return client_gspread.open("LoginSimulador").sheet1.get_all_records()

@st.cache_data(ttl=60)
def carregar_logs():
    return client_gspread.open("LogsSimulador").worksheet("Pagina1").get_all_records()

@st.cache_data(ttl=60)
def carregar_notas():
    return client_gspread.open("notasSimulador").sheet1.get_all_records()

LOGIN_SHEET = client_gspread.open("LoginSimulador").sheet1
LOG_SHEET = client_gspread.open("LogsSimulador").worksheet("Pagina1")
NOTA_SHEET = client_gspread.open("notasSimulador").sheet1

ASSISTANT_ID = st.secrets["assistants"]["default"]
ASSISTANT_PEDIATRIA_ID = st.secrets["assistants"]["pediatria"]
ASSISTANT_EMERGENCIAS_ID = st.secrets["assistants"]["emergencias"]

# ===== ESTADO =====
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

# ===== FUNÇÕES =====
def validar_credenciais(user, pwd):
    dados = carregar_login()
    for linha in dados:
        if linha.get("usuario", "").strip().lower() == user.lower() and linha.get("senha", "").strip() == pwd:
            return True
    return False

def contar_casos_usuario(user):
    dados = carregar_logs()
    return sum(1 for l in dados if l.get("usuario", "").lower() == user.lower())

def calcular_media_usuario(user):
    dados = carregar_notas()
    notas = [float(l["nota"]) for l in dados if l.get("usuario", "").lower() == user.lower()]
    return round(sum(notas)/len(notas), 2) if notas else 0.0

def registrar_caso(user, texto, especialidade):
    datahora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    resumo = texto[:300].replace("\n", " ").strip()
    LOG_SHEET.append_row([user, datahora, resumo, especialidade], value_input_option="USER_ENTERED")

def salvar_nota_usuario(user, nota):
    datahora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    NOTA_SHEET.append_row([user, str(nota), datahora], value_input_option="USER_ENTERED")

def extrair_nota(resp):
    m = re.search(r"nota\s*[:\-]?\s*(\d+(?:[.,]\d+)?)", resp, re.I)
    return float(m.group(1).replace(",", ".")) if m else None

def obter_ultimos_resumos(user, especialidade, n=10):
    dados = carregar_logs()
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
        texto = m.content[0].text.value
        if "Iniciar nova simulação clínica" in texto or "Gerar prontuário completo" in texto:
            continue
        hora = datetime.fromtimestamp(m.created_at).strftime("%H:%M")
        avatar = "👨‍⚕️" if m.role == "user" else "🧑‍⚕️"
        with st.chat_message(m.role, avatar=avatar):
            st.markdown(texto)
            st.caption(f"⏰ {hora}")

# ===== LOGIN =====
if not st.session_state.logado:
    st.title("🔐 Simulamax - Login")
    with st.form("login"):
        u = st.text_input("Usuário")
        s = st.text_input("Senha", type="password")
        if st.form_submit_button("Entrar") and validar_credenciais(u, s):
            st.session_state.usuario = u
            st.session_state.logado = True
            st.rerun()
    st.stop()

# ===== DASHBOARD =====
st.title("🩺 Simulador Médico com IA")
st.markdown(f"👤 Usuário: **{st.session_state.usuario}**")
col1, col2 = st.columns(2)
col1.metric("📋 Casos finalizados", contar_casos_usuario(st.session_state.usuario))
if st.session_state.media_usuario == 0:
    st.session_state.media_usuario = calcular_media_usuario(st.session_state.usuario)
col2.metric("📊 Média global", st.session_state.media_usuario)

# ===== ESPECIALIDADE =====
esp = st.radio("Especialidade:", ["PSF", "Pediatria", "Emergências"],
               index=["PSF", "Pediatria", "Emergências"].index(st.session_state.especialidade_atual)
               if st.session_state.especialidade_atual else 0)
if esp != st.session_state.especialidade_atual:
    st.session_state.especialidade_atual = esp
    st.session_state.thread_id = None
    st.session_state.consulta_finalizada = False
    st.rerun()

assistant_id = {
    "PSF": ASSISTANT_ID,
    "Pediatria": ASSISTANT_PEDIATRIA_ID,
    "Emergências": ASSISTANT_EMERGENCIAS_ID
}[esp]

# ===== NOVA SIMULAÇÃO =====
if st.button("➕ Nova Simulação"):
    with st.spinner("🔄 Gerando novo caso..."):
        st.session_state.thread_id = openai.beta.threads.create().id
        st.session_state.consulta_finalizada = False
        resumos = obter_ultimos_resumos(st.session_state.usuario, esp, 10)
        contexto = "\n".join(resumos) if resumos else "Nenhum caso anterior."
        prompt = f"Iniciar nova simulação clínica da especialidade {esp}. Casos anteriores:\n{contexto}"
        openai.beta.threads.messages.create(thread_id=st.session_state.thread_id, role="user", content=prompt)
        run = openai.beta.threads.runs.create(thread_id=st.session_state.thread_id, assistant_id=assistant_id)
        aguardar_run(st.session_state.thread_id)
        msgs = openai.beta.threads.messages.list(thread_id=st.session_state.thread_id).data
        for m in reversed(msgs):
            if m.role == "assistant" and m.content and hasattr(m.content[0], "text"):
                st.session_state.historico = m.content[0].text.value
                break
    st.rerun()

# ===== HISTÓRICO DO CASO =====
if st.session_state.historico:
    st.markdown("### 👤 Identificação do Paciente")
    st.info(st.session_state.historico)

if st.session_state.thread_id and not st.session_state.consulta_finalizada:
    renderizar_historico()
    pergunta = st.chat_input("Digite sua pergunta ou conduta:")
    if pergunta:
        openai.beta.threads.messages.create(thread_id=st.session_state.thread_id, role="user", content=pergunta)
        run = openai.beta.threads.runs.create(thread_id=st.session_state.thread_id, assistant_id=assistant_id)
        aguardar_run(st.session_state.thread_id)
        st.rerun()

# ===== ÁUDIO COM WEBRTC =====
class AudioProcessor(AudioProcessorBase):
    def __init__(self):
        self.audio = []

    def recv(self, frame: av.AudioFrame) -> av.AudioFrame:
        pcm = frame.to_ndarray().flatten().astype(np.int16)
        self.audio.extend(pcm)
        return frame

    def get_audio(self):
        return self.audio

ctx = webrtc_streamer(
    key="audio",
    mode=WebRtcMode.SENDONLY,
    audio_receiver_size=256,
    media_stream_constraints={"audio": True, "video": False},
    rtc_configuration={"iceServers": [{"urls": ["stun:stun.l.google.com:19302"]}]},
    audio_processor_factory=AudioProcessor,
)

if ctx.state.playing:
    st.success("🎙️ Gravando... clique para transcrever.")
    if st.button("⏹️ Parar e Transcrever"):
        audio = ctx.audio_processor.get_audio()
        if audio:
            temp = tempfile.NamedTemporaryFile(delete=False, suffix=".wav")
            write(temp.name, 16000, np.array(audio))
            with open(temp.name, "rb") as f:
                transcricao = openai.Audio.transcribe("whisper-1", f)
                st.success(f"📝 Transcrição: {transcricao['text']}")
                openai.beta.threads.messages.create(thread_id=st.session_state.thread_id, role="user", content=transcricao["text"])
                run = openai.beta.threads.runs.create(thread_id=st.session_state.thread_id, assistant_id=assistant_id)
                aguardar_run(st.session_state.thread_id)
                st.rerun()

# ===== FINALIZAR CONSULTA =====
if st.session_state.thread_id and not st.session_state.consulta_finalizada:
    if st.button("✅ Finalizar Consulta"):
        with st.spinner("🧾 Gerando feedback e prontuário..."):
            ts = datetime.now(timezone.utc).timestamp()
            prompt = "Finalize a simulação. Gere prontuário completo, análise da consulta e **Nota: X/10**."
            openai.beta.threads.messages.create(thread_id=st.session_state.thread_id, role="user", content=prompt)
            run = openai.beta.threads.runs.create(thread_id=st.session_state.thread_id, assistant_id=assistant_id)
            aguardar_run(st.session_state.thread_id)
            time.sleep(5)
            msgs = openai.beta.threads.messages.list(thread_id=st.session_state.thread_id).data
            for m in sorted(msgs, key=lambda x: x.created_at, reverse=True):
                if m.role == "assistant" and m.created_at > ts:
                    resposta = m.content[0].text.value
                    with st.chat_message("assistant", avatar="🧑‍⚕️"):
                        st.markdown("### 📄 Resultado Final")
                        st.markdown(resposta)
                    registrar_caso(st.session_state.usuario, resposta, st.session_state.especialidade_atual)
                    nota = extrair_nota(resposta)
                    if nota is not None:
                        salvar_nota_usuario(st.session_state.usuario, nota)
                        st.session_state.media_usuario = calcular_media_usuario(st.session_state.usuario)
                        st.success(f"✅ Nota salva: {nota}")
                    else:
                        st.warning("⚠️ Nota não extraída.")
                    st.session_state.consulta_finalizada = True
                    break
