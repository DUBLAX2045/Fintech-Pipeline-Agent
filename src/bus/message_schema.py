"""
message_schema.py — Envelope estándar para todos los mensajes del bus.

Define el contrato de comunicación entre productores (e-commerce, CRM, apps)
y consumidores (Bronze, Silver, Gold). Cualquier fuente puede enviar datos al
bus siempre que respete este envelope; los consumidores inspeccionan `msg_type`
para decidir cómo procesar cada mensaje.

Envelope estándar:
    {
        "msg_type":       "event" | "metric" | "record" | "log" | "alert",
        "source":         "ecommerce" | "crm" | "mobile_app" | ...,
        "schema_version": "1.0",
        "message_id":     "<uuid>",
        "timestamp":      "<ISO 8601 UTC>",
        "data":           { ... },   # payload específico del tipo
        "metadata":       { ... }    # contexto técnico (device, ip, canal...)
    }

Tipos soportados
────────────────
    event   → Acción puntual de un usuario (pago, compra, transferencia...).
              La capa Silver transforma estos en métricas.
    metric  → Valor numérico agregado en un momento (conversion_rate, daily_gmv...).
              Gold los consolida directamente sin pasar por Silver.
    record  → Estado persistente de una entidad (perfil de usuario, catálogo...).
              Bronze los guarda; Silver hace upsert.
    log     → Entrada de diagnóstico técnico (error, warning, info).
              Se almacena en Bronze pero no sube a Gold.
    alert   → Condición de negocio que requiere atención (fraude, umbral, SLA).
              Bronze + cola de notificaciones (Fase 3+).
"""

import random
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from faker import Faker

fake = Faker("es_CO")

# ── Tipos válidos ──────────────────────────────────────────────────────────────

MSG_TYPES = {"event", "metric", "record", "log", "alert"}

# Subtipos de evento fintech (los que ya existían en el dataset)
FINTECH_EVENT_SUBTYPES = {
    "PAYMENT_MADE", "PURCHASE_MADE", "TRANSFER_SENT",
    "MONEY_ADDED", "PAYMENT_FAILED", "USER_REGISTERED", "USER_PROFILE_UPDATED",
}

# ── Factory principal ──────────────────────────────────────────────────────────

def crear_mensaje(
    msg_type: str,
    source: str,
    data: Dict[str, Any],
    metadata: Optional[Dict[str, Any]] = None,
    schema_version: str = "1.0",
) -> dict:
    """
    Construye un mensaje con el envelope estándar.

    Args:
        msg_type:  Tipo de mensaje ("event", "metric", "record", "log", "alert").
        source:    Sistema de origen ("ecommerce", "crm", "mobile_app", etc.).
        data:      Payload específico del tipo (estructura libre).
        metadata:  Contexto técnico opcional (device, ip, canal, versión...).

    Returns:
        Dict con el envelope completo listo para publicar en el bus.

    Ejemplo:
        msg = crear_mensaje(
            msg_type="metric",
            source="analytics_job",
            data={"name": "conversion_rate", "value": 0.87, "unit": "ratio"},
        )
    """
    if msg_type not in MSG_TYPES:
        raise ValueError(f"msg_type '{msg_type}' inválido. Válidos: {MSG_TYPES}")

    return {
        "msg_type": msg_type,
        "source": source,
        "schema_version": schema_version,
        "message_id": str(uuid.uuid4()),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "data": data,
        "metadata": metadata or {},
    }


# ── Detección de formato ───────────────────────────────────────────────────────

def es_legacy_fintech(mensaje: dict) -> bool:
    """
    True si el mensaje tiene la estructura legacy del dataset (detail.payload).
    Permite backward-compat: Bronze procesa los 2000 eventos del JSON original
    sin necesidad de migrarlos al envelope estándar.
    """
    return "detail" in mensaje and isinstance(mensaje.get("detail"), dict)


def extraer_tipo(mensaje: dict) -> str:
    """
    Extrae el tipo del mensaje de forma robusta.
    Maneja tanto el envelope estándar como el formato legacy fintech.
    """
    if es_legacy_fintech(mensaje):
        return "event"
    return mensaje.get("msg_type", "unknown")


