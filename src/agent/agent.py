"""
agent.py — Agente Fintech con Ollama (llama3.2) via HTTP local.

Flujo definido en el diagrama:
  Pregunta en lenguaje natural
    → Ollama (localhost:11434)
    → genera SQL
    → ejecuta en Databricks (si credenciales presentes) o DuckDB (local)
    → interpreta resultado
    → respuesta en lenguaje natural al usuario de negocio

Dependencias críticas:
  - Ollama corriendo en localhost:11434  (REQUERIDO)
  - Modelo llama3.2 descargado           (ollama pull llama3.2)
  - .env con DATABRICKS_* para consultas en catálogo Unity
  - Parquets Gold en data/gold/ para fallback DuckDB local

Sin Ollama el agente NO funciona. Verificar antes de iniciar:
  curl http://localhost:11434/api/tags
"""

import os
import json
import re
import time
import datetime
import requests
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from pathlib import Path
from dotenv import load_dotenv

from strands import Agent, tool

import sys
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from src.agent.schema import SYSTEM_PROMPT
from src.agent.security import procesar_sql, SQLSecurityError
from src.agent.intent_router import (
    is_data_question        as _es_pregunta_de_datos,
    normalize_text          as _normalizar_texto,
    sql_for_intent          as _sql_por_intencion,
    extraer_intencion_grafico,
    construir_sql_grafico,
    METRICAS_GOLD,
    DIMENSIONES_GOLD,
)
from src.io.parquet_io import resolve_latest_parquet

load_dotenv()

# ── Configuración Ollama ─────────────────────────────────────────────────────
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL    = os.getenv("OLLAMA_MODEL", "llama3.2")

# ── Paleta corporativa ───────────────────────────────────────────────────────
_CORP_BLUE  = "#1B4F72"
_CORP_GREEN = "#2ECC71"
_PALETTE    = [_CORP_BLUE, _CORP_GREEN, "#2980B9", "#27AE60",
               "#F39C12", "#8E44AD", "#E74C3C", "#16A085"]

# ── Directorio de gráficos ───────────────────────────────────────────────────
def _get_charts_dir() -> Path:
    d = Path(__file__).resolve().parents[2] / "outputs" / "charts"
    d.mkdir(parents=True, exist_ok=True)
    return d

def _save_chart(titulo: str) -> str:
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    nombre = titulo.lower().replace(" ", "_").replace("/", "-")[:40]
    ruta = _get_charts_dir() / f"{nombre}_{ts}.png"
    plt.tight_layout()
    plt.savefig(ruta, dpi=150, bbox_inches="tight")
    plt.close()
    _subir_a_docker(str(ruta))
    return str(ruta)


def _subir_a_docker(ruta_local: str) -> None:
    """
    Copia el archivo generado al contenedor Docker activo (fintech-dashboard o
    fintech-dashboard-dev). Es un respaldo explícito del volumen compartido ./outputs.
    Falla silenciosamente si Docker no está disponible.
    """
    import subprocess
    ruta_path = Path(ruta_local)
    rel_sub = ruta_path.parent.name          # "charts" o "reports"
    ruta_destino = f"/app/outputs/{rel_sub}/{ruta_path.name}"
    for contenedor in ("fintech-dashboard", "fintech-dashboard-dev"):
        try:
            res = subprocess.run(
                ["docker", "cp", ruta_local, f"{contenedor}:{ruta_destino}"],
                capture_output=True, text=True, timeout=8,
            )
            if res.returncode == 0:
                print(f"  ✅ Docker sync: {ruta_path.name} → {contenedor}:{ruta_destino}")
                return
        except Exception:
            continue


def _chart_to_base64() -> str:
    """Captura el gráfico activo de matplotlib como PNG base64 (para HTML embebido)."""
    import io
    import base64
    buf = io.BytesIO()
    plt.tight_layout()
    plt.savefig(buf, format="png", dpi=150, bbox_inches="tight")
    plt.close()
    buf.seek(0)
    return base64.b64encode(buf.read()).decode("utf-8")


# ── Historial de conversación (últimos 4 turnos = 8 mensajes) ────────────────
_conversation_history: list[dict] = []
_MAX_HISTORY = 8


def _agregar_historial(rol: str, texto: str) -> None:
    global _conversation_history
    _conversation_history.append({"role": rol, "content": texto[:2000]})
    if len(_conversation_history) > _MAX_HISTORY:
        _conversation_history = _conversation_history[-_MAX_HISTORY:]


_DATA_ANCHORS = {
    "kpi", "kpis", "resumen", "segmento", "segmentos", "ciudad", "ciudades",
    "fallo", "fallos", "error", "errores", "reporte", "informe", "alerta",
    "alertas", "tendencia", "usuario", "usuarios", "ticket", "revenue",
    "canal", "canales", "merchant", "categoria", "categorias", "dispositivo",
    "dispositivos", "evento", "eventos", "balance", "saldo", "periodo",
    "diagnostico", "inactivo", "inactivos", "churn", "comercio", "comercios",
    "total", "volumen", "monto", "transaccion", "transacciones", "ejecutivo",
}


def _es_pregunta_de_seguimiento(pregunta: str) -> bool:
    """Detecta preguntas anafóricas que referencian el contexto anterior."""
    if not _conversation_history:
        return False
    p = _normalizar_texto(pregunta)
    # Palabras clave de datos nunca son seguimientos, aunque sean cortas
    if any(anchor in p for anchor in _DATA_ANCHORS):
        return False
    if len(p.split()) <= 3:
        return True
    conectores = (
        "y en ", "y para ", "y los ", "y las ", "y ese ", "y esa ",
        "y esos ", "y esas ", "y que tal ", "y cuanto ", "ahora en ",
        "ahora para ", "tambien en ", "y el ", "y la ", "y como ",
        "que pasa con ", "y que hay ", "y si ", "y en el ", "y en la ",
    )
    return any(p.startswith(c) for c in conectores)


def _resolver_seguimiento_con_ollama(pregunta: str) -> str:
    """Responde preguntas de seguimiento usando el historial completo de la conversación."""
    if not _verificar_ollama():
        return (
            "Ollama no está disponible para responder la pregunta de seguimiento.\n"
            "Por favor, reformula la pregunta con más contexto."
        )
    mensajes = [{"role": "system", "content": _SYSTEM_INTERPRETACION}]
    for m in _conversation_history[-6:]:
        mensajes.append({"role": m["role"], "content": m["content"][:800]})
    mensajes.append({"role": "user", "content": pregunta})
    try:
        resp = requests.post(
            f"{OLLAMA_BASE_URL}/api/chat",
            json={
                "model": OLLAMA_MODEL,
                "messages": mensajes,
                "stream": False,
                "options": {"num_ctx": 4096},
            },
            timeout=120,
        )
        resp.raise_for_status()
        return resp.json().get("message", {}).get("content", "").strip() or "No pude generar respuesta."
    except Exception as e:
        return f"Error al procesar seguimiento: {e}"


# ── Conexión DuckDB local (fallback cuando Databricks no está disponible) ────
_conn_duckdb = None
_tablas_cargadas: dict = {}

def _get_conn_duckdb():
    global _conn_duckdb, _tablas_cargadas
    if _conn_duckdb is not None:
        return _conn_duckdb
    import duckdb
    ROOT = Path(__file__).resolve().parents[2]
    _conn_duckdb = duckdb.connect()
    tablas = {
        "gold_user_360":      resolve_latest_parquet(ROOT / "data/gold/gold_user_360.parquet"),
        "gold_daily_metrics": resolve_latest_parquet(ROOT / "data/gold/gold_daily_metrics.parquet"),
        "gold_event_summary": resolve_latest_parquet(ROOT / "data/gold/gold_event_summary.parquet"),
    }
    for nombre, ruta in tablas.items():
        if ruta.exists():
            df = pd.read_parquet(ruta)
            _conn_duckdb.register(nombre, df)
            _tablas_cargadas[nombre] = True
            print(f"  ✅ DuckDB: tabla '{nombre}' — {len(df):,} filas")
        else:
            _tablas_cargadas[nombre] = False
            print(f"  ⚠️  DuckDB: '{nombre}' no encontrada en {ruta}")
    return _conn_duckdb


def _verificar_ollama() -> bool:
    """Verifica que Ollama esté corriendo y tenga el modelo disponible."""
    try:
        r = requests.get(f"{OLLAMA_BASE_URL}/api/tags", timeout=5)
        if r.status_code != 200:
            return False
        modelos = [m["name"].split(":")[0] for m in r.json().get("models", [])]
        return OLLAMA_MODEL.split(":")[0] in modelos
    except Exception:
        return False


def _ejecutar_sql_duckdb_texto(sql: str, max_rows: int = 100) -> str:
    sql_seguro, advertencias_pii = procesar_sql(sql, max_rows=max_rows)
    conn = _get_conn_duckdb()
    df = conn.execute(sql_seguro).fetchdf()
    cols_pii = {"user_name", "user_email", "user_age"}
    df = df.drop(columns=[c for c in df.columns if c.lower() in cols_pii], errors="ignore")
    if df.empty:
        return "La consulta no retorno resultados."
    resultado = df.to_string(index=False)
    if advertencias_pii:
        resultado += f"\n\nAdvertencia PII: {advertencias_pii}"
    return resultado


_SYSTEM_INTERPRETACION = """Eres un analista senior de negocio en una fintech colombiana con 15 años de experiencia. Recibes datos reales de la capa Gold y DEBES producir un análisis ejecutivo profundo y elaborado — nunca superficial.

OBLIGATORIO en cada respuesta:
• Nombra el valor MÁXIMO y el MÍNIMO de cada métrica con sus cifras exactas.
• Calcula brechas entre extremos en términos relativos (ej: "el segmento Premium genera 3.2x más revenue que el segmento Basic").
• Contextualiza contra benchmarks del sector fintech colombiano: tasa de fallo aceptable <3%, ticket saludable >COP 150K, retención >70% a 30 días.
• Identifica al menos UN patrón no obvio o correlación entre métricas que no sea inmediatamente evidente en los números.
• Si hay concentración en pocos segmentos o ciudades, cuantifica el riesgo de concentración explícitamente.
• Da DOS recomendaciones accionables y específicas, cada una justificada con cifras concretas del análisis.
• NUNCA uses frases como "no hay suficientes datos" o "se necesita más información" — con los datos entregados SIEMPRE se puede concluir algo valioso.
• NUNCA inventes cifras que no estén en la tabla.
• Español, tono ejecutivo, mínimo 350 palabras.

FORMATO OBLIGATORIO (sin excepción, siempre usa estos 4 bloques):

**Resumen Ejecutivo**
Hallazgo principal con el número más impactante y su significado estratégico para el negocio en 2-3 oraciones.

**Análisis Comparativo**
Mínimo 4 comparaciones concretas con cifras exactas, brechas porcentuales e implicaciones operativas. Identifica el mejor y el peor performer en cada dimensión relevante.

**Insights Clave**
2 observaciones que van más allá de lo obvio: correlaciones entre métricas, comportamientos inesperados, riesgos ocultos o tendencias que el ejecutivo debería atender de inmediato.

**Recomendaciones Estratégicas**
2 acciones priorizadas por impacto potencial. Cada recomendación debe especificar: segmento o ciudad objetivo, métrica que se espera mejorar y la justificación numérica directa."""


def _interpretar_con_ollama(pregunta: str, datos_texto: str) -> str:
    """
    Pasa datos reales de Gold a Ollama para interpretación en lenguaje natural.
    Si Ollama no está disponible, devuelve los datos en formato tabla como fallback.
    """
    fallback = (
        f"**Datos Gold**\n```text\n{datos_texto}\n```\n\n"
        "_Ollama no disponible para interpretación. Estos son los datos reales de la capa Gold._"
    )
    if not _verificar_ollama():
        return fallback

    prompt_usuario = (
        f"Pregunta: {pregunta}\n\n"
        f"Datos reales de la capa Gold:\n{datos_texto}\n\n"
        "Analiza TODOS los valores de la tabla con profundidad ejecutiva. "
        "Identifica el mejor y el peor performer en cada métrica con sus cifras exactas. "
        "Calcula brechas porcentuales entre extremos. "
        "Busca correlaciones no obvias entre las métricas disponibles. "
        "Si hay concentración de riesgo en pocos segmentos o ciudades, señálala. "
        "Responde con el formato de 4 bloques del sistema: Resumen Ejecutivo, Análisis Comparativo, Insights Clave y Recomendaciones Estratégicas. "
        "No añadas ningún número que no esté en los datos anteriores."
    )
    try:
        mensajes = [{"role": "system", "content": _SYSTEM_INTERPRETACION}]
        # Inyectar últimos 2 turnos del historial para que Ollama tenga contexto
        for m in _conversation_history[-4:]:
            mensajes.append({"role": m["role"], "content": m["content"][:600]})
        mensajes.append({"role": "user", "content": prompt_usuario})
        resp = requests.post(
            f"{OLLAMA_BASE_URL}/api/chat",
            json={
                "model": OLLAMA_MODEL,
                "messages": mensajes,
                "stream": False,
                "options": {"num_ctx": 4096},
            },
            timeout=120,
        )
        resp.raise_for_status()
        interpretacion = resp.json().get("message", {}).get("content", "").strip()
        if interpretacion:
            return (
                f"**Datos Gold**\n```text\n{datos_texto}\n```\n\n"
                f"**Análisis** _(Ollama · {OLLAMA_MODEL})_\n\n{interpretacion}"
            )
    except Exception as e:
        print(f"  [Ollama] Error en interpretación: {e}")
    return fallback


