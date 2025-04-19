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
for k, v in {
    "logado": False,
    "thread_id": None,
    "consulta_finalizada": False,
    "prompt_inicial": "",
    "historico": "",
    "media_usuario": 0.0,
    "especialidade": None
}.items():
    if k not in st.session_state:
        st.session_state[k] = v

# ======= FUNÇÕES UTILITÁRIAS =======
def remover_acentos(texto):
    return ''.join((c for c in unicodedata.normalize('NFD', texto) if unicodedata.category(c) != 'Mn'))

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
        return sum(1 for linha in dados if str(linha.get("usuario", "")).strip().lower() == usuario.lower())
    except Exception as e:
        st.warning(f"Erro ao contar casos: {e}")
        return 0

def calcular_media_usuario(usuario):
    try:
        sheet = client_gspread.open("notasSimulador").sheet1
        dados = sheet.get_all_records()
        notas = [float(l["nota"]) for l in dados if str(l.get("usuario", "")).strip().lower() == usuario.lower()]
        return round(sum(notas) / len(notas), 2) if notas else 0.0
    except:
        return 0.0

def salvar_nota_usuario(usuario, nota):
    sheet = client_gspread.open("notasSimulador").sheet1
    datahora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    sheet.append_row([usuario, str(nota), datahora], value_input_option="USER_ENTERED")

def registrar_caso(usuario, texto, especialidade):
    sheet = client_gspread.open("LogsSimulador").worksheet("Pagina1")
    datahora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    resumo = texto[:300].replace("\n", " ").strip()
    sheet.append_row([usuario, datahora, resumo, especialidade], value_input_option="USER_ENTERED")

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
        historico = [l for l in dados if l.get("usuario", "").strip().lower() == usuario.lower() and l.get("assistente", "").strip().lower() == especialidade.lower()]
        ultimos = historico[-n:]
        return [l.get("resumo", "")[:250] for l in ultimos]
    except:
        return []

def renderizar_historico():
    if not st.session_state.thread_id:
        return
    mensagens = openai.beta.threads.messages.list(thread_id=st.session_state.thread_id).data
    mensagens_ordenadas = sorted(mensagens, key=lambda x: x.created_at)
    for msg in mensagens_ordenadas:
        conteudo = msg.content[0].text.value
        if "Iniciar nova simulação clínica" in conteudo:
            continue
        hora = datetime.fromtimestamp(msg.created_at).strftime("%H:%M")
        avatar = "👨‍⚕️" if msg.role == "user" else "🧑‍⚕️"
        with st.chat_message(msg.role, avatar=avatar):
            st.markdown(conteudo)
            st.caption(f"⏰ {hora}")

# ======= TELA DE LOGIN =======
if not st.session_state.logado:
    st.title("🔐 Simulador Médico - Login")
    with st.form("login_form"):
        usuario = st.text_input("Usuário")
        senha = st.text_input("Senha", type="password")
        submitted = st.form_submit_button("Entrar")
        if submitted and validar_credenciais(usuario, senha):
            st.session_state.usuario = usuario
            st.session_state.logado = True
            st.rerun()
        else:
            st.error("Usuário ou senha inválidos.")
    st.stop()

# ======= INTERFACE =======
st.title("🩺 Simulador Médico Interativo com IA")
st.markdown(f"👤 Usuário: **{st.session_state.usuario}**")

especialidade = st.radio("Especialidade:", ["PSF", "Pediatria", "Emergências"])
st.session_state["especialidade"] = especialidade

col1, col2 = st.columns(2)
col1.metric("📋 Casos finalizados", contar_casos_usuario(st.session_state.usuario))
col2.metric("📊 Média global", calcular_media_usuario(st.session_state.usuario))

