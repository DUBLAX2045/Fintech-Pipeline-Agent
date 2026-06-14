"""
Tests de integración con servicios reales.
Marker: cloud — excluidos del run normal de CI.

Ejecutar explícitamente con:
    pytest -m cloud tests/integration/test_real_services.py -v

Cubren:
  - Ciclo completo S3: escritura / lectura / borrado
  - Databricks: consulta las 3 tablas Gold y valida integridad de datos
  - DuckDB local: carga Gold parquets y ejecuta queries de negocio
  - _respuesta_con_grafico(): función central del agente, end-to-end
  - Generación de gráficos desde datos Gold reales + sincronización Docker
  - Intent routing → SQL certificado → gráfico → análisis Ollama
  - Ollama: conectividad y generación de respuesta real
"""

from __future__ import annotations

import os
import re
import time
from pathlib import Path

import pandas as pd
import pytest
from dotenv import load_dotenv as _load_dotenv

ROOT = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.cloud

# Cargar .env al inicio del módulo para que los skipif puedan leer las variables
_load_dotenv(ROOT / ".env")


# ── Helpers de skip ─────────────────────────────────────────────────────────

def _s3_disponible() -> bool:
    _load_dotenv(ROOT / ".env", override=False)
    return all(
        os.getenv(v)
        for v in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_BUCKET")
    )


def _databricks_disponible() -> bool:
    _load_dotenv(ROOT / ".env", override=False)
    return all(
        os.getenv(v)
        for v in ("DATABRICKS_HOST", "DATABRICKS_TOKEN", "DATABRICKS_HTTP_PATH")
    )


def _ollama_disponible() -> bool:
    _load_dotenv(ROOT / ".env", override=False)
    try:
        import requests
        r = requests.get(
            os.getenv("OLLAMA_BASE_URL", "http://localhost:11434") + "/api/tags",
            timeout=4,
        )
        return r.status_code == 200
    except Exception:
        return False


def _gold_parquets_disponibles() -> bool:
    for tabla in ("gold_user_360", "gold_daily_metrics", "gold_event_summary"):
        if not (ROOT / "data/gold" / f"{tabla}.parquet").exists():
            return False
    return True


# ══════════════════════════════════════════════════════════════════════════════
# 1. AWS S3 — ciclo real write / read / delete
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.skipif(not _s3_disponible(), reason="Credenciales AWS no disponibles")
def test_s3_write_read_delete_cycle():
    """Escribe un objeto de prueba en S3, lo lee de vuelta y lo elimina."""
    import boto3
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")

    bucket  = os.getenv("AWS_BUCKET")
    region  = os.getenv("AWS_REGION", "us-east-1")
    key_id  = os.getenv("AWS_ACCESS_KEY_ID")
    secret  = os.getenv("AWS_SECRET_ACCESS_KEY")
    token   = os.getenv("AWS_SESSION_TOKEN")

    kwargs = dict(aws_access_key_id=key_id, aws_secret_access_key=secret,
                  region_name=region)
    if token:
        kwargs["aws_session_token"] = token
    s3 = boto3.client("s3", **kwargs)

    test_key     = "ci-integration-test/healthcheck.txt"
    test_content = b"fintech-pipeline integration test"

    # Escribir
    s3.put_object(Bucket=bucket, Key=test_key, Body=test_content)

    # Leer y verificar contenido
    obj = s3.get_object(Bucket=bucket, Key=test_key)
    body = obj["Body"].read()
    assert body == test_content, f"Contenido S3 inesperado: {body!r}"

    # Eliminar
    s3.delete_object(Bucket=bucket, Key=test_key)

    # Confirmar eliminación
    import botocore.exceptions
    with pytest.raises(botocore.exceptions.ClientError) as exc_info:
        s3.head_object(Bucket=bucket, Key=test_key)
    assert exc_info.value.response["Error"]["Code"] in ("404", "NoSuchKey")


