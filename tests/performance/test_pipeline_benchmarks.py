from __future__ import annotations

import contextlib
import io
import shutil
from collections.abc import Callable

import pandas as pd
import pytest

from src.bronze.ingest import aplanar_todos, detectar_y_registrar_duplicados
from src.bronze.metadata import agregar_metadatos_ingesta
from src.bronze.save import guardar_bronze_parquet
from src.gold import pipeline_gold as gold_module
from src.silver import pipeline_silver as silver_module


pytestmark = [pytest.mark.performance, pytest.mark.slow]

EVENT_TYPES = [
    "PAYMENT_MADE",
    "PURCHASE_MADE",
    "TRANSFER_SENT",
    "PAYMENT_FAILED",
    "MONEY_ADDED",
    "USER_REGISTERED",
]


def _quiet_call(fn: Callable, *args, **kwargs):
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*args, **kwargs)


def _raw_event(index: int, users: int = 250) -> dict:
    event_type = EVENT_TYPES[index % len(EVENT_TYPES)]
    status = "FAILED" if event_type == "PAYMENT_FAILED" else "SUCCESS"
    user_id = f"user_{index % users:04d}"
    day = (index % 28) + 1
    hour = index % 24
    minute = index % 60
    amount = None if event_type == "USER_REGISTERED" else float(25_000 + (index % 400) * 1_250)
    balance_before = float(1_000_000 + (index % 100) * 10_000)
    balance_after = balance_before - (amount or 0.0)

    return {
        "source": "benchmark.source",
        "detailType": "event",
        "detail": {
            "id": f"evt-benchmark-{index:06d}",
            "event": event_type,
            "version": "1.0",
            "eventType": event_type.lower(),
            "transactionType": event_type.lower(),
            "eventEntity": "USER",
            "eventStatus": status,
            "payload": {
                "userId": user_id,
                "name": f"Benchmark User {index % users}",
                "age": 18 + (index % 50),
                "email": f"{user_id}@example.com",
                "city": ["Bogota", "Medellin", "Cali", "Barranquilla"][index % 4],
                "segment": ["standard", "premium", "vip"][index % 3],
                "timestamp": f"2026-01-{day:02d}T{hour:02d}:{minute:02d}:00Z",
                "accountId": f"acc_{user_id}",
                "amount": amount,
                "currency": "COP",
                "merchant": ["Rappi", "Exito", "Falabella", "D1"][index % 4],
                "category": ["food", "retail", "transport", "services"][index % 4],
                "paymentMethod": ["wallet", "card", "pse"][index % 3],
                "installments": 1 + (index % 3),
                "balanceBefore": balance_before,
                "balanceAfter": balance_after,
                "initialBalance": balance_before if event_type == "USER_REGISTERED" else None,
                "status": "ACTIVE" if event_type == "USER_REGISTERED" else None,
                "source": "bank_transfer" if event_type == "MONEY_ADDED" else None,
                "location": {"city": ["Bogota", "Medellin", "Cali", "Barranquilla"][index % 4],
                             "country": "Colombia"},
                "updatedFields": {},
            },
            "metadata": {
                "device": ["mobile", "web", "tablet"][index % 3],
                "os": ["android", "ios", "windows"][index % 3],
                "ip": f"192.168.{index % 255}.{(index * 7) % 255}",
                "channel": ["app", "web", "pos"][index % 3],
            },
        },
    }


@pytest.fixture(scope="module")
def raw_events() -> list[dict]:
    return [_raw_event(index) for index in range(1_200)]


@pytest.fixture(scope="module")
def bronze_frame(raw_events: list[dict]) -> pd.DataFrame:
    df = _quiet_call(aplanar_todos, raw_events)
    df = _quiet_call(agregar_metadatos_ingesta, df, "benchmark.json")
    df["is_duplicate"] = False
    df["duplicate_reason"] = None
    df["duplicate_first_seen_batch_id"] = None
    df["duplicate_first_seen_file"] = None
    return df


