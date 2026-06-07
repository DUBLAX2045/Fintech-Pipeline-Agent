from __future__ import annotations

import asyncio

import pytest
import requests
from fastapi import BackgroundTasks, HTTPException
from pydantic import ValidationError

from src.bus import api_receiver, ecommerce_api
from src.bus.message_schema import MSG_TYPES


pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def reset_ecommerce_stats(monkeypatch):
    stats = {msg_type: 0 for msg_type in MSG_TYPES}
    stats.update({"errores": 0, "simulaciones_activas": 0, "batch_total": 0})
    monkeypatch.setattr(ecommerce_api, "_stats", stats)
    return stats


def test_ecommerce_models_validate_and_build_envelopes():
    msg = ecommerce_api.MensajeEntrada(
        msg_type="event",
        source="test",
        data={"subtype": "PAYMENT_MADE"},
    )
    envelope = msg.to_envelope()

    assert envelope["msg_type"] == "event"
    assert envelope["message_id"]
    assert envelope["timestamp"]

    with pytest.raises(ValidationError):
        ecommerce_api.MensajeEntrada(msg_type="bad", source="test", data={})

    with pytest.raises(ValidationError):
        ecommerce_api.BatchEntrada(mensajes=[msg] * 501)


def test_ecommerce_enviar_success_error_and_connection(requests_mock):
    requests_mock.post(ecommerce_api.RECEIVER_INGEST_URL, status_code=202)
    assert ecommerce_api._enviar({"msg_type": "event"}) is True
    assert ecommerce_api._stats["event"] == 1

    requests_mock.post(ecommerce_api.RECEIVER_INGEST_URL, status_code=500)
    assert ecommerce_api._enviar({"msg_type": "metric"}) is False
    assert ecommerce_api._stats["errores"] == 1

    requests_mock.post(ecommerce_api.RECEIVER_INGEST_URL, exc=requests.ConnectionError)
    assert ecommerce_api._enviar({"msg_type": "record"}) is False
    assert ecommerce_api._stats["errores"] == 2


def test_ecommerce_endpoints_with_mocked_sender(monkeypatch):
    sent = []
    monkeypatch.setattr(ecommerce_api, "_enviar", lambda msg: sent.append(msg) or True)

    msg = ecommerce_api.MensajeEntrada(
        msg_type="metric",
        source="test",
        data={"name": "gmv", "value": 10},
    )
    response = ecommerce_api.ingestar_mensaje(msg)

    assert response["status"] == "accepted"
    assert sent[-1]["msg_type"] == "metric"

    batch = ecommerce_api.BatchEntrada(mensajes=[msg, msg])
    batch_response = ecommerce_api.ingestar_batch(batch)
    assert batch_response["enviados"] == 2
    assert batch_response["por_tipo"] == {"metric": 2}
    assert ecommerce_api._stats["batch_total"] == 1

    assert ecommerce_api.evento_pago(user_id="u1", monto=100)["status"] == "accepted"
    assert ecommerce_api.evento_compra()["subtype"] == "PURCHASE_MADE"
    assert ecommerce_api.evento_transferencia()["subtype"] == "TRANSFER_SENT"
    assert ecommerce_api.evento_pago_fallido()["subtype"] == "PAYMENT_FAILED"
    assert ecommerce_api.metrica_snapshot(nombre="conversion", valor=0.5)["name"] == "conversion"
    assert ecommerce_api.registro_usuario(user_id="u1")["entity_id"] == "u1"
    assert "POST /ingest" in ecommerce_api.root()["endpoints"]
    assert "envelope_estandar" in ecommerce_api.schema()


def test_ecommerce_ingest_failure_and_simulation(monkeypatch):
    monkeypatch.setattr(ecommerce_api, "_enviar", lambda msg: False)
    msg = ecommerce_api.MensajeEntrada(msg_type="event", source="test", data={})

    with pytest.raises(HTTPException) as exc:
        ecommerce_api.ingestar_mensaje(msg)
    assert exc.value.status_code == 503

    tasks = BackgroundTasks()
    response = ecommerce_api.simular(tasks, msg_type="event", n=3, tps=1.0, subtipo="PAYMENT_MADE")
    assert response["status"] == "simulacion_iniciada"
    assert ecommerce_api._stats["simulaciones_activas"] == 1
    assert len(tasks.tasks) == 1

    with pytest.raises(HTTPException):
        ecommerce_api.simular(BackgroundTasks(), msg_type="bad")


def test_ecommerce_run_simulacion_decrements_active(monkeypatch):
    monkeypatch.setattr(ecommerce_api, "_enviar", lambda msg: True)
    monkeypatch.setattr(ecommerce_api.time, "sleep", lambda seconds: None)
    ecommerce_api._stats["simulaciones_activas"] = 1

    ecommerce_api._run_simulacion(n=2, tps=10, msg_type="metric", subtipo=None)

    assert ecommerce_api._stats["simulaciones_activas"] == 0


def test_ecommerce_health_success_and_failure(requests_mock):
    requests_mock.get(ecommerce_api.RECEIVER_HEALTH_URL, json={"status": "ok"})
    ok = ecommerce_api.health()
    assert ok["receiver_conectado"] is True
    assert ok["receiver_8000"] == {"status": "ok"}

    requests_mock.get(ecommerce_api.RECEIVER_HEALTH_URL, exc=requests.ConnectionError)
    fail = ecommerce_api.health()
    assert fail["receiver_conectado"] is False
    assert "error" in fail["receiver_8000"]


def test_api_receiver_handlers_with_fake_runtime(monkeypatch):
    class FakeBus:
        def __init__(self):
            self.messages = []

        async def publish(self, message):
            self.messages.append(message)

        @property
        def pending(self):
            return len(self.messages)

        def stats(self):
            return {
                "total_published": len(self.messages),
                "total_consumed": 0,
                "pending_in_queue": self.pending,
            }

    class FakeConsumer:
        def stats(self):
            return {"batches_guardados": 2, "eventos_guardados": 10}

    class FakeTrigger:
        def __init__(self):
            self.calls = []
            self.runs_completados = 3

        def trigger(self, force=False):
            self.calls.append(force)
            return True

        def stats(self):
            return {"runs_completados": self.runs_completados}

    fake_bus = FakeBus()
    fake_trigger = FakeTrigger()
    monkeypatch.setattr(api_receiver, "_bus", fake_bus)
    monkeypatch.setattr(api_receiver, "_consumer", FakeConsumer())
    monkeypatch.setattr(api_receiver, "_trigger", fake_trigger)

    async def scenario():
        accepted = await api_receiver.ingestar_mensaje({"msg_type": "metric"})
        legacy = await api_receiver.recibir_evento_legacy({"detail": {"payload": {}}})
        health = await api_receiver.health()
        status = await api_receiver.pipeline_status()
        run = await api_receiver.run_pipeline()
        flush = await api_receiver.flush_queue()

        assert accepted["status"] == "accepted"
        assert accepted["queue_pending"] == 1
        assert legacy["msg_type"] == "legacy_event"
        assert health["consumer_eventos"] == 10
        assert status["bus"]["total_published"] == 2
        assert run["status"] == "triggered"
        assert flush["status"] == "flush_solicitado"
        assert fake_trigger.calls == [True, True]

    asyncio.run(scenario())


def test_api_receiver_flush_empty_queue(monkeypatch):
    class EmptyBus:
        @property
        def pending(self):
            return 0

    monkeypatch.setattr(api_receiver, "_bus", EmptyBus())

    assert asyncio.run(api_receiver.flush_queue()) == {
        "status": "cola_vacia",
        "eventos_procesados": 0,
    }