def _respuesta_desde_sql(titulo: str, sql: str, pregunta: str = "") -> str:
    datos = _ejecutar_sql_duckdb_texto(sql)
    return _interpretar_con_ollama(pregunta or titulo, datos)


def _respuesta_con_grafico(titulo: str, sql: str, pregunta: str) -> str:
    """
    Ejecuta SQL certificado sobre la capa Gold y entrega siempre 3 componentes:
      1. Imagen del gráfico (tipo adaptado al pedido del usuario)
      2. Tabla de datos Gold certificados
      3. Análisis Ollama en 3 partes (por dato · distribución · conclusión)

    Nunca inventa datos ni genera gráficos genéricos — solo datos reales de Gold.
    """
    try:
        sql_seguro, _ = procesar_sql(sql, max_rows=50)
        conn = _get_conn_duckdb()
        df = conn.execute(sql_seguro).fetchdf()
        cols_pii = {"user_name", "user_email", "user_age"}
        df = df.drop(columns=[c for c in df.columns if c.lower() in cols_pii], errors="ignore")
    except Exception as e:
        return _interpretar_con_ollama(pregunta, f"Error al consultar datos Gold: {e}")

    if df.empty:
        return _interpretar_con_ollama(pregunta, "La consulta no retornó resultados en la capa Gold.")

    # Prioridad 1: tipo explícito solicitado por el usuario (torta / línea / barras)
    p = _normalizar_texto(pregunta)
    tipo = _detectar_tipo_grafico(p)

    # Prioridad 2: inferir por estructura del DataFrame
    if tipo is None:
        cols_lower = [c.lower() for c in df.columns]
        if any(c in ("date", "fecha") for c in cols_lower):
            tipo = "line"
        elif len(df) <= 6 and any("pct" in c or "share" in c for c in cols_lower):
            tipo = "pie"
        else:
            tipo = "bar"

    return _ejecutar_grafico_con_analisis(df, titulo, tipo, pregunta)


def _respuesta_datos_confiable(pregunta: str) -> str | None:
    if not _es_pregunta_de_datos(pregunta):
        return None

    p = _normalizar_texto(pregunta)

    # ── Reporte ejecutivo HTML ────────────────────────────────────────────────
    if any(k in p for k in ("reporte", "informe", "report", "exportar", "generar reporte",
                             "generar informe", "descarga", "html", "pdf")):
        return generar_reporte_html()

    # ── Diagnóstico / Alertas automáticas ─────────────────────────────────────
    if any(k in p for k in ("alerta", "alertas", "diagnostico", "diagnóstico",
                             "anomalia", "anomalias", "estado del negocio",
                             "como esta el negocio", "como va el negocio",
                             "revision general", "salud del negocio")):
        datos = detectar_alertas()
        return _interpretar_con_ollama(pregunta, datos)

    # ── Comparación de períodos ────────────────────────────────────────────────
    if any(k in p for k in ("comparar periodo", "periodo anterior", "semana pasada",
                             "semana anterior", "mes pasado", "mes anterior",
                             "vs semana", "variacion", "variación", "cambio reciente",
                             "como fue", "evolucion reciente", "ultimos dias vs")):
        # Detectar número de días si viene en la pregunta (ej: "últimos 14 días")
        match = re.search(r"(\d+)\s*d[ií]as?", p)
        dias = int(match.group(1)) if match and 1 <= int(match.group(1)) <= 30 else 7
        datos = comparar_periodos(dias)
        return _interpretar_con_ollama(pregunta, datos)

    # ── Resumen ejecutivo / KPIs ───────────────────────────────────────────────
    if any(k in p for k in ("resumen", "ejecutivo", "kpi", "indicador", "indicadores")):
        datos = resumen_ejecutivo()
        return _interpretar_con_ollama(pregunta, datos)

    # ── Campañas / Estrategia ─────────────────────────────────────────────────
    if any(k in p for k in ("campana", "campaña", "lanzar", "lanzaria", "lanzaría",
                             "promocion", "promoción", "estrategia", "iniciativa")):
        _sql_campanias = """
            SELECT
                user_segment,
                COUNT(*)                                              AS usuarios,
                ROUND(SUM(total_amount_cop)/COUNT(*), 0)             AS revenue_por_usuario,
                ROUND(AVG(avg_ticket), 0)                            AS ticket_promedio,
                ROUND(AVG(failure_rate)*100, 1)                      AS tasa_fallo_pct,
                COUNT(CASE WHEN days_since_last_tx > 30 THEN 1 END)  AS inactivos
            FROM gold_user_360
            GROUP BY user_segment
            ORDER BY revenue_por_usuario DESC
        """
        return _respuesta_con_grafico(
            "Análisis para Estrategia de Campañas por Segmento", _sql_campanias, pregunta
        )

    intent = _sql_por_intencion(pregunta)
    if intent:
        sql, titulo = intent
        return _respuesta_con_grafico(titulo, sql, pregunta)

    return (
        "**Resumen**\nNecesito una pregunta más específica para consultar los datos.\n\n"
        "**Puedo analizar:** segmentos, ciudades, fallos, top usuarios, comercios, "
        "categorías, canales, dispositivos, eventos o métricas diarias.\n\n"
        "**Ejemplo:** `muéstrame el volumen por segmento` o "
        "`¿cuáles son las ciudades con mayor tasa de fallo?`"
    )


def _extraer_tool_call(text: str) -> dict | None:
    """Extrae un JSON de tool-call aunque tenga args anidados."""
    raw = (text or "").strip()
    raw = re.sub(r"^```(?:json)?", "", raw, flags=re.IGNORECASE).strip()
    raw = re.sub(r"```$", "", raw).strip()

    decoder = json.JSONDecoder()
    for idx, char in enumerate(raw):
        if char != "{":
            continue
        try:
            obj, _ = decoder.raw_decode(raw[idx:])
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and "tool" in obj:
            return obj
    return None


# ══════════════════════════════════════════════════════════════════════════════
# HERRAMIENTAS DEL AGENTE (@tool)
# ══════════════════════════════════════════════════════════════════════════════

@tool
def listar_tablas() -> str:
    """Lista las tablas disponibles para análisis."""
    return (
        "🔒 La estructura interna del sistema es confidencial. "
        "Puedo ayudarte con análisis, métricas agregadas, insights "
        "de negocio y recomendaciones. ¿Qué análisis necesitas?"
    )


@tool
def consultar_sql(query: str) -> str:
    """
    Ejecuta una consulta SQL sobre los datos Gold y retorna los resultados.
    Soporta tanto Databricks (catálogo fintech) como DuckDB local.
    Solo permite SELECT. Bloquea columnas PII y operaciones de escritura.
    """
    try:
        sql_seguro, advertencias_pii = procesar_sql(query, max_rows=100)
    except SQLSecurityError as e:
        return f"⛔ Consulta bloqueada por seguridad: {e}"

    # Intentar Databricks primero (según flujo del diagrama)
    try:
        from src.config.databricks_config import ejecutar_query, _validar_credenciales
        ok, _ = _validar_credenciales()
        if ok:
            filas = ejecutar_query(sql_seguro, max_filas=100)
            if not filas:
                return "La consulta no retornó resultados en Databricks."
            df = pd.DataFrame(filas)
            resultado = df.to_string(index=False)
            if advertencias_pii:
                resultado += f"\n\n⚠️ Advertencia PII: {advertencias_pii}"
            return resultado
    except Exception as e_db:
        print(f"  [SQL] Databricks no disponible ({e_db}), usando DuckDB local")

    # Fallback: DuckDB local
    conn = _get_conn_duckdb()
    try:
        df = conn.execute(sql_seguro).fetchdf()
        cols_pii = {"user_name", "user_email", "user_age"}
        df = df.drop(columns=[c for c in df.columns if c.lower() in cols_pii], errors="ignore")
        resultado = df.to_string(index=False)
        if advertencias_pii:
            resultado += f"\n\n⚠️ Advertencia PII: {advertencias_pii}"
        return resultado
    except Exception as e:
        return f"❌ Error SQL: {e}"


@tool
def consultar_databricks(sql: str) -> str:
    """
    Ejecuta una consulta SQL directamente en el catálogo Databricks fintech.
    Usa esta herramienta cuando el usuario pida análisis sobre la capa Gold
    almacenada en Databricks Unity Catalog.
    Solo ejecuta SELECT — nunca INSERT, UPDATE, DELETE ni DROP.

    Args:
        sql: Consulta SQL SELECT sobre tablas del catálogo fintech_pipeline.fintech
    Returns:
        Resultado en formato texto tabular
    """
    ts_inicio = time.time()
    print(f"\n🔷 [Databricks] SQL: {sql[:80]}...")

    try:
        sql_seguro, _ = procesar_sql(sql, max_rows=100)
    except SQLSecurityError as e:
        return f"⛔ SQL bloqueado: {e}"

    try:
        from src.config.databricks_config import ejecutar_query, _validar_credenciales
        ok, msg = _validar_credenciales()
        if not ok:
            return (
                f"⚠️ Databricks no configurado: {msg}\n"
                f"Configura DATABRICKS_HOST, DATABRICKS_TOKEN y DATABRICKS_HTTP_PATH en .env"
            )
        filas = ejecutar_query(sql_seguro, max_filas=100)
        if not filas:
            return "La consulta no retornó resultados."
        df = pd.DataFrame(filas)
        duracion = time.time() - ts_inicio
        print(f"✅ [Databricks] {len(df)} filas en {duracion:.2f}s")
        return df.to_string(index=False)
    except ImportError:
        return "❌ Instala el conector: pip install databricks-sql-connector"
    except Exception as e:
        return f"❌ Error Databricks ({time.time()-ts_inicio:.1f}s): {e}"


@tool
def grafico_barras(query: str, titulo: str = "Análisis") -> str:
    """Genera un gráfico de barras a partir de una consulta SQL. Retorna la ruta del PNG."""
    conn = _get_conn_duckdb()
    try:
        df = conn.execute(query).fetchdf()
        if df.empty:
            return "Sin datos para graficar."
        col_x, col_y = df.columns[0], (df.columns[1] if len(df.columns) > 1 else df.columns[0])
        fig, ax = plt.subplots(figsize=(10, 5))
        ax.bar(df[col_x].astype(str), pd.to_numeric(df[col_y], errors="coerce"),
               color=_PALETTE[:len(df)])
        ax.set_title(titulo, fontsize=14, color=_CORP_BLUE, fontweight="bold")
        ax.set_xlabel(col_x)
        ax.set_ylabel(col_y)
        ax.tick_params(axis="x", rotation=30)
        ruta = _save_chart(titulo)
        return f"✅ Gráfico guardado: {ruta}\nDatos:\n{df.to_string(index=False)}"
    except Exception as e:
        return f"Error: {e}"


@tool
def grafico_tendencia_diaria(query: str, titulo: str = "Tendencia Diaria") -> str:
    """Genera un gráfico de línea de tendencia diaria desde una consulta SQL."""
    conn = _get_conn_duckdb()
    try:
        df = conn.execute(query).fetchdf()
        if df.empty:
            return "Sin datos para graficar."
        col_x, col_y = df.columns[0], (df.columns[1] if len(df.columns) > 1 else df.columns[0])
        fig, ax = plt.subplots(figsize=(12, 5))
        vals = pd.to_numeric(df[col_y], errors="coerce")
        ax.plot(range(len(df)), vals, color=_CORP_BLUE, linewidth=2, marker="o", markersize=4)
        ax.fill_between(range(len(df)), vals, alpha=0.1, color=_CORP_GREEN)
        ax.set_title(titulo, fontsize=14, color=_CORP_BLUE, fontweight="bold")
        ax.set_xlabel(col_x)
        ax.set_ylabel(col_y)
        plt.xticks(range(len(df)), df[col_x].astype(str), rotation=30)
        ruta = _save_chart(titulo)
        return f"✅ Gráfico guardado: {ruta}\nDatos:\n{df.to_string(index=False)}"
    except Exception as e:
        return f"Error: {e}"


@tool
def grafico_segmentos(query: str, titulo: str = "Distribución por Segmento") -> str:
    """Genera un gráfico de pie desde una consulta SQL."""
    conn = _get_conn_duckdb()
    try:
        df = conn.execute(query).fetchdf()
        if df.empty:
            return "Sin datos para graficar."
        col_label = df.columns[0]
        col_val = df.columns[1] if len(df.columns) > 1 else df.columns[0]
        fig, ax = plt.subplots(figsize=(8, 8))
        ax.pie(pd.to_numeric(df[col_val], errors="coerce").fillna(0),
               labels=df[col_label].astype(str), autopct="%1.1f%%",
               colors=_PALETTE[:len(df)], startangle=90)
        ax.set_title(titulo, fontsize=14, color=_CORP_BLUE, fontweight="bold")
        ruta = _save_chart(titulo)
        return f"✅ Gráfico guardado: {ruta}\nDatos:\n{df.to_string(index=False)}"
    except Exception as e:
        return f"Error: {e}"


