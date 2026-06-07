from __future__ import annotations

import pandas as pd
import pytest

from src.silver.pipeline_silver import (
    ExchangeRateService,
    paso2_limpiar_tipos,
    paso4_enriquecer_geolocalización,
    paso5_enriquecer_moneda,
    paso6_renombrar_y_seleccionar_columnas,
    paso7_guardar_silver,
)


pytestmark = pytest.mark.unit


def test_exchange_rate_service_uses_api_and_cache(requests_mock):
    service = ExchangeRateService()
    requests_mock.get("https://open.er-api.com/v6/latest/COP", json={"rates": {"USD": 0.0003}})

    assert service.tasa_cop_usd() == 0.0003
    assert service.tasa_cop_usd() == 0.0003
    assert requests_mock.call_count == 1


def test_paso2_limpiar_tipos_normalizes_fields():
    df = pd.DataFrame(
        {
            "timestamp": ["2026-01-01T10:00:00Z"],
            "amount": ["1000"],
            "balance_before": ["2000"],
            "balance_after": ["1500"],
            "initial_balance": [None],
            "installments": [None],
            "user_email": [" USER@EXAMPLE.COM "],
        }
    )

    result = paso2_limpiar_tipos(df)

    assert str(result.loc[0, "timestamp"].tz) == "UTC"
    assert result.loc[0, "date"].isoformat() == "2026-01-01"
    assert result.loc[0, "amount"] == 1000.0
    assert result.loc[0, "installments"] == 1
    assert result.loc[0, "user_email"] == "user@example.com"


def test_paso4_enriquecer_geolocalizacion_uses_public_ip_api(requests_mock):
    df = pd.DataFrame(
        {
            "ip": ["8.8.8.8"],
            "ip_is_private": [False],
            "location_city": [None],
            "location_country": [None],
            "user_city": ["Bogota"],
        }
    )
    requests_mock.get(
        "http://ip-api.com/json/8.8.8.8?fields=status,city,country",
        json={"status": "success", "city": "Mountain View", "country": "United States"},
    )

    result = paso4_enriquecer_geolocalización(df)

    assert result.loc[0, "location_city"] == "Mountain View"
    assert result.loc[0, "location_country"] == "United States"


def test_paso5_enriquecer_moneda_and_column_selection(monkeypatch):
    from src.silver import pipeline_silver

    monkeypatch.setattr(pipeline_silver.fx, "tasa_cop_usd", lambda: 0.00025)
    df = pd.DataFrame(
        {
            "amount": [1000.0, None],
            "source": ["x", "x"],
            "detailType": ["event", "event"],
            "event_type": ["payment", "payment"],
            "transaction_type": ["payment", "payment"],
            "event_entity": ["USER", "USER"],
            "event_version": ["1.0", "1.0"],
            "account_status": [None, None],
            "money_source": [None, None],
            "updated_city": [None, None],
            "updated_segment": [None, None],
            "ingestion_date": ["2026-01-01", "2026-01-01"],
            "is_duplicate": [False, True],
        }
    )

    enriched = paso5_enriquecer_moneda(df)
    selected = paso6_renombrar_y_seleccionar_columnas(enriched)

    assert enriched.loc[0, "amount_usd"] == 0.25
    assert pd.isna(enriched.loc[1, "amount_usd"])
    assert "amount_cop" in selected.columns
    assert "bronze_is_duplicate" in selected.columns
    assert "source" not in selected.columns


def test_paso7_guardar_silver_writes_file(tmp_path):
    df = pd.DataFrame({"event_id": ["evt-1"], "timestamp": [pd.Timestamp("2026-01-01T00:00:00Z")]})

    path = paso7_guardar_silver(df, str(tmp_path))

    assert path.endswith("silver_events.parquet")
    assert pd.read_parquet(path).loc[0, "event_id"] == "evt-1"
