"""
databricks_config.py — Configuración y utilidades para Databricks Unity Catalog.

BUG ORIGINAL CORREGIDO:
  os.getenv("https://dbc-...") usaba la URL como clave → siempre None → ValueError
  Corrección: usar el nombre correcto de la variable de entorno como clave.

Variables requeridas en .env:
    DATABRICKS_HOST       → ej: dbc-cd89db62-9f56.cloud.databricks.com  (sin https://)
    DATABRICKS_TOKEN      → Token personal de acceso (dapi...)
    DATABRICKS_HTTP_PATH  → /sql/1.0/warehouses/<warehouse_id>
    DATABRICKS_CATALOG    → fintech_pipeline  (por defecto)
    DATABRICKS_SCHEMA     → fintech           (por defecto)
"""

import os
import re
import time
import pandas as pd
from dotenv import load_dotenv

load_dotenv()

# ── Lectura CORRECTA de variables de entorno ────────────────────────────────
# BUG ORIGINAL: os.getenv("https://dbc-cd89db62-9f56.cloud.databricks.com")
# CORRECCIÓN  : os.getenv("DATABRICKS_HOST")
DATABRICKS_HOST      = os.getenv("DATABRICKS_HOST", "")
DATABRICKS_TOKEN     = os.getenv("DATABRICKS_TOKEN", "")
DATABRICKS_HTTP_PATH = os.getenv("DATABRICKS_HTTP_PATH", "")
DATABRICKS_CATALOG   = os.getenv("DATABRICKS_CATALOG", "fintech_pipeline")
DATABRICKS_SCHEMA    = os.getenv("DATABRICKS_SCHEMA", "fintech")

# Normalizar host: quitar https:// si el usuario lo incluyó
DATABRICKS_HOST = DATABRICKS_HOST.replace("https://", "").replace("http://", "").rstrip("/")

TABLAS_GOLD_REQUERIDAS = {
    "gold_user_360",
    "gold_daily_metrics",
    "gold_event_summary",
}

_MUTATING_SQL = re.compile(
    r"\b(DROP|DELETE|UPDATE|INSERT|ALTER|CREATE|TRUNCATE|MERGE|REPLACE|GRANT|REVOKE|COPY|PUT|REMOVE)\b",
    re.IGNORECASE,
)


def _validar_credenciales() -> tuple:
    faltantes = []
    if not DATABRICKS_HOST:
        faltantes.append("DATABRICKS_HOST")
    if not DATABRICKS_TOKEN:
        faltantes.append("DATABRICKS_TOKEN")
    if not DATABRICKS_HTTP_PATH:
        faltantes.append("DATABRICKS_HTTP_PATH")
    if faltantes:
        return False, f"Faltan variables de entorno: {', '.join(faltantes)}. Configura .env"
    return True, "OK"


def get_connection():
    """
    Retorna una conexión activa al SQL Warehouse de Databricks.
    Úsala con context manager: with get_connection() as conn: ...
    """
    ok, msg = _validar_credenciales()
    if not ok:
        raise EnvironmentError(f"❌ {msg}")
    try:
        from databricks import sql as dbsql
    except ImportError:
        raise ImportError(
            "❌ Instala el conector: pip install databricks-sql-connector"
        )
    return dbsql.connect(
        server_hostname=DATABRICKS_HOST,
        http_path=DATABRICKS_HTTP_PATH,
        access_token=DATABRICKS_TOKEN,
        catalog=DATABRICKS_CATALOG,
        schema=DATABRICKS_SCHEMA,
    )


def _quote_identifier(value: str) -> str:
    return f"`{str(value).replace('`', '``')}`"


def _validar_sql_readonly(sql: str, prefijos_permitidos: tuple[str, ...]) -> str:
    sql_limpio = sql.strip().rstrip(";")
    if not sql_limpio:
        raise ValueError("La consulta SQL esta vacia.")
    if _MUTATING_SQL.search(sql_limpio):
        raise ValueError("Operacion de escritura no permitida. Solo lectura.")

    sql_upper = sql_limpio.upper()
    if not sql_upper.startswith(prefijos_permitidos):
        permitidos = ", ".join(prefijos_permitidos)
        raise ValueError(f"Solo se permiten consultas de lectura: {permitidos}.")
    return sql_limpio


