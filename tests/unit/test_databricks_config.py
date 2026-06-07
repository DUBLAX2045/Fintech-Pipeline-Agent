from __future__ import annotations

import pytest

from src.config import databricks_config as db


pytestmark = pytest.mark.unit


class FakeCursor:
    def __init__(self, responses):
        self.responses = responses
        self.description = []
        self.rows = []
        self.executed = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, sql):
        self.executed.append(sql)
        cols, rows = self.responses.pop(0)
        self.description = [(c,) for c in cols]
        self.rows = rows

    def fetchmany(self, max_rows):
        return self.rows[:max_rows]


class FakeConnection:
    def __init__(self, responses):
        self.responses = responses

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def cursor(self):
        return FakeCursor(self.responses)


def test_validar_sql_readonly_allows_only_expected_prefixes():
    assert db._validar_sql_readonly(" SELECT 1; ", ("SELECT",)) == "SELECT 1"
    with pytest.raises(ValueError):
        db._validar_sql_readonly("DELETE FROM t", ("SELECT",))
    with pytest.raises(ValueError):
        db._validar_sql_readonly("SHOW TABLES", ("SELECT",))
    with pytest.raises(ValueError):
        db._validar_sql_readonly("   ", ("SELECT",))


def test_helpers_quote_fetch_and_value():
    assert db._quote_identifier("a`b") == "`a``b`"
    cursor = FakeCursor([])
    cursor.description = [("name",), ("count",)]
    cursor.rows = [("gold_user_360", 489)]
    assert db._fetch_dicts(cursor, 5) == [{"name": "gold_user_360", "count": 489}]
    assert db._valor({"tableName": "gold"}, "name", "tableName") == "gold"
    assert db._valor({}, "missing") is None


def test_verificar_conexion_success(monkeypatch):
    monkeypatch.setattr(db, "DATABRICKS_HOST", "host")
    monkeypatch.setattr(db, "DATABRICKS_TOKEN", "token")
    monkeypatch.setattr(db, "DATABRICKS_HTTP_PATH", "/sql/path")
    monkeypatch.setattr(db, "DATABRICKS_CATALOG", "fintech_pipeline")
    monkeypatch.setattr(db, "DATABRICKS_SCHEMA", "fintech")
    responses = [
        (["ok"], [(1,)]),
        (["catalog"], [("fintech_pipeline",)]),
        (["databaseName"], [("fintech",)]),
        (["tableName"], [("gold_user_360",), ("gold_daily_metrics",), ("gold_event_summary",)]),
    ]
    monkeypatch.setattr(db, "get_connection", lambda: FakeConnection(responses))

    result = db.verificar_conexion()

    assert result["ok"] is True
    assert result["ready_for_agent"] is True
    assert result["tablas_requeridas_faltantes"] == []


def test_verificar_conexion_missing_credentials(monkeypatch):
    monkeypatch.setattr(db, "DATABRICKS_HOST", "")
    monkeypatch.setattr(db, "DATABRICKS_TOKEN", "")
    monkeypatch.setattr(db, "DATABRICKS_HTTP_PATH", "")

    result = db.verificar_conexion()

    assert result["ok"] is False
    assert "Faltan variables" in result["error"]


def test_ejecutar_query_and_metadata_command(monkeypatch):
    monkeypatch.setattr(db, "DATABRICKS_HOST", "host")
    monkeypatch.setattr(db, "DATABRICKS_TOKEN", "token")
    monkeypatch.setattr(db, "DATABRICKS_HTTP_PATH", "/sql/path")
    monkeypatch.setattr(db, "get_connection", lambda: FakeConnection([(["answer"], [(1,)])]))

    assert db.ejecutar_query("SELECT 1", max_filas=1) == [{"answer": 1}]

    monkeypatch.setattr(db, "get_connection", lambda: FakeConnection([(["tableName"], [("gold",)])]))
    assert db.ejecutar_comando_lectura("SHOW TABLES", max_filas=1) == [{"tableName": "gold"}]
