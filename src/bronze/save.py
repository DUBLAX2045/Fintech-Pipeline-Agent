"""
Guarda el DataFrame de Bronze en formato Parquet,
particionado por fecha de ingesta.
"""

import os
import pandas as pd


def guardar_bronze_parquet(df: pd.DataFrame, carpeta_base: str = "data/bronze/events") -> str:
    """
    Guarda el DataFrame en Parquet particionado por fecha.
    
    Estructura resultante:
        data/bronze/events/
        └── date=2026-04-21/
            └── batch_a3f8c291.parquet
    
    La partición por fecha permite:
        - Leer solo los datos de un día sin cargar todo
        - Agregar nuevos lotes sin reemplazar los anteriores
    
    Args:
        df:           DataFrame con todos los datos de Bronze
        carpeta_base: Carpeta raíz donde guardar las particiones
    
    Returns:
        Ruta del archivo Parquet guardado
    """
    # Tomar la fecha de ingesta del primer registro (todos tienen la misma)
    fecha_ingesta = df["ingestion_date"].iloc[0]
    batch_id      = df["batch_id"].iloc[0]
    
    # Crear la carpeta de partición si no existe
    carpeta_particion = f"{carpeta_base}/date={fecha_ingesta}"
    os.makedirs(carpeta_particion, exist_ok=True)
    
    # Nombre del archivo Parquet
    ruta_parquet = f"{carpeta_particion}/batch_{batch_id}.parquet"
    
    # Guardar en formato Parquet con compresión snappy (estándar)
    df.to_parquet(ruta_parquet, index=False, compression="snappy", engine="pyarrow")
    
    # Calcular tamaño del archivo para mostrar
    tamano_kb = os.path.getsize(ruta_parquet) / 1024
    
    print("✅ Bronze guardado exitosamente:")
    print(f"   Ruta:    {ruta_parquet}")
    print(f"   Filas:   {len(df):,}")
    print(f"   Tamaño:  {tamano_kb:.1f} KB")
    
    return ruta_parquet