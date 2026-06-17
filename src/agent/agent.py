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
import tempfile
import unicodedata
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


def _slug_archivo_seguro(texto: str, fallback: str = "chart") -> str:
    normalizado = unicodedata.normalize("NFKD", texto or "")
    ascii_texto = "".join(ch for ch in normalizado if not unicodedata.combining(ch))
    slug = re.sub(r"[^a-zA-Z0-9._-]+", "_", ascii_texto.lower()).strip("._-")
    return slug or fallback


def _save_chart(titulo: str) -> str:
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    nombre = _slug_archivo_seguro(titulo)[:48]
    ruta = _get_charts_dir() / f"{nombre}_{ts}.png"
    plt.tight_layout()
    try:
        plt.savefig(ruta, dpi=150, bbox_inches="tight")
    except PermissionError:
        fallback_dir = Path(tempfile.gettempdir()) / "fintech_pipeline_charts"
        fallback_dir.mkdir(parents=True, exist_ok=True)
        ruta = fallback_dir / f"{nombre}_{ts}.png"
        plt.savefig(ruta, dpi=150, bbox_inches="tight")
    finally:
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
_last_response_context: dict | None = None
_gold_profile_cache: dict | None = None
_TRACE_PATH = Path(__file__).resolve().parents[2] / "logs" / "agent_traces.jsonl"


def _agregar_historial(rol: str, texto: str) -> None:
    global _conversation_history, _last_response_context
    _conversation_history.append({"role": rol, "content": texto[:2000]})
    if rol == "assistant" and _last_response_context is not None:
        _last_response_context["ultima_respuesta"] = texto[:4000]
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


_FOLLOWUP_CONTEXT_CUES = (
    "esta grafica", "esta gráfica", "la grafica", "la gráfica", "este grafico", "este gráfico",
    "estos datos", "estos valores", "ese dato", "esa respuesta", "respuesta anterior",
    "lo anterior", "anterior", "a partir de eso", "a partir de esa", "a partir de esas",
    "con base en eso", "con base en lo anterior", "sobre eso", "sobre la recomendacion",
    "sobre la recomendación", "esa recomendacion", "esa recomendación",
    "esas recomendaciones", "la recomendacion", "la recomendación", "las recomendaciones",
    "valor mas bajo", "valor más bajo", "valor menor",
    "valor minimo", "valor mínimo", "valor maximo", "valor máximo", "el minimo",
    "el mínimo", "el maximo", "el máximo", "te equivocas", "te equivocaste",
    "estas equivocado", "estás equivocado", "se equivoco", "se equivocó",
    "corrige", "corregir", "mira que", "desde que tiempo", "desde qué tiempo",
    "desde que fecha", "desde qué fecha", "desde que periodo", "desde qué periodo",
    "de donde sale", "de dónde sale", "como estimaste", "cómo estimaste",
    "como calculaste", "cómo calculaste", "que significa", "qué significa",
    "no me quedo claro", "no me quedó claro", "explica mejor", "profundiza",
    "desarrolla", "aterriza", "detalla", "mas especific", "más especific",
    "ideas", "acciones concretas", "bien ejecutadas", "implementadas",
)

_FOLLOWUP_ELABORATION_CUES = (
    "dame 3", "dame tres", "3 ideas", "tres ideas", "3 recomendaciones",
    "tres recomendaciones", "recomendaciones especificas", "recomendaciones específicas",
    "ideas especificas", "ideas específicas", "acciones especificas", "acciones específicas",
    "implementadas", "bien ejecutadas", "paso a paso", "plan de accion", "plan de acción",
    "como lo harias", "cómo lo harías", "como hacerlo", "cómo hacerlo",
)

_CONTEXTUAL_FOLLOWUP_ACTION_CUES = (
    "por que", "porque", "como", "cual", "que significa", "explica",
    "desarrolla", "profundiza", "aterriza", "detalla", "amplia", "aclara",
    "hazlo", "resumelo", "convierte", "transforma", "compara", "prioriza",
    "ordena", "simula", "disena", "redacta", "escribe",
    "dame", "genera", "crea", "haz", "propone", "propon", "sugiere",
    "plan", "pasos", "cronograma", "mensaje", "copy", "guion",
    "campana", "ideas", "alternativas", "opciones",
    "recomendacion", "riesgos", "supuestos",
    "ejemplo", "ejemplos", "publico", "presupuesto",
    "kpi", "metricas de exito", "indicadores",
    "la primera", "la segunda", "la tercera", "primera", "segunda", "tercera",
    "la 1", "la 2", "la 3", "uno", "dos", "tres",
)

_CONTEXTUAL_FOLLOWUP_REFERENCES = (
    "eso", "esto", "esa", "ese", "esas", "esos", "lo anterior",
    "respuesta anterior", "recomendacion anterior",
    "recomendaciones", "lo que dijiste", "lo que recomendaste",
    "a partir", "con base", "sobre", "dicha", "dicho", "la idea",
)

_NEW_TOPIC_CUES = (
    "nuevo tema", "otra pregunta", "cambiando de tema", "cambiemos de tema",
    "ahora quiero analizar", "ahora analiza", "analicemos ahora",
    "muestrame", "muéstrame", "genera", "generame", "genérame",
    "crea", "haz", "quiero ver", "quisiera ver",
)

_SATISFACTION_CUES = (
    "gracias", "muchas gracias", "perfecto", "excelente", "quedo claro",
    "quedó claro", "listo", "super", "súper", "me sirve", "entendido",
)

_AMBIGUOUS_QUESTIONS = {
    "como vamos", "cómo vamos", "como va", "cómo va", "que paso", "qué pasó",
    "que ves", "qué ves", "esta bien", "está bien", "lo ves bien",
    "dame los mejores", "los mejores", "los peores", "analiza esto",
    "mirame esto", "mírame esto", "grafica eso", "gráfica eso",
}

_PII_REQUEST_TERMS = (
    "email", "correo", "correos", "nombre", "nombres", "telefono", "teléfono",
    "cedula", "cédula", "documento", "direccion", "dirección", "datos personales",
    "informacion personal", "información personal",
)

_UNAVAILABLE_GOLD_TERMS: dict[str, str] = {
    "genero": "género",
    "género": "género",
    "edad": "edad",
    "fraude": "fraude",
    "fraudes": "fraude",
    "score crediticio": "score crediticio",
    "utilidad": "utilidad o margen",
    "margen": "utilidad o margen",
    "costo": "costos",
    "costos": "costos",
    "campana real": "campañas reales ejecutadas",
    "campaña real": "campañas reales ejecutadas",
}

_PREDICTION_TERMS = (
    "predice", "predecir", "pronostica", "pronostico", "pronóstico",
    "proximo mes", "próximo mes", "seguro", "con certeza", "exactamente",
)

_CAUSAL_TERMS = (
    "causa real", "causa exacta", "por que exactamente", "por qué exactamente",
    "demuestra que", "garantiza que",
)


def _respuesta_aclaracion(motivo: str, opciones: list[str]) -> str:
    opciones_txt = "\n".join(f"- {opcion}" for opcion in opciones)
    return (
        f"**Necesito una aclaración antes de consultar Gold.**\n\n"
        f"{motivo}\n\n"
        f"Puedes precisarlo así:\n{opciones_txt}"
    )


def _respuesta_no_disponible(variable: str) -> str:
    return (
        "**No puedo responder eso con evidencia de la capa Gold actual.**\n\n"
        f"La variable `{variable}` no está disponible en el esquema Gold perfilado. "
        "Puedo analizar métricas disponibles como usuarios, volumen, ticket/revenue, "
        "fallos, segmentos, ciudades, canales, dispositivos, comercios, categorías, "
        "eventos, inactividad y tendencias diarias."
    )


def _respuesta_pii_bloqueada() -> str:
    return (
        "**No puedo mostrar datos personales o sensibles.**\n\n"
        "La capa de control bloquea PII como nombres, correos, teléfonos o documentos. "
        "Sí puedo responder con métricas agregadas por segmento, ciudad, canal, comercio "
        "u otras dimensiones no sensibles."
    )


def _respuesta_prediccion_limitada() -> str:
    return (
        "**No puedo dar una predicción exacta con certeza usando solo la capa Gold.**\n\n"
        "Lo que sí puedo hacer es analizar tendencia histórica, comparar periodos y señalar "
        "escenarios probables como hipótesis. Para una predicción real se necesitaría un modelo "
        "predictivo validado, variables de entrenamiento y medición de error."
    )


def _respuesta_causalidad_limitada() -> str:
    return (
        "**No puedo afirmar una causa definitiva solo con datos descriptivos de Gold.**\n\n"
        "Puedo revisar correlaciones, cambios de comportamiento y señales que soporten hipótesis, "
        "pero no presentar causalidad como verdad absoluta sin experimento, variable causal o "
        "evidencia adicional."
    )


def _evaluar_control_preconsulta(pregunta: str) -> tuple[str | None, str]:
    p = _normalizar_texto(pregunta)
    if not p:
        return _respuesta_aclaracion(
            "La pregunta está vacía.",
            ["Escribe una métrica: `volumen por ciudad`.", "O pide un resumen: `dame el resumen ejecutivo`."],
        ), "aclaracion_vacia"

    if any(term in p for term in _PII_REQUEST_TERMS):
        return _respuesta_pii_bloqueada(), "bloqueo_pii"

    for termino, variable in _UNAVAILABLE_GOLD_TERMS.items():
        if termino in p:
            perfil = _perfil_gold_schema()
            columnas = " ".join(perfil.get("columnas_disponibles", [])).lower()
            if termino not in columnas:
                return _respuesta_no_disponible(variable), "variable_no_disponible"

    if any(term in p for term in _CAUSAL_TERMS):
        return _respuesta_causalidad_limitada(), "causalidad_no_soportada"

    if any(term in p for term in _PREDICTION_TERMS):
        return _respuesta_prediccion_limitada(), "prediccion_no_soportada"

    if p in _AMBIGUOUS_QUESTIONS and not _last_response_context:
        return _respuesta_aclaracion(
            "Tu pregunta es válida, pero no indica métrica, periodo ni dimensión.",
            [
                "`dame el resumen ejecutivo de la plataforma`",
                "`compara volumen por ciudad`",
                "`muestra fallos por segmento en una gráfica`",
                "`qué canal tiene mayor tasa de fallo`",
            ],
        ), "aclaracion_ambigua"

    if any(cue in p for cue in ("eso", "esto", "esta grafica", "esta gráfica", "lo anterior")) and not _last_response_context:
        return _respuesta_aclaracion(
            "La pregunta hace referencia a un contexto anterior, pero no hay una respuesta previa activa.",
            [
                "`muestra volumen por segmento`",
                "`explica la tasa de fallos por ciudad`",
                "`dame el resumen ejecutivo`",
            ],
        ), "aclaracion_sin_contexto"

    return None, "ok"


def _actualizar_contexto_estructurado(contexto: dict) -> None:
    global _last_response_context
    _last_response_context = {
        **contexto,
        "timestamp": datetime.datetime.now().isoformat(timespec="seconds"),
    }


def _contexto_estructurado_actual() -> dict | None:
    return _last_response_context


def _rol_columna_gold(columna: str, dtype: str) -> list[str]:
    col = columna.lower()
    roles: list[str] = []
    if col.endswith("_id") or col in {"user_id", "event_id", "transaction_id"}:
        roles.append("identificador")
    if "date" in col or "fecha" in col or "time" in col:
        roles.append("fecha")
    if any(token in col for token in ("amount", "ticket", "balance", "rate", "count", "total", "pct")):
        roles.append("metrica")
    if any(token in col for token in ("segment", "city", "channel", "device", "merchant", "category", "event")):
        roles.append("dimension")
    if any(token in col for token in ("failed", "failure", "status", "success")):
        roles.append("estado")
    if any(token in col for token in ("email", "name", "age", "phone", "document")):
        roles.append("pii")
    if not roles and ("object" in dtype or "category" in dtype or "string" in dtype):
        roles.append("dimension")
    if not roles and any(token in dtype for token in ("int", "float", "decimal")):
        roles.append("metrica")
    return roles or ["descriptiva"]


def _perfilar_dataframe_gold(nombre_tabla: str, df: pd.DataFrame) -> dict:
    columnas: dict[str, dict] = {}
    for columna in df.columns:
        serie = df[columna]
        dtype = str(serie.dtype)
        info = {
            "tipo": dtype,
            "nulos": int(serie.isna().sum()),
            "nulos_pct": round(float(serie.isna().mean() * 100), 2) if len(serie) else 0.0,
            "cardinalidad": int(serie.nunique(dropna=True)),
            "roles": _rol_columna_gold(columna, dtype),
        }
        if pd.api.types.is_numeric_dtype(serie):
            limpia = pd.to_numeric(serie, errors="coerce").dropna()
            if not limpia.empty:
                info.update({
                    "min": float(limpia.min()),
                    "max": float(limpia.max()),
                    "promedio": float(limpia.mean()),
                })
        if "fecha" in info["roles"] or "date" in columna.lower():
            fechas = pd.to_datetime(serie, errors="coerce").dropna()
            if not fechas.empty:
                info.update({
                    "min_fecha": str(fechas.min().date()),
                    "max_fecha": str(fechas.max().date()),
                })
        columnas[columna] = info
    return {
        "tabla": nombre_tabla,
        "filas": int(len(df)),
        "columnas": columnas,
        "metricas": [c for c, meta in columnas.items() if "metrica" in meta["roles"]],
        "dimensiones": [c for c, meta in columnas.items() if "dimension" in meta["roles"]],
        "fechas": [c for c, meta in columnas.items() if "fecha" in meta["roles"]],
        "pii": [c for c, meta in columnas.items() if "pii" in meta["roles"]],
    }


