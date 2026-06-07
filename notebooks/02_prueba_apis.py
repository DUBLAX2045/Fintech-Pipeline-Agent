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
            
            if data.get("status") == "fail":
                print(f"   ❌ Error ip-api.com: {data.get('message', 'unknown')}")
                print(f"   → Confirmado: IPs privadas no son resolvibles")
                print(f"   → Estrategia aplicada: usar campo 'city' del payload")
            else:
                print(f"   ✅ País: {data.get('country')}")
                print(f"   ✅ Ciudad: {data.get('city')}")
                print(f"   ✅ Región: {data.get('regionName')}")
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
