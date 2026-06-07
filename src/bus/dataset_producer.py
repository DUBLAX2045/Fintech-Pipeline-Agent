"""
DatasetProducer: publica los 2000 eventos reales del JSON en el bus de eventos.

Permite reproducir el dataset como si llegaran en streaming, sin depender
del e-commerce real. Cada evento se publica como dict crudo (mismo formato
que fintech_events_v4.json) para que BronzeConsumer lo procese sin cambios.

Uso directo (modo standalone, sin API):
    python src/bus/start_full_pipeline.py

Uso embebido (desde código):
    producer = DatasetProducer(bus, delay_segundos=0.05)
    await producer.start()
"""

import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
from src.bus.event_bus_asyncio import EventBus


class DatasetProducer:
    """
    Lee fintech_events_v4.json y publica cada evento en el EventBus.

    A diferencia de EcommerceProducer (que genera datos sintéticos con Faker),
    este productor usa los 2000 eventos reales del dataset para pruebas de integración.

    Args:
        bus:            EventBus compartido con el BronzeConsumer.
        json_path:      Ruta al JSON del dataset (relativa a la raíz del proyecto).
        delay_segundos: Pausa entre eventos. 0.05 = 20 eventos/s, 0.01 = 100 eventos/s.
        loop:           Si True, repite el dataset indefinidamente (modo stress test).
    """

    def __init__(
        self,
        bus: EventBus,
        json_path: str = "data/raw/fintech_events_v4.json",
        delay_segundos: float = 0.05,
        loop: bool = False,
    ):
        self.bus = bus
        self.json_path = json_path
        self.delay = delay_segundos
        self.loop = loop
        self._count = 0
        self._running = False

    async def start(self) -> None:
        """Publica todos los eventos del dataset en el bus y retorna al terminar."""
        self._running = True
        eventos = self._cargar_dataset()

        velocidad = 1 / self.delay if self.delay > 0 else float("inf")
        print(f"\n[DatasetProducer] {len(eventos)} eventos cargados")
        print(f"[DatasetProducer] Velocidad: {velocidad:.0f} eventos/s | Loop: {self.loop}\n")

        ronda = 0
        while self._running:
            ronda += 1
            if ronda > 1:
                print(f"[DatasetProducer] Ronda {ronda} — {len(eventos)} eventos")

            for evento in eventos:
                if not self._running:
                    break

                # Publica el dict crudo directamente — BronzeConsumer lo aplana igual
                # que si viniera del JSON estático. No se necesita wrapper FintechEvent.
                await self.bus.publish(evento)
                self._count += 1

                if self._count % 200 == 0:
                    print(
                        f"   [DatasetProducer] {self._count} eventos publicados "
                        f"| Cola: {self.bus.pending} esperando"
                    )

                if self.delay > 0:
                    await asyncio.sleep(self.delay)

            if not self.loop:
                break

        self._running = False
        print(f"\n[DatasetProducer] Completado. Total publicados: {self._count}")

    def stop(self) -> None:
        self._running = False

    @property
    def count(self) -> int:
        return self._count

    def _cargar_dataset(self) -> list:
        ruta = Path(self.json_path)
        if not ruta.is_absolute():
            base = Path(__file__).parent.parent.parent
            ruta = base / self.json_path
        if not ruta.exists():
            raise FileNotFoundError(f"Dataset no encontrado: {ruta}")
        with open(ruta, encoding="utf-8") as f:
            return json.load(f)
