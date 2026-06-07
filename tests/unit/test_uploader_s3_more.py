from __future__ import annotations

import boto3
import pandas as pd
import pytest
from moto import mock_aws

from src.ingesta import uploader_s3


pytestmark = pytest.mark.unit


def test_parquet_files_ignores_internal_dirs(tmp_path):
    root = tmp_path / "gold"
    root.mkdir()
    (root / "gold_user_360.parquet").write_bytes(b"ok")
    ignored = root / "_versions"
    ignored.mkdir()
    (ignored / "old.parquet").write_bytes(b"old")

    files = list(uploader_s3._parquet_files(str(root)))

    assert files == [root / "gold_user_360.parquet"]


def test_get_s3_client_requires_credentials(monkeypatch):
    monkeypatch.setattr(uploader_s3, "_AWS_KEY", "")
    monkeypatch.setattr(uploader_s3, "_AWS_SECRET", "")
    monkeypatch.setattr(uploader_s3, "BUCKET", "bucket")

    with pytest.raises(EnvironmentError):
        uploader_s3._get_s3_client()


@mock_aws
def test_verificar_s3_success_and_upload_missing_folder(monkeypatch, tmp_path):
    bucket = "fintech-pipeline-test"
    boto3.client("s3", region_name="us-east-1").create_bucket(Bucket=bucket)
    monkeypatch.setattr(uploader_s3, "_AWS_KEY", "testing")
    monkeypatch.setattr(uploader_s3, "_AWS_SECRET", "testing")
    monkeypatch.setattr(uploader_s3, "_AWS_SESSION_TOKEN", "")
    monkeypatch.setattr(uploader_s3, "_AWS_REGION", "us-east-1")
    monkeypatch.setattr(uploader_s3, "BUCKET", bucket)

    diag = uploader_s3.verificar_s3(probar_escritura=True)
    missing = uploader_s3.subir_parquets(str(tmp_path / "missing"), "gold")

    assert diag["ok"] is True
    assert diag["head_bucket_ok"] is True
    assert diag["write_test_ok"] is True
    assert diag["delete_test_ok"] is True
    assert missing == {"subidos": 0, "errores": 0, "archivos": []}


@mock_aws
def test_subir_parquets_counts_upload_errors(monkeypatch, tmp_path):
    bucket = "fintech-pipeline-test"
    boto3.client("s3", region_name="us-east-1").create_bucket(Bucket=bucket)
    local = tmp_path / "gold"
    local.mkdir()
    pd.DataFrame({"x": [1]}).to_parquet(local / "x.parquet", index=False)

    monkeypatch.setattr(uploader_s3, "_AWS_KEY", "testing")
    monkeypatch.setattr(uploader_s3, "_AWS_SECRET", "testing")
    monkeypatch.setattr(uploader_s3, "_AWS_SESSION_TOKEN", "")
    monkeypatch.setattr(uploader_s3, "_AWS_REGION", "us-east-1")
    monkeypatch.setattr(uploader_s3, "BUCKET", "missing-bucket")

    result = uploader_s3.subir_parquets(str(local), "gold")

    assert result["subidos"] == 0
    assert result["errores"] == 1