def _perfil_gold_schema(force: bool = False) -> dict:
    global _gold_profile_cache
    if _gold_profile_cache is not None and not force:
        return _gold_profile_cache
    root = Path(__file__).resolve().parents[2]
    tablas = {
        "gold_user_360": root / "data/gold/gold_user_360.parquet",
        "gold_daily_metrics": root / "data/gold/gold_daily_metrics.parquet",
        "gold_event_summary": root / "data/gold/gold_event_summary.parquet",
    }
    perfil = {"tablas": {}, "columnas_disponibles": set(), "pii": set()}
    for nombre, ruta_base in tablas.items():
        try:
            ruta = resolve_latest_parquet(ruta_base)
            if not ruta.exists():
                continue
            df = pd.read_parquet(ruta)
            perfil_tabla = _perfilar_dataframe_gold(nombre, df)
            perfil["tablas"][nombre] = perfil_tabla
            perfil["columnas_disponibles"].update(perfil_tabla["columnas"].keys())
            perfil["pii"].update(perfil_tabla["pii"])
        except Exception as exc:
            perfil["tablas"][nombre] = {"tabla": nombre, "error": str(exc)}
    perfil["columnas_disponibles"] = sorted(perfil["columnas_disponibles"])
    perfil["pii"] = sorted(perfil["pii"])
    _gold_profile_cache = perfil
    return perfil


def _resumen_perfil_gold() -> str:
    perfil = _perfil_gold_schema()
    lineas = ["Esquema Gold disponible:"]
    for nombre, tabla in perfil.get("tablas", {}).items():
        if "error" in tabla:
            lineas.append(f"- {nombre}: no perfilada ({tabla['error']})")
            continue
        lineas.append(
            f"- {nombre}: {tabla['filas']} filas, "
            f"{len(tabla['columnas'])} columnas, "
            f"metricas={tabla['metricas']}, dimensiones={tabla['dimensiones']}, fechas={tabla['fechas']}"
        )
    if perfil.get("pii"):
        lineas.append(f"Columnas sensibles bloqueadas: {perfil['pii']}")
    return "\n".join(lineas)


def _registrar_traza_agente(
    pregunta: str,
    respuesta: str,
    ruta: str,
    modo_respuesta: str,
    extra: dict | None = None,
) -> None:
    try:
        _TRACE_PATH.parent.mkdir(parents=True, exist_ok=True)
        contexto = _contexto_estructurado_actual() or {}
        evento = {
            "timestamp": datetime.datetime.now().isoformat(timespec="seconds"),
            "pregunta": pregunta[:500],
            "ruta": ruta,
            "modo_respuesta": modo_respuesta,
            "respuesta_chars": len(respuesta or ""),
            "contexto_tipo": contexto.get("tipo"),
            "contexto_titulo": contexto.get("titulo"),
            "extra": extra or {},
        }
        with _TRACE_PATH.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(evento, ensure_ascii=False) + "\n")
    except Exception as exc:
        print(f"  [Trace] No se pudo registrar traza: {exc}")


def _finalizar_respuesta(
    pregunta: str,
    respuesta: str,
    ruta: str,
    modo_respuesta: str,
    extra: dict | None = None,
) -> str:
    _registrar_traza_agente(pregunta, respuesta, ruta, modo_respuesta, extra)
    return respuesta


def _es_solicitud_elaboracion_contextual(pregunta: str) -> bool:
    if not _last_response_context:
        return False
    p = _normalizar_texto(pregunta)
    pide_recomendacion = any(term in p for term in (
        "recomendacion", "recomendación", "recomendaciones", "campana", "campaña",
        "campanas", "campañas", "ideas", "acciones", "implementar", "ejecutar",
    ))
    if any(cue in p for cue in _FOLLOWUP_ELABORATION_CUES) and pide_recomendacion:
        return True
    referencia_previa = any(term in p for term in (
        "esa", "esas", "eso", "anterior", "recomendacion", "recomendación",
        "recomendaciones", "a partir", "con base", "sobre",
    ))
    pide_formato = any(term in p for term in (
        "dame", "genera", "crea", "haz", "detalla", "desarrolla", "aterriza",
        "especific", "implementad", "ejecutad",
    ))
    return pide_recomendacion and referencia_previa and pide_formato


def _es_inicio_tema_nuevo(pregunta: str) -> bool:
    return _es_consulta_nueva_datos_explicita(pregunta)


def _tiene_referencia_al_contexto(pregunta: str) -> bool:
    if not _last_response_context:
        return False
    p = _normalizar_texto(pregunta)
    return (
        any(cue in p for cue in _FOLLOWUP_CONTEXT_CUES)
        or any(cue in p for cue in _CONTEXTUAL_FOLLOWUP_REFERENCES)
        or _es_solicitud_elaboracion_contextual(pregunta)
    )


def _es_consulta_nueva_datos_explicita(pregunta: str) -> bool:
    """Distingue una consulta nueva contra Gold de una continuacion conversacional."""
    p = _normalizar_texto(pregunta)
    if not p:
        return False
    if _es_solicitud_elaboracion_contextual(pregunta):
        return False
    if any(cue in p for cue in ("nuevo tema", "otra pregunta", "cambiando de tema", "cambiemos de tema")):
        return True

    keywords_grafico = globals().get("_KEYWORDS_GRAFICO", set())
    menciona_datos = any(anchor in p for anchor in _DATA_ANCHORS | keywords_grafico)
    if not menciona_datos:
        return False

    referencia_contextual = any(cue in p for cue in _CONTEXTUAL_FOLLOWUP_REFERENCES)
    referencia_contextual = referencia_contextual or any(cue in p for cue in _FOLLOWUP_CONTEXT_CUES)
    if referencia_contextual:
        return False

    comandos_consulta = (
        "muestrame", "muestra", "grafica", "genera una grafica", "genera un grafico",
        "crea una grafica", "crea un grafico", "visualiza", "analiza", "compara",
        "calcula", "dame el resumen", "dame resumen", "dame los datos", "quiero ver",
        "quisiera ver", "cual es", "que ciudad", "que segmento", "que canal",
        "que dispositivo", "cuales son", "ranking", "top", "lista",
        "kpi", "kpis", "indicadores", "resumen ejecutivo", "resumen",
    )
    return any(p.startswith(cue) for cue in comandos_consulta) or any(p.startswith(cue) for cue in _NEW_TOPIC_CUES)


def _es_seguimiento_contextual_generico(pregunta: str) -> bool:
    """Detecta seguimientos flexibles sin amarrarlos a una accion rapida concreta."""
    if not _last_response_context:
        return False
    if _es_consulta_nueva_datos_explicita(pregunta):
        return False

    p = _normalizar_texto(pregunta)
    if _tiene_referencia_al_contexto(pregunta):
        return True

    palabras = p.split()
    tiene_accion = any(cue in p for cue in _CONTEXTUAL_FOLLOWUP_ACTION_CUES)
    if not tiene_accion:
        conectores = (
            "y ", "pero ", "entonces ", "ok ", "vale ", "listo ",
            "ahora ", "tambien ", "ademas ",
        )
        return len(palabras) <= 10 and any(p.startswith(cue) for cue in conectores)

    keywords_grafico = globals().get("_KEYWORDS_GRAFICO", set())
    menciona_datos = any(anchor in p for anchor in _DATA_ANCHORS | keywords_grafico)
    if menciona_datos:
        conectores_contexto = ("y ", "pero ", "entonces ", "tambien ", "ademas ")
        return len(palabras) <= 12 and any(p.startswith(cue) for cue in conectores_contexto)

    return True


def _debe_resolver_como_seguimiento(pregunta: str) -> bool:
    if not _last_response_context:
        return False
    return _es_seguimiento_contextual_generico(pregunta)


def _es_cierre_satisfecho(pregunta: str) -> bool:
    p = _normalizar_texto(pregunta)
    palabras = p.split()
    if len(palabras) > 8:
        return False
    return any(cue in p for cue in _SATISFACTION_CUES)


def _respuesta_cierre_satisfecho() -> str:
    return (
        "Perfecto. Me alegra que haya quedado claro.\n\n"
        "Cuando quieras seguir, puedo profundizar sobre la última gráfica, comparar otra métrica "
        "o empezar un análisis nuevo sin perder el contexto."
    )


def _segmento_desde_metrica(meta: dict | None, fallback: str = "el grupo principal") -> str:
    if not meta:
        return fallback
    return _etiqueta_clara(str(meta.get("max_label", fallback)))


def _valor_desde_metrica(meta: dict | None, campo: str = "max_val") -> str:
    if not meta:
        return "N/D"
    return _formatear_valor_metrica(meta, campo)