def _fetch_dicts(cursor, max_filas: int) -> list:
    cols = [d[0] for d in (cursor.description or [])]
    filas = cursor.fetchmany(max_filas)
    return [dict(zip(cols, fila)) for fila in filas]


def _valor(row: dict, *keys: str):
    for key in keys:
        if key in row:
            return row[key]
    return None


def ejecutar_query(sql: str, max_filas: int = 500) -> list:
    """
    Ejecuta un SELECT en Databricks. Solo permite operaciones de lectura.
    Retorna lista de dicts [{col: val}, ...].
    """
    sql_limpio = _validar_sql_readonly(sql, ("SELECT", "WITH"))

    t0 = time.time()
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(sql_limpio)
            resultado = _fetch_dicts(cursor, max_filas)
            print(f"   [Databricks] {len(resultado)} filas en {time.time()-t0:.2f}s")
            return resultado


def ejecutar_comando_lectura(sql: str, max_filas: int = 500) -> list:
    """
    Ejecuta comandos metadata de solo lectura para diagnostico interno.

    Esta funcion no se expone al agente conversacional; el agente sigue usando
    ejecutar_query(), que solo permite SELECT/WITH.
    """
    sql_limpio = _validar_sql_readonly(
        sql,
        ("SELECT", "WITH", "SHOW", "DESCRIBE", "DESC", "EXPLAIN"),
    )

    t0 = time.time()
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(sql_limpio)
            resultado = _fetch_dicts(cursor, max_filas)
            print(f"   [Databricks] {len(resultado)} filas en {time.time()-t0:.2f}s")
            return resultado


def verificar_conexion() -> dict:
    """Prueba la conexion y retorna diagnostico completo."""
    resultado = {
        "ok": False,
        "host": DATABRICKS_HOST or "(no configurado)",
        "catalog": DATABRICKS_CATALOG,
        "schema": DATABRICKS_SCHEMA,
        "select_ok": False,
        "catalog_exists": False,
        "schema_exists": False,
        "tablas_encontradas": [],
        "tablas_requeridas_faltantes": [],
        "ready_for_agent": False,
        "advertencias": [],
        "error": None,
        "duracion_seg": 0.0,
    }
    ok, msg = _validar_credenciales()
    if not ok:
        resultado["error"] = msg
        return resultado
    t0 = time.time()
    try:
        with get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute("SELECT 1 AS ok")
                filas = _fetch_dicts(cursor, 1)
                resultado["select_ok"] = bool(filas and filas[0].get("ok") == 1)

                cursor.execute("SHOW CATALOGS")
                catalogos = _fetch_dicts(cursor, 500)
                catalog_names = {
                    _valor(row, "catalog", "catalog_name", "namespace", "name")
                    for row in catalogos
                }
                resultado["catalog_exists"] = DATABRICKS_CATALOG in catalog_names
                if not resultado["catalog_exists"]:
                    visibles = ", ".join(sorted(str(c) for c in catalog_names if c))
                    resultado["error"] = (
                        f"El catalogo '{DATABRICKS_CATALOG}' no existe o no es visible. "
                        f"Catalogos visibles: {visibles or '(ninguno)'}"
                    )
                    resultado["duracion_seg"] = round(time.time() - t0, 2)
                    return resultado

                catalog = _quote_identifier(DATABRICKS_CATALOG)
                schema = _quote_identifier(DATABRICKS_SCHEMA)

                cursor.execute(f"SHOW SCHEMAS IN {catalog}")
                schemas = _fetch_dicts(cursor, 500)
                schema_names = {
                    _valor(row, "databaseName", "schemaName", "namespace", "name")
                    for row in schemas
                }
                resultado["schema_exists"] = DATABRICKS_SCHEMA in schema_names
                if not resultado["schema_exists"]:
                    visibles = ", ".join(sorted(str(s) for s in schema_names if s))
                    resultado["error"] = (
                        f"El schema '{DATABRICKS_SCHEMA}' no existe o no es visible en "
                        f"'{DATABRICKS_CATALOG}'. Schemas visibles: {visibles or '(ninguno)'}"
                    )
                    resultado["duracion_seg"] = round(time.time() - t0, 2)
                    return resultado

                cursor.execute(f"SHOW TABLES IN {catalog}.{schema}")
                tablas = _fetch_dicts(cursor, 500)

        resultado["tablas_encontradas"] = [
            _valor(t, "tableName", "table_name", "name") or str(t) for t in tablas
        ]
        table_set = {str(t).lower() for t in resultado["tablas_encontradas"]}
        faltantes = sorted(t for t in TABLAS_GOLD_REQUERIDAS if t.lower() not in table_set)
        resultado["tablas_requeridas_faltantes"] = faltantes
        resultado["ready_for_agent"] = not faltantes
        if not resultado["tablas_encontradas"]:
            resultado["advertencias"].append(
                f"No hay tablas visibles en {DATABRICKS_CATALOG}.{DATABRICKS_SCHEMA}."
            )
        elif faltantes:
            resultado["advertencias"].append(
                f"Faltan tablas Gold requeridas: {', '.join(faltantes)}."
            )
        resultado["ok"] = True
        resultado["duracion_seg"] = round(time.time() - t0, 2)
    except Exception as e:
        resultado["error"] = str(e)
        resultado["duracion_seg"] = round(time.time() - t0, 2)
    return resultado


