"""Intent routing for trusted business queries over Gold data."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Callable


IntentResult = tuple[str, str]
IntentBuilder = Callable[[str], IntentResult]
IntentMatcher = Callable[[str], bool]


def normalize_text(text: str) -> str:
    """Normalize user text for keyword-based intent routing."""
    normalized = unicodedata.normalize("NFKD", text or "")
    without_marks = "".join(
        char for char in normalized if not unicodedata.combining(char)
    )
    return without_marks.lower().strip()


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9_]+", text))


def _contains_any(text: str, keywords: tuple[str, ...]) -> bool:
    return any(keyword in text for keyword in keywords)


def _has_token(text: str, keyword: str) -> bool:
    return keyword in _tokens(text)


def is_data_question(question: str) -> bool:
    """Return True when a question should be answered from Gold data."""
    text = normalize_text(question)
    text_keywords = (
        "kpi", "metric", "numero", "cuanto", "total", "promedio", "ticket",
        "volumen", "monto", "transaccion", "usuario", "segment", "ciudad",
        "fallo", "failed", "tasa", "ranking", "top", "resumen", "ejecutivo",
        "merchant", "comercio", "categoria", "canal", "device", "dispositivo",
        "evento", "diario", "fecha", "daily", "gold", "negocio",
        # alertas y diagnóstico
        "alerta", "alertas", "diagnostico", "anomalia", "salud", "estado",
        # comparación de períodos
        "variacion", "variacion", "periodo", "semana", "anterior", "comparar",
        "evolucion", "cambio", "crecio", "bajo", "sube", "baja",
        # churn y retención
        "churn", "inactivo", "retencion", "abandono",
        # balance y riesgo
        "balance", "saldo", "riesgo",
        # campañas, estrategia y acciones de negocio
        "campana", "campanas", "lanzar", "lanzaria", "lanzarias", "lanzaría",
        "promocion", "promociones", "estrategia", "iniciativa", "accion",
        "inversion", "oportunidad", "recomendar", "recomendacion",
        "objetivo", "meta", "plan", "targeting",
    )
    return _contains_any(text, text_keywords) or _has_token(text, "dia")


def _static_intent(sql: str, title: str) -> IntentBuilder:
    return lambda _: (sql, title)


def _city_intent(text: str) -> IntentResult:
    by_fallo = _contains_any(text, ("fallo", "failed", "rechazo", "tasa"))
    order_by = "tasa_fallo_pct DESC" if by_fallo else "revenue_por_usuario DESC"
    title = "Ciudades con mayor fricción de pago" if by_fallo else "Potencial de crecimiento por ciudad"
    return (f"""
            SELECT city,
                   COUNT(*) AS usuarios,
                   ROUND(SUM(total_amount_cop), 0) AS volumen_cop,
                   ROUND(SUM(total_amount_cop)/COUNT(*), 0) AS revenue_por_usuario,
                   ROUND(AVG(avg_ticket), 0) AS ticket_promedio,
                   ROUND(AVG(failure_rate) * 100, 1) AS tasa_fallo_pct,
                   COUNT(CASE WHEN days_since_last_tx > 30 THEN 1 END) AS inactivos
            FROM gold_user_360
            GROUP BY city
            ORDER BY {order_by}
        """, title)


@dataclass(frozen=True)
class IntentRule:
    """Single business intent rule used by the deterministic router."""

    name: str
    matches: IntentMatcher
    build: IntentBuilder


INTENT_RULES: tuple[IntentRule, ...] = (
    IntentRule(
        "inactive",
        lambda text: _contains_any(
            text, ("inactiv", "sin transaccionar", "dias sin", "30 dia", "sin tx",
                   "no transacciona", "no han transaccionado")
        ),
        _static_intent("""
            SELECT
                user_segment,
                COUNT(*)                                  AS usuarios_inactivos,
                ROUND(AVG(days_since_last_tx), 0)         AS promedio_dias_sin_tx,
                MAX(days_since_last_tx)                   AS max_dias_sin_tx,
                ROUND(AVG(avg_ticket), 0)                 AS ticket_historico,
                ROUND(AVG(failure_rate)*100, 1)           AS tasa_fallo_pct
            FROM gold_user_360
            WHERE days_since_last_tx > 30
            GROUP BY user_segment
            ORDER BY usuarios_inactivos DESC
        """, "Usuarios inactivos más de 30 días por segmento"),
    ),
    IntentRule(
        "segment",
        lambda text: "segment" in text or "segmento" in text or "rentable" in text,
        _static_intent("""
            SELECT user_segment,
                   COUNT(*)                                          AS usuarios,
                   ROUND(SUM(total_amount_cop), 0)                  AS volumen_cop,
                   ROUND(SUM(total_amount_cop)/COUNT(*), 0)         AS revenue_por_usuario,
                   ROUND(AVG(avg_ticket), 0)                        AS ticket_promedio,
                   ROUND(AVG(failure_rate) * 100, 1)                AS tasa_fallo_pct,
                   ROUND(AVG(days_since_last_tx), 0)                AS dias_promedio_sin_tx
            FROM gold_user_360
            GROUP BY user_segment
            ORDER BY revenue_por_usuario DESC
        """, "Rentabilidad por segmento"),
    ),
    IntentRule(
        "city",
        lambda text: "ciudad" in text or "city" in text or "crecimiento" in text,
        _city_intent,
    ),
    IntentRule(
        "failure",
        lambda text: _contains_any(text, ("fallo", "failed", "rechazo", "friccion")),
        _static_intent("""
            SELECT user_segment,
                   COUNT(*)                                          AS usuarios,
                   SUM(failed_transactions)                         AS transacciones_fallidas,
                   ROUND(AVG(failure_rate) * 100, 1)                AS tasa_fallo_pct,
                   ROUND(SUM(total_amount_cop)/COUNT(*), 0)         AS revenue_por_usuario
            FROM gold_user_360
            GROUP BY user_segment
            ORDER BY tasa_fallo_pct DESC
        """, "Análisis de fallos por segmento"),
    ),
    IntentRule(
        "top_users",
        lambda text: "usuario" in text and _contains_any(
            text, ("top", "ranking", "gasto", "mayor", "mas")
        ),
        _static_intent("""
            SELECT user_id,
                   user_segment,
                   city,
                   total_transactions,
                   ROUND(total_amount_cop, 0) AS total_amount_cop,
                   ROUND(avg_ticket, 0)        AS avg_ticket,
                   top_merchant,
                   top_category
            FROM gold_user_360
            ORDER BY total_amount_cop DESC
            LIMIT 10
        """, "Top 10 usuarios por gasto"),
    ),
    IntentRule(
        "merchant",
        lambda text: _contains_any(text, ("merchant", "comercio", "alianza", "aliado")),
        _static_intent("""
            SELECT top_merchant,
                   COUNT(*) AS usuarios,
                   ROUND(SUM(total_amount_cop), 0) AS volumen_cop,
                   ROUND(SUM(total_amount_cop)/COUNT(*), 0) AS ticket_por_usuario,
                   ROUND(COUNT(*)*100.0/(SELECT COUNT(*) FROM gold_user_360), 1) AS penetracion_pct,
                   ROUND(AVG(failure_rate)*100, 1) AS tasa_fallo_pct
            FROM gold_user_360
            WHERE top_merchant IS NOT NULL
            GROUP BY top_merchant
            ORDER BY volumen_cop DESC
            LIMIT 10
        """, "Comercios con mayor potencial de alianza"),
    ),
    IntentRule(
        "category",
        lambda text: "categoria" in text or "category" in text,
        _static_intent("""
            SELECT top_category,
                   COUNT(*) AS usuarios,
                   ROUND(SUM(total_amount_cop), 0) AS volumen_cop,
                   ROUND(SUM(total_amount_cop)/COUNT(*), 0) AS revenue_por_usuario,
                   ROUND(AVG(failure_rate)*100, 1) AS tasa_fallo_pct
            FROM gold_user_360
            WHERE top_category IS NOT NULL
            GROUP BY top_category
            ORDER BY volumen_cop DESC
            LIMIT 10
        """, "Categorías con mayor volumen"),
    ),
    IntentRule(
        "channel",
        lambda text: "canal" in text or "channel" in text,
        _static_intent("""
            SELECT preferred_channel,
                   COUNT(*) AS usuarios,
                   ROUND(COUNT(*)*100.0/(SELECT COUNT(*) FROM gold_user_360), 1) AS share_pct,
                   ROUND(SUM(total_amount_cop), 0) AS volumen_cop,
                   ROUND(SUM(total_amount_cop)/COUNT(*), 0) AS revenue_por_usuario,
                   ROUND(AVG(failure_rate)*100, 1) AS tasa_fallo_pct
            FROM gold_user_360
            GROUP BY preferred_channel
            ORDER BY volumen_cop DESC
        """, "Distribución y rentabilidad por canal"),
    ),
    IntentRule(
        "device",
        lambda text: "device" in text or "dispositivo" in text,
        _static_intent("""
            SELECT preferred_device,
                   COUNT(*) AS usuarios,
                   ROUND(COUNT(*)*100.0/(SELECT COUNT(*) FROM gold_user_360), 1) AS share_pct,
                   ROUND(SUM(total_amount_cop), 0) AS volumen_cop,
                   ROUND(SUM(total_amount_cop)/COUNT(*), 0) AS revenue_por_usuario
            FROM gold_user_360
            GROUP BY preferred_device
            ORDER BY usuarios DESC
        """, "Distribución por dispositivo"),
    ),
    IntentRule(
        "event",
        lambda text: "evento" in text or "event" in text,
        _static_intent("""
            SELECT event,
                   count,
                   success_count,
                   failed_count,
                   ROUND(pct_of_total, 2) AS pct_of_total,
                   ROUND(failed_count * 100.0 / NULLIF(count, 0), 1) AS tasa_fallo_pct
            FROM gold_event_summary
            ORDER BY count DESC
        """, "Resumen por tipo de evento"),
    ),
    IntentRule(
        "daily",
        lambda text: _contains_any(text, ("diario", "fecha", "daily", "tendencia", "evolucion")) or _has_token(text, "dia"),
        _static_intent("""
            SELECT date,
                   total_events,
                   total_transactions,
                   ROUND(total_amount_cop, 0) AS total_amount_cop,
                   failed_count,
                   unique_users,
                   ROUND(failed_count*100.0/NULLIF(total_transactions,0), 1) AS tasa_fallo_pct
            FROM gold_daily_metrics
            ORDER BY date DESC
            LIMIT 35
        """, "Tendencia diaria últimos 35 días"),
    ),
    IntentRule(
        "business_kpis",
        lambda text: _contains_any(
            text, ("total", "volumen", "ticket", "transaccion", "monto", "negocio", "plataforma")
        ),
        _static_intent("""
            SELECT COUNT(*) AS usuarios,
                   ROUND(SUM(total_transactions), 0) AS total_transacciones,
                   ROUND(SUM(total_amount_cop)/1e6, 2) AS volumen_M_cop,
                   ROUND(SUM(total_amount_usd), 2) AS volumen_usd,
                   ROUND(AVG(avg_ticket), 0) AS ticket_promedio,
                   ROUND(AVG(failure_rate) * 100, 1) AS tasa_fallo_pct,
                   COUNT(CASE WHEN days_since_last_tx > 30 THEN 1 END) AS inactivos_30d
            FROM gold_user_360
        """, "KPIs generales del negocio"),
    ),
)


def sql_for_intent(question: str) -> IntentResult | None:
    """Resolve a business question to a deterministic Gold SQL query."""
    text = normalize_text(question)

    if _contains_any(text, ("resumen", "ejecutivo", "kpi", "indicador", "indicadores")):
        return None

    for rule in INTENT_RULES:
        if rule.matches(text):
            return rule.build(text)

    return None


# ════════════════════════════════════════════════════════════════════════════
# CHART CERTIFICATION SYSTEM
# Fórmulas SQL únicas y validadas para gráficos de la capa Gold.
# Garantizan que los gráficos usen exactamente las mismas fórmulas
# que el dashboard, eliminando discrepancias por interpretación del LLM.
# ════════════════════════════════════════════════════════════════════════════

# ── Métricas certificadas ─────────────────────────────────────────────────────
# NOTA CRÍTICA sobre avg_ticket vs revenue_usuario:
#   avg_ticket          = promedio de amount_cop por TRANSACCIÓN individual (pipeline_gold.py línea 120)
#   revenue_usuario     = SUM(total_amount_cop)/COUNT(*) = gasto acumulado total por usuario
#   Ambas usan "ticket" en su nombre pero difieren ~4x (por ~4 transacciones/usuario promedio).
#   El dashboard siempre muestra revenue_usuario para "ticket/revenue por usuario".

# Formato: clave → (expresión SQL, alias en resultado, descripción semántica)
METRICAS_GOLD: dict[str, tuple[str, str, str]] = {
    "revenue_usuario":    (
        "ROUND(SUM(total_amount_cop)/COUNT(*), 0)",
        "revenue_por_usuario",
        "gasto total acumulado por usuario (misma fórmula que dashboard)",
    ),
    "volumen_total":      (
        "ROUND(SUM(total_amount_cop)/1e6, 2)",
        "volumen_M_cop",
        "volumen total en millones COP",
    ),
    "ticket_transaccion": (
        "ROUND(AVG(avg_ticket), 0)",
        "ticket_promedio_tx",
        "monto promedio por transacción individual (NO por usuario)",
    ),
    "usuarios":           (
        "COUNT(*)",
        "usuarios",
        "número de usuarios",
    ),
    "tasa_fallo":         (
        "ROUND(AVG(failure_rate)*100, 1)",
        "tasa_fallo_pct",
        "porcentaje promedio de transacciones fallidas",
    ),
    "balance_promedio":   (
        "ROUND(AVG(balance_current), 0)",
        "balance_promedio",
        "balance actual promedio del usuario",
    ),
    "balance_total":      (
        "ROUND(SUM(balance_current), 0)",
        "balance_total",
        "balance total acumulado del grupo",
    ),
    "inactivos_30d":      (
        "COUNT(CASE WHEN days_since_last_tx > 30 THEN 1 END)",
        "inactivos_30d",
        "usuarios sin transaccionar en los últimos 30 días",
    ),
    "inactivos_60d":      (
        "COUNT(CASE WHEN days_since_last_tx > 60 THEN 1 END)",
        "inactivos_60d",
        "usuarios sin transaccionar en los últimos 60 días",
    ),
    "pct_inactivos_30d":  (
        "ROUND(COUNT(CASE WHEN days_since_last_tx > 30 THEN 1 END)*100.0/COUNT(*), 1)",
        "pct_inactivos_30d",
        "porcentaje de usuarios inactivos 30+ días",
    ),
    "transacciones_prom": (
        "ROUND(AVG(total_transactions), 1)",
        "tx_promedio",
        "número promedio de transacciones por usuario",
    ),
    "transacciones_total":(
        "SUM(total_transactions)",
        "transacciones_total",
        "total de transacciones del grupo",
    ),
    "fallos_totales":     (
        "SUM(failed_transactions)",
        "fallos_totales",
        "total de transacciones fallidas",
    ),
    "dias_sin_tx":        (
        "ROUND(AVG(days_since_last_tx), 0)",
        "dias_promedio_sin_tx",
        "días promedio desde la última transacción",
    ),
    "revenue_usd":        (
        "ROUND(SUM(total_amount_usd)/COUNT(*), 2)",
        "revenue_usd_usuario",
        "revenue por usuario en USD",
    ),
    "eventos_prom":       (
        "ROUND(AVG(total_events), 1)",
        "eventos_promedio",
        "número promedio de eventos por usuario",
    ),
}

NOMBRES_METRICA: dict[str, str] = {
    "revenue_usuario":    "Revenue por Usuario (COP)",
    "volumen_total":      "Volumen Total (M COP)",
    "ticket_transaccion": "Ticket por Transacción",
    "usuarios":           "Usuarios",
    "tasa_fallo":         "Tasa de Fallo (%)",
    "balance_promedio":   "Balance Promedio",
    "balance_total":      "Balance Total",
    "inactivos_30d":      "Inactivos 30 días",
    "inactivos_60d":      "Inactivos 60 días",
    "pct_inactivos_30d":  "% Inactivos 30d",
    "transacciones_prom": "Transacciones Promedio",
    "transacciones_total":"Transacciones Totales",
    "fallos_totales":     "Fallos Totales",
    "dias_sin_tx":        "Días sin Transacción",
    "revenue_usd":        "Revenue por Usuario (USD)",
    "eventos_prom":       "Eventos Promedio",
}

# ── Dimensiones certificadas ──────────────────────────────────────────────────
# Formato: clave → (columna_sql, filtro_where_adicional)
DIMENSIONES_GOLD: dict[str, tuple[str, str]] = {
    "merchant":    ("top_merchant",      "WHERE top_merchant IS NOT NULL"),
    "ciudad":      ("city",              ""),
    "segmento":    ("user_segment",      ""),
    "canal":       ("preferred_channel", "WHERE preferred_channel IS NOT NULL"),
    "dispositivo": ("preferred_device",  "WHERE preferred_device IS NOT NULL"),
    "categoria":   ("top_category",      "WHERE top_category IS NOT NULL"),
}

NOMBRES_DIMENSION: dict[str, str] = {
    "merchant":    "Merchant",
    "ciudad":      "Ciudad",
    "segmento":    "Segmento",
    "canal":       "Canal",
    "dispositivo": "Dispositivo",
    "categoria":   "Categoría",
}

# ── Mapeo NLP → claves certificadas ──────────────────────────────────────────
NLP_A_DIMENSION: dict[str, str] = {
    # merchants / comercios
    "merchant": "merchant",  "merchants": "merchant",
    "comercio": "merchant",  "comercios": "merchant",
    "tienda":   "merchant",  "tiendas":   "merchant",
    "aliado":   "merchant",  "aliados":   "merchant",
    # ciudades
    "ciudad":   "ciudad",    "ciudades":  "ciudad",    "city": "ciudad",
    # segmentos
    "segmento": "segmento",  "segmentos": "segmento",  "segment": "segmento",
    # canales
    "canal":    "canal",     "canales":   "canal",     "channel": "canal",
    # dispositivos
    "dispositivo": "dispositivo", "dispositivos": "dispositivo",
    "device":   "dispositivo",   "movil":   "dispositivo",
    "mobile":   "dispositivo",   "app":     "dispositivo",
    # categorías
    "categoria":  "categoria", "categorias": "categoria",
    "category":   "categoria",
}

# CRÍTICO: "ticket" y "revenue" sin "transaccion" → revenue_usuario (fórmula del dashboard)
# Solo "ticket_transaccion" o "ticket_promedio" → ticket_transaccion (avg_ticket)
NLP_A_METRICA: dict[str, str] = {
    # Revenue por usuario (dashboard formula = SUM/COUNT)
    "revenue":            "revenue_usuario",
    "revenues":           "revenue_usuario",
    "ingreso":            "revenue_usuario",
    "ingresos":           "revenue_usuario",
    "monto":              "revenue_usuario",
    "montos":             "revenue_usuario",
    "ticket":             "revenue_usuario",   # "ticket" genérico → revenue por usuario
    "tickets":            "revenue_usuario",
    "gasto":              "revenue_usuario",
    "gastos":             "revenue_usuario",
    "cop":                "volumen_total",
    # Volumen total
    "volumen":            "volumen_total",
    "amount":             "volumen_total",
    # Ticket por transacción individual (avg_ticket)
    "ticket_transaccion": "ticket_transaccion",
    "ticket_promedio":    "ticket_transaccion",
    "avg_ticket":         "ticket_transaccion",
    # Usuarios
    "usuario":            "usuarios",
    "usuarios":           "usuarios",
    "user":               "usuarios",
    "users":              "usuarios",
    "clientes":           "usuarios",
    "cantidad":           "usuarios",
    "personas":           "usuarios",
    # Fallos
    "fallo":              "tasa_fallo",
    "fallos":             "tasa_fallo",
    "error":              "tasa_fallo",
    "errores":            "tasa_fallo",
    "tasa_fallo":         "tasa_fallo",
    "rechazos":           "tasa_fallo",
    "fracaso":            "tasa_fallo",
    "fallido":            "tasa_fallo",
    # Balance / saldo
    "balance":            "balance_promedio",
    "saldo":              "balance_promedio",
    "saldos":             "balance_promedio",
    # Inactivos / churn
    "inactivo":           "inactivos_30d",
    "inactivos":          "inactivos_30d",
    "churn":              "inactivos_30d",
    "dormido":            "inactivos_30d",
    "abandono":           "inactivos_30d",
    "retencion":          "pct_inactivos_30d",
    # Transacciones
    "transaccion":        "transacciones_prom",
    "transacciones":      "transacciones_prom",
    # Días sin transacción
    "dias":               "dias_sin_tx",
    "recencia":           "dias_sin_tx",
    # Eventos
    "eventos":            "eventos_prom",
    "evento":             "eventos_prom",
}

# Bigramas especiales que tienen prioridad sobre tokens individuales
_BIGRAMAS_NLP: list[tuple[str, str, str]] = [
    # (texto_a_buscar, tipo, clave_certificada)
    ("ticket por usuario",      "metrica",   "revenue_usuario"),
    ("ticket_por_usuario",      "metrica",   "revenue_usuario"),
    ("revenue por usuario",     "metrica",   "revenue_usuario"),
    ("monto por usuario",       "metrica",   "revenue_usuario"),
    ("gasto por usuario",       "metrica",   "revenue_usuario"),
    ("ticket promedio",         "metrica",   "ticket_transaccion"),
    ("ticket por transaccion",  "metrica",   "ticket_transaccion"),
    ("tasa de fallo",           "metrica",   "tasa_fallo"),
    ("tasa fallo",              "metrica",   "tasa_fallo"),
    ("inactivos 30",            "metrica",   "inactivos_30d"),
    ("inactivos 60",            "metrica",   "inactivos_60d"),
]


def extraer_intencion_grafico(text: str) -> tuple[str | None, str | None]:
    """
    Extrae (dimension_key, metrica_key) de texto normalizado.
    Usa bigramas primero para resolver ambigüedades semánticas,
    luego tokens individuales.
    Retorna (None, None) si no puede identificar alguno de los dos.
    """
    # Paso 1: buscar bigramas con prioridad
    metrica_bigrama = None
    for bigrama, tipo, clave in _BIGRAMAS_NLP:
        if bigrama in text and tipo == "metrica":
            metrica_bigrama = clave
            break

    # Paso 2: extraer dimensión por tokens
    tokens = set(re.findall(r"[a-z0-9_]+", text))
    dim_encontrada: str | None = None
    for token in tokens:
        if token in NLP_A_DIMENSION:
            dim_encontrada = NLP_A_DIMENSION[token]
            break

    # Paso 3: extraer métrica por tokens — la más específica gana sobre "usuarios" genérico
    # Prioridad: métricas específicas (2) > "usuarios" genérico (1)
    metrica_encontrada: str | None = metrica_bigrama
    if metrica_encontrada is None:
        metrica_prioridad = 0
        for token in tokens:
            if token in NLP_A_METRICA:
                clave = NLP_A_METRICA[token]
                prioridad = 1 if clave == "usuarios" else 2
                if prioridad > metrica_prioridad:
                    metrica_encontrada = clave
                    metrica_prioridad = prioridad

    # Default: si se encontró dimensión pero no métrica → usuarios es lo más natural
    if dim_encontrada and metrica_encontrada is None:
        metrica_encontrada = "usuarios"

    return dim_encontrada, metrica_encontrada


def construir_sql_grafico(
    dim_key: str,
    metrica_key: str,
    tipo: str,
    top_n: int = 10,
) -> tuple[str, str] | None:
    """
    Construye SQL certificado para un gráfico dado dimensión, métrica y tipo.
    Usa exclusivamente fórmulas de METRICAS_GOLD — nunca genera SQL libre.

    Returns:
        (sql, titulo) o None si los parámetros son inválidos.
    """
    if dim_key not in DIMENSIONES_GOLD or metrica_key not in METRICAS_GOLD:
        return None

    col_dim, where_clause = DIMENSIONES_GOLD[dim_key]
    sql_expr, alias, _ = METRICAS_GOLD[metrica_key]

    limit = 8 if tipo == "pie" else top_n
    where_part = f"\n{where_clause}" if where_clause else ""

    # Para barras: añadir usuarios como segunda métrica de contexto
    if tipo == "bar" and metrica_key not in ("usuarios",):
        sql = (
            f"SELECT {col_dim},\n"
            f"       {sql_expr} AS {alias},\n"
            f"       COUNT(*) AS usuarios\n"
            f"FROM gold_user_360"
            f"{where_part}\n"
            f"GROUP BY {col_dim}\n"
            f"ORDER BY {alias} DESC\n"
            f"LIMIT {limit}"
        )
    else:
        sql = (
            f"SELECT {col_dim},\n"
            f"       {sql_expr} AS {alias}\n"
            f"FROM gold_user_360"
            f"{where_part}\n"
            f"GROUP BY {col_dim}\n"
            f"ORDER BY {alias} DESC\n"
            f"LIMIT {limit}"
        )

    dim_nombre     = NOMBRES_DIMENSION.get(dim_key, dim_key)
    metrica_nombre = NOMBRES_METRICA.get(metrica_key, metrica_key)
    titulo = f"{metrica_nombre} por {dim_nombre}"

    return sql, titulo
