from __future__ import annotations

import json

import pandas as pd
import pytest

from src.agent import tools


pytestmark = pytest.mark.unit


@pytest.fixture
def gold_files(tmp_path, monkeypatch):
    gold_dir = tmp_path / "gold"
    gold_dir.mkdir()
    pd.DataFrame(
        {
            "user_id": ["u1", "u2"],
            "user_segment": ["premium", "student"],
            "total_amount_cop": [1000.0, 2000.0],
            "user_email": ["a@example.com", "b@example.com"],
        }
    ).to_parquet(gold_dir / "gold_user_360.parquet", index=False)
    pd.DataFrame({"date": ["2026-01-01"], "total_events": [2]}).to_parquet(
        gold_dir / "gold_daily_metrics.parquet",
        index=False,
    )
    pd.DataFrame({"event": ["PAYMENT_MADE"], "count": [2]}).to_parquet(
        gold_dir / "gold_event_summary.parquet",
        index=False,
    )

    monkeypatch.setattr(tools, "_GOLD_DIR", gold_dir)
    monkeypatch.setattr(
        tools,
        "_TABLAS",
        {
            "gold_user_360": gold_dir / "gold_user_360.parquet",
            "gold_daily_metrics": gold_dir / "gold_daily_metrics.parquet",
            "gold_event_summary": gold_dir / "gold_event_summary.parquet",
        },
    )
    return gold_dir


def test_ejecutar_sql_success_security_and_execution_error(gold_files, monkeypatch):
    payload = json.loads(tools.ejecutar_sql("SELECT user_segment, total_amount_cop FROM gold_user_360"))

    assert payload["filas"] == 2
    assert payload["columnas"] == ["user_segment", "total_amount_cop"]

    blocked = json.loads(tools.ejecutar_sql("DROP TABLE gold_user_360"))
    assert blocked["tipo"] == "seguridad"

    error = json.loads(tools.ejecutar_sql("SELECT * FROM missing_table"))
    assert error["tipo"] == "ejecucion"


def test_obtener_esquema_reports_files(gold_files):
    text = tools.obtener_esquema()

    assert "gold_user_360: 2 filas" in text
    assert "gold_daily_metrics: 1 filas" in text
    assert "gold_event_summary" in text


def test_generar_grafico_success_and_errors(gold_files):
    result = json.loads(
        tools.generar_grafico(
            "SELECT user_segment, total_amount_cop FROM gold_user_360",
            "bar",
            "Volumen Segmento",
            "user_segment",
            "total_amount_cop",
        )
    )
    assert result["filas_graficadas"] == 2
    assert (gold_files / "graficos" / "volumen_segmento.png").exists()

    blocked = json.loads(
        tools.generar_grafico("DELETE FROM gold_user_360", "bar", "Bad", "x", "y")
    )
    assert "error" in blocked

    empty = json.loads(
        tools.generar_grafico(
            "SELECT user_segment, total_amount_cop FROM gold_user_360 WHERE user_segment = 'none'",
            "bar",
            "Empty",
            "user_segment",
            "total_amount_cop",
        )
    )
    assert "error" in empty