def _respuesta_elaboracion_contextual_deterministica(
    pregunta: str,
    modo_respuesta: str = "claro",
) -> str:
    contexto = _contexto_estructurado_actual() or {}
    metricas = _ordenar_metricas_para_pregunta(
        _metricas_contexto(contexto),
        contexto.get("pregunta", pregunta),
        contexto.get("titulo", ""),
    )
    if not metricas:
        return (
            "Sí, te entiendo. Lo que pides ya no es repetir el análisis, sino convertirlo en acciones.\n\n"
            "Con el contexto disponible no encontré métricas suficientes para diseñar campañas específicas sin inventar. "
            "Pídeme primero una comparación por segmento, ciudad o canal y luego puedo convertirla en ideas accionables."
        )

    metrica_inactivo = _buscar_metrica(metricas, "inactivo")
    metrica_fallo = _buscar_metrica(metricas, "fallo")
    metrica_revenue = _buscar_metrica(metricas, "revenue")
    metrica_ticket = _buscar_metrica(metricas, "ticket")
    metrica_usuarios = _buscar_metrica(metricas, "usuarios")

    grupo_inactivo = _segmento_desde_metrica(metrica_inactivo or metrica_usuarios, "el grupo con mayor oportunidad")
    grupo_fallo = _segmento_desde_metrica(metrica_fallo, "el grupo con mayor fricción")
    grupo_valor = _segmento_desde_metrica(metrica_revenue or metrica_ticket, "el grupo de mayor valor")

    valor_inactivo = _valor_desde_metrica(metrica_inactivo or metrica_usuarios)
    valor_fallo = _valor_desde_metrica(metrica_fallo)
    valor_valor = _valor_desde_metrica(metrica_revenue or metrica_ticket)
    p = _normalizar_texto(pregunta)

    pide_alternativas = any(term in p for term in ("alternativa", "alternativas", "opcion", "opciones", "ideas"))
    ordinal = None
    if any(term in p for term in ("primera", "la 1", "idea 1")):
        ordinal = "reactivacion"
    elif any(term in p for term in ("segunda", "la 2", "idea 2")):
        ordinal = "friccion"
    elif any(term in p for term in ("tercera", "la 3", "idea 3")):
        ordinal = "valor"

    if pide_alternativas and ordinal:
        if ordinal == "reactivacion":
            grupo_objetivo = grupo_inactivo
            base_dato = f"mayor oportunidad de reactivacion ({valor_inactivo})"
            alternativa_1 = "beneficio de regreso por completar una operacion este mes"
            alternativa_2 = "recordatorio personalizado con una accion facil: pagar, recargar o transferir"
            kpi = "usuarios que vuelven a transaccionar y reduccion de inactivos"
        elif ordinal == "friccion":
            grupo_objetivo = grupo_fallo
            base_dato = f"mayor friccion operativa observada ({valor_fallo})"
            alternativa_1 = "flujo de recuperacion de pagos fallidos con ayuda inmediata"
            alternativa_2 = "campana preventiva para revisar metodo de pago antes de operar"
            kpi = "menor tasa de fallo y mas operaciones recuperadas"
        else:
            grupo_objetivo = grupo_valor
            base_dato = f"mejor valor economico observado ({valor_valor})"
            alternativa_1 = "beneficio por uso frecuente en comercios o categorias habituales"
            alternativa_2 = "reto mensual de mayor uso con recompensa si mantiene buena experiencia"
            kpi = "volumen, frecuencia o ticket sin aumentar fallos"

        if normalizar_modo_respuesta(modo_respuesta) == MODO_RESPUESTA_PROFESIONAL:
            return (
                f"**Alternativas ejecutables para la idea enfocada en {grupo_objetivo}**\n\n"
                f"**Base del dato:** el foco sale del analisis anterior porque {grupo_objetivo} muestra {base_dato}. "
                "Estas alternativas son acciones derivadas, no cifras nuevas.\n\n"
                f"**1. Alternativa operativa:** {alternativa_1}.\n"
                f"- **Objetivo:** atacar directamente la senal prioritaria de {grupo_objetivo}.\n"
                "- **Ejecucion:** segmentar el publico, activar comunicacion por el canal disponible y medir contra grupo de control si es posible.\n"
                f"- **KPI:** {kpi}.\n\n"
                f"**2. Alternativa preventiva:** {alternativa_2}.\n"
                "- **Objetivo:** reducir la causa probable de abandono o bajo uso antes de empujar una promocion mas agresiva.\n"
                "- **Ejecucion:** mensaje simple, ayuda guiada y medicion en el siguiente corte Gold.\n"
                f"- **KPI:** {kpi}.\n\n"
                "**Criterio de decision:** empieza por la alternativa que reduzca friccion o inactividad con menor costo operativo; escala solo si mejora el siguiente corte de datos."
            )

        return (
            f"**Dos alternativas claras para la idea enfocada en {grupo_objetivo}**\n\n"
            f"La base es esta: en el analisis anterior, {grupo_objetivo} aparece con {base_dato}. "
            "No estoy inventando numeros nuevos; estoy convirtiendo ese hallazgo en acciones.\n\n"
            f"**1. Alternativa simple:** {alternativa_1}.\n"
            f"- **Como se veria:** enviar un mensaje corto a {grupo_objetivo} con una accion concreta para hacer este mes.\n"
            f"- **Como saber si sirvio:** revisar {kpi} en el siguiente corte de datos.\n\n"
            f"**2. Alternativa de cuidado:** {alternativa_2}.\n"
            "- **Como se veria:** antes de insistir con ventas, ayudar al usuario a completar mejor sus operaciones.\n"
            f"- **Como saber si sirvio:** revisar {kpi} y comparar si mejora frente al corte anterior.\n\n"
            "**Mi recomendacion:** empieza con la alternativa de cuidado si el problema principal son fallos; empieza con la simple si el problema principal es inactividad."
        )

    if normalizar_modo_respuesta(modo_respuesta) == MODO_RESPUESTA_PROFESIONAL:
        return (
            "**Plan de campañas a partir del análisis anterior**\n\n"
            f"**1. Reactivación prioritaria para {grupo_inactivo}**\n"
            f"- **Base del dato:** este grupo aparece como el principal foco de inactividad ({valor_inactivo}).\n"
            "- **Ejecución:** segmentar usuarios inactivos, enviar incentivo de retorno y activar comunicación por el canal con mayor adopción disponible.\n"
            "- **Mensaje:** `Vuelve a usar tu cuenta esta semana y recibe un beneficio en tu próxima operación`.\n"
            "- **Métrica de éxito:** reducción de inactivos y aumento de usuarios que vuelven a transaccionar.\n\n"
            f"**2. Campaña de confianza operativa para {grupo_fallo}**\n"
            f"- **Base del dato:** este grupo concentra la mayor tasa de operaciones con problemas ({valor_fallo}).\n"
            "- **Ejecución:** antes de vender más, priorizar soporte, mensajes de recuperación de pago y seguimiento a operaciones fallidas.\n"
            "- **Mensaje:** `Queremos que tus pagos salgan bien: revisa tu método favorito y recibe ayuda inmediata si algo falla`.\n"
            "- **Métrica de éxito:** disminución de tasa de fallo y recuperación de operaciones rechazadas.\n\n"
            f"**3. Campaña de valor para {grupo_valor}**\n"
            f"- **Base del dato:** este grupo destaca por mayor valor económico observado ({valor_valor}).\n"
            "- **Ejecución:** ofrecer beneficios en categorías o comercios frecuentes, cuidando no saturar a usuarios con baja fricción.\n"
            "- **Mensaje:** `Aprovecha un beneficio especial en tus compras o pagos favoritos`.\n"
            "- **Métrica de éxito:** aumento de volumen, ticket promedio o frecuencia de uso sin deteriorar la tasa de fallo.\n\n"
            "**Cómo lo implementaría:** prueba las tres campañas por grupos separados, mide el siguiente corte de datos y escala solo la que mejore uso sin aumentar fallos."
        )

    return (
        "**Sí, te entiendo. Ya no necesitas otro resumen: necesitas ideas de campaña aterrizadas.**\n\n"
        f"**1. Campaña `Vuelve y gana` para {grupo_inactivo}**\n"
        f"- **Por qué esta campaña:** en los datos, {grupo_inactivo} aparece como el grupo con más personas alejadas o con mayor oportunidad de reactivación ({valor_inactivo}).\n"
        "- **Qué haría:** enviarles un mensaje corto con un beneficio por volver a hacer una compra, pago, transferencia o recarga.\n"
        "- **Cómo se ejecuta:** crear una lista solo con ese grupo, enviar recordatorio por el canal más usado y activar un beneficio sencillo por una primera operación de retorno.\n"
        "- **Ejemplo de mensaje:** `Te extrañamos. Vuelve a mover tu dinero este mes y recibe un beneficio en tu próxima operación`.\n"
        "- **Cómo sabes si funcionó:** bajan los usuarios inactivos y suben las personas que vuelven a usar la plataforma.\n\n"
        f"**2. Campaña `Operación sin fricción` para {grupo_fallo}**\n"
        f"- **Por qué esta campaña:** {grupo_fallo} aparece como el grupo con más operaciones con problemas ({valor_fallo}). Si primero mejoras la experiencia, luego venderles será más fácil.\n"
        "- **Qué haría:** no empezaría con una promoción agresiva; primero reduciría molestias en pagos fallidos, recargas rechazadas o intentos que no terminan bien.\n"
        "- **Cómo se ejecuta:** enviar ayuda guiada, recordatorios de método de pago, soporte rápido y mensajes de recuperación cuando una operación falle.\n"
        "- **Ejemplo de mensaje:** `Notamos que algunas operaciones pueden fallar. Te ayudamos a completarlas de forma más fácil y segura`.\n"
        "- **Cómo sabes si funcionó:** baja el porcentaje de operaciones con problemas y se recuperan más operaciones exitosas.\n\n"
        f"**3. Campaña `Más valor por uso frecuente` para {grupo_valor}**\n"
        f"- **Por qué esta campaña:** {grupo_valor} destaca por mover más dinero por persona o por tener mejor valor económico observado ({valor_valor}).\n"
        "- **Qué haría:** ofrecer un beneficio ligado a sus hábitos reales: comercios frecuentes, pagos recurrentes o categorías donde ya usan la plataforma.\n"
        "- **Cómo se ejecuta:** separar este grupo, enviar una oferta personalizada y medir si aumenta el uso sin subir los fallos.\n"
        "- **Ejemplo de mensaje:** `Sigue usando tu cuenta para tus pagos favoritos y desbloquea un beneficio especial este mes`.\n"
        "- **Cómo sabes si funcionó:** sube el dinero movido, sube la frecuencia de uso o mejora el ticket promedio sin empeorar la experiencia.\n\n"
        "**Mi orden recomendado:** primero reactivaría usuarios alejados, luego corregiría fricciones en el grupo con más fallos y después impulsaría al grupo de mayor valor. Así no solo vendes más: también mejoras la experiencia."
    )


def _es_pregunta_de_seguimiento(pregunta: str) -> bool:
    """Detecta preguntas anafóricas que referencian el contexto anterior."""
    if not _conversation_history and not _last_response_context:
        return False
    p = _normalizar_texto(pregunta)
    if _debe_resolver_como_seguimiento(pregunta):
        return True
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


def _respuesta_seguimiento_contextual_deterministica(
    pregunta: str,
    modo_respuesta: str = "claro",
    motivo: str | None = None,
) -> str:
    """Fallback flexible basado en el contexto, sin inventar cifras nuevas."""
    contexto = _contexto_estructurado_actual() or {}
    p = _normalizar_texto(pregunta)
    hechos = contexto.get("hechos_texto") or "- No hay hechos estructurados suficientes."
    ultima = contexto.get("ultima_respuesta") or ""
    titulo = contexto.get("titulo") or contexto.get("pregunta") or "analisis anterior"

    if any(term in p for term in ("idea", "alternativa", "recomendacion", "campana", "accion", "plan")):
        return _respuesta_elaboracion_contextual_deterministica(pregunta, modo_respuesta)

    aviso = f"_{motivo}_\n\n" if motivo else ""

    if any(term in p for term in ("mensaje", "copy", "redacta", "escribe", "guion")):
        return (
            f"{aviso}**Mensaje basado en el analisis anterior**\n\n"
            "No voy a inventar datos nuevos; uso los hechos validados como base.\n\n"
            f"**Contexto usado:** {titulo}\n\n"
            "**Borrador de mensaje**\n"
            "`Tenemos una oportunidad para mejorar tu experiencia este mes. "
            "Queremos ayudarte a completar tus operaciones con menos friccion y aprovechar mejor los beneficios disponibles.`\n\n"
            "**Por que este mensaje tiene sentido**\n"
            f"{hechos}\n\n"
            "Puedes ajustar el beneficio, canal y publico objetivo segun el segmento o ciudad que quieras activar."
        )

    if any(term in p for term in ("claro", "explica", "significa", "entienda", "simple")):
        return (
            f"{aviso}**Explicacion en palabras simples**\n\n"
            f"Estamos retomando `{titulo}`. Lo importante es no cambiar los numeros: "
            "la lectura debe salir de los hechos validados, no de suposiciones.\n\n"
            "**Lo que si sabemos por Gold**\n"
            f"{hechos}\n\n"
            "**Como usarlo**\n"
            "Piensa en estos datos como senales para decidir donde actuar primero: "
            "donde hay mas usuarios, mas valor, mas fallos o mas inactividad. "
            "La mejor decision combina esas senales, no una sola cifra aislada."
        )

    return (
        f"{aviso}**Retomando la respuesta anterior**\n\n"
        f"Contexto: `{titulo}`.\n\n"
        "**Hechos validados por codigo**\n"
        f"{hechos}\n\n"
        "**Ultima lectura disponible**\n"
        f"{ultima[:1200] if ultima else 'No hay una respuesta anterior guardada con detalle.'}\n\n"
        "Con esto puedo seguir profundizando sin inventar cifras. Puedo convertirlo en pasos, "
        "mensajes, alternativas, riesgos, KPIs o una explicacion mas sencilla."
    )


