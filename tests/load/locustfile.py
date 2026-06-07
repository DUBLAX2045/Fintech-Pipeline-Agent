from __future__ import annotations

import os
import random
import uuid
from datetime import datetime, timezone
from typing import Any

from locust import HttpUser, between, task


EVENT_TYPES = [
    "PAYMENT_MADE",
    "PURCHASE_MADE",
    "TRANSFER_SENT",
    "PAYMENT_FAILED",
    "MONEY_ADDED",
]


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        value = default
    return max(minimum, min(value, maximum))


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _event_message() -> dict[str, Any]:
    event_type = random.choice(EVENT_TYPES)
    amount = None if event_type == "PAYMENT_FAILED" else random.randint(20_000, 800_000)
    user_id = f"locust_user_{random.randint(1, 2_000):04d}"

    return {
        "msg_type": "event",
        "source": "locust_load_test",
        "schema_version": "1.0",
        "message_id": str(uuid.uuid4()),
        "timestamp": _timestamp(),
        "data": {
            "subtype": event_type,
            "user_id": user_id,
            "amount": amount,
            "currency": "COP",
            "merchant": random.choice(["Rappi", "Exito", "Falabella", "D1", "Netflix"]),
            "category": random.choice(["food", "retail", "services", "transport"]),
            "status": "FAILED" if event_type == "PAYMENT_FAILED" else "SUCCESS",
        },
        "metadata": {
            "device": random.choice(["mobile", "web", "tablet"]),
            "channel": random.choice(["app", "web", "pos"]),
            "ip": f"192.168.{random.randint(0, 254)}.{random.randint(1, 254)}",
        },
    }


def _metric_message() -> dict[str, Any]:
    return {
        "msg_type": "metric",
        "source": "locust_load_test",
        "schema_version": "1.0",
        "message_id": str(uuid.uuid4()),
        "timestamp": _timestamp(),
        "data": {
            "name": random.choice(["conversion_rate", "daily_gmv", "avg_ticket"]),
            "value": round(random.uniform(0.1, 1_000_000.0), 4),
            "unit": "ratio",
            "period": "realtime",
        },
        "metadata": {"load_test": True},
    }


class FintechIngestApiUser(HttpUser):
    host = "http://127.0.0.1:8001"
    wait_time = between(0.1, 0.8)

    @task(8)
    def ingest_event(self):
        self.client.post("/ingest", json=_event_message(), name="POST /ingest event")

    @task(2)
    def ingest_metric(self):
        self.client.post("/ingest", json=_metric_message(), name="POST /ingest metric")

    @task(3)
    def ingest_batch(self):
        batch_size = _env_int("FINTECH_LOAD_BATCH_SIZE", default=10, minimum=1, maximum=500)
        messages = [
            _event_message() if index % 4 else _metric_message()
            for index in range(batch_size)
        ]
        self.client.post(
            "/ingest/batch",
            json={"mensajes": messages},
            name=f"POST /ingest/batch x{batch_size}",
        )

    @task(2)
    def domain_shortcut_payment(self):
        params = {
            "user_id": f"locust_user_{random.randint(1, 2_000):04d}",
            "monto": random.randint(20_000, 800_000),
        }
        self.client.post("/events/payment", params=params, name="POST /events/payment")

    @task(1)
    def read_health(self):
        with self.client.get("/health", catch_response=True, name="GET /health") as response:
            if response.status_code != 200:
                response.failure(f"health devolvio HTTP {response.status_code}")
                return

            try:
                payload = response.json()
            except ValueError:
                response.failure("health no devolvio JSON valido")
                return

            require_receiver = os.getenv("FINTECH_LOAD_REQUIRE_RECEIVER", "1") != "0"
            if require_receiver and not payload.get("receiver_conectado"):
                response.failure("api_receiver no esta conectado en puerto 8000")

    @task(1)
    def read_stats(self):
        self.client.get("/stats", name="GET /stats")
