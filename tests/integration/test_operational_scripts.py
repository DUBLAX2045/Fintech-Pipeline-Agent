from __future__ import annotations

import asyncio
import json
import subprocess

import pandas as pd
import pytest

from src import run_pipeline
from src.bronze import pipeline_bronze, repair_duplicates, simulator
from src.bus import start_full_pipeline
from src.ingesta import uploader, uploader_api


pytestmark = pytest.mark.integration


def test_bronze_pipeline_runs_against_temp_dataset(tmp_path, make_fintech_event):
    raw = tmp_path / "events.json"
    raw.write_text(
        json.dumps(
            [
                make_fintech_event(event_id="evt-bronze-1"),
                make_fintech_event(event_id="evt-bronze-2"),
            ]
        ),
        encoding="utf-8",
    )

    output = pipeline_bronze.ejecutar_pipeline_bronze(
        ruta_json=str(raw),
        carpeta_bronze=str(tmp_path / "bronze" / "events"),
        carpeta_logs=str(tmp_path / "logs"),
    )
    df = pd.read_parquet(output)

    assert len(df) == 2
    assert set(df["event_id"]) == {"evt-bronze-1", "evt-bronze-2"}
    assert df["is_duplicate"].tolist() == [False, False]


def test_repair_duplicates_marks_cross_file_and_missing_event_ids(tmp_path):
    bronze = tmp_path / "bronze" / "events"
    day1 = bronze / "date=2026-01-01"
    day2 = bronze / "date=2026-01-02"
    day1.mkdir(parents=True)
    day2.mkdir(parents=True)

    pd.DataFrame(
        [
            {
                "event_id": "evt-1",
                "batch_id": "batch-1",
                "ingestion_timestamp": "2026-01-01T00:00:00Z",
            },
            {
                "event_id": None,
                "batch_id": "batch-1",
                "ingestion_timestamp": "2026-01-01T00:00:01Z",
            },
        ]
    ).to_parquet(day1 / "batch_1.parquet", index=False)
    pd.DataFrame(
        [
            {
                "event_id": "evt-1",
                "batch_id": "batch-2",
                "ingestion_timestamp": "2026-01-02T00:00:00Z",
            }
        ]
    ).to_parquet(day2 / "batch_2.parquet", index=False)

    dry = repair_duplicates.reparar_flags_duplicados_bronze(str(bronze), dry_run=True)
    result = repair_duplicates.reparar_flags_duplicados_bronze(str(bronze), dry_run=False)
    repaired_day1 = pd.read_parquet(day1 / "batch_1.parquet")
    repaired_day2 = pd.read_parquet(day2 / "batch_2.parquet")

    assert dry["dry_run"] is True
    assert result["duplicados_marcados"] == 2
    assert repaired_day1.loc[1, "duplicate_reason"] == "missing_event_id"
    assert repaired_day2.loc[0, "duplicate_reason"] == "seen_in_bronze"
    assert repaired_day2.loc[0, "duplicate_first_seen_batch_id"] == "batch-1"


def test_repair_duplicates_errors_for_empty_or_invalid_bronze(tmp_path):
    with pytest.raises(FileNotFoundError):
        repair_duplicates.reparar_flags_duplicados_bronze(str(tmp_path / "missing"))

    bronze = tmp_path / "bronze"
    bronze.mkdir()
    pd.DataFrame({"x": [1]}).to_parquet(bronze / "x.parquet", index=False)

    with pytest.raises(ValueError):
        repair_duplicates.reparar_flags_duplicados_bronze(str(bronze))


def test_simulator_generates_valid_event_and_writes_one_batch(tmp_path):
    event = simulator.generar_evento_ecommerce()
    assert event["source"] == "ecommerce.app"
    assert "payload" in event["detail"]

    simulator.ejecutar_simulador(
        eventos_por_lote=2,
        intervalo_segundos=0,
        carpeta_bronze=str(tmp_path / "bronze" / "events"),
        max_lotes=1,
    )

    files = list((tmp_path / "bronze" / "events").rglob("*.parquet"))
    assert len(files) == 1
    assert len(pd.read_parquet(files[0])) == 2


