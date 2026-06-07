"""
Simulador de eventos de ecommerce.
Genera nuevos eventos con datos realistas usando la librería Faker,
y los agrega al pipeline de Bronze en modo micro-batch.

Uso:
    python src/bronze/simulator.py
    (Ctrl+C para detener)
"""

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
            df = detectar_y_registrar_duplicados(df, carpeta_bronze=carpeta_bronze)
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
        print("\n\n⛔ Simulador detenido por el usuario.")
        print(f"   Total de lotes procesados: {lote_num}")
        print(f"   Total de eventos generados: {lote_num * eventos_por_lote}")


if __name__ == "__main__":
    # Para pruebas rápidas: 5 eventos cada 10 segundos, máximo 3 lotes
    ejecutar_simulador(
        eventos_por_lote=5,
        intervalo_segundos=10,
        max_lotes=3           # Quitar esta línea para modo producción
    )
