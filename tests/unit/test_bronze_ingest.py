from __future__ import annotations

import pandas as pd
import pytest

from src.bronze.ingest import aplanar_evento, detectar_y_registrar_duplicados


pytestmark = pytest.mark.unit


def test_aplanar_evento_maps_nested_payload(make_fintech_event):
    row = aplanar_evento(make_fintech_event(event_id="evt-123", user_id="user_99"))

    assert row["event_id"] == "evt-123"
    assert row["user_id"] == "user_99"
    assert row["amount"] == 100000.0
    assert row["location_city"] == "Bogota"
    assert row["ip"] == "192.168.1.10"


def test_detectar_duplicados_batch_and_historical(tmp_path):
    bronze_dir = tmp_path / "bronze" / "events"
    historical_dir = bronze_dir / "date=2026-01-01"
    historical_dir.mkdir(parents=True)
    pd.DataFrame(
        [
            {
                "event_id": "evt-historical",
                "batch_id": "batch-old",
                "ingestion_timestamp": "2026-01-01T00:00:00Z",
                "source_file": "old.json",
            }
        ]
    ).to_parquet(historical_dir / "batch_old.parquet", index=False)

    df = pd.DataFrame(
        [
            {
                "event_id": "evt-historical",
                "event": "PAYMENT_MADE",
                "user_id": "u1",
                "timestamp": "2026-01-01T10:00:00Z",
                "batch_id": "batch-new",
                "ingestion_timestamp": "2026-01-02T00:00:00Z",
            },
            {
                "event_id": "evt-batch",
                "event": "PAYMENT_MADE",
                "user_id": "u2",
                "timestamp": "2026-01-01T10:00:00Z",
                "batch_id": "batch-new",
                "ingestion_timestamp": "2026-01-02T00:00:00Z",
            },
            {
                "event_id": "evt-batch",
                "event": "PAYMENT_MADE",
                "user_id": "u2",
                "timestamp": "2026-01-01T10:00:01Z",
                "batch_id": "batch-new",
                "ingestion_timestamp": "2026-01-02T00:00:00Z",
            },
        ]
    )

    result = detectar_y_registrar_duplicados(
        df,
        carpeta_logs=str(tmp_path / "logs"),
        carpeta_bronze=str(bronze_dir),
    )

    assert result["is_duplicate"].tolist() == [True, False, True]
    assert result.loc[0, "duplicate_reason"] == "seen_in_bronze"
    assert result.loc[2, "duplicate_reason"] == "repeated_in_batch"
