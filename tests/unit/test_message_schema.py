from __future__ import annotations

import json

import pytest

from src.bus import message_schema as schema


pytestmark = pytest.mark.unit


def test_crear_mensaje_validates_type_and_builds_envelope():
    msg = schema.crear_mensaje("metric", "pytest", {"name": "gmv", "value": 1})

    assert msg["msg_type"] == "metric"
    assert msg["source"] == "pytest"
    assert msg["schema_version"] == "1.0"
    assert msg["message_id"]

    with pytest.raises(ValueError):
        schema.crear_mensaje("bad", "pytest", {})


def test_classification_and_flattening(make_fintech_event):
    legacy = make_fintech_event()
    metric = schema.crear_mensaje("metric", "pytest", {"name": "gmv", "value": 123, "nested": {"x": 1}})

    assert schema.es_legacy_fintech(legacy) is True
    assert schema.extraer_tipo(legacy) == "event"
    assert schema.extraer_tipo(metric) == "metric"
    assert schema.clasificar_mensajes([legacy, metric]).keys() == {"fintech_legacy", "metric"}

    row = schema.aplanar_mensaje_generico(metric)
    assert row["msg_type"] == "metric"
    assert row["data_name"] == "gmv"
    assert json.loads(row["raw_data"])["value"] == 123

    df = schema.aplanar_mensajes_genericos([metric])
    assert len(df) == 1


def test_generators_create_supported_messages():
    event = schema.generar_evento_fintech(subtype="PAYMENT_MADE", user_id="u1", monto=1000)
    metric = schema.generar_metrica(nombre="avg_ticket")
    record = schema.generar_registro_usuario(user_id="u1")
    log = schema.generar_log(nivel="warning")

    assert schema.es_legacy_fintech(event)
    assert metric["msg_type"] == "metric"
    assert record["msg_type"] == "record"
    assert log["msg_type"] == "log"
