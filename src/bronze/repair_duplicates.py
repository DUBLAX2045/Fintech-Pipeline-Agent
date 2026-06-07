"""
Repair duplicate flags in existing Bronze Parquet files.

Bronze remains append-only at the event level: no events are deleted. This tool
only repairs audit columns so historical duplicated event_id values are marked
consistently after the deduplication rules are improved.
"""

from __future__ import annotations

import argparse
import glob
import os
import sys
import uuid
from pathlib import Path

import pandas as pd

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))


AUDIT_COLUMNS = [
    "is_duplicate",
    "duplicate_reason",
    "duplicate_first_seen_batch_id",
    "duplicate_first_seen_file",
]


def _normalizar_event_id(value) -> str | None:
    if pd.isna(value):
        return None
    text = str(value).strip()
    return text or None


def _atomic_write_parquet(df: pd.DataFrame, path: str) -> None:
    target = Path(path)
    tmp = target.with_name(f"{target.stem}_repair_{uuid.uuid4().hex[:8]}.tmp{target.suffix}")
    df.to_parquet(tmp, index=False, compression="snappy", engine="pyarrow")
    os.replace(tmp, target)


def reparar_flags_duplicados_bronze(
    carpeta_bronze: str = "data/bronze/events",
    *,
    dry_run: bool = False,
) -> dict:
    archivos = sorted(glob.glob(os.path.join(carpeta_bronze, "**", "*.parquet"), recursive=True))
    if not archivos:
        raise FileNotFoundError(f"No se encontraron Parquets Bronze en {carpeta_bronze}")

    frames = []
    for archivo in archivos:
        df = pd.read_parquet(archivo).copy()
        if "event_id" not in df.columns:
            print(f"Saltando {archivo}: no tiene event_id")
            continue
        df["__bronze_file"] = archivo
        df["__row_order"] = range(len(df))
        frames.append(df)

    if not frames:
        raise ValueError("No hay archivos Bronze con columna event_id")

    all_rows = pd.concat(frames, ignore_index=True)
    all_rows["__event_id_norm"] = all_rows["event_id"].map(_normalizar_event_id)
    all_rows["__missing_event_id"] = all_rows["__event_id_norm"].isna()
    all_rows["__ingestion_order"] = pd.to_datetime(
        all_rows.get("ingestion_timestamp"),
        utc=True,
        errors="coerce",
    )

    with_id = all_rows[~all_rows["__missing_event_id"]].copy()
    with_id = with_id.sort_values(
        by=["__event_id_norm", "__ingestion_order", "__bronze_file", "__row_order"],
        kind="mergesort",
    )
    with_id["__duplicate_rank"] = with_id.groupby("__event_id_norm").cumcount()

    canonical = (
        with_id[with_id["__duplicate_rank"] == 0]
        .set_index("__event_id_norm")[["batch_id", "__bronze_file"]]
        .to_dict(orient="index")
    )

    all_rows["is_duplicate"] = False
    all_rows["duplicate_reason"] = None
    all_rows["duplicate_first_seen_batch_id"] = None
    all_rows["duplicate_first_seen_file"] = None

    duplicate_rank = with_id.set_index(["__bronze_file", "__row_order"])["__duplicate_rank"]
    keys = list(zip(all_rows["__bronze_file"], all_rows["__row_order"]))
    all_rows["__duplicate_rank"] = [duplicate_rank.get(key, 0) for key in keys]

    missing_mask = all_rows["__missing_event_id"]
    duplicate_mask = (~missing_mask) & (all_rows["__duplicate_rank"] > 0)
    all_rows.loc[missing_mask, "is_duplicate"] = True
    all_rows.loc[missing_mask, "duplicate_reason"] = "missing_event_id"
    all_rows.loc[duplicate_mask, "is_duplicate"] = True

    for idx in all_rows[duplicate_mask].index:
        event_id = all_rows.at[idx, "__event_id_norm"]
        first = canonical.get(event_id, {})
        first_batch = first.get("batch_id")
        first_file = first.get("__bronze_file")
        current_batch = all_rows.at[idx, "batch_id"] if "batch_id" in all_rows.columns else None
        reason = "repeated_in_batch" if current_batch == first_batch else "seen_in_bronze"
        all_rows.at[idx, "duplicate_reason"] = reason
        all_rows.at[idx, "duplicate_first_seen_batch_id"] = first_batch
        all_rows.at[idx, "duplicate_first_seen_file"] = first_file

    total_rows = len(all_rows)
    unique_event_ids = all_rows["__event_id_norm"].nunique(dropna=True)
    marked_duplicates = int(all_rows["is_duplicate"].sum())

    print("Resumen reparacion Bronze:")
    print(f"  Archivos:              {len(archivos):,}")
    print(f"  Registros:             {total_rows:,}")
    print(f"  Event_id unicos:       {unique_event_ids:,}")
    print(f"  Duplicados marcados:   {marked_duplicates:,}")
    print(f"  Dry run:               {'SI' if dry_run else 'NO'}")

    temp_cols = [
        "__bronze_file", "__row_order", "__event_id_norm",
        "__missing_event_id", "__ingestion_order", "__duplicate_rank",
    ]
    original_by_file = all_rows.groupby("__bronze_file", sort=False)
    for archivo, group in original_by_file:
        repaired = (
            group.sort_values("__row_order", kind="mergesort")
            .drop(columns=[col for col in temp_cols if col in group.columns])
            .reset_index(drop=True)
        )
        if not dry_run:
            _atomic_write_parquet(repaired, archivo)
        print(f"  {'Revisado' if dry_run else 'Reparado'}: {archivo} ({len(repaired):,} filas)")

    return {
        "archivos": len(archivos),
        "registros": total_rows,
        "event_id_unicos": unique_event_ids,
        "duplicados_marcados": marked_duplicates,
        "dry_run": dry_run,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Repara flags de duplicados en Bronze")
    parser.add_argument("--carpeta-bronze", default="data/bronze/events")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    reparar_flags_duplicados_bronze(args.carpeta_bronze, dry_run=args.dry_run)

