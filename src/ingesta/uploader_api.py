"""Upload files to Databricks DBFS through the REST API."""

from __future__ import annotations

import os
from pathlib import Path

import requests
from dotenv import load_dotenv


load_dotenv()

DATABRICKS_HOST = (
    os.getenv("DATABRICKS_HOST", "")
    .replace("https://", "")
    .replace("http://", "")
    .rstrip("/")
)
DATABRICKS_TOKEN = os.getenv("DATABRICKS_TOKEN", "")


def _dbfs_put_url() -> str:
    if not DATABRICKS_HOST:
        raise EnvironmentError("Falta DATABRICKS_HOST en .env")
    return f"https://{DATABRICKS_HOST}/api/2.0/dbfs/put"


def _headers() -> dict:
    if not DATABRICKS_TOKEN:
        raise EnvironmentError("Falta DATABRICKS_TOKEN en .env")
    return {"Authorization": f"Bearer {DATABRICKS_TOKEN}"}


def subir_archivo_dbfs(local_path: str, dbfs_path: str) -> bool:
    """Upload one local file to DBFS. Returns True when Databricks accepts it."""
    url = _dbfs_put_url()
    headers = _headers()

    with open(local_path, "rb") as file_obj:
        response = requests.post(
            url,
            headers=headers,
            data={"path": dbfs_path, "overwrite": "true"},
            files={"file": file_obj},
            timeout=30,
        )

    if response.status_code == 200:
        print(f"Subido: {dbfs_path}")
        return True

    print(f"Error {response.status_code}: {response.text}")
    return False


def subir_parquets(local_folder: str, dbfs_base_path: str) -> dict:
    """Upload all Parquet files from a folder to DBFS."""
    resultado = {"subidos": 0, "errores": 0, "archivos": []}
    folder = Path(local_folder)
    if not folder.exists():
        print(f"Ruta no existe: {local_folder}")
        return resultado

    for local_file in folder.rglob("*.parquet"):
        relative_path = local_file.relative_to(folder).as_posix()
        dbfs_file = f"{dbfs_base_path}/{relative_path}"

        print(f"{local_file} -> {dbfs_file}")
        if subir_archivo_dbfs(str(local_file), dbfs_file):
            resultado["subidos"] += 1
            resultado["archivos"].append(dbfs_file)
        else:
            resultado["errores"] += 1

    return resultado
