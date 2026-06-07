"""
Smoke test de browser para el dashboard Streamlit vía Playwright.
Marker: e2e — excluido del run normal de CI.

Ejecutar explícitamente con:
    pytest -m e2e tests/ui/test_dashboard_smoke_playwright.py -v --browser chromium

Requiere:
    pip install playwright pytest-playwright
    playwright install chromium

El fixture `streamlit_server` inicia el dashboard automáticamente si no está
corriendo en el puerto 8501. No es necesario iniciarlo manualmente.

Cubre:
  - El dashboard carga sin errores de consola críticos
  - Las 3 páginas de navegación renderizan correctamente (inner_text rendered)
  - Los KPIs del Centro de mando muestran etiquetas reales
  - Los botones de Acciones rápidas son clickeables
  - El chat input existe y acepta texto
  - La página Sistema muestra el estado de los servicios
  - El endpoint /_stcore/health responde 200 OK
"""

from __future__ import annotations

import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
APP_URL = "http://localhost:8501"

pytestmark = pytest.mark.e2e


# ── Helpers ───────────────────────────────────────────────────────────────────

def _streamlit_corriendo() -> bool:
    try:
        with socket.create_connection(("localhost", 8501), timeout=3):
            return True
    except OSError:
        return False


# ── Fixture: arranca el servidor si no está corriendo ─────────────────────────

@pytest.fixture(scope="session")
def streamlit_server():
    """
    Inicia el dashboard Streamlit si no está ya corriendo.
    Lo detiene al terminar la sesión de tests.
    Si no puede arrancar en 40s, salta toda la sesión e2e.
    """
    if _streamlit_corriendo():
        yield APP_URL
        return

    import os as _os
    env_proc = {**_os.environ, "FINTECH_DASHBOARD_TEST_MODE": "1"}

    proc = subprocess.Popen(
        [
            sys.executable, "-m", "streamlit", "run", "src/agent/app.py",
            "--server.port=8501",
            "--server.headless=true",
            "--server.fileWatcherType=none",
        ],
        cwd=str(ROOT),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=env_proc,
    )

    for _ in range(40):
        if _streamlit_corriendo():
            break
        time.sleep(1)
    else:
        proc.terminate()
        pytest.skip("Dashboard Streamlit no arrancó en 40s — omitiendo tests e2e")

    yield APP_URL

    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()


# ── Utilidad para texto visible renderizado por React ─────────────────────────

def _texto_visible(page) -> str:
    """
    Devuelve el texto visible del body después de que React renderiza.
    Usar siempre en lugar de page.content() (que es HTML estático sin texto dinámico).
    """
    return page.locator("body").inner_text()


# ══════════════════════════════════════════════════════════════════════════════
# Tests
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.usefixtures("streamlit_server")
def test_dashboard_carga_sin_errores_criticos(page, streamlit_server):
    """
    El dashboard debe cargar en menos de 20s sin errores JavaScript críticos
    y mostrar el título 'Fintech' en la pestaña del navegador.
    """
    errores_criticos = []

    def capturar_error(error):
        msg = error.text
        # Filtrar falsos positivos conocidos de Streamlit en desarrollo
        ignorados = ["ResizeObserver", "Non-Error promise rejection",
                     "favicon", "404", "/_stcore", "WebSocket", "hydrat"]
        if any(tok in msg for tok in ignorados):
            return
        errores_criticos.append(msg)

    page.on("pageerror", capturar_error)
    page.goto(APP_URL, timeout=20_000)
    page.wait_for_load_state("networkidle", timeout=20_000)

    title = page.title()
    assert "Fintech" in title or "fintech" in title.lower(), \
        f"Título inesperado: {title!r}"

    assert not errores_criticos, \
        f"Errores JS críticos: {errores_criticos}"


@pytest.mark.usefixtures("streamlit_server")
def test_dashboard_centro_de_mando_muestra_kpis(page, streamlit_server):
    """
    La página 'Centro de mando' debe mostrar los 4 KPIs y las secciones
    de gráficos. Usa inner_text() porque Streamlit renderiza vía React/JS.
    """
    page.goto(APP_URL, timeout=20_000)
    page.wait_for_load_state("networkidle", timeout=20_000)
    page.wait_for_timeout(4_000)  # dar tiempo al rendering de datos Gold

    texto = _texto_visible(page)

    # Los KPI labels se renderizan en MAYÚSCULAS por CSS (text-transform: uppercase)
    texto_upper = texto.upper()
    for kpi_label in ["USUARIOS", "VOLUMEN COP", "TICKET PROMEDIO", "TASA DE FALLO"]:
        assert kpi_label in texto_upper, \
            f"KPI '{kpi_label}' no visible en Centro de mando"

    for seccion in ["Volumen por segmento", "Usuarios por ciudad"]:
        assert seccion in texto, \
            f"Sección '{seccion}' no visible en Centro de mando"


