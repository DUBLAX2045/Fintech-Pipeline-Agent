from __future__ import annotations

import pytest

from src.agent.agent import _extraer_tool_call
from src.agent.security import SQLSecurityError, procesar_sql


pytestmark = pytest.mark.unit


def test_procesar_sql_blocks_mutating_statements():
    with pytest.raises(SQLSecurityError):
        procesar_sql("DROP TABLE gold_user_360")


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM gold_user_360; DELETE FROM gold_user_360",
        "SELECT * FROM gold_user_360 -- borrar validacion posterior",
        "SELECT * FROM gold_user_360 /* comentario peligroso */",
    ],
)
def test_procesar_sql_blocks_forbidden_patterns_even_with_select(sql):
    with pytest.raises(SQLSecurityError):
        procesar_sql(sql)


def test_procesar_sql_adds_limit_and_reports_pii():
    sql, pii = procesar_sql("SELECT user_email, total_amount_cop FROM gold_user_360")

    assert "LIMIT 100" in sql
    assert pii == ["user_email"]


def test_extraer_tool_call_parses_nested_json():
    parsed = _extraer_tool_call(
        '{"tool":"consultar_sql","args":{"query":"SELECT COUNT(*) FROM gold_user_360"}}'
    )

    assert parsed["tool"] == "consultar_sql"
    assert parsed["args"]["query"].startswith("SELECT")
