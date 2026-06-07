"""
pipeline_trigger.py — Dispara Silver → Gold → S3 tras cada batch Bronze.

Flujo segun el diagrama:
  Nuevo batch Bronze guardado
    → Silver (limpieza y enriquecimiento)
    → Gold   (vision 360, metricas)
    → S3     (subida Parquets para Databricks auto-deteccion)

Incluye throttling: no ejecuta mas de una vez cada min_intervalo_segundos.
Corre en thread separado para no bloquear el event loop de asyncio.
"""

import threading
from collections.abc import Callable
from datetime import datetime

PipelineRunner = Callable[[], object]
S3Verifier = Callable[[], dict]
ParquetUploader = Callable[[str, str], dict]


class PipelineTrigger:
    """
    Ejecuta Silver → Gold → S3 en background tras cada batch Bronze.

    Args:
        auto_trigger:            Si False, trigger() siempre es no-op (para tests).
        min_intervalo_segundos:  Tiempo minimo entre ejecuciones (throttling).
        subir_s3:                Si True, sube Parquets a S3 tras cada ejecucion.
        *_runner / *_verifier:   Dependencias opcionales para pruebas e integracion.
    """

    def __init__(
        self,
        auto_trigger: bool = True,
        min_intervalo_segundos: int = 60,
        subir_s3: bool = True,
        silver_runner: PipelineRunner | None = None,
        gold_runner: PipelineRunner | None = None,
        s3_verifier: S3Verifier | None = None,
        parquet_uploader: ParquetUploader | None = None,
    ):
        self.auto_trigger = auto_trigger
        self.min_intervalo = min_intervalo_segundos
        self.subir_s3 = subir_s3
        self._silver_runner = silver_runner
        self._gold_runner = gold_runner
        self._s3_verifier = s3_verifier
        self._parquet_uploader = parquet_uploader
        self._last_run: datetime | None = None
        self._running = False
        self._lock = threading.Lock()
        self._done_event = threading.Event()
        self.runs_completados = 0
        self.errores = 0

    def trigger(self, force: bool = False) -> bool:
        """
        Lanza Silver → Gold → S3 si las condiciones lo permiten.

        Args:
            force: Ignora throttling. Util al final del pipeline o en pruebas.
        Returns:
            True si se lanzo la ejecucion, False si se salto.
        """
        if not self.auto_trigger and not force:
            return False

        ahora = datetime.now()
        with self._lock:
            if not force and self._last_run:
                segundos = (ahora - self._last_run).total_seconds()
                if segundos < self.min_intervalo:
                    print(f"   [Trigger] Saltando (ultimo hace {segundos:.0f}s, minimo {self.min_intervalo}s)")
                    return False
            if self._running:
                print("   [Trigger] Ya en ejecucion, se omite este trigger")
                return False
            self._running = True
            self._done_event.clear()

        thread = threading.Thread(target=self._ejecutar, daemon=False)
        thread.start()
        return True

    def wait_for_completion(self, timeout: int = 180) -> bool:
        """Bloquea hasta que el trigger termine. Retorna True si completo."""
        return self._done_event.wait(timeout=timeout)

    def _run_silver(self) -> None:
        if self._silver_runner:
            self._silver_runner()
            return
        from src.silver.pipeline_silver import ejecutar_pipeline_silver
        ejecutar_pipeline_silver()

    def _run_gold(self) -> None:
        if self._gold_runner:
            self._gold_runner()
            return
        from src.gold.pipeline_gold import ejecutar_pipeline_gold
        ejecutar_pipeline_gold()

    def _verificar_s3(self) -> dict:
        if self._s3_verifier:
            return self._s3_verifier()
        from src.ingesta.uploader_s3 import verificar_s3
        return verificar_s3()

    def _subir_parquets(self, local_folder: str, capa: str) -> dict:
        if self._parquet_uploader:
            return self._parquet_uploader(local_folder, capa)
        from src.ingesta.uploader_s3 import subir_parquets
        return subir_parquets(local_folder, capa)

    def _ejecutar(self) -> None:
        inicio = datetime.now()
        try:
            # ── Silver ──────────────────────────────────────────────────
            print("\n   [Trigger] Silver — iniciando...")
            self._run_silver()
            print("   [Trigger] Silver completado.")

            # ── Gold ─────────────────────────────────────────────────────
            print("   [Trigger] Gold — iniciando...")
            self._run_gold()
            print("   [Trigger] Gold completado.")

            # ── S3 (segun diagrama: Parquet → AWS S3 → auto-deteccion) ───
            if self.subir_s3:
                try:
                    diag = self._verificar_s3()
                    if diag["ok"]:
                        print("   [Trigger] Subiendo Silver y Gold a S3...")
                        self._subir_parquets("data/silver", "silver")
                        self._subir_parquets("data/gold",   "gold")
                        print("   [Trigger] S3 upload completado.")
                    else:
                        print(f"   [Trigger] S3 no disponible: {diag['error']} — omitiendo upload")
                except Exception as e_s3:
                    print(f"   [Trigger] Error S3 (no critico): {e_s3}")

            duracion = (datetime.now() - inicio).total_seconds()
            self.runs_completados += 1
            self._last_run = datetime.now()
            print(f"   [Trigger] Ciclo completo en {duracion:.1f}s (run #{self.runs_completados})\n")

        except Exception as e:
            self.errores += 1
            print(f"   [Trigger] ERROR: {e}")

        finally:
            self._running = False
            self._done_event.set()

    def stats(self) -> dict:
        return {
            "auto_trigger":      self.auto_trigger,
            "subir_s3":          self.subir_s3,
            "runs_completados":  self.runs_completados,
            "errores":           self.errores,
            "activo_ahora":      self._running,
            "ultimo_run":        self._last_run.isoformat() if self._last_run else None,
        }
