from __future__ import annotations

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest


pytestmark = pytest.mark.ui

APP_PATH = Path("src/agent/app.py")


def _run_dashboard(monkeypatch) -> AppTest:
    monkeypatch.setenv("FINTECH_DASHBOARD_TEST_MODE", "1")
    return AppTest.from_file(str(APP_PATH)).run(timeout=25)


def _assert_no_streamlit_exceptions(app: AppTest) -> None:
    assert not app.exception, [exception.value for exception in app.exception]


def _markdown_text(app: AppTest) -> str:
    return "\n".join(str(item.value) for item in app.markdown)


def test_dashboard_centro_de_mando_renders_gold_overview(monkeypatch):
    app = _run_dashboard(monkeypatch)

    _assert_no_streamlit_exceptions(app)
    assert app.radio[0].options == ["Centro de mando", "Mesa de analisis", "Sistema"]

    markdown = _markdown_text(app)
    assert "Pulso <em>financiero</em> de usuarios" in markdown
    assert "Convierte eventos de pagos, compras, transferencias y recargas" in markdown
    assert "Qué puedes descubrir aquí" in markdown
    assert "Oportunidades de crecimiento" in markdown
    assert "Indicadores clave" in markdown
    assert "Cómo leer estos indicadores" in markdown
    assert "Panorama de mercado" in markdown
    assert "esta gráfica muestra qué segmentos concentran mayor volumen de dinero" in markdown
    assert "Alianzas y distribución geográfica" in markdown
    assert "Top 15 — Perfiles de mayor volumen" in markdown
    assert "esta tabla ayuda a ubicar usuarios o grupos de alto valor" in markdown
    assert not app.error


def test_dashboard_mesa_de_analisis_renders_chat_controls(monkeypatch):
    app = _run_dashboard(monkeypatch)
    app.radio[0].set_value("Mesa de analisis").run(timeout=25)

    _assert_no_streamlit_exceptions(app)
    button_labels = {button.label for button in app.button}
    markdown = _markdown_text(app)

    assert "Pregúntale a la plataforma qué está pasando con los usuarios" in markdown
    assert "Qué puede responder el agente" in markdown
    assert "Nivel de explicación" in markdown
    assert app.segmented_control[0].options == ["Profesional financiero", "Explicación clara"]
    assert "Dame el resumen ejecutivo de la plataforma" in button_labels
    assert "Analiza la tasa de fallos de pago por segmento" in button_labels
    assert "Limpiar conversacion" in button_labels
    assert len(app.chat_input) == 1
    assert not app.error


def test_dashboard_sistema_renders_operational_status(monkeypatch):
    app = _run_dashboard(monkeypatch)
    app.radio[0].set_value("Sistema").run(timeout=25)

    _assert_no_streamlit_exceptions(app)
    markdown = _markdown_text(app)

    assert "Estado <em>operativo</em>" in markdown
    assert "Servicios activos" in markdown
    assert "Warehouse analítico — Databricks" in markdown
    assert "Capa de datos Gold" in markdown
    assert "gold_user_360" in markdown
    assert not app.error
