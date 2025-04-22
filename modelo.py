import streamlit as st
import unicodedata
import time, re, openai
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
    padrao_final = re.search(r"(?:nota\s*(?:estimada|final)?\s*[:\-]?\s*)(\d{1,2}(?:[.,]\d+)?)(?:\s*/\s*10)?", resp, re.IGNORECASE)
    if padrao_final:
        return float(padrao_final.group(1).replace(",", "."))
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
               index=["PSF", "Pediatria", "Emergências"].index(st.session_state.especialidade_atual)
               if st.session_state.especialidade_atual else 0)
if esp != st.session_state.especialidade_atual:
    st.session_state.especialidade_atual = esp
    st.session_state.thread_id = None
    st.session_state.historico = ""
    st.session_state.resposta_final = ""
    st.session_state.consulta_finalizada = False
    st.rerun()

# === HISTÓRICO ===
dados_usuario = obter_dados_usuario(st.session_state.usuario)
contagem_especialidades = contar_por_especialidade(dados_usuario)

if st.button("📜 Meus últimos 10 casos"):
    st.subheader("📄 Histórico de Casos Recentes")
    temas_usados = obter_temas_usados(st.session_state.usuario, st.session_state.especialidade_atual)
    contexto = "\n".join(temas_usados) if temas_usados else "Nenhum tema anterior."
    prompt_inicial = gerar_prompt_por_especialidade(st.session_state.especialidade_atual, contexto)
    if resumos:
        for i, r in enumerate(resumos, 1):
            st.markdown(f"**Caso {i}:** {r}")
    else:
        st.info("Nenhum caso anterior encontrado.")

# === FUNÇÃO GERADORA DO PROMPT INICIAL ADAPTATIVO ===
def gerar_prompt_por_especialidade(especialidade, contexto):
    if especialidade == "Emergências":
        return (
            f"Crie um novo caso clínico completo da especialidade Emergências com base em um dos temas disponíveis no arquivo enviado, evitando os temas já utilizados abaixo. "
            f"O caso deve seguir a estrutura completa: Identificação, Queixa Principal (QP), HDA, exame físico, exames laboratoriais com valores de referência, e exames complementares quando necessário. "
            f"\n\nTemas já utilizados:\n{contexto}"
        )
    elif especialidade in ["Pediatria", "PSF"]:
        return (
            f"Inicie uma nova simulação clínica da especialidade {especialidade}. "
            f"O caso deve ser apresentado passo a passo, começando apenas com a Identificação e a Queixa Principal (QP). "
            f"As demais informações (HDA, exame físico, exames, diagnóstico, conduta) devem ser fornecidas somente quando solicitadas. "
            f"Evite repetir os temas já utilizados pelo estudante listados abaixo.\n\nTemas já utilizados:\n{contexto}"
        )
    else:
        return (
            f"Inicie uma simulação da especialidade {especialidade}, utilizando temas clínicos relevantes. "
            f"Se houver arquivos anexados, utilize-os como base para evitar repetições. "
            f"\n\nTemas já utilizados:\n{contexto}"
        )

# === NOVA SIMULAÇÃO ===
if st.button("➕ Nova Simulação"):
    with st.spinner("⏳ Gerando novo caso clínico..."):
        st.session_state.thread_id = openai.beta.threads.create().id
        st.session_state.consulta_finalizada = False
        st.session_state.historico = ""
        st.session_state.resposta_final = ""

        resumos = obter_ultimos_resumos(st.session_state.usuario, st.session_state.especialidade_atual, 10)
        contexto = "\n".join(resumos) if resumos else "Nenhum caso anterior."

        # Usa a função adaptativa para gerar o prompt
        prompt_inicial = gerar_prompt_por_especialidade(st.session_state.especialidade_atual, contexto)

        if caso_similar(prompt_inicial, resumos):
            st.warning("⚠️ Tema semelhante a um caso recente detectado. Regerando caso...")

        openai.beta.threads.messages.create(thread_id=st.session_state.thread_id, role="user", content=prompt_inicial)
        run = openai.beta.threads.runs.create(thread_id=st.session_state.thread_id, assistant_id={
            "PSF": ASSISTANT_ID,
            "Pediatria": ASSISTANT_PEDIATRIA_ID,
            "Emergências": ASSISTANT_EMERGENCIAS_ID
        }[st.session_state.especialidade_atual])
        aguardar_run(st.session_state.thread_id)

        msgs = openai.beta.threads.messages.list(thread_id=st.session_state.thread_id).data
        for m in reversed(msgs):
            if m.role == "assistant" and m.content and hasattr(m.content[0], "text"):
                st.session_state.historico = m.content[0].text.value
                break
        time.sleep(10)
    st.rerun()

