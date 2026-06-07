"""
Verificacion integral de servicios cloud del fintech pipeline.

Valida:
  - ExchangeRate API con red real.
  - AWS S3: acceso al bucket y permiso PutObject/DeleteObject temporal.
  - Databricks: SQL Warehouse, catalogo, schema y tablas visibles.
"""

from __future__ import annotations

import sys
import time

import requests

from src.config.databricks_config import verificar_conexion as verificar_databricks
from src.ingesta.uploader_s3 import verificar_s3


def verificar_exchangerate() -> dict:
    t0 = time.time()
    try:
        response = requests.get("https://open.er-api.com/v6/latest/COP", timeout=10)
        response.raise_for_status()
        payload = response.json()
        tasa = float(payload["rates"]["USD"])
        if tasa <= 0:
            raise ValueError(f"Tasa USD invalida: {tasa}")
        return {
            "ok": True,
            "tasa_cop_usd": tasa,
            "duracion_seg": round(time.time() - t0, 2),
            "error": None,
        }
    except Exception as exc:
        return {
            "ok": False,
            "tasa_cop_usd": None,
            "duracion_seg": round(time.time() - t0, 2),
            "error": str(exc),
        }


def main() -> int:
    print("=" * 60)
    print("VERIFICACION CLOUD - FINTECH PIPELINE")
    print("=" * 60)

    print("\nExchangeRate")
    fx = verificar_exchangerate()
    if fx["ok"]:
        print(f"  OK API responde en {fx['duracion_seg']}s")
        print(f"  1 COP = {fx['tasa_cop_usd']:.8f} USD")
    else:
        print(f"  ERROR {fx['error']}")

    print("\nAWS S3")
    s3 = verificar_s3(probar_escritura=True)
    if s3["ok"]:
        print(f"  OK bucket s3://{s3['bucket']} ({s3['region']}) accesible")
        print(f"  head_bucket: {'OK' if s3['head_bucket_ok'] else 'FALLO'}")
        print(f"  put_object : {'OK' if s3['write_test_ok'] else 'FALLO'}")
        print(f"  delete_obj : {'OK' if s3['delete_test_ok'] else 'FALLO'}")
        for advertencia in s3.get("advertencias", []):
            print(f"  Advertencia: {advertencia}")
    else:
        print(f"  ERROR {s3['error']}")

    print("\nDatabricks")
    db = verificar_databricks()
    if db["ok"]:
        print(f"  OK warehouse responde en {db['duracion_seg']}s")
        print(f"  SELECT 1 : {'OK' if db['select_ok'] else 'FALLO'}")
        print(f"  Catalogo : {db['catalog']} ({'OK' if db['catalog_exists'] else 'FALLO'})")
        print(f"  Schema   : {db['schema']} ({'OK' if db['schema_exists'] else 'FALLO'})")
        print(f"  Agente   : {'OK' if db['ready_for_agent'] else 'PENDIENTE TABLAS'}")
        tablas = db.get("tablas_encontradas", [])
        print(f"  Tablas visibles: {len(tablas)}")
        for tabla in tablas[:20]:
            print(f"    - {tabla}")
        for advertencia in db.get("advertencias", []):
            print(f"  Advertencia: {advertencia}")
    else:
        print(f"  ERROR {db['error']}")

    ok = bool(fx["ok"] and s3["ok"] and db["ok"])
    ready_for_agent = bool(ok and db.get("ready_for_agent", False))
    print("\n" + "=" * 60)
    if ready_for_agent:
        print("OK CLOUD SANO Y DATABRICKS LISTO PARA EL AGENTE")
    elif ok:
        print("OK CONECTIVIDAD CLOUD; DATABRICKS PENDIENTE DE TABLAS GOLD")
    else:
        print("ERROR CLOUD NO SANO")
    print("=" * 60)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
