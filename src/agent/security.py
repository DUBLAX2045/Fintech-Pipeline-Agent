"""
security.py - Validación de seguridad para SQL generado por el LLM.

Bloquea operaciones destructivas y filtra columnas PII antes de ejecutar.
"""

import re

# Columnas que contienen PII — se advierte si el LLM las incluye
PII_COLUMNS = {"user_name", "user_email"}

# Operaciones SQL prohibidas
FORBIDDEN_PATTERNS = [
    r"\bDROP\b",
    r"\bDELETE\b",
    r"\bUPDATE\b",
    r"\bINSERT\b",
    r"\bALTER\b",
    r"\bCREATE\b",
    r"\bTRUNCATE\b",
    r"\bEXEC\b",
    r"\bEXECUTE\b",
    r"--",          # comentarios SQL inline (riesgo de inyección)
    r"/\*",         # comentarios de bloque
]


class SQLSecurityError(Exception):
    """Se lanza cuando el SQL viola las reglas de seguridad."""
    pass


def validar_sql(sql: str) -> str:
    """
    Valida que el SQL no contenga operaciones prohibidas.

    Args:
        sql: Consulta SQL a validar.

    Returns:
        El mismo SQL si pasa la validación.

    Raises:
        SQLSecurityError: Si el SQL contiene operaciones prohibidas.
    """
    sql_upper = sql.upper()

    for pattern in FORBIDDEN_PATTERNS:
        if re.search(pattern, sql_upper, re.IGNORECASE):
            raise SQLSecurityError(
                f"SQL bloqueado por seguridad: patron prohibido '{pattern}' detectado.\n"
                f"Solo se permiten consultas SELECT de solo lectura."
            )

    if not re.search(r"\bSELECT\b", sql_upper):
        raise SQLSecurityError(
            "SQL bloqueado: solo se permiten consultas SELECT."
        )

    return sql


def detectar_pii(sql: str) -> list[str]:
    """
    Detecta si el SQL incluye columnas PII directamente.

    Args:
        sql: Consulta SQL a revisar.

    Returns:
        Lista de columnas PII encontradas (vacía si ninguna).
    """
    sql_lower = sql.lower()
    encontradas = []
    for col in PII_COLUMNS:
        # Busca la columna como palabra completa (no dentro de otra)
        if re.search(r"\b" + col + r"\b", sql_lower):
            encontradas.append(col)
    return encontradas


def agregar_limit(sql: str, max_rows: int = 100) -> str:
    """
    Agrega LIMIT si el SQL no tiene uno o si el límite es mayor al permitido.

    Args:
        sql: Consulta SQL.
        max_rows: Número máximo de filas permitidas.

    Returns:
        SQL con LIMIT aplicado.
    """
    sql_stripped = sql.rstrip().rstrip(";")
    limit_match = re.search(r"\bLIMIT\s+(\d+)", sql_stripped, re.IGNORECASE)

    if limit_match:
        current_limit = int(limit_match.group(1))
        if current_limit > max_rows:
            sql_stripped = re.sub(
                r"\bLIMIT\s+\d+",
                f"LIMIT {max_rows}",
                sql_stripped,
                flags=re.IGNORECASE,
            )
    else:
        sql_stripped = f"{sql_stripped}\nLIMIT {max_rows}"

    return sql_stripped


def procesar_sql(sql: str, max_rows: int = 100) -> tuple[str, list[str]]:
    """
    Pipeline completo de seguridad: validar + detectar PII + agregar LIMIT.

    Args:
        sql: SQL generado por el LLM.
        max_rows: Límite máximo de filas.

    Returns:
        Tupla (sql_seguro, advertencias_pii).

    Raises:
        SQLSecurityError: Si el SQL contiene operaciones prohibidas.
    """
    sql_seguro = validar_sql(sql)
    advertencias = detectar_pii(sql_seguro)
    sql_seguro = agregar_limit(sql_seguro, max_rows)
    return sql_seguro, advertencias