def _resolver_seguimiento_con_ollama(
    pregunta: str,
    modo_respuesta: str = "profesional",
) -> str:
    """Responde preguntas de seguimiento usando el historial completo de la conversación."""
    contexto = _contexto_estructurado_actual()
    es_elaboracion = _es_solicitud_elaboracion_contextual(pregunta)
    contexto_texto = ""
    if contexto:
        contexto_texto = (
            f"Pregunta anterior: {contexto.get('pregunta', 'N/D')}\n"
            f"Título anterior: {contexto.get('titulo', 'N/D')}\n"
            f"Tipo de respuesta anterior: {contexto.get('tipo', 'N/D')}\n\n"
            f"Hechos validados por código:\n{contexto.get('hechos_texto', 'N/D')}\n\n"
            f"Datos exactos usados:\n{contexto.get('datos_texto', 'N/D')}\n\n"
            f"Ultima respuesta del agente:\n{contexto.get('ultima_respuesta', 'N/D')}"
        )
    if not _verificar_ollama():
        if es_elaboracion:
            return _respuesta_elaboracion_contextual_deterministica(pregunta, modo_respuesta)
        if contexto_texto:
            return _respuesta_seguimiento_contextual_deterministica(
                pregunta,
                modo_respuesta,
                "Ollama no esta disponible; respondo con el control deterministico y los hechos Gold.",
            )
        return (
            "Ollama no está disponible para responder la pregunta de seguimiento.\n"
            "Por favor, reformula la pregunta con más contexto."
        )
    mensajes = [{"role": "system", "content": _system_seguimiento_contextual(modo_respuesta)}]
    historial = _conversation_history
    if historial and historial[-1]["role"] == "user" and historial[-1]["content"] == pregunta:
        historial = historial[:-1]
    for m in historial[-6:]:
        mensajes.append({"role": m["role"], "content": m["content"][:800]})
    if contexto_texto:
        mensajes.append({
            "role": "system",
            "content": (
                "Contexto estructurado obligatorio de la respuesta anterior. "
                "Si el usuario corrige un máximo, mínimo, período o dato, valida contra estos hechos. "
                "No cambies a otro tema salvo que el usuario lo pida explícitamente.\n\n"
                f"{contexto_texto}"
            ),
        })
    if es_elaboracion:
        mensajes.append({
            "role": "user",
            "content": (
                f"Pregunta de profundización del usuario: {pregunta}\n\n"
                "El usuario quiere transformar o profundizar la respuesta anterior. "
                "No repitas el analisis inicial. Adapta el formato al pedido exacto: ideas, mensajes, pasos, "
                "alternativas, riesgos, KPIs, comparaciones o explicacion. Usa los hechos Gold como restricciones. "
                "Si propones acciones, deja claro que son recomendaciones derivadas de los datos, no datos observados."
            ),
        })
    else:
        mensajes.append({
            "role": "user",
            "content": (
                f"Pregunta de seguimiento del usuario: {pregunta}\n\n"
                "Responde manteniendo cohesion con el contexto anterior. "
                "No conviertas esta pregunta en una consulta nueva salvo que el usuario lo pida explicitamente. "
                "No repitas el formato fijo. Adapta el formato al pedido concreto del usuario. "
                "Si hubo un error en la interpretacion previa, reconocelo brevemente, corrige usando los hechos "
                "validados y explica la duda concreta."
            ),
        })
    try:
        resp = requests.post(
            f"{OLLAMA_BASE_URL}/api/chat",
            json={
                "model": OLLAMA_MODEL,
                "messages": mensajes,
                "stream": False,
                "options": {
                    "num_ctx": 8192,
                    "num_predict": 1400,
                    "temperature": 0.1,
                    "top_p": 0.85,
                    "repeat_penalty": 1.05,
                },
            },
            timeout=120,
        )
        resp.raise_for_status()
        respuesta = resp.json().get("message", {}).get("content", "").strip()
        respuesta_norm = _normalizar_texto(respuesta)
        formato_repetido = any(term in respuesta_norm for term in (
            "verificacion simple", "lectura validada", "analisis por datos",
            "datos usados para responder", "datos gold",
        ))
        if es_elaboracion:
            sin_plan = not any(term in respuesta_norm for term in (
                "campana", "campaña", "idea", "publico", "público", "ejecucion", "ejecución",
            ))
            if not respuesta or formato_repetido or sin_plan:
                return _respuesta_elaboracion_contextual_deterministica(pregunta, modo_respuesta)
        elif respuesta and formato_repetido and any(
            cue in _normalizar_texto(pregunta) for cue in _CONTEXTUAL_FOLLOWUP_ACTION_CUES
        ):
            return _respuesta_seguimiento_contextual_deterministica(
                pregunta,
                modo_respuesta,
                "La respuesta generada repitio el formato rigido; uso el control deterministico para mantener el hilo.",
            )
        return respuesta or "No pude generar respuesta."
    except Exception as e:
        if es_elaboracion:
            return _respuesta_elaboracion_contextual_deterministica(pregunta, modo_respuesta)
        return _respuesta_seguimiento_contextual_deterministica(
            pregunta,
            modo_respuesta,
            f"Error al procesar seguimiento con Ollama: {e}",
        )


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


def _verificar_ollama(intentos: int = 3, espera_segundos: float = 0.6, timeout: float = 8.0) -> bool:
    """Verifica que Ollama esté corriendo y tenga el modelo disponible."""
    modelo_esperado = OLLAMA_MODEL.split(":")[0]
    for intento in range(max(1, intentos)):
        try:
            r = requests.get(f"{OLLAMA_BASE_URL}/api/tags", timeout=timeout)
            if r.status_code == 200:
                modelos = [m["name"].split(":")[0] for m in r.json().get("models", [])]
                if modelo_esperado in modelos:
                    return True
        except Exception:
            pass
        if intento < intentos - 1:
            time.sleep(espera_segundos)
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


_SYSTEM_INTERPRETACION_CLARA = """Eres un traductor de datos fintech para personas que NO son expertas en finanzas, datos ni tecnología. Recibes datos reales de usuarios, pagos, compras, transferencias, recargas, fallos y comportamiento transaccional. Tu misión es explicar qué significan esos datos en lenguaje natural, útil y completo, sin infantilizar al lector.

OBLIGATORIO en cada respuesta:
• Usa los datos exactos que recibes. NUNCA inventes cifras ni porcentajes.
• Explica cada concepto financiero o técnico la primera vez que aparezca: volumen, ticket promedio, tasa de fallo, segmento, canal, retención, inactividad, revenue o monto.
• Mantén la misma estructura profesional de 4 bloques, pero con lenguaje claro, ejemplos cotidianos y conclusiones accionables.
• Cuando compares datos, di qué significa en la práctica. Ejemplo: "si esto fuera una tienda, este segmento sería el grupo de clientes que más compra".
• Si una cifra es alta o baja, explica por qué importa para una persona o negocio real.
• Evita jerga innecesaria. Si debes usar una palabra técnica, defínela en la misma frase.
• Da recomendaciones concretas indicando qué hacer, para quién, por qué y qué beneficio se espera.
• Español claro, cercano y completo. Mínimo 350 palabras.

FORMATO OBLIGATORIO (sin excepción, siempre usa estos 4 bloques):

**Resumen Ejecutivo**
Explica en palabras simples qué está pasando y por qué importa. Incluye el dato más importante y su significado práctico.

**Análisis Comparativo**
Compara los valores principales con cifras exactas. Explica quién está mejor, quién está peor y qué significa esa diferencia con ejemplos fáciles de entender.

**Insights Clave**
2 observaciones que una persona normal podría usar para tomar decisiones: oportunidades, riesgos, comportamientos inesperados o señales de alerta.

**Recomendaciones Estratégicas**
2 acciones concretas y entendibles. Cada recomendación debe decir: qué hacer, a qué usuarios/segmento/ciudad/canal aplica, por qué conviene y qué resultado busca mejorar."""


MODO_RESPUESTA_PROFESIONAL = "profesional"
MODO_RESPUESTA_CLARO = "claro"


def normalizar_modo_respuesta(modo_respuesta: str | None = None) -> str:
    """Normaliza alias de audiencia para mantener compatibilidad con UI/tests."""
    modo = (modo_respuesta or MODO_RESPUESTA_PROFESIONAL).strip().lower()
    aliases_claros = {
        "claro",
        "natural",
        "persona",
        "persona natural",
        "usuario",
        "usuario natural",
        "explicacion clara",
        "explicación clara",
        "no financiero",
        "principiante",
    }
    if modo in aliases_claros:
        return MODO_RESPUESTA_CLARO
    return MODO_RESPUESTA_PROFESIONAL


def etiqueta_modo_respuesta(modo_respuesta: str | None = None) -> str:
    modo = normalizar_modo_respuesta(modo_respuesta)
    if modo == MODO_RESPUESTA_CLARO:
        return "explicación clara"
    return "profesional financiero"


def _system_interpretacion(modo_respuesta: str | None = None) -> str:
    if normalizar_modo_respuesta(modo_respuesta) == MODO_RESPUESTA_CLARO:
        return _SYSTEM_INTERPRETACION_CLARA
    return _SYSTEM_INTERPRETACION


def _system_seguimiento_contextual(modo_respuesta: str | None = None) -> str:
    base = """Eres un agente conversacional de analisis fintech. Estas respondiendo un seguimiento del usuario sobre una respuesta anterior, no un analisis inicial.

REGLAS DE CONTROL:
- La fuente de verdad son los hechos validados por codigo, los datos Gold y la ultima respuesta del agente.
- Mantiene el hilo de la conversacion: identifica que parte de la respuesta anterior esta retomando el usuario.
- Adapta el formato a lo que el usuario pide ahora. Si pide ideas, entrega ideas; si pide un mensaje, redacta un mensaje; si pide pasos, da pasos; si pide explicacion, explica; si pide comparacion, compara.
- No fuerces los bloques fijos de analisis inicial. No repitas "Verificacion simple", "Lectura validada", "Analisis por datos", "Conclusion" y "Recomendacion" salvo que el usuario pida ese formato.
- No vuelvas a mostrar toda la tabla ni toda la respuesta anterior si el usuario solo esta pidiendo profundizar.
- No inventes cifras, porcentajes, rankings, fechas ni nombres que no esten en los datos recibidos.
- Puedes proponer acciones, ejemplos de mensajes, hipotesis y planes operativos, pero debes separarlos de los hechos observados.
- Si el usuario pide algo que no puede sostenerse con Gold, dilo con claridad y ofrece una alternativa basada en lo que si existe.
- Si el usuario corrige un dato, valida contra los hechos recibidos y reconoce la correccion sin defender una lectura equivocada.
"""
    if normalizar_modo_respuesta(modo_respuesta) == MODO_RESPUESTA_CLARO:
        return base + """
AUDIENCIA:
- Responde para una persona que no domina finanzas ni datos.
- Usa lenguaje natural, ejemplos cotidianos y frases cortas.
- Cambia palabras tecnicas por equivalentes claros cuando sea posible.
- Si usas un termino como tasa de fallo, ticket o revenue, explica que significa en la practica.
- Tu objetivo es que la persona pueda tomar una decision sin sentirse perdida.
"""
    return base + """
AUDIENCIA:
- Responde para un profesional financiero o de negocio.
- Usa tono ejecutivo, criterios de priorizacion, trade-offs, riesgos, KPIs y supuestos controlados.
- Mantente accionable: cada recomendacion debe tener objetivo, publico, ejecucion y metrica de exito cuando aplique.
"""


def _instrucciones_interpretacion(modo_respuesta: str | None = None) -> str:
    if normalizar_modo_respuesta(modo_respuesta) == MODO_RESPUESTA_CLARO:
        return (
            "Analiza TODOS los valores con una explicación clara para una persona sin conocimientos financieros. "
            "Define en palabras simples cada concepto importante antes de interpretarlo. "
            "Usa ejemplos cotidianos para explicar qué significa una diferencia entre segmentos, ciudades, canales o métricas. "
            "Mantén los 4 bloques obligatorios: Resumen Ejecutivo, Análisis Comparativo, Insights Clave y Recomendaciones Estratégicas. "
            "No añadas ningún número que no esté en los datos anteriores."
        )
    return (
        "Analiza TODOS los valores de la tabla con profundidad ejecutiva. "
        "Identifica el mejor y el peor performer en cada métrica con sus cifras exactas. "
        "Calcula brechas porcentuales entre extremos. "
        "Busca correlaciones no obvias entre las métricas disponibles. "
        "Si hay concentración de riesgo en pocos segmentos o ciudades, señálala. "
        "Responde con el formato de 4 bloques del sistema: Resumen Ejecutivo, Análisis Comparativo, Insights Clave y Recomendaciones Estratégicas. "
        "No añadas ningún número que no esté en los datos anteriores."
    )


def _interpretar_con_ollama(
    pregunta: str,
    datos_texto: str,
    modo_respuesta: str = MODO_RESPUESTA_PROFESIONAL,
) -> str:
    """
    Pasa datos reales de Gold a Ollama para interpretación en lenguaje natural.
    Si Ollama no está disponible, devuelve los datos en formato tabla como fallback.
    """
    fallback = (
        f"**Datos Gold**\n```text\n{datos_texto}\n```\n\n"
        "_Ollama no disponible para interpretación. Estos son los datos reales de la capa Gold._"
    )
    _actualizar_contexto_estructurado({
        "tipo": "datos",
        "titulo": pregunta,
        "pregunta": pregunta,
        "datos_texto": datos_texto,
        "hechos_texto": (
            "- Respuesta basada en datos Gold certificados.\n"
            "- Para consultas tabulares, los valores visibles en el bloque Datos Gold son la fuente de verdad.\n"
            "- Si el usuario pide aclarar, corregir o profundizar, se debe mantener este mismo contexto."
        ),
    })
    if not _verificar_ollama():
        return fallback

    prompt_usuario = (
        f"Pregunta: {pregunta}\n\n"
        f"Datos reales de la capa Gold:\n{datos_texto}\n\n"
        f"{_instrucciones_interpretacion(modo_respuesta)}"
    )
    try:
        mensajes = [{"role": "system", "content": _system_interpretacion(modo_respuesta)}]
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
                f"**Análisis** _(Ollama · {OLLAMA_MODEL} · {etiqueta_modo_respuesta(modo_respuesta)})_\n\n"
                f"{interpretacion}"
            )
    except Exception as e:
        print(f"  [Ollama] Error en interpretación: {e}")
    return fallback


def _respuesta_desde_sql(
    titulo: str,
    sql: str,
    pregunta: str = "",
    modo_respuesta: str = MODO_RESPUESTA_PROFESIONAL,
) -> str:
    datos = _ejecutar_sql_duckdb_texto(sql)
    return _interpretar_con_ollama(pregunta or titulo, datos, modo_respuesta)


def _respuesta_con_grafico(
    titulo: str,
    sql: str,
    pregunta: str,
    modo_respuesta: str = MODO_RESPUESTA_PROFESIONAL,
) -> str:
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
        return _interpretar_con_ollama(pregunta, f"Error al consultar datos Gold: {e}", modo_respuesta)

    if df.empty:
        return _interpretar_con_ollama(
            pregunta,
            "La consulta no retornó resultados en la capa Gold.",
            modo_respuesta,
        )

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

    return _ejecutar_grafico_con_analisis(df, titulo, tipo, pregunta, modo_respuesta)


