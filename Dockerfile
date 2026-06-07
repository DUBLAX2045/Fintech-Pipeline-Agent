# ── Stage: imagen base ───────────────────────────────────────────────────────
FROM python:3.12-slim

LABEL description="Fintech Pipeline V3 — Bronze/Silver/Gold + Agente IA"

# ── Dependencias de sistema mínimas ──────────────────────────────────────────
# gcc: compila extensiones C de algunas libs (duckdb, pyarrow)
# libgomp1: OpenMP requerido por numpy/scikit en slim
# curl: healthcheck del dashboard
RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc \
        libgomp1 \
        curl \
    && rm -rf /var/lib/apt/lists/*

# ── Directorio de trabajo ─────────────────────────────────────────────────────
WORKDIR /app

# ── Dependencias Python
# Se copia requirements.txt primero para aprovechar la caché de capas Docker.
# Si requirements.txt no cambia, esta capa se reutiliza aunque el código cambie.
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir -r requirements.txt

# ── Código fuente ─────────────────────────────────────────────────────────────
COPY src/ ./src/
COPY .streamlit/ ./.streamlit/
COPY .env.example ./.env.example

# ── Dataset fuente (solo raw — bronze/silver/gold se montan como volumen) ────
COPY data/raw/ ./data/raw/

# ── Directorios de runtime (se sobreescriben por volúmenes en docker-compose) ─
RUN mkdir -p \
        data/bronze \
        data/silver \
        data/gold \
        outputs/charts \
        outputs/reports \
        logs

# ── Variable de entorno para que Python no genere .pyc dentro del contenedor ─
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# ── Puerto por defecto (Streamlit dashboard) ──────────────────────────────────
EXPOSE 8501

# ── Healthcheck: verifica que los módulos del agente importan correctamente ───
HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
    CMD python -c "import src.agent.schema; import src.gold.pipeline_gold" || exit 1

# ── Entrypoint por defecto: dashboard ────────────────────────────────────────
CMD ["python", "-m", "streamlit", "run", "src/agent/app.py", \
     "--server.port=8501", \
     "--server.address=0.0.0.0", \
     "--server.headless=true", \
     "--server.fileWatcherType=none"]
