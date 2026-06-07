"""
tools.py - Herramientas Strands para el agente de consultas Gold.

Cada función decorada con @tool es invocable por el LLM durante la inferencia.
DuckDB lee los Parquet directamente; no hay base de datos persistente.
"""

import os
import json
import duckdb
from pathlib import Path
from strands import tool

from src.agent.security import procesar_sql, SQLSecurityError
from src.agent.schema import GOLD_SCHEMA, sugerir_grafico
from src.agent.charts import auto_grafico
from src.io.parquet_io import resolve_latest_parquet

# ── Rutas por defecto (relativas al directorio de trabajo del proyecto) ──────
_GOLD_DIR = Path(os.environ.get("GOLD_DIR", "data/gold"))

_TABLAS = {
    "gold_user_360": _GOLD_DIR / "gold_user_360.parquet",
    "gold_daily_metrics": _GOLD_DIR / "gold_daily_metrics.parquet",
    "gold_event_summary": _GOLD_DIR / "gold_event_summary.parquet",
}


def _ruta_gold(ruta: Path) -> Path:
    return Path(resolve_latest_parquet(ruta))


def _get_conn() -> duckdb.DuckDBPyConnection:
    """Crea una conexión DuckDB en memoria con las vistas Gold registradas."""
    conn = duckdb.connect(":memory:")
    for nombre, ruta in _TABLAS.items():
        ruta_abs = _ruta_gold(ruta).resolve()
        if os.path.exists(ruta_abs):
            ruta_sql = ruta_abs.as_posix()
            conn.execute(
                f"CREATE VIEW {nombre} AS SELECT * FROM read_parquet('{ruta_sql}')"
            )
    return conn


@tool
def ejecutar_sql(sql: str) -> str:
    """
    Ejecuta una consulta SQL SELECT sobre las tablas Gold y retorna los resultados.

    Usa esta herramienta para responder preguntas de negocio consultando los datos
    de la capa Gold del data warehouse (gold_user_360, gold_daily_metrics,
    gold_event_summary). La consulta debe ser un SELECT valido en sintaxis DuckDB.

    Args:
        sql: Consulta SQL SELECT a ejecutar sobre las tablas Gold.

    Returns:
        Resultados en formato JSON con columnas, filas y metadatos.
    """
    try:
        sql_seguro, advertencias_pii = procesar_sql(sql, max_rows=100)
    except SQLSecurityError as e:
        return json.dumps({
            "error": str(e),
            "tipo": "seguridad",
            "sql_original": sql,
        }, ensure_ascii=False)

    try:
        conn = _get_conn()
        df = conn.execute(sql_seguro).df()
        conn.close()
    except Exception as e:
        return json.dumps({
            "error": str(e),
            "tipo": "ejecucion",
            "sql": sql_seguro,
        }, ensure_ascii=False)

    resultado = {
        "filas": len(df),
        "columnas": df.columns.tolist(),
        "datos": df.to_dict(orient="records"),
        "sql_ejecutado": sql_seguro,
    }
    if advertencias_pii:
        resultado["advertencia_pii"] = (
            f"Columnas PII incluidas: {advertencias_pii}. "
            "Estos datos deben tratarse con confidencialidad."
        )
    return json.dumps(resultado, ensure_ascii=False, default=str)


@tool
def obtener_esquema() -> str:
    """
    Retorna el esquema completo de las tablas Gold disponibles para consulta.

    Usa esta herramienta cuando necesites recordar qué columnas existen,
    sus tipos de datos o las reglas de negocio antes de generar SQL.

    Returns:
        Documentación del esquema Gold en formato Markdown.
    """
    info_archivos = []
    for nombre, ruta in _TABLAS.items():
        ruta_abs = _ruta_gold(ruta).resolve()
        if os.path.exists(ruta_abs):
            size_kb = os.path.getsize(ruta_abs) / 1024
            try:
                conn = duckdb.connect(":memory:")
                ruta_sql = ruta_abs.as_posix()
                count = conn.execute(
                    f"SELECT COUNT(*) FROM read_parquet('{ruta_sql}')"
                ).fetchone()[0]
                conn.close()
                info_archivos.append(f"- {nombre}: {count:,} filas ({size_kb:.1f} KB)")
            except Exception:
                info_archivos.append(f"- {nombre}: {size_kb:.1f} KB (conteo no disponible)")
        else:
            info_archivos.append(f"- {nombre}: ARCHIVO NO ENCONTRADO en {ruta_abs}")

    estado = "\n".join(info_archivos)
    return f"## Estado actual de los archivos Gold\n{estado}\n\n{GOLD_SCHEMA}"


@tool
def generar_grafico(
    sql: str,
    tipo_grafico: str,
    titulo: str,
    col_x: str,
    col_y: str,
) -> str:
    """
    Ejecuta una consulta SQL y genera un grafico con los resultados.

    Usa esta herramienta cuando el usuario pida una visualizacion o cuando
    los datos sean mejor comunicados graficamente (tendencias, rankings,
    distribuciones). El grafico se guarda en data/gold/graficos/.

    Args:
        sql: Consulta SQL SELECT para obtener los datos del grafico.
        tipo_grafico: Tipo de grafico - 'bar', 'line' o 'pie'.
        titulo: Titulo descriptivo del grafico.
        col_x: Nombre de la columna para el eje X o etiquetas.
        col_y: Nombre de la columna para el eje Y o valores.

    Returns:
        Ruta del archivo PNG guardado o mensaje de error.
    """
    try:
        sql_seguro, _ = procesar_sql(sql, max_rows=50)
    except SQLSecurityError as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)

    try:
        conn = _get_conn()
        df = conn.execute(sql_seguro).df()
        conn.close()
    except Exception as e:
        return json.dumps({"error": str(e), "sql": sql_seguro}, ensure_ascii=False)

    if df.empty:
        return json.dumps({"error": "La consulta no retornó datos"}, ensure_ascii=False)

    if col_x not in df.columns or col_y not in df.columns:
        tipo_sugerido = sugerir_grafico(titulo)
        img_b64 = auto_grafico(df, tipo_sugerido, titulo)
    else:
        from src.agent.charts import (
            generar_grafico_barras,
            generar_grafico_lineas,
            generar_grafico_pie,
        )
        tipo_norm = tipo_grafico.lower()
        if tipo_norm == "line":
            img_b64 = generar_grafico_lineas(df, col_x, col_y, titulo=titulo)
        elif tipo_norm == "pie":
            img_b64 = generar_grafico_pie(df, col_x, col_y, titulo=titulo)
        else:
            horizontal = len(df) > 6
            img_b64 = generar_grafico_barras(df, col_x, col_y, titulo=titulo,
                                             horizontal=horizontal)

    if img_b64 is None:
        return json.dumps({"error": "No se pudo generar el gráfico con los datos disponibles"},
                          ensure_ascii=False)

    directorio = os.path.join(_GOLD_DIR, "graficos")
    os.makedirs(directorio, exist_ok=True)
    nombre_archivo = titulo.lower().replace(" ", "_")[:40] + ".png"
    ruta_salida = os.path.join(directorio, nombre_archivo)

    import base64
    with open(ruta_salida, "wb") as f:
        f.write(base64.b64decode(img_b64))

    return json.dumps({
        "guardado_en": ruta_salida,
        "filas_graficadas": len(df),
        "tipo": tipo_grafico,
        "base64": img_b64,
    }, ensure_ascii=False)