def clasificar_mensajes(mensajes: list) -> Dict[str, list]:
    """
    Agrupa una lista de mensajes por tipo para procesamiento diferenciado en Bronze.

    Returns:
        Dict donde la clave es el tipo y el valor es la lista de mensajes.
        Ejemplo: {"fintech_legacy": [...], "metric": [...], "record": [...]}
    """
    grupos: Dict[str, list] = {}
    for msg in mensajes:
        if es_legacy_fintech(msg):
            clave = "fintech_legacy"
        else:
            clave = msg.get("msg_type", "unknown")
        grupos.setdefault(clave, []).append(msg)
    return grupos


# ── Aplanado genérico ──────────────────────────────────────────────────────────

def aplanar_mensaje_generico(mensaje: dict) -> dict:
    """
    Convierte un mensaje con envelope estándar en una fila plana para Parquet.
    El campo `data` se serializa como string JSON para preservar flexibilidad.

    Columns resultantes:
        msg_type, source, schema_version, message_id, timestamp, raw_data,
        + hasta 10 campos top-level de data (para queries simples sin deserializar)
    """
    import json

    data = mensaje.get("data", {})

    # Extraer hasta 10 campos simples del data al nivel raíz (facilita queries en Silver)
    campos_planos = {}
    for k, v in list(data.items())[:10]:
        if isinstance(v, (str, int, float, bool)) or v is None:
            campos_planos[f"data_{k}"] = v

    return {
        "msg_type": mensaje.get("msg_type", "unknown"),
        "source": mensaje.get("source", "unknown"),
        "schema_version": mensaje.get("schema_version", "1.0"),
        "message_id": mensaje.get("message_id", str(uuid.uuid4())),
        "timestamp": mensaje.get("timestamp", datetime.now(timezone.utc).isoformat()),
        "raw_data": json.dumps(data, ensure_ascii=False),
        **campos_planos,
    }


def aplanar_mensajes_genericos(mensajes: list):
    """Aplana una lista de mensajes genéricos en un DataFrame."""
    import pandas as pd
    filas = [aplanar_mensaje_generico(m) for m in mensajes]
    return pd.DataFrame(filas)


# ── Generadores de datos sintéticos por tipo ───────────────────────────────────
# Usados por ecommerce_api.py para pruebas sin e-commerce real.

_CIUDADES = ["Bogotá", "Medellín", "Cali", "Barranquilla", "Cartagena"]
_SEGMENTOS = ["premium", "student", "family", "young_professional"]
_MERCHANTS = ["Rappi", "Éxito", "Falabella", "Nike", "Netflix", "Spotify", "Amazon"]
_CATEGORIAS = ["food", "shopping", "transport", "entertainment", "utilities"]
_METODOS = ["debit_card", "credit_card", "wallet"]
_DISPOSITIVOS = ["mobile", "web", "tablet"]
_SISTEMAS = ["ios", "android", "windows"]
_CANALES = ["app", "web", "api"]
_FINTECH_EVENTS = ["PAYMENT_MADE", "PURCHASE_MADE", "TRANSFER_SENT", "MONEY_ADDED", "PAYMENT_FAILED"]


