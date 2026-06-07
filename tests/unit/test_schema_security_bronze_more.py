from __future__ import annotations

import json

import pandas as pd
import pytest

from src.agent import schema
from src.agent.security import SQLSecurityError, agregar_limit, detectar_pii, validar_sql
from src.bronze import ingest


pytestmark = pytest.mark.unit


def test_schema_sugerir_grafico_routes():
    assert schema.sugerir_grafico("tendencia diaria por fecha") == "line"
    assert schema.sugerir_grafico("distribucion porcentual por segmento") == "pie"
    assert schema.sugerir_grafico("top comercios") == "bar"
    assert "gold_user_360" in schema.GOLD_SCHEMA
    assert "Nunca inventes cifras" in schema.SYSTEM_PROMPT


def test_security_helpers_cover_limits_pii_and_invalid_sql():
    assert set(detectar_pii("SELECT user_name, user_email FROM t")) == {"user_name", "user_email"}
    assert agregar_limit("SELECT * FROM t LIMIT 500", max_rows=10).endswith("LIMIT 10")
    assert agregar_limit("SELECT * FROM t LIMIT 5", max_rows=10).endswith("LIMIT 5")

    with pytest.raises(SQLSecurityError):
        validar_sql("SHOW TABLES")

    with pytest.raises(SQLSecurityError):
        validar_sql("SELECT * FROM t -- comentario")


def test_bronze_load_flatten_and_duplicate_edge_cases(tmp_path, make_fintech_event):
    raw = tmp_path / "events.json"
    event = make_fintech_event(event_id="evt-json", user_id="user-json")
    raw.write_text(json.dumps([event]), encoding="utf-8")

    loaded = ingest.cargar_json(str(raw))
    df = ingest.aplanar_todos(loaded)

    assert loaded[0]["detail"]["id"] == "evt-json"
    assert df.loc[0, "user_id"] == "user-json"

    assert ingest._normalizar_event_id(None) is None
    assert ingest._normalizar_event_id("  evt-1  ") == "evt-1"
    assert ingest._cargar_eventos_existentes(str(tmp_path / "missing")) == {}

    with pytest.raises(ValueError):
        ingest.detectar_y_registrar_duplicados(pd.DataFrame({"x": [1]}))


def test_cargar_eventos_existentes_skips_unreadable_or_incomplete_files(tmp_path):
    bronze = tmp_path / "bronze"
    bronze.mkdir()
    (bronze / "broken.parquet").write_text("not parquet", encoding="utf-8")
    pd.DataFrame({"other": [1]}).to_parquet(bronze / "without_event_id.parquet", index=False)

    assert ingest._cargar_eventos_existentes(str(bronze)) == {}


def test_detectar_duplicados_without_duplicates_does_not_create_log(tmp_path):
    df = pd.DataFrame(
        [
            {
                "event_id": "evt-1",
                "event": "PAYMENT_MADE",
                "user_id": "u1",
                "timestamp": "2026-01-01",
                "batch_id": "batch-1",
                "ingestion_timestamp": "2026-01-01T00:00:00Z",
            },
            {
                "event_id": "evt-2",
                "event": "PAYMENT_MADE",
                "user_id": "u2",
                "timestamp": "2026-01-01",
                "batch_id": "batch-1",
                "ingestion_timestamp": "2026-01-01T00:00:00Z",
            },
        ]
    )

    result = ingest.detectar_y_registrar_duplicados(df, carpeta_logs=str(tmp_path / "logs"))

    assert result["is_duplicate"].tolist() == [False, False]
    assert not (tmp_path / "logs").exists()
