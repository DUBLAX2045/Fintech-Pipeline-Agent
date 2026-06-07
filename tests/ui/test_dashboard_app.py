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


def test_dashboard_centro_de_mando_renders_gold_overview(monkeypatch):
    app = _run_dashboard(monkeypatch)

    _assert_no_streamlit_exceptions(app)
    assert app.radio[0].options == ["Centro de mando", "Mesa de analisis", "Sistema"]

    subheaders = {item.value for item in app.subheader}
    assert "Volumen por segmento" in subheaders
    assert "Usuarios por ciudad" in subheaders
    assert "Comercios dominantes" in subheaders
    assert "Canal preferido" in subheaders
    assert not app.error


def test_dashboard_mesa_de_analisis_renders_chat_controls(monkeypatch):
    app = _run_dashboard(monkeypatch)
    app.radio[0].set_value("Mesa de analisis").run(timeout=25)

    _assert_no_streamlit_exceptions(app)
    button_labels = {button.label for button in app.button}

    assert "Dame el resumen ejecutivo de la plataforma" in button_labels
    assert "Analiza la tasa de fallos de pago por segmento" in button_labels
    assert "Limpiar conversacion" in button_labels
    assert len(app.chat_input) == 1
    assert not app.error


def test_dashboard_sistema_renders_operational_status(monkeypatch):
    app = _run_dashboard(monkeypatch)
    app.radio[0].set_value("Sistema").run(timeout=25)

    _assert_no_streamlit_exceptions(app)
    subheaders = {item.value for item in app.subheader}
    success_messages = [item.value for item in app.success]

    assert {"Motor conversacional local", "Databricks", "Estado de datos Gold"}.issubset(subheaders)
    assert any(message.startswith("gold_user_360:") for message in success_messages)
    assert not app.error
