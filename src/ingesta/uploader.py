"""Upload local Parquet files to Databricks DBFS with the CLI."""

from __future__ import annotations

import os
import subprocess


def subir_parquets(local_path: str, dbfs_path: str) -> dict:
    """
    Upload Parquet files to DBFS preserving the local folder structure.

    Returns:
        Dict with counters: subidos, errores and archivos.
    """
    resultado = {"subidos": 0, "errores": 0, "archivos": []}

    if not os.path.exists(local_path):
        print(f"Ruta no existe: {local_path}")
        return resultado

    print(f"\nSubiendo desde {local_path} -> {dbfs_path}\n")

    for root, _, files in os.walk(local_path):
        for file in files:
            if not file.endswith(".parquet"):
                continue

            local_file = os.path.join(root, file)
            relative_path = os.path.relpath(local_file, local_path)
            dbfs_file = f"{dbfs_path}/{relative_path}".replace("\\", "/")

            print(f"{local_file} -> {dbfs_file}")

            try:
                subprocess.run(
                    [
                        "databricks",
                        "fs",
                        "cp",
                        local_file,
                        dbfs_file,
                        "--overwrite",
                    ],
                    check=True,
                )
                resultado["subidos"] += 1
                resultado["archivos"].append(dbfs_file)
            except subprocess.CalledProcessError as exc:
                print(f"Error subiendo {local_file}: {exc}")
                resultado["errores"] += 1

    print("\nSubida completada\n")
    return resultado