def _respuesta_datos_confiable(
    pregunta: str,
    modo_respuesta: str = MODO_RESPUESTA_PROFESIONAL,
) -> str | None:
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
        return _interpretar_con_ollama(pregunta, datos, modo_respuesta)

    # ── Comparación de períodos ────────────────────────────────────────────────
    if any(k in p for k in ("comparar periodo", "periodo anterior", "semana pasada",
                             "semana anterior", "mes pasado", "mes anterior",
                             "vs semana", "variacion", "variación", "cambio reciente",
                             "como fue", "evolucion reciente", "ultimos dias vs")):
        # Detectar número de días si viene en la pregunta (ej: "últimos 14 días")
        match = re.search(r"(\d+)\s*d[ií]as?", p)
        dias = int(match.group(1)) if match and 1 <= int(match.group(1)) <= 30 else 7
        datos = comparar_periodos(dias)
        return _interpretar_con_ollama(pregunta, datos, modo_respuesta)

    # ── Resumen ejecutivo / KPIs ───────────────────────────────────────────────
    if any(k in p for k in ("resumen", "ejecutivo", "kpi", "indicador", "indicadores")):
        datos = resumen_ejecutivo()
        return _interpretar_con_ollama(pregunta, datos, modo_respuesta)

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
        if normalizar_modo_respuesta(modo_respuesta) == MODO_RESPUESTA_PROFESIONAL:
            return _respuesta_con_grafico(
                "Análisis para Estrategia de Campañas por Segmento",
                _sql_campanias,
                pregunta,
            )
        return _respuesta_con_grafico(
            "Análisis para Estrategia de Campañas por Segmento",
            _sql_campanias,
            pregunta,
            modo_respuesta,
        )

    intent = _sql_por_intencion(pregunta)
    if intent:
        sql, titulo = intent
        if normalizar_modo_respuesta(modo_respuesta) == MODO_RESPUESTA_PROFESIONAL:
            return _respuesta_con_grafico(titulo, sql, pregunta)
        return _respuesta_con_grafico(titulo, sql, pregunta, modo_respuesta)

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
    return _resumen_perfil_gold()


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


def _formatear_valor_contexto(valor) -> str:
    if pd.isna(valor):
        return "N/D"
    try:
        numero = float(valor)
    except (TypeError, ValueError):
        return str(valor)
    if numero.is_integer():
        return f"{int(numero):,}"
    return f"{numero:,.2f}"


def _nombre_metrica_legible(columna: str) -> str:
    nombres = {
        "usuarios": "usuarios",
        "volumen_cop": "volumen COP",
        "volumen_m_cop": "volumen en millones COP",
        "revenue_por_usuario": "revenue por usuario",
        "ticket_promedio": "ticket promedio",
        "ticket_promedio_tx": "ticket promedio por transaccion",
        "tasa_fallo_pct": "tasa de fallo",
        "inactivos": "usuarios inactivos",
        "inactivos_30d": "usuarios inactivos 30 dias",
        "dias_promedio_sin_tx": "dias promedio sin transaccion",
        "transacciones_fallidas": "transacciones fallidas",
        "fallos_totales": "fallos totales",
        "balance_promedio": "balance promedio",
        "penetracion_pct": "penetracion",
        "share_pct": "participacion",
    }
    key = _normalizar_texto(columna).replace(" ", "_")
    return nombres.get(key, columna.replace("_", " "))


def _etiqueta_clara(valor: str) -> str:
    etiquetas = {
        "student": "estudiantes",
        "young_professional": "jovenes profesionales",
        "family": "familias",
        "premium": "clientes premium",
        "web": "canal web",
        "mobile": "celular",
        "app": "aplicacion movil",
        "desktop": "computador",
    }
    key = _normalizar_texto(str(valor)).replace(" ", "_")
    return etiquetas.get(key, str(valor).replace("_", " "))


def _nombre_metrica_clara(columna: str) -> str:
    nombres = {
        "usuarios": "cantidad de personas",
        "volumen_cop": "dinero total movido",
        "volumen_m_cop": "dinero total movido",
        "revenue_por_usuario": "dinero promedio movido por persona",
        "ticket_promedio": "valor promedio de cada pago o compra",
        "ticket_promedio_tx": "valor promedio de cada pago o compra",
        "tasa_fallo_pct": "porcentaje de operaciones con problemas",
        "inactivos": "personas que llevan tiempo sin usar la plataforma",
        "inactivos_30d": "personas que llevan mas de 30 dias sin usar la plataforma",
        "dias_promedio_sin_tx": "dias promedio sin movimiento",
        "transacciones_fallidas": "operaciones que fallaron",
        "fallos_totales": "operaciones que fallaron",
        "balance_promedio": "saldo promedio disponible",
        "penetracion_pct": "nivel de adopcion",
        "share_pct": "participacion dentro del total",
    }
    key = _normalizar_texto(columna).replace(" ", "_")
    return nombres.get(key, columna.replace("_", " "))


def _explicacion_metrica_clara(columna: str) -> str:
    explicaciones = {
        "usuarios": "sirve para ver donde hay mas personas a quienes impactar.",
        "volumen_cop": "sirve para ver donde se mueve mas dinero en total.",
        "volumen_m_cop": "sirve para ver donde se mueve mas dinero en total.",
        "revenue_por_usuario": "sirve para entender que grupo suele mover mas dinero por persona.",
        "ticket_promedio": "sirve para entender el valor promedio de los pagos o compras.",
        "ticket_promedio_tx": "sirve para entender el valor promedio de los pagos o compras.",
        "tasa_fallo_pct": "sirve para detectar donde la experiencia puede estar fallando.",
        "inactivos": "sirve para encontrar personas que podrian reactivarse con una campana.",
        "inactivos_30d": "sirve para encontrar personas que podrian reactivarse con una campana.",
        "dias_promedio_sin_tx": "sirve para ver que grupos llevan mas tiempo sin moverse.",
    }
    key = _normalizar_texto(columna).replace(" ", "_")
    return explicaciones.get(key, "ayuda a entender mejor el comportamiento de ese grupo.")


def _formatear_valor_metrica(meta: dict, campo: str) -> str:
    texto = _formatear_valor_contexto(meta.get(campo))
    columna = _normalizar_texto(str(meta.get("columna", "")))
    nombre = _normalizar_texto(str(meta.get("nombre", "")))
    if texto != "N/D" and any(token in f"{columna} {nombre}" for token in ("pct", "tasa", "porcentaje")):
        return f"{texto}%"
    return texto


def _detectar_columna_temporal_df(df: pd.DataFrame) -> str | None:
    for col in df.columns:
        col_lower = str(col).lower()
        if col_lower in {"date", "fecha", "dia", "día"} or "date" in col_lower or "fecha" in col_lower:
            serie = pd.to_datetime(df[col], errors="coerce")
            if serie.notna().any():
                return col
    return None


def _columnas_numericas_df(df: pd.DataFrame) -> list[str]:
    cols = []
    for col in df.columns:
        serie = pd.to_numeric(df[col], errors="coerce")
        if serie.notna().any():
            cols.append(col)
    return cols


def _columna_etiqueta_df(df: pd.DataFrame) -> str:
    temporal = _detectar_columna_temporal_df(df)
    if temporal:
        return temporal
    numericas = set(_columnas_numericas_df(df))
    for col in df.columns:
        if col not in numericas:
            return col
    return df.columns[0]


def _periodo_gold_global() -> str:
    try:
        conn = _get_conn_duckdb()
        row = conn.execute(
            "SELECT MIN(date) AS desde, MAX(date) AS hasta FROM gold_daily_metrics"
        ).fetchone()
        if row and row[0] is not None and row[1] is not None:
            desde = pd.to_datetime(row[0]).date()
            hasta = pd.to_datetime(row[1]).date()
            return f"{desde} a {hasta}"
    except Exception:
        return ""
    return ""


def _ajustar_tipo_grafico_por_datos(tipo: str, df: pd.DataFrame) -> tuple[str, list[str]]:
    notas: list[str] = []
    tipo_final = tipo
    tiene_tiempo = _detectar_columna_temporal_df(df) is not None
    if tipo == "line" and not tiene_tiempo:
        tipo_final = "bar"
        notas.append(
            "El usuario pidió un gráfico de líneas, pero los datos no tienen columna de fecha; "
            "se usa barras porque es una comparación entre categorías, no una evolución temporal."
        )
    if tipo == "pie" and len(df) > 8:
        notas.append(
            "La torta usa muchas categorías; para lectura ejecutiva conviene revisar también barras ordenadas."
        )
    return tipo_final, notas


def _construir_contexto_dataframe(
    df: pd.DataFrame,
    titulo: str,
    tipo_solicitado: str,
    tipo_usado: str,
    pregunta: str,
    notas: list[str] | None = None,
) -> dict:
    label_col = _columna_etiqueta_df(df)
    temporal_col = _detectar_columna_temporal_df(df)
    numericas = [col for col in _columnas_numericas_df(df) if col != label_col]
    if not numericas and label_col in _columnas_numericas_df(df):
        numericas = [label_col]

    lineas = [
        f"- Título analizado: {titulo}",
        f"- Pregunta original: {pregunta}",
        f"- Filas analizadas: {len(df)}",
        f"- Tipo solicitado: {tipo_solicitado}",
        f"- Tipo usado: {tipo_usado}",
    ]
    if notas:
        lineas.extend(f"- Ajuste aplicado: {nota}" for nota in notas)

    if temporal_col:
        serie_fecha = pd.to_datetime(df[temporal_col], errors="coerce").dropna()
        if not serie_fecha.empty:
            lineas.append(f"- Periodo en la tabla: {serie_fecha.min().date()} a {serie_fecha.max().date()}")
    else:
        periodo = _periodo_gold_global()
        if periodo:
            lineas.append(
                "- Temporalidad: esta tabla no es una serie de tiempo; "
                f"compara categorías consolidadas sobre el periodo Gold disponible ({periodo})."
            )
        else:
            lineas.append(
                "- Temporalidad: esta tabla no es una serie de tiempo; compara categorías consolidadas."
            )

    metricas_resumen: list[dict] = []
    for col in numericas:
        serie = pd.to_numeric(df[col], errors="coerce").dropna()
        if serie.empty:
            continue
        idx_max = serie.idxmax()
        idx_min = serie.idxmin()
        max_label = str(df.loc[idx_max, label_col])
        min_label = str(df.loc[idx_min, label_col])
        max_val = float(serie.loc[idx_max])
        min_val = float(serie.loc[idx_min])
        brecha = max_val - min_val
        if min_val != 0:
            brecha_pct = (brecha / abs(min_val)) * 100
            brecha_txt = f"{brecha_pct:,.1f}% sobre el mínimo"
        else:
            brecha_pct = None
            brecha_txt = "no calculable porcentualmente porque el mínimo es 0"
        metricas_resumen.append({
            "columna": col,
            "nombre": _nombre_metrica_legible(col),
            "max_label": max_label,
            "min_label": min_label,
            "max_val": max_val,
            "min_val": min_val,
            "brecha": brecha,
            "brecha_pct": brecha_pct,
            "brecha_txt": brecha_txt,
        })
        lineas.extend([
            f"- Métrica principal '{col}': máximo {max_label} = {_formatear_valor_contexto(max_val)}.",
            f"- Métrica principal '{col}': mínimo {min_label} = {_formatear_valor_contexto(min_val)}.",
            f"- Brecha '{col}': {_formatear_valor_contexto(brecha)} ({brecha_txt}).",
        ])

    datos_texto = df.to_string(index=False)
    return {
        "tipo": "grafico",
        "titulo": titulo,
        "pregunta": pregunta,
        "tipo_solicitado": tipo_solicitado,
        "tipo_usado": tipo_usado,
        "label_col": label_col,
        "etiquetas": [str(v) for v in df[label_col].tolist()] if label_col in df.columns else [],
        "metricas": metricas_resumen,
        "datos_texto": datos_texto,
        "hechos_texto": "\n".join(lineas),
    }


def _metricas_contexto(contexto: dict) -> list[dict]:
    metricas = contexto.get("metricas", [])
    return metricas if isinstance(metricas, list) else []