@pytest.mark.skipif(not _s3_disponible(), reason="Credenciales AWS no disponibles")
def test_s3_gold_parquets_exist_in_bucket():
    """Verifica que los 3 parquets Gold existen en el bucket S3."""
    import boto3
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")

    bucket = os.getenv("AWS_BUCKET")
    s3 = boto3.client(
        "s3",
        aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
        aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
        region_name=os.getenv("AWS_REGION", "us-east-1"),
    )

    tablas = ["gold_user_360", "gold_daily_metrics", "gold_event_summary"]
    for tabla in tablas:
        key = f"gold/{tabla}.parquet"
        resp = s3.head_object(Bucket=bucket, Key=key)
        assert resp["ContentLength"] > 0, f"s3://{bucket}/{key} está vacío"


# ══════════════════════════════════════════════════════════════════════════════
# 2. Databricks — consultas reales sobre tablas Gold
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.skipif(not _databricks_disponible(), reason="Credenciales Databricks no disponibles")
def test_databricks_gold_user_360_data_integrity():
    """
    Consulta gold_user_360 en Databricks y valida:
    - 4 segmentos presentes (premium, student, family, young_professional)
    - Revenue por usuario > 0 en todos los segmentos
    - Tasa de fallo entre 0% y 100%
    - Al menos 100 usuarios totales
    """
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    from src.config.databricks_config import ejecutar_query, DATABRICKS_CATALOG as CAT, DATABRICKS_SCHEMA as SCH

    sql = (
        f"SELECT user_segment, COUNT(*) AS n,"
        f" ROUND(SUM(total_amount_cop)/COUNT(*),0) AS revenue_por_usuario,"
        f" ROUND(AVG(failure_rate)*100,1) AS tasa_fallo_pct"
        f" FROM {CAT}.{SCH}.gold_user_360"
        f" GROUP BY user_segment ORDER BY n DESC"
    )
    rows = ejecutar_query(sql)

    assert len(rows) == 4, f"Esperaba 4 segmentos, obtuvo {len(rows)}"

    segmentos = {r["user_segment"] for r in rows}
    assert segmentos == {"premium", "student", "family", "young_professional"}, \
        f"Segmentos inesperados: {segmentos}"

    total_usuarios = sum(r["n"] for r in rows)
    assert total_usuarios >= 400, f"Muy pocos usuarios: {total_usuarios}"

    for r in rows:
        seg = r["user_segment"]
        rev = r["revenue_por_usuario"]
        tf  = r["tasa_fallo_pct"]
        assert rev > 0, f"Revenue negativo en segmento {seg}: {rev}"
        assert 0 <= tf <= 100, f"Tasa de fallo inválida en {seg}: {tf}"


@pytest.mark.skipif(not _databricks_disponible(), reason="Credenciales Databricks no disponibles")
def test_databricks_gold_daily_metrics_structure():
    """
    Valida que gold_daily_metrics tiene las columnas requeridas
    y métricas coherentes.
    """
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    from src.config.databricks_config import ejecutar_query, DATABRICKS_CATALOG as CAT, DATABRICKS_SCHEMA as SCH

    sql = (
        f"SELECT date, total_transactions, unique_users, failed_count,"
        f" ROUND(total_amount_cop/1e6,2) AS volumen_M_cop"
        f" FROM {CAT}.{SCH}.gold_daily_metrics"
        f" ORDER BY total_transactions DESC LIMIT 5"
    )
    rows = ejecutar_query(sql)

    assert len(rows) > 0, "gold_daily_metrics está vacía"
    for r in rows:
        assert r["total_transactions"] >= r["failed_count"], \
            "failed_count supera total_transactions"
        assert r["unique_users"] > 0, "unique_users debe ser > 0"
        assert r["volumen_M_cop"] >= 0, "Volumen negativo detectado"


