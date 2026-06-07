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
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
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
/* ── Variables ──────────────────────────────────────────────────────────── */
:root {
  --ink:       #0f172a;
  --ink-soft:  #1e293b;
  --muted:     #64748b;
  --paper:     #f8fafc;
  --glass:     rgba(255,255,255,.6);
  --line:      #e2e8f0;
  --teal:      #0d9488;
  --green:     #059669;
  --amber:     #d97706;
  --coral:     #dc2626;
}

/* ── Keyframes ──────────────────────────────────────────────────────────── */
@keyframes fadeUp {
  from { opacity:0; transform:translateY(16px); }
  to   { opacity:1; transform:translateY(0); }
}
@keyframes pulse-dot {
  0%,100% { opacity:1;   transform:scale(1); }
  50%     { opacity:.4;  transform:scale(.72); }
}
@keyframes brand-glow {
  0%,100% { box-shadow:0 0 8px 2px  rgba(13,148,136,.4); }
  50%     { box-shadow:0 0 20px 7px rgba(13,148,136,.7),
                       0 0 36px 12px rgba(5,150,105,.2); }
}
@keyframes shimmer {
  0%   { background-position:0%   50%; }
  100% { background-position:300% 50%; }
}

/* ── Base ───────────────────────────────────────────────────────────────── */
.stApp {
  background: radial-gradient(ellipse at 0% 0%,#e0f2ef 0%,#f8fafc 40%,#f1f5f9 100%);
  color: var(--ink);
}

section[data-testid="stSidebar"] {
  background: linear-gradient(180deg,#0c1220 0%,#111827 100%);
  border-right: 1px solid rgba(13,148,136,.22);
}
section[data-testid="stSidebar"] * { color:#f1f5f9; }

.block-container { padding-top:1.6rem; max-width:1300px; }

/* ── Brand ──────────────────────────────────────────────────────────────── */
.brand-lockup {
  padding:1rem 0 1.2rem;
  border-bottom:1px solid rgba(255,255,255,.1);
  margin-bottom:1rem;
}
.brand-mark {
  width:40px; height:40px; border-radius:10px;
  background:linear-gradient(135deg,#0d9488 0%,#059669 55%,#d97706 100%);
  display:inline-flex; align-items:center; justify-content:center;
  font-weight:900; color:#fff; margin-right:.7rem;
  animation:brand-glow 3s ease-in-out infinite;
}
.brand-title  { font-size:1.05rem; font-weight:800; line-height:1.1; }
.brand-caption{ color:#94a3b8; font-size:.76rem; margin-top:.2rem; }

/* ── Status rows ────────────────────────────────────────────────────────── */
.status-row {
  display:flex; justify-content:space-between; align-items:center;
  gap:.75rem; padding:.6rem .75rem;
  border:1px solid rgba(255,255,255,.09); border-radius:8px;
  margin-bottom:.5rem; background:rgba(255,255,255,.04);
  transition:background .25s;
}
.status-row:hover { background:rgba(13,148,136,.08); }
.status-name { color:#cbd5e1; font-size:.75rem; }

.status-pill {
  font-size:.7rem; font-weight:800; border-radius:999px;
  padding:.17rem .52rem; display:flex; align-items:center; gap:.35rem;
}
.status-pill::before {
  content:''; width:6px; height:6px; border-radius:50%;
  display:inline-block; animation:pulse-dot 2s ease-in-out infinite;
}
.status-ok   { background:rgba(5,150,105,.17); color:#34d399; }
.status-ok::before   { background:#34d399; }
.status-warn { background:rgba(220,38,38,.16); color:#f87171; }
.status-warn::before { background:#f87171; }

/* ── Hero ───────────────────────────────────────────────────────────────── */
.hero-strip {
  position:relative; overflow:hidden; border-radius:14px;
  padding:2rem 2rem 1.8rem;
  background:linear-gradient(135deg,#0f172a 0%,#1e3a5f 55%,#0d4f4a 100%);
  margin-bottom:1.5rem; animation:fadeUp .55s ease both;
}
.hero-strip::before {
  content:''; position:absolute; top:0; left:0; right:0; height:3px;
  background:linear-gradient(90deg,#0d9488,#059669,#d97706,#0d9488);
  background-size:300% 100%; animation:shimmer 4s linear infinite;
}
.eyebrow {
  color:#5eead4; font-weight:800; font-size:.72rem;
  text-transform:uppercase; letter-spacing:.1em;
}
.hero-title {
  font-size:clamp(1.9rem,3.2vw,2.8rem); line-height:1.05; font-weight:900;
  margin:.3rem 0 .5rem;
  background:linear-gradient(90deg,#ffffff 0%,#a7f3d0 55%,#fde68a 100%);
  -webkit-background-clip:text; -webkit-text-fill-color:transparent;
  background-clip:text;
}
.hero-copy { color:#94a3b8; max-width:720px; font-size:1rem; }

.signal-row { display:flex; flex-wrap:wrap; gap:.5rem; margin-top:1rem; }
.signal-chip {
  border:1px solid rgba(255,255,255,.18); border-radius:999px;
  padding:.3rem .65rem; background:rgba(255,255,255,.08); color:#e2e8f0;
  font-size:.76rem; font-weight:700; backdrop-filter:blur(4px);
  transition:background .2s, border-color .2s;
}
.signal-chip:hover { background:rgba(13,148,136,.25); border-color:#0d9488; }

/* ── KPI cards ──────────────────────────────────────────────────────────── */
.kpi-card {
  background:var(--glass); border:1px solid rgba(255,255,255,.65);
  border-radius:12px; padding:1.1rem; min-height:120px;
  backdrop-filter:blur(14px);
  box-shadow:0 4px 20px rgba(15,23,42,.07),0 1px 4px rgba(15,23,42,.04);
  transition:transform .22s ease, box-shadow .22s ease;
  animation:fadeUp .5s ease both;
}
.kpi-card:hover {
  transform:translateY(-5px);
  box-shadow:0 14px 36px rgba(15,23,42,.13),0 2px 8px rgba(15,23,42,.06);
}
.kpi-card[data-accent="teal"]  { border-top:4px solid var(--teal);  box-shadow:0 4px 20px rgba(13,148,136,.13); }
.kpi-card[data-accent="green"] { border-top:4px solid var(--green); box-shadow:0 4px 20px rgba(5,150,105,.13); }
.kpi-card[data-accent="amber"] { border-top:4px solid var(--amber); box-shadow:0 4px 20px rgba(217,119,6,.13); }
.kpi-card[data-accent="coral"] { border-top:4px solid var(--coral); box-shadow:0 4px 20px rgba(220,38,38,.13); }

.kpi-label { color:var(--muted); font-size:.73rem; text-transform:uppercase; font-weight:800; letter-spacing:.07em; }
.kpi-value { color:var(--ink);   font-size:1.85rem; font-weight:900; margin-top:.35rem; }
.kpi-detail{ color:var(--muted); font-size:.76rem; margin-top:.2rem; }

/* ── Section headings ───────────────────────────────────────────────────── */
.section-heading {
  margin:1.4rem 0 .6rem; color:var(--ink); font-weight:800; font-size:1.05rem;
  display:flex; align-items:center; gap:.5rem;
}
.section-heading::before {
  content:''; display:inline-block; width:3px; height:1em; border-radius:2px;
  background:linear-gradient(180deg,var(--teal),var(--green)); flex-shrink:0;
}

/* ── Buttons ────────────────────────────────────────────────────────────── */
.stButton > button {
  border-radius:9px; border:1px solid #cbd5e1;
  background:rgba(255,255,255,.8); color:#1e293b;
  min-height:2.6rem; font-weight:700;
  backdrop-filter:blur(8px); transition:all .2s ease;
}
.stButton > button:hover {
  border-color:var(--teal); color:var(--teal);
  background:rgba(13,148,136,.06);
  box-shadow:0 0 0 3px rgba(13,148,136,.12);
  transform:translateY(-1px);
}
.stButton > button:active { transform:translateY(0); }

/* ── Chat ───────────────────────────────────────────────────────────────── */
.stChatMessage {
  border-radius:10px; border:1px solid var(--line);
  background:rgba(255,255,255,.78); backdrop-filter:blur(8px);
  color:var(--ink); animation:fadeUp .3s ease both;
  transition:box-shadow .2s;
}
.stChatMessage:hover { box-shadow:0 4px 16px rgba(15,23,42,.08); }

div[data-testid="stChatMessageContent"] p,
div[data-testid="stChatMessageContent"] span,
div[data-testid="stChatMessageContent"] li {
  color:var(--ink) !important;
}

/* ── Misc ───────────────────────────────────────────────────────────────── */
div[data-testid="stMetricValue"] { color:var(--ink); }
.question-grid-note { color:var(--muted); margin-bottom:.6rem; }
hr { border-color:var(--line); }

::-webkit-scrollbar         { width:6px; height:6px; }
::-webkit-scrollbar-track   { background:transparent; }
::-webkit-scrollbar-thumb   { background:rgba(13,148,136,.3); border-radius:3px; }
::-webkit-scrollbar-thumb:hover { background:rgba(13,148,136,.55); }

/* ── Code blocks: todos los spans visibles (headers, data, labels) ──────── */
[data-testid="stCode"] *,
[data-testid="stCode"] span,
[data-testid="stCodeBlock"] *,
[data-testid="stCodeBlock"] span,
.stMarkdown pre,
.stMarkdown pre *,
.stMarkdown code,
.stMarkdown code * { color:#e2e8f0 !important; }

/* ── Chat: cubrir strong/b/em/div que Ollama genera con markdown ────────── */
div[data-testid="stChatMessageContent"] *         { color:var(--ink) !important; }
div[data-testid="stChatMessageContent"] strong,
div[data-testid="stChatMessageContent"] b,
div[data-testid="stChatMessageContent"] em        { color:var(--ink-soft) !important; }
/* Revertir: dentro del chat, los code blocks deben seguir siendo claros */
div[data-testid="stChatMessageContent"] pre *,
div[data-testid="stChatMessageContent"] code *,
div[data-testid="stChatMessageContent"] [data-testid="stCode"] *,
div[data-testid="stChatMessageContent"] [data-testid="stCodeBlock"] * {
  color:#e2e8f0 !important;
}

/* ── Sidebar: labels y widgets sobre fondo oscuro ───────────────────────── */
section[data-testid="stSidebar"] label,
section[data-testid="stSidebar"] [data-testid="stWidgetLabel"],
section[data-testid="stSidebar"] [data-testid="stWidgetLabel"] p,
section[data-testid="stSidebar"] span[data-baseweb="label"],
section[data-testid="stSidebar"] div[data-baseweb="label"],
section[data-testid="stSidebar"] .stRadio label,
section[data-testid="stSidebar"] .stRadio span,
section[data-testid="stSidebar"] p,
section[data-testid="stSidebar"] [data-testid="stMarkdownContainer"] p { color:#f1f5f9 !important; }

/* ── Expander (filtros): labels sobre fondo claro ───────────────────────── */
[data-testid="stExpander"] label,
[data-testid="stExpander"] [data-testid="stWidgetLabel"],
[data-testid="stExpander"] [data-testid="stWidgetLabel"] p,
[data-testid="stExpander"] p { color:var(--ink) !important; }

/* ── Multiselect: pills/tags legibles en ambos contextos ────────────────── */
[data-baseweb="tag"] span                                        { color:#1e293b !important; }
section[data-testid="stSidebar"] [data-baseweb="tag"] span       { color:#f1f5f9 !important; }

/* ── Selectbox dropdown options ─────────────────────────────────────────── */
[data-testid="stSelectbox"] label,
[data-testid="stMultiSelect"] label { color:var(--ink) !important; }
</style>
""", unsafe_allow_html=True)

ROOT            = Path(__file__).resolve().parents[2]
COLOR_PRINCIPAL = "#0E7C7B"
COLOR_VERDE     = "#2F9E6D"
COLOR_AMBER     = "#C7922B"
COLOR_CORAL     = "#D85C4A"
COLOR_INK       = "#18212F"
COLOR_MUTED     = "#667085"
OLLAMA_URL      = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL    = os.getenv("OLLAMA_MODEL", "llama3.2")


import re as _re

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
    fig.patch.set_facecolor("#FFFFFF")
    ax.set_facecolor("#FFFFFF")
    ax.spines[["top","right"]].set_visible(False)
    ax.spines["left"].set_color("#D9DED8")
    ax.spines["bottom"].set_color("#D9DED8")
    ax.tick_params(colors=COLOR_MUTED, labelsize=9)
    ax.title.set_color(COLOR_INK)
    ax.xaxis.label.set_color(COLOR_MUTED)
    ax.yaxis.label.set_color(COLOR_MUTED)
    ax.grid(axis="y", color="#E8ECE7", linewidth=0.8)
    ax.set_axisbelow(True)
    return fig, ax

def _show(fig):
    plt.tight_layout()
    st.pyplot(fig)
    plt.close(fig)


def _palette(n: int) -> list[str]:
    base = [COLOR_PRINCIPAL, COLOR_VERDE, COLOR_AMBER, COLOR_CORAL, "#536878"]
    return [base[i % len(base)] for i in range(n)]


def _status_badge(ok: bool) -> str:
    cls = "status-ok" if ok else "status-warn"
    label = "Activo" if ok else "Revisar"
    return f'<span class="status-pill {cls}">{label}</span>'


def _render_status_row(nombre: str, detalle: str, ok: bool) -> None:
    st.markdown(
        f"""
        <div class="status-row">
          <div>
            <div class="status-name">{nombre}</div>
            <div style="font-size:.78rem;color:#f7f8f4;margin-top:.15rem;">{detalle}</div>
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
    chips_html = "".join(f'<span class="signal-chip">{chip}</span>' for chip in chips)
    st.markdown(
        f"""
        <section class="hero-strip">
          <div class="eyebrow">Fintech 360 Decision Console</div>
          <div class="hero-title">{titulo}</div>
          <p class="hero-copy">{subtitulo}</p>
          <div class="signal-row">{chips_html}</div>
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

    st.caption("SENAL OPERATIVA")
    ollama_ok, ollama_msg = _check_ollama()
    db_ok, db_msg = _check_databricks()
    _render_status_row("Motor conversacional", ollama_msg, ollama_ok)
    _render_status_row("Warehouse analitico", db_msg, db_ok)

    st.divider()
    pagina = st.radio(
        "Navegacion",
        ["Centro de mando", "Mesa de analisis", "Sistema"],
    )
    st.divider()
    if st.button("Recargar datos Gold"):
        st.cache_data.clear()
        st.rerun()


# ══════════════════════════════════════════════════════════════════════════════
# PÁGINA: DASHBOARD
# ══════════════════════════════════════════════════════════════════════════════
if pagina == "Centro de mando":
    df_360, df_daily, df_events = cargar_datos()

    _hero(
        "Pulso financiero de usuarios",
        "Vista ejecutiva sobre la capa Gold: comportamiento, volumen, fallas y preferencias consolidadas por usuario.",
        ["Gold activo", "Vision 360", "S3 + Databricks", "Near real-time"],
    )

    if df_360 is None:
        st.error(
            "No se encontraron datos Gold. "
            "Ejecuta primero: `python src/run_pipeline.py`"
        )
        st.stop()

    # ── Filtros interactivos ──────────────────────────────────────────────
    with st.expander("🔍 Filtros de análisis", expanded=False):
        col_f1, col_f2 = st.columns(2)
        with col_f1:
            segmentos_disp = sorted(df_360["user_segment"].dropna().unique().tolist())
            segmentos_sel = st.multiselect(
                "Segmento de usuario",
                options=segmentos_disp,
                default=segmentos_disp,
                key="filter_segmento",
            )
        with col_f2:
            ciudades_disp = sorted(df_360["city"].dropna().unique().tolist())
            ciudades_sel = st.multiselect(
                "Ciudad",
                options=ciudades_disp,
                default=ciudades_disp,
                key="filter_ciudad",
            )

    if segmentos_sel:
        df_360 = df_360[df_360["user_segment"].isin(segmentos_sel)]
    if ciudades_sel:
        df_360 = df_360[df_360["city"].isin(ciudades_sel)]

    if df_360.empty:
        st.warning("Los filtros seleccionados no devuelven datos. Ajusta los criterios.")
        st.stop()

    k1, k2, k3, k4 = st.columns(4)
    with k1:
        _metric_card("Usuarios", f"{len(df_360):,}", "Perfiles consolidados en Gold", "teal")
    with k2:
        vol = df_360["total_amount_cop"].sum() / 1_000_000
        _metric_card("Volumen COP", f"${vol:,.1f}M", "Suma transaccional exitosa", "green")
    with k3:
        ticket = df_360["avg_ticket"].mean()
        _metric_card("Ticket promedio", f"${ticket:,.0f}", "Promedio por usuario activo", "amber")
    with k4:
        fallo = df_360["failure_rate"].mean() * 100
        _metric_card("Tasa de fallo", f"{fallo:.1f}%", "Promedio de friccion transaccional", "coral")

    _section("Mapa de desempeno")
    col1, col2 = st.columns(2)

    with col1:
        st.subheader("Volumen por segmento")
        datos = df_360.groupby("user_segment")["total_amount_cop"].sum().sort_values()
        fig, ax = _fig((7.2, 4.2))
        ax.barh(datos.index, datos.values / 1e6, color=_palette(len(datos)))
        ax.set_xlabel("Millones COP")
        _show(fig)

    with col2:
        st.subheader("Usuarios por ciudad")
        datos = df_360.groupby("city")["user_id"].count().sort_values(ascending=False)
        fig, ax = _fig((7.2, 4.2))
        ax.bar(datos.index, datos.values, color=_palette(len(datos)))
        ax.set_ylabel("Usuarios")
        ax.tick_params(axis="x", rotation=30)
        _show(fig)

    _section("Preferencias y alianzas")
    col3, col4 = st.columns(2)
    with col3:
        st.subheader("Comercios dominantes")
        datos = df_360["top_merchant"].value_counts().head(10).sort_values()
        datos = datos[datos.index.notna() & (datos.index != "None")]
        fig, ax = _fig((7.2, 4.2))
        ax.barh(datos.index.astype(str), datos.values, color=COLOR_PRINCIPAL)
        ax.set_xlabel("Usuarios")
        _show(fig)

    with col4:
        st.subheader("Canal preferido")
        datos = df_360["preferred_channel"].value_counts()
        fig, ax = _fig((7.2, 4.2))
        ax.grid(False)
        colors = _palette(len(datos))
        ax.pie(datos.values, labels=datos.index, autopct="%1.1f%%",
               colors=colors, startangle=90)
        _show(fig)

    if df_daily is not None:
        _section("Ritmo operativo")
        st.subheader("Tendencia diaria de transacciones")
        if "date" in df_daily.columns and "total_transactions" in df_daily.columns:
            fig, ax = _fig((12, 4))
            x_idx = range(len(df_daily))
            y_vals = df_daily["total_transactions"].astype(float)
            ax.plot(x_idx, y_vals, color=COLOR_PRINCIPAL, linewidth=2.5, marker="o", markersize=5)
            ax.fill_between(
                x_idx,
                y_vals,
                color=COLOR_VERDE,
                alpha=0.14,
            )
            ax.set_xticks(list(x_idx))
            ax.set_xticklabels(df_daily["date"].astype(str))
            ax.tick_params(axis="x", rotation=45)
            ax.set_xlabel("Fecha")
            ax.set_ylabel("Transacciones")
            _show(fig)


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
    _section("Acciones rapidas")
    st.markdown(
        '<div class="question-grid-note">Elige una pregunta de negocio o escribe una propia.</div>',
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
        "Estado operativo",
        "Configuracion y salud de los servicios que sostienen la consola: datos Gold, modelo local y warehouse externo.",
        ["Servicios", "Credenciales", "Catalogo", "Gold"],
    )

    col1, col2 = st.columns(2)

    with col1:
        st.subheader("Motor conversacional local")
        st.code(f"URL: {OLLAMA_URL}\nModelo: {OLLAMA_MODEL}", language="bash")
        ok, msg = _check_ollama()
        st.markdown(f"**Estado:** {msg}")
        st.markdown("**Comandos:**")
        st.code(
            f"# Iniciar servidor\nollama serve\n\n"
            f"# Descargar modelo\nollama pull {OLLAMA_MODEL}\n\n"
            f"# Verificar modelos disponibles\nollama list",
            language="bash"
        )

    with col2:
        st.subheader("Databricks")
        host = os.getenv("DATABRICKS_HOST", "(no configurado)")
        catalog = os.getenv("DATABRICKS_CATALOG", "fintech_pipeline")
        schema = os.getenv("DATABRICKS_SCHEMA", "fintech")
        st.code(f"HOST: {host}\nCATALOG: {catalog}\nSCHEMA: {schema}", language="bash")
        ok_db, msg_db = _check_databricks()
        st.markdown(f"**Estado:** {msg_db}")
        if st.button("Probar conexion Databricks"):
            with st.spinner("Conectando..."):
                ok_db, msg_db = _check_databricks()
            if ok_db:
                st.success(msg_db)
            else:
                st.warning(msg_db)

    st.divider()
    st.subheader("Estado de datos Gold")
    df_360, _, _ = cargar_datos()
    if df_360 is not None:
        st.success(f"gold_user_360: {len(df_360):,} usuarios")
    else:
        st.error("No hay datos Gold. Ejecuta: `python src/run_pipeline.py`")