@pytest.mark.usefixtures("streamlit_server")
def test_dashboard_navegacion_tres_paginas(page, streamlit_server):
    """
    Navega por las 3 páginas y verifica que cada una renderiza contenido
    esperado usando inner_text() (texto visible renderizado por React).
    """
    page.goto(APP_URL, timeout=20_000)
    page.wait_for_load_state("networkidle", timeout=20_000)
    page.wait_for_timeout(4_000)

    # ── Centro de mando ─────────────────────────────────────────────────────
    texto_inicio = _texto_visible(page)
    assert "Centro de mando" in texto_inicio or "Pulso financiero" in texto_inicio, \
        f"Centro de mando no cargó. Texto visible: {texto_inicio[:200]}"

    # ── Mesa de análisis ────────────────────────────────────────────────────
    nav_labels = page.locator("[data-testid='stRadio'] label").all_text_contents()
    assert any("Mesa" in t for t in nav_labels), \
        f"Radio 'Mesa de analisis' no encontrado. Labels: {nav_labels}"

    page.locator("[data-testid='stRadio'] label", has_text="Mesa").click()
    page.wait_for_timeout(2_500)

    texto_mesa = _texto_visible(page)
    assert (
        "Acciones rapidas" in texto_mesa
        or "segmento" in texto_mesa.lower()
        or "ciudad" in texto_mesa.lower()
    ), f"Mesa de análisis no cargó. Texto: {texto_mesa[:200]}"

    chat_input = page.locator("[data-testid='stChatInput']")
    assert chat_input.count() > 0, "Chat input no encontrado en Mesa de análisis"

    # ── Sistema ─────────────────────────────────────────────────────────────
    page.locator("[data-testid='stRadio'] label", has_text="Sistema").click()
    page.wait_for_timeout(2_500)

    texto_sistema = _texto_visible(page)
    assert (
        "Motor conversacional" in texto_sistema
        or "Databricks" in texto_sistema
        or "Estado" in texto_sistema
    ), f"Página Sistema no cargó. Texto: {texto_sistema[:200]}"


@pytest.mark.usefixtures("streamlit_server")
def test_dashboard_botones_acciones_rapidas_son_clickeables(page, streamlit_server):
    """
    En Mesa de análisis, los botones de Acciones rápidas deben existir
    y ser clickeables (no deshabilitados).
    """
    page.goto(APP_URL, timeout=20_000)
    page.wait_for_load_state("networkidle", timeout=20_000)
    page.wait_for_timeout(3_000)

    page.locator("[data-testid='stRadio'] label", has_text="Mesa").click()
    page.wait_for_timeout(2_500)

    botones = page.locator("[data-testid='stButton'] button").all()
    assert len(botones) >= 4, \
        f"Esperaba ≥4 botones de acciones rápidas, encontró {len(botones)}"

    textos = [b.inner_text() for b in botones]
    kws_negocio = ["segmento", "ciudad", "ejecutivo", "fallo", "merchant", "canal", "usuario"]
    tiene_negocio = any(
        any(kw in txt.lower() for kw in kws_negocio) for txt in textos
    )
    assert tiene_negocio, f"Ningún botón tiene texto de negocio. Textos: {textos[:6]}"

    habilitados = [b for b in botones if not b.is_disabled()]
    assert len(habilitados) >= 2, "La mayoría de botones están deshabilitados"


@pytest.mark.usefixtures("streamlit_server")
def test_dashboard_chat_input_acepta_texto(page, streamlit_server):
    """El chat input acepta texto correctamente."""
    page.goto(APP_URL, timeout=20_000)
    page.wait_for_load_state("networkidle", timeout=20_000)
    page.wait_for_timeout(3_000)

    page.locator("[data-testid='stRadio'] label", has_text="Mesa").click()
    page.wait_for_timeout(2_500)

    chat_textarea = page.locator("[data-testid='stChatInput'] textarea")
    assert chat_textarea.count() > 0, "Textarea del chat input no encontrada"

    chat_textarea.first.fill("¿Cuántos usuarios hay por segmento?")
    valor = chat_textarea.first.input_value()
    assert "usuarios" in valor.lower(), f"Chat input no capturó el texto: {valor!r}"


def test_dashboard_health_endpoint_responde(streamlit_server):
    """El endpoint /_stcore/health responde 200 OK."""
    import requests
    r = requests.get(f"{APP_URL}/_stcore/health", timeout=10)
    assert r.status_code == 200, \
        f"Health endpoint retornó {r.status_code}: {r.text[:100]}"


@pytest.mark.usefixtures("streamlit_server")
def test_dashboard_sidebar_muestra_estado_servicios(page, streamlit_server):
    """El sidebar muestra los indicadores de Ollama y Databricks."""
    page.goto(APP_URL, timeout=20_000)
    page.wait_for_load_state("networkidle", timeout=20_000)
    page.wait_for_timeout(3_000)

    sidebar = page.locator("[data-testid='stSidebar']")
    texto_sidebar = sidebar.inner_text()

    assert "Motor conversacional" in texto_sidebar or "Ollama" in texto_sidebar, \
        "Sidebar no muestra estado del motor conversacional"
    assert (
        "Warehouse" in texto_sidebar
        or "Databricks" in texto_sidebar
        or "analitico" in texto_sidebar.lower()
    ), "Sidebar no muestra estado del warehouse analítico"