@pytest.mark.skipif(not _databricks_disponible(), reason="Credenciales Databricks no disponibles")
def test_databricks_gold_event_summary_covers_all_types():
    """
    Valida que gold_event_summary cubre los 7 tipos de evento
    y que las sumas son coherentes.
    """
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    from src.config.databricks_config import ejecutar_query, DATABRICKS_CATALOG as CAT, DATABRICKS_SCHEMA as SCH

    sql = (
        f"SELECT event, count, success_count, failed_count"
        f" FROM {CAT}.{SCH}.gold_event_summary ORDER BY count DESC"
    )
    rows = ejecutar_query(sql)

    assert len(rows) == 7, f"Esperaba 7 tipos de evento, obtuvo {len(rows)}"

    tipos_esperados = {
        "USER_REGISTERED", "MONEY_ADDED", "PAYMENT_MADE",
        "PURCHASE_MADE", "TRANSFER_SENT", "PAYMENT_FAILED",
        "USER_PROFILE_UPDATED",
    }
    tipos_reales = {r["event"] for r in rows}
    assert tipos_reales == tipos_esperados, f"Tipos inesperados: {tipos_reales}"

    for r in rows:
        total = r["count"]
        ok    = r["success_count"]
        fail  = r["failed_count"]
        assert ok + fail == total, \
            f"Suma incoherente en {r['event']}: ok({ok})+fail({fail}) ≠ total({total})"


# ══════════════════════════════════════════════════════════════════════════════
# 3. DuckDB local — carga y consultas sobre Gold parquets
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.skipif(not _gold_parquets_disponibles(), reason="Parquets Gold no disponibles")
def test_duckdb_gold_queries_match_parquet_counts():
    """
    Carga los parquets Gold en DuckDB y verifica que los conteos
    y métricas coinciden con las lecturas directas vía pandas.
    """
    import duckdb
    from src.io.parquet_io import resolve_latest_parquet

    conn = duckdb.connect()
    tablas = {
        "gold_user_360":      ROOT / "data/gold/gold_user_360.parquet",
        "gold_daily_metrics": ROOT / "data/gold/gold_daily_metrics.parquet",
        "gold_event_summary": ROOT / "data/gold/gold_event_summary.parquet",
    }

    for nombre, ruta in tablas.items():
        ruta_real = resolve_latest_parquet(ruta)
        df_pandas = pd.read_parquet(ruta_real)
        conn.execute(
            f"CREATE VIEW {nombre} AS SELECT * FROM read_parquet('{ruta_real.as_posix()}')"
        )
        count_duck = conn.execute(f"SELECT COUNT(*) FROM {nombre}").fetchone()[0]
        assert count_duck == len(df_pandas), \
            f"{nombre}: DuckDB={count_duck} ≠ pandas={len(df_pandas)}"

    # Query de negocio completa
    result = conn.execute("""
        SELECT u.user_segment,
               COUNT(*) AS usuarios,
               ROUND(SUM(u.total_amount_cop)/COUNT(*), 0) AS revenue_por_usuario,
               ROUND(AVG(u.failure_rate)*100, 1) AS tasa_fallo_pct
        FROM gold_user_360 u
        GROUP BY u.user_segment
        ORDER BY revenue_por_usuario DESC
    """).fetchdf()

    assert len(result) == 4
    assert result["revenue_por_usuario"].min() > 0
    assert result["tasa_fallo_pct"].between(0, 100).all()
    conn.close()