@tool
def perfil_usuario_360(user_id: str) -> str:
    """Retorna el perfil completo 360 de un usuario dado su ID (sin datos PII)."""
    conn = _get_conn_duckdb()
    try:
        df = conn.execute(
            "SELECT * FROM gold_user_360 WHERE user_id = ?", [user_id]
        ).fetchdf()
        if df.empty:
            return f"No se encontró el usuario '{user_id}'."
        # Eliminar columnas PII antes de retornar
        df = df.drop(columns=[c for c in df.columns
                               if c.lower() in {"user_name","user_email","user_age"}],
                     errors="ignore")
        return df.to_string(index=False)
    except Exception as e:
        return f"Error: {e}"


@tool
def resumen_ejecutivo() -> str:
    """Genera un resumen ejecutivo completo con KPIs, segmentos y ciudades desde la capa Gold."""
    conn = _get_conn_duckdb()
    try:
        kpis = conn.execute("""
            SELECT
                COUNT(*)                                              AS total_usuarios,
                ROUND(SUM(total_transactions), 0)                    AS total_transacciones,
                ROUND(SUM(total_amount_cop)/1e6, 2)                  AS volumen_M_cop,
                ROUND(SUM(total_amount_usd), 0)                      AS volumen_usd,
                ROUND(AVG(avg_ticket), 0)                            AS ticket_promedio,
                ROUND(AVG(failure_rate)*100, 1)                      AS tasa_fallo_pct,
                COUNT(CASE WHEN days_since_last_tx > 30 THEN 1 END)  AS usuarios_inactivos_30d,
                ROUND(SUM(total_amount_cop)/COUNT(*)/1e3, 1)         AS revenue_K_por_usuario
            FROM gold_user_360
        """).fetchdf()

        seg = conn.execute("""
            SELECT user_segment,
                   COUNT(*) AS usuarios,
                   ROUND(SUM(total_amount_cop)/COUNT(*), 0) AS revenue_por_usuario,
                   ROUND(AVG(failure_rate)*100, 1) AS tasa_fallo_pct,
                   ROUND(AVG(avg_ticket), 0) AS ticket_promedio
            FROM gold_user_360
            GROUP BY user_segment
            ORDER BY revenue_por_usuario DESC
        """).fetchdf()

        city = conn.execute("""
            SELECT city,
                   COUNT(*) AS usuarios,
                   ROUND(SUM(total_amount_cop)/COUNT(*), 0) AS revenue_por_usuario,
                   ROUND(AVG(failure_rate)*100, 1) AS tasa_fallo_pct
            FROM gold_user_360
            GROUP BY city
            ORDER BY revenue_por_usuario DESC
        """).fetchdf()

        merchant = conn.execute("""
            SELECT top_merchant,
                   COUNT(*) AS usuarios,
                   ROUND(SUM(total_amount_cop)/COUNT(*), 0) AS ticket_por_usuario
            FROM gold_user_360
            WHERE top_merchant IS NOT NULL
            GROUP BY top_merchant
            ORDER BY ticket_por_usuario DESC
            LIMIT 5
        """).fetchdf()

        return (
            "KPIs GLOBALES:\n" + kpis.to_string(index=False) +
            "\n\nSEGMENTOS (ordenados por revenue/usuario):\n" + seg.to_string(index=False) +
            "\n\nCIUDADES (ordenadas por revenue/usuario):\n" + city.to_string(index=False) +
            "\n\nTOP MERCHANTS (por ticket/usuario):\n" + merchant.to_string(index=False)
        )
    except Exception as e:
        return f"Error: {e}"


@tool
def detectar_alertas() -> str:
    """
    Diagnóstico automático de la capa Gold. Detecta anomalías críticas sin necesidad
    de que el usuario sepa qué preguntar: tasas de fallo elevadas por segmento,
    concentración de revenue, churn acelerado, caídas bruscas en actividad diaria
    y segmentos con balance en riesgo. Úsala al inicio de sesión o cuando el usuario
    pregunte por el estado general del negocio.
    """
    conn = _get_conn_duckdb()
    alertas = []

    # ── 1. KPIs globales base ────────────────────────────────────────────────
    base = conn.execute("""
        SELECT
            ROUND(AVG(failure_rate)*100, 2)                           AS tasa_fallo_global,
            COUNT(*)                                                  AS total_usuarios,
            COUNT(CASE WHEN days_since_last_tx > 30 THEN 1 END)       AS inactivos_30d,
            COUNT(CASE WHEN days_since_last_tx > 60 THEN 1 END)       AS inactivos_60d,
            ROUND(SUM(total_amount_cop)/1e6, 2)                       AS volumen_M_cop,
            ROUND(AVG(avg_ticket), 0)                                 AS ticket_promedio
        FROM gold_user_360
    """).fetchdf().iloc[0]

    tasa_global  = float(base["tasa_fallo_global"])
    total_u      = int(base["total_usuarios"])
    inact_30     = int(base["inactivos_30d"])
    inact_60     = int(base["inactivos_60d"])
    pct_inact_30 = round(inact_30 * 100 / total_u, 1) if total_u else 0
    pct_inact_60 = round(inact_60 * 100 / total_u, 1) if total_u else 0

    # ── 2. Tasa de fallo global ───────────────────────────────────────────────
    if tasa_global > 5:
        alertas.append(f"🔴 CRÍTICO  | Tasa fallo global {tasa_global}% supera umbral crítico (5%). Impacto directo en revenue.")
    elif tasa_global > 3:
        alertas.append(f"🟡 ATENCIÓN | Tasa fallo global {tasa_global}% supera umbral saludable (3%).")
    else:
        alertas.append(f"🟢 OK       | Tasa fallo global {tasa_global}% dentro del rango saludable (<3%).")

    # ── 3. Tasa de fallo por segmento ─────────────────────────────────────────
    seg_fallos = conn.execute("""
        SELECT user_segment,
               ROUND(AVG(failure_rate)*100, 1) AS tasa_fallo,
               COUNT(*)                         AS usuarios
        FROM gold_user_360
        GROUP BY user_segment
        ORDER BY tasa_fallo DESC
    """).fetchdf()

    for _, row in seg_fallos.iterrows():
        tf = float(row["tasa_fallo"])
        if tf > 5:
            alertas.append(f"🔴 CRÍTICO  | Segmento '{row['user_segment']}' ({int(row['usuarios'])} usuarios): fallo {tf}%. Revisar de inmediato.")
        elif tf > 3:
            alertas.append(f"🟡 ATENCIÓN | Segmento '{row['user_segment']}': fallo {tf}%. Por encima del umbral saludable.")

    # ── 4. Concentración de revenue ───────────────────────────────────────────
    conc = conn.execute("""
        SELECT user_segment,
               ROUND(SUM(total_amount_cop)*100.0 / (SELECT SUM(total_amount_cop) FROM gold_user_360), 1) AS pct_revenue
        FROM gold_user_360
        GROUP BY user_segment
        ORDER BY pct_revenue DESC
        LIMIT 1
    """).fetchdf().iloc[0]

    pct_rev = float(conc["pct_revenue"])
    if pct_rev > 60:
        alertas.append(f"🔴 CONCENTRACIÓN | Segmento '{conc['user_segment']}' acumula el {pct_rev}% del revenue. Riesgo alto de dependencia.")
    elif pct_rev > 45:
        alertas.append(f"🟡 CONCENTRACIÓN | Segmento '{conc['user_segment']}' acumula el {pct_rev}% del revenue. Diversificación recomendada.")
    else:
        alertas.append(f"🟢 OK       | Revenue distribuido: el segmento más grande concentra solo el {pct_rev}%.")

    # ── 5. Churn / Inactividad ────────────────────────────────────────────────
    if pct_inact_30 > 30:
        alertas.append(f"🔴 CHURN ELEVADO | {inact_30} usuarios ({pct_inact_30}%) sin transaccionar en 30+ días.")
    elif pct_inact_30 > 20:
        alertas.append(f"🟡 CHURN MODERADO | {inact_30} usuarios ({pct_inact_30}%) inactivos >30 días.")
    else:
        alertas.append(f"🟢 OK       | Inactividad 30d: {pct_inact_30}% ({inact_30} usuarios).")

    if pct_inact_60 > 15:
        alertas.append(f"🔴 CHURN PROFUNDO | {inact_60} usuarios ({pct_inact_60}%) sin transaccionar en 60+ días. Riesgo de pérdida permanente.")

    # ── 6. Tendencia reciente (últimos 3 días vs 7 días anteriores) ───────────
    try:
        diario = conn.execute("""
            WITH ranked AS (
                SELECT total_transactions, failed_count,
                       ROW_NUMBER() OVER (ORDER BY date DESC) AS rn
                FROM gold_daily_metrics
            )
            SELECT
                ROUND(AVG(CASE WHEN rn <= 3 THEN total_transactions END), 0) AS tx_reciente,
                ROUND(AVG(CASE WHEN rn BETWEEN 4 AND 10 THEN total_transactions END), 0) AS tx_anterior,
                ROUND(AVG(CASE WHEN rn <= 3 THEN failed_count END), 0) AS fallos_reciente,
                ROUND(AVG(CASE WHEN rn BETWEEN 4 AND 10 THEN failed_count END), 0) AS fallos_anterior
            FROM ranked
        """).fetchdf().iloc[0]

        tx_rec = float(diario["tx_reciente"] or 0)
        tx_ant = float(diario["tx_anterior"] or 0)
        if tx_ant > 0:
            delta = round((tx_rec - tx_ant) / tx_ant * 100, 1)
            if delta < -20:
                alertas.append(f"🔴 CAÍDA DE ACTIVIDAD | Transacciones últimos 3d vs semana previa: {delta}%. Investigar causa urgente.")
            elif delta < -10:
                alertas.append(f"🟡 ACTIVIDAD BAJA | Transacciones 3d vs semana previa: {delta}%.")
            elif delta > 20:
                alertas.append(f"🟢 PICO DE ACTIVIDAD | Transacciones 3d vs semana previa: +{delta}%. Monitorear capacidad.")
            else:
                alertas.append(f"🟢 OK       | Actividad estable: variación transacciones 3d vs semana previa: {'+' if delta >= 0 else ''}{delta}%.")
    except Exception:
        pass

    # ── 7. Balance crítico por segmento ──────────────────────────────────────
    try:
        bal = conn.execute("""
            SELECT user_segment, ROUND(AVG(balance_current), 0) AS balance_promedio
            FROM gold_user_360
            GROUP BY user_segment
            ORDER BY balance_promedio ASC
            LIMIT 1
        """).fetchdf().iloc[0]

        bp = float(bal["balance_promedio"])
        if bp < 0:
            alertas.append(f"🔴 SALDO NEGATIVO | Segmento '{bal['user_segment']}': balance promedio COP {bp:,.0f}. Riesgo de crédito elevado.")
        elif bp < 50_000:
            alertas.append(f"🟡 SALDO BAJO | Segmento '{bal['user_segment']}': balance promedio COP {bp:,.0f}. Posible fricción en pagos futuros.")
    except Exception:
        pass

    criticos  = sum(1 for a in alertas if "🔴" in a)
    atenciones = sum(1 for a in alertas if "🟡" in a)
    oks       = sum(1 for a in alertas if "🟢" in a)

    encabezado = (
        f"📋 DIAGNÓSTICO AUTOMÁTICO — {len(alertas)} hallazgos "
        f"({criticos} críticos · {atenciones} advertencias · {oks} OK)\n"
        f"{'═'*60}\n\n"
    )
    return encabezado + "\n".join(f"{i+1}. {a}" for i, a in enumerate(alertas))


