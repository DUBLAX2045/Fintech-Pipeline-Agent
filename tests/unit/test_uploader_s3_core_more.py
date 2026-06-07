from __future__ import annotations


import boto3
import pandas as pd
import pytest
from botocore.exceptions import ClientError, NoCredentialsError
from moto import mock_aws

from src.ingesta import uploader_s3


pytestmark = pytest.mark.unit


def test_get_s3_client_includes_session_token(monkeypatch):
    captured = {}

    def fake_client(service, **kwargs):
        captured["service"] = service
        captured["kwargs"] = kwargs
        return object()

    monkeypatch.setattr(uploader_s3, "_AWS_KEY", "key")
    monkeypatch.setattr(uploader_s3, "_AWS_SECRET", "secret")
    monkeypatch.setattr(uploader_s3, "_AWS_SESSION_TOKEN", "session")
    monkeypatch.setattr(uploader_s3, "_AWS_REGION", "us-west-2")
    monkeypatch.setattr(uploader_s3, "BUCKET", "bucket")
    monkeypatch.setattr(uploader_s3.boto3, "client", fake_client)

    client = uploader_s3._get_s3_client()

    assert client is not None
    assert captured["service"] == "s3"
    assert captured["kwargs"]["aws_session_token"] == "session"
    assert captured["kwargs"]["region_name"] == "us-west-2"


def test_client_error_message_and_verificar_s3_error_paths(monkeypatch):
    error = ClientError(
        {"Error": {"Code": "403", "Message": "Forbidden"}},
        "HeadBucket",
    )
    assert "S3 error 403" in uploader_s3._client_error_message(error, "verificar")

    monkeypatch.setattr(uploader_s3, "_AWS_KEY", "key")
    monkeypatch.setattr(uploader_s3, "_AWS_SECRET", "secret")
    monkeypatch.setattr(uploader_s3, "BUCKET", "bucket")

    class ClientErrorS3:
        def head_bucket(self, Bucket):
            raise error

    monkeypatch.setattr(uploader_s3, "_get_s3_client", lambda: ClientErrorS3())
    diag = uploader_s3.verificar_s3()
    assert diag["ok"] is False
    assert "403" in diag["error"]

    monkeypatch.setattr(uploader_s3, "_get_s3_client", lambda: (_ for _ in ()).throw(NoCredentialsError()))
    diag = uploader_s3.verificar_s3()
    assert diag["ok"] is False
    assert "credentials" in diag["error"].lower()


def test_resolver_fuente_subida_uses_latest_when_available(tmp_path, monkeypatch):
    canonical = tmp_path / "x.parquet"
    latest = tmp_path / "_versions" / "x_latest.parquet"
    latest.parent.mkdir()
    latest.write_bytes(b"latest")

    monkeypatch.setattr(uploader_s3, "resolve_latest_parquet", lambda path: latest)
    assert uploader_s3._resolver_fuente_subida(canonical) == latest

    monkeypatch.setattr(
        uploader_s3,
        "resolve_latest_parquet",
        lambda path: (_ for _ in ()).throw(RuntimeError("manifest roto")),
    )
    assert uploader_s3._resolver_fuente_subida(canonical) == canonical


@mock_aws
def test_subir_parquets_success_uses_canonical_s3_key_with_latest_source(monkeypatch, tmp_path):
    bucket = "fintech-pipeline-test"
    boto3.client("s3", region_name="us-east-1").create_bucket(Bucket=bucket)
    local = tmp_path / "gold"
    local.mkdir()
    canonical = local / "gold_user_360.parquet"
    latest = tmp_path / "latest_gold_user_360.parquet"
    pd.DataFrame({"x": [1]}).to_parquet(canonical, index=False)
    pd.DataFrame({"x": [2]}).to_parquet(latest, index=False)

    monkeypatch.setattr(uploader_s3, "_AWS_KEY", "testing")
    monkeypatch.setattr(uploader_s3, "_AWS_SECRET", "testing")
    monkeypatch.setattr(uploader_s3, "_AWS_SESSION_TOKEN", "")
    monkeypatch.setattr(uploader_s3, "_AWS_REGION", "us-east-1")
    monkeypatch.setattr(uploader_s3, "BUCKET", bucket)
    monkeypatch.setattr(uploader_s3, "resolve_latest_parquet", lambda path: latest)

    result = uploader_s3.subir_parquets(str(local), "gold")
    body = boto3.client("s3", region_name="us-east-1").get_object(
        Bucket=bucket,
        Key="gold/gold_user_360.parquet",
    )["Body"].read()

    assert result["subidos"] == 1
    assert result["archivos"] == ["gold/gold_user_360.parquet"]
    assert body


def test_subir_parquets_environment_and_upload_error_branches(monkeypatch, tmp_path):
    local = tmp_path / "gold"
    local.mkdir()
    (local / "x.parquet").write_bytes(b"fake")

    monkeypatch.setattr(uploader_s3, "_get_s3_client", lambda: (_ for _ in ()).throw(EnvironmentError("faltan")))
    assert uploader_s3.subir_parquets(str(local), "gold") == {"subidos": 0, "errores": 0, "archivos": []}

    class NoCredsUploader:
        def upload_file(self, *args, **kwargs):
            raise NoCredentialsError()

    monkeypatch.setattr(uploader_s3, "_get_s3_client", lambda: NoCredsUploader())
    result = uploader_s3.subir_parquets(str(local), "gold")
    assert result["errores"] == 1
