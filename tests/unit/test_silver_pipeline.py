from __future__ import annotations

import pandas as pd
import pytest

from src.silver.pipeline_silver import paso2b_deduplicar_eventos, paso3_agregar_flags


pytestmark = pytest.mark.unit


def test_paso2b_deduplicar_eventos_keeps_canonical_row():
    df = pd.DataFrame(
        [
            {
                "event_id": "evt-1",
                "is_duplicate": False,
                "ingestion_timestamp": "2026-01-01T00:00:00Z",
                "amount": 100,
            },
            {
                "event_id": "evt-1",
                "is_duplicate": True,
                "ingestion_timestamp": "2026-01-01T00:00:01Z",
                "amount": 999,
            },
            {
                "event_id": "evt-2",
                "is_duplicate": False,
                "ingestion_timestamp": "2026-01-01T00:00:02Z",
                "amount": 200,
            },
        ]
    )

    result = paso2b_deduplicar_eventos(df)

    assert len(result) == 2
    assert result["event_id"].tolist() == ["evt-1", "evt-2"]
    assert result.loc[result["event_id"] == "evt-1", "amount"].item() == 100
    assert result.loc[result["event_id"] == "evt-1", "bronze_duplicate_count"].item() == 2


def test_paso3_agregar_flags_sets_failure_transactional_and_private_ip():
    df = pd.DataFrame(
        [
            {"event_status": "FAILED", "event": "PAYMENT_FAILED", "ip": "192.168.1.20"},
            {"event_status": "SUCCESS", "event": "USER_REGISTERED", "ip": "8.8.8.8"},
        ]
    )

    result = paso3_agregar_flags(df)

    assert result["is_failed"].tolist() == [True, False]
    assert result["is_transactional"].tolist() == [True, False]
    assert result["ip_is_private"].tolist() == [True, False]
    assert result["geo_source"].tolist() == ["payload_location", "ip-api.com"]
