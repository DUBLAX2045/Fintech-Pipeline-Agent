# 📘 Manual de Desarrollo — Fase 1: Capa Bronze
## Proyecto: Plataforma de Datos Fintech en Tiempo Real

> **Nivel:** Principiante  
> **Lenguaje:** Python 3.10+  
> **IDE recomendado:** Visual Studio Code  
> **Última actualización:** Abril 2026

---

## 📋 Tabla de Contenidos

1. [Preparación del Entorno](#1-preparación-del-entorno)
2. [Estructura de Carpetas del Proyecto](#2-estructura-de-carpetas-del-proyecto)
3. [Fase 1 — Capa Bronze: Paso a Paso](#3-fase-1--capa-bronze-paso-a-paso)
   - [Paso 1: Leer el JSON crudo](#paso-1-leer-el-json-crudo)
   - [Paso 2: Aplanar la estructura anidada](#paso-2-aplanar-la-estructura-anidada)
   - [Paso 3: Agregar metadatos de ingesta](#paso-3-agregar-metadatos-de-ingesta)
   - [Paso 4: Detectar duplicados](#paso-4-detectar-duplicados)
   - [Paso 5: Guardar en formato Parquet](#paso-5-guardar-en-formato-parquet)
4. [Fase 1B — Simulador de Eventos de Ecommerce](#4-fase-1b--simulador-de-eventos-de-ecommerce)
5. [Fase 2 — Capas Silver y Gold (Preview)](#5-fase-2--capas-silver-y-gold-preview)
6. [Cómo Documentar el README](#6-cómo-documentar-el-readme)
7. [Resumen de Archivos del Proyecto](#7-resumen-de-archivos-del-proyecto)

---

## 1. Preparación del Entorno

### 1.1 Abrir Visual Studio Code

1. Descarga VS Code desde: https://code.visualstudio.com/
2. Instala las extensiones recomendadas:
   - **Python** (Microsoft) — autocompletado y ejecución de scripts
   - **Jupyter** (Microsoft) — para notebooks de exploración
   - **Pylance** — análisis de código inteligente
   - **Rainbow CSV** — ver archivos CSV con colores
3. Abre la carpeta del proyecto: `Archivo → Abrir Carpeta`

### 1.2 Crear el Entorno Virtual (`venv`)

Un **entorno virtual** es una burbuja aislada donde instalas las librerías del proyecto sin afectar el resto de tu computador.

```bash
# 1. Abre la terminal en VS Code (Ctrl+Ñ en Windows / Ctrl+` en Mac)

# 2. Navega a la carpeta del proyecto
cd fintech_pipeline

# 3. Crea el entorno virtual (se llama 'venv' por convención)
python -m venv venv

# 4. Actívalo:
# En Windows:
venv\Scripts\activate
# En Mac/Linux:
source venv/bin/activate

# Sabrás que está activo porque el prompt cambia a:
# (venv) C:\...\fintech_pipeline>
```

> ⚠️ **IMPORTANTE**: Activa el entorno virtual SIEMPRE antes de trabajar. Si cierras la terminal, debes volver a activarlo.

### 1.3 Instalar Dependencias

Con el entorno virtual activo, ejecuta:

```bash
pip install pandas pyarrow faker schedule requests python-dotenv
```

Descripción de cada librería:

| Librería | ¿Para qué sirve en este proyecto? |
|---|---|
| `pandas` | Manipular y transformar los datos (limpiar, filtrar, agrupar) |
| `pyarrow` | Guardar datos en formato Parquet (eficiente y rápido) |
| `faker` | Generar datos falsos pero realistas para el simulador de ecommerce |
| `schedule` | Ejecutar tareas automáticamente cada cierto tiempo (micro-batch) |
| `requests` | Hacer llamadas a las APIs externas (ip-api.com, ExchangeRate) |
| `python-dotenv` | Leer variables de entorno desde un archivo `.env` (para API keys) |

Después de instalar, guarda las dependencias para que otros puedan replicar tu entorno:

```bash
pip freeze > requirements.txt
```

---

## 2. Estructura de Carpetas del Proyecto

Organiza tu proyecto así desde el principio. Una buena estructura hace que el código sea fácil de entender y mantener.

```
fintech_pipeline/
│
├── data/                          ← Todas las capas de datos
│   ├── raw/                       ← JSON original sin tocar
│   │   └── fintech_events_v4.json
│   ├── bronze/                    ← Datos aplanados + metadatos de ingesta
│   │   └── events/
│   │       └── date=2026-04-21/
│   │           └── batch_001.parquet
│   ├── silver/                    ← Datos limpios + enriquecidos (Fase 2)
│   └── gold/                      ← Agregados por usuario (Fase 3)
│
├── src/                           ← Código fuente del proyecto
│   ├── bronze/
│   │   ├── ingest.py              ← Leer y aplanar el JSON
│   │   ├── metadata.py            ← Agregar metadatos de ingesta
│   │   └── simulator.py           ← Generador de eventos de ecommerce
│   ├── silver/                    ← (Fase 2)
│   └── gold/                      ← (Fase 3)
│
├── notebooks/                     ← Exploración y prototipado
│   └── 01_explorar_json.ipynb
│
├── logs/                          ← Registros de ejecución
│   └── duplicates_log.csv
│
├── .env                           ← Claves de API (NUNCA subir a Git)
├── .gitignore                     ← Archivos que Git debe ignorar
├── requirements.txt               ← Lista de dependencias
└── README.md                      ← Documentación del proyecto
```

Crea todas las carpetas de una vez ejecutando esto en la terminal:

```bash
mkdir -p data/raw data/bronze/events data/silver data/gold
mkdir -p src/bronze src/silver src/gold
mkdir -p notebooks logs
```

Crea el archivo `.gitignore` con este contenido:

```
# .gitignore
venv/
__pycache__/
*.pyc
.env
data/bronze/
data/silver/
data/gold/
*.parquet
```

Crea el archivo `.env` (aquí irán tus API keys, nunca se sube a Git):

```
# .env
EXCHANGE_RATE_API_KEY=tu_clave_aqui
IPAPI_KEY=opcional
```

---

## 3. Fase 1 — Capa Bronze: Paso a Paso

> **Objetivo de la Capa Bronze**: Capturar los eventos exactamente como vienen del JSON original. No modificamos los valores, solo los organizamos en formato tabular y les agregamos información de cuándo y desde dónde llegaron.
>
> **Regla de oro de Bronze**: "Guardar todo, modificar nada."

---

### Paso 1: Leer el JSON Crudo

Primero, explora el JSON en un Jupyter Notebook para entender su estructura antes de escribir código de producción.

Crea el archivo `notebooks/01_explorar_json.ipynb` y ejecuta:

```python
# Celda 1: Importar librerías
import json
import pandas as pd

# Celda 2: Leer el archivo JSON
with open("../data/raw/fintech_events_v4.json", "r", encoding="utf-8") as f:
    raw_data = json.load(f)

print(f"Total de eventos: {len(raw_data)}")
print(f"Tipo de dato: {type(raw_data)}")
```

```python
# Celda 3: Ver la estructura del primer evento
print(json.dumps(raw_data[0], indent=2, ensure_ascii=False))
```

Verás esta estructura (así se ve el JSON real de tu archivo):

```
Nivel 1 (raíz)
├── source          → "fintech.app"
├── detailType      → "event"
└── detail/
    ├── id          → UUID único del evento
    ├── event       → "TRANSFER_SENT", "PAYMENT_MADE", etc.
    ├── version     → "1.0"
    ├── eventType   → igual que event pero en minúsculas
    ├── eventStatus → "SUCCESS" o "FAILED"
    ├── payload/    → Datos del usuario y la transacción
    │   ├── userId, name, age, email, city, segment
    │   ├── timestamp, accountId
    │   ├── amount, currency, merchant, category
    │   ├── paymentMethod, installments
    │   ├── balanceBefore, balanceAfter
    │   └── location/ → {city, country}
    └── metadata/   → Contexto técnico
        ├── device, os, ip, channel
```

```python
# Celda 4: Ver cuántos eventos hay por tipo
from collections import Counter
tipos = Counter(r["detail"]["event"] for r in raw_data)
for tipo, cantidad in sorted(tipos.items()):
    print(f"  {tipo}: {cantidad}")
```

Resultado esperado:
```
  MONEY_ADDED: 266
  PAYMENT_FAILED: 300
  PAYMENT_MADE: 294
  PURCHASE_MADE: 283
  TRANSFER_SENT: 280
  USER_PROFILE_UPDATED: 284
  USER_REGISTERED: 293
```

```python
# Celda 5: Ver los valores únicos de campos clave
ciudades = set(r["detail"]["payload"].get("city", "") for r in raw_data)
segmentos = set(r["detail"]["payload"].get("segment", "") for r in raw_data)
print("Ciudades:", ciudades)
print("Segmentos:", segmentos)
```

---

### Paso 2: Aplanar la Estructura Anidada

El JSON tiene 3 niveles de profundidad. Para guardarlo en formato tabular (como una hoja de cálculo), necesitamos **aplanarlo**: convertir cada evento en una sola fila con todas sus columnas.

Crea el archivo `src/bronze/ingest.py`:

```python
# src/bronze/ingest.py
"""
Módulo de ingesta para la Capa Bronze.
Lee el JSON crudo y aplana su estructura anidada.
"""

import json
import pandas as pd
from pathlib import Path


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
```

**Para probar este script**, ejecuta en la terminal:
```bash
python src/bronze/ingest.py
```

---

### Paso 3: Agregar Metadatos de Ingesta

Los metadatos de ingesta responden a estas preguntas para cada lote de datos:
- **¿Cuándo** llegó este dato? → `ingestion_timestamp`
- **¿De dónde** vino? → `source_file`
- **¿A qué lote** pertenece? → `batch_id`

Crea el archivo `src/bronze/metadata.py`:

```python
# src/bronze/metadata.py
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
    
    print(f"✅ Metadatos agregados:")
    print(f"   batch_id:            {batch_id}")
    print(f"   ingestion_timestamp: {ahora.isoformat()}")
    print(f"   source_file:         {archivo_origen}")
    
    return df
```

---

### Paso 4: Detectar Duplicados

En Bronze **no eliminamos duplicados** (necesitamos mantener el dato original), pero sí los **registramos** en un log para auditoría.

Agrega esta función al final de `src/bronze/ingest.py`:

```python
# Agregar esto al final de src/bronze/ingest.py

import os
from datetime import datetime

def detectar_y_registrar_duplicados(df: pd.DataFrame, carpeta_logs: str = "logs") -> pd.DataFrame:
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
    
    # Marcar duplicados: is_duplicate = True si event_id aparece más de una vez
    # keep=False → marca TODAS las ocurrencias del duplicado, no solo la segunda
    df["is_duplicate"] = df.duplicated(subset=["event_id"], keep=False)
    
    total_duplicados = df["is_duplicate"].sum()
    print(f"🔍 Duplicados encontrados: {total_duplicados}")
    
    # Si hay duplicados, guardarlos en el log
    if total_duplicados > 0:
        duplicados_df = df[df["is_duplicate"] == True][
            ["event_id", "event", "user_id", "timestamp", "batch_id", "ingestion_timestamp"]
        ].copy()
        
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
```

---

### Paso 5: Guardar en Formato Parquet

Parquet es el formato estándar en ingeniería de datos porque:
- Ocupa mucho menos espacio que CSV (compresión automática)
- Es mucho más rápido de leer para consultas
- Soporta tipos de datos ricos (fechas, números, booleanos)

Crea el archivo `src/bronze/save.py`:

```python
# src/bronze/save.py
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
    
    print(f"✅ Bronze guardado exitosamente:")
    print(f"   Ruta:    {ruta_parquet}")
    print(f"   Filas:   {len(df):,}")
    print(f"   Tamaño:  {tamano_kb:.1f} KB")
    
    return ruta_parquet
```

---

### Paso 6: Script Principal de la Capa Bronze

Ahora juntamos todos los pasos en un script principal. Crea `src/bronze/pipeline_bronze.py`:

```python
# src/bronze/pipeline_bronze.py
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
    df = detectar_y_registrar_duplicados(df, carpeta_logs)
    
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
```

**Para ejecutar el pipeline completo:**
```bash
python src/bronze/pipeline_bronze.py
```

---

## 4. Fase 1B — Simulador de Eventos de Ecommerce

Este simulador genera nuevos eventos de manera continua para imitar un ecommerce real enviando datos en "casi tiempo real" (micro-batch cada N segundos).

Crea `src/bronze/simulator.py`:

```python
# src/bronze/simulator.py
"""
Simulador de eventos de ecommerce.
Genera nuevos eventos con datos realistas usando la librería Faker,
y los agrega al pipeline de Bronze en modo micro-batch.

Uso:
    python src/bronze/simulator.py
    (Ctrl+C para detener)
"""

import json
import uuid
import time
import random
import os
from datetime import datetime, timezone
from faker import Faker

# Importamos el pipeline de Bronze para procesar cada nuevo lote
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
from src.bronze.ingest import aplanar_todos, detectar_y_registrar_duplicados
from src.bronze.metadata import agregar_metadatos_ingesta
from src.bronze.save import guardar_bronze_parquet


# ── Configuración del simulador ─────────────────────────────────────────────
fake = Faker("es_CO")   # Datos con contexto colombiano

# Catálogos de valores posibles (basados en el dataset real)
EVENTOS_POSIBLES = [
    "PAYMENT_MADE", "PURCHASE_MADE", "TRANSFER_SENT",
    "MONEY_ADDED", "PAYMENT_FAILED"
]

CIUDADES = ["Bogotá", "Medellín", "Cali", "Barranquilla", "Cartagena"]
SEGMENTOS = ["young_professional", "premium", "family", "student"]
MERCHANTS = ["Rappi", "Éxito", "Falabella", "Nike", "Zara", "Netflix", "Spotify"]
CATEGORIAS = ["food", "transport", "shopping", "entertainment", "health"]
METODOS_PAGO = ["debit_card", "credit_card", "wallet", "bank_transfer"]
DISPOSITIVOS = ["mobile", "web", "tablet"]
OS_OPCIONES = ["ios", "android", "windows", "macos"]
CANALES = ["app", "web", "api"]


def generar_evento_ecommerce() -> dict:
    """
    Genera un evento de ecommerce simulado con datos realistas.
    La estructura es idéntica al JSON original para que el pipeline
    lo procese sin cambios.
    
    Returns:
        Diccionario con la misma estructura que fintech_events_v4.json
    """
    tipo_evento = random.choice(EVENTOS_POSIBLES)
    user_num = random.randint(1, 500)
    ciudad = random.choice(CIUDADES)
    monto = random.randint(5000, 2000000)       # Entre $5.000 y $2.000.000 COP
    balance_antes = random.randint(50000, 5000000)
    balance_despues = max(0, balance_antes - monto)
    
    evento = {
        "source": "ecommerce.app",              # ← Distingue del fintech original
        "detailType": "event",
        "detail": {
            "id": str(uuid.uuid4()),             # UUID único por evento
            "event": tipo_evento,
            "version": "1.0",
            "eventType": tipo_evento.lower(),
            "transactionType": tipo_evento.lower(),
            "eventEntity": "USER",
            "eventStatus": "FAILED" if tipo_evento == "PAYMENT_FAILED" else "SUCCESS",
            "payload": {
                "userId": f"ecom_user_{user_num}",
                "name": fake.first_name(),
                "age": random.randint(18, 65),
                "email": fake.email(),
                "city": ciudad,
                "segment": random.choice(SEGMENTOS),
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "accountId": f"ecom_acc_{random.randint(100, 999)}",
                "amount": monto,
                "currency": "COP",
                "merchant": random.choice(MERCHANTS),
                "category": random.choice(CATEGORIAS),
                "paymentMethod": random.choice(METODOS_PAGO),
                "installments": random.choice([1, 3, 6, 12]),
                "balanceBefore": balance_antes,
                "balanceAfter": balance_despues,
                "location": {
                    "city": ciudad,
                    "country": "Colombia"
                }
            },
            "metadata": {
                "device": random.choice(DISPOSITIVOS),
                "os": random.choice(OS_OPCIONES),
                "ip": fake.ipv4_public(),        # IPs públicas (para enriquecer con ip-api.com después)
                "channel": random.choice(CANALES)
            }
        }
    }
    
    return evento


def ejecutar_simulador(
    eventos_por_lote: int = 10,
    intervalo_segundos: int = 30,
    carpeta_bronze: str = "data/bronze/events",
    max_lotes: int = None           # None = correr indefinidamente
):
    """
    Ejecuta el simulador de ecommerce en modo micro-batch.
    
    Cada 'intervalo_segundos' genera 'eventos_por_lote' eventos nuevos
    y los procesa a través del pipeline de Bronze.
    
    Args:
        eventos_por_lote:    Cuántos eventos generar por ciclo
        intervalo_segundos:  Cada cuánto tiempo generar un nuevo lote
        carpeta_bronze:      Dónde guardar los archivos Parquet
        max_lotes:           Límite de lotes (None = infinito)
    """
    print("🛒 SIMULADOR DE ECOMMERCE INICIADO")
    print(f"   Eventos por lote:  {eventos_por_lote}")
    print(f"   Intervalo:         cada {intervalo_segundos} segundos")
    print(f"   Destino:           {carpeta_bronze}")
    print("   (Ctrl+C para detener)\n")
    
    lote_num = 0
    
    try:
        while True:
            lote_num += 1
            print(f"\n⏱️  Lote #{lote_num} — {datetime.now().strftime('%H:%M:%S')}")
            
            # Generar eventos nuevos
            nuevos_eventos = [generar_evento_ecommerce() for _ in range(eventos_por_lote)]
            
            # Procesar a través del pipeline Bronze
            df = aplanar_todos(nuevos_eventos)
            df = agregar_metadatos_ingesta(df, f"ecommerce_simulator_lote_{lote_num}")
            df = detectar_y_registrar_duplicados(df)
            ruta = guardar_bronze_parquet(df, carpeta_bronze)
            
            print(f"   ✅ {eventos_por_lote} eventos procesados → {ruta}")
            
            # Verificar límite de lotes
            if max_lotes and lote_num >= max_lotes:
                print(f"\n✅ Límite de {max_lotes} lotes alcanzado. Simulador detenido.")
                break
            
            # Esperar hasta el siguiente lote
            print(f"   💤 Esperando {intervalo_segundos}s para el próximo lote...")
            time.sleep(intervalo_segundos)
    
    except KeyboardInterrupt:
        print(f"\n\n⛔ Simulador detenido por el usuario.")
        print(f"   Total de lotes procesados: {lote_num}")
        print(f"   Total de eventos generados: {lote_num * eventos_por_lote}")


if __name__ == "__main__":
    # Para pruebas rápidas: 5 eventos cada 10 segundos, máximo 3 lotes
    ejecutar_simulador(
        eventos_por_lote=5,
        intervalo_segundos=10,
        max_lotes=3           # Quitar esta línea para modo producción
    )
```

**Para probar el simulador:**
```bash
python src/bronze/simulator.py
```

---

## 5. Fase 2 — Capas Silver y Gold (Preview)

Esta sección es un adelanto para que sepas hacia dónde va el proyecto.

### Capa Silver (siguiente fase)

La capa Silver toma los datos Bronze y los **limpia y enriquece**:

```
Bronze (crudo, aplanado)
    ↓
Silver (limpio, enriquecido)
    - Convertir timestamp a datetime real
    - Convertir amount a float (tipo numérico correcto)
    - Agregar amount_usd (llamando a ExchangeRate API)
    - Agregar ip_country, ip_city (llamando a ip-api.com)
    - Marcar registros FAILED vs SUCCESS
    - Eliminar duplicados confirmados
```

### Capa Gold (fase posterior)

La capa Gold consolida los datos por usuario para construir la **Visión 360**:

```
Silver (evento a evento)
    ↓
Gold (consolidado por userId)
    - total_transactions por usuario
    - total_amount_cop y total_amount_usd
    - top_merchant, top_category
    - preferred_channel
    - balance_current (último balance conocido)
    - failed_transaction_rate
```

### Integración con flujo en tiempo real

Una vez que el simulador funcione, el flujo completo será:

```
Ecommerce (eventos nuevos)
    ↓ cada 30 segundos
Simulador genera JSON
    ↓
Pipeline Bronze (aplanar + metadatos + Parquet)
    ↓
Pipeline Silver (limpiar + enriquecer con APIs)
    ↓
Pipeline Gold (actualizar Visión 360 del usuario)
    ↓
Dashboard (mostrar métricas actualizadas)
```

---

## 6. Cómo Documentar el README

El README es el "mapa" del proyecto. Alguien que lo lea debe entender qué hace el proyecto, cómo instalarlo y cómo usarlo sin necesitar preguntar nada.

Crea `README.md` en la raíz del proyecto:

```markdown
# 🏦 Fintech Pipeline — Plataforma de Datos en Tiempo Real

Plataforma de procesamiento de datos fintech con arquitectura Bronze/Silver/Gold,
visión 360 del usuario y agente inteligente de consultas en lenguaje natural.

## 📋 Descripción

Este proyecto implementa una arquitectura de datos moderna para procesar
eventos financieros (pagos, transferencias, compras) de una fintech colombiana.

**Fuente de datos**: `fintech_events_v4.json` — 2.000 eventos reales simulados

## 🏗️ Arquitectura

```
Bronze  → Datos crudos aplanados + metadatos de ingesta
Silver  → Datos limpios + enriquecidos con APIs (ip-api.com, ExchangeRate)
Gold    → Agregados por usuario (Visión 360) + métricas de negocio
Agente  → Consultas en lenguaje natural sobre capa Gold (Claude AI)
```

## 🚀 Instalación

### 1. Clonar el repositorio
```bash
git clone https://github.com/tu-usuario/fintech_pipeline.git
cd fintech_pipeline
```

### 2. Crear entorno virtual
```bash
python -m venv venv
# Windows:
venv\Scripts\activate
# Mac/Linux:
source venv/bin/activate
```

### 3. Instalar dependencias
```bash
pip install -r requirements.txt
```

### 4. Configurar variables de entorno
```bash
cp .env.example .env
# Editar .env con tus API keys
```

## 📦 Dependencias

| Librería | Versión | Uso |
|---|---|---|
| pandas | >=2.0 | Transformación de datos |
| pyarrow | >=14.0 | Formato Parquet |
| faker | >=24.0 | Simulador de ecommerce |
| schedule | >=1.2 | Micro-batch scheduler |
| requests | >=2.31 | Llamadas a APIs externas |
| python-dotenv | >=1.0 | Variables de entorno |

## 📂 Estructura del Proyecto

```
fintech_pipeline/
├── data/raw/          → JSON fuente original
├── data/bronze/       → Parquet por fecha (capa Bronze)
├── data/silver/       → Parquet enriquecido (capa Silver)
├── data/gold/         → Parquet agregado por usuario (capa Gold)
├── src/bronze/        → Scripts de la capa Bronze
├── notebooks/         → Exploración y prototipado
└── logs/              → Registros de duplicados y errores
```

## ▶️ Uso

### Ejecutar pipeline Bronze (archivo base)
```bash
python src/bronze/pipeline_bronze.py
```

### Ejecutar simulador de ecommerce
```bash
python src/bronze/simulator.py
```

## 📊 Fases del Proyecto

### ✅ Fase 1 — Capa Bronze (COMPLETADA)
- [x] Leer y aplanar JSON anidado
- [x] Agregar metadatos de ingesta (timestamp, batch_id, source_file)
- [x] Detectar y registrar duplicados
- [x] Guardar en Parquet particionado por fecha
- [x] Simulador de eventos de ecommerce con Faker

### 🔄 Fase 2 — Capa Silver (EN PROGRESO)
- [ ] Normalizar tipos de datos
- [ ] Enriquecimiento con ip-api.com (geolocalización)
- [ ] Enriquecimiento con ExchangeRate API (conversión de moneda)
- [ ] Limpieza de registros fallidos

### ⏳ Fase 3 — Capa Gold
- [ ] Agregados por usuario (Visión 360)
- [ ] Métricas de negocio

### ⏳ Fase 4 — Agente Inteligente
- [ ] Text-to-SQL con Claude AI
- [ ] Dashboard interactivo

## 🔑 APIs Externas

| API | URL | Uso |
|---|---|---|
| ip-api.com | https://ip-api.com/ | Geolocalización por IP |
| ExchangeRate | https://www.exchangerate-api.com/ | Tasas de cambio COP→USD |
| CoinGecko | https://www.coingecko.com/en/api | Datos cripto (opcional) |

## 🧪 Estructura de los Datos

### Tipos de eventos (`event`)
- `USER_REGISTERED` — Registro de nuevo usuario
- `USER_PROFILE_UPDATED` — Actualización de perfil
- `MONEY_ADDED` — Recarga de saldo
- `PAYMENT_MADE` — Pago realizado
- `PURCHASE_MADE` — Compra realizada
- `TRANSFER_SENT` — Transferencia enviada
- `PAYMENT_FAILED` — Pago fallido

### Ciudades
Bogotá, Medellín, Cali, Barranquilla, Cartagena

### Segmentos de usuario
young_professional, premium, family, student
```

---

## 7. Resumen de Archivos del Proyecto

Al finalizar la Fase 1, deberías tener estos archivos:

| Archivo | Estado | Descripción |
|---|---|---|
| `data/raw/fintech_events_v4.json` | ✅ | JSON original sin modificar |
| `data/bronze/events/date=.../batch_*.parquet` | ✅ | Bronze generado |
| `src/bronze/ingest.py` | ✅ | Lectura y aplanamiento del JSON |
| `src/bronze/metadata.py` | ✅ | Metadatos de ingesta |
| `src/bronze/save.py` | ✅ | Guardado en Parquet |
| `src/bronze/pipeline_bronze.py` | ✅ | Script principal que une todo |
| `src/bronze/simulator.py` | ✅ | Simulador de eventos de ecommerce |
| `logs/duplicates_*.csv` | ✅ | Log de duplicados (si existen) |
| `requirements.txt` | ✅ | Dependencias del proyecto |
| `.env` | ✅ | API keys (no subir a Git) |
| `.gitignore` | ✅ | Archivos ignorados por Git |
| `README.md` | ✅ | Documentación del proyecto |
| `notebooks/01_explorar_json.ipynb` | ✅ | Exploración inicial |

---

## ✅ Checklist de la Fase 1

Antes de pasar a la Fase 2, verifica:

- [ ] El entorno virtual está creado y activo
- [ ] Todas las dependencias están instaladas (`pip install -r requirements.txt`)
- [ ] La estructura de carpetas está creada
- [ ] `python src/bronze/pipeline_bronze.py` corre sin errores
- [ ] El archivo Parquet fue creado en `data/bronze/events/date=.../`
- [ ] `python src/bronze/simulator.py` genera eventos nuevos cada N segundos
- [ ] El README documenta los pasos completados
- [ ] `.gitignore` incluye `venv/`, `.env`, `data/bronze/`, `*.parquet`

---

*Manual generado para el Reto 1 — Plataforma de Datos Fintech en Tiempo Real*
*Versión 1.0 — Abril 2026*
```

