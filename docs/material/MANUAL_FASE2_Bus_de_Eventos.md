# 📘 Manual de Desarrollo — Fase 2: Bus de Eventos para la Capa Bronze
## Proyecto: Plataforma de Datos Fintech en Tiempo Real

> **Nivel:** Principiante con fundamentos de Fase 1 completados
> **Objetivo:** Conectar un ecommerce externo al pipeline Bronze usando un bus de eventos
> **Última actualización:** Abril 2026

---

## 📋 Tabla de Contenidos

1. [¿Qué es un Bus de Eventos?](#1-qué-es-un-bus-de-eventos)
2. [Cómo Elegir la Herramienta Correcta](#2-cómo-elegir-la-herramienta-correcta)
3. [Opción A — Bus de Eventos en Python Puro (asyncio)](#3-opción-a--bus-de-eventos-en-python-puro-asyncio)
4. [Opción B — PyPubSub (pub/sub en proceso)](#4-opción-b--pypubsub-pubsub-en-proceso)
5. [Opción C — Servicios Gestionados en la Nube](#5-opción-c--servicios-gestionados-en-la-nube)
6. [Arquitectura Completa del Pipeline](#6-arquitectura-completa-del-pipeline)
7. [Implementación Paso a Paso (Opción Recomendada)](#7-implementación-paso-a-paso-opción-recomendada)
8. [Conectar el Bus a la Capa Bronze](#8-conectar-el-bus-a-la-capa-bronze)
9. [Pruebas y Verificación](#9-pruebas-y-verificación)
10. [README — Sección Fase 2](#10-readme--sección-fase-2)

---

## 1. ¿Qué es un Bus de Eventos?

Imagina que el ecommerce es una tienda física y el pipeline de datos es tu bodega. Cada vez que se hace una venta en la tienda, alguien debe llevar el ticket a la bodega para registrarlo. El **bus de eventos** es ese mensajero.

En términos técnicos, un bus de eventos es un sistema de comunicación con tres actores:

```
┌─────────────────┐       EVENTO       ┌─────────────────┐       EVENTO       ┌─────────────────┐
│   PRODUCTOR     │ ──────────────────▶ │   BUS DE        │ ──────────────────▶ │   CONSUMIDOR    │
│  (Ecommerce)    │                     │   EVENTOS       │                     │  (Bronze Layer) │
│                 │                     │                 │                     │                 │
│ Genera eventos: │                     │ - Recibe        │                     │ - Se suscribe   │
│ PAYMENT_MADE    │                     │ - Enruta        │                     │ - Procesa       │
│ PURCHASE_MADE   │                     │ - Distribuye    │                     │ - Guarda Parquet│
└─────────────────┘                     └─────────────────┘                     └─────────────────┘
```

**La diferencia con el simulador Faker de la Fase 1:**
- Faker generaba datos DENTRO del mismo script (todo en un proceso)
- Un bus de eventos permite que el ecommerce esté en un servidor SEPARADO y envíe eventos al pipeline de forma desacoplada

Según Databricks, la capa Bronze puede recibir datos de "message buses (por ejemplo, Kafka, Kinesis, etc.)" y debe almacenarlos en su formato original sin transformaciones, de forma append-only (solo agregar, nunca modificar).

---

## 2. Cómo Elegir la Herramienta Correcta

Esta es la decisión más importante. Aquí tienes una tabla honesta y práctica:

| Criterio | asyncio (Python puro) | PyPubSub (librería) | Kafka/RabbitMQ | AWS/GCP/Azure |
|---|---|---|---|---|
| **Dificultad** | ⭐⭐ Media | ⭐ Baja | ⭐⭐⭐⭐ Alta | ⭐⭐⭐ Media-Alta |
| **Costo** | Gratis | Gratis | Gratis (self-hosted) | $$ |
| **Escala** | Hasta ~10K eventos/s | Hasta ~1K eventos/s | Millones eventos/s | Ilimitado |
| **Persistencia** | ❌ No (en memoria) | ❌ No (en memoria) | ✅ Sí | ✅ Sí |
| **Tolerancia a fallos** | ❌ Baja | ❌ Baja | ✅ Alta | ✅ Muy alta |
| **Bueno para este reto** | ✅ SÍ | ✅ SÍ | ✅ SÍ (producción) | ⚠️ Exceso para reto |
| **Requiere instalación extra** | No | Solo `pip install` | Sí (Docker/servidor) | Sí (cuenta cloud) |

### ¿Cuál elegir para este reto?

**Recomendación para principiantes: asyncio + Queue** (Opción A)
- Corre 100% en tu computador
- No necesitas cuentas cloud ni instalaciones complejas
- Es exactamente lo que Databricks llama "micro-batch streaming"
- Puedes migrar a Kafka o AWS después sin cambiar el código de Bronze

**Para producción real en una fintech: Apache Kafka o AWS EventBridge**
- Kafka es el estándar de la industria para eventos financieros
- AWS EventBridge es lo que usan empresas como Rappi, Bancolombia, etc.

---

## 3. Opción A — Bus de Eventos en Python Puro (asyncio)

### ¿Cómo funciona asyncio?

`asyncio` es el módulo de Python para ejecutar múltiples tareas al mismo tiempo sin bloquear el programa. La clave es la `Queue` (cola): el ecommerce pone eventos en la cola, y el procesador Bronze los saca y los guarda.

```
Ecommerce Producer          asyncio.Queue              Bronze Consumer
      │                          │                           │
      │  ──── evento 1 ────▶     │                           │
      │  ──── evento 2 ────▶     │  ◀──── get() ────────────│
      │  ──── evento 3 ────▶     │  ◀──── get() ────────────│
      │                          │  (micro-batch cada 30s)  │
```

### Instalación

Solo necesitas Python 3.10+ (asyncio ya viene incluido):
```bash
# asyncio está en la librería estándar, no necesitas pip install
# Solo necesitas las que ya instalaste en Fase 1:
pip install pandas pyarrow
```

### Código completo — Bus con asyncio

Crea el archivo `src/bus/event_bus_asyncio.py`:

```python
# src/bus/event_bus_asyncio.py
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
import json
import uuid
import random
import os
import sys
from datetime import datetime, timezone
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List

import pandas as pd

# Permitir imports del proyecto
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
from src.bronze.ingest import aplanar_evento
from src.bronze.metadata import agregar_metadatos_ingesta
from src.bronze.save import guardar_bronze_parquet


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
    
    async def publish(self, event: FintechEvent) -> None:
        """
        Publica un evento en el bus.
        Si la cola está llena, espera hasta que haya espacio.
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
        flush_interval_segundos: int = 30
    ):
        self.bus = bus
        self.carpeta_bronze = carpeta_bronze
        self.batch_size = batch_size
        self.flush_interval = flush_interval_segundos
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
    
    async def _guardar_batch(self, eventos: List[FintechEvent]) -> None:
        """
        Convierte los eventos al formato del pipeline y los guarda en Parquet.
        Usa asyncio.to_thread para no bloquear el event loop con I/O de disco.
        """
        print(f"\n💾 [BRONZE] Guardando lote de {len(eventos)} eventos...")
        
        # Convertir al formato del pipeline (mismo que fintech_events_v4.json)
        eventos_dict = [e.to_pipeline_format() for e in eventos]
        
        # Ejecutar en un thread separado (pandas/pyarrow son síncronos)
        # asyncio.to_thread evita que el guardado de archivos bloquee el bus
        await asyncio.to_thread(
            self._guardar_sincronico, eventos_dict
        )
    
    def _guardar_sincronico(self, eventos_dict: list) -> None:
        """Lógica sincrónica de guardado (corre en thread separado)."""
        from src.bronze.ingest import aplanar_todos, detectar_y_registrar_duplicados
        from src.bronze.metadata import agregar_metadatos_ingesta
        from src.bronze.save import guardar_bronze_parquet
        
        df = aplanar_todos(eventos_dict)
        df = agregar_metadatos_ingesta(df, "ecommerce_bus_stream")
        df = detectar_y_registrar_duplicados(df)
        ruta = guardar_bronze_parquet(df, self.carpeta_bronze)
        
        self._batches_guardados += 1
        self._eventos_guardados += len(eventos_dict)
        
        print(f"   ✅ [BRONZE] Lote #{self._batches_guardados} guardado → {ruta}")
        print(f"   📊 Total acumulado: {self._eventos_guardados} eventos en Bronze")
    
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
```

---

## 4. Opción B — PyPubSub (pub/sub en proceso)

PyPubSub es más simple y usa el patrón "publicar por tema". Ideal si quieres código más legible y menos orientado a async.

### Instalación

```bash
pip install pypubsub
```

### Código — Bus con PyPubSub

Crea el archivo `src/bus/event_bus_pypubsub.py`:

```python
# src/bus/event_bus_pypubsub.py
"""
Bus de eventos con PyPubSub.
Más simple que asyncio, ideal para entender el patrón pub/sub.

Diferencia con asyncio:
- asyncio: asíncrono, no bloquea, mejor para producción
- PyPubSub: síncrono, más simple, mejor para aprender el concepto

Uso:
    python src/bus/event_bus_pypubsub.py
"""

import json
import uuid
import random
import time
import os
import sys
from datetime import datetime, timezone

from pubsub import pub   # PyPubSub

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
from src.bronze.ingest import aplanar_todos, detectar_y_registrar_duplicados
from src.bronze.metadata import agregar_metadatos_ingesta
from src.bronze.save import guardar_bronze_parquet


# ═══════════════════════════════════════════════════════════════════════════
# DEFINICIÓN DE TEMAS (TOPICS)
# PyPubSub organiza los mensajes por "temas"
# Puedes suscribirte a "fintech.eventos" y recibir TODOS los subtipos
# ═══════════════════════════════════════════════════════════════════════════

TOPIC_EVENTOS     = "fintech.eventos"          # Todos los eventos
TOPIC_PAGOS       = "fintech.eventos.pagos"    # Solo pagos
TOPIC_COMPRAS     = "fintech.eventos.compras"  # Solo compras
TOPIC_BRONZE_OK   = "fintech.bronze.guardado"  # Confirmación de guardado


# ═══════════════════════════════════════════════════════════════════════════
# SUSCRIPTORES — Se registran para recibir eventos
# ═══════════════════════════════════════════════════════════════════════════

# Buffer global para acumular eventos antes de guardar
_buffer_eventos = []
_BATCH_SIZE = 10   # Guardar cada 10 eventos

def procesador_bronze(evento_json: dict) -> None:
    """
    Suscriptor principal: recibe eventos y los acumula en el buffer.
    Cuando el buffer llega a BATCH_SIZE, guarda en Parquet.
    """
    global _buffer_eventos
    
    _buffer_eventos.append(evento_json)
    print(f"   📥 [BRONZE] Recibido: {evento_json['detail']['event']} "
          f"| Buffer: {len(_buffer_eventos)}/{_BATCH_SIZE}")
    
    # Guardar cuando el buffer esté lleno
    if len(_buffer_eventos) >= _BATCH_SIZE:
        _vaciar_buffer()


def monitor_eventos(evento_json: dict) -> None:
    """
    Suscriptor de monitoreo: solo registra, no guarda datos.
    Muestra que múltiples suscriptores pueden recibir el mismo evento.
    """
    user = evento_json["detail"]["payload"]["userId"]
    tipo = evento_json["detail"]["event"]
    print(f"   👁️  [MONITOR] Evento detectado: {tipo} — {user}")


def _vaciar_buffer() -> None:
    """Guarda el buffer actual en Parquet y lo limpia."""
    global _buffer_eventos
    
    if not _buffer_eventos:
        return
    
    print(f"\n💾 [BRONZE] Guardando lote de {len(_buffer_eventos)} eventos...")
    
    df = aplanar_todos(_buffer_eventos)
    df = agregar_metadatos_ingesta(df, "pypubsub_bus")
    df = detectar_y_registrar_duplicados(df)
    ruta = guardar_bronze_parquet(df, "data/bronze/events")
    
    print(f"   ✅ Guardado: {ruta}")
    
    # Publicar confirmación (otros módulos pueden escuchar esto)
    pub.sendMessage(TOPIC_BRONZE_OK, ruta=ruta, cantidad=len(_buffer_eventos))
    
    _buffer_eventos = []   # Limpiar buffer


def confirmacion_guardado(ruta: str, cantidad: int) -> None:
    """Suscriptor de confirmaciones — solo para logging."""
    print(f"   🎯 [CONFIRMACIÓN] {cantidad} eventos → {ruta}")


# ═══════════════════════════════════════════════════════════════════════════
# REGISTRAR SUSCRIPTORES EN EL BUS
# ═══════════════════════════════════════════════════════════════════════════

def inicializar_bus() -> None:
    """Registra todos los suscriptores en PyPubSub."""
    pub.subscribe(procesador_bronze, TOPIC_EVENTOS)
    pub.subscribe(monitor_eventos, TOPIC_EVENTOS)
    pub.subscribe(confirmacion_guardado, TOPIC_BRONZE_OK)
    print("✅ [BUS] Suscriptores registrados:")
    print("   - procesador_bronze → fintech.eventos")
    print("   - monitor_eventos   → fintech.eventos")
    print("   - confirmacion      → fintech.bronze.guardado")


# ═══════════════════════════════════════════════════════════════════════════
# PRODUCTOR — Ecommerce externo
# ═══════════════════════════════════════════════════════════════════════════

def generar_evento_ecommerce() -> dict:
    """Genera un evento con el formato del pipeline."""
    tipo = random.choice(["PAYMENT_MADE", "PURCHASE_MADE", "TRANSFER_SENT", "PAYMENT_FAILED"])
    return {
        "source": "ecommerce.app",
        "detailType": "event",
        "detail": {
            "id": str(uuid.uuid4()),
            "event": tipo,
            "version": "1.0",
            "eventType": tipo.lower(),
            "transactionType": tipo.lower(),
            "eventEntity": "USER",
            "eventStatus": "FAILED" if "FAILED" in tipo else "SUCCESS",
            "payload": {
                "userId": f"ecom_{random.randint(1, 200)}",
                "name": "Ecom User",
                "age": random.randint(18, 60),
                "email": "user@ecommerce.co",
                "city": random.choice(["Bogotá", "Medellín", "Cali"]),
                "segment": random.choice(["premium", "young_professional", "family"]),
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "accountId": f"acc_{random.randint(100, 999)}",
                "amount": float(random.randint(10000, 500000)),
                "currency": "COP",
                "merchant": random.choice(["Rappi", "Éxito", "Amazon"]),
                "category": random.choice(["food", "shopping", "transport"]),
                "paymentMethod": random.choice(["debit_card", "wallet"]),
                "installments": 1,
                "balanceBefore": random.randint(200000, 3000000),
                "balanceAfter": random.randint(100000, 2000000),
                "location": {"city": "Bogotá", "country": "Colombia"}
            },
            "metadata": {
                "device": "mobile",
                "os": "android",
                "ip": f"190.{random.randint(1,254)}.{random.randint(1,254)}.1",
                "channel": "app"
            }
        }
    }


def ejecutar_productor_pypubsub(total_eventos: int = 50, intervalo: float = 0.5) -> None:
    """
    Publica eventos en el bus usando PyPubSub.
    
    Args:
        total_eventos: Cuántos eventos generar en total
        intervalo:     Segundos entre eventos
    """
    print(f"\n🛒 [PRODUCTOR] Iniciando — {total_eventos} eventos, cada {intervalo}s")
    
    for i in range(1, total_eventos + 1):
        evento = generar_evento_ecommerce()
        
        # PUBLICAR en el bus → todos los suscriptores lo recibirán
        pub.sendMessage(TOPIC_EVENTOS, evento_json=evento)
        
        print(f"📤 Evento #{i}/{total_eventos}: {evento['detail']['event']}")
        time.sleep(intervalo)
    
    # Vaciar el buffer al terminar (eventos que quedaron < BATCH_SIZE)
    if _buffer_eventos:
        print(f"\n🔄 Vaciando buffer final ({len(_buffer_eventos)} eventos)...")
        _vaciar_buffer()
    
    print(f"\n✅ [PRODUCTOR] Completado. {total_eventos} eventos publicados.")


# ── Punto de entrada ────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("=" * 60)
    print("🚀 PIPELINE CON PYPUBSUB")
    print("=" * 60)
    
    inicializar_bus()
    ejecutar_productor_pypubsub(total_eventos=35, intervalo=0.3)
```

---

## 5. Opción C — Servicios Gestionados en la Nube

Para cuando el proyecto pase a producción real. Aquí está la comparativa con las ventajas y cómo se integran con Bronze:

### AWS EventBridge (recomendado para AWS)

```
Ecommerce → AWS EventBridge → Lambda/SQS → Tu script Bronze
```

```python
# Ejemplo: Recibir eventos de AWS SQS y escribir a Bronze
import boto3
import json

def consumir_sqs_a_bronze():
    sqs = boto3.client('sqs', region_name='us-east-1')
    queue_url = 'https://sqs.us-east-1.amazonaws.com/TU_CUENTA/fintech-events'
    
    while True:
        response = sqs.receive_message(
            QueueUrl=queue_url,
            MaxNumberOfMessages=10,   # Micro-batch de hasta 10
            WaitTimeSeconds=20        # Long polling
        )
        
        mensajes = response.get('Messages', [])
        if mensajes:
            eventos = [json.loads(m['Body']) for m in mensajes]
            # → Aquí llamas a tu pipeline Bronze exactamente igual
            guardar_lote_en_bronze(eventos)
```

### Apache Kafka (estándar de la industria)

```
Ecommerce → Kafka Topic: fintech-events → Consumer Group → Bronze
```

```bash
# Instalar cliente Kafka para Python
pip install kafka-python
```

```python
from kafka import KafkaConsumer
import json

def consumir_kafka_a_bronze():
    consumer = KafkaConsumer(
        'fintech-events',
        bootstrap_servers=['localhost:9092'],
        value_deserializer=lambda m: json.loads(m.decode('utf-8')),
        auto_offset_reset='earliest',
        group_id='bronze-consumer-group'
    )
    
    batch = []
    for mensaje in consumer:
        batch.append(mensaje.value)
        if len(batch) >= 50:   # Micro-batch de 50
            guardar_lote_en_bronze(batch)
            batch = []
```

### ¿Cuándo migrar a estos servicios?

- **asyncio**: Equipos pequeños, prototipos, hasta ~50K eventos/día
- **Kafka**: Cuando superas 100K eventos/día o necesitas replay de eventos
- **AWS/GCP**: Cuando el equipo no quiere administrar infraestructura

---

## 6. Arquitectura Completa del Pipeline

Así queda el flujo completo combinando lo de la Fase 1 y la Fase 2:

```
╔══════════════════════════════════════════════════════════════════╗
║                    FUENTES DE DATOS                              ║
╠══════════════════╦═══════════════════════════════════════════════╣
║  Archivo JSON    ║           Ecommerce Externo                   ║
║  (Carga inicial) ║    (Eventos en tiempo casi real)              ║
║  fintech_events  ║                                               ║
║  _v4.json        ║   API REST / Webhook / WebSocket              ║
╚══════════════════╩═══════════════════════════════════════════════╝
         │                          │
         │                          ▼
         │               ┌─────────────────────┐
         │               │    BUS DE EVENTOS   │
         │               │  asyncio.Queue  OR  │
         │               │  PyPubSub       OR  │
         │               │  Kafka/SQS          │
         │               └─────────┬───────────┘
         │                         │
         └─────────────────────────┘
                          │
                          ▼ (micro-batch: cada N eventos o N segundos)
         ╔════════════════════════════════════════════╗
         ║              CAPA BRONZE                   ║
         ║  - Aplana JSON anidado                     ║
         ║  - Agrega metadatos (timestamp, batch_id)  ║
         ║  - Detecta y registra duplicados           ║
         ║  - Guarda en Parquet particionado          ║
         ║  data/bronze/events/date=YYYY-MM-DD/       ║
         ╚════════════════════════════════════════════╝
                          │
                          ▼ (Fase 3 — próxima entrega)
         ╔════════════════════════════════════════════╗
         ║              CAPA SILVER                   ║
         ║  - Limpia y normaliza                      ║
         ║  - Enriquece con ip-api.com + ExchangeRate      ║
         ╚════════════════════════════════════════════╝
                          │
                          ▼
         ╔════════════════════════════════════════════╗
         ║              CAPA GOLD                     ║
         ║  - Visión 360 del usuario                  ║
         ║  - Métricas agregadas por userId           ║
         ╚════════════════════════════════════════════╝
```

---

## 7. Implementación Paso a Paso (Opción Recomendada)

### Paso 1 — Crear la carpeta del bus

```bash
mkdir -p src/bus
touch src/bus/__init__.py
```

### Paso 2 — Instalar dependencias nuevas

```bash
pip install pypubsub
pip freeze > requirements.txt
```

### Paso 3 — Copiar los archivos

Crea estos dos archivos en `src/bus/`:
- `event_bus_asyncio.py` (código de la Opción A)
- `event_bus_pypubsub.py` (código de la Opción B)

### Paso 4 — Probar la Opción A (asyncio) — 60 segundos

```bash
python src/bus/event_bus_asyncio.py
```

Salida esperada:
```
=================================================================
🚀 PIPELINE STREAMING — BUS DE EVENTOS ASYNCIO
=================================================================
   Duración:         60s
   Velocidad:        3.0 eventos/segundo
   Batch size:       15 eventos
   Flush interval:   cada 20s
=================================================================

🏭 [BRONZE] Consumidor iniciado | Batch: 15 eventos | Flush: cada 20s
🛒 [PRODUCTOR] Iniciado — 3.0 eventos/segundo
   📤 [PRODUCTOR] 10 eventos publicados | Cola: 3 esperando
   📤 [PRODUCTOR] 20 eventos publicados | Cola: 1 esperando

💾 [BRONZE] Guardando lote de 15 eventos...
   ✅ [BRONZE] Lote #1 guardado → data/bronze/events/date=2026-04-21/batch_xyz.parquet
   📊 Total acumulado: 15 eventos en Bronze
```

### Paso 5 — Verificar los archivos generados

```python
# Ejecuta esto en un notebook o script para verificar
import pandas as pd
import glob

archivos = glob.glob("data/bronze/events/**/*.parquet", recursive=True)
print(f"Archivos Parquet generados: {len(archivos)}")

for ruta in archivos:
    df = pd.read_parquet(ruta)
    print(f"\n📄 {ruta}")
    print(f"   Filas: {len(df)} | Columnas: {len(df.columns)}")
    print(f"   Sources: {df['source'].unique()}")   # Debe incluir 'ecommerce.app'
    print(f"   Batch IDs: {df['batch_id'].unique()}")
```

### Paso 6 — Probar la Opción B (PyPubSub)

```bash
python src/bus/event_bus_pypubsub.py
```

---

## 8. Conectar el Bus a la Capa Bronze

### ¿Qué pasa si el ecommerce ya existe y envía eventos via HTTP?

Si el ecommerce real envía eventos via una API REST (lo más común), puedes crear un receptor HTTP con FastAPI que los ponga en el bus:

```python
# src/bus/api_receiver.py
"""
Receptor HTTP para eventos del ecommerce.
El ecommerce hace POST a /eventos y este servidor los pone en el bus.

Instalación:
    pip install fastapi uvicorn

Uso:
    uvicorn src.bus.api_receiver:app --port 8000
    
El ecommerce entonces hace:
    POST http://localhost:8000/eventos
    Body: { ...evento JSON... }
"""

from fastapi import FastAPI, BackgroundTasks
from typing import Any, Dict
import asyncio

app = FastAPI(title="Fintech Event Receiver")
_bus_queue: asyncio.Queue = asyncio.Queue(maxsize=1000)


@app.post("/eventos")
async def recibir_evento(evento: Dict[str, Any], background_tasks: BackgroundTasks):
    """
    Endpoint que recibe eventos del ecommerce y los pone en el bus.
    Retorna inmediatamente (202 Accepted) sin esperar el procesamiento.
    """
    await _bus_queue.put(evento)
    return {"status": "accepted", "queue_size": _bus_queue.qsize()}


@app.get("/health")
async def health():
    return {"status": "ok", "queue_size": _bus_queue.qsize()}
```

```bash
# Instalar FastAPI
pip install fastapi uvicorn

# Iniciar el receptor
uvicorn src.bus.api_receiver:app --port 8000

# Probar enviando un evento (desde otra terminal)
curl -X POST http://localhost:8000/eventos \
  -H "Content-Type: application/json" \
  -d '{"source": "ecommerce.app", "detail": {"event": "PAYMENT_MADE"}}'
```

---

## 9. Pruebas y Verificación

### Checklist de verificación

Después de ejecutar cualquiera de las opciones, verifica:

```python
# verificar_bronze.py — Ejecuta este script para validar
import pandas as pd
import glob
import os

print("🔍 VERIFICACIÓN DE CAPA BRONZE\n")

# 1. ¿Existen archivos Parquet?
archivos = glob.glob("data/bronze/events/**/*.parquet", recursive=True)
print(f"✅ Archivos Parquet: {len(archivos)}")
assert len(archivos) > 0, "❌ No se encontraron archivos Parquet"

# 2. ¿Tienen el formato correcto?
df_total = pd.concat([pd.read_parquet(f) for f in archivos])
print(f"✅ Total de eventos: {len(df_total)}")

# 3. ¿Tienen los metadatos de ingesta?
columnas_requeridas = ["event_id", "source", "event", "user_id",
                        "ingestion_timestamp", "source_file", "batch_id",
                        "ingestion_date", "is_duplicate"]
for col in columnas_requeridas:
    assert col in df_total.columns, f"❌ Falta columna: {col}"
print(f"✅ Columnas requeridas presentes: {len(columnas_requeridas)}")

# 4. ¿Los eventos del ecommerce llegaron?
sources = df_total['source'].unique()
print(f"✅ Fuentes de datos: {sources}")

# 5. ¿Hay duplicados?
dupes = df_total['is_duplicate'].sum()
print(f"{'⚠️' if dupes > 0 else '✅'} Duplicados detectados: {dupes}")

# 6. Distribución por tipo de evento
print(f"\n📊 Eventos por tipo:")
print(df_total['event'].value_counts().to_string())

print("\n🎉 Verificación completada exitosamente")
```

---

## 10. README — Sección Fase 2

Agrega esto al final de tu `README.md`:

```markdown
## ▶️ Fase 2 — Bus de Eventos (COMPLETADA)

### Componentes implementados

**Opción A: asyncio (Recomendada para desarrollo)**
- Productor: genera eventos de ecommerce a N eventos/segundo
- Bus: asyncio.Queue con backpressure (máx 1000 eventos en cola)
- Consumidor: micro-batch → escribe a Bronze cada N eventos o N segundos

**Opción B: PyPubSub (Más simple, para aprendizaje)**
- Patrón pub/sub por topics (fintech.eventos, fintech.bronze.guardado)
- Múltiples suscriptores al mismo topic (Bronze + Monitor)

### Cómo ejecutar

```bash
# Opción A — asyncio (60 segundos, 3 eventos/segundo)
python src/bus/event_bus_asyncio.py

# Opción B — PyPubSub (35 eventos con pausa de 0.3s)
python src/bus/event_bus_pypubsub.py

# Verificar datos en Bronze
python verificar_bronze.py
```

### Diagrama de flujo

```
Ecommerce → Bus de Eventos → BronzeConsumer → Parquet (Bronze)
  (3 ev/s)   (asyncio.Queue)   (micro-batch)   (particionado por fecha)
```

### Próxima fase: Silver
Los Parquet de Bronze serán leídos por el pipeline Silver para:
- Limpiar y normalizar tipos de datos
- Enriquecer con ip-api.com y ExchangeRate API
- Eliminar duplicados confirmados
```

---

## 🔑 Resumen Ejecutivo para el Equipo

| Pregunta | Respuesta |
|---|---|
| **¿Qué herramienta usar ahora?** | `asyncio` — sin instalación extra, corre en local |
| **¿Para qué sirve PyPubSub?** | Para aprender el patrón pub/sub de forma simple |
| **¿Cuándo usar Kafka?** | Cuando el volumen supere 100K eventos/día |
| **¿El código de Bronze cambia?** | ❌ No — el bus solo alimenta Bronze con el mismo formato |
| **¿Qué es micro-batch?** | Guardar N eventos juntos cada X segundos (no uno a uno) |
| **¿Cómo se conecta un ecommerce real?** | Via HTTP POST a `api_receiver.py` (FastAPI) |

---

*Manual Fase 2 — Bus de Eventos para Pipeline Fintech*
*Basado en la arquitectura Medallion de Databricks*
*Versión 1.0 — Abril 2026*
```