# ══════════════════════════════════════════════════════════════════════════════
# ESCRITURA GOLD — funciones de pipeline (no expuestas al agente)
# ══════════════════════════════════════════════════════════════════════════════

def _pandas_dtype_a_sql(dtype) -> str:
    """Mapea un dtype de pandas al tipo SQL equivalente en Databricks."""
    s = str(dtype)
    if "datetime" in s:
        return "TIMESTAMP"
    if s == "object":
        return "STRING"
    if s in ("int64", "Int64"):
        return "BIGINT"
    if s in ("int32", "Int32"):
        return "INT"
    if s in ("float64", "Float64"):
        return "DOUBLE"
    if s in ("float32", "Float32"):
        return "FLOAT"
    if s == "bool":
        return "BOOLEAN"
    return "STRING"


def _literal_sql(v) -> str:
    """Convierte un valor Python a su representación literal SQL."""
    if v is None:
        return "NULL"
    if isinstance(v, float) and pd.isna(v):
        return "NULL"
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, str):
        if v.lower() in ("none", "nat", "nan", "null", ""):
            return "NULL"
        return "'" + v.replace("\\", "\\\\").replace("'", "''") + "'"
    return "NULL"


def _preparar_df_sql(df: pd.DataFrame) -> pd.DataFrame:
    """Convierte columnas datetime a string ISO y normaliza nulos."""
    df2 = df.copy()
    for col in df2.columns:
        dtype_str = str(df2[col].dtype)
        if "datetime" in dtype_str:
            df2[col] = df2[col].dt.strftime("%Y-%m-%d %H:%M:%S").where(
                df2[col].notna(), None
            )
        elif dtype_str == "object":
            df2[col] = df2[col].where(df2[col].notna(), None)
    return df2


