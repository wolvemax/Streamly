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
for k in ["logado", "thread_id", "consulta_finalizada", "prompt_inicial", "run_em_andamento", "especialidade", "usuario"]:
    if k not in st.session_state:
        st.session_state[k] = False if k == "logado" else None

# ======= FUNÇÕES =======
def remover_acentos(txt):
    return ''.join(c for c in unicodedata.normalize('NFD', txt) if unicodedata.category(c) != 'Mn')

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
        st.error(f"Erro de login: {e}")
        return False

def calcular_media_usuario(usuario):
    try:
        sheet = client_gspread.open("notasSimulador").sheet1
        dados = sheet.get_all_records()
        notas = [float(l["nota"]) for l in dados if l.get("usuario", "").strip().lower() == usuario.lower()]
        return round(sum(notas) / len(notas), 2) if notas else 0.0
    except:
        return 0.0

def registrar_caso(usuario, texto, especialidade):
    try:
        sheet = client_gspread.open("LogsSimulador").worksheet("Pagina1")
        datahora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        resumo = texto[:300].replace("\n", " ").strip()
        sheet.append_row([usuario, datahora, resumo, especialidade], value_input_option="USER_ENTERED")
    except Exception as e:
        st.error(f"Erro ao registrar caso: {e}")

def salvar_nota_usuario(usuario, nota):
    try:
        sheet = client_gspread.open("notasSimulador").sheet1
        datahora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        sheet.append_row([usuario, str(nota), datahora], value_input_option="USER_ENTERED")
    except Exception as e:
        st.error(f"Erro ao salvar nota: {e}")

def extrair_nota(texto):
    try:
        match = re.search(r"nota\s*[:\-]?\s*(\d+(?:[.,]\d+)?)(?:\s*/?\s*10)?", texto, re.IGNORECASE)
        if not match:
            match = re.search(r"(\d+(?:[.,]\d+)?)\s*/\s*10", texto)
        return float(match.group(1).replace(",", ".")) if match else None
    except:
        return None

def obter_ultimos_resumos(usuario, especialidade, n=10):
    try:
        sheet = client_gspread.open("LogsSimulador").worksheet("Pagina1")
        dados = sheet.get_all_records()
        filtrados = [l for l in dados if l.get("usuario", "").strip().lower() == usuario.lower() and l.get("assistente", "").strip().lower() == especialidade.lower()]
        return [l["resumo"][:250] for l in filtrados[-n:] if "resumo" in l]
    except Exception as e:
        st.warning(f"Erro ao obter resumos: {e}")
        return []

def renderizar_historico():
    if not st.session_state.thread_id:
        return
    mensagens = openai.beta.threads.messages.list(thread_id=st.session_state.thread_id).data
    mensagens = sorted(mensagens, key=lambda x: x.created_at)
    for msg in mensagens:
        texto = msg.content[0].text.value
        if "Iniciar nova simulação clínica" in texto:
            continue
        hora = datetime.fromtimestamp(msg.created_at).strftime("%H:%M")
        with st.chat_message(msg.role, avatar="👨‍⚕️" if msg.role == "user" else "🧑‍⚕️"):
            st.markdown(texto)
            st.caption(f"⏰ {hora}")

def contar_casos_usuario(usuario):
    try:
        sheet = client_gspread.open("LogsSimulador").worksheet("Pagina1")
        dados = sheet.get_all_records()
        return sum(1 for linha in dados if str(linha.get("usuario", "")).strip().lower() == usuario.lower())
    except Exception as e:
        st.warning(f"Erro ao contar casos: {e}")
        return 0

def aguardar_fim_run(thread_id, run_id):
    while True:
        status = openai.beta.threads.runs.retrieve(thread_id=thread_id, run_id=run_id)
        if status.status == "completed":
            break
        time.sleep(1)

# ======= LOGIN =======
if not st.session_state.logado:
    st.title("🔐 Login")
    with st.form("form_login"):
        user = st.text_input("Usuário")
        pwd = st.text_input("Senha", type="password")
        if st.form_submit_button("Entrar"):
            if validar_credenciais(user, pwd):
                st.session_state.usuario = user
                st.session_state.logado = True
                st.rerun()
            else:
                st.error("Credenciais inválidas.")
    st.stop()

