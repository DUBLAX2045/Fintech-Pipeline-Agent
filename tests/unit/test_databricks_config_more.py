from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from src.config import databricks_config as db


pytestmark = pytest.mark.unit


class FakeCursor:
    def __init__(self, responses):
        self.responses = responses
        self.description = []
        self.rows = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, sql):
        cols, rows = self.responses.pop(0)
        self.description = [(col,) for col in cols]
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


def _set_valid_env(monkeypatch):
    monkeypatch.setattr(db, "DATABRICKS_HOST", "host")
    monkeypatch.setattr(db, "DATABRICKS_TOKEN", "token")
    monkeypatch.setattr(db, "DATABRICKS_HTTP_PATH", "/sql/path")
    monkeypatch.setattr(db, "DATABRICKS_CATALOG", "fintech_pipeline")
    monkeypatch.setattr(db, "DATABRICKS_SCHEMA", "fintech")


def test_get_connection_missing_credentials_and_import_error(monkeypatch):
    monkeypatch.setattr(db, "DATABRICKS_HOST", "")
    monkeypatch.setattr(db, "DATABRICKS_TOKEN", "")
    monkeypatch.setattr(db, "DATABRICKS_HTTP_PATH", "")

    with pytest.raises(EnvironmentError):
        db.get_connection()

    _set_valid_env(monkeypatch)

    real_import = __import__

    def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "databricks":
            raise ImportError("sin conector")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr("builtins.__import__", fake_import)

    with pytest.raises(ImportError):
        db.get_connection()


def test_get_connection_uses_databricks_connector(monkeypatch):
    _set_valid_env(monkeypatch)
    captured = {}

    def fake_connect(**kwargs):
        captured.update(kwargs)
        return "conn"

    fake_databricks = SimpleNamespace(sql=SimpleNamespace(connect=fake_connect))
    monkeypatch.setitem(sys.modules, "databricks", fake_databricks)
    monkeypatch.setitem(sys.modules, "databricks.sql", fake_databricks.sql)

    assert db.get_connection() == "conn"
    assert captured["server_hostname"] == "host"
    assert captured["http_path"] == "/sql/path"
    assert captured["catalog"] == "fintech_pipeline"
    assert captured["schema"] == "fintech"


def test_verificar_conexion_catalog_schema_tables_and_exception(monkeypatch):
    _set_valid_env(monkeypatch)

    responses = [
        (["ok"], [(1,)]),
        (["catalog"], [("otro_catalogo",)]),
    ]
    monkeypatch.setattr(db, "get_connection", lambda: FakeConnection(responses))
    result = db.verificar_conexion()
    assert result["ok"] is False
    assert "no existe o no es visible" in result["error"]
    assert result["select_ok"] is True
    assert result["catalog_exists"] is False

    responses = [
        (["ok"], [(1,)]),
        (["catalog"], [("fintech_pipeline",)]),
        (["databaseName"], [("otro_schema",)]),
    ]
    monkeypatch.setattr(db, "get_connection", lambda: FakeConnection(responses))
    result = db.verificar_conexion()
    assert result["ok"] is False
    assert result["catalog_exists"] is True
    assert result["schema_exists"] is False

    responses = [
        (["ok"], [(1,)]),
        (["catalog"], [("fintech_pipeline",)]),
        (["databaseName"], [("fintech",)]),
        (["tableName"], [("gold_user_360",)]),
    ]
    monkeypatch.setattr(db, "get_connection", lambda: FakeConnection(responses))
    result = db.verificar_conexion()
    assert result["ok"] is True
    assert result["ready_for_agent"] is False
    assert "gold_daily_metrics" in result["tablas_requeridas_faltantes"]
    assert result["advertencias"]

    monkeypatch.setattr(db, "get_connection", lambda: (_ for _ in ()).throw(RuntimeError("timeout")))
    result = db.verificar_conexion()
    assert result["ok"] is False
    assert result["error"] == "timeout"
