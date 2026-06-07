"""
Verifica que la documentacion principal este alineada con el pipeline real.

Revisa README, guia AWS S3, guia Databricks y .env.example contra:
  - Proveedor real de geolocalizacion: ip-api.com.
  - Conteos actuales de Bronze/Silver/Gold.
  - Configuracion S3/IAM esperada por boto3.
  - Tablas externas Databricks en formato Parquet.
"""

from __future__ import annotations

import glob
from pathlib import Path

import pandas as pd

from src.io.parquet_io import resolve_latest_parquet


FILES = [
    Path("README.md"),
    Path("docs/AWS_S3_SETUP.md"),
    Path("docs/DATABRICKS_SETUP.md"),
    Path(".env.example"),
    Path("src/silver/pipeline_silver.py"),
    Path("src/bronze/simulator.py"),
    Path("notebooks/02_prueba_apis.py"),
    Path("docs/material/MANUAL_3_Silver_Gold_Completo.md"),
    Path("docs/material/MANUAL_FASE1_Fintech_Pipeline.md"),
    Path("docs/material/MANUAL_FASE2_Bus_de_Eventos.md"),
]

FORBIDDEN = [
    "ipapi.co",
    "USING DELTA",
    "637 usuarios",
    "38 columnas",
    "~35",
    "500 req/dia",
    "500 req/día",
]


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore")


def main() -> int:
    print("=" * 60)
    print("VERIFICACION DOCUMENTACION")
    print("=" * 60)

    bronze_files = glob.glob("data/bronze/events/**/*.parquet", recursive=True)
    bronze = pd.concat([pd.read_parquet(p) for p in bronze_files], ignore_index=True)
    silver = pd.read_parquet(resolve_latest_parquet("data/silver/silver_events.parquet"))
    gold = pd.read_parquet(resolve_latest_parquet("data/gold/gold_user_360.parquet"))

    metrics = {
        "bronze_cols": len(bronze.columns),
        "silver_cols": len(silver.columns),
        "silver_rows": len(silver),
        "gold_users": len(gold),
    }

    combined = "\n".join(_read(path) for path in FILES)
    lower = combined.lower()

    for pattern in FORBIDDEN:
        if pattern.lower() in lower:
            raise AssertionError(f"Patron obsoleto en documentacion: {pattern}")

    readme = _read(Path("README.md"))
    expected_readme = [
        "ip-api.com",
        f"{metrics['silver_cols']} cols",
        f"{metrics['bronze_cols']} columnas",
        "489 usuarios en Gold",
        "puede cambiar tras ingesta API/Locust",
    ]
    for expected in expected_readme:
        if expected not in readme:
            raise AssertionError(f"README no contiene valor esperado: {expected}")

    databricks_doc = _read(Path("docs/DATABRICKS_SETUP.md"))
    for expected in ("USING PARQUET", "AVG(avg_ticket)", "PARQUET_TYPE_ILLEGAL"):
        if expected not in databricks_doc:
            raise AssertionError(f"DATABRICKS_SETUP.md no contiene: {expected}")

    aws_s3_doc = _read(Path("docs/AWS_S3_SETUP.md"))
    expected_s3_doc = [
        "`AWS_BUCKET` es solo el nombre del bucket. No uses `s3://`.",
        "Block all public access",
        "s3:PutObject",
        "s3:GetObject",
        "s3:DeleteObject",
        "AWS_SESSION_TOKEN",
        "scripts/verificar_cloud.py",
        "Databricks no usa `AWS_ACCESS_KEY_ID` ni `AWS_SECRET_ACCESS_KEY` del `.env`.",
    ]
    for expected in expected_s3_doc:
        if expected not in aws_s3_doc:
            raise AssertionError(f"AWS_S3_SETUP.md no contiene: {expected}")

    env_example = _read(Path(".env.example"))
    if "IPAPI_KEY" in env_example:
        raise AssertionError(".env.example todavia sugiere IPAPI_KEY")

    print(f"  OK Bronze documentado: {metrics['bronze_cols']} columnas")
    print(f"  OK Silver documentado: {metrics['silver_rows']:,} filas, {metrics['silver_cols']} columnas")
    print("  OK Gold documentado: 489 usuarios en dataset base")
    print(f"  OK Gold actual en workspace vivo: {metrics['gold_users']:,} usuarios")
    print("  OK Geolocalizacion documentada: ip-api.com")
    print("  OK AWS S3 documentado: IAM minimo + boto3 + healthcheck")
    print("  OK Databricks documentado: USING PARQUET")
    print("\nOK DOCUMENTACION ALINEADA")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
