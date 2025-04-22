import streamlit as st
import unicodedata

from datetime import datetime, timezone
from supabase import create_client
from difflib import SequenceMatcher
import matplotlib.pyplot as plt
from collections import defaultdict

# ===== CONFIGURAÇÕES =====
st.set_page_config(page_title="Bem-vindo ao SIMULAMAX – Simulador Médico IA", page_icon="🢕", layout="wide")

# Supabase
url = st.secrets["supabase"]["url"]
key = st.secrets["supabase"]["api_key"]
supabase = create_client(url, key)

# OpenAI
openai.api_key = st.secrets["openai"]["api_key"]
ASSISTANT_ID = st.secrets["assistants"]["default"]
ASSISTANT_PEDIATRIA_ID = st.secrets["assistants"]["pediatria"]
ASSISTANT_EMERGENCIAS_ID = st.secrets["assistants"]["emergencias"]

# ===== ESTADO =====
DEFAULTS = {
    "logado": False,
    "thread_id": None,
    "historico": "",
    "consulta_finalizada": False,
    "prompt_inicial": "",
    "media_usuario": 0.0,
    "run_em_andamento": False,
    "especialidade_atual": "",
    "resposta_final": ""
}
for k, v in DEFAULTS.items():
    st.session_state.setdefault(k, v)

# ===== FUNÇÕES SUPABASE =====
def validar_credenciais(user, pwd):
    result = supabase.table("usuarios").select("*").eq("usuario", user).eq("senha", pwd).execute()
    return bool(result.data)

def extrair_tema(texto):
    match = re.search(r"(?:Tema|Assunto|Caso|Paciente)\s*[:\-]?\s*(.+)", texto, re.IGNORECASE)
    if match:
        return match.group(1).split("\n")[0].strip()
    return " ".join(texto.strip().split("\n")[0].split()[:10])

def registrar_caso(user, texto, especialidade, tema):
    nota = extrair_nota(texto)
    datahora = datetime.now().isoformat()
    supabase.table("logs_simulacoes").insert({
        "usuario": user,
        "especialidade": especialidade,
        "tema": tema,
        "nota": nota,
        "resposta": texto,
        "data_hora": datahora
    }).execute()

def salvar_media_global(user):
    result = supabase.table("logs_simulacoes").select("nota").eq("usuario", user).execute()
    notas = [float(n["nota"]) for n in result.data if n.get("nota") is not None]
    media = round(sum(notas)/len(notas), 2) if notas else 0.0
    supabase.table("notas_finais").upsert({
        "usuario": user,
        "media_global": media,
        "data_hora": datetime.now().isoformat()
    }, on_conflict=["usuario"]).execute()

def calcular_media_usuario(user):
    result = supabase.table("notas_finais").select("media_global").eq("usuario", user).execute()
    if result.data and "media_global" in result.data[0]:
        return float(result.data[0]["media_global"])
    return 0.0

def obter_dados_usuario(usuario):
    result = supabase.table("logs_simulacoes").select("especialidade, resposta, data_hora").eq("usuario", usuario).order("data_hora", desc=True).execute()
    return result.data

def obter_temas_usados(user, especialidade, n=10):
    result = supabase.table("logs_simulacoes").select("tema").eq("usuario", user).eq("especialidade", especialidade).order("data_hora", desc=True).limit(n).execute()
    return [t["tema"] for t in result.data if t.get("tema")]

def extrair_nota(resp):
    # Expressões que capturam "Nota: X", "Nota final: X", com ou sem "/10"
    padrao = re.findall(
        r"(?:nota\s*(?:final|estimada)?\s*[:\-]?\s*)(\d{1,2}(?:[.,]\d+)?)(?:\s*/\s*10)?",
        resp, re.IGNORECASE
    )

    if padrao:
        return float(padrao[-1].replace(",", "."))  # <-- pega a última ocorrência

    # Fallback: números no formato X/10
    padrao_fallback = re.findall(r"\b(\d{1,2}(?:[.,]\d+)?)\s*/\s*10\b", resp)
    if padrao_fallback:
        return float(padrao_fallback[-1].replace(",", "."))

    return None

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
        content_text = m.content[0].text.value.strip()
        if any(frase in content_text for frase in [
            "Inicie uma nova simulação clínica da especialidade",
            "Crie um novo caso clínico completo da especialidade",
            "Temas já utilizados",
            "Finalize agora a simulação clínica",
            "Gere feedback educacional"]):
            continue
        hora = datetime.fromtimestamp(m.created_at).strftime("%H:%M")
        avatar = "👨‍⚕️" if m.role == "user" else "🧑‍⚕️"
        with st.chat_message(m.role, avatar=avatar):
            st.markdown(content_text)
            st.caption(f"⏰ {hora}")

def obter_temas_usados(user, especialidade, n=10):
    result = supabase.table("logs_simulacoes").select("tema").eq("usuario", user).eq("especialidade", especialidade).order("data_hora", desc=True).limit(n).execute()
    return [t["tema"] for t in result.data if t.get("tema")]

def caso_similar(novo_prompt, historico, limite_similaridade=0.75):
    for texto_antigo in historico:
        similaridade = SequenceMatcher(None, novo_prompt.lower(), texto_antigo.lower()).ratio()
        if similaridade > limite_similaridade:
            return True
    return False

def contar_por_especialidade(dados):
    contagem = defaultdict(int)
    for r in dados:
        if r.get("especialidade"):
            contagem[r["especialidade"]] += 1
    return dict(contagem)

# ===== LOGIN =====
if not st.session_state.logado:
    st.title("🔐 Simulamax - Simulador Médico – Login")
    with st.form("login"):
        u = st.text_input("Usuário")
        s = st.text_input("Senha", type="password")
        submit = st.form_submit_button("Entrar")
        if submit:
            if validar_credenciais(u, s):
                st.session_state.usuario = u
                st.session_state.logado = True
                st.rerun()
            else:
                st.warning("Usuário ou senha inválidos.")
    st.stop()

# ===== DASHBOARD =====
st.title("🩺 Simulador Médico Interativo com IA")
st.markdown(f"👤 Usuário: **{st.session_state.usuario}**")
col1, col2 = st.columns(2)
col1.metric("📋 Casos finalizados", len(obter_dados_usuario(st.session_state.usuario)))
if st.session_state.media_usuario == 0:
    st.session_state.media_usuario = calcular_media_usuario(st.session_state.usuario)
col2.metric("📊 Média global", st.session_state.media_usuario)

# ===== ESPECIALIDADE =====
esp = st.radio("Especialidade:", ["PSF", "Pediatria", "Emergências"],
               index=["PSF", "Pediatria", "Emergências"].




