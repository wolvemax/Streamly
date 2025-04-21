def renderizar_historico():
    if not st.session_state.thread_id:
        return

    msgs = openai.beta.threads.messages.list(thread_id=st.session_state.thread_id).data

    for m in sorted(msgs, key=lambda x: x.created_at):
        # ⛔️ Pula mensagens sem conteúdo
        if not hasattr(m, "content") or not m.content:
            continue
        conteudo_raw = m.content[0]

        # ⛔️ Pula se o item não for do tipo texto (pode ser imagem ou erro futuro)
        if not hasattr(conteudo_raw, "text") or not hasattr(conteudo_raw.text, "value"):
            continue

        conteudo = conteudo_raw.text.value

        if "Iniciar nova simulação clínica" in conteudo:
            continue

        hora = datetime.fromtimestamp(m.created_at).strftime("%H:%M")
        avatar = "👨‍⚕️" if m.role == "user" else "🧑‍⚕️"
        with st.chat_message(m.role, avatar=avatar):
            st.markdown(conteudo)
            st.caption(f"⏰ {hora}")
