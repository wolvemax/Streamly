import streamlit as st
from streamlit_mic_recorder import mic_recorder
from oauth2client.service_account import ServiceAccountCredentials
import gspread
import openai
import time
import re
from datetime import datetime, timezone
import io
from openai import OpenAI


# ========== CONFIGURAÇÕES ==========
st.set_page_config(page_title="Simulador Médico IA", page_icon="🩺", layout="wide")
openai.api_key = st.secrets["openai"]["api_key"]

# Assistentes
ASSISTANT_ID = st.secrets["assistants"]["default"]
ASSISTANT_PEDIATRIA_ID = st.secrets["assistants"]["pediatria"]
ASSISTANT_EMERGENCIAS_ID = st.secrets["assistants"]["emergencias"]

# Google Sheets
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
creds = ServiceAccountCredentials.from_json_keyfile_dict(dict(st.secrets["google_credentials"]), scope)
client_gspread = gspread.authorize(creds)

@st.cache_data(ttl=300)
def get_sheet_data(sheet_name, worksheet_name="Pagina1"):
    try:
        sheet = client_gspread.open(sheet_name).worksheet(worksheet_name)
        return sheet.get_all_records()
    except:
        return []

@st.cache_data(ttl=300)
def get_sheet(sheet_name, worksheet_name="Pagina1"):
    try:
        return client_gspread.open(sheet_name).worksheet(worksheet_name)
    except:
        return None

LOG_SHEET = get_sheet("LogsSimulador")
NOTA_SHEET = get_sheet("notasSimulador", "Sheet1")
LOGIN_SHEET = get_sheet("LoginSimulador", "Sheet1")

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
    sheet = get_sheet("LoginSimulador", "Sheet1")
    if not sheet:
        st.error("⚠️ Erro ao carregar a planilha de login.")
        return False

    dados = sheet.get_all_records()
    for linha in dados:
        if linha.get("usuario", "").strip().lower() == user.lower() and linha.get("senha", "").strip() == pwd:
            return True
    return False


def contar_casos_usuario(user):
    dados = get_sheet_data("LogsSimulador")
    return sum(1 for l in dados if l.get("usuario", "").lower() == user.lower())

def calcular_media_usuario(user):
    dados = get_sheet_data("notasSimulador", "Sheet1")
    notas = [float(l["nota"]) for l in dados if l.get("usuario", "").lower() == user.lower()]
    return round(sum(notas) / len(notas), 2) if notas else 0.0

def registrar_caso(user, texto, especialidade):
    if LOG_SHEET:
        datahora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        resumo = texto[:300].replace("\\n", " ").strip()
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
    m = re.search(r"nota\\s*[:\\-]?\\s*(\\d+(?:[.,]\\d+)?)", resp, re.I)
    return float(m.group(1).replace(",", ".")) if m else None

def obter_ultimos_resumos(user, especialidade, n=10):
    dados = get_sheet_data("LogsSimulador")
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

# ========== LOGIN ==========
if not st.session_state.logado:
    st.title("🔐 Simulamax - Login")

    with st.form("login"):
        u = st.text_input("Usuário")
        s = st.text_input("Senha", type="password")
        submit = st.form_submit_button("Entrar")

        if submit:
            try:
                # Verifica se a planilha de login está acessível
                login_sheet = get_sheet("LoginSimulador", "sheet1")  # <- ajuste aqui conforme o nome real da aba
                if not login_sheet:
                    st.error("⚠️ Erro ao acessar a planilha LoginSimulador. Verifique se o nome da aba é correto (ex: 'Pagina1') e se o arquivo está compartilhado com o e-mail da API.")
                    st.stop()

                # Verifica credenciais
                if validar_credenciais(u, s):
                    st.session_state.usuario = u
                    st.session_state.logado = True
                    st.success("✅ Login realizado com sucesso!")
                    st.rerun()
                else:
                    st.warning("❌ Usuário ou senha inválidos. Tente novamente.")
            except Exception as e:
                st.error(f"⚠️ Erro inesperado ao acessar o login: {e}")
    st.stop()


