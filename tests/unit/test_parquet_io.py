from __future__ import annotations

import pandas as pd
import pytest

from src.io.parquet_io import resolve_latest_parquet, write_parquet_resilient


pytestmark = pytest.mark.unit


def test_write_parquet_resilient_writes_and_resolves_latest(tmp_path):
    canonical = tmp_path / "silver_events.parquet"
    df = pd.DataFrame(
        {
            "event_id": ["evt-1"],
            "timestamp": [pd.Timestamp("2026-01-01T10:00:00Z")],
            "amount": [100000.0],
        }
    )

    written = write_parquet_resilient(df, canonical)
    resolved = resolve_latest_parquet(canonical)
    loaded = pd.read_parquet(resolved)

    assert written == canonical
    assert resolved == canonical
    assert len(loaded) == 1
    assert loaded.loc[0, "event_id"] == "evt-1"