# ======= ÁREA LOGADA =======
st.title("🩺 Simulador Médico IA")
st.markdown(f"👤 Usuário logado: **{st.session_state.usuario}**")

especialidade = st.radio("Especialidade:", ["PSF", "Pediatria", "Emergências"])
st.session_state["especialidade"] = especialidade

col1, col2 = st.columns(2)
col1.metric("📋 Casos finalizados", contar_casos_usuario(st.session_state.usuario))
col2.metric("📊 Média global", calcular_media_usuario(st.session_state.usuario))

if st.button("➕ Nova Simulação"):
    st.session_state.historico = ""
    st.session_state.thread_id = openai.beta.threads.create().id
    st.session_state.consulta_finalizada = False

    if especialidade == "Emergências":
        assistant_id_usado = ASSISTANT_EMERGENCIAS_ID
    elif especialidade == "Pediatria":
        assistant_id_usado = ASSISTANT_PEDIATRIA_ID
    else:
        assistant_id_usado = ASSISTANT_ID

    resumos_anteriores = obter_ultimos_resumos(st.session_state.usuario, especialidade)
    contexto = "\n\n".join(resumos_anteriores) if resumos_anteriores else "Nenhum caso anterior registrado."

    prompt_simulacao = f"""
Casos anteriores do estudante {st.session_state.usuario} na especialidade {especialidade}:

{contexto}

Com base nisso, crie um NOVO CASO CLÍNICO completamente diferente dos anteriores.
Evite repetir diagnósticos, queixas principais, condutas ou contextos clínicos semelhantes.
"""

    openai.beta.threads.messages.create(
        thread_id=st.session_state.thread_id,
        role="user",
        content=prompt_simulacao
    )
    run = openai.beta.threads.runs.create(
        thread_id=st.session_state.thread_id,
        assistant_id=assistant_id_usado
    )
    with st.spinner("Gerando paciente..."):
        aguardar_fim_run(st.session_state.thread_id, run.id)
    st.rerun()

# ======= CHAT =======
if st.session_state.thread_id and not st.session_state.consulta_finalizada:
    renderizar_historico()
    msg = st.chat_input("Digite uma pergunta ou conduta:")
    if msg:
        openai.beta.threads.messages.create(thread_id=st.session_state.thread_id, role="user", content=msg)
        run = openai.beta.threads.runs.create(thread_id=st.session_state.thread_id, assistant_id=assistant_id_usado)
        with st.spinner("Pensando..."):
            aguardar_fim_run(st.session_state.thread_id, run.id)
        st.rerun()

# ======= FINALIZAR =======
if st.session_state.thread_id and not st.session_state.consulta_finalizada:
    if st.button("✅ Finalizar Consulta"):
        comando = "Finalize a consulta gerando: prontuário completo, feedback educacional e nota no formato 'Nota: X/10'."
        openai.beta.threads.messages.create(thread_id=st.session_state.thread_id, role="user", content=comando)
        run = openai.beta.threads.runs.create(thread_id=st.session_state.thread_id, assistant_id=assistant_id_usado)
        with st.spinner("Gerando relatório..."):
            aguardar_fim_run(st.session_state.thread_id, run.id)

        mensagens = openai.beta.threads.messages.list(thread_id=st.session_state.thread_id).data
        for msg in reversed(mensagens):
            if msg.role == "assistant":
                resposta = msg.content[0].text.value
                st.session_state.consulta_finalizada = True
                with st.chat_message("assistant", avatar="🧑‍⚕️"):
                    st.markdown("### 📄 Resultado Final")
                    st.markdown(resposta)
                registrar_caso(st.session_state.usuario, resposta, st.session_state["especialidade"])
                nota = extrair_nota(resposta)
                if nota:
                    salvar_nota_usuario(st.session_state.usuario, nota)
                    st.success(f"✅ Nota registrada: {nota}/10")
                break
