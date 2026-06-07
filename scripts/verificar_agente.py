"""
Verificacion del agente fintech.

Comprueba que:
  - Las herramientas importan correctamente.
  - Los tool-calls JSON con args anidados se parsean.
  - Las consultas naturales de metricas usan datos reales, no numeros inventados.
"""

from __future__ import annotations

import re
import sys
import unicodedata

import pandas as pd

sys.path.insert(0, "src")

from agent.agent import _extraer_tool_call, agent_query, resumen_ejecutivo
from src.io.parquet_io import resolve_latest_parquet


def assert_contains(texto: str, esperado: str) -> None:
    if esperado not in texto:
        raise AssertionError(f"No se encontro '{esperado}' en la respuesta")


def assert_not_contains_any(texto: str, prohibidos: list[str]) -> None:
    lower = texto.lower()
    encontrados = [p for p in prohibidos if p.lower() in lower]
    if encontrados:
        raise AssertionError(f"La respuesta contiene terminos inventados: {encontrados}")


def _normalizar(texto: str) -> str:
    normalized = unicodedata.normalize("NFKD", texto or "")
    return "".join(char for char in normalized if not unicodedata.combining(char)).lower()


def assert_contains_normalized(texto: str, esperado: str) -> None:
    if _normalizar(esperado) not in _normalizar(texto):
        raise AssertionError(f"No se encontro '{esperado}' en la respuesta")


def assert_number_present(texto: str, esperado: float, tolerance: float = 0.01) -> None:
    numeros = [float(n) for n in re.findall(r"-?\d+(?:\.\d+)?", texto.replace(",", ""))]
    if not any(abs(numero - esperado) <= tolerance for numero in numeros):
        raise AssertionError(f"No se encontro el valor esperado {esperado} en la respuesta")


def _gold_actual() -> pd.DataFrame:
    return pd.read_parquet(resolve_latest_parquet("data/gold/gold_user_360.parquet"))


def _kpis_actuales() -> dict[str, float]:
    gold = _gold_actual()
    avg_ticket = gold["avg_ticket"].dropna().mean()
    return {
        "total_usuarios": float(len(gold)),
        "volumen_m_cop": round(float(gold["total_amount_cop"].fillna(0).sum()) / 1e6, 2),
        "ticket_promedio": round(float(avg_ticket), 0) if pd.notna(avg_ticket) else 0.0,
        "tasa_fallo_pct": round(float(gold["failure_rate"].fillna(0).mean()) * 100, 1),
    }


def assert_kpis_actuales(texto: str, kpis: dict[str, float]) -> None:
    for etiqueta in ("total_usuarios", "volumen_M_cop", "ticket_promedio", "tasa_fallo_pct"):
        assert_contains(texto, etiqueta)

    assert_number_present(texto, kpis["total_usuarios"], tolerance=0)
    assert_number_present(texto, kpis["volumen_m_cop"], tolerance=0.01)
    assert_number_present(texto, kpis["ticket_promedio"], tolerance=0.5)
    assert_number_present(texto, kpis["tasa_fallo_pct"], tolerance=0.1)


def _ciudad_mayor_tasa_fallo() -> tuple[str, float]:
    gold = _gold_actual()
    city_stats = (
        gold.groupby("city")
        .agg(tasa_fallo_pct=("failure_rate", lambda s: round(float(s.fillna(0).mean()) * 100, 1)))
        .reset_index()
        .sort_values("tasa_fallo_pct", ascending=False)
        .iloc[0]
    )
    return str(city_stats["city"]), float(city_stats["tasa_fallo_pct"])


def main() -> int:
    print("=" * 60)
    print("VERIFICACION AGENTE FINTECH")
    print("=" * 60)

    print("\nImports")
    import src.agent.tools as tools
    print("  OK src.agent.tools importable")
    assert "gold_user_360" in tools.obtener_esquema()
    print("  OK esquema interno disponible para tools")

    print("\nParser tool-call")
    payload = '{"tool":"consultar_sql","args":{"query":"SELECT COUNT(1) FROM gold_user_360"}}'
    parsed = _extraer_tool_call(payload)
    assert parsed and parsed["args"]["query"].startswith("SELECT")
    print("  OK JSON anidado parseado")

    print("\nHerramienta directa")
    kpis = _kpis_actuales()
    resumen = resumen_ejecutivo()
    assert_kpis_actuales(resumen, kpis)
    print("  OK resumen_ejecutivo usa KPIs reales")

    print("\nConsulta natural: resumen ejecutivo")
    respuesta = agent_query("Dame un resumen ejecutivo con los KPIs principales del negocio")
    assert_kpis_actuales(respuesta, kpis)
    assert_not_contains_any(
        respuesta,
        ["ultimo trimestre", "85%", "10% diario", "ciudades costeras"],
    )
    print("  OK respuesta natural anclada a datos reales")

    print("\nConsulta natural: ciudad/fallos")
    ciudad, tasa = _ciudad_mayor_tasa_fallo()
    respuesta_ciudad = agent_query("Cuales son las ciudades con mayor tasa de fallo")
    assert_contains_normalized(respuesta_ciudad, ciudad)
    assert_number_present(respuesta_ciudad, tasa, tolerance=0.1)
    print("  OK intencion ciudad/fallos ordenada por tasa")

    print("\n" + "=" * 60)
    print("OK AGENTE CONFIABLE PARA CONSULTAS KPI BASICAS")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
