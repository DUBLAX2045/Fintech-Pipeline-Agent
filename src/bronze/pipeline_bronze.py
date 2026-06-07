"""
Pipeline completo de la Capa Bronze.
Ejecuta todos los pasos en orden:
    1. Leer JSON crudo
    2. Aplanar estructura anidada
    3. Agregar metadatos de ingesta
    4. Detectar y registrar duplicados
    5. Guardar en Parquet

Uso:
    python src/bronze/pipeline_bronze.py
"""

import sys
import os

# Permite importar desde la raíz del proyecto
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from src.bronze.ingest import cargar_json, aplanar_todos, detectar_y_registrar_duplicados
from src.bronze.metadata import agregar_metadatos_ingesta
from src.bronze.save import guardar_bronze_parquet


def ejecutar_pipeline_bronze(
    ruta_json: str = "data/raw/fintech_events_v4.json",
    carpeta_bronze: str = "data/bronze/events",
    carpeta_logs: str = "logs"
) -> str:
    """
    Ejecuta el pipeline completo de la Capa Bronze.
    
    Returns:
        Ruta del archivo Parquet generado
    """
    print("=" * 60)
    print("🚀 INICIANDO PIPELINE — CAPA BRONZE")
    print("=" * 60)
    
    # ── Paso 1: Leer JSON ────────────────────────────────────────────
    print("\n📌 PASO 1: Cargando JSON crudo...")
    eventos = cargar_json(ruta_json)
    
    # ── Paso 2: Aplanar ──────────────────────────────────────────────
    print("\n📌 PASO 2: Aplanando estructura anidada...")
    df = aplanar_todos(eventos)
    
    # ── Paso 3: Metadatos ────────────────────────────────────────────
    print("\n📌 PASO 3: Agregando metadatos de ingesta...")
    nombre_archivo = os.path.basename(ruta_json)
    df = agregar_metadatos_ingesta(df, nombre_archivo)
    
    # ── Paso 4: Duplicados ───────────────────────────────────────────
    print("\n📌 PASO 4: Detectando duplicados...")
    df = detectar_y_registrar_duplicados(df, carpeta_logs, carpeta_bronze=carpeta_bronze)
    
    # ── Paso 5: Guardar ──────────────────────────────────────────────
    print("\n📌 PASO 5: Guardando en formato Parquet...")
    ruta_parquet = guardar_bronze_parquet(df, carpeta_bronze)
    
    # ── Resumen ──────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("✅ PIPELINE BRONZE COMPLETADO EXITOSAMENTE")
    print(f"   Eventos procesados: {len(df):,}")
    print(f"   Columnas totales:   {len(df.columns)}")
    print(f"   Archivo Parquet:    {ruta_parquet}")
    print("=" * 60)
    
    return ruta_parquet


if __name__ == "__main__":
    ejecutar_pipeline_bronze()
