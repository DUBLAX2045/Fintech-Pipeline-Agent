"""
ecommerce_api.py — API genérica de ingesta de datos

FastAPI en puerto 8001 que actúa como punto de entrada genérico al bus de eventos.
Acepta cualquier tipo de mensaje (eventos, métricas, registros, logs, alertas)
y los envía al api_receiver (puerto 8000) donde el BronzeConsumer los procesa.

Mientras el e-commerce real no esté disponible, este API actúa como su sustituto
y como generador de datos de prueba para el pipeline completo.

Arranque:
    uvicorn src.bus.ecommerce_api:app --port 8001 --reload

Luego visita http://localhost:8001/docs para el Swagger UI interactivo.

Flujo completo con ambos servidores:
    Ingest API (8001) ──POST /ingest──► Receiver (8000) ──► Bus ──► Bronze ──► Silver/Gold

Tipos de mensaje soportados:
    event   → Acción de usuario (pago, compra, transferencia...)
    metric  → Valor numérico agregado (conversion_rate, daily_gmv...)
    record  → Estado de una entidad (perfil de usuario, producto...)
    log     → Entrada de diagnóstico técnico
    alert   → Condición de negocio que requiere atención

Endpoints principales:
    POST /ingest              → Ingesta un mensaje de cualquier tipo
    POST /ingest/batch        → Ingesta múltiples mensajes en una sola llamada
    POST /simulate            → Genera N mensajes sintéticos en background
    GET  /stats               → Contadores por tipo de mensaje
    GET  /health              → Estado de la conexión con el receiver
    GET  /schema              → Documentación del envelope estándar

    Atajos de dominio (wrappers sobre /ingest):
    POST /events/payment      → PAYMENT_MADE
    POST /events/purchase     → PURCHASE_MADE
    POST /events/transfer     → TRANSFER_SENT
    POST /events/failure      → PAYMENT_FAILED
    POST /metrics/snapshot    → Métrica de negocio
    POST /records/user        → Registro de usuario
"""

import os
import random
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import requests
from fastapi import BackgroundTasks, FastAPI, HTTPException, Query
from pydantic import BaseModel, field_validator

from src.bus.message_schema import (
    MSG_TYPES,
    crear_mensaje,
    generar_evento_fintech,
    generar_log,
    generar_metrica,
    generar_registro_usuario,
)

# ── App ────────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="Fintech Ingest API",
    description=(
        "Punto de entrada genérico al bus de eventos del pipeline fintech. "
        "Acepta eventos, métricas, registros, logs y alertas. "
        "Consulta GET /schema para ver el envelope estándar."
    ),
    version="2.0",
)

# ── Configuración ──────────────────────────────────────────────────────────────