def generar_evento_fintech(
    subtype: Optional[str] = None,
    user_id: Optional[str] = None,
    monto: Optional[float] = None,
    source: str = "ecommerce.simulador",
) -> dict:
    """
    Genera un evento fintech en el envelope estándar.
    El campo `data` contiene la estructura que Bronze aplana con aplanar_evento().

    Nota: genera en formato LEGACY (detail.payload) para que sea 100% compatible
    con los 2000 eventos del dataset original y con la lógica Bronze existente.
    """
    subtype = subtype or random.choice(_FINTECH_EVENTS)
    uid = user_id or f"ecom_{random.randint(1, 500):03d}"
    ciudad = random.choice(_CIUDADES)
    ts = datetime.now(timezone.utc).isoformat()
    amount = monto or float(random.randint(5_000, 2_000_000))

    # Formato legacy para compatibilidad total con Bronze
    return {
        "msg_type": "event",
        "source": source,
        "schema_version": "1.0",
        "message_id": str(uuid.uuid4()),
        "timestamp": ts,
        # 'detail' mantiene la estructura original — Bronze usa aplanar_evento()
        "detail": {
            "id": str(uuid.uuid4()),
            "event": subtype,
            "version": "1.0",
            "eventType": subtype.lower(),
            "transactionType": subtype.lower(),
            "eventEntity": "USER",
            "eventStatus": "FAILED" if "FAILED" in subtype else "SUCCESS",
            "payload": {
                "userId": uid,
                "name": fake.name(),
                "age": random.randint(18, 65),
                "email": fake.email(),
                "city": ciudad,
                "segment": random.choice(_SEGMENTOS),
                "timestamp": ts,
                "accountId": f"acc_{uid}",
                "amount": amount,
                "currency": "COP",
                "merchant": random.choice(_MERCHANTS),
                "category": random.choice(_CATEGORIAS),
                "paymentMethod": random.choice(_METODOS),
                "installments": random.choice([1, 3, 6, 12]),
                "balanceBefore": random.randint(100_000, 5_000_000),
                "balanceAfter": random.randint(50_000, 4_000_000),
                "location": {"city": ciudad, "country": "Colombia"},
            },
            "metadata": {
                "device": random.choice(_DISPOSITIVOS),
                "os": random.choice(_SISTEMAS),
                "ip": f"192.168.{random.randint(1,254)}.{random.randint(1,254)}",
                "channel": random.choice(_CANALES),
            },
        },
    }


def generar_metrica(
    nombre: Optional[str] = None,
    source: str = "analytics_job",
) -> dict:
    """
    Genera un mensaje de tipo 'metric' con el envelope estándar.

    Ejemplos de métricas: conversion_rate, daily_gmv, avg_ticket, failure_rate.
    """
    nombres = ["conversion_rate", "daily_gmv", "avg_ticket", "failure_rate",
               "active_users", "new_registrations", "churn_rate"]
    nombre = nombre or random.choice(nombres)
    unidades = {"conversion_rate": "ratio", "daily_gmv": "COP", "avg_ticket": "COP",
                "failure_rate": "ratio", "active_users": "count",
                "new_registrations": "count", "churn_rate": "ratio"}

    return crear_mensaje(
        msg_type="metric",
        source=source,
        data={
            "name": nombre,
            "value": round(random.uniform(0.01, 1_000_000), 4),
            "unit": unidades.get(nombre, "unknown"),
            "period": "daily",
            "date": datetime.now().strftime("%Y-%m-%d"),
            "segment": random.choice(_SEGMENTOS + ["all"]),
        },
    )


def generar_registro_usuario(
    user_id: Optional[str] = None,
    source: str = "crm",
) -> dict:
    """
    Genera un mensaje de tipo 'record' representando el estado actual de un usuario.
    """
    uid = user_id or f"user_{random.randint(1, 1000):04d}"
    return crear_mensaje(
        msg_type="record",
        source=source,
        data={
            "entity_type": "user",
            "entity_id": uid,
            "name": fake.name(),
            "email": fake.email(),
            "city": random.choice(_CIUDADES),
            "segment": random.choice(_SEGMENTOS),
            "account_status": random.choice(["active", "inactive", "suspended"]),
            "created_at": fake.date_time_between(start_date="-2y").isoformat(),
            "updated_at": datetime.now().isoformat(),
        },
    )


def generar_log(
    nivel: str = "info",
    source: str = "pipeline",
) -> dict:
    """Genera un mensaje de tipo 'log'."""
    mensajes = {
        "info": "Pipeline ejecutado correctamente",
        "warning": "Latencia elevada en ExchangeRate API (>2s)",
        "error": "Fallo al escribir batch Bronze — reintentando",
    }
    return crear_mensaje(
        msg_type="log",
        source=source,
        data={
            "level": nivel,
            "message": mensajes.get(nivel, "Evento de log"),
            "component": random.choice(["bronze", "silver", "gold", "bus", "api"]),
            "duration_ms": random.randint(5, 5000),
        },
    )