@tool
def comparar_periodos(dias_atras: int = 7) -> str:
    """
    Compara las métricas clave del negocio entre dos períodos consecutivos
    usando la capa Gold diaria (gold_daily_metrics).

    Ejemplo: dias_atras=7 compara los últimos 7 días contra los 7 días anteriores.
    Muestra variación porcentual con semáforo de color para cada métrica.

    Args:
        dias_atras: Tamaño del período en días (1-30). Default: 7.
    Returns:
        Tabla comparativa con deltas porcentuales y tendencias.
    """
    dias = max(1, min(int(dias_atras), 30))
    conn = _get_conn_duckdb()

    try:
        comparativa = conn.execute(f"""
            WITH ranked AS (
                SELECT *,
                       ROW_NUMBER() OVER (ORDER BY date DESC) AS rn
                FROM gold_daily_metrics
            ),
            actual   AS (SELECT * FROM ranked WHERE rn <= {dias}),
            anterior AS (SELECT * FROM ranked WHERE rn > {dias} AND rn <= {dias * 2})
            SELECT
                'ACTUAL'                                                          AS periodo,
                MIN(date)                                                         AS desde,
                MAX(date)                                                         AS hasta,
                COUNT(*)                                                          AS dias_datos,
                SUM(total_transactions)                                           AS transacciones,
                ROUND(SUM(total_amount_cop)/1e6, 3)                               AS volumen_M_cop,
                ROUND(AVG(unique_users), 0)                                       AS usuarios_activos_dia,
                SUM(unique_users)                                                 AS usuarios_totales,
                SUM(failed_count)                                                 AS fallos,
                ROUND(SUM(failed_count)*100.0/NULLIF(SUM(total_transactions),0), 2) AS tasa_fallo_pct
            FROM actual
            UNION ALL
            SELECT
                'ANTERIOR',
                MIN(date), MAX(date), COUNT(*),
                SUM(total_transactions),
                ROUND(SUM(total_amount_cop)/1e6, 3),
                ROUND(AVG(unique_users), 0),
                SUM(unique_users),
                SUM(failed_count),
                ROUND(SUM(failed_count)*100.0/NULLIF(SUM(total_transactions),0), 2)
            FROM anterior
        """).fetchdf()
    except Exception as e:
        return f"❌ Error al comparar períodos: {e}"

    if comparativa.empty or len(comparativa) < 2:
        return "⚠️ No hay suficientes datos diarios para comparar dos períodos. Verifica gold_daily_metrics."

    act = comparativa[comparativa["periodo"] == "ACTUAL"].iloc[0]
    ant = comparativa[comparativa["periodo"] == "ANTERIOR"].iloc[0]

    metricas = [
        ("Transacciones totales",    "transacciones",       False, "{:,.0f}"),
        ("Volumen (M COP)",          "volumen_M_cop",       False, "{:.3f}M"),
        ("Usuarios activos/día",     "usuarios_activos_dia",False, "{:,.0f}"),
        ("Usuarios totales período", "usuarios_totales",    False, "{:,.0f}"),
        ("Fallos totales",           "fallos",              True,  "{:,.0f}"),
        ("Tasa de fallo (%)",        "tasa_fallo_pct",      True,  "{:.2f}%"),
    ]

    lineas = [
        f"📊 COMPARATIVA DE PERÍODOS — últimos {dias} días vs {dias} días anteriores",
        f"   Período actual:   {act['desde']} → {act['hasta']} ({int(act['dias_datos'])} días con datos)",
        f"   Período anterior: {ant['desde']} → {ant['hasta']} ({int(ant['dias_datos'])} días con datos)",
        "═" * 68,
        f"{'Métrica':<30} {'Actual':>12} {'Anterior':>12} {'Δ':>10}",
        "─" * 68,
    ]

    for label, col, menor_es_mejor, fmt in metricas:
        try:
            v_act = float(act[col]) if not pd.isna(act[col]) else 0.0
            v_ant = float(ant[col]) if not pd.isna(ant[col]) else 0.0
        except Exception:
            continue

        if v_ant != 0:
            delta = round((v_act - v_ant) / abs(v_ant) * 100, 1)
            mejora = (delta < 0) if menor_es_mejor else (delta > 0)
            emoji = "🟢" if mejora else ("🔴" if abs(delta) > 10 else "🟡")
            signo = "+" if delta >= 0 else ""
            variacion = f"{emoji} {signo}{delta}%"
        else:
            variacion = "   N/D"

        s_act = fmt.format(v_act)
        s_ant = fmt.format(v_ant)
        lineas.append(f"{label:<30} {s_act:>12} {s_ant:>12} {variacion:>10}")

    lineas.append("─" * 68)

    # Tendencia resumen
    try:
        tx_delta = round((float(act["transacciones"]) - float(ant["transacciones"])) / float(ant["transacciones"]) * 100, 1)
        vol_delta = round((float(act["volumen_M_cop"]) - float(ant["volumen_M_cop"])) / float(ant["volumen_M_cop"]) * 100, 1)
        tf_delta  = round(float(act["tasa_fallo_pct"]) - float(ant["tasa_fallo_pct"]), 2)
        tendencia = "📈 Crecimiento" if tx_delta > 0 and vol_delta > 0 else ("📉 Contracción" if tx_delta < 0 and vol_delta < 0 else "➡️  Mixta")
        lineas.append(f"Tendencia general: {tendencia}  |  Δ tasa fallo: {'+' if tf_delta >= 0 else ''}{tf_delta} pp")
    except Exception:
        pass

    return "\n".join(lineas)


@tool
def generar_reporte_html() -> str:
    """
    Genera un reporte ejecutivo HTML completo y autocontenido con:
    KPIs clave, gráficos embebidos en base64, tablas de datos, diagnóstico
    de alertas y análisis narrativo de Ollama.
    El archivo se guarda en outputs/reports/ y no requiere conexión a internet.
    Retorna la ruta del archivo HTML generado.
    """

    conn = _get_conn_duckdb()
    fecha_str = datetime.datetime.now().strftime("%d/%m/%Y %H:%M")
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")

    # ── 1. Consultas de datos ─────────────────────────────────────────────────
    kpis = conn.execute("""
        SELECT COUNT(*) AS usuarios,
               ROUND(SUM(total_transactions),0) AS transacciones,
               ROUND(SUM(total_amount_cop)/1e6,2) AS volumen_M_cop,
               ROUND(AVG(avg_ticket),0) AS ticket_promedio,
               ROUND(AVG(failure_rate)*100,1) AS tasa_fallo_pct,
               COUNT(CASE WHEN days_since_last_tx > 30 THEN 1 END) AS inactivos_30d
        FROM gold_user_360
    """).fetchdf()

    seg = conn.execute("""
        SELECT user_segment AS Segmento, COUNT(*) AS Usuarios,
               ROUND(SUM(total_amount_cop)/COUNT(*),0) AS Revenue_por_usuario,
               ROUND(AVG(avg_ticket),0) AS Ticket_promedio,
               ROUND(AVG(failure_rate)*100,1) AS Tasa_fallo_pct,
               COUNT(CASE WHEN days_since_last_tx>30 THEN 1 END) AS Inactivos_30d
        FROM gold_user_360 GROUP BY user_segment ORDER BY Revenue_por_usuario DESC
    """).fetchdf()

    city = conn.execute("""
        SELECT city AS Ciudad, COUNT(*) AS Usuarios,
               ROUND(SUM(total_amount_cop)/COUNT(*),0) AS Revenue_por_usuario,
               ROUND(AVG(failure_rate)*100,1) AS Tasa_fallo_pct
        FROM gold_user_360 GROUP BY city ORDER BY Revenue_por_usuario DESC LIMIT 10
    """).fetchdf()

    merchant = conn.execute("""
        SELECT top_merchant AS Merchant, COUNT(*) AS Usuarios,
               ROUND(SUM(total_amount_cop)/COUNT(*),0) AS Revenue_por_usuario
        FROM gold_user_360 WHERE top_merchant IS NOT NULL
        GROUP BY top_merchant ORDER BY Revenue_por_usuario DESC LIMIT 8
    """).fetchdf()

    daily = conn.execute("""
        SELECT date AS Fecha, total_transactions AS Transacciones,
               ROUND(total_amount_cop/1e6,3) AS Volumen_M_COP,
               unique_users AS Usuarios_unicos,
               ROUND(failed_count*100.0/NULLIF(total_transactions,0),1) AS Tasa_fallo_pct
        FROM gold_daily_metrics ORDER BY date DESC LIMIT 14
    """).fetchdf()

    alertas_txt = detectar_alertas()
    comparativa_txt = comparar_periodos(7)

    # ── 2. Gráficos → base64 ─────────────────────────────────────────────────
    def _bar_b64(df, col_x, col_y, titulo):
        fig, ax = plt.subplots(figsize=(8, 4))
        ax.bar(df[col_x].astype(str), pd.to_numeric(df[col_y], errors="coerce"),
               color=_PALETTE[:len(df)])
        ax.set_title(titulo, fontsize=12, color=_CORP_BLUE, fontweight="bold")
        ax.tick_params(axis="x", rotation=25)
        return _chart_to_base64()

    def _line_b64(df, col_x, col_y, titulo):
        fig, ax = plt.subplots(figsize=(10, 4))
        vals = pd.to_numeric(df[col_y], errors="coerce")
        ax.plot(range(len(df)), vals, color=_CORP_BLUE, linewidth=2, marker="o", markersize=4)
        ax.fill_between(range(len(df)), vals, alpha=0.1, color=_CORP_GREEN)
        ax.set_title(titulo, fontsize=12, color=_CORP_BLUE, fontweight="bold")
        plt.xticks(range(len(df)), df[col_x].astype(str), rotation=30, fontsize=7)
        return _chart_to_base64()

    chart_seg = _bar_b64(seg, "Segmento", "Revenue_por_usuario", "Revenue por Segmento")
    chart_city = _bar_b64(city.head(8), "Ciudad", "Revenue_por_usuario", "Revenue por Ciudad (Top 8)")
    chart_daily = _line_b64(daily.iloc[::-1].reset_index(drop=True),
                            "Fecha", "Transacciones", "Tendencia Diaria de Transacciones")

    # ── 3. Análisis narrativo de Ollama ────────────────────────────────────────
    datos_para_ollama = (
        f"KPIs globales:\n{kpis.to_string(index=False)}\n\n"
        f"Por segmento:\n{seg.to_string(index=False)}\n\n"
        f"Por ciudad (top 10):\n{city.to_string(index=False)}\n\n"
        f"Top merchants:\n{merchant.to_string(index=False)}"
    )
    analisis_narrativo = ""
    if _verificar_ollama():
        try:
            r = requests.post(
                f"{OLLAMA_BASE_URL}/api/chat",
                json={
                    "model": OLLAMA_MODEL,
                    "messages": [
                        {"role": "system", "content": _SYSTEM_INTERPRETACION},
                        {"role": "user", "content":
                            f"Genera el análisis ejecutivo para el reporte mensual del negocio.\n\n{datos_para_ollama}"},
                    ],
                    "stream": False,
                    "options": {"num_ctx": 4096},
                },
                timeout=180,
            )
            r.raise_for_status()
            analisis_narrativo = r.json().get("message", {}).get("content", "").strip()
        except Exception as e:
            analisis_narrativo = f"_(Ollama no disponible: {e})_"
    else:
        analisis_narrativo = "_(Ollama no disponible para análisis narrativo)_"

    # ── 4. Helpers HTML ────────────────────────────────────────────────────────
    def _df_to_html(df: pd.DataFrame) -> str:
        ths = "".join(f"<th>{c}</th>" for c in df.columns)
        rows = ""
        for _, row in df.iterrows():
            cells = "".join(f"<td>{v}</td>" for v in row)
            rows += f"<tr>{cells}</tr>"
        return f"<table><thead><tr>{ths}</tr></thead><tbody>{rows}</tbody></table>"

    def _kpi_card(label: str, value: str, color: str = "#1B4F72") -> str:
        return (
            f'<div class="kpi-card">'
            f'<div class="kpi-value" style="color:{color}">{value}</div>'
            f'<div class="kpi-label">{label}</div>'
            f'</div>'
        )

    row = kpis.iloc[0]
    kpi_cards = "".join([
        _kpi_card("Usuarios totales",      f"{int(row['usuarios']):,}"),
        _kpi_card("Transacciones",         f"{int(row['transacciones']):,}"),
        _kpi_card("Volumen (M COP)",       f"{row['volumen_M_cop']:.2f}M"),
        _kpi_card("Ticket promedio",       f"${int(row['ticket_promedio']):,}"),
        _kpi_card("Tasa de fallo",         f"{row['tasa_fallo_pct']}%",
                  "#e74c3c" if row["tasa_fallo_pct"] > 5 else
                  "#f39c12" if row["tasa_fallo_pct"] > 3 else "#27ae60"),
        _kpi_card("Inactivos 30d",         f"{int(row['inactivos_30d']):,}",
                  "#e74c3c" if int(row["inactivos_30d"]) / int(row["usuarios"]) > 0.3 else "#1B4F72"),
    ])

    alertas_html = ""
    for linea in alertas_txt.split("\n"):
        if "🔴" in linea:
            alertas_html += f'<p class="alert-critical">{linea}</p>'
        elif "🟡" in linea:
            alertas_html += f'<p class="alert-warning">{linea}</p>'
        elif "🟢" in linea:
            alertas_html += f'<p class="alert-ok">{linea}</p>'
        else:
            alertas_html += f'<p>{linea}</p>'

    # ── 5. Ensamblado HTML ────────────────────────────────────────────────────
    css = """
    *{box-sizing:border-box;margin:0;padding:0}
    body{font-family:'Segoe UI',Arial,sans-serif;background:#f0f3f7;color:#2c3e50;font-size:14px}
    .header{background:linear-gradient(135deg,#1B4F72,#2980B9);color:#fff;padding:28px 40px}
    .header h1{font-size:1.9em;font-weight:700}
    .header p{opacity:.85;margin-top:6px;font-size:.95em}
    .container{max-width:1200px;margin:0 auto;padding:28px 20px}
    .kpi-grid{display:flex;flex-wrap:wrap;gap:16px;margin-bottom:32px}
    .kpi-card{flex:1;min-width:160px;background:#fff;border-radius:10px;padding:18px 20px;
              box-shadow:0 2px 8px rgba(0,0,0,.07);border-left:4px solid #1B4F72}
    .kpi-value{font-size:1.75em;font-weight:700}
    .kpi-label{color:#7f8c8d;font-size:.82em;margin-top:5px;text-transform:uppercase;letter-spacing:.5px}
    .section{background:#fff;border-radius:10px;padding:24px;margin-bottom:28px;
             box-shadow:0 2px 8px rgba(0,0,0,.07)}
    .section h2{color:#1B4F72;border-bottom:3px solid #2ECC71;padding-bottom:10px;
                margin-bottom:18px;font-size:1.1em;text-transform:uppercase;letter-spacing:.5px}
    .charts-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(340px,1fr));gap:22px}
    .chart-item img{width:100%;border-radius:6px;border:1px solid #ecf0f1}
    .chart-caption{text-align:center;color:#95a5a6;font-size:.8em;margin-top:6px}
    table{width:100%;border-collapse:collapse;font-size:.88em}
    th{background:#1B4F72;color:#fff;padding:9px 12px;text-align:left;font-weight:600}
    td{padding:7px 12px;border-bottom:1px solid #ecf0f1}
    tr:nth-child(even){background:#f8f9fa}
    tr:hover{background:#eaf4fb}
    .analysis{white-space:pre-wrap;line-height:1.75;font-size:.93em;color:#34495e}
    .alert-critical{color:#c0392b;margin:4px 0;font-size:.92em}
    .alert-warning{color:#e67e22;margin:4px 0;font-size:.92em}
    .alert-ok{color:#27ae60;margin:4px 0;font-size:.92em}
    .footer{text-align:center;color:#bdc3c7;font-size:.78em;padding:24px;border-top:1px solid #ecf0f1;margin-top:10px}
    @media print{body{background:#fff}.section{box-shadow:none;border:1px solid #ddd}}
    """

    html = f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Reporte Ejecutivo Fintech — {fecha_str}</title>