# ======= NOVA SIMULAÇÃO =======
if st.button("➕ Nova Simulação"):
    st.session_state.historico = ""
    st.session_state.consulta_finalizada = False
    st.session_state.thread_id = openai.beta.threads.create().id

    resumos = obter_ultimos_resumos(st.session_state.usuario, especialidade)
    contexto = "\n".join(resumos) if resumos else "Nenhum caso anterior registrado."

    prompt_simulacao = f"""
Considere os seguintes casos anteriores do estudante {st.session_state.usuario} na especialidade {especialidade}:

{contexto}

Gere um novo caso clínico completamente diferente dos anteriores.
Evite repetir diagnóstico, QP ou conduta.
"""
    openai.beta.threads.messages.create(thread_id=st.session_state.thread_id, role="user", content=prompt_simulacao)

    run = openai.beta.threads.runs.create(thread_id=st.session_state.thread_id, assistant_id={
        "PSF": ASSISTANT_ID,
        "Pediatria": ASSISTANT_PEDIATRIA_ID,
        "Emergências": ASSISTANT_EMERGENCIAS_ID
    }[especialidade])

    with st.spinner("Gerando novo caso..."):
        while True:
            status = openai.beta.threads.runs.retrieve(thread_id=st.session_state.thread_id, run_id=run.id)
            if status.status == "completed":
                break
            time.sleep(1)

    mensagens = openai.beta.threads.messages.list(thread_id=st.session_state.thread_id).data
    for msg in mensagens:
        if msg.role == "assistant":
            st.session_state.historico = msg.content[0].text.value
            break
    st.rerun()

# ======= EXIBIR HISTÓRICO =======
if st.session_state.historico:
    st.markdown("### 👤 Identificação do Paciente")
    st.info(st.session_state.historico)

if st.session_state.thread_id and not st.session_state.consulta_finalizada:
    renderizar_historico()

# ======= INPUT MÉDICO =======
if st.session_state.thread_id and not st.session_state.consulta_finalizada:
    pergunta = st.chat_input("Digite sua pergunta ou conduta:")
    if pergunta:
        openai.beta.threads.messages.create(thread_id=st.session_state.thread_id, role="user", content=pergunta)
        run = openai.beta.threads.runs.create(thread_id=st.session_state.thread_id, assistant_id={
            "PSF": ASSISTANT_ID,
            "Pediatria": ASSISTANT_PEDIATRIA_ID,
            "Emergências": ASSISTANT_EMERGENCIAS_ID
        }[especialidade])
        with st.spinner("Processando..."):
            while True:
                status = openai.beta.threads.runs.retrieve(thread_id=st.session_state.thread_id, run_id=run.id)
                if status.status == "completed":
                    break
                time.sleep(1)
        st.rerun()

# ======= FINALIZAR CONSULTA =======
if st.session_state.thread_id and not st.session_state.consulta_finalizada:
    if st.button("✅ Finalizar Consulta"):
        mensagem_final = (
            "Finalizar consulta. A partir do histórico da consulta, gere:\n"
            "1. O prontuário completo do paciente (### Prontuário Completo).\n"
            "2. Um feedback educacional ao médico.\n"
            "3. Gere uma nota objetiva de 0 a 10. Formato: Nota: X/10."
        )
        openai.beta.threads.messages.create(thread_id=st.session_state.thread_id, role="user", content=mensagem_final)
        run = openai.beta.threads.runs.create(thread_id=st.session_state.thread_id, assistant_id={
            "PSF": ASSISTANT_ID,
            "Pediatria": ASSISTANT_PEDIATRIA_ID,
            "Emergências": ASSISTANT_EMERGENCIAS_ID
        }[especialidade])
        with st.spinner("Gerando relatório final..."):
            while True:
                status = openai.beta.threads.runs.retrieve(thread_id=st.session_state.thread_id, run_id=run.id)
                if status.status == "completed":
                    break
                time.sleep(1)

        mensagens = openai.beta.threads.messages.list(thread_id=st.session_state.thread_id).data
        for msg in mensagens:
            if msg.role == "assistant":
                resposta = msg.content[0].text.value
                st.markdown("### 📄 Resultado Final")
                st.markdown(resposta)
                st.session_state.consulta_finalizada = True
                registrar_caso(st.session_state.usuario, resposta, especialidade)
                nota = extrair_nota(resposta)
                if nota is not None:
                    salvar_nota_usuario(st.session_state.usuario, nota)
                    st.session_state.media_usuario = calcular_media_usuario(st.session_state.usuario)
                    st.success("✅ Nota salva com sucesso!")
                else:
                    st.warning("⚠️ Nota não encontrada.")
                break