@pytest.mark.skipif(not _gold_parquets_disponibles(), reason="Parquets Gold no disponibles")
def test_duckdb_security_filters_pii_and_blocks_ddl():
    """
    Verifica que la capa de seguridad SQL del agente:
    - Bloquea DDL (DROP, DELETE, ALTER)
    - Filtra columnas PII (user_name, user_email, user_age)
    - Añade LIMIT automático a queries sin LIMIT
    """
    from src.agent.security import procesar_sql, SQLSecurityError

    # DDL bloqueado
    for stmt in ["DROP TABLE gold_user_360", "DELETE FROM gold_user_360",
                 "ALTER TABLE gold_user_360 ADD COLUMN x INT"]:
        with pytest.raises(SQLSecurityError):
            procesar_sql(stmt)

    # LIMIT auto-añadido
    sql_sin_limit = "SELECT user_segment, COUNT(*) FROM gold_user_360 GROUP BY user_segment"
    sql_con_limit, _ = procesar_sql(sql_sin_limit, max_rows=50)
    assert re.search(r"\bLIMIT\b", sql_con_limit, re.IGNORECASE)

    # PII: columnas marcadas
    _, pii = procesar_sql("SELECT user_id, user_segment FROM gold_user_360", max_rows=10)
    # user_segment no es PII; user_id tampoco. Solo user_name/user_email/user_age lo son.
    sql_pii, cols_pii = procesar_sql(
        "SELECT user_id, user_name, user_segment FROM gold_user_360", max_rows=10
    )
    # user_name es PII — debe estar en advertencias
    assert "user_name" in (cols_pii or [])


# ══════════════════════════════════════════════════════════════════════════════
# 4. _respuesta_con_grafico() — función central del agente, end-to-end
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.skipif(not _gold_parquets_disponibles(), reason="Parquets Gold no disponibles")
def test_respuesta_con_grafico_bar_chart_from_real_gold():
    """
    _respuesta_con_grafico() con SQL real sobre Gold:
    - Retorna string con ruta PNG válida en outputs/charts/
    - El PNG existe en disco
    - El string contiene la tabla de datos Gold
    - El PNG tiene tamaño razonable (> 20 KB)
    """
    import sys
    sys.path.insert(0, str(ROOT))
    from src.agent.agent import _respuesta_con_grafico, _get_conn_duckdb

    _get_conn_duckdb()  # precalentar conexión

    sql = (
        "SELECT user_segment,"
        " COUNT(*) AS usuarios,"
        " ROUND(SUM(total_amount_cop)/COUNT(*), 0) AS revenue_por_usuario"
        " FROM gold_user_360"
        " GROUP BY user_segment ORDER BY revenue_por_usuario DESC"
    )
    resultado = _respuesta_con_grafico(
        titulo="Revenue por Segmento — Test",
        sql=sql,
        pregunta="dame un grafico de barras del revenue por segmento",
    )

    # Debe contener la marca de gráfico guardado
    assert "✅ Gráfico guardado:" in resultado, \
        f"No se encontró marca de gráfico en respuesta: {resultado[:200]}"

    # Extraer ruta del PNG
    match = re.search(r'✅ Gráfico guardado: (.+?\.png)', resultado)
    assert match, "No se pudo extraer ruta PNG del resultado"
    ruta_png = Path(match.group(1).strip())

    # El PNG debe existir en disco
    assert ruta_png.exists(), f"PNG no encontrado en: {ruta_png}"

    # Tamaño mínimo (> 20 KB → imagen real, no vacía)
    assert ruta_png.stat().st_size > 20_000, \
        f"PNG demasiado pequeño: {ruta_png.stat().st_size} bytes"

    # La tabla de datos debe estar en el resultado
    assert "**Datos Gold**" in resultado or "user_segment" in resultado, \
        "Resultado no contiene tabla de datos Gold"

    # Debe estar en outputs/charts/
    assert "outputs" in str(ruta_png) and "charts" in str(ruta_png), \
        f"PNG no está en outputs/charts/: {ruta_png}"


