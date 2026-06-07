from __future__ import annotations

import asyncio
import json

import pandas as pd
import pytest

from src.bus.dataset_producer import DatasetProducer
from src.bus import event_bus_asyncio
from src.bus.event_bus_asyncio import BronzeConsumer, EcommerceProducer, EventBus, FintechEvent
from src.bus.message_schema import crear_mensaje


pytestmark = pytest.mark.integration


def test_event_bus_publish_consume_and_stats():
    async def scenario():
        bus = EventBus(maxsize=10)
        await bus.publish({"id": 1})
        await bus.publish({"id": 2})

        batch = await bus.consume_batch(max_batch_size=10)

        assert batch == [{"id": 1}, {"id": 2}]
        assert bus.stats() == {
            "total_published": 2,
            "total_consumed": 2,
            "pending_in_queue": 0,
        }

    asyncio.run(scenario())


def test_fintech_event_to_pipeline_format_has_bronze_contract():
    event = FintechEvent(
        event_type="PAYMENT_FAILED",
        user_id="ecom_user_1",
        amount=10000.0,
        merchant="Rappi",
        event_id="evt-runtime",
        timestamp="2026-01-01T00:00:00+00:00",
    )

    payload = event.to_pipeline_format()

    assert payload["detail"]["id"] == "evt-runtime"
    assert payload["detail"]["eventStatus"] == "FAILED"
    assert payload["detail"]["payload"]["userId"] == "ecom_user_1"
    assert payload["detail"]["payload"]["amount"] == 10000.0


def test_dataset_producer_loads_and_publishes_temp_dataset(tmp_path, make_fintech_event):
    raw = tmp_path / "events.json"
    raw.write_text(
        json.dumps(
            [
                make_fintech_event(event_id="evt-1"),
                make_fintech_event(event_id="evt-2"),
            ]
        ),
        encoding="utf-8",
    )

    async def scenario():
        bus = EventBus()
        producer = DatasetProducer(bus, json_path=str(raw), delay_segundos=0, loop=False)

        await producer.start()
        batch = await bus.consume_batch(max_batch_size=5)

        assert producer.count == 2
        assert len(batch) == 2
        assert batch[0]["detail"]["id"] == "evt-1"

    asyncio.run(scenario())


def test_dataset_producer_missing_file_raises(tmp_path):
    producer = DatasetProducer(EventBus(), json_path=str(tmp_path / "missing.json"))

    with pytest.raises(FileNotFoundError):
        producer._cargar_dataset()


def test_bronze_consumer_saves_legacy_and_generic_messages(tmp_path, make_fintech_event):
    class TriggerSpy:
        def __init__(self):
            self.calls = 0

        def trigger(self):
            self.calls += 1

    trigger = TriggerSpy()
    bronze_events = tmp_path / "bronze" / "events"
    consumer = BronzeConsumer(
        EventBus(),
        carpeta_bronze=str(bronze_events),
        batch_size=2,
        trigger=trigger,
    )
    metric = crear_mensaje(
        "metric",
        "integration_test",
        {"name": "conversion_rate", "value": 0.91},
    )

    consumer._guardar_sincronico(
        [make_fintech_event(event_id="evt-runtime-bus"), metric]
    )

    event_files = list(bronze_events.rglob("*.parquet"))
    metric_files = list((tmp_path / "bronze" / "metric").rglob("*.parquet"))

    assert consumer.stats() == {"batches_guardados": 1, "eventos_guardados": 2}
    assert trigger.calls == 1
    assert len(event_files) == 1
    assert len(metric_files) == 1
    assert pd.read_parquet(event_files[0]).loc[0, "event_id"] == "evt-runtime-bus"
    assert pd.read_parquet(metric_files[0]).loc[0, "msg_type"] == "metric"


def test_bronze_consumer_async_batch_normalizes_fintech_event(monkeypatch):
    captured = {}
    consumer = BronzeConsumer(EventBus())
    event = FintechEvent("PAYMENT_MADE", "u1", 50000.0, "Rappi", event_id="evt-async")

    def fake_save(messages):
        captured["messages"] = messages

    monkeypatch.setattr(consumer, "_guardar_sincronico", fake_save)

    asyncio.run(consumer._guardar_batch([event]))

    assert captured["messages"][0]["detail"]["id"] == "evt-async"


def test_ecommerce_producer_start_publishes_until_duration_and_stop():
    async def scenario():
        bus = EventBus()
        producer = EcommerceProducer(bus, eventos_por_segundo=10_000)

        await producer.start(duracion_segundos=0.01)
        published = bus.stats()["total_published"]
        producer.stop()

        assert published > 0
        assert producer._running is False

    asyncio.run(scenario())


def test_bronze_consumer_start_flushes_batch_and_can_stop(monkeypatch):
    async def scenario():
        bus = EventBus()
        consumer = BronzeConsumer(bus, batch_size=1, flush_interval_segundos=999)
        saved_batches = []

        async def fake_save(batch):
            saved_batches.append(batch)
            consumer.stop()

        monkeypatch.setattr(consumer, "_guardar_batch", fake_save)
        await bus.publish({"detail": {"id": "evt-consumer"}})

        await asyncio.wait_for(consumer.start(), timeout=2)

        assert saved_batches == [[{"detail": {"id": "evt-consumer"}}]]

    asyncio.run(scenario())


def test_streaming_orchestrator_uses_runtime_components(monkeypatch):
    class FakeBus:
        def __init__(self, maxsize):
            self.maxsize = maxsize

        def stats(self):
            return {
                "total_published": 3,
                "total_consumed": 3,
                "pending_in_queue": 0,
            }

    class FakeProducer:
        def __init__(self, bus, eventos_por_segundo):
            self.bus = bus
            self.eventos_por_segundo = eventos_por_segundo

        async def start(self, duracion_segundos):
            return None

        def stop(self):
            return None

    class FakeConsumer:
        def __init__(self, bus, batch_size, flush_interval_segundos):
            self.bus = bus
            self.batch_size = batch_size
            self.flush_interval = flush_interval_segundos

        async def start(self):
            return None

        def stop(self):
            return None

        def stats(self):
            return {"eventos_guardados": 3, "batches_guardados": 1}

    monkeypatch.setattr(event_bus_asyncio, "EventBus", FakeBus)
    monkeypatch.setattr(event_bus_asyncio, "EcommerceProducer", FakeProducer)
    monkeypatch.setattr(event_bus_asyncio, "BronzeConsumer", FakeConsumer)

    asyncio.run(
        event_bus_asyncio.ejecutar_pipeline_streaming(
            duracion_segundos=1,
            eventos_por_segundo=2,
            batch_size=2,
            flush_interval=1,
        )
    )
