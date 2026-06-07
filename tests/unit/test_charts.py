from __future__ import annotations

import base64

import pandas as pd
import pytest

from src.agent.charts import (
    _formato_cop,
    auto_grafico,
    generar_grafico_barras,
    generar_grafico_lineas,
    generar_grafico_pie,
)


pytestmark = pytest.mark.unit


def _assert_png_b64(value: str) -> None:
    raw = base64.b64decode(value)
    assert raw.startswith(b"\x89PNG")


def test_formato_cop_compacts_values():
    assert _formato_cop(1_500_000) == "1.5M"
    assert _formato_cop(20_000) == "20K"
    assert _formato_cop(99) == "99"


def test_chart_generators_return_png_base64():
    df = pd.DataFrame({"segment": ["premium", "student"], "volumen": [1000000, 500000]})

    _assert_png_b64(generar_grafico_barras(df, "segment", "volumen"))
    _assert_png_b64(generar_grafico_barras(df, "segment", "volumen", horizontal=True))
    _assert_png_b64(generar_grafico_lineas(df, "segment", "volumen"))
    _assert_png_b64(generar_grafico_pie(df, "segment", "volumen"))


def test_auto_grafico_selects_or_returns_none():
    df = pd.DataFrame({"segment": ["premium"], "volumen": [1000000]})

    assert auto_grafico(df, "bar")
    assert auto_grafico(df, "line")
    assert auto_grafico(df, "pie")
    assert auto_grafico(pd.DataFrame({"segment": ["premium"]}), "bar") is None
    assert auto_grafico(pd.DataFrame(), "bar") is None
