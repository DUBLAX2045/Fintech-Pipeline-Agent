# 📘 Manual 3 — Cierre Completo: Silver, Gold y Entregables de Diseño
## Proyecto: Plataforma de Datos Fintech en Tiempo Real

> **Nivel:** Principiante con Fases 1 y 2 completadas
> **Objetivo:** Implementar Silver, Gold y cerrar todos los entregables pendientes
> **Última actualización:** Abril 2026

---

## 📋 Tabla de Contenidos

1. [Entregables de Diseño (Fase 1 pendiente)](#1-entregables-de-diseño-fase-1-pendiente)
   - 1.1 Esquema Bronze → Silver (documento de mapeo)
   - 1.2 Métricas Gold (diccionario formal)
   - 1.3 Las 10 preguntas del agente
2. [Notebook de Prueba de APIs](#2-notebook-de-prueba-de-apis)
3. [Pipeline Silver — Código Completo](#3-pipeline-silver--código-completo)
4. [Pipeline Gold — Código Completo](#4-pipeline-gold--código-completo)
5. [Script Principal que une todo](#5-script-principal-que-une-todo)
6. [Verificación Final del Proyecto](#6-verificación-final-del-proyecto)
7. [Actualización del README](#7-actualización-del-readme)

---

## 1. Entregables de Diseño (Fase 1 pendiente)

### 1.1 Esquema Bronze → Silver (Tabla de Mapeo)

Este documento define exactamente qué le pasa a cada campo cuando pasa de Bronze a Silver. Es el "contrato" del pipeline — cualquier persona del equipo puede leerlo y entender la transformación sin ver el código.

**Nota importante descubierta en el análisis del dataset real:**
- 577 de 2.000 registros NO tienen `amount` (eventos `USER_REGISTERED` y `USER_PROFILE_UPDATED` no tienen monto)
- 1.157 de 2.000 registros NO tienen `location` (eventos sin transacción)
- 2.000 de 2.000 IPs son privadas (192.168.x.x) → ip-api.com no puede resolverlas → usamos el campo `city` del payload como fuente de geolocalización

#### Tabla de Mapeo Completa

| Campo Bronze | Campo Silver | Tipo Bronze | Tipo Silver | Transformación | Nulos posibles |
|---|---|---|---|---|---|
| `event_id` | `event_id` | string | string | Sin cambio | No |
| `event` | `event` | string | string | Sin cambio | No |
| `event_status` | `event_status` | string | string | Sin cambio | No |
| _(calculado)_ | `is_failed` | — | bool | `event_status == 'FAILED'` | No |
| `user_id` | `user_id` | string | string | Sin cambio | No |
| `user_name` | `user_name` | string | string | Sin cambio | No |
| `user_age` | `user_age` | int | int | Sin cambio | No |
| `user_email` | `user_email` | string | string | Lowercase + strip | No |
| `user_segment` | `user_segment` | string | string | Sin cambio | No |
| `timestamp` | `timestamp` | string (ISO) | datetime (UTC) | `datetime.fromisoformat()` | No |
| _(calculado)_ | `date` | — | date | Extraído de `timestamp` | No |
| `account_id` | `account_id` | string | string | Sin cambio | Solo USER_PROFILE_UPDATED |
| `amount` | `amount_cop` | int | float | `float(amount)` | Sí — 577 registros |
| _(calculado)_ | `amount_usd` | — | float | `amount_cop * tasa_cop_usd` | Sí — cuando amount es null |
| `currency` | `currency` | string | string | Sin cambio | Sí — eventos sin transacción |
| `merchant` | `merchant` | string | string | Sin cambio | Sí — eventos sin transacción |
| `category` | `category` | string | string | Sin cambio | Sí — eventos sin transacción |
| `payment_method` | `payment_method` | string | string | Sin cambio | Sí — eventos sin transacción |
| `installments` | `installments` | int | int | `int(installments)` | Sí — eventos sin transacción |
| `balance_before` | `balance_before` | int | float | `float(balance_before)` | Sí |
| `balance_after` | `balance_after` | int | float | `float(balance_after)` | Sí |
| `initial_balance` | `initial_balance` | int | float | `float(initial_balance)` | Solo USER_REGISTERED |
| `user_city` | `city` | string | string | Fuente primaria de geolocalización | No |
| `location_city` | `location_city` | string | string | Fallback: igual a `city` si location es null | Sí |
| `location_country` | `location_country` | string | string | Default: 'Colombia' | No |
| `ip` | `ip` | string | string | Sin cambio | No |
| _(calculado)_ | `ip_is_private` | — | bool | `ip.startswith('192.168.')` | No |
| _(calculado)_ | `geo_source` | — | string | 'payload_location' o 'ip-api.com' | No |
| `device` | `device` | string | string | Sin cambio | No |
| `os` | `os` | string | string | Sin cambio | No |
| `channel` | `channel` | string | string | Sin cambio | No |
| `ingestion_timestamp` | `ingestion_timestamp` | string | datetime | `datetime.fromisoformat()` | No |
| `source_file` | `source_file` | string | string | Sin cambio | No |
| `batch_id` | `batch_id` | string | string | Sin cambio | No |
| `is_duplicate` | `is_duplicate` | bool | bool | Sin cambio | No |

#### Campos que NO pasan a Silver

| Campo Bronze | Razón |
|---|---|
| `source` | Redundante — siempre 'fintech.app' |
| `detailType` | Siempre 'event', sin valor analítico |
| `event_type` | Duplicado de `event` en minúsculas |
| `transaction_type` | Duplicado de `event_type` |
| `event_entity` | Siempre 'USER', sin variación |
| `event_version` | Siempre '1.0', sin variación |
| `updated_city` / `updated_segment` | Se procesan solo para USER_PROFILE_UPDATED en lógica separada |
| `money_source` / `account_status` | Campos muy específicos con muy baja cobertura |
| `ingestion_date` | Reemplazado por columna `date` derivada del timestamp del evento |

---

### 1.2 Métricas Gold — Diccionario Formal

La capa Gold tiene **una tabla principal** y **dos tablas de soporte**.

#### Tabla Principal: `gold_user_360`

Cada fila representa UN usuario con toda su información consolidada.

| Columna | Tipo | Descripción | Fuente | Nota |
|---|---|---|---|---|
| `user_id` | string | Identificador único del usuario | Silver | PK |
| `user_name` | string | Nombre del usuario | Silver | Primer valor registrado |
| `user_email` | string | Email del usuario | Silver | Primer valor registrado |
| `user_age` | int | Edad del usuario | Silver | Primer valor registrado |
| `user_segment` | string | Segmento: premium, student, family, young_professional | Silver | Primer valor registrado |
| `city` | string | Ciudad registrada del usuario | Silver | Primer valor registrado |
| `total_events` | int | Total de TODOS los eventos (incluye registros y actualizaciones) | Silver | COUNT(*) |
| `total_transactions` | int | Solo PAYMENT_MADE + PURCHASE_MADE + TRANSFER_SENT exitosos | Silver | COUNT donde is_failed=False y event IN tx_types |
| `total_amount_cop` | float | Suma de montos en COP (transacciones exitosas) | Silver | SUM(amount_cop) |
| `total_amount_usd` | float | Suma de montos en USD (transacciones exitosas) | Silver | SUM(amount_usd) |
| `avg_ticket` | float | Monto promedio por transacción en COP | Silver | MEAN(amount_cop) |
| `failed_transactions` | int | Total de eventos con eventStatus=FAILED | Silver | COUNT donde is_failed=True |
| `failure_rate` | float | Porcentaje de fallos sobre total de transacciones | Gold calculado | failed / (total_tx + failed) |
| `balance_current` | float | Último balanceAfter conocido | Silver | LAST(balance_after) ordenado por timestamp |
| `top_merchant` | string | Comercio con más transacciones para este usuario | Silver | MODE(merchant) |
| `top_category` | string | Categoría más frecuente | Silver | MODE(category) |
| `preferred_channel` | string | Canal más usado (app, web, api) | Silver | MODE(channel) |
| `preferred_device` | string | Dispositivo más usado (mobile, web) | Silver | MODE(device) |
| `last_transaction_date` | datetime | Fecha de la última transacción exitosa | Silver | MAX(timestamp) filtrado por tx_types |
| `last_event_date` | datetime | Fecha del último evento de cualquier tipo | Silver | MAX(timestamp) |
| `days_since_last_tx` | int | Días transcurridos desde la última transacción | Gold calculado | (hoy - last_transaction_date).days |

#### Tabla de Soporte 1: `gold_daily_metrics`

Métricas agregadas por día para tendencias temporales.

| Columna | Tipo | Descripción |
|---|---|---|
| `date` | date | Fecha del evento |
| `total_events` | int | Total de eventos ese día |
| `total_transactions` | int | Transacciones exitosas ese día |
| `total_amount_cop` | float | Volumen total en COP ese día |
| `failed_count` | int | Fallos ese día |
| `unique_users` | int | Usuarios únicos activos ese día |

#### Tabla de Soporte 2: `gold_event_summary`

Conteo de eventos por tipo para el dashboard de KPIs.

| Columna | Tipo | Descripción |
|---|---|---|
| `event` | string | Tipo de evento |
| `count` | int | Cantidad de ocurrencias |
| `success_count` | int | Eventos exitosos |
| `failed_count` | int | Eventos fallidos |
| `pct_of_total` | float | Porcentaje del total |

---

### 1.3 Las 10 Preguntas del Agente

Estas preguntas definen qué debe ser capaz de responder el agente inteligente de la Fase 3. Están ordenadas de menor a mayor complejidad para servir como test suite.

| # | Pregunta (lenguaje natural) | Tabla Gold consultada | Complejidad |
|---|---|---|---|
| 1 | ¿Cuántos usuarios únicos hay en la plataforma? | `gold_user_360` | Baja |
| 2 | ¿Cuál es el usuario con mayor gasto total en COP? | `gold_user_360` | Baja |
| 3 | ¿Cuánto ha gastado el usuario `user_99` en total? | `gold_user_360` | Baja |
| 4 | ¿Cuáles son los 5 merchants más populares por número de transacciones? | `gold_user_360` | Media |
| 5 | ¿Qué segmento de usuario tiene el mayor ticket promedio? | `gold_user_360` | Media |
| 6 | ¿Cuántos usuarios tienen más de 3 transacciones fallidas? | `gold_user_360` | Media |
| 7 | ¿Cuál es el canal preferido de los usuarios premium? | `gold_user_360` | Media |
| 8 | ¿Qué ciudad tiene el mayor volumen de transacciones en COP? | `gold_user_360` | Media-Alta |
| 9 | ¿Cuáles son los 3 usuarios más inactivos (más días sin transaccionar)? | `gold_user_360` | Alta |
| 10 | Dame un resumen financiero del usuario `user_210`: gasto total, ticket promedio, merchant favorito y canal preferido | `gold_user_360` | Alta |

---

## 2. Notebook de Prueba de APIs

Crea el archivo `notebooks/02_prueba_apis.py` (o ejecútalo como celdas en Jupyter).

### ⚠️ Nota crítica sobre las IPs del dataset

Antes de configurar ip-api.com, debes saber: **el 100% de las IPs en `fintech_events_v4.json` son privadas** (rango 192.168.x.x). ip-api.com no puede resolver IPs privadas — devuelve error o datos vacíos. La estrategia definida en el esquema es usar el campo `city` del payload como fuente primaria de geolocalización.

```python
# notebooks/02_prueba_apis.py
"""
Notebook de prueba de APIs externas.
Ejecuta cada sección de forma independiente para verificar conectividad.

APIs a probar:
    1. ExchangeRate API (open.er-api.com) — Tasas de cambio
    2. ip-api.com — Geolocalización por IP
    3. CoinGecko — Datos de mercado cripto (opcional)
"""

import requests
import json
import time
from datetime import datetime


# ═══════════════════════════════════════════════════════════════════════════
# SECCIÓN 1 — ExchangeRate API
# URL: https://open.er-api.com (sin API key en plan gratuito)
# Límite: 1500 requests/mes en plan gratuito
# ═══════════════════════════════════════════════════════════════════════════

def probar_exchangerate_api():
    """
    Prueba la API de tasas de cambio.
    Retorna el rate COP → USD si funciona correctamente.
    """
    print("=" * 50)
    print("PRUEBA 1: ExchangeRate API")
    print("=" * 50)
    
    # Opción A: open.er-api.com (gratuito, sin key)
    url_gratuita = "https://open.er-api.com/v6/latest/COP"
    
    # Opción B: exchangerate-api.com (requiere key gratuita en https://www.exchangerate-api.com)
    # API_KEY = "tu_clave_aqui"  # Obtener en exchangerate-api.com
    # url_con_key = f"https://v6.exchangerate-api.com/v6/{API_KEY}/latest/COP"
    
    try:
        print(f"Probando: {url_gratuita}")
        response = requests.get(url_gratuita, timeout=10)
        response.raise_for_status()
        
        data = response.json()
        print(f"✅ Estado: {data.get('result', 'unknown')}")
        print(f"✅ Actualizado: {data.get('time_last_update_utc', 'N/A')}")
        
        rates = data.get("rates", {})
        cop_usd = rates.get("USD")
        cop_eur = rates.get("EUR")
        cop_brl = rates.get("BRL")
        
        print(f"\n📊 Tasas de COP hacia:")
        print(f"   1 COP = {cop_usd:.8f} USD")
        print(f"   1 COP = {cop_eur:.8f} EUR")
        print(f"   1 COP = {cop_brl:.8f} BRL")
        
        # Verificar con un monto real del dataset
        monto_cop = 360088
        print(f"\n💡 Conversión de ejemplo:")
        print(f"   {monto_cop:,} COP = {monto_cop * cop_usd:.2f} USD")
        
        return cop_usd  # Retorna la tasa para usar en el pipeline
    
    except requests.exceptions.ConnectionError:
        print("❌ Sin conexión a internet")
        print("   → Usando tasa de respaldo: 1 USD = 4,150 COP (Abril 2026)")
        return 1 / 4150  # Tasa de respaldo
    
    except requests.exceptions.HTTPError as e:
        print(f"❌ Error HTTP: {e}")
        print("   → Posible solución: registrarse en exchangerate-api.com para obtener key")
        return 1 / 4150
    
    except Exception as e:
        print(f"❌ Error inesperado: {e}")
        return 1 / 4150


# ═══════════════════════════════════════════════════════════════════════════
# SECCIÓN 2 — ip-api.com (Geolocalización por IP)
# ═══════════════════════════════════════════════════════════════════════════

def probar_ip_api():
    """
    Prueba ip-api.com con diferentes tipos de IP.
    
    RESULTADO ESPERADO para este proyecto:
    - IPs públicas (ej: 190.x.x.x) → datos completos ✅
    - IPs privadas (192.168.x.x) → error o datos vacíos ❌
    → Estrategia: usar campo 'city' del payload como fallback
    """
    print("\n" + "=" * 50)
    print("PRUEBA 2: ip-api.com")
    print("=" * 50)
    
    # Probar con IP pública colombiana
    ip_publica = "190.26.232.1"   # IP pública (ejemplo Colombia)
    ip_privada = "192.168.60.177" # IP del dataset (privada)
    
    for ip, tipo in [(ip_publica, "PÚBLICA"), (ip_privada, "PRIVADA (dataset)")]:
        url = f"http://ip-api.com/json/{ip}?fields=status,city,country,regionName,timezone"
        print(f"\nProbando IP {tipo}: {ip}")
        
        try:
            # ip-api.com permite 45 req/min en plan gratuito
            time.sleep(1)
            response = requests.get(url, timeout=10)
            data = response.json()
            
            if "error" in data:
                print(f"   ❌ Error ip-api.com: {data.get('reason', 'unknown')}")
                print(f"   → Confirmado: IPs privadas no son resolvibles")
                print(f"   → Estrategia aplicada: usar campo 'city' del payload")
            else:
                print(f"   ✅ País: {data.get('country_name')}")
                print(f"   ✅ Ciudad: {data.get('city')}")
                print(f"   ✅ Región: {data.get('region')}")
                print(f"   ✅ Timezone: {data.get('timezone')}")
        
        except requests.exceptions.ConnectionError:
            print(f"   ❌ Sin conexión a internet")
        except Exception as e:
            print(f"   ❌ Error: {e}")
    
    print("\n📋 Conclusión para el pipeline Silver:")
    print("   - ip_is_private = True para TODOS los registros del dataset")
    print("   - geo_source = 'payload_location' (usando campo city del payload)")
    print("   - ip-api.com se activará SOLO si llegan IPs públicas desde el ecommerce")


# ═══════════════════════════════════════════════════════════════════════════
# SECCIÓN 3 — CoinGecko (Opcional)
# ═══════════════════════════════════════════════════════════════════════════

def probar_coingecko():
    """
    Prueba CoinGecko API para precios de criptomonedas.
    Esta API no requiere key en el plan gratuito.
    """
    print("\n" + "=" * 50)
    print("PRUEBA 3: CoinGecko API (Opcional)")
    print("=" * 50)
    
    url = "https://api.coingecko.com/api/v3/simple/price"
    params = {
        "ids": "bitcoin,ethereum,usd-coin",
        "vs_currencies": "cop,usd",
        "include_24hr_change": "true"
    }
    
    try:
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()
        
        print("✅ CoinGecko responde correctamente")
        for coin, prices in data.items():
            cop_price = prices.get("cop", "N/A")
            usd_price = prices.get("usd", "N/A")
            change = prices.get("cop_24h_change", "N/A")
            if isinstance(cop_price, (int, float)):
                print(f"   {coin.upper()}: {cop_price:,.0f} COP | ${usd_price:,.2f} USD | 24h: {change:.2f}%")
    
    except requests.exceptions.ConnectionError:
        print("❌ Sin conexión — CoinGecko no disponible")
    except Exception as e:
        print(f"❌ Error: {e}")


# ═══════════════════════════════════════════════════════════════════════════
# SECCIÓN 4 — Módulo de Tasa de Cambio (para el pipeline)
# ═══════════════════════════════════════════════════════════════════════════

class ExchangeRateService:
    """
    Servicio de tasas de cambio con caché local.
    
    Diseño: intentar la API, y si falla, usar tasa de respaldo.
    La tasa se cachea en memoria por 1 hora para no hacer una
    llamada API por cada registro del dataset.
    """
    
    # Tasa de respaldo (actualizar manualmente si no hay API)
    # 1 COP = X USD (Abril 2026: 1 USD ≈ 4,150 COP)
    TASA_RESPALDO_COP_USD = 1 / 4150
    
    def __init__(self):
        self._cache = {}           # {moneda: tasa}
        self._cache_timestamp = None
        self._cache_ttl = 3600     # 1 hora en segundos
        self._usando_respaldo = False
    
    def obtener_tasa_cop_usd(self) -> float:
        """
        Obtiene la tasa COP → USD.
        Primero intenta la API, luego usa la tasa de respaldo.
        """
        # Verificar si el caché es válido
        if self._cache and self._cache_timestamp:
            elapsed = time.time() - self._cache_timestamp
            if elapsed < self._cache_ttl:
                return self._cache.get("USD_desde_COP", self.TASA_RESPALDO_COP_USD)
        
        # Intentar la API
        try:
            response = requests.get(
                "https://open.er-api.com/v6/latest/COP",
                timeout=8
            )
            response.raise_for_status()
            data = response.json()
            tasa = data["rates"]["USD"]
            
            # Guardar en caché
            self._cache["USD_desde_COP"] = tasa
            self._cache_timestamp = time.time()
            self._usando_respaldo = False
            print(f"✅ [ExchangeRate] Tasa actualizada: 1 COP = {tasa:.8f} USD")
            return tasa
        
        except Exception:
            # Usar tasa de respaldo sin interrumpir el pipeline
            self._usando_respaldo = True
            print(f"⚠️  [ExchangeRate] API no disponible. "
                  f"Usando tasa de respaldo: 1 COP = {self.TASA_RESPALDO_COP_USD:.8f} USD")
            return self.TASA_RESPALDO_COP_USD
    
    def convertir_cop_a_usd(self, monto_cop: float) -> float:
        """Convierte un monto de COP a USD."""
        tasa = self.obtener_tasa_cop_usd()
        return round(monto_cop * tasa, 4)
    
    @property
    def usando_tasa_respaldo(self) -> bool:
        return self._usando_respaldo


# ── Ejecutar todas las pruebas ──────────────────────────────────────────────
if __name__ == "__main__":
    print("🔬 PRUEBA COMPLETA DE APIs EXTERNAS")
    print(f"   Fecha: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
    
    tasa = probar_exchangerate_api()
    probar_ip_api()
    probar_coingecko()
    
    print("\n" + "=" * 50)
    print("📋 RESUMEN PARA EL PIPELINE")
    print("=" * 50)
    print(f"   Tasa COP→USD a usar: {tasa:.8f}")
    print(f"   Geolocalización: campo 'city' del payload (IPs privadas)")
    print(f"   CoinGecko: opcional, solo para segmento 'crypto'")
    print("\n✅ Notebook de APIs completado")
```

**Para ejecutar:**
```bash
python notebooks/02_prueba_apis.py
```

---

## 3. Pipeline Silver — Código Completo

Crea el archivo `src/silver/pipeline_silver.py`:

```python
# src/silver/pipeline_silver.py
"""
Pipeline de la Capa Silver.

Transforma los datos Bronze en datos limpios, tipados y enriquecidos.

Reglas aplicadas:
    - Parsear timestamp a datetime con timezone UTC
    - Convertir amount a float (estaba como int en el JSON)
    - Agregar amount_usd usando ExchangeRate API (con fallback)
    - Resolver geolocalización: payload.city es fuente primaria
      (100% de IPs son privadas en el dataset)
    - Marcar registros fallidos con is_failed = True
    - Eliminar columnas redundantes de Bronze
    - Guardar en Parquet particionado por fecha del evento

Uso:
    python src/silver/pipeline_silver.py
"""

import os
import sys
import glob
import time
import requests
import pandas as pd
from datetime import datetime, timezone
from pathlib import Path

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))


# ═══════════════════════════════════════════════════════════════════════════
# CONFIGURACIÓN
# ═══════════════════════════════════════════════════════════════════════════

# Tasa de respaldo COP→USD (actualizar si hay cambios grandes)
# Abril 2026: 1 USD ≈ 4,150 COP
TASA_RESPALDO_COP_USD = 1 / 4150

# Tipos de eventos que representan transacciones financieras reales
# (excluye registros, actualizaciones de perfil, etc.)
EVENTOS_TRANSACCIONALES = {
    "PAYMENT_MADE",
    "PURCHASE_MADE",
    "TRANSFER_SENT",
    "MONEY_ADDED",
    "PAYMENT_FAILED",
}

# Columnas de Bronze que NO pasan a Silver (redundantes o sin valor analítico)
COLUMNAS_A_ELIMINAR = [
    "source",
    "detailType",
    "event_type",
    "transaction_type",
    "event_entity",
    "event_version",
    "account_status",
    "money_source",
    "updated_city",
    "updated_segment",
    "ingestion_date",      # Reemplazado por 'date' derivado del timestamp
]


# ═══════════════════════════════════════════════════════════════════════════
# SERVICIO DE TASAS DE CAMBIO
# ═══════════════════════════════════════════════════════════════════════════

class ExchangeRateService:
    """
    Obtiene la tasa COP→USD con caché de 1 hora.
    Si la API falla, usa la tasa de respaldo sin interrumpir el pipeline.
    """
    
    def __init__(self):
        self._tasa: float = None
        self._ts_cache: float = None
        self._ttl: int = 3600   # 1 hora
    
    def tasa_cop_usd(self) -> float:
        """Retorna la tasa COP→USD actual."""
        # Caché válido?
        if self._tasa and self._ts_cache:
            if (time.time() - self._ts_cache) < self._ttl:
                return self._tasa
        
        # Intentar API
        try:
            r = requests.get(
                "https://open.er-api.com/v6/latest/COP",
                timeout=8
            )
            r.raise_for_status()
            tasa = r.json()["rates"]["USD"]
            self._tasa = tasa
            self._ts_cache = time.time()
            print(f"   ✅ [ExchangeRate] 1 COP = {tasa:.8f} USD (API)")
            return tasa
        except Exception:
            print(f"   ⚠️  [ExchangeRate] API no disponible → tasa de respaldo: "
                  f"1 COP = {TASA_RESPALDO_COP_USD:.8f} USD")
            return TASA_RESPALDO_COP_USD
    
    def convertir(self, monto_cop: float) -> float:
        return round(monto_cop * self.tasa_cop_usd(), 4)


# Instancia global (se reutiliza el caché en todo el pipeline)
fx = ExchangeRateService()


# ═══════════════════════════════════════════════════════════════════════════
# TRANSFORMACIONES PASO A PASO
# ═══════════════════════════════════════════════════════════════════════════

def paso1_leer_bronze(carpeta_bronze: str) -> pd.DataFrame:
    """
    Lee todos los archivos Parquet de Bronze en un solo DataFrame.
    
    Args:
        carpeta_bronze: Ruta a data/bronze/events/
    
    Returns:
        DataFrame con todos los eventos de Bronze concatenados
    """
    patron = os.path.join(carpeta_bronze, "**", "*.parquet")
    archivos = glob.glob(patron, recursive=True)
    
    if not archivos:
        raise FileNotFoundError(
            f"No se encontraron archivos Parquet en {carpeta_bronze}\n"
            f"Ejecuta primero: python src/bronze/pipeline_bronze.py"
        )
    
    print(f"   📂 Leyendo {len(archivos)} archivo(s) Parquet de Bronze...")
    df = pd.concat([pd.read_parquet(f) for f in archivos], ignore_index=True)
    print(f"   ✅ {len(df):,} registros cargados de Bronze")
    return df


def paso2_limpiar_tipos(df: pd.DataFrame) -> pd.DataFrame:
    """
    Convierte campos al tipo de dato correcto.
    
    Transformaciones:
        - timestamp: string ISO → datetime con timezone UTC
        - amount: int → float (permite valores decimales futuros)
        - balance_before/after: int → float
        - installments: object → int (con manejo de nulos)
        - user_email: string → lowercase + strip
        - date: extraído del timestamp (para particionado)
    """
    df = df.copy()
    
    # timestamp → datetime UTC
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
    
    # Extraer columna de fecha (para particionado y consultas por día)
    df["date"] = df["timestamp"].dt.date
    
    # Cantidades monetarias → float
    for col in ["amount", "balance_before", "balance_after", "initial_balance"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").astype("float64")
    
    # installments → int (rellenar nulos con 1 — pago único)
    if "installments" in df.columns:
        df["installments"] = (
            pd.to_numeric(df["installments"], errors="coerce")
            .fillna(1)
            .astype("Int64")   # Int64 (con mayúscula) soporta nulos
        )
    
    # email → lowercase + strip espacios
    if "user_email" in df.columns:
        df["user_email"] = df["user_email"].str.lower().str.strip()
    
    print(f"   ✅ Tipos de datos corregidos")
    return df


def paso3_agregar_flags(df: pd.DataFrame) -> pd.DataFrame:
    """
    Agrega columnas calculadas (flags) que facilitan el análisis.
    
    Columnas nuevas:
        - is_failed: True si el evento terminó en FAILED
        - is_transactional: True si el evento mueve dinero
        - ip_is_private: True si la IP es del rango privado
        - geo_source: 'payload_location' o 'ip-api.com' según origen de geo
    """
    df = df.copy()
    
    # Flag de fallo
    df["is_failed"] = df["event_status"] == "FAILED"
    
    # Flag de transaccionalidad
    df["is_transactional"] = df["event"].isin(EVENTOS_TRANSACCIONALES)
    
    # Flag de IP privada
    # Para este dataset: 100% son privadas (192.168.x.x)
    # Para eventos del ecommerce pueden llegar IPs públicas
    df["ip_is_private"] = df["ip"].fillna("").str.startswith(
        ("192.168.", "10.", "172.16.", "172.17.", "172.18.",
         "172.19.", "172.20.", "172.21.", "172.22.", "172.23.",
         "172.24.", "172.25.", "172.26.", "172.27.", "172.28.",
         "172.29.", "172.30.", "172.31.")
    )
    
    # Fuente de geolocalización
    df["geo_source"] = df["ip_is_private"].map(
        {True: "payload_location", False: "ip-api.com"}
    )
    
    print(f"   ✅ Flags agregados:")
    print(f"      is_failed: {df['is_failed'].sum()} registros")
    print(f"      is_transactional: {df['is_transactional'].sum()} registros")
    print(f"      ip_is_private: {df['ip_is_private'].sum()} / {len(df)} registros")
    return df


def paso4_enriquecer_geolocalización(df: pd.DataFrame) -> pd.DataFrame:
    """
    Resuelve la geolocalización de cada evento.
    
    Estrategia (en orden de prioridad):
        1. Si IP es pública → llamar a ip-api.com
        2. Si IP es privada (caso del dataset actual) → usar payload.location_city
        3. Si location_city es null → usar user_city (campo city del payload)
        4. Fallback final → 'Colombia' para country, 'Desconocida' para city
    
    Para el dataset actual: 100% caerá en el caso 2 o 3.
    Para eventos del ecommerce con IPs públicas: caerá en caso 1.
    """
    df = df.copy()
    
    # Resolver location_city
    # Jerarquía: location_city → city (user_city) → 'Desconocida'
    df["location_city"] = (
        df["location_city"]
        .fillna(df["city"])
        .fillna("Desconocida")
    )
    
    # Resolver location_country
    df["location_country"] = df["location_country"].fillna("Colombia")
    
    # Para IPs públicas (del ecommerce simulado): llamar a ip-api.com
    ips_publicas = df[~df["ip_is_private"] & df["ip"].notna()]["ip"].unique()
    
    if len(ips_publicas) > 0:
        print(f"   🌐 Resolviendo {len(ips_publicas)} IPs públicas con ip-api.com...")
        cache_ipapi = {}
        
        for ip in ips_publicas:
            if ip in cache_ipapi:
                continue
            try:
                time.sleep(0.5)  # Rate limit: 45 req/min en plan gratuito
                r = requests.get(f"http://ip-api.com/json/{ip}?fields=status,city,country,regionName,timezone", timeout=6)
                data = r.json()
                if "error" not in data:
                    cache_ipapi[ip] = {
                        "city": data.get("city", ""),
                        "country": data.get("country_name", "")
                    }
            except Exception:
                pass
        
        # Aplicar los resultados de ip-api.com donde aplique
        for ip, geo in cache_ipapi.items():
            mask = (df["ip"] == ip) & (~df["ip_is_private"])
            if geo["city"]:
                df.loc[mask, "location_city"] = geo["city"]
            if geo["country"]:
                df.loc[mask, "location_country"] = geo["country"]
    else:
        print(f"   📍 Geolocalización: usando payload.city "
              f"(todas las IPs son privadas)")
    
    return df


def paso5_enriquecer_moneda(df: pd.DataFrame) -> pd.DataFrame:
    """
    Agrega la columna amount_usd convirtiendo desde COP.
    
    Solo aplica a registros que tienen amount (1,423 de 2,000).
    Los eventos sin monto (USER_REGISTERED, USER_PROFILE_UPDATED)
    quedrán con amount_usd = null, lo cual es correcto.
    """
    df = df.copy()
    
    # Obtener tasa una sola vez (el servicio la cachea)
    tasa = fx.tasa_cop_usd()
    
    # Calcular amount_usd solo donde existe amount
    df["amount_usd"] = df["amount"].apply(
        lambda x: round(x * tasa, 4) if pd.notna(x) else None
    )
    
    registros_convertidos = df["amount_usd"].notna().sum()
    print(f"   ✅ amount_usd calculado para {registros_convertidos:,} registros")
    print(f"      Tasa usada: 1 COP = {tasa:.8f} USD")
    return df


def paso6_renombrar_y_seleccionar_columnas(df: pd.DataFrame) -> pd.DataFrame:
    """
    Renombra columnas para consistencia y elimina las redundantes.
    
    Bronze usa nombres como 'amount' (ambiguo).
    Silver usa 'amount_cop' (explícito sobre la moneda).
    """
    df = df.copy()
    
    # Renombrar para mayor claridad
    renombres = {
        "amount": "amount_cop",          # Explícito: COP
        "user_id": "user_id",            # Sin cambio
        "is_duplicate": "bronze_is_duplicate",  # Preservar para auditoría
    }
    df = df.rename(columns=renombres)
    
    # Eliminar columnas redundantes de Bronze
    columnas_a_eliminar = [c for c in COLUMNAS_A_ELIMINAR if c in df.columns]
    df = df.drop(columns=columnas_a_eliminar)
    
    print(f"   ✅ Columnas Silver finales: {len(df.columns)}")
    return df


def paso7_guardar_silver(df: pd.DataFrame, carpeta_silver: str) -> str:
    """
    Guarda el DataFrame Silver en Parquet particionado por fecha del evento.
    
    Nota: Silver se particiona por la fecha DEL EVENTO (no de ingesta).
    Esto permite consultar Silver por períodos de negocio de forma eficiente.
    """
    os.makedirs(carpeta_silver, exist_ok=True)
    
    # Guardar como un solo archivo Silver (para este proyecto)
    # En producción con millones de registros, se particionaría por date
    ruta = os.path.join(carpeta_silver, "silver_events.parquet")
    
    df.to_parquet(ruta, index=False, compression="snappy", engine="pyarrow")
    
    tamano_mb = os.path.getsize(ruta) / (1024 * 1024)
    print(f"   ✅ Silver guardado: {ruta}")
    print(f"      Filas: {len(df):,} | Tamaño: {tamano_mb:.2f} MB")
    return ruta


# ═══════════════════════════════════════════════════════════════════════════
# PIPELINE PRINCIPAL
# ═══════════════════════════════════════════════════════════════════════════

def ejecutar_pipeline_silver(
    carpeta_bronze: str = "data/bronze/events",
    carpeta_silver: str = "data/silver"
) -> pd.DataFrame:
    """
    Ejecuta el pipeline completo Bronze → Silver.
    
    Returns:
        DataFrame Silver listo para ser consumido por el pipeline Gold
    """
    print("=" * 60)
    print("⚗️  INICIANDO PIPELINE — CAPA SILVER")
    print("=" * 60)
    
    print("\n📌 PASO 1: Leyendo datos de Bronze...")
    df = paso1_leer_bronze(carpeta_bronze)
    
    print("\n📌 PASO 2: Limpiando tipos de datos...")
    df = paso2_limpiar_tipos(df)
    
    print("\n📌 PASO 3: Agregando flags...")
    df = paso3_agregar_flags(df)
    
    print("\n📌 PASO 4: Resolviendo geolocalización...")
    df = paso4_enriquecer_geolocalización(df)
    
    print("\n📌 PASO 5: Convirtiendo monedas...")
    df = paso5_enriquecer_moneda(df)
    
    print("\n📌 PASO 6: Seleccionando columnas finales...")
    df = paso6_renombrar_y_seleccionar_columnas(df)
    
    print("\n📌 PASO 7: Guardando Silver en Parquet...")
    paso7_guardar_silver(df, carpeta_silver)
    
    # Estadísticas finales
    print("\n" + "=" * 60)
    print("✅ PIPELINE SILVER COMPLETADO")
    print(f"   Registros totales:    {len(df):,}")
    print(f"   Con monto (COP/USD):  {df['amount_cop'].notna().sum():,}")
    print(f"   Eventos fallidos:     {df['is_failed'].sum():,}")
    print(f"   Columnas finales:     {len(df.columns)}")
    print(f"   Usuarios únicos:      {df['user_id'].nunique():,}")
    print("=" * 60)
    
    return df


if __name__ == "__main__":
    ejecutar_pipeline_silver()
```

---

## 4. Pipeline Gold — Código Completo

Crea el archivo `src/gold/pipeline_gold.py`:

```python
# src/gold/pipeline_gold.py
"""
Pipeline de la Capa Gold.

Construye la tabla gold_user_360 y las tablas de soporte
a partir de los datos Silver.

Tablas generadas:
    - gold_user_360.parquet      → Visión 360 por usuario (tabla principal)
    - gold_daily_metrics.parquet → Métricas agregadas por día
    - gold_event_summary.parquet → Resumen por tipo de evento

Uso:
    python src/gold/pipeline_gold.py
    (Requiere que Silver esté generado primero)
"""

import os
import sys
import pandas as pd
import numpy as np
from datetime import datetime, date

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))


# ═══════════════════════════════════════════════════════════════════════════
# CONSTANTES
# ═══════════════════════════════════════════════════════════════════════════

# Eventos que representan transacciones financieras reales (mueven dinero)
EVENTOS_TRANSACCIONALES = ["PAYMENT_MADE", "PURCHASE_MADE", "TRANSFER_SENT"]

# Fecha de referencia para calcular "días desde última transacción"
HOY = datetime.now().date()


# ═══════════════════════════════════════════════════════════════════════════
# LECTURA DE SILVER
# ═══════════════════════════════════════════════════════════════════════════

def leer_silver(carpeta_silver: str = "data/silver") -> pd.DataFrame:
    """Lee el archivo Silver y lo retorna como DataFrame."""
    ruta = os.path.join(carpeta_silver, "silver_events.parquet")
    
    if not os.path.exists(ruta):
        raise FileNotFoundError(
            f"No se encontró Silver en {ruta}\n"
            f"Ejecuta primero: python src/silver/pipeline_silver.py"
        )
    
    df = pd.read_parquet(ruta)
    print(f"   ✅ Silver leído: {len(df):,} registros, {df['user_id'].nunique()} usuarios")
    return df


# ═══════════════════════════════════════════════════════════════════════════
# TABLA 1 — gold_user_360
# ═══════════════════════════════════════════════════════════════════════════

def construir_user_360(silver: pd.DataFrame) -> pd.DataFrame:
    """
    Construye la tabla principal gold_user_360.
    Una fila = un usuario con toda su información consolidada.
    
    Proceso:
        1. Datos demográficos (primer registro del usuario)
        2. Métricas de todos los eventos
        3. Métricas de transacciones exitosas (solo PAYMENT, PURCHASE, TRANSFER)
        4. Top merchant, categoría y canal
        5. Columnas calculadas (failure_rate, days_since_last_tx)
    """
    
    # ── 1. Datos demográficos del usuario ──────────────────────────────────
    demograficos = (
        silver
        .sort_values("timestamp")            # Ordenar por tiempo
        .groupby("user_id")
        .agg(
            user_name=("user_name", "first"),
            user_email=("user_email", "first"),
            user_age=("user_age", "first"),
            user_segment=("user_segment", "first"),
            city=("city", "first"),
            total_events=("event_id", "count"),
            failed_transactions=("is_failed", "sum"),
            last_event_date=("timestamp", "max"),
        )
        .reset_index()
    )
    
    # ── 2. Métricas de transacciones exitosas ──────────────────────────────
    # Solo PAYMENT_MADE, PURCHASE_MADE, TRANSFER_SENT (no MONEY_ADDED ni FAILED)
    silver_tx = silver[
        silver["event"].isin(EVENTOS_TRANSACCIONALES) &
        ~silver["is_failed"]
    ].copy()
    
    if len(silver_tx) == 0:
        print("   ⚠️  No hay transacciones exitosas para calcular métricas Gold")
        metricas_tx = pd.DataFrame(columns=[
            "user_id", "total_transactions", "total_amount_cop",
            "total_amount_usd", "avg_ticket", "balance_current",
            "last_transaction_date"
        ])
    else:
        # Ordenar para que 'last' en balance_current sea el más reciente
        silver_tx = silver_tx.sort_values("timestamp")
        
        metricas_tx = (
            silver_tx
            .groupby("user_id")
            .agg(
                total_transactions=("event_id", "count"),
                total_amount_cop=("amount_cop", "sum"),
                total_amount_usd=("amount_usd", "sum"),
                avg_ticket=("amount_cop", "mean"),
                balance_current=("balance_after", "last"),
                last_transaction_date=("timestamp", "max"),
            )
            .reset_index()
        )
        
        # Redondear columnas monetarias
        for col in ["total_amount_cop", "total_amount_usd", "avg_ticket", "balance_current"]:
            metricas_tx[col] = metricas_tx[col].round(2)
    
    # ── 3. Top merchant por usuario ────────────────────────────────────────
    top_merchant = (
        silver_tx
        .dropna(subset=["merchant"])
        .groupby("user_id")["merchant"]
        .agg(lambda x: x.value_counts().index[0] if len(x) > 0 else None)
        .reset_index()
        .rename(columns={"merchant": "top_merchant"})
    )
    
    # ── 4. Top categoría por usuario ───────────────────────────────────────
    top_categoria = (
        silver_tx
        .dropna(subset=["category"])
        .groupby("user_id")["category"]
        .agg(lambda x: x.value_counts().index[0] if len(x) > 0 else None)
        .reset_index()
        .rename(columns={"category": "top_category"})
    )
    
    # ── 5. Canal y dispositivo preferido (sobre TODOS los eventos) ─────────
    canal_preferido = (
        silver
        .dropna(subset=["channel"])
        .groupby("user_id")["channel"]
        .agg(lambda x: x.value_counts().index[0])
        .reset_index()
        .rename(columns={"channel": "preferred_channel"})
    )
    
    dispositivo_preferido = (
        silver
        .dropna(subset=["device"])
        .groupby("user_id")["device"]
        .agg(lambda x: x.value_counts().index[0])
        .reset_index()
        .rename(columns={"device": "preferred_device"})
    )
    
    # ── 6. Unir todo ───────────────────────────────────────────────────────
    gold = demograficos.copy()
    gold = gold.merge(metricas_tx,         on="user_id", how="left")
    gold = gold.merge(top_merchant,        on="user_id", how="left")
    gold = gold.merge(top_categoria,       on="user_id", how="left")
    gold = gold.merge(canal_preferido,     on="user_id", how="left")
    gold = gold.merge(dispositivo_preferido, on="user_id", how="left")
    
    # ── 7. Columnas calculadas ─────────────────────────────────────────────
    
    # Tasa de fallo = fallos / (transacciones exitosas + fallos)
    gold["total_transactions"] = gold["total_transactions"].fillna(0)
    gold["failure_rate"] = (
        gold["failed_transactions"] /
        (gold["total_transactions"] + gold["failed_transactions"])
    ).round(4)
    gold["failure_rate"] = gold["failure_rate"].fillna(0.0)
    
    # Días desde la última transacción
    def calcular_dias(last_tx):
        if pd.isna(last_tx):
            return None
        if hasattr(last_tx, "date"):
            last_tx = last_tx.date()
        try:
            return (HOY - last_tx).days
        except Exception:
            return None
    
    gold["days_since_last_tx"] = gold["last_transaction_date"].apply(calcular_dias)
    
    # ── 8. Ordenar columnas en el orden lógico definido en el diccionario ──
    orden_columnas = [
        "user_id", "user_name", "user_email", "user_age", "user_segment", "city",
        "total_events", "total_transactions", "total_amount_cop", "total_amount_usd",
        "avg_ticket", "failed_transactions", "failure_rate",
        "balance_current", "top_merchant", "top_category",
        "preferred_channel", "preferred_device",
        "last_transaction_date", "last_event_date", "days_since_last_tx"
    ]
    columnas_existentes = [c for c in orden_columnas if c in gold.columns]
    gold = gold[columnas_existentes]
    
    print(f"   ✅ gold_user_360: {len(gold):,} usuarios | {len(gold.columns)} columnas")
    return gold


# ═══════════════════════════════════════════════════════════════════════════
# TABLA 2 — gold_daily_metrics
# ═══════════════════════════════════════════════════════════════════════════

def construir_daily_metrics(silver: pd.DataFrame) -> pd.DataFrame:
    """
    Métricas agregadas por día para el dashboard de tendencias.
    """
    daily = (
        silver
        .groupby("date")
        .agg(
            total_events=("event_id", "count"),
            total_transactions=("is_transactional", "sum"),
            total_amount_cop=("amount_cop", "sum"),
            failed_count=("is_failed", "sum"),
            unique_users=("user_id", "nunique"),
        )
        .reset_index()
    )
    
    daily["total_amount_cop"] = daily["total_amount_cop"].round(2)
    daily = daily.sort_values("date").reset_index(drop=True)
    
    print(f"   ✅ gold_daily_metrics: {len(daily)} días")
    return daily


# ═══════════════════════════════════════════════════════════════════════════
# TABLA 3 — gold_event_summary
# ═══════════════════════════════════════════════════════════════════════════

def construir_event_summary(silver: pd.DataFrame) -> pd.DataFrame:
    """
    Resumen por tipo de evento para los KPIs del dashboard principal.
    """
    summary = (
        silver
        .groupby("event")
        .agg(
            count=("event_id", "count"),
            success_count=("is_failed", lambda x: (~x).sum()),
            failed_count=("is_failed", "sum"),
        )
        .reset_index()
    )
    
    total = summary["count"].sum()
    summary["pct_of_total"] = (summary["count"] / total * 100).round(2)
    summary = summary.sort_values("count", ascending=False).reset_index(drop=True)
    
    print(f"   ✅ gold_event_summary: {len(summary)} tipos de evento")
    return summary


# ═══════════════════════════════════════════════════════════════════════════
# GUARDAR TABLAS GOLD
# ═══════════════════════════════════════════════════════════════════════════

def guardar_gold(
    user_360: pd.DataFrame,
    daily: pd.DataFrame,
    summary: pd.DataFrame,
    carpeta_gold: str = "data/gold"
) -> dict:
    """Guarda las tres tablas Gold en formato Parquet."""
    os.makedirs(carpeta_gold, exist_ok=True)
    
    rutas = {}
    tablas = {
        "gold_user_360.parquet":      user_360,
        "gold_daily_metrics.parquet": daily,
        "gold_event_summary.parquet": summary,
    }
    
    for nombre, df in tablas.items():
        ruta = os.path.join(carpeta_gold, nombre)
        df.to_parquet(ruta, index=False, compression="snappy", engine="pyarrow")
        tamano_kb = os.path.getsize(ruta) / 1024
        print(f"   ✅ {nombre}: {len(df):,} filas | {tamano_kb:.1f} KB")
        rutas[nombre] = ruta
    
    return rutas


# ═══════════════════════════════════════════════════════════════════════════
# PIPELINE PRINCIPAL
# ═══════════════════════════════════════════════════════════════════════════

def ejecutar_pipeline_gold(
    carpeta_silver: str = "data/silver",
    carpeta_gold: str = "data/gold"
) -> dict:
    """
    Ejecuta el pipeline completo Silver → Gold.
    
    Returns:
        Diccionario con las rutas de los archivos Gold generados
    """
    print("=" * 60)
    print("🥇 INICIANDO PIPELINE — CAPA GOLD")
    print("=" * 60)
    
    print("\n📌 PASO 1: Leyendo Silver...")
    silver = leer_silver(carpeta_silver)
    
    print("\n📌 PASO 2: Construyendo gold_user_360...")
    user_360 = construir_user_360(silver)
    
    print("\n📌 PASO 3: Construyendo gold_daily_metrics...")
    daily = construir_daily_metrics(silver)
    
    print("\n📌 PASO 4: Construyendo gold_event_summary...")
    summary = construir_event_summary(silver)
    
    print("\n📌 PASO 5: Guardando tablas Gold...")
    rutas = guardar_gold(user_360, daily, summary, carpeta_gold)
    
    # Resumen con métricas reales del dataset
    print("\n" + "=" * 60)
    print("✅ PIPELINE GOLD COMPLETADO")
    print(f"   Usuarios en gold_user_360:  {len(user_360):,}")
    top_user = user_360.nlargest(1, "total_amount_cop").iloc[0]
    print(f"   Usuario con más gasto:       {top_user['user_id']} "
          f"({top_user['total_amount_cop']:,.0f} COP)")
    print(f"   Ticket promedio global:      "
          f"{user_360['avg_ticket'].mean():,.0f} COP")
    print(f"   Usuarios con fallos > 3:     "
          f"{(user_360['failed_transactions'] > 3).sum()}")
    print("=" * 60)
    
    return {"user_360": user_360, "daily": daily, "summary": summary, "rutas": rutas}


if __name__ == "__main__":
    ejecutar_pipeline_gold()
```

---

## 5. Script Principal que une todo

Crea `src/run_pipeline.py` — este es el script que ejecutas cuando quieres correr todo el pipeline de principio a fin:

```python
# src/run_pipeline.py
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
```

---

## 6. Verificación Final del Proyecto

Crea `verificar_pipeline_completo.py` en la raíz del proyecto:

```python
# verificar_pipeline_completo.py
"""
Verifica que todas las capas del pipeline están correctas.
Ejecutar después de src/run_pipeline.py.

Uso:
    python verificar_pipeline_completo.py
"""

import pandas as pd
import os

print("=" * 60)
print("🔍 VERIFICACIÓN COMPLETA DEL PIPELINE")
print("=" * 60)

errores = []

# ── BRONZE ──────────────────────────────────────────────────────────────
import glob
bronze_files = glob.glob("data/bronze/events/**/*.parquet", recursive=True)
print(f"\n🥉 BRONZE")
if not bronze_files:
    errores.append("❌ Bronze: No hay archivos Parquet")
else:
    bronze = pd.concat([pd.read_parquet(f) for f in bronze_files])
    print(f"   Archivos: {len(bronze_files)}")
    print(f"   Registros: {len(bronze):,}")
    assert "event_id" in bronze.columns, "Falta event_id"
    assert "ingestion_timestamp" in bronze.columns, "Falta ingestion_timestamp"
    assert "batch_id" in bronze.columns, "Falta batch_id"
    print(f"   ✅ Estructura correcta")

# ── SILVER ──────────────────────────────────────────────────────────────
print(f"\n⚗️  SILVER")
ruta_silver = "data/silver/silver_events.parquet"
if not os.path.exists(ruta_silver):
    errores.append("❌ Silver: No existe silver_events.parquet")
else:
    silver = pd.read_parquet(ruta_silver)
    print(f"   Registros: {len(silver):,}")
    print(f"   Columnas: {len(silver.columns)}")
    
    reqs = ["event_id", "event", "is_failed", "is_transactional",
            "amount_cop", "amount_usd", "timestamp", "date",
            "ip_is_private", "geo_source"]
    for col in reqs:
        assert col in silver.columns, f"Falta columna: {col}"
    
    failed = silver["is_failed"].sum()
    con_monto = silver["amount_cop"].notna().sum()
    print(f"   Eventos fallidos: {failed:,}")
    print(f"   Con monto (COP): {con_monto:,}")
    print(f"   Con amount_usd: {silver['amount_usd'].notna().sum():,}")
    print(f"   ✅ Estructura correcta")

# ── GOLD ─────────────────────────────────────────────────────────────────
print(f"\n🥇 GOLD")
ruta_gold = "data/gold/gold_user_360.parquet"
if not os.path.exists(ruta_gold):
    errores.append("❌ Gold: No existe gold_user_360.parquet")
else:
    gold = pd.read_parquet(ruta_gold)
    print(f"   Usuarios: {len(gold):,}")
    print(f"   Columnas: {len(gold.columns)}")
    
    reqs_gold = ["user_id", "user_name", "total_transactions", "total_amount_cop",
                 "total_amount_usd", "avg_ticket", "failed_transactions",
                 "failure_rate", "top_merchant", "preferred_channel",
                 "days_since_last_tx"]
    for col in reqs_gold:
        assert col in gold.columns, f"Falta columna Gold: {col}"
    
    top = gold.nlargest(3, "total_amount_cop")[
        ["user_id", "user_name", "total_transactions", "total_amount_cop", "top_merchant"]
    ]
    print(f"\n   🏆 Top 3 usuarios por gasto:")
    print(top.to_string(index=False))
    print(f"\n   ✅ Estructura correcta")

# ── TABLAS DE SOPORTE ────────────────────────────────────────────────────
print(f"\n📊 TABLAS DE SOPORTE GOLD")
for tabla in ["gold_daily_metrics.parquet", "gold_event_summary.parquet"]:
    ruta = f"data/gold/{tabla}"
    if os.path.exists(ruta):
        df = pd.read_parquet(ruta)
        print(f"   ✅ {tabla}: {len(df)} filas")
    else:
        errores.append(f"❌ Falta: {tabla}")

# ── RESULTADO FINAL ──────────────────────────────────────────────────────
print("\n" + "=" * 60)
if errores:
    print("❌ VERIFICACIÓN FALLIDA:")
    for e in errores:
        print(f"   {e}")
else:
    print("✅ TODAS LAS CAPAS VERIFICADAS CORRECTAMENTE")
    print("   El pipeline está listo para la Fase 3 (Agente Inteligente)")
print("=" * 60)
```

---

## 7. Actualización del README

Agrega estas secciones al final de tu `README.md`:

```markdown
## ✅ Fase 1 — Análisis y Diseño (COMPLETADA)

### Entregables
- [x] **1.1** Exploración del JSON — 2.000 eventos, 7 tipos, 489 usuarios únicos
- [x] **1.2** Esquema Bronze→Silver — `/docs/schema_bronze_silver.md` (Manual 3)
- [x] **1.3** Métricas Gold — gold_user_360 con 21 columnas definidas (Manual 3)
- [x] **1.4** Prueba de APIs — `notebooks/02_prueba_apis.py`
- [x] **1.5** 10 preguntas del agente — documentadas en Manual 3

### Hallazgos clave del dataset
- 100% de IPs son privadas (192.168.x.x) → ip-api.com usa campo `city` como fallback
- 577 registros sin `amount` (USER_REGISTERED y USER_PROFILE_UPDATED)
- 300 eventos PAYMENT_FAILED (15% del total)
- 489 usuarios únicos en el dataset

## ✅ Fase 2 — Pipeline Bronze → Silver → Gold (COMPLETADA)

### Cómo ejecutar
```bash
# Pipeline completo
python src/run_pipeline.py

# Solo Silver + Gold (si Bronze ya existe)
python src/run_pipeline.py --desde-silver

# Verificar todas las capas
python verificar_pipeline_completo.py
```

### Datos del pipeline
| Capa | Registros | Archivo |
|---|---|---|
| Bronze | 2,000+ | `data/bronze/events/date=*/batch_*.parquet` |
| Silver | 2,000+ | `data/silver/silver_events.parquet` |
| Gold user_360 | 489 usuarios | `data/gold/gold_user_360.parquet` |
| Gold daily | Por día | `data/gold/gold_daily_metrics.parquet` |
| Gold summary | 7 tipos | `data/gold/gold_event_summary.parquet` |

## ⏳ Fase 3 — Agente Inteligente (PRÓXIMA)
- [ ] Text-to-SQL con Claude AI sobre gold_user_360
- [ ] Responder las 10 preguntas del agente en lenguaje natural
- [ ] Dashboard interactivo con Streamlit
```

---

## Checklist Final — ¿Estás listo para Fase 3?

```
FASE 1 — ANÁLISIS Y DISEÑO
   [x] 1.1 JSON explorado (tipos, campos, nulos)
   [x] 1.2 Esquema Bronze→Silver documentado (tabla de mapeo completa)
   [x] 1.3 Métricas Gold definidas (21 columnas en gold_user_360)
   [x] 1.4 Notebook de prueba de APIs (notebooks/02_prueba_apis.py)
   [x] 1.5 10 preguntas del agente definidas

FASE 2 — PIPELINE
   [x] 2.1 Capa Bronze (pipeline_bronze.py + simulador + bus de eventos)
   [x] 2.2 Capa Silver (pipeline_silver.py — 7 pasos documentados)
   [x] 2.3 Capa Gold (pipeline_gold.py — 3 tablas generadas)
   [x] Script maestro (run_pipeline.py)
   [x] Verificación completa (verificar_pipeline_completo.py)

PREREQUISITO FASE 3
   [ ] Ejecutar: python src/run_pipeline.py
   [ ] Ejecutar: python verificar_pipeline_completo.py (sin errores)
   [ ] Confirmar que data/gold/gold_user_360.parquet existe y tiene 489 filas
```

Cuando el checklist esté completamente marcado, **tienes el verde para Fase 3**.

---

*Manual 3 — Cierre Silver, Gold y Entregables de Diseño*
*Referencia: Databricks Medallion Architecture | open.er-api.com | ip-api.com*
*Versión 1.0 — Abril 2026*

