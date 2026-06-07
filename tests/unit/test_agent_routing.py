from __future__ import annotations

import pytest

from src.agent import agent as agent_module


pytestmark = pytest.mark.unit


def test_intent_detection_and_sql_generation():
    assert agent_module._es_pregunta_de_datos("Dame KPIs por segmento")
    assert not agent_module._es_pregunta_de_datos("Hola, como estas?")

    sql, title = agent_module._sql_por_intencion("ciudades con mayor tasa de fallo")
    assert "GROUP BY city" in sql
    assert "tasa_fallo_pct DESC" in sql
    assert title == "Ciudades con mayor fricción de pago"

    sql, title = agent_module._sql_por_intencion("top usuarios por gasto")
    assert "ORDER BY total_amount_cop DESC" in sql
    assert title == "Top 10 usuarios por gasto"


def test_respuesta_datos_confiable_uses_direct_summary(monkeypatch):
    monkeypatch.setattr(agent_module, "resumen_ejecutivo", lambda: "total_usuarios 489")

    response = agent_module._respuesta_datos_confiable("Dame un resumen ejecutivo")

    assert "489" in response


def test_respuesta_datos_confiable_uses_sql_for_known_intent(monkeypatch):
    # Desde el cambio a _respuesta_con_grafico, el intent es enrutado a generación de gráfico.
    # Mockeamos la función de destino para verificar que el routing es correcto.
    monkeypatch.setattr(
        agent_module,
        "_respuesta_con_grafico",
        lambda titulo, sql, pregunta: f"grafico::{titulo}",
    )

    response = agent_module._respuesta_datos_confiable("volumen por segmento")

    assert "Rentabilidad por segmento" in response


def test_respuesta_datos_confiable_rejects_ambiguous_data_question():
    response = agent_module._respuesta_datos_confiable("quiero analizar gold")

    assert "Necesito" in response
    assert "específica" in response


def test_tool_call_parser_handles_fenced_json():
    parsed = agent_module._extraer_tool_call(
        '```json\n{"tool":"resumen_ejecutivo","args":{}}\n```'
    )

    assert parsed["tool"] == "resumen_ejecutivo"
