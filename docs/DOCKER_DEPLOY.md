# Dockerización y Despliegue — Fintech Pipeline v3

## Arquitectura de contenedores

```text
┌────────────────────────────────────────────────────────────┐
│                    docker-compose.yml                      │
│                                                            │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────┐   │
│  │  pipeline   │  │  dashboard  │  │   api/ecommerce  │   │
│  │ Bronze→Gold │  │  Streamlit  │  │  FastAPI :8000   │   │
│  │  one-shot   │  │   :8501     │  │  FastAPI :8001   │   │
│  └─────────────┘  └─────────────┘  └─────────────────┘   │
│                                                            │
│  ┌─────────────┐  ┌─────────────┐                        │
│  │     bus     │  │ dashboard-  │                        │
│  │  streaming  │  │    dev      │  ← perfil desarrollo   │
│  │  asyncio    │  │ (hot-reload)│    src/ montado live   │
│  └─────────────┘  └─────────────┘                        │
│                                                            │
│  Volúmenes compartidos: /data  /outputs  /logs             │
└────────────────────────────────────────────────────────────┘
          │                    │
          ▼                    ▼
     AWS S3 / Parquet     Ollama (HOST)
     Databricks           host.docker.internal:11434
```

> **Ollama corre en el HOST**, no en Docker, porque necesita acceso directo a
> GPU/CPU. Los contenedores se conectan a él via `host.docker.internal:11434`.

---

## Prerrequisitos

| Herramienta | Versión mínima | Verificación |
|-------------|---------------|--------------|
| Docker Desktop | 24.x | `docker --version` |
| Docker Compose v2 | incluido | `docker compose version` |
| Ollama | cualquiera | `ollama serve` |
| Modelo llama3.2 | — | `ollama pull llama3.2` |

---

## Archivos Docker del proyecto

Los tres archivos ya están creados en la raíz del proyecto.
A continuación se documenta su contenido actual de referencia.

---

## `.dockerignore`

```
# Entornos virtuales
venv/
.venv/
env/

# Caché Python
__pycache__/
*.py[cod]
*.pyo
*.pyd
.Python
.ruff_cache/

# Credenciales (NUNCA en la imagen)
.env
.env.*
!.env.example
*.pem
*.key
*.crt

# Tests (no necesarios en producción)
tests/
.pytest_cache/
.pytest_tmp/
.benchmarks/
mutants/
.coverage
htmlcov/
coverage.xml

# Datos generados (montados como volumen en runtime)
data/bronze/
data/silver/
data/gold/
*.parquet
*.db
*.duckdb

# Outputs de runtime
outputs/
logs/

# Documentación y materiales
docs/
docs/material/
notebooks/

# Git
.git/
.gitignore

# Editor y SO
.DS_Store
Thumbs.db
.idea/
.vscode/

# Docker (no incluirse a sí mismo)
Dockerfile
docker-compose.yml
.dockerignore
```

---

## `Dockerfile`

```dockerfile
FROM python:3.12-slim

LABEL description="Fintech Pipeline V3 — Bronze/Silver/Gold + Agente IA"

# gcc: compila extensiones C (duckdb, pyarrow)
# libgomp1: OpenMP requerido por numpy en slim
# curl: healthcheck del dashboard
RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc \
        libgomp1 \
        curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Dependencias primero — se cachean si requirements.txt no cambia
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir -r requirements.txt

# Código fuente
COPY src/ ./src/
COPY .streamlit/ ./.streamlit/
COPY .env.example ./.env.example

# Dataset fuente (solo raw — bronze/silver/gold se montan como volumen)
COPY data/raw/ ./data/raw/

# Directorios de runtime
RUN mkdir -p data/bronze data/silver data/gold \
             outputs/charts outputs/reports logs

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

EXPOSE 8501

HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
    CMD python -c "import src.agent.schema; import src.gold.pipeline_gold" || exit 1

CMD ["python", "-m", "streamlit", "run", "src/agent/app.py", \
     "--server.port=8501", \
     "--server.address=0.0.0.0", \
     "--server.headless=true", \
     "--server.fileWatcherType=none"]
```

---

## `docker-compose.yml`

