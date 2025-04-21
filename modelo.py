import streamlit as st
from streamlit_webrtc import webrtc_streamer, AudioProcessorBase
import openai, gspread, re, time, av, tempfile
import numpy as np
from datetime import datetime, timezone
from scipy.io.wavfile import write
from oauth2client.service_account import ServiceAccountCredentials

# ========== CONFIGURAÇÕES ==========
st.set_page_config(page_title="Simulador Médico IA", page_icon="🩺", layout="wide")
openai.api_key = st.secrets["openai"]["api_key"]

# IDs dos Assistentes
ASSISTANT_ID           = st.secrets["assistants"]["default"]
ASSISTANT_PEDIATRIA_ID = st.secrets["assistants"]["pediatria"]
ASSISTANT_EMERGENCIAS_ID = st.secrets["assistants"]["emergencias"]

# Autenticação Google Sheets
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
creds = ServiceAccountCredentials.from_json_keyfile_dict(dict(st.secrets["google_credentials"]), scope)
client_gspread = gspread.authorize(creds)

# ========== CACHE DAS PLANILHAS ==========
@st.cache_data(ttl=300)
def carregar_planilha_login():
    try:
        return client_gspread.open("LoginSimulador").sheet1.get_all_records()
    except Exception as e:
        st.error(f"Erro ao carregar LoginSimulador: {e}")
        return []

@st.cache_data(ttl=300)
def carregar_planilha_log():
    try:
        return client_gspread.open("LogsSimulador").worksheet("Pagina1").get_all_records()
    except Exception as e:
        st.error(f"Erro ao carregar LogsSimulador: {e}")
        return []

@st.cache_data(ttl=300)
def carregar_planilha_nota():
    try:
        return client_gspread.open("notasSimulador").sheet1.get_all_records()
    except Exception as e:
        st.error(f"Erro ao carregar notasSimulador: {e}")
        return []

# ========== FUNÇÕES ==========
def validar_credenciais(user, pwd):
    dados = carregar_planilha_login()
    for linha in dados:
        if linha.get("usuario", "").strip().lower() == user.lower() and linha.get("senha", "").strip() == pwd:
            return True
    return False

def contar_casos_usuario(user):
    dados = carregar_planilha_log()
    return sum(1 for l in dados if l.get("usuario", "").lower() == user.lower())

def calcular_media_usuario(user):
    dados = carregar_planilha_nota()
    notas = [float(l["nota"]) for l in dados if l.get("usuario", "").lower() == user.lower()]
    return round(sum(notas)/len(notas), 2) if notas else 0.0

def registrar_caso(user, texto, especialidade):
    try:
        resumo = texto[:300].replace("\n", " ").strip()
        datahora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        plan = client_gspread.open("LogsSimulador").worksheet("Pagina1")
        plan.append_row([user, datahora, resumo, especialidade], value_input_option="USER_ENTERED")
        st.success("✅ Caso salvo na planilha LOG.")
    except Exception as e:
        st.error(f"❌ Erro ao salvar na LOG: {e}")

def salvar_nota_usuario(user, nota):
    try:
        datahora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        plan = client_gspread.open("notasSimulador").sheet1
        plan.append_row([user, str(nota), datahora], value_input_option="USER_ENTERED")
    except Exception as e:
        st.error(f"❌ Erro ao salvar nota: {e}")

DEFAULTS = {
    "logado": False,
    "thread_id": None,
    "historico": "",
    "consulta_finalizada": False,
    "especialidade_atual": "",
    "media_usuario": 0.0,
    "resposta_final": ""
}
for k, v in DEFAULTS.items():
    st.session_state.setdefault(k, v)

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
    st.session_state.historico = ""
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
        time.sleep(3)
    st.rerun()

# ===== HISTÓRICO DO CASO =====
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
    key="audio_transcriber",
    mode=webrtc_streamer.Mode.SENDONLY,
    audio_receiver_size=256,
    media_stream_constraints={"audio": True, "video": False},
    rtc_configuration={"iceServers": [{"urls": ["stun:stun.l.google.com:19302"]}]},
    audio_processor_factory=AudioProcessor,
)

if ctx.state.playing:
    st.success("🎤 Gravando... clique em '⏹️ Parar e Transcrever'")
    if st.button("⏹️ Parar e Transcrever"):
        audio = ctx.audio_processor.get_audio()
        if audio:
            wav_temp = tempfile.NamedTemporaryFile(delete=False, suffix=".wav")
            write(wav_temp.name, 16000, np.array(audio))
            audio_file = open(wav_temp.name, "rb")
            transcript = openai.Audio.transcribe("whisper-1", audio_file)
            st.success(f"📝 Transcrição: {transcript['text']}")
            openai.beta.threads.messages.create(thread_id=st.session_state.thread_id, role="user", content=transcript["text"])
            run = openai.beta.threads.runs.create(thread_id=st.session_state.thread_id, assistant_id=assistant_id)
            aguardar_run(st.session_state.thread_id)
            st.rerun()

# ===== FINALIZAR CONSULTA =====
if st.session_state.thread_id and not st.session_state.consulta_finalizada:
    if st.button("✅ Finalizar Consulta"):
        with st.spinner("⏳ Gerando prontuário final..."):
            timestamp_inicio = datetime.now(timezone.utc).timestamp()
            prompt = ("Finalizar a simulação. Gerar prontuário completo, feedback com base na conduta do usuário, justificar com diretrizes médicas "
                      "e dar **Nota: X/10**.")
            openai.beta.threads.messages.create(thread_id=st.session_state.thread_id, role="user", content=prompt)
            run = openai.beta.threads.runs.create(thread_id=st.session_state.thread_id, assistant_id=assistant_id)
            aguardar_run(st.session_state.thread_id)
            time.sleep(5)

            # Captura da resposta correta do assistant
            msgs = openai.beta.threads.messages.list(thread_id=st.session_state.thread_id).data
            resposta = ""
            for m in sorted(msgs, key=lambda x: x.created_at, reverse=True):
                if m.role == "assistant" and m.created_at > timestamp_inicio:
                    if m.content and hasattr(m.content[0], "text"):
                        resposta = m.content[0].text.value
                        break

            if resposta:
                with st.chat_message("assistant", avatar="🧑‍⚕️"):
                    st.markdown("### 📄 Resultado Final")
                    st.markdown(resposta)

                try:
                    registrar_caso(st.session_state.usuario, resposta, st.session_state.especialidade_atual)
                    st.success("✅ Caso salvo na planilha LOG.")
                except Exception as e:
                    st.error(f"❌ Erro ao salvar no LOG: {e}")

                nota = extrair_nota(resposta)
                if nota is not None:
                    try:
                        salvar_nota_usuario(st.session_state.usuario, nota)
                        st.session_state.media_usuario = calcular_media_usuario(st.session_state.usuario)
                        st.success(f"📊 Nota extraída e salva com sucesso: {nota}")
                    except Exception as e:
                        st.error(f"❌ Erro ao salvar nota: {e}")
                else:
                    st.warning("⚠️ Não foi possível extrair a nota do prontuário.")

                st.session_state.consulta_finalizada = True
                time.sleep(3)
                st.rerun()

