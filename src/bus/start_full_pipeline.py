import argparse
import asyncio
import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from src.bus.dataset_producer import DatasetProducer
from src.bus.event_bus_asyncio import BronzeConsumer, EventBus
from src.bus.pipeline_trigger import PipelineTrigger


async def main(
    delay: float,
    batch_size: int,
    flush_interval: int,
    auto_trigger: bool,
    trigger_interval: int,
    loop_dataset: bool,
) -> None:

    print("\n" + "=" * 65)
    print("  PIPELINE INTEGRADO — DATASET → BUS → BRONZE → SILVER → GOLD")
    print("=" * 65)
    print(f"  Velocidad:            {1/delay:.0f} eventos/s" if delay > 0 else "  Velocidad: maxima")
    print(f"  Batch size Bronze:    {batch_size} eventos")
    print(f"  Flush interval:       {flush_interval}s")
    print(f"  Auto-trigger S/G:     {'SI' if auto_trigger else 'NO'}")
    print(f"  Loop dataset:         {'SI' if loop_dataset else 'NO'}")
    print("=" * 65 + "\n")

    bus = EventBus(maxsize=500)
    trigger = PipelineTrigger(
        auto_trigger=auto_trigger,
        min_intervalo_segundos=trigger_interval
    )

    consumer = BronzeConsumer(
        bus,
        batch_size=batch_size,
        flush_interval_segundos=flush_interval,
        trigger=trigger,
    )

    producer = DatasetProducer(bus, delay_segundos=delay, loop=loop_dataset)

    # 🚀 Iniciar consumidor en background
    consumer_task = asyncio.create_task(consumer.start())

    # 🚀 Ejecutar productor
    await producer.start()

    # 🧠 Esperar a que la cola se vacíe
    print("\n[Pipeline] Productor terminado. Vaciando cola...")
    while bus.pending > 0:
        await asyncio.sleep(0.5)

    # 🧠 Forzar último flush
    await asyncio.sleep(flush_interval + 2)

    # 🛑 Detener consumer
    consumer.stop()
    try:
        await asyncio.wait_for(consumer_task, timeout=10.0)
    except asyncio.TimeoutError:
        print("[Pipeline] Timeout esperando cierre del consumer")

    # 🔥 SOLUCIÓN CLAVE: siempre esperar al trigger
    if auto_trigger:
        print("\n[Pipeline] 🔥 Asegurando ejecución final de Silver/Gold...")

        ejecutado = trigger.trigger(force=True)

        if ejecutado:
            print("[Pipeline] ✔ Se lanzó un nuevo trigger")
        else:
            print("[Pipeline] ✔ Ya había un trigger en ejecución")

        # 🔥 CLAVE: SIEMPRE esperar (aunque no haya lanzado uno nuevo)
        print("[Pipeline] ⏳ Esperando finalización de Silver/Gold...")
        completado = trigger.wait_for_completion(timeout=300)

        if not completado:
            print("[Pipeline] ❌ ERROR: Silver/Gold no terminó a tiempo")
        else:
            print("[Pipeline] ✅ Silver/Gold completado correctamente")

    else:
        print("\n[Pipeline] Trigger omitido (--no-trigger activo)")
        print("[Pipeline] Ejecuta manualmente: python src/run_pipeline.py --desde-silver")

    # 📊 Resumen final
    bus_stats = bus.stats()
    consumer_stats = consumer.stats()
    trigger_stats = trigger.stats()

    print("\n" + "=" * 65)
    print("  PIPELINE COMPLETADO")
    print(f"  Eventos publicados:   {bus_stats['total_published']}")
    print(f"  Eventos en Bronze:    {consumer_stats['eventos_guardados']}")
    print(f"  Batches Parquet:      {consumer_stats['batches_guardados']}")
    print(f"  Triggers Silver/Gold: {trigger_stats['runs_completados']}")
    print(f"  Eventos en cola:      {bus_stats['pending_in_queue']} (deben ser 0)")
    print("=" * 65)

    print("\n  Listo para Fase 3 — Agente Inteligente\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Pipeline integrado: DatasetProducer → Bus → Bronze → Silver → Gold"
    )

    parser.add_argument(
        "--delay",
        type=float,
        default=0.05,
        help="Segundos entre eventos (0 = máximo)"
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=50
    )
    parser.add_argument(
        "--flush-interval",
        type=int,
        default=15
    )
    parser.add_argument(
        "--no-trigger",
        action="store_true"
    )
    parser.add_argument(
        "--trigger-interval",
        type=int,
        default=30
    )
    parser.add_argument(
        "--loop",
        action="store_true"
    )

    args = parser.parse_args()

    asyncio.run(
        main(
            delay=args.delay,
            batch_size=args.batch_size,
            flush_interval=args.flush_interval,
            auto_trigger=not args.no_trigger,
            trigger_interval=args.trigger_interval,
            loop_dataset=args.loop,
        )
    )