```yaml
# FINTECH PIPELINE V3 — Orquestación de servicios
#
# Perfiles disponibles:
#   pipeline   → Bronze→Silver→Gold (one-shot)
#   dashboard  → Streamlit en :8501
#   api        → FastAPI receptor en :8000
#   ecommerce  → FastAPI generador en :8001
#   bus        → Streaming completo asyncio
#   dev        → Dashboard con hot-reload (src/ montado en vivo)

x-env-common: &env-common
  PYTHONUNBUFFERED: "1"
  PYTHONDONTWRITEBYTECODE: "1"
  OLLAMA_BASE_URL: "http://host.docker.internal:11434"
  OLLAMA_MODEL: "${OLLAMA_MODEL:-llama3.2}"

x-env-cloud: &env-cloud
  AWS_ACCESS_KEY_ID: "${AWS_ACCESS_KEY_ID}"
  AWS_SECRET_ACCESS_KEY: "${AWS_SECRET_ACCESS_KEY}"
  AWS_SESSION_TOKEN: "${AWS_SESSION_TOKEN:-}"
  AWS_REGION: "${AWS_REGION:-us-east-1}"
  AWS_BUCKET: "${AWS_BUCKET}"
  DATABRICKS_HOST: "${DATABRICKS_HOST}"
  DATABRICKS_TOKEN: "${DATABRICKS_TOKEN}"
  DATABRICKS_HTTP_PATH: "${DATABRICKS_HTTP_PATH}"
  DATABRICKS_CATALOG: "${DATABRICKS_CATALOG:-fintech_pipeline}"
  DATABRICKS_SCHEMA: "${DATABRICKS_SCHEMA:-fintech}"
  EXCHANGE_RATE_API_KEY: "${EXCHANGE_RATE_API_KEY:-}"

x-volumes-data: &volumes-data
  - ./data:/app/data
  - ./outputs:/app/outputs
  - ./logs:/app/logs

services:

  pipeline:
    build: { context: ., dockerfile: Dockerfile }
    image: fintech-pipeline:latest
    container_name: fintech-pipeline
    command: python src/run_pipeline.py
    environment: { <<: [*env-common, *env-cloud] }
    volumes: *volumes-data
    extra_hosts: ["host.docker.internal:host-gateway"]
    restart: "no"
    profiles: [pipeline]

  dashboard:
    build: { context: ., dockerfile: Dockerfile }
    image: fintech-pipeline:latest
    container_name: fintech-dashboard
    command: ["python", "-m", "streamlit", "run", "src/agent/app.py",
              "--server.port=8501", "--server.address=0.0.0.0",
              "--server.headless=true", "--server.fileWatcherType=none"]
    ports: ["8501:8501"]
    environment: { <<: [*env-common, *env-cloud] }
    volumes: *volumes-data
    extra_hosts: ["host.docker.internal:host-gateway"]
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8501/_stcore/health"]
      interval: 30s
      timeout: 10s
      start_period: 20s
      retries: 3
    profiles: [dashboard]

  api:
    build: { context: ., dockerfile: Dockerfile }
    image: fintech-pipeline:latest
    container_name: fintech-api
    command: ["python", "-m", "uvicorn", "src.bus.api_receiver:app",
              "--host=0.0.0.0", "--port=8000"]
    ports: ["8000:8000"]
    environment: { <<: *env-common }
    volumes: *volumes-data
    extra_hosts: ["host.docker.internal:host-gateway"]
    restart: unless-stopped
    profiles: [api]

  ecommerce:
    build: { context: ., dockerfile: Dockerfile }
    image: fintech-pipeline:latest
    container_name: fintech-ecommerce
    command: ["python", "-m", "uvicorn", "src.bus.ecommerce_api:app",
              "--host=0.0.0.0", "--port=8001"]
    ports: ["8001:8001"]
    environment: { <<: *env-common }
    volumes: *volumes-data
    extra_hosts: ["host.docker.internal:host-gateway"]
    restart: unless-stopped
    profiles: [ecommerce]

  bus:
    build: { context: ., dockerfile: Dockerfile }
    image: fintech-pipeline:latest
    container_name: fintech-bus
    command: ["python", "src/bus/start_full_pipeline.py",
              "--delay=0.05", "--batch-size=100",
              "--flush-interval=15", "--trigger-interval=60", "--loop"]
    environment: { <<: [*env-common, *env-cloud] }
    volumes: *volumes-data
    extra_hosts: ["host.docker.internal:host-gateway"]
    restart: unless-stopped
    profiles: [bus]

  # Perfil desarrollo: src/ montado como volumen → cambios sin rebuild
  dashboard-dev:
    build: { context: ., dockerfile: Dockerfile }
    image: fintech-pipeline:latest
    container_name: fintech-dashboard-dev
    command: ["python", "-m", "streamlit", "run", "src/agent/app.py",
              "--server.port=8501", "--server.address=0.0.0.0",
              "--server.headless=true", "--server.fileWatcherType=poll",
              "--server.runOnSave=true"]
    ports: ["8501:8501"]
    environment: { <<: [*env-common, *env-cloud] }
    volumes:
      - ./src:/app/src        # src/ en vivo — cambios sin rebuild
      - ./data:/app/data
      - ./outputs:/app/outputs
      - ./logs:/app/logs
    extra_hosts: ["host.docker.internal:host-gateway"]
    restart: unless-stopped
    profiles: [dev]
```

---

## Variables de entorno (`.env`)

Docker Compose lee el `.env` de la raíz automáticamente. **No hace falta** pasar `--env-file`.

