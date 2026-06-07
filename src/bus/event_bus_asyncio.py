"""
Bus de eventos implementado con asyncio.Queue.
Simula la comunicación entre un ecommerce externo y el pipeline Bronze.

Componentes:
    1. EcommerceProducer  → Genera eventos (simula el ecommerce real)
    2. EventBus           → Cola central que transporta los eventos
    3. BronzeConsumer     → Se suscribe y escribe a la capa Bronze

Uso:
    python src/bus/event_bus_asyncio.py
"""

import asyncio
import uuid
import random
import os
import sys
from datetime import datetime, timezone
from dataclasses import dataclass, field
from typing import List

# Permitir imports del proyecto
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))


# ═══════════════════════════════════════════════════════════════════════════
# 1. DEFINICIÓN DEL EVENTO
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class FintechEvent:
    """
    Estructura de un evento fintech.
    Idéntica al JSON original para que Bronze no necesite cambios.
    
    @dataclass → Python genera automáticamente __init__, __repr__, etc.
    field(default_factory=...) → valores calculados en el momento de crear
    """
    event_type: str                      # PAYMENT_MADE, PURCHASE_MADE, etc.
    user_id: str                         # user_123, ecom_user_456
    amount: float                        # Monto en COP
    merchant: str                        # Rappi, Éxito, etc.
    
    # Estos se generan automáticamente
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    
    def to_pipeline_format(self) -> dict:
        """
        Convierte el evento al formato JSON del pipeline
        (igual que fintech_events_v4.json) para que Bronze lo procese
        sin ningún cambio.
        """
        return {
            "source": "ecommerce.app",
            "detailType": "event",
            "detail": {
                "id": self.event_id,
                "event": self.event_type,
                "version": "1.0",
                "eventType": self.event_type.lower(),
                "transactionType": self.event_type.lower(),
                "eventEntity": "USER",
                "eventStatus": "FAILED" if "FAILED" in self.event_type else "SUCCESS",
                "payload": {
                    "userId": self.user_id,
                    "name": "Ecom User",
                    "age": random.randint(18, 65),
                    "email": f"{self.user_id}@ecommerce.co",
                    "city": random.choice(["Bogotá", "Medellín", "Cali", "Barranquilla"]),
                    "segment": random.choice(["young_professional", "premium", "family", "student"]),
                    "timestamp": self.timestamp,
                    "accountId": f"acc_{self.user_id}",
                    "amount": self.amount,
                    "currency": "COP",
                    "merchant": self.merchant,
                    "category": random.choice(["food", "shopping", "transport", "entertainment"]),
                    "paymentMethod": random.choice(["debit_card", "credit_card", "wallet"]),
                    "installments": random.choice([1, 3, 6, 12]),
                    "balanceBefore": random.randint(100000, 5000000),
                    "balanceAfter": random.randint(50000, 4000000),
                    "location": {
                        "city": random.choice(["Bogotá", "Medellín", "Cali"]),
                        "country": "Colombia"
                    }
                },
                "metadata": {
                    "device": random.choice(["mobile", "web", "tablet"]),
                    "os": random.choice(["ios", "android", "windows"]),
                    "ip": f"190.{random.randint(1,254)}.{random.randint(1,254)}.{random.randint(1,254)}",
                    "channel": random.choice(["app", "web", "api"])
                }
            }
        }


# ═══════════════════════════════════════════════════════════════════════════
# 2. EL BUS DE EVENTOS (Cola Central)
# ═══════════════════════════════════════════════════════════════════════════

