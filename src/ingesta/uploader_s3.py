"""
uploader_s3.py - Sube Parquets de Silver y Gold a AWS S3.

Flujo del proyecto:
  Parquet local -> AWS S3 -> Databricks / herramientas analiticas

Variables requeridas en .env:
    AWS_ACCESS_KEY_ID
    AWS_SECRET_ACCESS_KEY
    AWS_REGION
    AWS_BUCKET

Variable opcional:
    AWS_SESSION_TOKEN
"""

from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

import boto3
from boto3.exceptions import S3UploadFailedError
from botocore.exceptions import ClientError, NoCredentialsError
from dotenv import load_dotenv

try:
    from src.io.parquet_io import resolve_latest_parquet
except ImportError:
    sys.path.append(str(Path(__file__).resolve().parents[2]))
    from src.io.parquet_io import resolve_latest_parquet

load_dotenv()

_AWS_KEY = os.getenv("AWS_ACCESS_KEY_ID", "")
_AWS_SECRET = os.getenv("AWS_SECRET_ACCESS_KEY", "")
_AWS_SESSION_TOKEN = os.getenv("AWS_SESSION_TOKEN", "")
_AWS_REGION = os.getenv("AWS_REGION", "us-east-1")
BUCKET = os.getenv("AWS_BUCKET", "")


def _get_s3_client():
    """Crea y retorna el cliente S3. Lanza error descriptivo si falta config."""
    if not _AWS_KEY or not _AWS_SECRET:
        raise EnvironmentError(
            "Faltan credenciales AWS en .env: AWS_ACCESS_KEY_ID y AWS_SECRET_ACCESS_KEY"
        )
    if not BUCKET:
        raise EnvironmentError("Falta AWS_BUCKET en .env")

    kwargs = {
        "aws_access_key_id": _AWS_KEY,
        "aws_secret_access_key": _AWS_SECRET,
        "region_name": _AWS_REGION,
    }
    if _AWS_SESSION_TOKEN:
        kwargs["aws_session_token"] = _AWS_SESSION_TOKEN
    return boto3.client("s3", **kwargs)


def _client_error_message(e: ClientError, accion: str) -> str:
    error = e.response.get("Error", {})
    code = error.get("Code", "Unknown")
    message = error.get("Message", str(e))
    return f"S3 error {code} en {accion}: {message}"


def _parquet_files(local_folder: str):
    ignored_dirs = {"_latest", "_versions", "_tmp", "__pycache__"}
    for root, dirs, files in os.walk(local_folder):
        dirs[:] = [d for d in dirs if d not in ignored_dirs]
        for file in files:
            if file.endswith(".parquet"):
                yield Path(root) / file


def _resolver_fuente_subida(canonical_path: Path) -> Path:
    """
    Usa el ultimo Parquet disponible si el canonical fue redirigido por locks.

    El objeto en S3 conserva la clave canonica para que Databricks no apunte a
    rutas versionadas locales.
    """
    try:
        latest = Path(resolve_latest_parquet(canonical_path))
        if latest.exists():
            return latest
    except Exception:
        pass
    return canonical_path


def subir_parquets(local_folder: str, capa: str) -> dict:
    """
    Sube todos los .parquet de una carpeta local a S3.

    Args:
        local_folder: Ruta local, por ejemplo "data/silver".
        capa: Prefijo S3, por ejemplo "silver" o "gold".

    Returns:
        {"subidos": int, "errores": int, "archivos": list[str]}
    """
    resultado = {"subidos": 0, "errores": 0, "archivos": []}

    if not os.path.exists(local_folder):
        print(f"Carpeta no existe: {local_folder}")
        return resultado

    try:
        s3 = _get_s3_client()
    except EnvironmentError as e:
        print(e)
        return resultado

    print(f"\nSubiendo {capa} -> s3://{BUCKET}/{capa}/")

    local_root = Path(local_folder)
    for canonical_path in _parquet_files(local_folder):
        rel_path = canonical_path.relative_to(local_root)
        s3_key = f"{capa}/{rel_path.as_posix()}"
        upload_path = _resolver_fuente_subida(canonical_path)

        try:
            s3.upload_file(str(upload_path), BUCKET, s3_key)
            note = ""
            if upload_path.resolve() != canonical_path.resolve():
                note = f" (latest: {upload_path})"
            print(f"  OK {rel_path} -> s3://{BUCKET}/{s3_key}{note}")
            resultado["subidos"] += 1
            resultado["archivos"].append(s3_key)
        except ClientError as e:
            print(f"  Error subiendo {canonical_path.name}: {_client_error_message(e, 'upload_file')}")
            resultado["errores"] += 1
        except S3UploadFailedError as e:
            print(f"  Error subiendo {canonical_path.name}: {e}")
            resultado["errores"] += 1
        except NoCredentialsError as e:
            print(f"  Error subiendo {canonical_path.name}: {e}")
            resultado["errores"] += 1

    print(f"   Subidos: {resultado['subidos']} | Errores: {resultado['errores']}\n")
    return resultado


def verificar_s3(probar_escritura: bool = False) -> dict:
    """Verifica la conexion con S3 y, opcionalmente, permisos de escritura."""
    resultado = {
        "ok": False,
        "bucket": BUCKET,
        "region": _AWS_REGION,
        "head_bucket_ok": False,
        "write_test_ok": None,
        "delete_test_ok": None,
        "advertencias": [],
        "error": None,
    }

    try:
        s3 = _get_s3_client()
        s3.head_bucket(Bucket=BUCKET)
        resultado["head_bucket_ok"] = True

        if probar_escritura:
            key = f"_healthchecks/fintech_pipeline_{uuid.uuid4().hex}.txt"
            s3.put_object(
                Bucket=BUCKET,
                Key=key,
                Body=b"fintech_pipeline_s3_healthcheck\n",
                ContentType="text/plain",
            )
            resultado["write_test_ok"] = True
            try:
                s3.delete_object(Bucket=BUCKET, Key=key)
                resultado["delete_test_ok"] = True
            except ClientError as e:
                resultado["delete_test_ok"] = False
                resultado["advertencias"].append(_client_error_message(e, "delete_object"))

        resultado["ok"] = True
        return resultado
    except EnvironmentError as e:
        resultado["error"] = str(e)
        return resultado
    except ClientError as e:
        resultado["error"] = _client_error_message(e, "verificar_s3")
        return resultado
    except NoCredentialsError as e:
        resultado["error"] = str(e)
        return resultado
    except Exception as e:
        resultado["error"] = str(e)
        return resultado


if __name__ == "__main__":
    print("Verificando conexion S3...")
    diag = verificar_s3(probar_escritura=True)
    if diag["ok"]:
        print(f"OK Bucket s3://{diag['bucket']} ({diag['region']}) accesible")
        if diag["write_test_ok"]:
            print("OK Escritura S3 validada con healthcheck temporal")
        if diag["delete_test_ok"]:
            print("OK Limpieza del healthcheck validada")
        for advertencia in diag.get("advertencias", []):
            print(f"Advertencia: {advertencia}")
    else:
        print(f"Error: {diag['error']}")