def _ordenar_metricas_para_pregunta(metricas: list[dict], pregunta: str, titulo: str) -> list[dict]:
    texto = _normalizar_texto(f"{pregunta} {titulo}")
    prioridades: list[str] = []
    if any(term in texto for term in ("campana", "campanas", "lanzar", "lanzaria", "promocion")):
        prioridades = ["inactivo", "fallo", "revenue", "ticket", "usuarios", "volumen"]
    elif any(term in texto for term in ("fallo", "rechazo", "friccion", "error")):
        prioridades = ["fallo", "transacciones_fallidas", "usuarios", "revenue", "volumen"]
    elif any(term in texto for term in ("crecimiento", "potencial", "ciudad", "mercado")):
        prioridades = ["usuarios", "volumen", "revenue", "ticket", "fallo", "inactivo"]
    elif any(term in texto for term in ("revenue", "rentable", "ticket", "monto", "volumen")):
        prioridades = ["revenue", "ticket", "volumen", "usuarios", "fallo"]
    elif any(term in texto for term in ("inactivo", "churn", "retencion", "abandono")):
        prioridades = ["inactivo", "dias", "fallo", "usuarios", "ticket"]

    if not prioridades:
        return metricas

    def puntaje(meta: dict) -> int:
        texto_meta = _normalizar_texto(f"{meta.get('columna', '')} {meta.get('nombre', '')}")
        for idx, token in enumerate(prioridades):
            if token in texto_meta:
                return idx
        return len(prioridades)

    return sorted(metricas, key=puntaje)


def _linea_extremo(meta: dict) -> str:
    nombre = meta.get("nombre", meta.get("columna", "metrica"))
    max_label = meta.get("max_label", "N/D")
    min_label = meta.get("min_label", "N/D")
    max_val = _formatear_valor_metrica(meta, "max_val")
    min_val = _formatear_valor_metrica(meta, "min_val")
    brecha = _formatear_valor_contexto(meta.get("brecha"))
    brecha_txt = meta.get("brecha_txt", "brecha no calculable")
    return (
        f"- En **{nombre}**, el valor mas alto es **{max_label}** con **{max_val}**; "
        f"el valor mas bajo es **{min_label}** con **{min_val}**. "
        f"La brecha es **{brecha}** ({brecha_txt})."
    )


def _linea_extremo_clara(meta: dict) -> str:
    columna = str(meta.get("columna", ""))
    nombre = _nombre_metrica_clara(columna)
    max_label = _etiqueta_clara(str(meta.get("max_label", "N/D")))
    min_label = _etiqueta_clara(str(meta.get("min_label", "N/D")))
    max_val = _formatear_valor_metrica(meta, "max_val")
    min_val = _formatear_valor_metrica(meta, "min_val")
    brecha = _formatear_valor_contexto(meta.get("brecha"))
    explicacion = _explicacion_metrica_clara(columna)
    return (
        f"- **{nombre.capitalize()}**: el grupo mas alto es **{max_label}** con **{max_val}**; "
        f"el mas bajo es **{min_label}** con **{min_val}**. "
        f"La diferencia es de **{brecha}**. Esto {explicacion}"
    )


def _buscar_metrica(metricas: list[dict], *tokens: str) -> dict | None:
    for meta in metricas:
        texto = _normalizar_texto(f"{meta.get('columna', '')} {meta.get('nombre', '')}")
        if any(token in texto for token in tokens):
            return meta
    return None


def _recomendacion_grafico_clara(metricas: list[dict], pregunta: str, titulo: str) -> str:
    texto = _normalizar_texto(f"{pregunta} {titulo}")
    metrica_usuarios = _buscar_metrica(metricas, "usuarios")
    metrica_revenue = _buscar_metrica(metricas, "revenue")
    metrica_ticket = _buscar_metrica(metricas, "ticket")
    metrica_fallo = _buscar_metrica(metricas, "fallo")
    metrica_inactivo = _buscar_metrica(metricas, "inactivo")

    if any(term in texto for term in ("campana", "campanas", "lanzar", "lanzaria", "promocion")):
        reactivar = metrica_inactivo or metrica_usuarios or metricas[0]
        grupo_reactivar = _etiqueta_clara(reactivar.get("max_label", "el grupo principal"))
        valor_reactivar = _formatear_valor_metrica(reactivar, "max_val")
        partes = [
            f"Yo lanzaria una campana de **reactivacion para {grupo_reactivar}**, "
            f"porque es el grupo con mas personas alejadas de la plataforma (**{valor_reactivar}**).",
            "La campana podria ofrecer un beneficio simple: cashback pequeno, recordatorio personalizado "
            "o incentivo por volver a hacer una compra, pago o recarga este mes.",
        ]
        if metrica_fallo:
            partes.append(
                f"Tambien revisaria a **{_etiqueta_clara(metrica_fallo.get('max_label'))}**, "
                f"porque alli aparece el mayor porcentaje de operaciones con problemas "
                f"(**{_formatear_valor_metrica(metrica_fallo, 'max_val')}**). "
                "Antes de venderles mas, conviene reducir esa friccion."
            )
        if metrica_revenue or metrica_ticket:
            valor = metrica_revenue or metrica_ticket
            partes.append(
                f"Como oportunidad comercial, miraria a **{_etiqueta_clara(valor.get('max_label'))}**, "
                f"porque es el grupo que mas dinero mueve por persona "
                f"(**{_formatear_valor_metrica(valor, 'max_val')}**)."
            )
        return " ".join(partes)

    if any(term in texto for term in ("crecimiento", "potencial", "ciudad", "mercado")):
        escala = metrica_usuarios or metricas[0]
        partes = [
            f"Si buscas crecer rapido, empezaria por **{_etiqueta_clara(escala.get('max_label'))}**, "
            f"porque concentra mas personas (**{_formatear_valor_metrica(escala, 'max_val')}**)."
        ]
        valor = metrica_revenue or metrica_ticket
        if valor:
            partes.append(
                f"Si buscas vender mejor, miraria **{_etiqueta_clara(valor.get('max_label'))}**, "
                f"porque tiene el mayor dinero promedio por persona "
                f"(**{_formatear_valor_metrica(valor, 'max_val')}**)."
            )
        if metrica_fallo:
            partes.append(
                f"Y antes de invertir demasiado, revisaria los problemas de operacion en "
                f"**{_etiqueta_clara(metrica_fallo.get('max_label'))}** "
                f"(**{_formatear_valor_metrica(metrica_fallo, 'max_val')}**)."
            )
        return " ".join(partes)

    principal = metricas[0]
    return (
        f"Empezaria por **{_etiqueta_clara(principal.get('max_label'))}**, porque tiene el valor mas alto en "
        f"**{_nombre_metrica_clara(str(principal.get('columna', '')))}** "
        f"(**{_formatear_valor_metrica(principal, 'max_val')}**). "
        f"Compararia ese resultado con **{_etiqueta_clara(principal.get('min_label'))}**, "
        f"que tiene el valor mas bajo (**{_formatear_valor_metrica(principal, 'min_val')}**), "
        "para entender que accion podria cerrar esa diferencia."
    )


def _recomendacion_grafico_deterministica(metricas: list[dict], pregunta: str, titulo: str) -> str:
    texto = _normalizar_texto(f"{pregunta} {titulo}")
    metrica_usuarios = _buscar_metrica(metricas, "usuarios")
    metrica_volumen = _buscar_metrica(metricas, "volumen")
    metrica_revenue = _buscar_metrica(metricas, "revenue")
    metrica_ticket = _buscar_metrica(metricas, "ticket")
    metrica_fallo = _buscar_metrica(metricas, "fallo")
    metrica_inactivo = _buscar_metrica(metricas, "inactivo")

    if any(term in texto for term in ("campana", "campanas", "lanzar", "lanzaria", "promocion")):
        foco = metrica_inactivo or metrica_usuarios or metrica_revenue or metricas[0]
        apoyo = metrica_fallo or metrica_revenue or metrica_ticket
        foco_label = foco.get("max_label", "el grupo lider")
        foco_nombre = foco.get("nombre", foco.get("columna", "la metrica principal"))
        apoyo_txt = ""
        if apoyo:
            apoyo_txt = (
                f" Como segunda senal, revisa **{apoyo.get('nombre')}**: "
                f"su valor mas alto esta en **{apoyo.get('max_label')}** "
                f"({_formatear_valor_contexto(apoyo.get('max_val'))})."
            )
        return (
            f"Lanza una campana prioritaria sobre **{foco_label}**, porque lidera en **{foco_nombre}** "
            f"con **{_formatear_valor_contexto(foco.get('max_val'))}**. "
            "La accion debe tener un objetivo medible: reactivar, reducir friccion o aumentar uso, "
            "segun la metrica que estes priorizando."
            f"{apoyo_txt} Evita decidir solo por una variable: cruza tamano, valor economico y fallos."
        )

    if any(term in texto for term in ("crecimiento", "potencial", "ciudad", "mercado")):
        escala = metrica_usuarios or metrica_volumen or metricas[0]
        monetizacion = metrica_revenue or metrica_ticket
        riesgo = metrica_fallo
        partes = [
            f"Para crecimiento, prioriza **{escala.get('max_label')}** si buscas escala, "
            f"porque lidera en **{escala.get('nombre')}** con **{_formatear_valor_contexto(escala.get('max_val'))}**."
        ]
        if monetizacion:
            partes.append(
                f"Si el objetivo es monetizacion por usuario, el foco cambia a **{monetizacion.get('max_label')}** "
                f"por su mayor **{monetizacion.get('nombre')}** "
                f"({_formatear_valor_contexto(monetizacion.get('max_val'))})."
            )
        if riesgo:
            partes.append(
                f"Antes de invertir fuerte, revisa friccion en **{riesgo.get('max_label')}**, "
                f"donde la **{riesgo.get('nombre')}** llega a **{_formatear_valor_contexto(riesgo.get('max_val'))}**."
            )
        return " ".join(partes)

    principal = metricas[0]
    return (
        f"Actua primero sobre **{principal.get('max_label')}**, que lidera en "
        f"**{principal.get('nombre')}** con **{_formatear_valor_contexto(principal.get('max_val'))}**. "
        f"Usa **{principal.get('min_label')}** como punto de comparacion, porque marca el valor mas bajo "
        f"({_formatear_valor_contexto(principal.get('min_val'))}). "
        "Despues valida si la diferencia se mantiene en el siguiente corte Gold antes de escalar la decision."
    )


def _analisis_grafico_deterministico(contexto: dict, modo_respuesta: str) -> str:
    modo_claro = normalizar_modo_respuesta(modo_respuesta) == MODO_RESPUESTA_CLARO
    metricas = _ordenar_metricas_para_pregunta(
        _metricas_contexto(contexto),
        contexto.get("pregunta", ""),
        contexto.get("titulo", ""),
    )
    if not metricas:
        if modo_claro:
            return (
                "_Respuesta generada con verificacion automatica para evitar datos inventados._\n\n"
                "**Analisis por datos**\n"
                "- La grafica usa los datos disponibles, pero no encontre una columna numerica clara para comparar.\n"
                "- Puedes mirar la tabla como una lista de grupos y valores, pero no conviene sacar una conclusion fuerte sin una metrica concreta.\n\n"
                "**Conclusion**\n"
                "- La mejor siguiente pregunta seria pedir una comparacion simple: personas, dinero movido, fallos o usuarios inactivos.\n\n"
                "**Recomendacion**\n"
                "- Pregunta, por ejemplo: `que grupo tiene mas usuarios inactivos` o `que ciudad mueve mas dinero`."
            )
        return (
            "_Interpretacion generada con control deterministico para preservar exactitud._\n\n"
            "**Analisis por datos**\n"
            "- La grafica se construyo con datos Gold certificados, pero no se identificaron columnas numericas suficientes para comparar extremos.\n"
            "- Usa la tabla de Datos Gold como fuente de verdad para revisar cada categoria visible.\n\n"
            "**Conclusion**\n"
            "- La respuesta debe leerse como una vista descriptiva, no como una prediccion automatica.\n"
            "- Para profundizar, pide una metrica concreta como usuarios, volumen, ticket o tasa de fallo.\n\n"
            "**Recomendacion**\n"
            "- Formula la siguiente pregunta con una metrica y una dimension especificas para obtener una lectura accionable."
        )

    titulo = contexto.get("titulo", "Analisis Gold")
    tipo_usado = contexto.get("tipo_usado", "grafico")
    tipo_solicitado = contexto.get("tipo_solicitado", tipo_usado)
    filas = len(contexto.get("etiquetas", []))
    principales = metricas[:5]
    principal = principales[0]
    recomendacion = (
        _recomendacion_grafico_clara(principales, contexto.get("pregunta", ""), titulo)
        if modo_claro
        else _recomendacion_grafico_deterministica(principales, contexto.get("pregunta", ""), titulo)
    )

    if modo_claro:
        intro = "_Respuesta en lenguaje claro, generada con verificacion automatica para evitar datos inventados._"
        analisis_intro = (
            f"- Esta grafica compara **{filas} grupos** de usuarios. "
            "La idea es ver donde hay mas oportunidad para una accion comercial este mes."
        )
        conclusion_extra = (
            "- En palabras simples: una buena campana no depende solo del grupo mas grande. "
            "Tambien hay que mirar quien dejo de usar la plataforma, donde fallan mas operaciones "
            "y que grupo mueve mas dinero."
        )
    else:
        intro = "_Interpretacion generada con control deterministico para preservar exactitud._"
        analisis_intro = (
            f"- La visualizacion **{titulo}** compara **{filas} categorias** con datos Gold certificados. "
            f"El tipo usado fue **{tipo_usado}** y el tipo solicitado fue **{tipo_solicitado}**."
        )
        conclusion_extra = (
            "- La lectura ejecutiva debe separar escala, monetizacion y friccion: una categoria puede liderar en usuarios "
            "pero no necesariamente en rentabilidad o calidad operativa."
        )

    analisis_lineas = [analisis_intro]
    if modo_claro:
        analisis_lineas.extend(_linea_extremo_clara(meta) for meta in principales)
    else:
        analisis_lineas.extend(_linea_extremo(meta) for meta in principales)
    if len(metricas) > len(principales):
        analisis_lineas.append(
            f"- Hay **{len(metricas) - len(principales)} metricas adicionales** en la tabla; "
            "se priorizaron las mas relacionadas con la pregunta del usuario."
        )

    if modo_claro:
        conclusion = (
            f"- Lo mas importante es que **{_etiqueta_clara(principal.get('max_label'))}** aparece como el grupo mas relevante en "
            f"**{_nombre_metrica_clara(str(principal.get('columna', '')))}** "
            f"con **{_formatear_valor_metrica(principal, 'max_val')}**. "
            f"En el extremo contrario esta **{_etiqueta_clara(principal.get('min_label'))}** "
            f"con **{_formatear_valor_metrica(principal, 'min_val')}**.\n"
            f"{conclusion_extra}"
        )
    else:
        conclusion = (
            f"- El hallazgo principal es que **{principal.get('max_label')}** lidera en "
            f"**{principal.get('nombre')}** con **{_formatear_valor_metrica(principal, 'max_val')}**, "
            f"mientras **{principal.get('min_label')}** marca el menor valor con "
            f"**{_formatear_valor_metrica(principal, 'min_val')}**.\n"
            f"{conclusion_extra}"
        )

    return (
        f"{intro}\n\n"
        "**Analisis por datos**\n"
        + "\n".join(analisis_lineas)
        + "\n\n**Conclusion**\n"
        + conclusion
        + "\n\n**Recomendacion**\n"
        f"- {recomendacion}\n"
        + (
            "- Despues de lanzar la campana, compara si bajan los usuarios inactivos o los fallos. "
            "Si mejora, puedes repetirla; si no mejora, cambia el beneficio o el publico objetivo."
            if modo_claro
            else "- Mide el resultado en el siguiente corte de datos Gold antes de convertir esta accion en una regla permanente."
        )
    )