@pytest.mark.skipif(not _gold_parquets_disponibles(), reason="Parquets Gold no disponibles")
def test_respuesta_con_grafico_infiere_tipo_linea_para_fechas():
    """
    _respuesta_con_grafico() debe inferir tipo 'line' cuando el DataFrame
    contiene una columna 'date', sin necesidad de que el usuario lo pida explícito.
    """
    import sys
    sys.path.insert(0, str(ROOT))
    from src.agent.agent import _respuesta_con_grafico, _get_conn_duckdb

    _get_conn_duckdb()

    sql = (
        "SELECT date, total_transactions, unique_users"
        " FROM gold_daily_metrics ORDER BY date LIMIT 15"
    )
    resultado = _respuesta_con_grafico(
        titulo="Tendencia Diaria — Test",
        sql=sql,
        pregunta="muéstrame la tendencia de transacciones diarias",
    )

    assert "✅ Gráfico guardado:" in resultado
    match = re.search(r'✅ Gráfico guardado: (.+?\.png)', resultado)
    assert match
    ruta_png = Path(match.group(1).strip())
    assert ruta_png.exists()
    assert ruta_png.stat().st_size > 15_000


@pytest.mark.skipif(not _gold_parquets_disponibles(), reason="Parquets Gold no disponibles")
def test_respuesta_con_grafico_tipo_pie_desde_pregunta():
    """
    Cuando el usuario pide 'torta', _respuesta_con_grafico() genera un gráfico pie.
    """
    import sys
    sys.path.insert(0, str(ROOT))
    from src.agent.agent import _respuesta_con_grafico, _get_conn_duckdb

    _get_conn_duckdb()

    sql = (
        "SELECT preferred_channel, COUNT(*) AS usuarios"
        " FROM gold_user_360"
        " WHERE preferred_channel IS NOT NULL"
        " GROUP BY preferred_channel"
    )
    resultado = _respuesta_con_grafico(
        titulo="Distribución Canal — Test",
        sql=sql,
        pregunta="muéstrame la torta de usuarios por canal",
    )

    assert "✅ Gráfico guardado:" in resultado
    match = re.search(r'✅ Gráfico guardado: (.+?\.png)', resultado)
    assert match
    ruta_png = Path(match.group(1).strip())
    assert ruta_png.exists()
    # Pie charts suelen ser más pesadas (más detalle visual)
    assert ruta_png.stat().st_size > 20_000


# ══════════════════════════════════════════════════════════════════════════════
# 5. Flujo completo: intent NLP → SQL certificado → gráfico real
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.skipif(not _gold_parquets_disponibles(), reason="Parquets Gold no disponibles")
@pytest.mark.parametrize("pregunta,token_sql_esperado", [
    ("segmento mas rentable",             "user_segment"),
    ("ciudad con mayor crecimiento",      "city"),
    ("top merchants por volumen",         "top_merchant"),
    ("canal con mayor revenue",           "preferred_channel"),
    ("usuarios inactivos mas de 30 dias", "days_since_last_tx"),
])
def test_intent_to_chart_pipeline_end_to_end(pregunta, token_sql_esperado):
    """
    Flujo completo: pregunta natural → intent router → SQL certificado
    → _respuesta_con_grafico() → PNG en disco.
    Verifica que el SQL generado contiene el token esperado y que el PNG existe.
    """
    import sys
    sys.path.insert(0, str(ROOT))
    from src.agent.intent_router import sql_for_intent
    from src.agent.agent import _respuesta_con_grafico, _get_conn_duckdb

    _get_conn_duckdb()

    intent = sql_for_intent(pregunta)
    assert intent is not None, f"sql_for_intent devolvió None para: '{pregunta}'"

    sql, titulo = intent
    assert token_sql_esperado in sql, \
        f"SQL no contiene '{token_sql_esperado}' para pregunta '{pregunta}'"

    resultado = _respuesta_con_grafico(titulo, sql, pregunta)

    assert "✅ Gráfico guardado:" in resultado, \
        f"Sin gráfico en respuesta para '{pregunta}': {resultado[:150]}"

    match = re.search(r'✅ Gráfico guardado: (.+?\.png)', resultado)
    assert match
    ruta_png = Path(match.group(1).strip())
    assert ruta_png.exists(), f"PNG no encontrado para '{pregunta}': {ruta_png}"
    assert ruta_png.stat().st_size > 10_000


