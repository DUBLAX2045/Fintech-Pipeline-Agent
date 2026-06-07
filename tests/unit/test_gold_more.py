from __future__ import annotations

import pandas as pd
import pytest

from src.gold.pipeline_gold import construir_user_360, guardar_gold, leer_silver


pytestmark = pytest.mark.unit


def test_construir_user_360_handles_no_successful_transactions():
    silver = pd.DataFrame(
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
                "event": "PAYMENT_FAILED",
                "is_failed": True,
                "amount_cop": 1000.0,
                "amount_usd": 0.25,
                "balance_after": 1000.0,
                "merchant": "Rappi",
                "category": "food",
                "channel": "app",
                "device": "mobile",
            }
        ]
    )

    result = construir_user_360(silver)

    assert result.loc[0, "total_transactions"] == 0
    assert result.loc[0, "failed_transactions"] == 1
    assert result.loc[0, "failure_rate"] == 1.0


def test_guardar_gold_and_leer_silver_use_resilient_paths(tmp_path):
    silver_dir = tmp_path / "silver"
    gold_dir = tmp_path / "gold"
    silver_dir.mkdir()
    pd.DataFrame({"user_id": ["u1"]}).to_parquet(silver_dir / "silver_events.parquet", index=False)

    loaded = leer_silver(str(silver_dir))
    assert len(loaded) == 1

    user = pd.DataFrame({"user_id": ["u1"], "total_amount_cop": [1000.0]})
    daily = pd.DataFrame({"date": [pd.Timestamp("2026-01-01").date()], "total_events": [1]})
    summary = pd.DataFrame({"event": ["PAYMENT_MADE"], "count": [1]})

    paths = guardar_gold(user, daily, summary, str(gold_dir))

    assert set(paths) == {
        "gold_user_360.parquet",
        "gold_daily_metrics.parquet",
        "gold_event_summary.parquet",
    }
    for path in paths.values():
        assert pd.read_parquet(path).shape[0] == 1
