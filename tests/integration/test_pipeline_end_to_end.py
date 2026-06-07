from __future__ import annotations

import pytest

from src.bronze.ingest import aplanar_todos, detectar_y_registrar_duplicados
from src.bronze.metadata import agregar_metadatos_ingesta
from src.bronze.save import guardar_bronze_parquet
from src.gold.pipeline_gold import ejecutar_pipeline_gold
from src.silver import pipeline_silver as silver_module


pytestmark = pytest.mark.integration


def test_silver_to_gold_end_to_end_with_temp_data(tmp_path, monkeypatch, make_fintech_event):
    bronze_dir = tmp_path / "bronze" / "events"
    silver_dir = tmp_path / "silver"
    gold_dir = tmp_path / "gold"

    events = [
        make_fintech_event(event_id="evt-1", user_id="user_1", amount=100000.0),
        make_fintech_event(event_id="evt-1", user_id="user_1", amount=100000.0),
        make_fintech_event(
            event_id="evt-2",
            user_id="user_2",
            event="PAYMENT_FAILED",
            status="FAILED",
            amount=50000.0,
        ),
        make_fintech_event(
            event_id="evt-3",
            user_id="user_2",
            event="PURCHASE_MADE",
            status="SUCCESS",
            amount=200000.0,
        ),
    ]

    bronze = aplanar_todos(events)
    bronze = agregar_metadatos_ingesta(bronze, "pytest.json")
    bronze = detectar_y_registrar_duplicados(bronze, carpeta_logs=str(tmp_path / "logs"))
    guardar_bronze_parquet(bronze, str(bronze_dir))

    monkeypatch.setattr(silver_module.fx, "tasa_cop_usd", lambda: 0.00025)

    silver = silver_module.ejecutar_pipeline_silver(
        carpeta_bronze=str(bronze_dir),
        carpeta_silver=str(silver_dir),
    )
    gold = ejecutar_pipeline_gold(carpeta_silver=str(silver_dir), carpeta_gold=str(gold_dir))

    assert len(silver) == 3
    assert silver.duplicated(subset=["event_id"], keep=False).sum() == 0
    assert len(gold["user_360"]) == 2
    assert len(gold["daily"]) == 1
    assert len(gold["summary"]) == 3
