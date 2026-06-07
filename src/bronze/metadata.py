"""
Agrega metadatos de ingesta a cada evento en la Capa Bronze.
Estos metadatos son esenciales para la trazabilidad y auditoría.
"""

import uuid
import pandas as pd
from datetime import datetime, timezone


def agregar_metadatos_ingesta(df: pd.DataFrame, archivo_origen: str) -> pd.DataFrame:
    """
    Agrega columnas de metadatos a cada fila del DataFrame.
    
    Columnas que agrega:
        - ingestion_timestamp: Cuándo se procesó este lote
        - source_file:         Qué archivo originó los datos
        - batch_id:            Identificador único del lote de ingesta
        - ingestion_date:      Fecha (para usar como partición del Parquet)
    
    Args:
        df:              DataFrame con los eventos aplanados
        archivo_origen:  Nombre o ruta del archivo fuente
    
    Returns:
        DataFrame con las columnas de metadatos agregadas
    """
    # Generamos UN solo timestamp y batch_id para todo el lote
    # (todos los eventos del mismo archivo comparten este lote)
    ahora = datetime.now(timezone.utc)
    batch_id = str(uuid.uuid4())[:8]   # Ejemplo: "a3f8c291"
    
    df = df.copy()  # Buena práctica: no modificar el DataFrame original
    
    df["ingestion_timestamp"] = ahora.isoformat()
    df["source_file"]         = archivo_origen
    df["batch_id"]            = batch_id
    df["ingestion_date"]      = ahora.strftime("%Y-%m-%d")   # Para partición
    
    print("✅ Metadatos agregados:")
    print(f"   batch_id:            {batch_id}")
    print(f"   ingestion_timestamp: {ahora.isoformat()}")
    print(f"   source_file:         {archivo_origen}")
    
    return df