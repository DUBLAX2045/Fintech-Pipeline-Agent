"""
app.py — Dashboard Streamlit del Agente Fintech.

REQUERIDO antes de iniciar:
  1. Pipeline ejecutado: python src/run_pipeline.py
  2. Ollama corriendo:   ollama serve
  3. Modelo descargado:  ollama pull llama3.2

Iniciar dashboard:
  streamlit run src/agent/app.py
"""

import sys
from pathlib import Path

# Paths para imports
sys.path.insert(0, str(Path(__file__).resolve().parents[0]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import os
import re as _re
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import plotly.graph_objects as go
import streamlit as st
import requests
from src.io.parquet_io import resolve_latest_parquet

# ── Configuración de página ───────────────────────────────────────────────────
st.set_page_config(
    page_title="Fintech 360 - Decision Console",
    page_icon="F",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown("""
<style>
/* ═══════════════════════════════════════════════════════════════════════════
   FINTECH 360 — Terminal Financiero Oscuro
   Palette: #050a14 bg · #10b981 emerald · #e2e8f0 text
   ═══════════════════════════════════════════════════════════════════════════ */

/* ── Tokens ──────────────────────────────────────────────────────────────── */
:root {
  --bg:     #050a14;
  --bg-1:   #0c1220;
  --bg-2:   #111827;
  --bg-3:   #1a2332;
  --em:     #10b981;
  --em-g:   #34d399;
  --em-d:   rgba(16,185,129,.08);
  --em-b:   rgba(16,185,129,.18);
  --em-b2:  rgba(16,185,129,.38);
  --amber:  #f59e0b;
  --coral:  #ef4444;
  --ind:    #818cf8;
  --t1:     #e2e8f0;
  --t2:     #64748b;
  --t3:     #334155;
  --bdr:    rgba(255,255,255,.06);
  --mono:   'JetBrains Mono','SF Mono',ui-monospace,monospace;
}

/* ── Keyframes ───────────────────────────────────────────────────────────── */
@keyframes fadeUp {
  from { opacity:0; transform:translateY(10px); }
  to   { opacity:1; transform:translateY(0); }
}
@keyframes em-pulse {
  0%,100% { box-shadow:0 0 0 0   rgba(16,185,129,.5); }
  50%     { box-shadow:0 0 0 5px rgba(16,185,129,0); }
}
@keyframes cursor-blink {
  0%,100% { opacity:1; } 50% { opacity:0; }
}

/* ── Base ────────────────────────────────────────────────────────────────── */
.stApp, .main { background:var(--bg) !important; color:var(--t1); }
.block-container { padding-top:1.4rem !important; max-width:1440px !important; }
[data-testid="stHeader"] { display:none !important; }
footer { display:none !important; }

/* ── Sidebar ─────────────────────────────────────────────────────────────── */
section[data-testid="stSidebar"] {
  background: var(--bg-1) !important;
  border-right: 1px solid var(--em-b) !important;
}
section[data-testid="stSidebar"] * { color:var(--t1) !important; }
section[data-testid="stSidebar"] ::-webkit-scrollbar { width:3px; }
section[data-testid="stSidebar"] ::-webkit-scrollbar-thumb { background:var(--em-b2); border-radius:2px; }

/* ── Sidebar nav: botones primario = activo / secundario = inactivo ──────── */
section[data-testid="stSidebar"] [data-testid="baseButton-secondary"] {
  background:transparent !important;
  border:none !important;
  border-radius:6px !important;
  color:var(--t2) !important;
  font-size:.83rem !important;
  font-weight:500 !important;
  text-align:left !important;
  justify-content:flex-start !important;
  padding:8px 10px !important;
  min-height:unset !important;
  box-shadow:none !important;
  transition:all .15s ease !important;
}
section[data-testid="stSidebar"] [data-testid="baseButton-secondary"]:hover {
  background:var(--em-d) !important;
  color:var(--em-g) !important;
  box-shadow:none !important;
}
section[data-testid="stSidebar"] [data-testid="baseButton-primary"] {
  background:var(--em-d) !important;
  border:1px solid var(--em-b2) !important;
  border-left:3px solid var(--em) !important;
  border-radius:6px !important;
  color:var(--em) !important;
  font-size:.83rem !important;
  font-weight:700 !important;
  text-align:left !important;
  justify-content:flex-start !important;
  padding:8px 10px !important;
  min-height:unset !important;
  box-shadow:none !important;
}
/* ── Botón reload al fondo de sidebar ────────────────────────────────────── */
section[data-testid="stSidebar"] .stButton:last-of-type [data-testid="baseButton-secondary"] {
  color:var(--t3) !important;
  font-size:.75rem !important;
  font-family:var(--mono) !important;
}
section[data-testid="stSidebar"] .stButton:last-of-type [data-testid="baseButton-secondary"]:hover {
  color:var(--t2) !important;
}

/* ── Brand ───────────────────────────────────────────────────────────────── */
.brand-lockup {
  padding:.9rem .2rem 1rem;
  border-bottom:1px solid var(--bdr);
  margin-bottom:.6rem;
}
.brand-mark {
  width:32px; height:32px; border-radius:7px;
  background:var(--em);
  display:inline-flex; align-items:center; justify-content:center;
  font-family:var(--mono); font-weight:900; font-size:.9rem;
  color:#050a14; margin-right:.65rem; flex-shrink:0;
}
.brand-title  { font-size:.95rem; font-weight:700; color:var(--t1); letter-spacing:.01em; }
.brand-caption{ font-family:var(--mono); color:#4e7065; font-size:.65rem; margin-top:.15rem; letter-spacing:.06em; }

/* ── Sidebar labels ──────────────────────────────────────────────────────── */
.sidebar-label {
  font-family:var(--mono);
  font-size:.6rem;
  letter-spacing:.14em;
  color:#5a8a78;
  text-transform:uppercase;
  padding:.5rem .2rem .3rem;
  display:block;
}

/* ── Status service cards ────────────────────────────────────────────────── */
.srv-card {
  background:rgba(255,255,255,.025);
  border:1px solid rgba(16,185,129,.12);
  border-radius:5px;
  padding:.48rem .65rem;
  margin-bottom:.38rem;
  display:flex;
  justify-content:space-between;
  align-items:center;
  transition:all .2s;
  box-shadow:0 0 10px rgba(16,185,129,.06);
}
.srv-card:hover { border-color:var(--em-b); box-shadow:0 0 16px rgba(16,185,129,.12); }
.srv-name   { font-family:var(--mono); font-size:.68rem; color:#94bfb2; }
.srv-detail { font-family:var(--mono); font-size:.6rem;  color:#4e7065; margin-top:.08rem; }
.pill {
  font-family:var(--mono); font-size:.6rem; font-weight:700;
  border-radius:3px; padding:.13rem .38rem;
  display:inline-flex; align-items:center; gap:.28rem;
  flex-shrink:0;
}
.pill::before { content:''; width:5px; height:5px; border-radius:50%; display:inline-block; }
.pill-ok   { background:rgba(16,185,129,.12); color:var(--em-g); }
.pill-ok::before   { background:var(--em); animation:em-pulse 2s ease-in-out infinite; }
.pill-warn { background:rgba(239,68,68,.12); color:#f87171; }
.pill-warn::before { background:#ef4444; }

/* ── Dividers ────────────────────────────────────────────────────────────── */
[data-testid="stDivider"] hr, hr { border-color:var(--bdr) !important; }

/* ── Hero ────────────────────────────────────────────────────────────────── */
.hero-wrap {
  padding:1.6rem 0 1.2rem;
  border-bottom:1px solid var(--bdr);
  margin-bottom:1.2rem;
  animation:fadeUp .4s ease both;
}
.hero-eyebrow {
  font-family:var(--mono);
  font-size:.65rem;
  color:var(--em);
  letter-spacing:.16em;
  text-transform:uppercase;
  margin-bottom:.55rem;
  display:flex;
  align-items:center;
  gap:.5rem;
}
.hero-eyebrow::before {
  content:'';
  width:6px; height:6px; border-radius:50%;
  background:var(--em);
  display:inline-block;
  animation:em-pulse 2.2s ease-in-out infinite;
  flex-shrink:0;
}
.hero-h1 {
  font-size:clamp(1.9rem,3vw,2.9rem);
  font-weight:800;
  letter-spacing:-.035em;
  line-height:1.0;
  color:var(--t1);
  margin:.25rem 0 .55rem;
}
.hero-h1 em { font-style:normal; color:var(--em); }
.hero-sub {
  color:var(--t2);
  font-size:.9rem;
  max-width:660px;
  line-height:1.55;
}
.hero-chips { display:flex; flex-wrap:wrap; gap:.35rem; margin-top:.85rem; }
.hero-chip {
  font-family:var(--mono);
  font-size:.64rem;
  color:#7fb3a0;
  border:1px solid rgba(16,185,129,.28);
  border-radius:3px;
  padding:.2rem .5rem;
  letter-spacing:.06em;
  box-shadow:0 0 8px rgba(16,185,129,.12), inset 0 0 8px rgba(16,185,129,.04);
  transition:all .2s ease;
}
.hero-chip:hover {
  color:var(--em-g);
  border-color:rgba(16,185,129,.55);
  box-shadow:0 0 14px rgba(16,185,129,.22), inset 0 0 10px rgba(16,185,129,.08);
}

/* ── KPI cards ───────────────────────────────────────────────────────────── */
.kpi-card {
  background:var(--bg-1);
  border:1px solid rgba(16,185,129,.15);
  border-radius:7px;
  padding:.9rem 1rem;
  position:relative;
  overflow:hidden;
  animation:fadeUp .45s ease both;
  transition:all .25s ease;
  box-shadow:0 0 14px rgba(16,185,129,.07);
}
.kpi-card:hover {
  border-color:rgba(16,185,129,.4);
  box-shadow:0 0 22px rgba(16,185,129,.14);
}
.kpi-card::before {
  content:''; position:absolute; top:0; left:0; right:0; height:2px;
}
.kpi-card[data-accent="teal"]::before  { background:var(--em); }
.kpi-card[data-accent="green"]::before { background:#22c55e; }
.kpi-card[data-accent="amber"]::before { background:var(--amber); }
.kpi-card[data-accent="coral"]::before { background:var(--coral); }
.kpi-label {
  font-family:var(--mono); font-size:.62rem;
  letter-spacing:.12em; text-transform:uppercase;
  color:#5d8a78; margin-bottom:.5rem;
}
.kpi-value {
  font-family:var(--mono); font-size:1.75rem; font-weight:700;
  color:var(--t1); line-height:1; letter-spacing:-.03em;
}
.kpi-detail { font-family:var(--mono); font-size:.65rem; color:#4e7065; margin-top:.3rem; }

/* ── Section headings ────────────────────────────────────────────────────── */
.section-heading {
  font-family:var(--mono);
  font-size:.62rem;
  letter-spacing:.16em;
  text-transform:uppercase;
  color:#6da090;
  padding:.35rem 0;
  border-bottom:1px solid rgba(16,185,129,.15);
  margin:1.5rem 0 .8rem;
  display:flex;
  align-items:center;
  gap:.5rem;
  text-shadow:0 0 18px rgba(16,185,129,.2);
}
.section-heading::before {
  content:''; display:inline-block;
  width:14px; height:2px; background:var(--em);
  border-radius:1px; flex-shrink:0;
}

/* ── Buttons (área principal) ────────────────────────────────────────────── */
.stButton > button {
  border-radius:5px !important;
  background:var(--bg-2) !important;
  border:1px solid var(--bdr) !important;
  color:var(--t2) !important;
  font-size:.8rem !important;
  font-weight:500 !important;
  min-height:2.3rem !important;
  transition:all .15s ease !important;
  box-shadow:none !important;
}
.stButton > button:hover {
  border-color:var(--em-b2) !important;
  color:var(--em-g) !important;
  background:var(--em-d) !important;
  transform:none !important;
  box-shadow:none !important;
}
.stButton > button:active { transform:translateY(1px) !important; }

/* ── Chat messages ───────────────────────────────────────────────────────── */
[data-testid="stChatMessage"] {
  background:var(--bg-1) !important;
  border:1px solid var(--bdr) !important;
  border-radius:7px !important;
  animation:fadeUp .25s ease both !important;
}
div[data-testid="stChatMessageContent"] * { color:var(--t1) !important; }
div[data-testid="stChatMessageContent"] strong { color:var(--em-g) !important; }
div[data-testid="stChatMessageContent"] pre *,
div[data-testid="stChatMessageContent"] code * { color:var(--em-g) !important; }

/* ── Chat input ──────────────────────────────────────────────────────────── */
[data-testid="stChatInput"] textarea {
  background:var(--bg-1) !important;
  border:1px solid var(--em-b) !important;
  color:var(--t1) !important;
  border-radius:7px !important;
  font-family:var(--mono) !important;
  font-size:.85rem !important;
}
[data-testid="stChatInput"] textarea:focus {
  border-color:var(--em) !important;
  box-shadow:0 0 0 2px rgba(16,185,129,.12) !important;
}

/* ── Expanders ───────────────────────────────────────────────────────────── */
[data-testid="stExpander"] {
  background:var(--bg-1) !important;
  border:1px solid var(--bdr) !important;
  border-radius:7px !important;
}
[data-testid="stExpander"] summary { color:var(--t2) !important; }
[data-testid="stExpander"] label,
[data-testid="stExpander"] p { color:var(--t1) !important; }

/* ── Multiselect ─────────────────────────────────────────────────────────── */
[data-baseweb="tag"] { background:var(--em-d) !important; border-color:var(--em-b2) !important; }
[data-baseweb="tag"] span { color:var(--em-g) !important; }
[data-testid="stMultiSelect"] [data-baseweb="select"] div {
  background:var(--bg-2) !important; border-color:var(--bdr) !important; color:var(--t1) !important;
}
[data-testid="stMultiSelect"] label,
[data-testid="stSelectbox"]   label { color:var(--t2) !important; }

/* ── Code blocks ─────────────────────────────────────────────────────────── */
[data-testid="stCode"],
[data-testid="stCodeBlock"] { background:var(--bg-2) !important; border:1px solid var(--bdr) !important; border-radius:5px !important; }
[data-testid="stCode"] *,
[data-testid="stCodeBlock"] * { color:var(--em-g) !important; font-family:var(--mono) !important; }
.stMarkdown pre, .stMarkdown code { color:var(--em-g) !important; background:var(--bg-2) !important; }

/* ── Alerts ──────────────────────────────────────────────────────────────── */
[data-testid="stAlert"] { background:var(--bg-1) !important; border-radius:6px !important; }

/* ── Subheaders / headings default ──────────────────────────────────────── */
[data-testid="stHeadingWithActionElements"] h2,
[data-testid="stHeadingWithActionElements"] h3 { color:var(--t2) !important; font-size:.9rem !important; font-weight:600 !important; }

/* ── Success / warning messages ──────────────────────────────────────────── */
div[data-testid="stMarkdownContainer"] p { color:var(--t2) !important; }

/* ── Scrollbar ───────────────────────────────────────────────────────────── */
::-webkit-scrollbar { width:5px; height:5px; }
::-webkit-scrollbar-track { background:transparent; }
::-webkit-scrollbar-thumb { background:var(--em-b2); border-radius:3px; }

/* ── Skeleton loader ─────────────────────────────────────────────────────── */
@keyframes shimmer {
  0%   { background-position:-600px 0; }
  100% { background-position: 600px 0; }
}
.skeleton {
  background:linear-gradient(90deg,var(--bg-2) 25%,var(--bg-3) 50%,var(--bg-2) 75%);
  background-size:1200px 100%;
  animation:shimmer 1.6s ease-in-out infinite;
  border-radius:5px;
}
.sk-kpi  { height:82px; width:100%; }
.sk-chart{ height:260px; width:100%; }
.sk-line { height:14px; border-radius:3px; margin-bottom:.45rem; }
.sk-line.w70 { width:70%; }
.sk-line.w45 { width:45%; }
.sk-grid { display:grid; gap:.6rem; }

/* ── Plotly tweaks ───────────────────────────────────────────────────────── */
[data-testid="stPlotlyChart"] { border-radius:7px; overflow:hidden; }
[data-testid="stPlotlyChart"] > div { border-radius:7px; }

/* ── Data table ──────────────────────────────────────────────────────────── */
.dt-wrap {
  overflow-x:auto;
  border:1px solid var(--bdr);
  border-radius:7px;
  animation:fadeUp .4s ease both;
}
.dt-table { width:100%; border-collapse:collapse; }
.dt-th {
  font-family:var(--mono);
  font-size:.58rem;
  letter-spacing:.12em;
  text-transform:uppercase;
  color:#5d8a78;
  padding:.55rem .9rem;
  text-align:left;
  border-bottom:1px solid rgba(16,185,129,.15);
  background:var(--bg-2);
  white-space:nowrap;
}
.dt-td {
  font-family:var(--mono);
  font-size:.76rem;
  color:var(--t1);
  padding:.48rem .9rem;
  border-bottom:1px solid rgba(255,255,255,.025);
  white-space:nowrap;
}
.dt-tr:last-child .dt-td { border-bottom:none; }
.dt-tr:hover .dt-td { background:var(--em-d); }
.dt-td.accent-teal  { color:var(--em-g)  !important; }
.dt-td.accent-amber { color:var(--amber) !important; }
.dt-td.accent-coral { color:var(--coral) !important; }
.dt-td.accent-ind   { color:var(--ind)   !important; }
.dt-td.accent-muted { color:var(--t2)    !important; }

/* ── Mini stat card dentro de columna ───────────────────────────────────── */
.mini-stat {
  background:var(--bg-1);
  border:1px solid rgba(16,185,129,.14);
  border-radius:6px;
  padding:.65rem .85rem;
  margin-bottom:.5rem;
  position:relative;
  overflow:hidden;
  transition:all .25s ease;
  box-shadow:0 0 12px rgba(16,185,129,.06);
}
.mini-stat::before { content:''; position:absolute; left:0; top:0; bottom:0; width:2px; }
.mini-stat.ms-teal::before  { background:var(--em);    box-shadow:0 0 8px var(--em); }
.mini-stat.ms-amber::before { background:var(--amber); box-shadow:0 0 8px var(--amber); }
.mini-stat.ms-coral::before { background:var(--coral); box-shadow:0 0 8px var(--coral); }
.mini-stat.ms-ind::before   { background:var(--ind);   box-shadow:0 0 8px var(--ind); }
.mini-stat:hover { border-color:rgba(16,185,129,.35); box-shadow:0 0 20px rgba(16,185,129,.12); }
.ms-label { font-family:var(--mono); font-size:.58rem; letter-spacing:.1em; text-transform:uppercase; color:#5d8a78; }
.ms-value { font-family:var(--mono); font-size:1.25rem; font-weight:700; color:var(--t1); line-height:1.1; margin-top:.2rem; }
.ms-sub   { font-family:var(--mono); font-size:.6rem; color:#4e7065; margin-top:.18rem; }
</style>
""", unsafe_allow_html=True)

ROOT            = Path(__file__).resolve().parents[2]
COLOR_PRINCIPAL = "#10b981"
COLOR_VERDE     = "#34d399"
COLOR_AMBER     = "#f59e0b"
COLOR_CORAL     = "#ef4444"
COLOR_INK       = "#e2e8f0"
COLOR_MUTED     = "#64748b"
OLLAMA_URL      = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL    = os.getenv("OLLAMA_MODEL", "llama3.2")


def _render_agent_response(texto: str) -> None:
    """
    Renderiza la respuesta del agente siempre en este orden:
      1. Gráfico (imagen al tope, máximo ancho)
      2. Tabla de datos Gold certificados
      3. Análisis y conclusión Ollama
    """
    match = _re.search(r'✅ Gráfico guardado: (.+?\.png)', texto)
    if match:
        ruta_img = match.group(1).strip()
        texto_extra = texto.replace(match.group(0), "").strip()
        # 1. Imagen primero
        if os.path.exists(ruta_img):
            st.image(ruta_img, use_container_width=True)
        else:
            st.warning(f"Gráfico no encontrado en disco: {ruta_img}")
        # 2 & 3. Tabla + análisis después del gráfico
        if texto_extra:
            st.markdown(texto_extra)
    else:
        st.markdown(texto)


def _dashboard_test_mode() -> bool:
    return os.getenv("FINTECH_DASHBOARD_TEST_MODE", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }

PREGUNTAS_SUGERIDAS = [
    "Dame el resumen ejecutivo de la plataforma",
    "Cual es el segmento mas rentable?",
    "Que campana lanzarias este mes?",
    "Cuantos usuarios llevan mas de 30 dias sin transaccionar?",
    "Que ciudad tiene mayor potencial de crecimiento?",
    "Analiza la tasa de fallos de pago por segmento",
    "Cual es el merchant con mas oportunidad de alianza?",
    "Muestra la distribucion de usuarios por canal preferido",
]


# ── Estado de servicios ───────────────────────────────────────────────────────
@st.cache_data(ttl=30)
def _check_ollama() -> tuple[bool, str]:
    if _dashboard_test_mode():
        return True, "Modo test: Ollama omitido"
    try:
        r = requests.get(f"{OLLAMA_URL}/api/tags", timeout=4)
        if r.status_code == 200:
            modelos = [m["name"].split(":")[0] for m in r.json().get("models", [])]
            if OLLAMA_MODEL.split(":")[0] in modelos:
                return True, f"{OLLAMA_MODEL} disponible"
            return False, f"Modelo '{OLLAMA_MODEL}' no encontrado. Ejecuta: ollama pull {OLLAMA_MODEL}"
        return False, "Ollama responde pero con error"
    except Exception:
        return False, f"Ollama no responde en {OLLAMA_URL}. Inicia con: ollama serve"


@st.cache_data(ttl=60)
def _check_databricks() -> tuple[bool, str]:
    if _dashboard_test_mode():
        return True, "Modo test: Databricks omitido"
    try:
        from src.config.databricks_config import verificar_conexion
        diag = verificar_conexion()
        if diag["ok"] and diag.get("ready_for_agent", True):
            return True, f"{diag['catalog']}.{diag['schema']} ({diag['duracion_seg']}s)"
        if diag["ok"]:
            faltantes = ", ".join(diag.get("tablas_requeridas_faltantes", []))
            return False, f"Warehouse OK; faltan tablas Gold: {faltantes or 'sin tablas visibles'}"
        return False, f"{diag['error']}"
    except Exception as e:
        return False, f"{e}"


# ── Carga de datos Gold (con manejo de error si no existen) ──────────────────
@st.cache_data
def cargar_datos():
    rutas = {
        "360":    ROOT / "data/gold/gold_user_360.parquet",
        "daily":  ROOT / "data/gold/gold_daily_metrics.parquet",
        "events": ROOT / "data/gold/gold_event_summary.parquet",
    }
    dfs = {}
    for key, ruta in rutas.items():
        ruta_real = resolve_latest_parquet(ruta)
        if ruta_real.exists():
            dfs[key] = pd.read_parquet(ruta_real)
        else:
            dfs[key] = None
    return dfs["360"], dfs["daily"], dfs["events"]


def _fig(figsize=(6, 4)):
    fig, ax = plt.subplots(figsize=figsize)
    fig.patch.set_facecolor("#0c1220")
    ax.set_facecolor("#0c1220")
    ax.spines[["top","right"]].set_visible(False)
    ax.spines["left"].set_color("#1a2332")
    ax.spines["bottom"].set_color("#1a2332")
    ax.tick_params(colors="#64748b", labelsize=9)
    ax.title.set_color("#e2e8f0")
    ax.xaxis.label.set_color("#64748b")
    ax.yaxis.label.set_color("#64748b")
    ax.grid(axis="y", color="#1a2332", linewidth=0.8)
    ax.set_axisbelow(True)
    return fig, ax

def _show(fig):
    plt.tight_layout()
    st.pyplot(fig)
    plt.close(fig)


def _palette(n: int) -> list[str]:
    base = [COLOR_PRINCIPAL, COLOR_VERDE, COLOR_AMBER, COLOR_CORAL, "#818cf8"]
    return [base[i % len(base)] for i in range(n)]


# ── Plotly ────────────────────────────────────────────────────────────────────
_MONO = "JetBrains Mono, ui-monospace, monospace"
_PL_BASE = dict(
    paper_bgcolor="#0c1220",
    plot_bgcolor="#0c1220",
    font=dict(family=_MONO, color="#64748b", size=10),
    margin=dict(l=0, r=0, t=28, b=0),
    hoverlabel=dict(
        bgcolor="#111827", bordercolor="#1a2332",
        font=dict(color="#e2e8f0", family=_MONO, size=11),
    ),
    colorway=["#10b981","#34d399","#f59e0b","#ef4444","#818cf8"],
)
_XAXIS = dict(gridcolor="#1a2332", linecolor="#1a2332", tickcolor="#334155",
              tickfont=dict(color="#64748b", family=_MONO, size=9))
_YAXIS = dict(gridcolor="#1a2332", linecolor="#1a2332", tickcolor="#334155",
              tickfont=dict(color="#64748b", family=_MONO, size=9))

def _plotly(fig: go.Figure, height: int = 0) -> None:
    kw = {"use_container_width": True, "config": {"displayModeBar": False}}
    if height:
        kw["height"] = height
    st.plotly_chart(fig, **kw)

def _pl(**overrides) -> dict:
    """Merge _PL_BASE con overrides del gráfico. Overrides siempre ganan — nunca hay claves duplicadas."""
    return {**_PL_BASE, **overrides}


# ── Skeleton loaders ──────────────────────────────────────────────────────────
def _skeleton_dashboard() -> None:
    k1, k2, k3, k4 = st.columns(4)
    for col in [k1, k2, k3, k4]:
        with col:
            st.markdown('<div class="skeleton sk-kpi"></div>', unsafe_allow_html=True)
    st.markdown('<div style="height:.8rem;"></div>', unsafe_allow_html=True)
    c1, c2 = st.columns([3, 2])
    with c1:
        st.markdown('<div class="skeleton sk-chart"></div>', unsafe_allow_html=True)
    with c2:
        st.markdown(
            '<div class="sk-grid">'
            '<div class="skeleton sk-line"></div>'
            '<div class="skeleton sk-line w70"></div>'
            '<div class="skeleton sk-line w45"></div>'
            '<div class="skeleton sk-chart" style="height:160px;margin-top:.3rem;"></div>'
            '</div>',
            unsafe_allow_html=True,
        )


# ── Styled data table ─────────────────────────────────────────────────────────
def _data_table(df: pd.DataFrame, cols: list[dict]) -> None:
    """
    cols = [{"key": str, "label": str, "fmt": str, "accent": "teal|amber|coral|ind|muted|none"}]
    """
    header = "".join(f'<th class="dt-th">{c["label"]}</th>' for c in cols)
    rows = ""
    for _, row in df.iterrows():
        cells = ""
        for c in cols:
            val = row.get(c["key"], "")
            try:
                display = c.get("fmt", "{}").format(val)
            except Exception:
                display = str(val)
            accent = c.get("accent", "none")
            cls = f"dt-td accent-{accent}" if accent != "none" else "dt-td"
            cells += f'<td class="{cls}">{display}</td>'
        rows += f'<tr class="dt-tr">{cells}</tr>'
    st.markdown(
        f'<div class="dt-wrap"><table class="dt-table">'
        f'<thead><tr>{header}</tr></thead>'
        f'<tbody>{rows}</tbody>'
        f'</table></div>',
        unsafe_allow_html=True,
    )


def _status_badge(ok: bool) -> str:
    cls = "pill-ok" if ok else "pill-warn"
    label = "Activo" if ok else "Revisar"
    return f'<span class="pill {cls}">{label}</span>'


def _render_status_row(nombre: str, detalle: str, ok: bool) -> None:
    st.markdown(
        f"""
        <div class="srv-card">
          <div>
            <div class="srv-name">{nombre}</div>
            <div class="srv-detail">{detalle}</div>
          </div>
          {_status_badge(ok)}
        </div>
        """,
        unsafe_allow_html=True,
    )


def _metric_card(label: str, value: str, detail: str, accent: str) -> None:
    st.markdown(
        f"""
        <div class="kpi-card" data-accent="{accent}">
          <div class="kpi-label">{label}</div>
          <div class="kpi-value">{value}</div>
          <div class="kpi-detail">{detail}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _hero(titulo: str, subtitulo: str, chips: list[str]) -> None:
    chips_html = "".join(f'<span class="hero-chip">{chip}</span>' for chip in chips)
    st.markdown(
        f"""
        <section class="hero-wrap">
          <div class="hero-eyebrow">Fintech 360 &mdash; Decision Console</div>
          <div class="hero-h1">{titulo}</div>
          <p class="hero-sub">{subtitulo}</p>
          <div class="hero-chips">{chips_html}</div>
        </section>
        """,
        unsafe_allow_html=True,
    )


def _section(title: str) -> None:
    st.markdown(f'<div class="section-heading">{title}</div>', unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════════════════════
# SIDEBAR
# ══════════════════════════════════════════════════════════════════════════════
with st.sidebar:
    st.markdown(
        """
        <div class="brand-lockup">
          <div style="display:flex;align-items:center;">
            <div class="brand-mark">F</div>
            <div>
              <div class="brand-title">Fintech 360</div>
              <div class="brand-caption">Decision console</div>
            </div>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    st.markdown('<span class="sidebar-label">Señal operativa</span>', unsafe_allow_html=True)
    ollama_ok, ollama_msg = _check_ollama()
    db_ok, db_msg = _check_databricks()
    _render_status_row("Motor conversacional", ollama_msg, ollama_ok)
    _render_status_row("Warehouse analitico", db_msg, db_ok)

    st.markdown('<span class="sidebar-label">Navegación</span>', unsafe_allow_html=True)

    _PAGES = ["Centro de mando", "Mesa de analisis", "Sistema"]
    if "pagina" not in st.session_state:
        st.session_state["pagina"] = _PAGES[0]

    for _p in _PAGES:
        _active = st.session_state["pagina"] == _p
        if st.button(_p, use_container_width=True, type="primary" if _active else "secondary", key=f"nav_{_p}"):
            st.session_state["pagina"] = _p
            st.rerun()

    pagina = st.session_state["pagina"]

    st.markdown('<span class="sidebar-label" style="margin-top:.8rem;">Datos</span>', unsafe_allow_html=True)
    if st.button("↺  Recargar Gold", use_container_width=True, type="secondary"):
        st.cache_data.clear()
        st.rerun()


# ══════════════════════════════════════════════════════════════════════════════
# PÁGINA: DASHBOARD
# ══════════════════════════════════════════════════════════════════════════════
if pagina == "Centro de mando":

    # ── Skeleton mientras cargan los datos ────────────────────────────────
    _ph = st.empty()
    with _ph.container():
        _hero(
            "Pulso <em>financiero</em> de usuarios",
            "Vista ejecutiva sobre la capa Gold: comportamiento, volumen, fallas y preferencias consolidadas por usuario.",
            ["Gold activo", "Visión 360", "S3 + Databricks", "Near real-time"],
        )
        _skeleton_dashboard()

    df_360, df_daily, df_events = cargar_datos()
    _ph.empty()

    # ── Hero ──────────────────────────────────────────────────────────────
    _hero(
        "Pulso <em>financiero</em> de usuarios",
        "Vista ejecutiva sobre la capa Gold: comportamiento, volumen, fallas y preferencias consolidadas por usuario.",
        ["Gold activo", "Visión 360", "S3 + Databricks", "Near real-time"],
    )

    if df_360 is None:
        st.error("No se encontraron datos Gold. Ejecuta primero: `python src/run_pipeline.py`")
        st.stop()

    # ── Filtros ───────────────────────────────────────────────────────────
    with st.expander("Filtros de análisis", expanded=False):
        col_f1, col_f2 = st.columns(2)
        with col_f1:
            segmentos_disp = sorted(df_360["user_segment"].dropna().unique().tolist())
            segmentos_sel = st.multiselect("Segmento", options=segmentos_disp, default=segmentos_disp, key="filter_segmento")
        with col_f2:
            ciudades_disp = sorted(df_360["city"].dropna().unique().tolist())
            ciudades_sel = st.multiselect("Ciudad", options=ciudades_disp, default=ciudades_disp, key="filter_ciudad")

    if segmentos_sel:
        df_360 = df_360[df_360["user_segment"].isin(segmentos_sel)]
    if ciudades_sel:
        df_360 = df_360[df_360["city"].isin(ciudades_sel)]
    if df_360.empty:
        st.warning("Los filtros no devuelven datos. Ajusta los criterios.")
        st.stop()

    # ── Pre-cómputos ──────────────────────────────────────────────────────
    vol       = df_360["total_amount_cop"].sum() / 1_000_000
    ticket    = df_360["avg_ticket"].mean()
    fallo     = df_360["failure_rate"].mean() * 100
    top_seg   = df_360.groupby("user_segment")["total_amount_cop"].sum().idxmax()
    top_city  = df_360.groupby("city")["user_id"].count().idxmax()

    # ── KPI cards ─────────────────────────────────────────────────────────
    _section("Indicadores clave")
    k1, k2, k3, k4 = st.columns(4)
    with k1:
        _metric_card("Usuarios Gold", f"{len(df_360):,}", "Perfiles consolidados", "teal")
    with k2:
        _metric_card("Volumen COP", f"${vol:,.1f}M", "Suma transaccional exitosa", "green")
    with k3:
        _metric_card("Ticket promedio", f"${ticket:,.0f}", "Promedio por usuario activo", "amber")
    with k4:
        _metric_card("Tasa de fallo", f"{fallo:.1f}%", "Fricción transaccional media", "coral")

    # ── Fila 1: Volumen por segmento (60%) + mini-stats + Donut canal (40%) ──
    _section("Panorama de mercado")
    col_main, col_side = st.columns([3, 2])

    with col_main:
        datos_seg = df_360.groupby("user_segment")["total_amount_cop"].sum().sort_values()
        colors_seg = ["#10b981","#34d399","#f59e0b","#ef4444","#818cf8"][:len(datos_seg)]
        fig = go.Figure(go.Bar(
            x=datos_seg.values / 1e6,
            y=datos_seg.index,
            orientation="h",
            marker=dict(color=colors_seg, opacity=0.9),
            hovertemplate="<b>%{y}</b><br>$%{x:,.2f}M<extra></extra>",
            text=[f"${v/1e6:,.1f}M" for v in datos_seg.values],
            textposition="outside",
            textfont=dict(color="#64748b", size=9, family=_MONO),
        ))
        fig.update_layout(**_pl(showlegend=False, xaxis={**_XAXIS, "title": "Millones COP"}, yaxis=_YAXIS, height=260))
        _plotly(fig)

    with col_side:
        st.markdown(
            f'<div class="mini-stat ms-teal">'
            f'<div class="ms-label">Segmento líder</div>'
            f'<div class="ms-value">{top_seg}</div>'
            f'<div class="ms-sub">mayor volumen acumulado</div>'
            f'</div>'
            f'<div class="mini-stat ms-amber">'
            f'<div class="ms-label">Ciudad principal</div>'
            f'<div class="ms-value">{top_city}</div>'
            f'<div class="ms-sub">mayor concentración de usuarios</div>'
            f'</div>',
            unsafe_allow_html=True,
        )
        # Donut canal preferido
        datos_ch = df_360["preferred_channel"].value_counts()
        fig2 = go.Figure(go.Pie(
            labels=datos_ch.index,
            values=datos_ch.values,
            hole=0.65,
            marker=dict(colors=["#10b981","#34d399","#f59e0b","#ef4444","#818cf8"], line=dict(color="#0c1220", width=2)),
            textfont=dict(color="#64748b", size=9),
            hovertemplate="<b>%{label}</b><br>%{value:,} usuarios (%{percent})<extra></extra>",
        ))
        fig2.update_layout(**_pl(
            showlegend=True,
            legend=dict(orientation="h", x=0, y=-0.15, font=dict(color="#64748b", size=9, family=_MONO)),
            annotations=[dict(text="canal", x=0.5, y=0.5, showarrow=False,
                              font=dict(color="#6da090", size=10, family=_MONO))],
            height=200,
            margin=dict(l=0, r=0, t=6, b=30),
        ))
        _plotly(fig2)

    # ── Fila 2: Comercios (65%) + Ciudad (35%) ────────────────────────────
    _section("Alianzas y distribución geográfica")
    col_merch, col_city = st.columns([13, 10])

    with col_merch:
        datos_m = df_360["top_merchant"].value_counts().head(12).sort_values()
        datos_m = datos_m[datos_m.index.notna() & (datos_m.index != "None")]
        n = len(datos_m)
        bar_colors = [f"rgba(16,185,129,{0.25 + 0.65*(i/max(n-1,1)):.2f})" for i in range(n)]
        fig3 = go.Figure(go.Bar(
            x=datos_m.values,
            y=datos_m.index.astype(str),
            orientation="h",
            marker=dict(color=bar_colors),
            hovertemplate="<b>%{y}</b><br>%{x:,} usuarios<extra></extra>",
            text=datos_m.values,
            textposition="outside",
            textfont=dict(color="#334155", size=9, family=_MONO),
        ))
        fig3.update_layout(**_pl(showlegend=False, xaxis={**_XAXIS, "title": "Usuarios"}, yaxis=_YAXIS, height=320))
        _plotly(fig3)

    with col_city:
        datos_c = df_360.groupby("city")["user_id"].count().sort_values(ascending=False)
        fig4 = go.Figure(go.Bar(
            x=datos_c.index,
            y=datos_c.values,
            marker=dict(
                color=datos_c.values,
                colorscale=[[0,"#111827"],[0.4,"#1a2332"],[1,"#10b981"]],
                showscale=False,
            ),
            hovertemplate="<b>%{x}</b><br>%{y:,} usuarios<extra></extra>",
        ))
        fig4.update_layout(**_pl(showlegend=False, xaxis=_XAXIS, yaxis={**_YAXIS, "title": "Usuarios"}, height=320))
        _plotly(fig4)

    # ── Fila 3: Serie temporal full-width ─────────────────────────────────
    if df_daily is not None and "date" in df_daily.columns and "total_transactions" in df_daily.columns:
        _section("Ritmo operativo — Tendencia diaria")
        y_vals = df_daily["total_transactions"].astype(float)
        x_vals = df_daily["date"].astype(str)
        avg_val = y_vals.mean()
        fig5 = go.Figure()
        fig5.add_trace(go.Scatter(
            x=x_vals, y=y_vals,
            mode="lines+markers",
            name="Transacciones",
            line=dict(color="#10b981", width=2),
            marker=dict(color="#10b981", size=5, line=dict(color="#0c1220", width=1)),
            fill="tozeroy",
            fillcolor="rgba(16,185,129,0.05)",
            hovertemplate="<b>%{x}</b><br>%{y:,.0f} transacciones<extra></extra>",
        ))
        fig5.add_hline(
            y=avg_val, line_dash="dot", line_color="#334155", line_width=1,
            annotation_text=f"Promedio: {avg_val:,.0f}",
            annotation_font=dict(color="#334155", size=9, family=_MONO),
            annotation_position="top right",
        )
        fig5.update_layout(**_pl(
            showlegend=False,
            xaxis={**_XAXIS, "title": "Fecha"},
            yaxis={**_YAXIS, "title": "Transacciones"},
            height=240,
        ))
        _plotly(fig5)

    # ── Fila 4: Top 15 perfiles — tabla styled ────────────────────────────
    _section("Top 15 — Perfiles de mayor volumen")
    _cols_tabla = ["user_id","user_segment","city","total_amount_cop","avg_ticket","failure_rate","preferred_channel"]
    _cols_tabla = [c for c in _cols_tabla if c in df_360.columns]
    top_df = df_360.nlargest(15, "total_amount_cop")[_cols_tabla].reset_index(drop=True)
    _data_table(top_df, [
        {"key": "user_id",           "label": "Usuario",     "fmt": "{}",       "accent": "muted"},
        {"key": "user_segment",      "label": "Segmento",    "fmt": "{}",       "accent": "teal"},
        {"key": "city",              "label": "Ciudad",      "fmt": "{}",       "accent": "none"},
        {"key": "total_amount_cop",  "label": "Volumen COP", "fmt": "${:,.0f}", "accent": "teal"},
        {"key": "avg_ticket",        "label": "Ticket",      "fmt": "${:,.0f}", "accent": "amber"},
        {"key": "failure_rate",      "label": "Fallo %",     "fmt": "{:.1%}",   "accent": "coral"},
        {"key": "preferred_channel", "label": "Canal",       "fmt": "{}",       "accent": "none"},
    ])


# ══════════════════════════════════════════════════════════════════════════════
# PÁGINA: MESA DE ANALISIS
# ══════════════════════════════════════════════════════════════════════════════
elif pagina == "Mesa de analisis":
    _hero(
        "Mesa de analisis conversacional",
        "Haz preguntas de negocio sobre la capa Gold. Las metricas se calculan desde datos reales antes de redactar la respuesta.",
        ["KPIs auditables", "DuckDB local", "Databricks disponible", f"Modelo: {OLLAMA_MODEL}"],
    )

    if not ollama_ok:
        st.error(ollama_msg)
        st.info(
            "Para usar el agente:\n"
            "1. Instala Ollama: https://ollama.com/download\n"
            f"2. Inicia el servidor: `ollama serve`\n"
            f"3. Descarga el modelo: `ollama pull {OLLAMA_MODEL}`"
        )
        st.stop()

    # Preguntas sugeridas
    _section("Acciones rápidas")
    st.markdown(
        '<p style="font-family:var(--mono);font-size:.72rem;color:var(--t3);margin-bottom:.6rem;">Elige una pregunta de negocio o escribe una propia.</p>',
        unsafe_allow_html=True,
    )
    cols = st.columns(2)
    for i, pregunta in enumerate(PREGUNTAS_SUGERIDAS):
        with cols[i % 2]:
            if st.button(pregunta, use_container_width=True):
                st.session_state.setdefault("messages", [])
                st.session_state["messages"].append({"role": "user", "content": pregunta})
                st.session_state["pending_query"] = pregunta

    st.divider()

    # Historial de chat
    if "messages" not in st.session_state:
        st.session_state["messages"] = []

    for msg in st.session_state["messages"]:
        avatar = "👤" if msg["role"] == "user" else "🤖"
        with st.chat_message(msg["role"], avatar=avatar):
            if msg["role"] == "assistant":
                _render_agent_response(msg["content"])
            else:
                st.markdown(msg["content"])

    # Procesar query pendiente (de botones sugeridos)
    if "pending_query" in st.session_state:
        query = st.session_state.pop("pending_query")
        with st.chat_message("assistant", avatar="🤖"):
            with st.spinner("Consultando datos..."):
                try:
                    from agent.agent import agent_query
                    respuesta = agent_query(query)
                except Exception as e:
                    respuesta = f"Error del analista: {e}"
            _render_agent_response(respuesta)
        st.session_state["messages"].append({"role": "assistant", "content": respuesta})
        st.rerun()

    # Input de chat — streaming token a token
    if prompt := st.chat_input("Escribe tu pregunta sobre los datos..."):
        st.session_state["messages"].append({"role": "user", "content": prompt})
        with st.chat_message("user", avatar="👤"):
            st.markdown(prompt)
        with st.chat_message("assistant", avatar="🤖"):
            from agent.agent import stream_agent_query
            contenedor = st.empty()
            texto_acumulado = ""
            try:
                for chunk in stream_agent_query(prompt):
                    texto_acumulado += chunk
                    if "✅ Gráfico guardado:" not in texto_acumulado:
                        contenedor.markdown(texto_acumulado + "▌")
            except Exception as e:
                texto_acumulado = f"Error del analista: {e}"
            contenedor.empty()
            _render_agent_response(texto_acumulado)
        st.session_state["messages"].append({"role": "assistant", "content": texto_acumulado})

    if st.button("Limpiar conversacion"):
        st.session_state["messages"] = []
        from agent.agent import reset_agent
        reset_agent()
        st.rerun()


# ══════════════════════════════════════════════════════════════════════════════
# PÁGINA: CONFIGURACIÓN
# ══════════════════════════════════════════════════════════════════════════════
elif pagina == "Sistema":
    _hero(
        "Estado <em>operativo</em>",
        "Salud en tiempo real de los servicios que sostienen la consola: modelo local, warehouse externo y capa de datos Gold.",
        ["Servicios", "Credenciales", "Catálogo", "Gold"],
    )

    ok_ollama, msg_ollama = _check_ollama()
    ok_db, msg_db = _check_databricks()
    df_360, _, _ = cargar_datos()
    gold_ok = df_360 is not None

    _section("Servicios activos")
    sc1, sc2, sc3 = st.columns(3)
    with sc1:
        _metric_card("Ollama", "Online" if ok_ollama else "Offline", msg_ollama, "teal" if ok_ollama else "coral")
    with sc2:
        _metric_card("Databricks", "Online" if ok_db else "Offline", msg_db, "teal" if ok_db else "coral")
    with sc3:
        gold_label = f"{len(df_360):,} filas" if gold_ok else "Sin datos"
        _metric_card("Gold Layer", "Listo" if gold_ok else "Vacío", gold_label, "teal" if gold_ok else "amber")

    col1, col2 = st.columns(2)

    with col1:
        _section("Motor conversacional — Ollama")
        st.code(f"URL:    {OLLAMA_URL}\nModelo: {OLLAMA_MODEL}", language="bash")
        st.code(
            f"# Iniciar servidor\nollama serve\n\n"
            f"# Descargar modelo\nollama pull {OLLAMA_MODEL}\n\n"
            f"# Ver modelos instalados\nollama list",
            language="bash"
        )

    with col2:
        _section("Warehouse analítico — Databricks")
        host = os.getenv("DATABRICKS_HOST", "(no configurado)")
        catalog = os.getenv("DATABRICKS_CATALOG", "fintech_pipeline")
        schema = os.getenv("DATABRICKS_SCHEMA", "fintech")
        st.code(f"HOST:    {host}\nCATALOG: {catalog}\nSCHEMA:  {schema}", language="bash")
        if st.button("↺  Probar conexión Databricks", use_container_width=True):
            with st.spinner("Conectando…"):
                ok_db, msg_db = _check_databricks()
            if ok_db:
                st.success(msg_db)
            else:
                st.warning(msg_db)

    _section("Capa de datos Gold")
    if gold_ok:
        st.markdown(
            f'<div class="srv-card"><div class="srv-name">gold_user_360</div>'
            f'<span class="pill pill-ok">{len(df_360):,} usuarios</span></div>',
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            '<div class="srv-card"><div class="srv-name">gold_user_360</div>'
            '<span class="pill pill-warn">Sin datos</span></div>',
            unsafe_allow_html=True,
        )
        st.markdown(
            '<p style="font-family:var(--mono);font-size:.72rem;color:var(--t3);margin-top:.5rem;">'
            'Ejecuta primero: <code>python src/run_pipeline.py</code></p>',
            unsafe_allow_html=True,
        )