```env
# LLM (en el HOST, no en Docker)
OLLAMA_BASE_URL=http://localhost:11434   # docker-compose.yml lo reemplaza por host.docker.internal
OLLAMA_MODEL=llama3.2

# AWS S3
AWS_ACCESS_KEY_ID=AKIAXXXXXXXXXXXXXXXX
AWS_SECRET_ACCESS_KEY=XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX
AWS_REGION=us-east-1
AWS_BUCKET=tu-bucket-fintech

# Databricks Unity Catalog
DATABRICKS_HOST=dbc-xxxxxxxx.cloud.databricks.com
DATABRICKS_TOKEN=dapiXXXXXXXXXXXXXXXX
DATABRICKS_HTTP_PATH=/sql/1.0/warehouses/XXXXXXXX
DATABRICKS_CATALOG=fintech_pipeline
DATABRICKS_SCHEMA=fintech

# APIs de enriquecimiento
EXCHANGE_RATE_API_KEY=tu_clave_aqui
```

---

## Comandos de uso

### Build de la imagen

```bash
# Primera vez o cuando cambie requirements.txt
docker compose build

# Forzar rebuild completo (sin caché)
docker compose build --no-cache

# Verificar imagen creada
docker images fintech-pipeline
```

### Pipeline batch (Bronze → Silver → Gold)

```bash
docker compose --profile pipeline up pipeline

# Ver logs
docker compose --profile pipeline logs -f pipeline
```

### Dashboard Streamlit

```bash
# Producción (imagen empaquetada)
docker compose --profile dashboard up -d

# Desarrollo (hot-reload — cambios en src/ visibles al instante)
docker compose --profile dev up -d dashboard-dev

# Acceder en: http://localhost:8501
```

### APIs REST

```bash
# Receptor de eventos (puerto 8000)
docker compose --profile api up -d api
curl http://localhost:8000/docs

# Generador de eventos (puerto 8001)
docker compose --profile api --profile ecommerce up -d api ecommerce
curl http://localhost:8001/docs
```

### Bus de streaming completo

```bash
docker compose --profile bus up -d bus
docker compose logs -f bus
```

### Varios servicios a la vez

```bash
docker compose --profile dashboard --profile api up -d

# Ver estado de todos los contenedores
docker compose ps

# Detener todo
docker compose down
```

---

## Flujo de actualización de código

```
¿Qué cambió?
│
├── src/ (Python, CSS, prompts)
│   ├── Estoy desarrollando  →  docker compose --profile dev up -d dashboard-dev
│   └── Voy a producción     →  docker compose build
│                               docker compose --profile dashboard up -d
│
└── requirements.txt (nueva librería)
    └── Siempre rebuild      →  docker compose down
                                docker compose build
                                docker compose --profile dashboard up -d
```

---

## Comandos de operación y debugging

```bash
# Ver logs en tiempo real
docker compose logs -f dashboard

# Entrar al contenedor (shell interactivo)
docker exec -it fintech-dashboard bash

# Ejecutar pipeline manualmente dentro del contenedor
docker exec fintech-dashboard python src/run_pipeline.py

# Ver uso de CPU/memoria de todos los contenedores
docker stats

# Limpiar contenedores, red e imagen local
docker compose down --rmi local
```

---

## Solución de problemas frecuentes

### Ollama no responde desde el contenedor

```bash
# Ollama debe escuchar en 0.0.0.0, no solo en localhost
OLLAMA_HOST=0.0.0.0 ollama serve

# Verificar conectividad desde el contenedor
docker exec fintech-dashboard curl http://host.docker.internal:11434/api/tags
```

### `host.docker.internal` no resuelve en Linux

Ya está incluido en todos los servicios del compose:
```yaml
extra_hosts:
  - "host.docker.internal:host-gateway"
```

### Error de permisos en Windows con volúmenes (WSL2)

```bash
# Usar la ruta WSL en docker-compose.yml
volumes:
  - /mnt/c/Users/Alexander/Documents/fintech_pipeline_v3/data:/app/data
```

### Puerto 8501 ya en uso

```bash
# Cambiar en docker-compose.yml
ports:
  - "8502:8501"   # expuesto en 8502, interno 8501
```

### Ver tamaño de la imagen

```bash
docker image inspect fintech-pipeline:latest --format='{{.Size}}' | \
  python -c "import sys; print(f'{int(sys.stdin.read())/1024/1024:.0f} MB')"
```

---

## Notas de seguridad

- `.env` **nunca** al repositorio — está en `.gitignore`
- Las credenciales AWS y Databricks se inyectan en runtime via `.env`, no se hornean en la imagen
- Para producción cloud, usar los secrets nativos del proveedor (AWS Secrets Manager, Doppler, etc.)
- La imagen base `python:3.12-slim` minimiza la superficie de ataque