<style>{css}</style>
</head>
<body>

<div class="header">
  <h1>Reporte Ejecutivo Fintech</h1>
  <p>Pipeline Gold Analytics &nbsp;·&nbsp; Generado: {fecha_str} &nbsp;·&nbsp; Modelo: {OLLAMA_MODEL}</p>
</div>

<div class="container">

  <div class="section">
    <h2>KPIs Globales</h2>
    <div class="kpi-grid">{kpi_cards}</div>
  </div>

  <div class="section">
    <h2>Análisis Visual</h2>
    <div class="charts-grid">
      <div class="chart-item">
        <img src="data:image/png;base64,{chart_seg}" alt="Revenue por Segmento">
        <div class="chart-caption">Revenue por Segmento</div>
      </div>
      <div class="chart-item">
        <img src="data:image/png;base64,{chart_city}" alt="Revenue por Ciudad">
        <div class="chart-caption">Revenue por Ciudad (Top 8)</div>
      </div>
      <div class="chart-item" style="grid-column:1/-1">
        <img src="data:image/png;base64,{chart_daily}" alt="Tendencia Diaria">
        <div class="chart-caption">Tendencia Diaria de Transacciones (últimos 14 días)</div>
      </div>
    </div>
  </div>

  <div class="section">
    <h2>Análisis Ejecutivo (IA)</h2>
    <div class="analysis">{analisis_narrativo}</div>
  </div>

  <div class="section">
    <h2>Diagnóstico de Alertas</h2>
    {alertas_html}
    <br>
    <pre style="font-size:.82em;color:#555;background:#f8f9fa;padding:14px;border-radius:6px;overflow-x:auto">{comparativa_txt}</pre>
  </div>

  <div class="section">
    <h2>Rendimiento por Segmento</h2>
    {_df_to_html(seg)}
  </div>

  <div class="section">
    <h2>Rendimiento por Ciudad</h2>
    {_df_to_html(city)}
  </div>

  <div class="section">
    <h2>Top Merchants</h2>
    {_df_to_html(merchant)}
  </div>

  <div class="section">
    <h2>Últimos 14 Días</h2>
    {_df_to_html(daily)}
  </div>

</div>

<div class="footer">
  Generado automáticamente por el Agente Fintech &nbsp;·&nbsp;
  Datos: Capa Gold (DuckDB/Databricks) &nbsp;·&nbsp;
  Análisis: Ollama {OLLAMA_MODEL}
</div>