@pytest.fixture(scope="module")
def silver_frame(bronze_frame: pd.DataFrame) -> pd.DataFrame:
    return _build_silver_in_memory(bronze_frame)


def _build_silver_in_memory(bronze: pd.DataFrame) -> pd.DataFrame:
    paso4_geo = getattr(silver_module, "paso4_enriquecer_geolocalizaci\u00f3n")

    df = _quiet_call(silver_module.paso2_limpiar_tipos, bronze)
    df = _quiet_call(silver_module.paso2b_deduplicar_eventos, df)
    df = _quiet_call(silver_module.paso3_agregar_flags, df)
    df = _quiet_call(paso4_geo, df)
    df = _quiet_call(silver_module.paso5_enriquecer_moneda, df)
    return _quiet_call(silver_module.paso6_renombrar_y_seleccionar_columnas, df)


def test_benchmark_bronze_ingest_preparation(benchmark, raw_events: list[dict], tmp_path):
    def run():
        df = _quiet_call(aplanar_todos, raw_events)
        df = _quiet_call(agregar_metadatos_ingesta, df, "benchmark.json")
        return _quiet_call(
            detectar_y_registrar_duplicados,
            df,
            carpeta_logs=str(tmp_path / "logs"),
        )

    result = benchmark.pedantic(run, rounds=3, iterations=1)

    assert len(result) == len(raw_events)
    assert result["is_duplicate"].sum() == 0


def test_benchmark_silver_transformations(benchmark, bronze_frame: pd.DataFrame, monkeypatch):
    monkeypatch.setattr(silver_module.fx, "tasa_cop_usd", lambda: 0.00025)

    result = benchmark.pedantic(lambda: _build_silver_in_memory(bronze_frame), rounds=3, iterations=1)

    assert len(result) == len(bronze_frame)
    assert {"amount_cop", "amount_usd", "is_transactional"}.issubset(result.columns)
    assert result["amount_usd"].notna().sum() > 0


def test_benchmark_gold_aggregations(benchmark, silver_frame: pd.DataFrame):
    def run():
        user_360 = _quiet_call(gold_module.construir_user_360, silver_frame)
        daily = _quiet_call(gold_module.construir_daily_metrics, silver_frame)
        summary = _quiet_call(gold_module.construir_event_summary, silver_frame)
        return user_360, daily, summary

    user_360, daily, summary = benchmark.pedantic(run, rounds=3, iterations=1)

    assert len(user_360) == silver_frame["user_id"].nunique()
    assert len(daily) == silver_frame["date"].nunique()
    assert set(summary["event"]) == set(EVENT_TYPES)


def test_benchmark_local_silver_to_gold_pipeline(benchmark, bronze_frame: pd.DataFrame, monkeypatch, tmp_path):
    bronze_dir = tmp_path / "bronze" / "events"
    silver_dir = tmp_path / "silver"
    gold_dir = tmp_path / "gold"

    monkeypatch.setattr(silver_module.fx, "tasa_cop_usd", lambda: 0.00025)

    def run():
        shutil.rmtree(bronze_dir, ignore_errors=True)
        shutil.rmtree(silver_dir, ignore_errors=True)
        shutil.rmtree(gold_dir, ignore_errors=True)
        _quiet_call(guardar_bronze_parquet, bronze_frame, str(bronze_dir))
        silver = _quiet_call(
            silver_module.ejecutar_pipeline_silver,
            carpeta_bronze=str(bronze_dir),
            carpeta_silver=str(silver_dir),
        )
        gold = _quiet_call(
            gold_module.ejecutar_pipeline_gold,
            carpeta_silver=str(silver_dir),
            carpeta_gold=str(gold_dir),
        )
        return len(silver), len(gold["user_360"]), len(gold["daily"]), len(gold["summary"])

    silver_rows, users, days, events = benchmark.pedantic(run, rounds=3, iterations=1)

    assert silver_rows == len(bronze_frame)
    assert users == bronze_frame["user_id"].nunique()
    assert days > 0
    assert events == len(EVENT_TYPES)