class EventBus:
    """
    Bus de eventos central usando asyncio.Queue.
    
    asyncio.Queue es thread-safe y perfecta para comunicar
    productores y consumidores de forma asíncrona.
    
    maxsize=1000 → máximo 1000 eventos en espera antes de hacer backpressure
    (el productor esperará si la cola está llena)
    """
    
    def __init__(self, maxsize: int = 1000):
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=maxsize)
        self._total_published = 0
        self._total_consumed = 0
    
    async def publish(self, event) -> None:
        """
        Publica un evento en el bus. Acepta FintechEvent o dict crudo del JSON.
        Si la cola está llena, espera hasta que haya espacio (backpressure).
        """
        await self._queue.put(event)
        self._total_published += 1
    
    async def consume_batch(self, max_batch_size: int = 50) -> List[FintechEvent]:
        """
        Consume hasta max_batch_size eventos de la cola.
        
        Estrategia micro-batch:
        1. Espera el primer evento (bloqueante — pausa hasta que llegue algo)
        2. Recoge todos los que estén disponibles sin esperar más
        3. Retorna el lote
        
        Esto es exactamente lo que Databricks llama "micro-batch processing".
        """
        batch = []
        
        # Esperar el primer evento (bloqueante)
        first_event = await self._queue.get()
        batch.append(first_event)
        self._queue.task_done()
        
        # Recoger el resto sin esperar (no bloqueante)
        while len(batch) < max_batch_size:
            try:
                event = self._queue.get_nowait()   # get_nowait = no esperar
                batch.append(event)
                self._queue.task_done()
            except asyncio.QueueEmpty:
                break   # No hay más eventos disponibles ahora mismo
        
        self._total_consumed += len(batch)
        return batch
    
    @property
    def pending(self) -> int:
        """Eventos en la cola esperando ser procesados."""
        return self._queue.qsize()
    
    def stats(self) -> dict:
        return {
            "total_published": self._total_published,
            "total_consumed": self._total_consumed,
            "pending_in_queue": self.pending
        }


# ═══════════════════════════════════════════════════════════════════════════
# 3. PRODUCTOR — Simula el Ecommerce Externo
# ═══════════════════════════════════════════════════════════════════════════

class EcommerceProducer:
    """
    Simula el ecommerce externo generando eventos y publicándolos en el bus.
    
    En producción real, esto sería reemplazado por:
    - Una API REST del ecommerce (FastAPI/Flask)
    - Un webhook configurado en el ecommerce
    - Una conexión a Kafka/AWS EventBridge
    """
    
    EVENTOS_POSIBLES = [
        "PAYMENT_MADE", "PURCHASE_MADE", "TRANSFER_SENT",
        "MONEY_ADDED", "PAYMENT_FAILED"
    ]
    
    MERCHANTS = ["Rappi", "Éxito", "Falabella", "Nike", "Netflix", "Spotify", "Amazon"]
    
    def __init__(self, bus: EventBus, eventos_por_segundo: float = 2.0):
        self.bus = bus
        self.intervalo = 1.0 / eventos_por_segundo  # segundos entre eventos
        self._running = False
        self._count = 0
    
    async def start(self, duracion_segundos: int = 60) -> None:
        """
        Inicia la producción de eventos durante un tiempo determinado.
        
        Args:
            duracion_segundos: Cuánto tiempo generar eventos (None = infinito)
        """
        self._running = True
        inicio = asyncio.get_event_loop().time()
        
        print(f"🛒 [PRODUCTOR] Iniciado — {1/self.intervalo:.1f} eventos/segundo")
        
        while self._running:
            # Generar un evento
            evento = FintechEvent(
                event_type=random.choice(self.EVENTOS_POSIBLES),
                user_id=f"ecom_user_{random.randint(1, 500)}",
                amount=float(random.randint(5000, 2000000)),
                merchant=random.choice(self.MERCHANTS)
            )
            
            # Publicar en el bus
            await self.bus.publish(evento)
            self._count += 1
            
            if self._count % 10 == 0:  # Log cada 10 eventos
                print(f"   📤 [PRODUCTOR] {self._count} eventos publicados | "
                      f"Cola: {self.bus.pending} esperando")
            
            # Verificar límite de tiempo
            if duracion_segundos and (asyncio.get_event_loop().time() - inicio) >= duracion_segundos:
                print(f"🛑 [PRODUCTOR] Tiempo completado. Total: {self._count} eventos")
                self._running = False
                break
            
            # Esperar antes del siguiente evento
            await asyncio.sleep(self.intervalo)
    
    def stop(self) -> None:
        self._running = False


# ═══════════════════════════════════════════════════════════════════════════
# 4. CONSUMIDOR — Procesador que escribe a Bronze
# ═══════════════════════════════════════════════════════════════════════════