</body>
</html>"""

    # ── 6. Guardar ────────────────────────────────────────────────────────────
    directorio = Path(__file__).resolve().parents[2] / "outputs" / "reports"
    directorio.mkdir(parents=True, exist_ok=True)
    ruta = directorio / f"reporte_ejecutivo_{ts}.html"
    ruta.write_text(html, encoding="utf-8")

    return f"✅ Reporte generado: {ruta}\nAbre el archivo en cualquier navegador — no requiere conexión a internet."


# ══════════════════════════════════════════════════════════════════════════════
# MODELO OLLAMA (implementa la interfaz de strands)
# ══════════════════════════════════════════════════════════════════════════════

class OllamaModel:
    """
    Adaptador que conecta Strands con Ollama corriendo en localhost:11434.
    Implementa el protocolo de streaming requerido por strands.Agent.
    """

    def __init__(self, model_id: str = "llama3.2"):
        self.model_id = model_id
        self.stateful = False
        self.config = {"model_id": model_id, "max_tokens": 4096}
        self._tools_registry: dict = {}

    def _build_chat(self, messages: list, system_prompt: str = None) -> list:
        chat = []
        if system_prompt:
            chat.append({"role": "system", "content": system_prompt})
        for m in messages:
            role = m.get("role", "user")
            content = m.get("content", "")
            if isinstance(content, list):
                content = " ".join(
                    c.get("text", "") for c in content if isinstance(c, dict)
                )
            chat.append({"role": role, "content": content})
        return chat

    def _inject_tools_in_system(self, chat: list) -> list:
        if not self._tools_registry:
            return chat
        desc = (
            "\n\nTIENES ACCESO A ESTAS FUNCIONES. Cuando el análisis requiera "
            "una, responde ÚNICAMENTE con este JSON (sin texto adicional):\n"
            '{"tool": "nombre_tool", "args": {"param": "valor"}}\n\n'
            "Funciones disponibles:\n"
        )
        for nombre, fn in self._tools_registry.items():
            doc = (getattr(fn, "__doc__", "") or "").strip()[:200]
            desc += f"- {nombre}: {doc}\n"
        if chat and chat[0]["role"] == "system":
            chat[0]["content"] += desc
        else:
            chat.insert(0, {"role": "system", "content": desc})
        return chat

    def _call_ollama(self, chat: list) -> str:
        url = f"{OLLAMA_BASE_URL}/api/chat"
        try:
            resp = requests.post(
                url,
                json={"model": self.model_id, "messages": chat, "stream": False, "options": {"num_ctx": 4096}},
                timeout=300,
            )
            resp.raise_for_status()
            return resp.json().get("message", {}).get("content", "")
        except requests.exceptions.ConnectionError:
            raise RuntimeError(
                f"❌ Ollama no responde en {OLLAMA_BASE_URL}.\n"
                f"   Inicia Ollama: ollama serve\n"
                f"   Descarga el modelo: ollama pull {self.model_id}"
            )

    def _detect_and_call_tool(self, text: str) -> str:
        """Detecta si el LLM quiere invocar una herramienta y la ejecuta."""
        call = _extraer_tool_call(text)
        if not call:
            return text
        try:
            tool_name = call.get("tool", "")
            args = call.get("args", {})
            fn = self._tools_registry.get(tool_name)
            if not fn:
                return text

            print(f"\n🔧 Tool invocada: {tool_name}({args})")
            resultado = fn(**args)
            print(f"   ✅ Resultado preview: {str(resultado)[:120]}...")

            # Pedir al LLM que interprete el resultado en lenguaje natural
            prompt_interpretacion = (
                "Eres un analista senior de datos fintech colombiano. "
                f"Datos obtenidos:\n\n{resultado}\n\n"
                "Responde en español, de forma profesional, con esta estructura:\n"
                "📊 RESUMEN: respuesta directa\n"
                "🔍 ANÁLISIS: qué muestran los números\n"
                "💡 INSIGHT CLAVE: qué significa para el negocio\n"
                "🎯 RECOMENDACIÓN: acción concreta\n\n"
                "No menciones nombres de tablas ni detalles técnicos internos."
            )
            return self._call_ollama([
                {"role": "system", "content": prompt_interpretacion},
                {"role": "user", "content": "Genera el análisis."},
            ])
        except Exception as e:
            print(f"   ❌ Error en tool: {e}")
            return text

    async def stream(self, messages, tool_specs=None, system_prompt=None, **kwargs):
        chat = self._build_chat(messages, system_prompt)
        chat = self._inject_tools_in_system(chat)
        text = self._call_ollama(chat)
        text = self._detect_and_call_tool(text)

        yield {"messageStart": {"role": "assistant"}}
        yield {"contentBlockStart": {"contentBlockIndex": 0, "start": {"text": ""}}}
        yield {"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"text": text}}}
        yield {"contentBlockStop": {"contentBlockIndex": 0}}
        yield {"messageStop": {"stopReason": "end_turn"}}
        yield {"metadata": {"usage": {"inputTokens": 0, "outputTokens": 0, "totalTokens": 0}}}

    def get_config(self):
        return self.config


# ══════════════════════════════════════════════════════════════════════════════
# CREACIÓN DEL AGENTE
# ══════════════════════════════════════════════════════════════════════════════

_HERRAMIENTAS = [
    consultar_sql,
    consultar_databricks,
    grafico_barras,
    grafico_tendencia_diaria,
    grafico_segmentos,
    perfil_usuario_360,
    resumen_ejecutivo,
    detectar_alertas,
    comparar_periodos,
    generar_reporte_html,
    listar_tablas,
]


def crear_agente() -> Agent:
    """
    Inicializa el agente con Ollama como LLM y carga los datos Gold.
    REQUERIDO: Ollama corriendo en localhost:11434 con llama3.2.
    """
    print(f"\n🔍 Verificando Ollama en {OLLAMA_BASE_URL}...")
    if not _verificar_ollama():
        raise RuntimeError(
            f"❌ Ollama no disponible o modelo '{OLLAMA_MODEL}' no encontrado.\n"
            f"   1. Inicia Ollama:         ollama serve\n"
            f"   2. Descarga el modelo:    ollama pull {OLLAMA_MODEL}\n"
            f"   3. Verifica con:          curl {OLLAMA_BASE_URL}/api/tags"
        )
    print(f"✅ Ollama OK — modelo: {OLLAMA_MODEL}")

    print("🔄 Cargando datos Gold (DuckDB local)...")
    _get_conn_duckdb()

    model = OllamaModel(model_id=OLLAMA_MODEL)
    model._tools_registry = {
        getattr(t, "__name__", str(t)): t for t in _HERRAMIENTAS
    }

    agente = Agent(
        model=model,
        system_prompt=SYSTEM_PROMPT,
        tools=_HERRAMIENTAS,
    )
    print("✅ Agente Fintech listo.\n")
    return agente


# ── API pública ──────────────────────────────────────────────────────────────
_agent_instance = None


_KEYWORDS_GRAFICO = {
    "grafica", "grafico", "gráfica", "gráfico", "diagrama",
    "barras", "torta", "pie", "tendencia", "linea", "línea",
    "visualiza", "chart", "plot",
}

def _es_peticion_de_grafico(pregunta: str) -> bool:
    p = _normalizar_texto(pregunta)
    return any(k in p for k in _KEYWORDS_GRAFICO)


# ── Validador SQL (Solución 4) ────────────────────────────────────────────────
def _validar_sql_grafico(sql: str, pregunta: str) -> str:
    """
    Valida y corrige SQL generado por Ollama para gráficos.
    Garantiza que las fórmulas coincidan con las del dashboard.

    Correcciones aplicadas:
      1. AVG(avg_ticket) → SUM/COUNT cuando el contexto es 'por usuario'
      2. IS NOT NULL obligatorio para columnas categóricas nullable
      3. LIMIT máximo si no está presente
    """
    p = _normalizar_texto(pregunta)

    # ── Corrección 1: fórmula de ticket/revenue por usuario ──────────────────
    # avg_ticket = promedio de UNA transacción; el dashboard usa SUM/COUNT
    contexto_por_usuario = any(k in p for k in (
        "ticket", "revenue", "ingreso", "monto", "gasto",
        "ticket por usuario", "ticket_por_usuario",
    )) and any(k in p for k in (
        "merchant", "merchants", "ciudad", "segmento", "canal",
        "dispositivo", "categoria", "usuario", "usuarios",
    ))
    if contexto_por_usuario:
        sql = re.sub(
            r"AVG\s*\(\s*avg_ticket\s*\)",
            "ROUND(SUM(total_amount_cop)/COUNT(*), 0)",
            sql, flags=re.IGNORECASE,
        )

    # ── Corrección 2: IS NOT NULL para columnas categóricas nullable ─────────
    col_sqls_nullable = {
        col_sql
        for _, (col_sql, where) in DIMENSIONES_GOLD.items()
        if where
    }
    for col_sql in col_sqls_nullable:
        if col_sql in sql:
            null_filter = f"{col_sql} IS NOT NULL"
            if null_filter.lower() not in sql.lower():
                if re.search(r"\bWHERE\b", sql, re.IGNORECASE):
                    # Ya hay WHERE — añadir con AND al principio
                    sql = re.sub(
                        r"(\bWHERE\b\s+)",
                        f"WHERE {null_filter} AND ",
                        sql, count=1, flags=re.IGNORECASE,
                    )
                else:
                    # Añadir WHERE antes de GROUP BY
                    sql = re.sub(
                        r"(\bGROUP\s+BY\b)",
                        f"WHERE {null_filter}\nGROUP BY",
                        sql, count=1, flags=re.IGNORECASE,
                    )

    # ── Corrección 3: asegurar LIMIT ─────────────────────────────────────────
    if not re.search(r"\bLIMIT\b", sql, re.IGNORECASE):
        sql = sql.rstrip() + "\nLIMIT 10"

    return sql


def _grafico_ollama_fallback(pregunta: str) -> str:
    """
    Usa Ollama para determinar el SQL y tipo de gráfico apropiado
    cuando los keywords no coinciden con ningún patrón conocido.
    """
    if not _verificar_ollama():
        return grafico_barras(
            "SELECT user_segment, COUNT(*) as usuarios FROM gold_user_360 "
            "GROUP BY user_segment ORDER BY usuarios DESC",
            "Análisis General por Segmento",
        )

    schema_ctx = (
        "Tablas disponibles:\n"
        "gold_user_360: user_id, user_segment, city, total_events, total_transactions, "
        "failed_transactions, failure_rate, total_amount_cop, total_amount_usd, avg_ticket, "
        "balance_current, top_merchant, top_category, preferred_channel, preferred_device, "
        "last_transaction_date, last_event_date, days_since_last_tx\n"
        "gold_daily_metrics: date, total_events, total_transactions, total_amount_cop, "
        "failed_count, unique_users\n"
        "gold_event_summary: event, count, success_count, failed_count, pct_of_total"
    )
    system = (
        "Eres un experto en SQL y visualización de datos fintech. "
        "Dado el esquema y una solicitud de gráfico, responde ÚNICAMENTE con un JSON válido: "
        '{"sql": "SELECT ...", "tipo": "bar|pie|line", "titulo": "título descriptivo"}\n'
        "REGLAS: solo columnas del esquema exacto. Solo SELECT. "
        "bar: máximo 2 columnas (etiqueta, valor numérico). "
        "pie: 2 columnas, máximo 8 filas. "
        "line: 2 columnas (fecha, valor), ORDER BY fecha. "
        "Responde SOLO con el JSON, sin texto adicional.\n\n"
        f"Esquema:\n{schema_ctx}"
    )
    try:
        resp = requests.post(
            f"{OLLAMA_BASE_URL}/api/chat",
            json={
                "model": OLLAMA_MODEL,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": f"Genera el gráfico para: {pregunta}"},
                ],
                "stream": False,
                "options": {"num_ctx": 4096},
            },
            timeout=60,
        )
        resp.raise_for_status()
        raw = resp.json().get("message", {}).get("content", "")
        call = _extraer_tool_call(raw)
        if call is None:
            raw_clean = re.sub(r"^```(?:json)?", "", raw.strip(), flags=re.IGNORECASE).strip()
            raw_clean = re.sub(r"```$", "", raw_clean).strip()
            call = json.loads(raw_clean)
        sql = call.get("sql", "")
        tipo = call.get("tipo", "bar").lower()
        titulo = call.get("titulo", "Análisis de Datos Gold")
        if not sql:
            raise ValueError("SQL vacío en respuesta de Ollama")
        if tipo == "pie":
            return grafico_segmentos(sql, titulo)
        if tipo == "line":
            return grafico_tendencia_diaria(sql, titulo)
        return grafico_barras(sql, titulo)
    except Exception as e:
        print(f"  [OllamaChart] Fallback a gráfico genérico: {e}")
        return grafico_barras(
            "SELECT user_segment, COUNT(*) as usuarios FROM gold_user_360 "
            "GROUP BY user_segment ORDER BY usuarios DESC",
            "Análisis General por Segmento",
        )


# ── Tipo de gráfico desde el lenguaje natural del usuario ─────────────────────
def _detectar_tipo_grafico(texto_normalizado: str) -> str | None:
    """Extrae el tipo de gráfico que el usuario pidió explícitamente."""
    t = texto_normalizado
    if any(k in t for k in ("torta", "pie", "circular", "dona", "pastel", "distribucion", "distribución")):
        return "pie"
    if any(k in t for k in ("linea", "línea", "lineas", "líneas", "temporal", "tendencia", "historico", "serie", "evolucion")):
        return "line"
    if any(k in t for k in ("barra", "barras", "columna", "columnas", "histograma", "comparar", "comparativa")):
        return "bar"
    return None


# ── Helpers de gráfico desde DataFrame (evitan re-ejecutar SQL) ───────────────
def _chart_df_pie(df: pd.DataFrame, titulo: str) -> str:
    col_label = df.columns[0]
    col_val   = df.columns[1] if len(df.columns) > 1 else df.columns[0]
    vals = pd.to_numeric(df[col_val], errors="coerce").fillna(0)
    fig, ax = plt.subplots(figsize=(8, 8))
    ax.pie(vals, labels=df[col_label].astype(str), autopct="%1.1f%%",
           colors=_PALETTE[:len(df)], startangle=90)
    ax.set_title(titulo, fontsize=14, color=_CORP_BLUE, fontweight="bold")
    ruta = _save_chart(titulo)
    return f"✅ Gráfico guardado: {ruta}"


def _chart_df_barras(df: pd.DataFrame, titulo: str, eje_x: str = "", eje_y: str = "") -> str:
    col_x = eje_x if eje_x in df.columns else df.columns[0]
    numericas = [c for c in df.columns if c != col_x and pd.to_numeric(df[c], errors="coerce").notna().any()]
    col_y = eje_y if eje_y in df.columns else (numericas[0] if numericas else df.columns[-1])

    horizontal = len(df) > 7
    ancho = max(10, len(df) * 1.2) if not horizontal else 10
    fig, ax = plt.subplots(figsize=(ancho, 5))
    vals = pd.to_numeric(df[col_y], errors="coerce")

    if horizontal:
        ax.barh(df[col_x].astype(str), vals, color=_PALETTE[:len(df)])
        ax.set_xlabel(col_y)
        ax.set_ylabel(col_x)
    else:
        ax.bar(df[col_x].astype(str), vals, color=_PALETTE[:len(df)])
        ax.set_xlabel(col_x)
        ax.set_ylabel(col_y)
        ax.tick_params(axis="x", rotation=35)

    if len(numericas) > 1:
        col_y2 = numericas[1]
        ax2 = ax.twinx()
        vals2 = pd.to_numeric(df[col_y2], errors="coerce")
        xs = range(len(df))
        ax2.plot(xs, vals2, color=_CORP_GREEN, linewidth=2, marker="o", markersize=5, label=col_y2)
        ax2.set_ylabel(col_y2, color=_CORP_GREEN)
        ax2.legend(loc="upper right")

    ax.set_title(titulo, fontsize=14, color=_CORP_BLUE, fontweight="bold")
    ruta = _save_chart(titulo)
    return f"✅ Gráfico guardado: {ruta}"


def _chart_df_linea(df: pd.DataFrame, titulo: str) -> str:
    col_x = df.columns[0]
    numericas = [c for c in df.columns[1:] if pd.to_numeric(df[c], errors="coerce").notna().any()]
    fig, ax = plt.subplots(figsize=(12, 5))
    for i, col in enumerate(numericas[:3]):
        vals = pd.to_numeric(df[col], errors="coerce")
        ax.plot(range(len(df)), vals, color=_PALETTE[i], linewidth=2,
                marker="o", markersize=4, label=col)
        if i == 0:
            ax.fill_between(range(len(df)), vals, alpha=0.08, color=_PALETTE[i])
    ax.set_title(titulo, fontsize=14, color=_CORP_BLUE, fontweight="bold")
    ax.set_xlabel(col_x)
    if len(numericas) > 1:
        ax.legend()
    plt.xticks(range(len(df)), df[col_x].astype(str), rotation=30, fontsize=7)
    ruta = _save_chart(titulo)
    return f"✅ Gráfico guardado: {ruta}"


# ── Análisis de 3 partes para gráficos ───────────────────────────────────────
def _analizar_grafico_con_ollama(
    datos_texto: str,
    titulo: str,
    tipo: str,
    pregunta: str,
) -> str:
    """
    Genera análisis ejecutivo de 3 partes obligatorias para un gráfico:
      1. Análisis por dato (cada categoría con cifras exactas)
      2. Distribución e interpretación (patrones, brechas, concentración)
      3. Conclusión y recomendación accionable
    """
    if not _verificar_ollama() or not datos_texto.strip():
        return ""

    tipo_nombre = {"pie": "torta/circular", "bar": "barras", "line": "línea temporal"}.get(tipo, tipo)

    system = f"""Eres un analista senior de negocio fintech colombiano con 15 años de experiencia.
Recibes los datos EXACTOS de un gráfico de la capa Gold y debes producir un análisis en 3 partes.

GRÁFICO: "{titulo}" (tipo: {tipo_nombre})
SOLICITUD ORIGINAL DEL USUARIO: "{pregunta}"

DATOS DEL GRÁFICO:
{datos_texto}

PRODUCE EXACTAMENTE estas 3 secciones (obligatorias, en este orden):

**📊 Análisis por dato**
Analiza CADA categoría/fila del gráfico individualmente con su valor exacto.
Identifica el mejor y el peor performer con cifras precisas.
Explica qué significa cada valor para el negocio fintech.

**📈 Distribución e interpretación**
Explica cómo se distribuyen los datos: ¿concentración? ¿dispersión? ¿outliers?
Calcula la brecha entre máximo y mínimo en términos porcentuales (X% superior/inferior).
Explica el patrón principal que revela el gráfico y su implicación operativa.

**✅ Conclusión y recomendación**
Resume los 2 hallazgos más importantes en cifras exactas.
Da UNA acción concreta priorizada y justificada con los datos.
Señala qué categoría merece atención inmediata y por qué.

REGLAS ABSOLUTAS:
- Usa SOLO las cifras de los datos proporcionados. NUNCA inventes números.
- Español profesional, máximo 350 palabras en total.
- Responde directamente con las 3 secciones, sin introducción."""

    try:
        resp = requests.post(
            f"{OLLAMA_BASE_URL}/api/chat",
            json={
                "model": OLLAMA_MODEL,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user",   "content": "Genera el análisis completo de 3 partes."},
                ],
                "stream": False,
                "options": {"num_ctx": 4096},
            },
            timeout=120,
        )
        resp.raise_for_status()
        return resp.json().get("message", {}).get("content", "").strip()
    except Exception as e:
        print(f"  [AnálisisGráfico] {e}")
        return ""


def _ejecutar_grafico_con_analisis(
    df: pd.DataFrame,
    titulo: str,
    tipo: str,
    pregunta: str,
) -> str:
    """
    Genera el gráfico + análisis completo de 3 partes.
    Siempre retorna el gráfico; el análisis se añade si Ollama está disponible.
    """
    # 1. Generar imagen del gráfico
    if tipo == "pie":
        chart_path = _chart_df_pie(df, titulo)
    elif tipo == "line":
        chart_path = _chart_df_linea(df, titulo)
    else:
        chart_path = _chart_df_barras(df, titulo)

    # 2. Tabla de datos en bloque colapsable de código
    datos_texto = df.to_string(index=False)
    tabla_md = f"**Datos Gold** _(fuente certificada)_\n```\n{datos_texto}\n```"

    # 3. Análisis de Ollama (3 partes)
    analisis = _analizar_grafico_con_ollama(datos_texto, titulo, tipo, pregunta)

    if analisis:
        return f"{chart_path}\n\n{tabla_md}\n\n---\n\n{analisis}"
    return f"{chart_path}\n\n{tabla_md}"


# ── Motor principal: Ollama interpreta la petición y genera SQL dinámico ──────
def _grafico_ollama_inteligente(pregunta: str, tipo_forzado: str | None) -> str | None:
    """
    Motor de gráficos en DOS PASOS (Solución 2):

    Paso 1 — Ollama extrae SOLO la intención (dimensión + métrica + tipo).
             NO genera SQL libre. Solo un JSON de intención.
    Paso 2 — El código construye SQL certificado desde METRICAS_GOLD y DIMENSIONES_GOLD.
             Garantiza que las fórmulas coincidan exactamente con el dashboard.

    Si Ollama falla en cualquier paso, retorna None para activar el fallback.
    """
    if not _verificar_ollama():
        return None

    tipo_instruccion = ""
    if tipo_forzado:
        nombres = {"pie": "pie/torta", "bar": "barras", "line": "línea temporal"}
        tipo_instruccion = (
            f"\nTIPO OBLIGATORIO: '{tipo_forzado}' ({nombres.get(tipo_forzado, tipo_forzado)}). "
            f"El campo 'tipo' en tu respuesta DEBE ser '{tipo_forzado}'.\n"
        )

    dims_disponibles  = list(DIMENSIONES_GOLD.keys())
    metrics_disponibles = list(METRICAS_GOLD.keys())

    system = f"""Eres un experto en análisis de datos fintech. Tu tarea es SOLO extraer la intención del usuario — NO generes SQL.
{tipo_instruccion}
DIMENSIONES (qué agrupar): {dims_disponibles}
MÉTRICAS (qué medir): {metrics_disponibles}

