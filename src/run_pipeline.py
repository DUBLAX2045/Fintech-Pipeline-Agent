"""
Script maestro del pipeline completo.

Ejecuta todas las capas en orden:
    Bronze → Silver → Gold

Uso:
    # Pipeline completo (JSON base → Gold)
    python src/run_pipeline.py

    # Solo Silver y Gold (si Bronze ya existe)
    python src/run_pipeline.py --desde-silver

Prerequisito:
    Tener data/raw/fintech_events_v4.json en su lugar.
"""

import sys
import os
import argparse
from datetime import datetime

sys.path.append(os.path.dirname(os.path.dirname(__file__)))

from src.bronze.pipeline_bronze import ejecutar_pipeline_bronze
from src.silver.pipeline_silver import ejecutar_pipeline_silver
from src.gold.pipeline_gold import ejecutar_pipeline_gold


def ejecutar_todo(desde_silver: bool = False):
    inicio = datetime.now()
    
    print("\n" + "🔷" * 30)
    print("  PIPELINE FINTECH — EJECUCIÓN COMPLETA")
    print("  Bronze → Silver → Gold")
    print("🔷" * 30 + "\n")
    
    if not desde_silver:
        print("── FASE BRONZE ──────────────────────────────")
        ejecutar_pipeline_bronze()
    else:
        print("── SALTANDO BRONZE (--desde-silver activo) ──")
    
    print("\n── FASE SILVER ──────────────────────────────")
    ejecutar_pipeline_silver()
    
    print("\n── FASE GOLD ────────────────────────────────")
    resultado = ejecutar_pipeline_gold()
    
    duracion = (datetime.now() - inicio).total_seconds()
    
    print("\n" + "🔷" * 30)
    print("  PIPELINE COMPLETADO EXITOSAMENTE")
    print(f"  Duración total: {duracion:.1f} segundos")
    print(f"  Usuarios en Gold: {len(resultado['user_360']):,}")
    print("  Listo para Fase 3 — Agente Inteligente ✅")
    print("🔷" * 30 + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--desde-silver", 
        action="store_true",
        help="Salta Bronze y empieza desde Silver (Bronze ya existe)"
    )
    args = parser.parse_args()
    ejecutar_todo(desde_silver=args.desde_silver)