class BronzeConsumer:
    """
    Consumidor suscrito al bus de eventos.
    Recoge lotes de eventos y los escribe a la capa Bronze (Parquet).
    
    Implementa el patrón que describe Databricks:
    "Streaming ingestion appends into Bronze; downstream jobs run
    as micro-batches to refresh Silver and Gold."
    """
    
    def __init__(
        self,
        bus: EventBus,
        carpeta_bronze: str = "data/bronze/events",
        batch_size: int = 20,
        flush_interval_segundos: int = 30,
        trigger=None,
    ):
        self.bus = bus
        self.carpeta_bronze = carpeta_bronze
        self.batch_size = batch_size
        self.flush_interval = flush_interval_segundos
        self.trigger = trigger  # PipelineTrigger opcional — dispara Silver/Gold post-batch
        self._running = False
        self._batches_guardados = 0
        self._eventos_guardados = 0
    
    async def start(self) -> None:
        """
        Inicia el ciclo de consumo y escritura a Bronze.
        Corre continuamente hasta que se detenga.
        """
        self._running = True
        print(f"🏭 [BRONZE] Consumidor iniciado | "
              f"Batch: {self.batch_size} eventos | "
              f"Flush: cada {self.flush_interval}s")
        
        ultimo_flush = asyncio.get_event_loop().time()
        buffer = []   # Buffer temporal antes de escribir a Parquet
        
        while self._running:
            try:
                # Consumir un lote del bus (espera máx 5 segundos)
                lote = await asyncio.wait_for(
                    self.bus.consume_batch(max_batch_size=self.batch_size),
                    timeout=5.0
                )
                buffer.extend(lote)
                
            except asyncio.TimeoutError:
                pass  # No llegaron eventos en 5 segundos, verificar flush
            
            # Decidir si escribir a Parquet:
            # - Si el buffer llegó al tamaño máximo, O
            # - Si pasaron más de flush_interval segundos desde el último flush
            ahora = asyncio.get_event_loop().time()
            tiempo_desde_flush = ahora - ultimo_flush
            
            debe_guardar = (
                len(buffer) >= self.batch_size or
                (len(buffer) > 0 and tiempo_desde_flush >= self.flush_interval)
            )
            
            if debe_guardar:
                await self._guardar_batch(buffer)
                buffer = []
                ultimo_flush = asyncio.get_event_loop().time()
    
    async def _guardar_batch(self, eventos: list) -> None:
        """
        Convierte los eventos al formato del pipeline y los guarda en Parquet.
        Acepta mezcla de FintechEvent (del EcommerceProducer) y dict crudos
        (del DatasetProducer). Usa asyncio.to_thread para no bloquear el bus.
        """
        print(f"\n💾 [BRONZE] Guardando lote de {len(eventos)} eventos...")

        # Normalizar: FintechEvent → dict con to_pipeline_format(), dict → pasa directo
        eventos_dict = [
            e if isinstance(e, dict) else e.to_pipeline_format()
            for e in eventos
        ]

        # PyArrow can hang on Windows when Parquet writes run inside
        # asyncio.to_thread under Uvicorn. Batches are intentionally small, so
        # the reliable path is to write synchronously in this consumer loop.
        self._guardar_sincronico(eventos_dict)
    
    def _guardar_sincronico(self, mensajes: list) -> None:
        """
        Lógica sincrónica de guardado (corre en thread separado).

        Clasifica los mensajes por tipo antes de guardar:
        - Mensajes con estructura legacy (detail.payload) → aplanar_evento() → data/bronze/events/
        - Mensajes con envelope estándar (msg_type != event) → aplanar genérico → data/bronze/<tipo>/

        Esto permite que Bronze sea un lago multi-tipo sin romper el pipeline
        Silver/Gold existente, que solo consume la carpeta 'events'.
        """
        from src.bronze.ingest import aplanar_todos, detectar_y_registrar_duplicados
        from src.bronze.metadata import agregar_metadatos_ingesta
        from src.bronze.save import guardar_bronze_parquet
        from src.bus.message_schema import (
            aplanar_mensajes_genericos,
            clasificar_mensajes,
        )

        grupos = clasificar_mensajes(mensajes)
        rutas_guardadas = []

        for tipo, msgs in grupos.items():
            if tipo == "fintech_legacy":
                # Ruta original: aplanar la estructura detail.payload → Bronze/events
                df = aplanar_todos(msgs)
                df = agregar_metadatos_ingesta(df, "bus_stream")
                df = detectar_y_registrar_duplicados(df, carpeta_bronze=self.carpeta_bronze)
                carpeta = self.carpeta_bronze  # data/bronze/events
            else:
                # Ruta genérica: aplanar envelope estándar → Bronze/<tipo>
                # Silver/Gold ignoran estas sub-carpetas por ahora (Fase 3+)
                df = aplanar_mensajes_genericos(msgs)
                df = agregar_metadatos_ingesta(df, f"bus_stream_{tipo}")
                carpeta = self.carpeta_bronze.replace("events", tipo)  # data/bronze/metric, etc.

            ruta = guardar_bronze_parquet(df, carpeta)
            rutas_guardadas.append((tipo, len(msgs), ruta))

        self._batches_guardados += 1
        self._eventos_guardados += len(mensajes)

        for tipo, count, ruta in rutas_guardadas:
            print(f"   ✅ [BRONZE] {count} msgs tipo '{tipo}' → {ruta}")
        print(f"   📊 [BRONZE] Lote #{self._batches_guardados} | Total acumulado: {self._eventos_guardados}")

        # Disparar Silver → Gold si hay un trigger configurado.
        # Silver solo lee data/bronze/events/ — los tipos genéricos no lo afectan.
        if self.trigger:
            self.trigger.trigger()
    
    def stop(self) -> None:
        self._running = False
    
    def stats(self) -> dict:
        return {
            "batches_guardados": self._batches_guardados,
            "eventos_guardados": self._eventos_guardados
        }


