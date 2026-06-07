from __future__ import annotations

import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _make_fintech_event(
    event_id: str = "evt-1",
    user_id: str = "user_1",
    event: str = "PAYMENT_MADE",
    status: str = "SUCCESS",
    amount: float | None = 100000.0,
    timestamp: str = "2026-01-01T10:00:00Z",
    city: str = "Bogota",
    segment: str = "premium",
) -> dict:
    return {
        "source": "test.source",
        "detailType": "event",
        "detail": {
            "id": event_id,
            "event": event,
            "version": "1.0",
            "eventType": event.lower(),
            "transactionType": event.lower(),
            "eventEntity": "USER",
            "eventStatus": status,
            "payload": {
                "userId": user_id,
                "name": "Test User",
                "age": 30,
                "email": f"{user_id}@example.com",
                "city": city,
                "segment": segment,
                "timestamp": timestamp,
                "accountId": f"acc_{user_id}",
                "amount": amount,
                "currency": "COP",
                "merchant": "Rappi",
                "category": "food",
                "paymentMethod": "wallet",
                "installments": 1,
                "balanceBefore": 500000,
                "balanceAfter": 400000,
                "initialBalance": None,
                "status": None,
                "source": None,
                "location": {
                    "city": city,
                    "country": "Colombia",
                },
                "updatedFields": {},
            },
            "metadata": {
                "device": "mobile",
                "os": "android",
                "ip": "192.168.1.10",
                "channel": "app",
            },
        },
    }


@pytest.fixture
def make_fintech_event():
    return _make_fintech_event