# ========== INTERFACE ==========
st.title("🩺 Simulador Médico com IA")
st.markdown(f"👤 Usuário: **{st.session_state.usuario}**")
col1, col2 = st.columns(2)
col1.metric("📋 Casos finalizados", contar_casos_usuario(st.session_state.usuario))
if st.session_state.media_usuario == 0:
    st.session_state.media_usuario = calcular_media_usuario(st.session_state.usuario)
col2.metric("📊 Média global", st.session_state.media_usuario)

# ========== ESPECIALIDADE ==========
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

# ========== NOVA SIMULAÇÃO ==========
if st.button("➕ Nova Simulação"):
    with st.spinner("🔄 Gerando novo caso..."):
        st.session_state.thread_id = openai.beta.threads.create().id
        resumos = obter_ultimos_resumos(st.session_state.usuario, esp, 10)
        contexto = "\\n".join(resumos) if resumos else "Nenhum caso anterior."
        prompt = f"Iniciar nova simulação clínica da especialidade {esp}. Casos anteriores:\\n{contexto}"
        openai.beta.threads.messages.create(thread_id=st.session_state.thread_id, role="user", content=prompt)
        run = openai.beta.threads.runs.create(thread_id=st.session_state.thread_id, assistant_id=assistant_id)
        aguardar_run(st.session_state.thread_id)
        msgs = openai.beta.threads.messages.list(thread_id=st.session_state.thread_id).data
        for m in reversed(msgs):
            if m.role == "assistant" and m.content and hasattr(m.content[0], "text"):
                st.session_state.historico = m.content[0].text.value
                break
    st.rerun()

# ========== HISTÓRICO ==========
if st.session_state.historico:
    st.markdown("### 👤 Identificação do Paciente")
    st.info(st.session_state.historico)

# ========== CHAT ==========
if st.session_state.thread_id and not st.session_state.consulta_finalizada:
    renderizar_historico()
    text = st.chat_input("Digite sua pergunta ou conduta:")
    if text:
        openai.beta.threads.messages.create(thread_id=st.session_state.thread_id, role="user", content=text)
        run = openai.beta.threads.runs.create(thread_id=st.session_state.thread_id, assistant_id=assistant_id)
        aguardar_run(st.session_state.thread_id)
        st.rerun()

# ========== MICROFONE ==========
audio = mic_recorder(
    start_prompt="🎤 Clique para gravar",
    stop_prompt="⏹️ Clique para parar",
    key="audio_rec"
)

if audio:
    st.audio(audio['bytes'], format="audio/wav")

    audio_file = io.BytesIO(audio["bytes"])
    audio_file.name = "audio.wav"

    with st.spinner("🔍 Transcrevendo áudio com Whisper..."):
        try:
            transcript = client.audio.transcriptions.create(
                model="whisper-1",
                file=audio_file
            )
            st.success("📝 Transcrição: " + transcript.text)

            # Envia a transcrição para a IA como pergunta
            openai.beta.threads.messages.create(
                thread_id=st.session_state.thread_id,
                role="user",
                content=transcript.text
            )
            run = openai.beta.threads.runs.create(
                thread_id=st.session_state.thread_id,
                assistant_id=assistant_id
            )
            aguardar_run(st.session_state.thread_id)
            st.rerun()
        except Exception as e:
            st.error(f"Erro ao transcrever: {e}")

# ========== FINALIZAR ==========
if st.session_state.thread_id and not st.session_state.consulta_finalizada:
    if st.button("✅ Finalizar Consulta"):
        with st.spinner("⏳ Gerando prontuário..."):
            ts = datetime.now(timezone.utc).timestamp()
            prompt_final = "Gerar prontuário completo, feedback educacional com base na conduta e dar nota final no formato **Nota: X/10**."
            openai.beta.threads.messages.create(thread_id=st.session_state.thread_id, role="user", content=prompt_final)
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
                        st.warning("⚠️ Nota não encontrada.")
                    st.session_state.consulta_finalizada = True
                    break
