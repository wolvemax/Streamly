import streamlit as st
import unicodedata
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime
import time, re, openai, gspread

# ===== CONFIGURAÇÕES =====
st.set_page_config(page_title="Bem‑vindo ao SIMULAMAX – Simulador Médico IA", page_icon="🧪", layout="wide")
openai.api_key = st.secrets["openai"]["api_key"]
ASSISTANT_ID           = st.secrets["assistants"]["default"]
ASSISTANT_PEDIATRIA_ID = st.secrets["assistants"]["pediatria"]
ASSISTANT_EMERGENCIAS_ID = st.secrets["assistants"]["emergencias"]

# ===== PLANILHAS =====
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
creds = ServiceAccountCredentials.from_json_keyfile_dict(dict(st.secrets["google_credentials"]), scope)
client_gspread = gspread.authorize(creds)

LOG_SHEET = client_gspread.open("LogsSimulador").worksheet("Pagina1")
NOTA_SHEET = client_gspread.open("notasSimulador").sheet1
LOGIN_SHEET = client_gspread.open("LoginSimulador").sheet1

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

# ===== FUNÇÕES =====
def remover_acentos(txt):
    return ''.join(c for c in unicodedata.normalize('NFD', txt) if unicodedata.category(c) != 'Mn')

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
    notas = [float(l["nota"]) for l in dados if l.get("usuario", "").lower() == user.lower()]
    return round(sum(notas) / len(notas), 2) if notas else 0.0

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
    dados = LOG_SHEET.get_all_records()
    historico = [l for l in dados if l.get("usuario", "").lower() == user.lower() and l.get("assistente", "").lower() == especialidade.lower()]
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
    msgs = openai.beta.threads.messages.list(thread_id=st.session_state.thread_id).data
    for m in sorted(msgs, key=lambda x: x.created_at):
        if not hasattr(m, "content") or not m.content: continue
        conteudo = m.content[0].text.value
        if any(p in conteudo.lower() for p in ["iniciar nova simula", "casos anteriores"]): continue
        hora = datetime.fromtimestamp(m.created_at).strftime("%H:%M")
        avatar = "👨‍⚕️" if m.role == "user" else "🧑‍⚕️"
        with st.chat_message(m.role, avatar=avatar):
            st.markdown(conteudo)
            st.caption(f"⏰ {hora}")

# ===== LOGIN =====
if not st.session_state.logado:
    st.title("🔐 Simulamax - Simulador Médico – Login")
    with st.form("login"):
        usuario = st.text_input("Usuário")
        senha = st.text_input("Senha", type="password")
        if st.form_submit_button("Entrar") and validar_credenciais(usuario, senha):
            st.session_state.usuario = usuario
            st.session_state.logado = True
            st.session_state.media_usuario = calcular_media_usuario(usuario)
            st.rerun()
    st.stop()

# ===== DASHBOARD =====
st.title("🧪 Simulador Médico Interativo com IA")
st.markdown(f"👤 Usuário: **{st.session_state.usuario}**")
col1, col2 = st.columns(2)
col1.metric("📋 Casos finalizados", contar_casos_usuario(st.session_state.usuario))
if st.session_state.media_usuario == 0:
    st.session_state.media_usuario = calcular_media_usuario(st.session_state.usuario)
col2.metric("📊 Média global", st.session_state.media_usuario)

# ===== ESPECIALIDADE =====
esp = st.radio("Especialidade:", ["PSF", "Pediatria", "Emergências"])
assistant_id = {
    "PSF": ASSISTANT_ID,
    "Pediatria": ASSISTANT_PEDIATRIA_ID,
    "Emergências": ASSISTANT_EMERGENCIAS_ID
}[esp]
st.session_state.especialidade_atual = esp

# ===== CONTAGEM POR ESPECIALIDADE =====
dados = LOG_SHEET.get_all_records()
usuario = st.session_state.usuario.lower()
total_consultas = sum(1 for l in dados if l.get("usuario", "").lower() == usuario)
total_especialidade = sum(1 for l in dados if l.get("usuario", "").lower() == usuario and l.get("assistente", "").strip().lower() == esp.lower())

if total_consultas > 0:
    percentual = (total_especialidade / total_consultas) * 100
    st.success(f"📈 Foram realizadas **{total_especialidade}** consultas de **{esp}**, de um total de **{total_consultas}**. Isso representa **{percentual:.1f}%** dos seus atendimentos.")
else:
    st.info("ℹ️ Nenhuma consulta finalizada ainda para este usuário.")

# ===== NOVA SIMULAÇÃO =====
if st.button("➕ Nova Simulação"):
    st.session_state.thread_id = openai.beta.threads.create().id
    st.session_state.consulta_finalizada = False
    resumos = obter_ultimos_resumos(st.session_state.usuario, esp, 10)
    contexto = "\n".join(resumos) if resumos else ""
    contexto = contexto[:4000]
    if contexto and st.session_state.thread_id:
        try:
            openai.beta.threads.messages.create(
                thread_id=st.session_state.thread_id,
                role="user",
                content=f"Casos anteriores do aluno:\n{contexto}"
            )
        except Exception as e:
            st.warning(f"⚠️ Erro ao enviar contexto anterior: {e}")
    run = openai.beta.threads.runs.create(thread_id=st.session_state.thread_id, assistant_id=assistant_id)
    aguardar_run(st.session_state.thread_id)
    mensagens = openai.beta.threads.messages.list(thread_id=st.session_state.thread_id).data
    for m in mensagens:
        if m.role == "assistant" and hasattr(m, "content") and m.content:
            st.session_state.historico = m.content[0].text.value
            break
    st.rerun()

# ===== HISTÓRICO =====
if st.session_state.historico and not st.session_state.consulta_finalizada:
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

# ===== FINALIZAR CONSULTA =====
if st.session_state.thread_id and not st.session_state.consulta_finalizada:
    if st.button("✅ Finalizar Consulta"):
        openai.beta.threads.messages.create(
            thread_id=st.session_state.thread_id,
            role="user",
            content=("Gerar prontuário completo, feedback educacional com fundamentos com diretrizes médicas, "
                     "notas ponderadas por etapa e nota final no formato **Nota: X/10**.")
        )
        run = openai.beta.threads.runs.create(thread_id=st.session_state.thread_id, assistant_id=assistant_id)
        aguardar_run(st.session_state.thread_id)
        msgs = openai.beta.threads.messages.list(thread_id=st.session_state.thread_id).data
        resposta_final = None
        for m in sorted(msgs, key=lambda x: getattr(x, 'created_at', 0), reverse=True):
            if m.role == "assistant" and hasattr(m, "content") and m.content:
                texto = m.content[0].text.value.strip()
                if re.search(r"nota\\s*[:\\-]?\\s*\\d+(?:[.,]\\d+)?", texto, re.I) or "feedback educacional" in texto.lower():
                    resposta_final = texto
                    break
        if resposta_final:
            with st.chat_message("assistant", avatar="🧑‍⚕️"):
                st.markdown("### 📄 Resultado Final")
                st.markdown(resposta_final)
            st.session_state.consulta_finalizada = True
            registrar_caso(st.session_state.usuario, resposta_final, st.session_state.especialidade_atual)
            nota = extrair_nota(resposta_final)
            if nota is not None:
                salvar_nota_usuario(st.session_state.usuario, nota)
                st.session_state.media_usuario = calcular_media_usuario(st.session_state.usuario)
        else:
            st.warning("⚠️ Não foi possível encontrar uma resposta final com nota.")
