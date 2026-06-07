from __future__ import annotations

import time

import pytest

from src.bus.pipeline_trigger import PipelineTrigger


pytestmark = pytest.mark.integration


def test_pipeline_trigger_runs_injected_steps_and_s3_uploads():
    calls = []

    trigger = PipelineTrigger(
        auto_trigger=True,
        min_intervalo_segundos=60,
        subir_s3=True,
        silver_runner=lambda: calls.append("silver"),
        gold_runner=lambda: calls.append("gold"),
        s3_verifier=lambda: {"ok": True, "error": None},
        parquet_uploader=lambda local, capa: calls.append((local, capa)) or {"subidos": 1},
    )

    assert trigger.trigger(force=True) is True
    assert trigger.wait_for_completion(timeout=5) is True

    assert calls == [
        "silver",
        "gold",
        ("data/silver", "silver"),
        ("data/gold", "gold"),
    ]
    assert trigger.stats()["runs_completados"] == 1
    assert trigger.stats()["errores"] == 0


def test_pipeline_trigger_throttles_noops_and_running_state():
    trigger = PipelineTrigger(
        auto_trigger=False,
        silver_runner=lambda: None,
        gold_runner=lambda: None,
        subir_s3=False,
    )
    assert trigger.trigger() is False

    assert trigger.trigger(force=True) is True
    assert trigger.wait_for_completion(timeout=5) is True
    assert trigger.trigger() is False

    running = PipelineTrigger(auto_trigger=True, silver_runner=lambda: None, gold_runner=lambda: None)
    running._running = True
    assert running.trigger(force=True) is False


def test_pipeline_trigger_counts_errors_and_sets_done_event():
    trigger = PipelineTrigger(
        auto_trigger=True,
        subir_s3=False,
        silver_runner=lambda: (_ for _ in ()).throw(RuntimeError("silver roto")),
        gold_runner=lambda: None,
    )

    assert trigger.trigger(force=True) is True
    assert trigger.wait_for_completion(timeout=5) is True

    assert trigger.stats()["errores"] == 1
    assert trigger.stats()["activo_ahora"] is False


def test_pipeline_trigger_skips_s3_when_healthcheck_fails():
    calls = []
    trigger = PipelineTrigger(
        auto_trigger=True,
        subir_s3=True,
        silver_runner=lambda: calls.append("silver"),
        gold_runner=lambda: calls.append("gold"),
        s3_verifier=lambda: {"ok": False, "error": "sin bucket"},
        parquet_uploader=lambda local, capa: calls.append((local, capa)),
    )

    assert trigger.trigger(force=True) is True
    assert trigger.wait_for_completion(timeout=5) is True

    assert calls == ["silver", "gold"]


def test_pipeline_trigger_running_thread_rejects_second_trigger():
    def slow_step():
        time.sleep(0.1)

    trigger = PipelineTrigger(
        auto_trigger=True,
        silver_runner=slow_step,
        gold_runner=lambda: None,
        subir_s3=False,
    )

    assert trigger.trigger(force=True) is True
    assert trigger.trigger(force=True) is False
    assert trigger.wait_for_completion(timeout=5) is True
