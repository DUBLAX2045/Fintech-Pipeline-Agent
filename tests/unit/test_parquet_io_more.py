from __future__ import annotations

import json
import os
from pathlib import Path

import pandas as pd
import pytest

from src.io import parquet_io


pytestmark = pytest.mark.unit


def test_write_parquet_resilient_uses_versioned_path_when_canonical_locked(tmp_path, monkeypatch):
    canonical = tmp_path / "gold_user_360.parquet"
    df = pd.DataFrame({"user_id": ["u1"], "amount": [100.0]})
    real_replace = os.replace

    def replace_with_locked_canonical(src, dst):
        if Path(dst) == canonical:
            raise PermissionError("locked canonical")
        return real_replace(src, dst)

    monkeypatch.setattr(parquet_io.os, "replace", replace_with_locked_canonical)

    written = parquet_io.write_parquet_resilient(
        df,
        canonical,
        retries=0,
        retry_sleep_seconds=0,
    )

    assert written != canonical
    assert written.parent.name == "_versions"
    assert parquet_io.resolve_latest_parquet(canonical) == written
    assert pd.read_parquet(written).loc[0, "user_id"] == "u1"

    manifest = canonical.parent / "_latest" / "gold_user_360.json"
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    assert payload["mode"] == "versioned_fallback"
    assert payload["rows"] == 1


def test_resolve_latest_parquet_ignores_invalid_or_missing_manifest(tmp_path):
    canonical = tmp_path / "silver_events.parquet"
    latest_dir = tmp_path / "_latest"
    latest_dir.mkdir()
    (latest_dir / "silver_events.json").write_text("{bad json", encoding="utf-8")

    assert parquet_io.resolve_latest_parquet(canonical) == canonical

    (latest_dir / "silver_events.json").write_text(
        json.dumps({"output_path": "missing.parquet", "updated_at_utc": "2026-01-01T00:00:00Z"}),
        encoding="utf-8",
    )
    assert parquet_io.resolve_latest_parquet(canonical) == canonical


def test_project_relative_and_safe_output_id_for_external_path(tmp_path):
    external = Path.home() / "folder with spaces" / "out.parquet"

    relative = parquet_io._as_project_relative(external)
    safe_id = parquet_io._safe_output_id(external)

    assert external.resolve().as_posix() in relative
    assert " " not in safe_id
    assert safe_id.endswith("out.parquet")