def subir_tabla_gold(
    df: pd.DataFrame,
    nombre_tabla: str,
    batch_size: int = 150,
) -> dict:
    """
    Crea o reemplaza una tabla Gold en Databricks Unity Catalog.

    Estrategia (evita requerir permiso DELETE/TRUNCATE):
        1. CREATE OR REPLACE TABLE … USING DELTA  ← define esquema vacío
        2. INSERT INTO en lotes de batch_size filas

    Solo para uso interno del pipeline — nunca exponer al agente conversacional.

    Args:
        df:           DataFrame con los datos de la capa Gold.
        nombre_tabla: Nombre de la tabla destino (ej: "gold_user_360").
        batch_size:   Filas por sentencia INSERT (default 150).

    Returns:
        dict con claves: ok, tabla, filas, duracion_seg, error.
    """
    resultado: dict = {
        "ok": False,
        "tabla": nombre_tabla,
        "filas": 0,
        "duracion_seg": 0.0,
        "error": None,
    }

    catalog_q  = _quote_identifier(DATABRICKS_CATALOG)
    schema_q   = _quote_identifier(DATABRICKS_SCHEMA)
    tabla_q    = _quote_identifier(nombre_tabla)
    tabla_full = f"{catalog_q}.{schema_q}.{tabla_q}"

    col_defs    = [
        f"`{col}` {_pandas_dtype_a_sql(dtype)}"
        for col, dtype in df.dtypes.items()
    ]
    cols_quoted = ", ".join(f"`{c}`" for c in df.columns)

    t0 = time.time()
    try:
        ok, msg = _validar_credenciales()
        if not ok:
            raise EnvironmentError(msg)

        df_sql = _preparar_df_sql(df)

        with get_connection() as conn:
            with conn.cursor() as cur:
                # 1. Si existe como tabla no-Delta, hay que eliminarla primero
                cur.execute(f"DROP TABLE IF EXISTS {tabla_full}")
                cur.execute(
                    f"CREATE TABLE {tabla_full} "
                    f"({', '.join(col_defs)}) USING DELTA"
                )

                # 2. Insertar datos en lotes
                total = 0
                for start in range(0, len(df_sql), batch_size):
                    batch = df_sql.iloc[start : start + batch_size]
                    values_clauses = []
                    for _, row in batch.iterrows():
                        tokens = [_literal_sql(v) for v in row]
                        values_clauses.append(f"({', '.join(tokens)})")
                    cur.execute(
                        f"INSERT INTO {tabla_full} ({cols_quoted}) "
                        f"VALUES {', '.join(values_clauses)}"
                    )
                    total += len(batch)

        resultado.update({"ok": True, "filas": total})
        resultado["duracion_seg"] = round(time.time() - t0, 2)
        print(
            f"   ✅ Databricks {tabla_full}: "
            f"{total} filas en {resultado['duracion_seg']}s"
        )

    except Exception as exc:
        resultado["error"] = str(exc)
        resultado["duracion_seg"] = round(time.time() - t0, 2)
        print(f"   ❌ Databricks {tabla_full}: {exc}")

    return resultado


if __name__ == "__main__":
    print("\n🔍 Verificando conexion a Databricks...")
    print(f"   HOST      : {DATABRICKS_HOST or '(vacio — configura .env)'}")
    print(f"   TOKEN     : {'OK' if DATABRICKS_TOKEN else 'VACIO'}")
    print(f"   HTTP_PATH : {DATABRICKS_HTTP_PATH or '(vacio)'}")
    print(f"   CATALOG   : {DATABRICKS_CATALOG}")
    print(f"   SCHEMA    : {DATABRICKS_SCHEMA}\n")
    diag = verificar_conexion()
    if diag["ok"]:
        print(f"Conexion exitosa en {diag['duracion_seg']}s")
        print(f"  SELECT 1 : {'OK' if diag['select_ok'] else 'FALLO'}")
        print(f"  Catalogo : {'OK' if diag['catalog_exists'] else 'FALLO'}")
        print(f"  Schema   : {'OK' if diag['schema_exists'] else 'FALLO'}")
        print(f"  Agente   : {'OK' if diag['ready_for_agent'] else 'PENDIENTE TABLAS'}")
        for t in diag["tablas_encontradas"]:
            print(f"  - {t}")
        for advertencia in diag.get("advertencias", []):
            print(f"Advertencia: {advertencia}")
    else:
        print(f"Error: {diag['error']}")
