"""
Resilient Parquet IO helpers.

Windows keeps strong locks on files opened by tools such as editors, previews,
DuckDB sessions, or dashboards. These helpers keep pipeline runs from failing
when the canonical output Parquet is temporarily locked.
"""

from __future__ import annotations

import json
import os
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd


PARQUET_WRITE_OPTIONS = {
    "coerce_timestamps": "us",
    "allow_truncated_timestamps": True,
}


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _as_project_relative(path: Path) -> str:
    try:
        return path.resolve().relative_to(_project_root()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def _manifest_path(canonical_path: Path) -> Path:
    return canonical_path.parent / "_latest" / f"{canonical_path.stem}.json"


def _central_root() -> Path:
    return _project_root() / "data" / "_pipeline_outputs"


def _safe_output_id(canonical_path: Path) -> str:
    rel = _as_project_relative(canonical_path)
    return re.sub(r"[^A-Za-z0-9_.-]+", "__", rel)


def _central_manifest_path(canonical_path: Path) -> Path:
    return _central_root() / "_latest" / f"{_safe_output_id(canonical_path)}.json"


def _versioned_path(canonical_path: Path) -> Path:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    suffix = uuid.uuid4().hex[:8]
    return canonical_path.parent / "_versions" / f"{canonical_path.stem}_{ts}_{suffix}{canonical_path.suffix}"


def _central_versioned_path(canonical_path: Path) -> Path:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    suffix = uuid.uuid4().hex[:8]
    safe_id = _safe_output_id(canonical_path)
    return _central_root() / "_versions" / safe_id / f"{canonical_path.stem}_{ts}_{suffix}{canonical_path.suffix}"


def _tmp_path(base_dir: Path, canonical_path: Path) -> Path:
    return base_dir / "_tmp" / f"{canonical_path.stem}_{os.getpid()}_{uuid.uuid4().hex}.tmp{canonical_path.suffix}"


def _write_manifest(canonical_path: Path, output_path: Path, rows: int, mode: str, error: str | None = None) -> None:
    payload: dict[str, Any] = {
        "canonical_path": _as_project_relative(canonical_path),
        "output_path": _as_project_relative(output_path),
        "rows": int(rows),
        "mode": mode,
        "updated_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    if error:
        payload["fallback_reason"] = error

    last_error: Exception | None = None
    for manifest in (_manifest_path(canonical_path), _central_manifest_path(canonical_path)):
        try:
            manifest.parent.mkdir(parents=True, exist_ok=True)
            tmp = manifest.with_name(f"{manifest.stem}_{os.getpid()}_{uuid.uuid4().hex}.tmp")
            tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, manifest)
            return
        except PermissionError as exc:
            last_error = exc

    if last_error:
        raise last_error


def _write_parquet_databricks_compatible(
    df: pd.DataFrame,
    path: Path,
    *,
    compression: str,
    engine: str,
) -> None:
    """
    Write Parquet with timestamp precision accepted by Databricks/Spark.

    Pandas/pyarrow writes timezone-aware datetimes as TIMESTAMP(NANOS,true) by
    default. Databricks rejects that physical type, so pipeline outputs are
    coerced to microsecond precision.
    """
    df.to_parquet(
        path,
        index=False,
        compression=compression,
        engine=engine,
        **PARQUET_WRITE_OPTIONS,
    )


def resolve_latest_parquet(canonical_path: str | os.PathLike[str]) -> Path:
    """
    Return the most recent readable Parquet path for a canonical output.

    If no manifest exists, the canonical path is returned for backwards
    compatibility.
    """
    canonical = Path(canonical_path)
    candidates: list[tuple[str, Path]] = []
    for manifest in (_manifest_path(canonical), _central_manifest_path(canonical)):
        if not manifest.exists():
            continue
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
            output = Path(data.get("output_path", ""))
            if not output.is_absolute():
                output = _project_root() / output
            if output.exists():
                candidates.append((data.get("updated_at_utc", ""), output))
        except Exception:
            pass

    if candidates:
        return sorted(candidates, key=lambda item: item[0])[-1][1]
    return canonical


def write_parquet_resilient(
    df: pd.DataFrame,
    canonical_path: str | os.PathLike[str],
    *,
    compression: str = "snappy",
    engine: str = "pyarrow",
    retries: int = 3,
    retry_sleep_seconds: float = 0.5,
) -> Path:
    """
    Write a Parquet output without letting a locked canonical file stop the run.

    The preferred path is the canonical Parquet. If replacing it fails with a
    PermissionError, a versioned Parquet is kept under `_versions/` and `_latest/`
    is updated so downstream readers can find the newest output.
    """
    canonical = Path(canonical_path)
    try:
        canonical.parent.mkdir(parents=True, exist_ok=True)
    except PermissionError:
        pass

    tmp = _tmp_path(canonical.parent, canonical)
    try:
        tmp.parent.mkdir(parents=True, exist_ok=True)
        _write_parquet_databricks_compatible(
            df, tmp, compression=compression, engine=engine
        )
    except PermissionError:
        tmp = _tmp_path(_central_root(), canonical)
        tmp.parent.mkdir(parents=True, exist_ok=True)
        _write_parquet_databricks_compatible(
            df, tmp, compression=compression, engine=engine
        )

    last_error: PermissionError | None = None
    for attempt in range(retries + 1):
        try:
            os.replace(tmp, canonical)
            _write_manifest(canonical, canonical, len(df), mode="canonical")
            return canonical
        except PermissionError as exc:
            last_error = exc
            if attempt < retries:
                time.sleep(retry_sleep_seconds)

    versioned = _versioned_path(canonical)
    try:
        versioned.parent.mkdir(parents=True, exist_ok=True)
        os.replace(tmp, versioned)
    except PermissionError:
        versioned = _central_versioned_path(canonical)
        versioned.parent.mkdir(parents=True, exist_ok=True)
        os.replace(tmp, versioned)

    reason = str(last_error) if last_error else "canonical file locked"
    mode = "central_fallback" if _central_root() in versioned.parents else "versioned_fallback"
    _write_manifest(canonical, versioned, len(df), mode=mode, error=reason)
    return versioned