REGLAS CRÍTICAS DE SEMÁNTICA:
- "ticket", "revenue", "monto", "gasto" → siempre "revenue_usuario"
  (NO "ticket_transaccion" — esa es el avg por transacción individual, ~4x menor)
- "ticket promedio por transacción" → "ticket_transaccion"
- "usuarios", "clientes", "cantidad" → "usuarios"
- "fallo", "error", "rechazo" → "tasa_fallo"
- "inactivo", "churn", "dormido" → "inactivos_30d"
- "balance", "saldo" → "balance_promedio"
- "volumen" con M/millones → "volumen_total"

TIPOS:
- pie/torta → distribuciones, máx 8 categorías
- bar/barras → comparaciones y rankings
- line/línea → solo para datos temporales (diarios)

Responde ÚNICAMENTE con JSON (sin texto extra, sin markdown):
{{"dimension": "merchant|ciudad|segmento|canal|dispositivo|categoria",
  "metrica": "revenue_usuario|usuarios|tasa_fallo|balance_promedio|...",
  "tipo": "bar|pie|line",
  "top_n": 8}}"""

    try:
        resp = requests.post(
            f"{OLLAMA_BASE_URL}/api/chat",
            json={
                "model": OLLAMA_MODEL,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user",   "content": f"Petición del usuario: {pregunta}"},
                ],
                "stream": False,
                "options": {"num_ctx": 2048, "temperature": 0.0},
            },
            timeout=20,
        )
        resp.raise_for_status()
        raw = resp.json().get("message", {}).get("content", "").strip()

        # Parsear JSON de intención
        call = _extraer_tool_call(raw)
        if call is None:
            clean = re.sub(r"^```(?:json)?", "", raw, flags=re.IGNORECASE).strip()
            clean = re.sub(r"```$", "", clean).strip()
            m = re.search(r"\{[^{}]+\}", clean, re.DOTALL)
            if m:
                clean = m.group(0)
            call = json.loads(clean)

        dim_key     = call.get("dimension", "").strip()
        metrica_key = call.get("metrica", "").strip()
        tipo        = tipo_forzado or call.get("tipo", "bar").lower()
        top_n       = int(call.get("top_n", 8 if tipo == "pie" else 10))

        # Paso 2: construir SQL certificado desde los diccionarios
        resultado_sql = construir_sql_grafico(dim_key, metrica_key, tipo, top_n)

        if resultado_sql is None:
            # Ollama devolvió claves inválidas — intentar NLP local como respaldo
            print(f"  [OllamaIntent] dim='{dim_key}' metrica='{metrica_key}' no válidos, probando NLP local")
            dim_key2, metrica_key2 = extraer_intencion_grafico(_normalizar_texto(pregunta))
            if dim_key2 and metrica_key2:
                resultado_sql = construir_sql_grafico(dim_key2, metrica_key2, tipo, top_n)

        if resultado_sql is None:
            return None

        sql_certif, titulo = resultado_sql

        # Seguridad y ejecución
        sql_seguro, _ = procesar_sql(sql_certif, max_rows=50)
        conn = _get_conn_duckdb()
        df = conn.execute(sql_seguro).fetchdf()

        if df.empty:
            return "⚠️ La consulta no retornó datos para este gráfico."

        return _ejecutar_grafico_con_analisis(df, titulo, tipo, pregunta)

    except Exception as e:
        print(f"  [OllamaInteligente] {e}")
        return None


# ── Keyword routing (fallback cuando Ollama no disponible) ────────────────────
def _grafico_keyword_routing(pregunta: str, p: str = "", tipo_forzado: str | None = None) -> str:
    """Routing por keywords — usado cuando Ollama no está disponible."""
    if not p:
        p = _normalizar_texto(pregunta)
    es_torta      = tipo_forzado == "pie"  or any(k in p for k in ("torta", "pie", "distribucion", "distribución", "participacion"))
    es_tendencia  = tipo_forzado == "line" or any(k in p for k in ("tendencia", "diaria", "tiempo", "linea", "línea", "historico", "evolucion"))
    tiene_revenue = any(k in p for k in ("revenue", "ingreso", "ingresos", "monto", "montos", "amount", "cop", "volumen"))

    # ── Segmentos ─────────────────────────────────────────────────────────────
    if any(k in p for k in ("segmento", "segmentos")):
        if es_torta:
            return grafico_segmentos(
                "SELECT user_segment, COUNT(*) as usuarios FROM gold_user_360 GROUP BY user_segment",
                "Distribución de Usuarios por Segmento",
            )
        if tiene_revenue:
            return grafico_barras(
                "SELECT user_segment, ROUND(SUM(total_amount_cop)/COUNT(*), 0) as revenue_por_usuario, "
                "ROUND(AVG(avg_ticket), 0) as ticket_promedio "
                "FROM gold_user_360 GROUP BY user_segment ORDER BY revenue_por_usuario DESC",
                "Revenue y Ticket por Segmento",
            )
        return grafico_barras(
            "SELECT user_segment, COUNT(*) as usuarios, ROUND(AVG(failure_rate)*100,1) as tasa_fallo "
            "FROM gold_user_360 GROUP BY user_segment ORDER BY usuarios DESC",
            "Usuarios y Tasa de Fallo por Segmento",
        )

    # ── Ciudades ──────────────────────────────────────────────────────────────
    elif any(k in p for k in ("ciudad", "ciudades")):
        if es_torta:
            return grafico_segmentos(
                "SELECT city, COUNT(*) as usuarios FROM gold_user_360 GROUP BY city ORDER BY usuarios DESC",
                "Distribución de Usuarios por Ciudad",
            )
        if tiene_revenue:
            return grafico_barras(
                "SELECT city, ROUND(SUM(total_amount_cop)/1e6, 2) as volumen_M_cop "
                "FROM gold_user_360 GROUP BY city ORDER BY volumen_M_cop DESC",
                "Volumen de Transacciones por Ciudad (M COP)",
            )
        if any(k in p for k in ("fallo", "fallos", "error")):
            return grafico_barras(
                "SELECT city, ROUND(AVG(failure_rate)*100, 1) as tasa_fallo "
                "FROM gold_user_360 GROUP BY city ORDER BY tasa_fallo DESC",
                "Tasa de Fallo por Ciudad",
            )
        return grafico_barras(
            "SELECT city, COUNT(*) as usuarios FROM gold_user_360 GROUP BY city ORDER BY usuarios DESC",
            "Usuarios por Ciudad",
        )

    # ── Merchants / Comercios ─────────────────────────────────────────────────
    elif any(k in p for k in ("merchant", "merchants", "comercio", "comercios", "tienda", "tiendas")):
        tiene_ticket = any(k in p for k in ("ticket", "promedio", "avg"))
        if es_torta:
            if tiene_revenue or tiene_ticket:
                return grafico_segmentos(
                    "SELECT top_merchant, ROUND(AVG(avg_ticket), 0) as ticket_promedio "
                    "FROM gold_user_360 WHERE top_merchant IS NOT NULL "
                    "GROUP BY top_merchant ORDER BY ticket_promedio DESC LIMIT 8",
                    "Distribución de Merchants por Ticket Promedio",
                )
            return grafico_segmentos(
                "SELECT top_merchant, COUNT(*) as usuarios FROM gold_user_360 "
                "WHERE top_merchant IS NOT NULL GROUP BY top_merchant ORDER BY usuarios DESC LIMIT 8",
                "Distribución de Usuarios por Merchant",
            )
        if tiene_revenue or tiene_ticket:
            return grafico_barras(
                "SELECT top_merchant, ROUND(SUM(total_amount_cop)/COUNT(*), 0) as revenue_por_usuario, "
                "ROUND(AVG(avg_ticket), 0) as ticket_promedio "
                "FROM gold_user_360 WHERE top_merchant IS NOT NULL "
                "GROUP BY top_merchant ORDER BY revenue_por_usuario DESC LIMIT 10",
                "Revenue y Ticket Promedio por Merchant (Top 10)",
            )
        return grafico_barras(
            "SELECT top_merchant, COUNT(*) as usuarios FROM gold_user_360 "
            "WHERE top_merchant IS NOT NULL GROUP BY top_merchant ORDER BY usuarios DESC LIMIT 10",
            "Top Merchants por Usuarios",
        )

    # ── Categorías ────────────────────────────────────────────────────────────
    elif any(k in p for k in ("categoria", "categorias", "categoría", "categorías", "categoria de compra")):
        if es_torta:
            return grafico_segmentos(
                "SELECT top_category, COUNT(*) as usuarios FROM gold_user_360 "
                "WHERE top_category IS NOT NULL GROUP BY top_category ORDER BY usuarios DESC",
                "Distribución por Categoría de Compra",
            )
        return grafico_barras(
            "SELECT top_category, COUNT(*) as usuarios, "
            "ROUND(AVG(avg_ticket), 0) as ticket_promedio "
            "FROM gold_user_360 WHERE top_category IS NOT NULL "
            "GROUP BY top_category ORDER BY usuarios DESC LIMIT 10",
            "Usuarios y Ticket Promedio por Categoría",
        )

    # ── Canales ───────────────────────────────────────────────────────────────
    elif any(k in p for k in ("canal", "canales", "channel")):
        if es_torta:
            return grafico_segmentos(
                "SELECT preferred_channel, COUNT(*) as usuarios FROM gold_user_360 "
                "WHERE preferred_channel IS NOT NULL GROUP BY preferred_channel",
                "Distribución por Canal Preferido",
            )
        return grafico_barras(
            "SELECT preferred_channel, COUNT(*) as usuarios, "
            "ROUND(AVG(failure_rate)*100, 1) as tasa_fallo "
            "FROM gold_user_360 WHERE preferred_channel IS NOT NULL "
            "GROUP BY preferred_channel ORDER BY usuarios DESC",
            "Adopción y Tasa de Fallo por Canal",
        )

    # ── Dispositivos ──────────────────────────────────────────────────────────
    elif any(k in p for k in ("dispositivo", "dispositivos", "device", "movil", "móvil", "web", "app")):
        if es_torta:
            return grafico_segmentos(
                "SELECT preferred_device, COUNT(*) as usuarios FROM gold_user_360 "
                "WHERE preferred_device IS NOT NULL GROUP BY preferred_device",
                "Distribución por Dispositivo Preferido",
            )
        return grafico_barras(
            "SELECT preferred_device, COUNT(*) as usuarios, "
            "ROUND(AVG(avg_ticket), 0) as ticket_promedio "
            "FROM gold_user_360 WHERE preferred_device IS NOT NULL "
            "GROUP BY preferred_device ORDER BY usuarios DESC",
            "Usuarios y Ticket por Dispositivo",
        )

    # ── Ticket / Monto / Revenue ───────────────────────────────────────────────
    elif any(k in p for k in ("ticket", "monto", "montos", "revenue", "ingreso", "ingresos")):
        if es_torta:
            return grafico_segmentos(
                "SELECT user_segment, ROUND(SUM(total_amount_cop)/COUNT(*), 0) as revenue_por_usuario "
                "FROM gold_user_360 GROUP BY user_segment ORDER BY revenue_por_usuario DESC",
                "Distribución de Revenue por Segmento",
            )
        return grafico_barras(
            "SELECT user_segment, ROUND(AVG(avg_ticket), 0) as ticket_promedio, "
            "ROUND(SUM(total_amount_cop)/COUNT(*), 0) as revenue_por_usuario "
            "FROM gold_user_360 GROUP BY user_segment ORDER BY revenue_por_usuario DESC",
            "Ticket Promedio y Revenue por Segmento",
        )

    # ── Balance / Saldo ────────────────────────────────────────────────────────
    elif any(k in p for k in ("balance", "saldo", "saldos")):
        if es_torta:
            return grafico_segmentos(
                "SELECT user_segment, ROUND(AVG(balance_current), 0) as balance_promedio "
                "FROM gold_user_360 GROUP BY user_segment ORDER BY balance_promedio DESC",
                "Distribución de Balance por Segmento",
            )
        return grafico_barras(
            "SELECT user_segment, ROUND(AVG(balance_current), 0) as balance_promedio, "
            "ROUND(MIN(balance_current), 0) as balance_minimo "
            "FROM gold_user_360 GROUP BY user_segment ORDER BY balance_promedio DESC",
            "Balance Promedio por Segmento",
        )

    # ── Eventos ────────────────────────────────────────────────────────────────
    elif any(k in p for k in ("evento", "eventos", "event", "tipo de evento")):
        if es_torta:
            return grafico_segmentos(
                "SELECT event, count FROM gold_event_summary ORDER BY count DESC",
                "Distribución de Tipos de Evento",
            )
        return grafico_barras(
            "SELECT event, count, success_count, failed_count FROM gold_event_summary ORDER BY count DESC",
            "Volumen por Tipo de Evento",
        )

    # ── Inactivos / Churn / Retención ──────────────────────────────────────────
    elif any(k in p for k in ("inactivo", "inactivos", "churn", "dormido", "retencion", "retención", "abandono")):
        if es_torta:
            return grafico_segmentos(
                "SELECT user_segment, "
                "COUNT(CASE WHEN days_since_last_tx > 30 THEN 1 END) as inactivos_30d "
                "FROM gold_user_360 GROUP BY user_segment ORDER BY inactivos_30d DESC",
                "Distribución de Usuarios Inactivos (30d) por Segmento",
            )
        return grafico_barras(
            "SELECT user_segment, "
            "COUNT(CASE WHEN days_since_last_tx > 30 THEN 1 END) as inactivos_30d, "
            "COUNT(CASE WHEN days_since_last_tx > 60 THEN 1 END) as inactivos_60d, "
            "COUNT(*) as total_usuarios "
            "FROM gold_user_360 GROUP BY user_segment ORDER BY inactivos_30d DESC",
            "Usuarios Inactivos por Segmento",
        )

    # ── Fallos / Errores ───────────────────────────────────────────────────────
    elif any(k in p for k in ("fallo", "fallos", "error", "errores", "fallida", "fallidas", "fracaso")):
        if es_torta:
            return grafico_segmentos(
                "SELECT user_segment, ROUND(AVG(failure_rate)*100,1) as tasa_fallo "
                "FROM gold_user_360 GROUP BY user_segment",
                "Tasa de Fallo por Segmento",
            )
        return grafico_barras(
            "SELECT user_segment, ROUND(AVG(failure_rate)*100,1) as tasa_fallo, "
            "SUM(failed_transactions) as transacciones_fallidas "
            "FROM gold_user_360 GROUP BY user_segment ORDER BY tasa_fallo DESC",
            "Fallos de Transacción por Segmento",
        )

    # ── Tendencia / Métricas Diarias ───────────────────────────────────────────
    elif es_tendencia:
        if tiene_revenue:
            return grafico_tendencia_diaria(
                "SELECT date, ROUND(total_amount_cop/1e6, 2) as volumen_M_cop "
                "FROM gold_daily_metrics ORDER BY date",
                "Tendencia Diaria de Volumen (M COP)",
            )
        if any(k in p for k in ("usuario", "usuarios", "unique", "activo")):
            return grafico_tendencia_diaria(
                "SELECT date, unique_users FROM gold_daily_metrics ORDER BY date",
                "Tendencia Diaria de Usuarios Únicos",
            )
        if any(k in p for k in ("fallo", "fallos", "error")):
            return grafico_tendencia_diaria(
                "SELECT date, failed_count FROM gold_daily_metrics ORDER BY date",
                "Tendencia Diaria de Fallos",
            )
        return grafico_tendencia_diaria(
            "SELECT date, total_transactions FROM gold_daily_metrics ORDER BY date",
            "Tendencia Diaria de Transacciones",
        )

    # ── Fallback final dentro del keyword routing ─────────────────────────────
    else:
        return _grafico_ollama_fallback(pregunta)


# ── Orquestador principal de gráficos ─────────────────────────────────────────
def _manejar_peticion_grafico(pregunta: str) -> str:
    """
    Genera gráficos con datos exactos de la capa Gold.

    Flujo de 3 capas (Soluciones 3 → 2 → 4/fallback):

    Capa 1 — NLP local certificado (Solución 3, sin Ollama, instantáneo):
      Extrae dimensión+métrica desde keywords → construye SQL certificado
      desde METRICAS_GOLD y DIMENSIONES_GOLD → garantiza fórmulas idénticas
      al dashboard. Cubre el 90% de los casos comunes.

    Capa 2 — Ollama extrae intención → SQL certificado (Solución 2):
      Ollama solo interpreta el lenguaje natural y devuelve JSON de intención.
      El código construye el SQL desde diccionarios certificados.
      Nunca deja a Ollama generar SQL libre.

    Capa 3 — Keyword routing (fallback sin Ollama):
      Para cuando Ollama no está disponible, usa routing por palabras clave.
    """
    p = _normalizar_texto(pregunta)
    tipo_forzado = _detectar_tipo_grafico(p)

    # ── Capa 1: NLP local certificado (sin Ollama, instantáneo) ─────────────
    dim_key, metrica_key = extraer_intencion_grafico(p)
    if dim_key and metrica_key:
        tipo  = tipo_forzado or "bar"
        top_n = 8 if tipo == "pie" else 10
        resultado_sql = construir_sql_grafico(dim_key, metrica_key, tipo, top_n)
        if resultado_sql:
            sql_certif, titulo = resultado_sql
            try:
                sql_seguro, _ = procesar_sql(sql_certif, max_rows=50)
                conn = _get_conn_duckdb()
                df = conn.execute(sql_seguro).fetchdf()
                if not df.empty:
                    return _ejecutar_grafico_con_analisis(df, titulo, tipo, pregunta)
            except Exception as e:
                print(f"  [ChartCertif] NLP local falló: {e}")

    # ── Capa 2: Ollama extrae intención → SQL certificado + análisis ─────────
    resultado = _grafico_ollama_inteligente(pregunta, tipo_forzado)
    if resultado:
        return resultado

    # ── Capa 3: keyword routing (sin Ollama) + análisis post-hoc ─────────────
    resultado_capa3 = _grafico_keyword_routing(pregunta, p, tipo_forzado)
    if "✅ Gráfico guardado:" in resultado_capa3:
        # Extraer los datos del string y añadir análisis de Ollama
        m = re.search(r'Datos:\n(.+)', resultado_capa3, re.DOTALL)
        datos_texto = m.group(1).strip() if m else ""
        titulo_capa3 = re.search(r'guardado:.+?([^/\\]+)_\d{8}', resultado_capa3)
        titulo_str = titulo_capa3.group(1).replace("_", " ").title() if titulo_capa3 else "Análisis Gold"
        analisis = _analizar_grafico_con_ollama(datos_texto, titulo_str, tipo_forzado or "bar", pregunta)
        if analisis:
            # Reemplazar "Datos:" raw con bloque código + añadir análisis
            if m:
                resultado_capa3 = resultado_capa3.replace(
                    f"Datos:\n{m.group(1)}",
                    f"**Datos Gold** _(fuente certificada)_\n```\n{datos_texto}\n```"
                )
            return f"{resultado_capa3}\n\n---\n\n{analisis}"
    return resultado_capa3


def agent_query(pregunta: str) -> str:
    """Punto de entrada principal para consultas al agente."""
    _agregar_historial("user", pregunta)

    # ── Gráficos ──────────────────────────────────────────────────────────────
    if _es_peticion_de_grafico(pregunta):
        _get_conn_duckdb()
        resultado = _manejar_peticion_grafico(pregunta)
        _agregar_historial("assistant", resultado)
        return resultado

    # ── Preguntas de seguimiento anafóricas ───────────────────────────────────
    if _es_pregunta_de_seguimiento(pregunta):
        resultado = _resolver_seguimiento_con_ollama(pregunta)
        _agregar_historial("assistant", resultado)
        return resultado

    # ── Datos Gold (determinístico) ───────────────────────────────────────────
    respuesta_datos = _respuesta_datos_confiable(pregunta)
    if respuesta_datos is not None:
        _agregar_historial("assistant", respuesta_datos)
        return respuesta_datos

    # ── Agente Strands (preguntas libres) ─────────────────────────────────────
    global _agent_instance
    if _agent_instance is None:
        _agent_instance = crear_agente()
    respuesta = _agent_instance(pregunta)
    if hasattr(respuesta, "message"):
        contenido = respuesta.message.get("content", [{}])
        if contenido and isinstance(contenido[0], dict):
            texto = contenido[0].get("text", str(respuesta))
            _agregar_historial("assistant", texto)
            return texto
    texto = str(respuesta)
    _agregar_historial("assistant", texto)
    return texto


def stream_agent_query(pregunta: str):
    """
    Versión streaming de agent_query — genera chunks de texto progresivamente.

    Flujo:
      1. Gráficos       → yield resultado completo (string con ruta PNG)
      2. Seguimientos   → yield respuesta Ollama con historial de contexto
      3. Preguntas Gold → yield datos inmediatamente + stream análisis Ollama
      4. Preguntas libres → yield respuesta completa del agente
    """
    _agregar_historial("user", pregunta)

    # ── Gráficos: resultado directo, sin streaming ────────────────────────────
    if _es_peticion_de_grafico(pregunta):
        _get_conn_duckdb()
        resultado = _manejar_peticion_grafico(pregunta)
        _agregar_historial("assistant", resultado)
        yield resultado
        return

    # ── Preguntas de seguimiento anafóricas ───────────────────────────────────
    if _es_pregunta_de_seguimiento(pregunta):
        resultado = _resolver_seguimiento_con_ollama(pregunta)
        _agregar_historial("assistant", resultado)
        yield resultado
        return

    # ── Obtener datos reales de Gold (determinístico, sin alucinación) ────────
    datos_texto = None
    p = _normalizar_texto(pregunta)

    if _es_pregunta_de_datos(pregunta):
        if any(k in p for k in ("resumen", "ejecutivo", "kpi", "indicador", "indicadores")):
            datos_texto = resumen_ejecutivo()
        else:
            intent = _sql_por_intencion(pregunta)
            if intent:
                sql, titulo = intent
                # Gráfico + tabla + análisis Ollama 3 partes (entrega completa)
                _get_conn_duckdb()
                resultado_grafico = _respuesta_con_grafico(titulo, sql, pregunta)
                _agregar_historial("assistant", resultado_grafico)
                yield resultado_grafico
                return

    # ── Sin datos reconocibles → agent_query completo ─────────────────────────
    if datos_texto is None:
        resultado = agent_query(pregunta)
        yield resultado
        return

    # ── Mostrar datos inmediatamente (el usuario ve algo al instante) ─────────
    cabecera = f"**Datos Gold**\n```text\n{datos_texto}\n```\n\n**Análisis** _(Ollama · {OLLAMA_MODEL})_\n\n"
    yield cabecera

    # ── Stream de interpretación Ollama ───────────────────────────────────────
    if not _verificar_ollama():
        msg = "_Ollama no disponible. Los datos de arriba son los reales de la capa Gold._"
        _agregar_historial("assistant", cabecera + msg)
        yield msg
        return

    prompt_usuario = (
        f"Pregunta del usuario: {pregunta}\n\n"
        f"Datos reales de la capa Gold:\n{datos_texto}\n\n"
        "Analiza TODOS los valores de la tabla con profundidad. "
        "Identifica el mejor y el peor performer en cada métrica. "
        "Calcula brechas porcentuales entre extremos. "
        "Señala cualquier patrón o correlación no obvia entre las métricas. "
        "Responde siguiendo el formato obligatorio del sistema con los 4 bloques. "
        "No añadas ningún número que no esté en los datos anteriores."
    )
    try:
        mensajes_stream = [{"role": "system", "content": _SYSTEM_INTERPRETACION}]
        for m in _conversation_history[-4:]:
            mensajes_stream.append({"role": m["role"], "content": m["content"][:600]})
        mensajes_stream.append({"role": "user", "content": prompt_usuario})
        resp = requests.post(
            f"{OLLAMA_BASE_URL}/api/chat",
            json={
                "model": OLLAMA_MODEL,
                "messages": mensajes_stream,
                "stream": True,
                "options": {"num_ctx": 4096},
            },
            stream=True,
            timeout=300,
        )
        resp.raise_for_status()
        tokens_acumulados = []
        for linea in resp.iter_lines():
            if linea:
                try:
                    chunk = json.loads(linea)
                    token = chunk.get("message", {}).get("content", "")
                    if token:
                        tokens_acumulados.append(token)
                        yield token
                except json.JSONDecodeError:
                    continue
        _agregar_historial("assistant", cabecera + "".join(tokens_acumulados))
    except Exception as e:
        yield f"\n\n_Error al conectar con Ollama: {e}_"


def reset_agent():
    """Reinicia el agente y limpia el historial de conversación."""
    global _agent_instance
    _agent_instance = None
    _conversation_history.clear()


# ── Modo interactivo ─────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("=" * 60)
    print("  AGENTE FINTECH — Modo Interactivo")
    print(f"  LLM: Ollama {OLLAMA_MODEL} @ {OLLAMA_BASE_URL}")
    print("=" * 60)
    agente = crear_agente()
    print("\nEscribe 'salir' para terminar.\n")
    while True:
        try:
            pregunta = input("Tú: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nHasta luego.")
            break
        if pregunta.lower() in ("salir", "exit", "quit"):
            print("Hasta luego.")
            break
        if not pregunta:
            continue
        respuesta = agente(pregunta)
        texto = (respuesta.message.get("content", [{}])[0].get("text", str(respuesta))
                 if hasattr(respuesta, "message") else str(respuesta))
        print(f"\nAgente: {texto}\n")
