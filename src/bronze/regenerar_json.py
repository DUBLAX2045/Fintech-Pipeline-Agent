"""
regenerar_json.py — Redistribuye los timestamps del dataset raw en 35 días.

Problema que resuelve:
    fintech_events_v4.json tiene todos los eventos en una sola fecha,
    lo que produce gold_daily_metrics con solo 1-2 filas y hace que
    la gráfica de tendencia diaria sea inútil.

Solución:
    Reasigna timestamps a lo largo de los últimos DIAS días siguiendo
    un patrón realista: días hábiles reciben más tráfico que fines de
    semana, y los días más recientes tienen ligeramente más volumen
    (tendencia de crecimiento orgánico).

Uso:
    python src/bronze/regenerar_json.py

    Luego re-ejecuta el pipeline completo:
    python src/run_pipeline.py
"""

import json
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT     = Path(__file__).resolve().parents[2]
RUTA_JSON = ROOT / "data" / "raw" / "fintech_events_v4.json"
DIAS      = 35  # días de historia a generar


def _peso_dia(idx: int, total: int, es_laboral: bool) -> float:
    """Peso de un día: crecimiento gradual + penalización de fin de semana."""
    crecimiento = 0.6 + 0.4 * (idx / max(total - 1, 1))
    return crecimiento if es_laboral else crecimiento * 0.25


def _generar_fechas() -> list[datetime]:
    hoy = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return [hoy - timedelta(days=DIAS - 1 - i) for i in range(DIAS)]


def redistribuir_fechas(ruta: Path = RUTA_JSON, dias: int = DIAS) -> None:
    if not ruta.exists():
        raise FileNotFoundError(f"No se encontró el JSON en: {ruta}")

    print(f"📂 Leyendo {ruta.name}...")
    data: list[dict] = json.loads(ruta.read_text(encoding="utf-8"))
    print(f"   {len(data):,} eventos cargados")

    fechas = _generar_fechas()
    pesos  = [
        _peso_dia(i, dias, f.weekday() < 5)
        for i, f in enumerate(fechas)
    ]
    total_peso = sum(pesos)
    pesos_norm = [p / total_peso for p in pesos]

    rng = random.Random(42)  # semilla fija → reproducible

    cambiados = 0
    for evento in data:
        payload = evento.get("detail", {}).get("payload", {})
        if "timestamp" not in payload:
            continue
        fecha_base = rng.choices(fechas, weights=pesos_norm, k=1)[0]
        hora   = rng.randint(7, 22)
        minuto = rng.randint(0, 59)
        seg    = rng.randint(0, 59)
        nuevo_ts = fecha_base.replace(hour=hora, minute=minuto, second=seg, microsecond=0)
        payload["timestamp"] = nuevo_ts.isoformat()
        cambiados += 1

    ruta.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    inicio = fechas[0].date()
    fin    = fechas[-1].date()
    print(f"✅ {cambiados:,} timestamps redistribuidos")
    print(f"   Rango: {inicio} → {fin} ({dias} días)")
    print(f"   Días hábiles con ~4× más volumen que fines de semana")
    print(f"\nAhora ejecuta el pipeline completo:")
    print(f"   python src/run_pipeline.py")


if __name__ == "__main__":
    redistribuir_fechas()