# === EXIBIÇÃO FINAL ===
if st.session_state.resposta_final:
    st.markdown("### 📄 Resultado Final")
    resposta_limpa = st.session_state.resposta_final
    if st.session_state.especialidade_atual == "Emergências":
        if "Crie um novo caso clínico completo da especialidade Emergências" in resposta_limpa:
            resposta_limpa = resposta_limpa.split("Crie um novo caso clínico completo da especialidade Emergências")[1].strip()
    elif st.session_state.especialidade_atual == "Pediatria":
        if "Inicie uma nova simulação clínica da especialidade Pediatria" in resposta_limpa:
            resposta_limpa = resposta_limpa.split("Inicie uma nova simulação clínica da especialidade Pediatria")[1].strip()
    elif st.session_state.especialidade_atual == "PSF":
        if "Inicie uma nova simulação clínica da especialidade PSF" in resposta_limpa:
            resposta_limpa = resposta_limpa.split("Inicie uma nova simulação clínica da especialidade PSF")[1].strip()
    resposta_limpa = resposta_limpa.split("Temas já utilizados:")[0].strip()
    st.markdown(resposta_limpa)

# === CHAT INTERATIVO ===
if st.session_state.thread_id:
    renderizar_historico()
    pergunta = st.chat_input("Digite sua pergunta ou conduta:")
    if pergunta:
        openai.beta.threads.messages.create(thread_id=st.session_state.thread_id, role="user", content=pergunta)
        run = openai.beta.threads.runs.create(
            thread_id=st.session_state.thread_id,
            assistant_id={
                "PSF": ASSISTANT_ID,
                "Pediatria": ASSISTANT_PEDIATRIA_ID,
                "Emergências": ASSISTANT_EMERGENCIAS_ID
            }[st.session_state.especialidade_atual]
        )
        aguardar_run(st.session_state.thread_id)
        st.rerun()

if not st.session_state.consulta_finalizada:
    if st.button("✅ Finalizar Consulta"):
        with st.spinner("⏳ Gerando prontuário final..."):
            prompt_final = (
                "⚠️ ATENÇÃO: Finalize agora a simulação clínica. "
                "Gere feedback educacional de acordo com o que o usuário conduziu, justifique com diretrizes médicas "
                "e forneça notas por etapa, finalizando com **Nota: X/10**."
            )
            timestamp_envio = datetime.now(timezone.utc).timestamp()
            openai.beta.threads.messages.create(
                thread_id=st.session_state.thread_id,
                role="user",
                content=prompt_final
            )
            run = openai.beta.threads.runs.create(
                thread_id=st.session_state.thread_id,
                assistant_id={
                    "PSF": ASSISTANT_ID,
                    "Pediatria": ASSISTANT_PEDIATRIA_ID,
                    "Emergências": ASSISTANT_EMERGENCIAS_ID
                }[st.session_state.especialidade_atual]
            )
            aguardar_run(st.session_state.thread_id)
            time.sleep(15)
            msgs = openai.beta.threads.messages.list(thread_id=st.session_state.thread_id).data
            resposta = None
            for m in sorted(msgs, key=lambda x: x.created_at, reverse=True):
                if m.role == "assistant" and m.created_at > timestamp_envio:
                    if m.content and hasattr(m.content[0], "text"):
                        resposta = m.content[0].text.value.strip()
                        break

            if resposta:
                st.session_state.consulta_finalizada = True
                st.session_state.resposta_final = resposta

                # === LIMPEZA DA RESPOSTA ===
                resposta_limpa = resposta
                if st.session_state.especialidade_atual == "Emergências":
                    if "Crie um novo caso clínico completo da especialidade Emergências" in resposta_limpa:
                        resposta_limpa = resposta_limpa.split("Crie um novo caso clínico completo da especialidade Emergências")[1].strip()
                elif st.session_state.especialidade_atual == "Pediatria":
                    if "Inicie uma nova simulação clínica da especialidade Pediatria" in resposta_limpa:
                        resposta_limpa = resposta_limpa.split("Inicie uma nova simulação clínica da especialidade Pediatria")[1].strip()
                elif st.session_state.especialidade_atual == "PSF":
                    if "Inicie uma nova simulação clínica da especialidade PSF" in resposta_limpa:
                        resposta_limpa = resposta_limpa.split("Inicie uma nova simulação clínica da especialidade PSF")[1].strip()
                resposta_limpa = resposta_limpa.split("Temas já utilizados:")[0].strip()

                registrar_caso(st.session_state.usuario, resposta_limpa, st.session_state.especialidade_atual)
                salvar_media_global(st.session_state.usuario)
                st.session_state.media_usuario = calcular_media_usuario(st.session_state.usuario)
                dados_usuario = obter_dados_usuario(st.session_state.usuario)
                contagem_especialidades = contar_por_especialidade(dados_usuario)
                tema = extrair_tema(resposta_limpa)
                registrar_caso(st.session_state.usuario, resposta_limpa, st.session_state.especialidade_atual, tema)
                st.rerun()
            else:
                st.error("⚠️ A IA não retornou uma resposta válida. Tente novamente.")