def _analisis_grafico_completo(texto: str) -> bool:
    limpio = (texto or "").strip()
    if len(limpio) < 350:
        return False
    norm = _normalizar_texto(limpio)
    return all(token in norm for token in ("analisis", "conclusion", "recomendacion"))


def _extraer_periodo_contexto(contexto: dict) -> str:
    hechos = contexto.get("hechos_texto", "")
    match = re.search(r"periodo Gold disponible \(([^)]+)\)", hechos, flags=re.IGNORECASE)
    if match:
        return match.group(1)
    match = re.search(r"Periodo en la tabla:\s*([^\n]+)", hechos, flags=re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return ""


def _lectura_contexto_clara(contexto: dict) -> str:
    metricas = _ordenar_metricas_para_pregunta(
        _metricas_contexto(contexto),
        contexto.get("pregunta", ""),
        contexto.get("titulo", ""),
    )
    etiquetas = [_etiqueta_clara(e) for e in contexto.get("etiquetas", [])]
    lineas = [
        "**Verificacion simple de los datos**",
        f"- Se compararon **{len(etiquetas)} grupos**: {', '.join(etiquetas) if etiquetas else 'grupos disponibles'}.",
    ]
    periodo = _extraer_periodo_contexto(contexto)
    if periodo:
        lineas.append(f"- Periodo revisado: **{periodo}**.")
    for meta in metricas[:5]:
        lineas.append(
            f"- En **{_nombre_metrica_clara(str(meta.get('columna', '')))}**, "
            f"el grupo mas alto es **{_etiqueta_clara(meta.get('max_label'))}** "
            f"({_formatear_valor_metrica(meta, 'max_val')}) y el mas bajo es "
            f"**{_etiqueta_clara(meta.get('min_label'))}** "
            f"({_formatear_valor_metrica(meta, 'min_val')})."
        )
    return "\n".join(lineas)


def _extraer_extremos_desde_hechos(hechos_texto: str) -> dict[str, str]:
    extremos: dict[str, str] = {}
    min_match = re.search(r"mínimo\s+(.+?)\s+=", hechos_texto, flags=re.IGNORECASE)
    max_match = re.search(r"máximo\s+(.+?)\s+=", hechos_texto, flags=re.IGNORECASE)
    if min_match:
        extremos["minimo"] = min_match.group(1).strip()
    if max_match:
        extremos["maximo"] = max_match.group(1).strip()
    return extremos


def _validar_respuesta_contra_contexto(texto: str, contexto: dict) -> str:
    hechos = contexto.get("hechos_texto", "")
    extremos = _extraer_extremos_desde_hechos(hechos)
    etiquetas = [str(e) for e in contexto.get("etiquetas", [])]
    texto_norm = _normalizar_texto(texto)
    correcciones: list[str] = []
    bloques_texto = [
        _normalizar_texto(bloque)
        for bloque in re.split(r"[\n.;:]+", texto)
        if bloque.strip()
    ]

    min_terms = ("valor mas bajo", "valor minimo", "minimo", "menor valor", "menor porcentaje")
    max_terms = ("valor mas alto", "valor maximo", "maximo", "mayor valor", "mayor porcentaje", "lidera")
    for meta in _metricas_contexto(contexto):
        columna = str(meta.get("columna", ""))
        nombre = str(meta.get("nombre", columna))
        min_label = str(meta.get("min_label", ""))
        max_label = str(meta.get("max_label", ""))
        metric_terms = {
            _normalizar_texto(columna),
            _normalizar_texto(nombre),
            _normalizar_texto(nombre.replace("porcentaje", "pct")),
        }
        metric_terms = {term for term in metric_terms if term}
        etiquetas_erradas_min = [e for e in etiquetas if _normalizar_texto(e) != _normalizar_texto(min_label)]
        etiquetas_erradas_max = [e for e in etiquetas if _normalizar_texto(e) != _normalizar_texto(max_label)]

        for bloque in bloques_texto:
            if metric_terms and not any(term in bloque for term in metric_terms):
                continue
            if any(term in bloque for term in min_terms):
                for etiqueta in etiquetas_erradas_min:
                    if _normalizar_texto(etiqueta) in bloque:
                        correcciones.append(
                            f"Para **{nombre}**, el mínimo validado por código es **{min_label}** "
                            f"({_formatear_valor_contexto(meta.get('min_val'))}). "
                            f"Si la redacción menciona **{etiqueta}** como mínimo, debe corregirse."
                        )
                        break
            if any(term in bloque for term in max_terms):
                for etiqueta in etiquetas_erradas_max:
                    if _normalizar_texto(etiqueta) in bloque:
                        correcciones.append(
                            f"Para **{nombre}**, el máximo validado por código es **{max_label}** "
                            f"({_formatear_valor_contexto(meta.get('max_val'))}). "
                            f"Si la redacción menciona **{etiqueta}** como máximo, debe corregirse."
                        )
                        break

    minimo = extremos.get("minimo")
    if minimo and not correcciones:
        minimo_norm = _normalizar_texto(minimo)
        menciona_minimo = any(frase in texto_norm for frase in (
            "valor mas bajo", "valor más bajo", "minimo", "mínimo", "menor valor",
        ))
        for etiqueta in etiquetas:
            etiqueta_norm = _normalizar_texto(etiqueta)
            if etiqueta_norm != minimo_norm and menciona_minimo and etiqueta_norm in texto_norm:
                correcciones.append(
                    f"El mínimo validado por código es **{minimo}**. "
                    f"Si la redacción menciona **{etiqueta}** como mínimo, debe corregirse."
                )
                break

    maximo = extremos.get("maximo")
    if maximo and not correcciones:
        maximo_norm = _normalizar_texto(maximo)
        menciona_maximo = any(frase in texto_norm for frase in (
            "valor mas alto", "valor más alto", "maximo", "máximo", "mayor valor",
        ))
        for etiqueta in etiquetas:
            etiqueta_norm = _normalizar_texto(etiqueta)
            if etiqueta_norm != maximo_norm and menciona_maximo and etiqueta_norm in texto_norm:
                correcciones.append(
                    f"El máximo validado por código es **{maximo}**. "
                    f"Si la redacción menciona **{etiqueta}** como máximo, debe corregirse."
                )
                break

    if contexto.get("tipo_solicitado") == "line" and contexto.get("tipo_usado") == "bar":
        if "línea temporal" in texto.lower() or "linea temporal" in texto_norm:
            correcciones.append(
                "La visualización validada no es una línea temporal: se usaron **barras** "
                "porque los datos no contienen columna de fecha."
            )

    if not correcciones:
        return texto
    bloque = "\n".join(f"- {correccion}" for correccion in dict.fromkeys(correcciones))
    return f"{texto}\n\n**Control determinístico**\n{bloque}"


# ── Análisis de 3 partes para gráficos ───────────────────────────────────────
def _analizar_grafico_con_ollama(
    datos_texto: str,
    titulo: str,
    tipo: str,
    pregunta: str,
    modo_respuesta: str = MODO_RESPUESTA_PROFESIONAL,
    hechos_validados: str = "",
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

    if normalizar_modo_respuesta(modo_respuesta) == MODO_RESPUESTA_CLARO:
        system = f"""Eres un traductor de datos fintech para personas no expertas.
Recibes los datos EXACTOS de un gráfico de la capa Gold y debes contextualizarlo con lenguaje sencillo.

GRÁFICO: "{titulo}" (tipo: {tipo_nombre})
SOLICITUD ORIGINAL DEL USUARIO: "{pregunta}"

DATOS DEL GRÁFICO:
{datos_texto}

HECHOS VALIDADOS POR CÓDIGO (OBLIGATORIOS, NO LOS CONTRADIGAS):
{hechos_validados or "Sin hechos adicionales."}

PRODUCE EXACTAMENTE estas 3 secciones (obligatorias, en este orden):

**Analisis por datos**
Explica qué representan las filas/categorías y qué significa la métrica en palabras simples.
Menciona el valor más alto y el más bajo con cifras exactas.
Incluye una lectura fila por fila o por grupos principales, usando solo los valores entregados.

**Conclusion**
Compara las diferencias principales con ejemplos cotidianos.
Explica si hay concentración, dispersión o una señal de alerta y por qué importa para usuarios o negocio.
Separa claramente dato observado, cálculo derivado e hipótesis prudente.

**Recomendacion**
Da UNA recomendación concreta y fácil de ejecutar.
Indica qué grupo, ciudad, canal o categoría merece atención y cuál sería el beneficio esperado.
Incluye una segunda acción opcional de seguimiento o validación, sin inventar datos nuevos.

REGLAS ABSOLUTAS:
- Usa SOLO las cifras de los datos proporcionados. NUNCA inventes números.
- No uses jerga tecnica en la respuesta final: evita "Gold", "revenue", "metrica principal",
  "control deterministico", "capa", "dataset" o nombres internos de columnas.
- Traduce segmentos y conceptos: "student" = estudiantes, "young_professional" = jovenes profesionales,
  "revenue_por_usuario" = dinero promedio movido por persona, "tasa_fallo_pct" = operaciones con problemas.
- Español claro, entre 450 y 650 palabras en total.
- Cada sección debe tener al menos 2 párrafos cortos o bullets explicativos.
- Responde directamente con las 3 secciones, sin introducción."""
    else:
        system = f"""Eres un analista senior de negocio fintech colombiano con 15 años de experiencia.
Recibes los datos EXACTOS de un gráfico de la capa Gold y debes producir un análisis en 3 partes.

GRÁFICO: "{titulo}" (tipo: {tipo_nombre})
SOLICITUD ORIGINAL DEL USUARIO: "{pregunta}"

DATOS DEL GRÁFICO:
{datos_texto}

HECHOS VALIDADOS POR CÓDIGO (OBLIGATORIOS, NO LOS CONTRADIGAS):
{hechos_validados or "Sin hechos adicionales."}

PRODUCE EXACTAMENTE estas 3 secciones (obligatorias, en este orden):

**Analisis por datos**
Analiza CADA categoría/fila del gráfico individualmente con su valor exacto.
Identifica el mejor y el peor performer con cifras precisas.
Explica qué significa cada valor para el negocio fintech.
Si hay muchas filas, agrupa las intermedias sin omitir máximo, mínimo ni brecha.

**Conclusion**
Explica cómo se distribuyen los datos: ¿concentración? ¿dispersión? ¿outliers?
Calcula la brecha entre máximo y mínimo en términos porcentuales (X% superior/inferior).
Explica el patrón principal que revela el gráfico y su implicación operativa.
Aclara qué es dato observado, qué es cálculo derivado y qué es hipótesis.

**Recomendacion**
Resume los 2 hallazgos más importantes en cifras exactas.
Da UNA acción concreta priorizada y justificada con los datos.
Señala qué categoría merece atención inmediata y por qué.
Agrega una acción secundaria de monitoreo o validación, sin inventar indicadores externos.

REGLAS ABSOLUTAS:
- Usa SOLO las cifras de los datos proporcionados. NUNCA inventes números.
- Español profesional, entre 500 y 700 palabras en total.
- Cada sección debe tener al menos 2 párrafos cortos o bullets explicativos.
- Responde directamente con las 3 secciones, sin introducción."""

    try:
        resp = requests.post(
            f"{OLLAMA_BASE_URL}/api/chat",
            json={
                "model": OLLAMA_MODEL,
                "messages": [
                    {"role": "system", "content": system},
                    {
                        "role": "user",
                        "content": (
                            "Genera un análisis completo, suficientemente desarrollado y fiel a los datos. "
                            "No resumas de forma superficial. Usa los hechos validados como restricción obligatoria."
                        ),
                    },
                ],
                "stream": False,
                "options": {
                    "num_ctx": 8192,
                    "num_predict": 950,
                    "temperature": 0.1,
                    "top_p": 0.85,
                    "repeat_penalty": 1.05,
                },
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
    modo_respuesta: str = MODO_RESPUESTA_PROFESIONAL,
) -> str:
    """
    Genera el gráfico + análisis completo de 3 partes.
    Siempre retorna el gráfico; el análisis se añade si Ollama está disponible.
    """
    tipo_usado, notas = _ajustar_tipo_grafico_por_datos(tipo, df)
    contexto = _construir_contexto_dataframe(df, titulo, tipo, tipo_usado, pregunta, notas)
    _actualizar_contexto_estructurado(contexto)

    # 1. Generar imagen del gráfico
    if tipo_usado == "pie":
        chart_path = _chart_df_pie(df, titulo)
    elif tipo_usado == "line":
        chart_path = _chart_df_linea(df, titulo)
    else:
        chart_path = _chart_df_barras(df, titulo)

    # 2. Tabla de datos en bloque colapsable de código
    datos_texto = contexto["datos_texto"]
    if normalizar_modo_respuesta(modo_respuesta) == MODO_RESPUESTA_CLARO:
        tabla_md = f"**Datos usados para responder** _(fuente validada)_\n```\n{datos_texto}\n```"
        lectura_md = _lectura_contexto_clara(contexto)
    else:
        tabla_md = f"**Datos Gold** _(fuente certificada)_\n```\n{datos_texto}\n```"
        lectura_md = f"**Lectura validada por código**\n{contexto['hechos_texto']}"

    # 3. Análisis de Ollama (3 partes)
    analisis = _analizar_grafico_con_ollama(
        datos_texto,
        titulo,
        tipo_usado,
        pregunta,
        modo_respuesta,
        contexto["hechos_texto"],
    )

    analisis_deterministico = _analisis_grafico_deterministico(contexto, modo_respuesta)
    if analisis and _analisis_grafico_completo(analisis):
        analisis_validado = _validar_respuesta_contra_contexto(analisis, contexto)
        if analisis_validado == analisis:
            return f"{chart_path}\n\n{tabla_md}\n\n{lectura_md}\n\n---\n\n{analisis_validado}"
    return f"{chart_path}\n\n{tabla_md}\n\n{lectura_md}\n\n---\n\n{analisis_deterministico}"


# ── Motor principal: Ollama interpreta la petición y genera SQL dinámico ──────
def _grafico_ollama_inteligente(
    pregunta: str,
    tipo_forzado: str | None,
    modo_respuesta: str = MODO_RESPUESTA_PROFESIONAL,
) -> str | None:
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

        return _ejecutar_grafico_con_analisis(df, titulo, tipo, pregunta, modo_respuesta)

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
def _manejar_peticion_grafico(
    pregunta: str,
    modo_respuesta: str = MODO_RESPUESTA_PROFESIONAL,
) -> str:
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
                    return _ejecutar_grafico_con_analisis(df, titulo, tipo, pregunta, modo_respuesta)
            except Exception as e:
                print(f"  [ChartCertif] NLP local falló: {e}")

    # ── Capa 2: Ollama extrae intención → SQL certificado + análisis ─────────
    resultado = _grafico_ollama_inteligente(pregunta, tipo_forzado, modo_respuesta)
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
        analisis = _analizar_grafico_con_ollama(
            datos_texto,
            titulo_str,
            tipo_forzado or "bar",
            pregunta,
            modo_respuesta,
        )
        if analisis:
            # Reemplazar "Datos:" raw con bloque código + añadir análisis
            if m:
                resultado_capa3 = resultado_capa3.replace(
                    f"Datos:\n{m.group(1)}",
                    f"**Datos Gold** _(fuente certificada)_\n```\n{datos_texto}\n```"
                )
            return f"{resultado_capa3}\n\n---\n\n{analisis}"
    return resultado_capa3


def agent_query(
    pregunta: str,
    modo_respuesta: str = MODO_RESPUESTA_PROFESIONAL,
) -> str:
    """Punto de entrada principal para consultas al agente."""
    modo_respuesta = normalizar_modo_respuesta(modo_respuesta)
    _agregar_historial("user", pregunta)

    # ── Cierre satisfecho ─────────────────────────────────────────────────────
    if _es_cierre_satisfecho(pregunta):
        resultado = _respuesta_cierre_satisfecho()
        _agregar_historial("assistant", resultado)
        return _finalizar_respuesta(pregunta, resultado, "cierre_satisfecho", modo_respuesta)

    respuesta_control, ruta_control = _evaluar_control_preconsulta(pregunta)
    if respuesta_control is not None:
        _agregar_historial("assistant", respuesta_control)
        return _finalizar_respuesta(pregunta, respuesta_control, ruta_control, modo_respuesta)

    # ── Seguimiento fuerte sobre la respuesta anterior ────────────────────────
    if _debe_resolver_como_seguimiento(pregunta):
        resultado = _resolver_seguimiento_con_ollama(pregunta, modo_respuesta)
        _agregar_historial("assistant", resultado)
        return _finalizar_respuesta(pregunta, resultado, "seguimiento_contextual", modo_respuesta)

    # ── Gráficos ──────────────────────────────────────────────────────────────
    if _es_peticion_de_grafico(pregunta):
        _get_conn_duckdb()
        resultado = _manejar_peticion_grafico(pregunta, modo_respuesta)
        _agregar_historial("assistant", resultado)
        return _finalizar_respuesta(pregunta, resultado, "grafico", modo_respuesta)

    # ── Preguntas de seguimiento anafóricas ───────────────────────────────────
    if _es_pregunta_de_seguimiento(pregunta):
        resultado = _resolver_seguimiento_con_ollama(pregunta, modo_respuesta)
        _agregar_historial("assistant", resultado)
        return _finalizar_respuesta(pregunta, resultado, "seguimiento_anaforico", modo_respuesta)

    # ── Datos Gold (determinístico) ───────────────────────────────────────────
    if modo_respuesta == MODO_RESPUESTA_PROFESIONAL:
        respuesta_datos = _respuesta_datos_confiable(pregunta)
    else:
        respuesta_datos = _respuesta_datos_confiable(pregunta, modo_respuesta)
    if respuesta_datos is not None:
        _agregar_historial("assistant", respuesta_datos)
        return _finalizar_respuesta(pregunta, respuesta_datos, "gold_deterministico", modo_respuesta)

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
            return _finalizar_respuesta(pregunta, texto, "agente_libre", modo_respuesta)
    texto = str(respuesta)
    _agregar_historial("assistant", texto)
    return _finalizar_respuesta(pregunta, texto, "agente_libre", modo_respuesta)


def stream_agent_query(
    pregunta: str,
    modo_respuesta: str = MODO_RESPUESTA_PROFESIONAL,
):
    """
    Versión streaming de agent_query — genera chunks de texto progresivamente.

    Flujo:
      1. Gráficos       → yield resultado completo (string con ruta PNG)
      2. Seguimientos   → yield respuesta Ollama con historial de contexto
      3. Preguntas Gold → yield datos inmediatamente + stream análisis Ollama
      4. Preguntas libres → yield respuesta completa del agente
    """
    modo_respuesta = normalizar_modo_respuesta(modo_respuesta)
    _agregar_historial("user", pregunta)

    # ── Cierre satisfecho ─────────────────────────────────────────────────────
    if _es_cierre_satisfecho(pregunta):
        resultado = _respuesta_cierre_satisfecho()
        _agregar_historial("assistant", resultado)
        _registrar_traza_agente(pregunta, resultado, "cierre_satisfecho", modo_respuesta)
        yield resultado
        return

    respuesta_control, ruta_control = _evaluar_control_preconsulta(pregunta)
    if respuesta_control is not None:
        _agregar_historial("assistant", respuesta_control)
        _registrar_traza_agente(pregunta, respuesta_control, ruta_control, modo_respuesta)
        yield respuesta_control
        return

    # ── Seguimiento fuerte sobre la respuesta anterior ────────────────────────
    if _debe_resolver_como_seguimiento(pregunta):
        resultado = _resolver_seguimiento_con_ollama(pregunta, modo_respuesta)
        _agregar_historial("assistant", resultado)
        _registrar_traza_agente(pregunta, resultado, "seguimiento_contextual", modo_respuesta)
        yield resultado
        return

    # ── Gráficos: resultado directo, sin streaming ────────────────────────────
    if _es_peticion_de_grafico(pregunta):
        _get_conn_duckdb()
        resultado = _manejar_peticion_grafico(pregunta, modo_respuesta)
        _agregar_historial("assistant", resultado)
        _registrar_traza_agente(pregunta, resultado, "grafico", modo_respuesta)
        yield resultado
        return

    # ── Preguntas de seguimiento anafóricas ───────────────────────────────────
    if _es_pregunta_de_seguimiento(pregunta):
        resultado = _resolver_seguimiento_con_ollama(pregunta, modo_respuesta)
        _agregar_historial("assistant", resultado)
        _registrar_traza_agente(pregunta, resultado, "seguimiento_anaforico", modo_respuesta)
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
                if modo_respuesta == MODO_RESPUESTA_PROFESIONAL:
                    resultado_grafico = _respuesta_con_grafico(titulo, sql, pregunta)
                else:
                    resultado_grafico = _respuesta_con_grafico(titulo, sql, pregunta, modo_respuesta)
                _agregar_historial("assistant", resultado_grafico)
                _registrar_traza_agente(pregunta, resultado_grafico, "gold_deterministico_grafico", modo_respuesta)
                yield resultado_grafico
                return

    # ── Sin datos reconocibles → agent_query completo ─────────────────────────
    if datos_texto is None:
        resultado = agent_query(pregunta, modo_respuesta)
        yield resultado
        return

    # ── Mostrar datos inmediatamente (el usuario ve algo al instante) ─────────
    cabecera = (
        f"**Datos Gold**\n```text\n{datos_texto}\n```\n\n"
        f"**Análisis** _(Ollama · {OLLAMA_MODEL} · {etiqueta_modo_respuesta(modo_respuesta)})_\n\n"
    )
    yield cabecera

    # ── Stream de interpretación Ollama ───────────────────────────────────────
    if not _verificar_ollama():
        msg = "_Ollama no disponible. Los datos de arriba son los reales de la capa Gold._"
        _agregar_historial("assistant", cabecera + msg)
        _registrar_traza_agente(pregunta, cabecera + msg, "gold_stream_sin_ollama", modo_respuesta)
        yield msg
        return

    prompt_usuario = (
        f"Pregunta del usuario: {pregunta}\n\n"
        f"Datos reales de la capa Gold:\n{datos_texto}\n\n"
        f"{_instrucciones_interpretacion(modo_respuesta)}"
    )
    try:
        mensajes_stream = [{"role": "system", "content": _system_interpretacion(modo_respuesta)}]
        for m in _conversation_history[-4:]:
            mensajes_stream.append({"role": m["role"], "content": m["content"][:600]})
        mensajes_stream.append({"role": "user", "content": prompt_usuario})
        resp = requests.post(
            f"{OLLAMA_BASE_URL}/api/chat",
            json={
                "model": OLLAMA_MODEL,
                "messages": mensajes_stream,
                "stream": True,
                "options": {
                    "num_ctx": 8192,
                    "num_predict": 1100,
                    "temperature": 0.1,
                    "top_p": 0.85,
                    "repeat_penalty": 1.05,
                },
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
        _registrar_traza_agente(
            pregunta,
            cabecera + "".join(tokens_acumulados),
            "gold_stream_ollama",
            modo_respuesta,
        )
    except Exception as e:
        yield f"\n\n_Error al conectar con Ollama: {e}_"


def reset_agent():
    """Reinicia el agente y limpia el historial de conversación."""
    global _agent_instance, _last_response_context
    _agent_instance = None
    _last_response_context = None
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
