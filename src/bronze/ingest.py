"""
Módulo de ingesta para la Capa Bronze.
Lee el JSON crudo y aplana su estructura anidada.
"""

from datetime import datetime
import glob
import json
import os
import pandas as pd


def cargar_json(ruta_archivo: str) -> list:
    """
    Lee el archivo JSON crudo y lo retorna como lista de diccionarios.
    
    Args:
        ruta_archivo: Ruta al archivo JSON (ejemplo: "data/raw/fintech_events_v4.json")
    
    Returns:
        Lista de eventos como diccionarios Python
    """
    print(f"📂 Leyendo archivo: {ruta_archivo}")
    
    with open(ruta_archivo, "r", encoding="utf-8") as f:
        datos = json.load(f)
    
    print(f"✅ {len(datos)} eventos cargados correctamente")
    return datos


def aplanar_evento(evento: dict) -> dict:
    """
    Convierte un evento anidado en un diccionario plano (una sola fila).
    
    Estructura original:
        evento["detail"]["payload"]["userId"]
    
    Resultado aplanado:
        fila["payload_userId"]
    
    Args:
        evento: Diccionario con la estructura anidada original
    
    Returns:
        Diccionario plano con todas las columnas
    """
    detail = evento.get("detail", {})
    payload = detail.get("payload", {})
    metadata = detail.get("metadata", {})
    location = payload.get("location", {})
    updated_fields = payload.get("updatedFields", {})
    
    fila = {
        # ── Nivel raíz ──────────────────────────────────────────────
        "source":                evento.get("source"),
        "detailType":            evento.get("detailType"),
        
        # ── Nivel detail ─────────────────────────────────────────────
        "event_id":              detail.get("id"),
        "event":                 detail.get("event"),
        "event_version":         detail.get("version"),
        "event_type":            detail.get("eventType"),
        "transaction_type":      detail.get("transactionType"),
        "event_entity":          detail.get("eventEntity"),
        "event_status":          detail.get("eventStatus"),
        
        # ── Payload: Datos del usuario ───────────────────────────────
        "user_id":               payload.get("userId"),
        "user_name":             payload.get("name"),
        "user_age":              payload.get("age"),
        "user_email":            payload.get("email"),
        "user_city":             payload.get("city"),
        "user_segment":          payload.get("segment"),
        
        # ── Payload: Datos de la transacción ─────────────────────────
        "timestamp":             payload.get("timestamp"),
        "account_id":            payload.get("accountId"),
        "amount":                payload.get("amount"),
        "currency":              payload.get("currency"),
        "merchant":              payload.get("merchant"),
        "category":              payload.get("category"),
        "payment_method":        payload.get("paymentMethod"),
        "installments":          payload.get("installments"),
        "balance_before":        payload.get("balanceBefore"),
        "balance_after":         payload.get("balanceAfter"),
        "initial_balance":       payload.get("initialBalance"),   # solo en USER_REGISTERED
        "account_status":        payload.get("status"),           # solo en USER_REGISTERED
        "money_source":          payload.get("source"),           # solo en MONEY_ADDED
        
        # ── Payload: Ubicación ───────────────────────────────────────
        "location_city":         location.get("city"),
        "location_country":      location.get("country"),
        
        # ── Payload: Campos actualizados ─────────────────────────────
        "updated_city":          updated_fields.get("city"),      # solo en USER_PROFILE_UPDATED
        "updated_segment":       updated_fields.get("segment"),   # solo en USER_PROFILE_UPDATED
        
        # ── Metadata: Contexto técnico ───────────────────────────────
        "device":                metadata.get("device"),
        "os":                    metadata.get("os"),
        "ip":                    metadata.get("ip"),
        "channel":               metadata.get("channel"),
    }
    
    return fila


def aplanar_todos(eventos: list) -> pd.DataFrame:
    """
    Aplana todos los eventos y retorna un DataFrame de pandas.
    
    Args:
        eventos: Lista de eventos crudos
    
    Returns:
        DataFrame con todos los eventos aplanados
    """
    print("🔄 Aplanando estructura anidada...")
    
    filas = [aplanar_evento(evento) for evento in eventos]
    df = pd.DataFrame(filas)
    
    print(f"✅ DataFrame creado: {df.shape[0]} filas × {df.shape[1]} columnas")
    return df


# ── Ejecución directa (para probar el módulo) ──────────────────────────────
if __name__ == "__main__":
    eventos = cargar_json("data/raw/fintech_events_v4.json")
    df = aplanar_todos(eventos)
    
    print("\n📊 Vista previa del DataFrame:")
    print(df.head(3).to_string())
    
    print("\n📋 Columnas disponibles:")
    for col in df.columns:
        nulos = df[col].isna().sum()
        print(f"  {col}: {df[col].dtype} — {nulos} nulos")