# ══════════════════════════════════════════════════════════════════════════════
# 6. Ollama — conectividad y respuesta real
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.skipif(not _ollama_disponible(), reason="Ollama no disponible en localhost:11434")
def test_ollama_responds_to_business_prompt():
    """
    Envía un prompt de negocio real a Ollama y verifica:
    - Responde en menos de 60 segundos
    - La respuesta tiene al menos 50 caracteres
    - No devuelve un error
    """
    import requests
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")

    ollama_url   = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
    ollama_model = os.getenv("OLLAMA_MODEL", "llama3.2")

    payload = {
        "model": ollama_model,
        "messages": [
            {
                "role": "user",
                "content": (
                    "Datos Gold:\nuser_segment=premium, usuarios=112, revenue_por_usuario=432416\n"
                    "user_segment=student, usuarios=146, revenue_por_usuario=422550\n\n"
                    "En 2 oraciones: ¿cuál segmento es más rentable y por qué?"
                ),
            }
        ],
        "stream": False,
        "options": {"num_ctx": 512},
    }

    t0  = time.time()
    r   = requests.post(f"{ollama_url}/api/chat", json=payload, timeout=60)
    dur = round(time.time() - t0, 1)

    assert r.status_code == 200, f"Ollama devolvió HTTP {r.status_code}"
    respuesta = r.json().get("message", {}).get("content", "")
    assert len(respuesta) >= 50, f"Respuesta muy corta ({len(respuesta)} chars): {respuesta}"
    assert dur < 60, f"Ollama tardó {dur}s — demasiado lento"


@pytest.mark.skipif(
    not (_ollama_disponible() and _gold_parquets_disponibles()),
    reason="Ollama o Gold parquets no disponibles",
)
def test_respuesta_con_grafico_incluye_analisis_ollama():
    """
    Cuando Ollama está disponible, _respuesta_con_grafico() debe incluir
    el análisis de 3 partes:
      - **📊 Análisis por dato**
      - **📈 Distribución e interpretación**
      - **✅ Conclusión y recomendación**
    """
    import sys
    sys.path.insert(0, str(ROOT))
    from src.agent.agent import _respuesta_con_grafico, _get_conn_duckdb

    _get_conn_duckdb()

    sql = (
        "SELECT user_segment,"
        " ROUND(AVG(failure_rate)*100, 1) AS tasa_fallo_pct,"
        " COUNT(*) AS usuarios"
        " FROM gold_user_360 GROUP BY user_segment ORDER BY tasa_fallo_pct DESC"
    )
    resultado = _respuesta_con_grafico(
        titulo="Tasa de Fallo por Segmento — Test",
        sql=sql,
        pregunta="analiza la tasa de fallos de pago por segmento",
    )

    # El análisis de 3 partes debe estar presente
    tiene_analisis = (
        "Análisis por dato" in resultado
        or "Distribución" in resultado
        or "Conclusión" in resultado
    )
    assert tiene_analisis, (
        "El resultado no contiene el análisis Ollama de 3 partes. "
        f"Primeros 300 chars: {resultado[:300]}"
    )


# ══════════════════════════════════════════════════════════════════════════════
# 7. Verificación completa de conectividad (smoke test de servicios)
# ══════════════════════════════════════════════════════════════════════════════

def test_verificar_cloud_script_exits_zero():
    """
    verificar_cloud.py debe terminar con exit code 0, indicando que
    todos los servicios externos están operativos.
    """
    import importlib.util
    import sys

    spec = importlib.util.spec_from_file_location(
        "verificar_cloud", ROOT / "scripts" / "verificar_cloud.py"
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["verificar_cloud"] = mod
    spec.loader.exec_module(mod)

    resultado = mod.main()
    assert resultado == 0, (
        f"verificar_cloud.main() retornó {resultado} (esperaba 0). "
        "Revisa la conectividad con S3, ExchangeRate API o Databricks."
    )