RECEIVER_BASE_URL = os.getenv("FINTECH_RECEIVER_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
RECEIVER_INGEST_URL = os.getenv("FINTECH_RECEIVER_INGEST_URL", f"{RECEIVER_BASE_URL}/ingest")
RECEIVER_HEALTH_URL = os.getenv("FINTECH_RECEIVER_HEALTH_URL", f"{RECEIVER_BASE_URL}/health")

# Stats por tipo de mensaje (no solo "events")
_stats: Dict[str, int] = {t: 0 for t in MSG_TYPES}
_stats.update({"errores": 0, "simulaciones_activas": 0, "batch_total": 0})


# ── Modelos Pydantic ───────────────────────────────────────────────────────────

class MensajeEntrada(BaseModel):
    """
    Envelope estándar para todos los mensajes del bus.
    Si no se proporciona message_id o timestamp, se generan automáticamente.
    """
    msg_type: str
    source: str
    data: Dict[str, Any]
    schema_version: str = "1.0"
    message_id: Optional[str] = None
    timestamp: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None

    @field_validator("msg_type")
    @classmethod
    def validar_tipo(cls, v: str) -> str:
        if v not in MSG_TYPES:
            raise ValueError(f"msg_type '{v}' inválido. Valores válidos: {sorted(MSG_TYPES)}")
        return v

    def to_envelope(self) -> dict:
        """Convierte a dict completo con campos auto-generados si faltan."""
        return {
            "msg_type": self.msg_type,
            "source": self.source,
            "schema_version": self.schema_version,
            "message_id": self.message_id or str(uuid.uuid4()),
            "timestamp": self.timestamp or datetime.now(timezone.utc).isoformat(),
            "data": self.data,
            "metadata": self.metadata or {},
        }


class BatchEntrada(BaseModel):
    mensajes: List[MensajeEntrada]

    @field_validator("mensajes")
    @classmethod
    def validar_tamanio(cls, v: list) -> list:
        if len(v) > 500:
            raise ValueError("El batch no puede superar 500 mensajes por llamada.")
        return v


# ── Helpers de envío ───────────────────────────────────────────────────────────

def _enviar(mensaje: dict) -> bool:
    """POST al api_receiver (/ingest). Retorna True si fue aceptado (202)."""
    try:
        r = requests.post(RECEIVER_INGEST_URL, json=mensaje, timeout=5)
        if r.status_code == 202:
            tipo = mensaje.get("msg_type", "event")
            _stats[tipo] = _stats.get(tipo, 0) + 1
            return True
        _stats["errores"] += 1
        return False
    except requests.exceptions.ConnectionError:
        _stats["errores"] += 1
        return False
    except Exception:
        _stats["errores"] += 1
        return False


def _run_simulacion(n: int, tps: float, msg_type: str, subtipo: Optional[str]) -> None:
    """Genera N mensajes sintéticos del tipo indicado y los envía al receiver."""
    generadores = {
        "event": lambda: generar_evento_fintech(subtype=subtipo),
        "metric": lambda: generar_metrica(),
        "record": lambda: generar_registro_usuario(),
        "log": lambda: generar_log(),
        "alert": lambda: crear_mensaje(
            "alert", "simulador",
            {"title": "Umbral superado", "severity": random.choice(["low", "medium", "high"]),
             "threshold": random.randint(100, 10000), "current": random.randint(100, 20000)}
        ),
    }
    gen = generadores.get(msg_type, generadores["event"])
    intervalo = 1.0 / tps
    enviados = 0

    for i in range(n):
        if _enviar(gen()):
            enviados += 1
        if (i + 1) % 50 == 0:
            print(f"   [IngestAPI] Simulacion {msg_type}: {i+1}/{n} enviados")
        time.sleep(intervalo)

    _stats["simulaciones_activas"] = max(0, _stats["simulaciones_activas"] - 1)
    print(f"   [IngestAPI] Simulacion completada: {enviados}/{n} mensajes tipo '{msg_type}'")


# ── Endpoints principales ──────────────────────────────────────────────────────

@app.get("/", tags=["Info"])
def root():
    return {
        "servicio": "Fintech Ingest API",
        "version": "2.0",
        "descripcion": "Punto de entrada generico al bus de eventos. Acepta events, metrics, records, logs, alerts.",
        "receptor": RECEIVER_INGEST_URL,
        "tipos_soportados": sorted(MSG_TYPES),
        "endpoints": {
            "POST /ingest": "Ingesta un mensaje de cualquier tipo",
            "POST /ingest/batch": "Ingesta hasta 500 mensajes en una sola llamada",
            "POST /simulate": "Genera N mensajes sinteticos en background",
            "GET /schema": "Documentacion del envelope estandar",
            "GET /stats": "Contadores por tipo de mensaje",
            "GET /health": "Estado de conexion con el receiver",
            "GET /docs": "Swagger UI interactivo",
        },
    }


@app.get("/schema", tags=["Info"])
def schema():
    """Devuelve la documentación del envelope estándar."""
    return {
        "envelope_estandar": {
            "msg_type": f"string — uno de: {sorted(MSG_TYPES)}",
            "source": "string — sistema de origen (ecommerce, crm, mobile_app...)",
            "schema_version": "string — version del schema (default: '1.0')",
            "message_id": "string (UUID) — auto-generado si no se provee",
            "timestamp": "string (ISO 8601 UTC) — auto-generado si no se provee",
            "data": "object — payload especifico del tipo (estructura libre)",
            "metadata": "object — contexto tecnico opcional (device, ip, canal...)",
        },
        "tipos": {
            "event": "Accion puntual de usuario: pago, compra, transferencia, registro...",
            "metric": "Valor numerico agregado: conversion_rate, daily_gmv, avg_ticket...",
            "record": "Estado de una entidad: perfil de usuario, producto, cuenta...",
            "log": "Entrada de diagnostico tecnico: error, warning, info...",
            "alert": "Condicion de negocio: fraude detectado, umbral superado, SLA...",
        },
        "ejemplo_event": {
            "msg_type": "event",
            "source": "ecommerce",
            "data": {
                "subtype": "PAYMENT_MADE",
                "user_id": "user_042",
                "amount": 150000,
                "currency": "COP",
                "merchant": "Rappi",
            },
        },
        "ejemplo_metric": {
            "msg_type": "metric",
            "source": "analytics_job",
            "data": {
                "name": "conversion_rate",
                "value": 0.87,
                "unit": "ratio",
                "period": "daily",
                "date": "2026-04-21",
            },
        },
    }


@app.post("/ingest", status_code=202, tags=["Ingesta"])
def ingestar_mensaje(mensaje: MensajeEntrada):
    """
    Punto de entrada genérico. Acepta cualquier tipo de mensaje del bus.

    El receiver lo encola en el EventBus y el BronzeConsumer lo escribe
    a Parquet. Bronze redirige a la sub-carpeta correcta según `msg_type`.

    Ejemplo mínimo:
        {
            "msg_type": "event",
            "source": "ecommerce",
            "data": {"subtype": "PAYMENT_MADE", "amount": 50000}
        }
    """
    envelope = mensaje.to_envelope()
    ok = _enviar(envelope)
    if not ok:
        raise HTTPException(
            status_code=503,
            detail=f"No se pudo conectar con el receiver en {RECEIVER_INGEST_URL}. "
                   "¿Está corriendo uvicorn src.bus.api_receiver:app --port 8000?",
        )
    return {
        "status": "accepted",
        "msg_type": envelope["msg_type"],
        "message_id": envelope["message_id"],
        "source": envelope["source"],
    }


@app.post("/ingest/batch", status_code=202, tags=["Ingesta"])
def ingestar_batch(batch: BatchEntrada):
    """
    Ingesta múltiples mensajes en una sola llamada HTTP.

    Acepta hasta 500 mensajes por request. Los tipos pueden mezclarse
    libremente en el mismo batch. Retorna un resumen de aceptados/rechazados.
    """
    enviados = 0
    errores = 0
    por_tipo: Dict[str, int] = {}

    for mensaje in batch.mensajes:
        envelope = mensaje.to_envelope()
        if _enviar(envelope):
            enviados += 1
            por_tipo[envelope["msg_type"]] = por_tipo.get(envelope["msg_type"], 0) + 1
        else:
            errores += 1

    _stats["batch_total"] += 1
    return {
        "status": "batch_procesado",
        "total": len(batch.mensajes),
        "enviados": enviados,
        "errores": errores,
        "por_tipo": por_tipo,
    }


@app.post("/simulate", status_code=202, tags=["Ingesta"])
def simular(
    background_tasks: BackgroundTasks,
    msg_type: str = Query("event", description=f"Tipo de mensaje a generar: {sorted(MSG_TYPES)}"),
    n: int = Query(50, ge=1, le=2000, description="Número de mensajes a generar"),
    tps: float = Query(2.0, ge=0.1, le=20.0, description="Mensajes por segundo"),
    subtipo: Optional[str] = Query(
        None,
        description="Para msg_type=event: subtype específico (PAYMENT_MADE, PURCHASE_MADE, etc.)"
    ),
):
    """
    Genera N mensajes sintéticos en background y los envía al receiver.

    Útil para probar el pipeline sin el e-commerce real.
    El progreso se muestra en la consola del servidor.

    Ejemplos:
        POST /simulate?msg_type=event&n=200&tps=5
        POST /simulate?msg_type=metric&n=50&tps=1
        POST /simulate?msg_type=event&n=100&subtipo=PAYMENT_FAILED
    """
    if msg_type not in MSG_TYPES:
        raise HTTPException(
            status_code=422,
            detail=f"msg_type '{msg_type}' inválido. Válidos: {sorted(MSG_TYPES)}"
        )
    _stats["simulaciones_activas"] += 1
    background_tasks.add_task(_run_simulacion, n, tps, msg_type, subtipo)
    return {
        "status": "simulacion_iniciada",
        "msg_type": msg_type,
        "mensajes_programados": n,
        "mensajes_por_segundo": tps,
        "subtipo": subtipo,
        "receptor": RECEIVER_INGEST_URL,
        "nota": "Corriendo en background. Consulta GET /stats para ver el progreso.",
    }


# ── Atajos de dominio (wrappers sobre /ingest) ─────────────────────────────────
# Permiten llamadas simples sin construir el envelope completo.
# Útiles para integración rápida desde el e-commerce real.

@app.post("/events/payment", status_code=202, tags=["Eventos Fintech"])
def evento_pago(user_id: Optional[str] = None, monto: Optional[float] = None):
    """Atajo: envía un evento PAYMENT_MADE."""
    msg = generar_evento_fintech("PAYMENT_MADE", user_id, monto)
    ok = _enviar(msg)
    return {"status": "accepted" if ok else "error", "msg_type": "event",
            "subtype": "PAYMENT_MADE", "message_id": msg["message_id"]}


@app.post("/events/purchase", status_code=202, tags=["Eventos Fintech"])
def evento_compra(user_id: Optional[str] = None, monto: Optional[float] = None):
    """Atajo: envía un evento PURCHASE_MADE."""
    msg = generar_evento_fintech("PURCHASE_MADE", user_id, monto)
    ok = _enviar(msg)
    return {"status": "accepted" if ok else "error", "msg_type": "event",
            "subtype": "PURCHASE_MADE", "message_id": msg["message_id"]}


@app.post("/events/transfer", status_code=202, tags=["Eventos Fintech"])
def evento_transferencia(user_id: Optional[str] = None, monto: Optional[float] = None):
    """Atajo: envía un evento TRANSFER_SENT."""
    msg = generar_evento_fintech("TRANSFER_SENT", user_id, monto)
    ok = _enviar(msg)
    return {"status": "accepted" if ok else "error", "msg_type": "event",
            "subtype": "TRANSFER_SENT", "message_id": msg["message_id"]}


@app.post("/events/failure", status_code=202, tags=["Eventos Fintech"])
def evento_pago_fallido(user_id: Optional[str] = None):
    """Atajo: envía un evento PAYMENT_FAILED."""
    msg = generar_evento_fintech("PAYMENT_FAILED", user_id)
    ok = _enviar(msg)
    return {"status": "accepted" if ok else "error", "msg_type": "event",
            "subtype": "PAYMENT_FAILED", "message_id": msg["message_id"]}


@app.post("/metrics/snapshot", status_code=202, tags=["Métricas"])
def metrica_snapshot(nombre: Optional[str] = None, valor: Optional[float] = None):
    """Atajo: envía una métrica de negocio. Si no se dan valores, se generan sintéticos."""
    msg = generar_metrica(nombre)
    if valor is not None:
        msg["data"]["value"] = valor
    ok = _enviar(msg)
    return {"status": "accepted" if ok else "error", "msg_type": "metric",
            "name": msg["data"]["name"], "message_id": msg["message_id"]}


@app.post("/records/user", status_code=202, tags=["Registros"])
def registro_usuario(user_id: Optional[str] = None):
    """Atajo: envía un registro de estado de usuario."""
    msg = generar_registro_usuario(user_id)
    ok = _enviar(msg)
    return {"status": "accepted" if ok else "error", "msg_type": "record",
            "entity_id": msg["data"]["entity_id"], "message_id": msg["message_id"]}


# ── Monitoreo ──────────────────────────────────────────────────────────────────

@app.get("/stats", tags=["Monitoreo"])
def stats():
    """Contadores de mensajes enviados, desglosados por tipo."""
    total = sum(v for k, v in _stats.items() if k in MSG_TYPES)
    return {**_stats, "total_enviados": total}


@app.get("/health", tags=["Monitoreo"])
def health():
    """Verifica la conexión con el api_receiver."""
    try:
        r = requests.get(RECEIVER_HEALTH_URL, timeout=3)
        receiver_ok = r.status_code == 200
        receiver_data = r.json() if receiver_ok else {"error": r.status_code}
    except Exception as e:
        receiver_ok = False
        receiver_data = {"error": str(e)}

    return {
        "ingest_api": "ok",
        "receiver_conectado": receiver_ok,
        "receiver_8000": receiver_data,
        "stats": _stats,
    }