def test_cli_dbfs_uploader_counts_success_and_errors(monkeypatch, tmp_path):
    local = tmp_path / "silver"
    nested = local / "nested"
    nested.mkdir(parents=True)
    (local / "ignore.txt").write_text("no", encoding="utf-8")
    (nested / "x.parquet").write_bytes(b"fake")
    calls = []

    def fake_run(args, check):
        calls.append(args)

    monkeypatch.setattr(uploader.subprocess, "run", fake_run)

    result = uploader.subir_parquets(str(local), "dbfs:/tmp/silver")

    assert result["subidos"] == 1
    assert result["errores"] == 0
    assert result["archivos"] == ["dbfs:/tmp/silver/nested/x.parquet"]
    assert calls[0][:3] == ["databricks", "fs", "cp"]

    def failing_run(args, check):
        raise subprocess.CalledProcessError(1, args)

    monkeypatch.setattr(uploader.subprocess, "run", failing_run)
    failed = uploader.subir_parquets(str(local), "dbfs:/tmp/silver")
    assert failed["subidos"] == 0
    assert failed["errores"] == 1

    missing = uploader.subir_parquets(str(tmp_path / "missing"), "dbfs:/tmp/silver")
    assert missing == {"subidos": 0, "errores": 0, "archivos": []}


def test_rest_dbfs_uploader_uses_env_and_counts_results(monkeypatch, requests_mock, tmp_path):
    local = tmp_path / "gold"
    local.mkdir()
    file_path = local / "x.parquet"
    file_path.write_bytes(b"fake")

    monkeypatch.setattr(uploader_api, "DATABRICKS_HOST", "dbc-test.cloud.databricks.com")
    monkeypatch.setattr(uploader_api, "DATABRICKS_TOKEN", "token")
    requests_mock.post("https://dbc-test.cloud.databricks.com/api/2.0/dbfs/put", status_code=200)

    assert uploader_api.subir_archivo_dbfs(str(file_path), "dbfs:/tmp/x.parquet") is True
    result = uploader_api.subir_parquets(str(local), "dbfs:/tmp/gold")
    assert result["subidos"] == 1
    assert result["archivos"] == ["dbfs:/tmp/gold/x.parquet"]

    requests_mock.post("https://dbc-test.cloud.databricks.com/api/2.0/dbfs/put", status_code=500, text="boom")
    failed = uploader_api.subir_parquets(str(local), "dbfs:/tmp/gold")
    assert failed["errores"] == 1

    monkeypatch.setattr(uploader_api, "DATABRICKS_TOKEN", "")
    with pytest.raises(EnvironmentError):
        uploader_api.subir_archivo_dbfs(str(file_path), "dbfs:/tmp/x.parquet")


def test_master_run_pipeline_uses_requested_layers(monkeypatch):
    calls = []
    monkeypatch.setattr(run_pipeline, "ejecutar_pipeline_bronze", lambda: calls.append("bronze"))
    monkeypatch.setattr(run_pipeline, "ejecutar_pipeline_silver", lambda: calls.append("silver"))
    monkeypatch.setattr(
        run_pipeline,
        "ejecutar_pipeline_gold",
        lambda: calls.append("gold") or {"user_360": [1, 2]},
    )

    run_pipeline.ejecutar_todo(desde_silver=False)
    assert calls == ["bronze", "silver", "gold"]

    calls.clear()
    run_pipeline.ejecutar_todo(desde_silver=True)
    assert calls == ["silver", "gold"]


def test_start_full_pipeline_main_with_fake_runtime(monkeypatch):
    class FakeBus:
        def __init__(self, maxsize):
            self.pending = 0

        def stats(self):
            return {
                "total_published": 2,
                "total_consumed": 2,
                "pending_in_queue": self.pending,
            }

    class FakeTrigger:
        def __init__(self, auto_trigger, min_intervalo_segundos):
            self.runs_completados = 0

        def trigger(self, force=False):
            self.runs_completados += 1
            return True

        def wait_for_completion(self, timeout):
            return True

        def stats(self):
            return {"runs_completados": self.runs_completados}

    class FakeConsumer:
        def __init__(self, bus, batch_size, flush_interval_segundos, trigger):
            self._running = False

        async def start(self):
            return None

        def stop(self):
            self._running = False

        def stats(self):
            return {"eventos_guardados": 2, "batches_guardados": 1}

    class FakeProducer:
        def __init__(self, bus, delay_segundos, loop):
            pass

        async def start(self):
            return None

    monkeypatch.setattr(start_full_pipeline, "EventBus", FakeBus)
    monkeypatch.setattr(start_full_pipeline, "PipelineTrigger", FakeTrigger)
    monkeypatch.setattr(start_full_pipeline, "BronzeConsumer", FakeConsumer)
    monkeypatch.setattr(start_full_pipeline, "DatasetProducer", FakeProducer)
    real_sleep = asyncio.sleep
    monkeypatch.setattr(start_full_pipeline.asyncio, "sleep", lambda seconds: real_sleep(0))

    asyncio.run(
        start_full_pipeline.main(
            delay=0,
            batch_size=2,
            flush_interval=0,
            auto_trigger=True,
            trigger_interval=1,
            loop_dataset=False,
        )
    )
