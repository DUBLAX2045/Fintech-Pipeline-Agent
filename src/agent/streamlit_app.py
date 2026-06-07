"""
streamlit_app.py — Interfaz Streamlit del agente (version alternativa/legacy).
Redirige a app.py que es la version principal actualizada.

Usar: streamlit run src/agent/app.py
"""
import streamlit as st

st.set_page_config(page_title="Fintech 360", page_icon="🏦")
st.warning(
    "Este archivo es una version legacy. "
    "Usa la interfaz principal:\n\n"
    "```bash\nstreamlit run src/agent/app.py\n```"
)
st.info("La version principal incluye verificacion de Ollama, dashboard y pagina de configuracion.")