def _normalizar_event_id(value) -> str | None:
    if pd.isna(value):
        return None
    text = str(value).strip()
    return text or None


def _cargar_eventos_existentes(carpeta_bronze: str | None) -> dict:
    """
    Lee event_id ya escritos en Bronze para detectar duplicados historicos.

    Retorna un mapa event_id -> metadata de la primera aparicion conocida.
    Si Bronze no existe todavia, retorna un dict vacio.
    """
    if not carpeta_bronze or not os.path.exists(carpeta_bronze):
        return {}

    archivos = sorted(glob.glob(os.path.join(carpeta_bronze, "**", "*.parquet"), recursive=True))
    existentes = {}

    for archivo in archivos:
        try:
            historico = pd.read_parquet(archivo)
        except Exception as exc:
            print(f"⚠️  No se pudo leer Bronze historico {archivo}: {exc}")
            continue

        if "event_id" not in historico.columns:
            continue

        for _, row in historico.iterrows():
            event_id = _normalizar_event_id(row.get("event_id"))
            if not event_id or event_id in existentes:
                continue
            existentes[event_id] = {
                "batch_id": row.get("batch_id"),
                "ingestion_timestamp": row.get("ingestion_timestamp"),
                "source_file": row.get("source_file", row.get("source_filename")),
                "bronze_file": archivo,
            }

    return existentes


def detectar_y_registrar_duplicados(
    df: pd.DataFrame,
    carpeta_logs: str = "logs",
    carpeta_bronze: str | None = None,
) -> pd.DataFrame:
    """
    Detecta filas duplicadas por event_id y las registra en un log CSV.
    NO elimina los duplicados — solo los marca y registra.
    
    Args:
        df:            DataFrame con todos los eventos (incluye metadatos)
        carpeta_logs:  Carpeta donde guardar el log de duplicados
    
    Returns:
        DataFrame con columna adicional 'is_duplicate' (True/False)
    """
    df = df.copy()

    if "event_id" not in df.columns:
        raise ValueError("No se puede detectar duplicados: falta la columna event_id")

    existentes = _cargar_eventos_existentes(carpeta_bronze)
    if existentes:
        print(f"📚 event_id historicos cargados: {len(existentes):,}")

    event_ids = df["event_id"].map(_normalizar_event_id)

    missing_event_id = event_ids.isna()
    seen_in_bronze = event_ids.map(lambda event_id: bool(event_id and event_id in existentes))
    repeated_in_batch = event_ids.duplicated(keep="first") & event_ids.notna()

    df["is_duplicate"] = seen_in_bronze | repeated_in_batch | missing_event_id
    df["duplicate_reason"] = None
    df.loc[missing_event_id, "duplicate_reason"] = "missing_event_id"
    df.loc[repeated_in_batch, "duplicate_reason"] = "repeated_in_batch"
    df.loc[seen_in_bronze, "duplicate_reason"] = "seen_in_bronze"
    df["duplicate_first_seen_batch_id"] = event_ids.map(
        lambda event_id: existentes.get(event_id, {}).get("batch_id") if event_id else None
    )
    df["duplicate_first_seen_file"] = event_ids.map(
        lambda event_id: existentes.get(event_id, {}).get("bronze_file") if event_id else None
    )

    total_duplicados = df["is_duplicate"].sum()
    print(f"🔍 Duplicados encontrados: {total_duplicados}")
    
    # Si hay duplicados, guardarlos en el log
    if total_duplicados > 0:
        columnas_log = [
            "event_id", "event", "user_id", "timestamp", "batch_id",
            "ingestion_timestamp", "duplicate_reason",
            "duplicate_first_seen_batch_id", "duplicate_first_seen_file",
        ]
        columnas_log = [col for col in columnas_log if col in df.columns]
        duplicados_df = df[df["is_duplicate"]][columnas_log].copy()
        
        # Crear la carpeta si no existe
        os.makedirs(carpeta_logs, exist_ok=True)
        
        # Nombre del archivo con fecha para no sobrescribir logs anteriores
        fecha = datetime.now().strftime("%Y%m%d_%H%M%S")
        ruta_log = f"{carpeta_logs}/duplicates_{fecha}.csv"
        
        duplicados_df.to_csv(ruta_log, index=False, encoding="utf-8")
        print(f"⚠️  Log de duplicados guardado en: {ruta_log}")
    else:
        print("✅ No se encontraron duplicados")
    
    return df
