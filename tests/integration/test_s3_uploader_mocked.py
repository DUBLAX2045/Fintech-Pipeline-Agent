from __future__ import annotations

import boto3
import pandas as pd
import pytest
from moto import mock_aws

from src.ingesta import uploader_s3


pytestmark = pytest.mark.integration


@mock_aws
def test_subir_parquets_uploads_to_mocked_s3(tmp_path, monkeypatch):
    bucket = "fintech-pipeline-test"
    boto3.client("s3", region_name="us-east-1").create_bucket(Bucket=bucket)

    local = tmp_path / "gold"
    local.mkdir()
    pd.DataFrame({"user_id": ["u1"], "total_amount_cop": [1000.0]}).to_parquet(
        local / "gold_user_360.parquet",
        index=False,
    )

    monkeypatch.setattr(uploader_s3, "_AWS_KEY", "testing")
    monkeypatch.setattr(uploader_s3, "_AWS_SECRET", "testing")
    monkeypatch.setattr(uploader_s3, "_AWS_SESSION_TOKEN", "")
    monkeypatch.setattr(uploader_s3, "_AWS_REGION", "us-east-1")
    monkeypatch.setattr(uploader_s3, "BUCKET", bucket)

    result = uploader_s3.subir_parquets(str(local), "gold")

    assert result["subidos"] == 1
    assert result["errores"] == 0
    assert result["archivos"] == ["gold/gold_user_360.parquet"]

    s3 = boto3.client("s3", region_name="us-east-1")
    obj = s3.get_object(Bucket=bucket, Key="gold/gold_user_360.parquet")
    assert obj["Body"].read()