# ═══════════════════════════════════════════════════════════════════════════
# 5. ORQUESTADOR — Une todo el sistema
# ═══════════════════════════════════════════════════════════════════════════

async def ejecutar_pipeline_streaming(
    duracion_segundos: int = 120,
    eventos_por_segundo: float = 3.0,
    batch_size: int = 15,
    flush_interval: int = 20
) -> None:
    """
    Orquesta el sistema completo:
    Ecommerce Producer → Event Bus → Bronze Consumer
    
    asyncio.gather() ejecuta el productor y el consumidor
    CONCURRENTEMENTE (al mismo tiempo, en el mismo proceso).
    """
    
    print("=" * 65)
    print("🚀 PIPELINE STREAMING — BUS DE EVENTOS ASYNCIO")
    print("=" * 65)
    print(f"   Duración:         {duracion_segundos}s")
    print(f"   Velocidad:        {eventos_por_segundo} eventos/segundo")
    print(f"   Batch size:       {batch_size} eventos")
    print(f"   Flush interval:   cada {flush_interval}s")
    print("=" * 65 + "\n")
    
    # Crear el bus (cola compartida entre productor y consumidor)
    bus = EventBus(maxsize=500)
    
    # Crear productor y consumidor
    productor = EcommerceProducer(bus, eventos_por_segundo=eventos_por_segundo)
    consumidor = BronzeConsumer(
        bus, 
        batch_size=batch_size,
        flush_interval_segundos=flush_interval
    )
    
    # Ejecutar concurrentemente con límite de tiempo
    try:
        await asyncio.wait_for(
            asyncio.gather(
                productor.start(duracion_segundos=duracion_segundos),
                consumidor.start()
            ),
            timeout=duracion_segundos + 10  # margen extra para el último flush
        )
    except asyncio.TimeoutError:
        productor.stop()
        consumidor.stop()
    
    # Resumen final
    print("\n" + "=" * 65)
    print("✅ PIPELINE COMPLETADO")
    stats_bus = bus.stats()
    stats_consumer = consumidor.stats()
    print(f"   Eventos publicados:  {stats_bus['total_published']}")
    print(f"   Eventos en Bronze:   {stats_consumer['eventos_guardados']}")
    print(f"   Lotes Parquet:       {stats_consumer['batches_guardados']}")
    print(f"   Eventos en cola:     {stats_bus['pending_in_queue']} (pendientes)")
    print("=" * 65)


# ── Punto de entrada ────────────────────────────────────────────────────────
if __name__ == "__main__":
    # Para prueba rápida: 60 segundos, 3 eventos/segundo
    asyncio.run(ejecutar_pipeline_streaming(
        duracion_segundos=60,
        eventos_por_segundo=3.0,
        batch_size=15,
        flush_interval=20
    ))
