from __future__ import annotations

import pandas as pd
import pytest

from src.gold.pipeline_gold import construir_daily_metrics, construir_event_summary, construir_user_360


pytestmark = pytest.mark.unit


def _silver_sample() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "event_id": "evt-1",
                "user_id": "user_1",
                "user_name": "Alice",
                "user_email": "alice@example.com",
                "user_age": 30,
                "user_segment": "premium",
                "location_city": "Bogota",
                "timestamp": pd.Timestamp("2026-01-01T10:00:00Z"),
                "date": pd.Timestamp("2026-01-01").date(),
                "event": "PAYMENT_MADE",
                "is_failed": False,
                "is_transactional": True,
                "amount_cop": 100000.0,
                "amount_usd": 25.0,
                "balance_after": 900000.0,
                "merchant": "Rappi",
                "category": "food",
                "channel": "app",
                "device": "mobile",
            },
            {
                "event_id": "evt-2",
                "user_id": "user_1",
                "user_name": "Alice",
                "user_email": "alice@example.com",
                "user_age": 30,
                "user_segment": "premium",
                "location_city": "Bogota",
                "timestamp": pd.Timestamp("2026-01-01T11:00:00Z"),
                "date": pd.Timestamp("2026-01-01").date(),
                "event": "PAYMENT_FAILED",
                "is_failed": True,
                "is_transactional": True,
                "amount_cop": 50000.0,
                "amount_usd": 12.5,
                "balance_after": 900000.0,
                "merchant": "Rappi",
                "category": "food",
                "channel": "app",
                "device": "mobile",
            },
        ]
    )


def test_construir_user_360_calculates_user_metrics():
    result = construir_user_360(_silver_sample())

    assert len(result) == 1
    row = result.iloc[0]
    assert row["user_id"] == "user_1"
    assert row["total_transactions"] == 1
    assert row["total_amount_cop"] == 100000.0
    assert row["failed_transactions"] == 1
    assert row["failure_rate"] == 0.5
    assert row["top_merchant"] == "Rappi"


def test_gold_support_tables_are_built():
    silver = _silver_sample()

    daily = construir_daily_metrics(silver)
    summary = construir_event_summary(silver)

    assert len(daily) == 1
    assert daily.loc[0, "total_events"] == 2
    assert set(summary["event"]) == {"PAYMENT_MADE", "PAYMENT_FAILED"}
