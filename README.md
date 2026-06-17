# Fintech Data Pipeline V3

Plataforma de datos financieros de extremo a extremo con arquitectura medallion **Bronze → Silver → Gold**, bus de eventos asyncio, APIs FastAPI, dashboard ejecutivo Streamlit, agente IA conversacional con **Ollama (llama3.2)**, AWS S3, Databricks Unity Catalog, Docker y CI/CD con GitHub Actions.

---

## Qué es este proyecto

Un pipeline de datos financieros que simula el backend analítico de una fintech colombiana. Procesa eventos de transacciones (pagos, transferencias, compras, recargas) a través de tres capas de transformación progresiva, expone APIs REST para ingesta en tiempo real y ofrece un **agente inteligente** que responde preguntas de negocio en lenguaje natural, genera gráficos, detecta anomalías, compara períodos y exporta reportes ejecutivos HTML.

Opera en **dos modos paralelos**:
- **Batch** — procesa el dataset completo en una sola ejecución (`python src/run_pipeline.py`)
- **Streaming** — ingesta continua vía HTTP con procesamiento asíncrono y micro-batches

También incluye despliegue local completo en **Windows + Docker Desktop**, publicación de imagen en **Docker Hub**, CD con **GitHub self-hosted runner**, pruebas automatizadas y publicación opcional del dashboard con **ngrok**, **Cloudflare Tunnel** o **DuckDNS**.

---

## Arquitectura del Sistema

```
┌──────────────────────────────────────────────────────────────────────────┐
│                          FUENTES DE DATOS                                │
│                                                                          │
│   data/raw/fintech_events_v4.json          POST /ingest (HTTP)          │
│   (2,000 eventos sintéticos)               (APIs externas, simulador)   │
└─────────────────────┬────────────────────────────┬───────────────────────┘
                      │ Batch                       │ Streaming
                      ▼                             ▼
┌─────────────────────────────┐   ┌────────────────────────────────────────┐
│        CAPA BRONZE          │   │           BUS DE EVENTOS               │
│   src/bronze/               │   │   src/bus/                             │
│                             │   │                                        │
│   Aplana detail.payload     │   │   EcommerceAPI (puerto 8001)           │
│   Enriquece con metadatos   │◄──┤     └─► EventBus (asyncio.Queue)       │
│   Detecta duplicados        │   │           └─► BronzeConsumer           │
│   Parquet por fecha         │   │               (micro-batch 50 eventos  │
│                             │   │                ó flush cada 30s)       │
└──────────────┬──────────────┘   └──────────────────┬─────────────────────┘
               │                                      │ PipelineTrigger
               │   data/bronze/events/date=*/         │ (throttled 60s)
               ▼                                      ▼
┌──────────────────────────────────────────────────────────────────────────┐
│                           CAPA SILVER                                    │
│   src/silver/pipeline_silver.py — 7 pasos secuenciales                  │
│                                                                          │
│   1. Lee todos los Parquets Bronze (glob recursivo + pd.concat)          │
│   2. Normaliza tipos: timestamp→datetime UTC, amount→float               │
│   3. Agrega flags: is_failed, is_transactional, ip_is_private            │
│   4. Geolocalización: IPs privadas→payload.city; públicas→ip-api.com    │
│   5. Conversión COP→USD vía open.er-api.com (fallback: 1/4150)          │
│   6. Renombra columnas, elimina redundantes                              │
│   7. Guarda Parquet comprimido (snappy)                                  │
└──────────────┬───────────────────────────────────────────────────────────┘
               │   data/silver/silver_events.parquet (40 cols, 2,000 filas)
               ▼
┌──────────────────────────────────────────────────────────────────────────┐
│                           CAPA GOLD                                      │
│   src/gold/pipeline_gold.py                                              │
│                                                                          │
│   gold_user_360.parquet        Vista 360 por usuario (489 en base)      │
│   gold_daily_metrics.parquet   KPIs agregados por día                   │
│   gold_event_summary.parquet   Resumen por tipo de evento               │
└──────────────┬───────────────────────────────────────────────────────────┘
               │
       ┌───────┴────────┐
       ▼                ▼
┌─────────────┐  ┌──────────────────────────────────────────────────────────┐
│   AWS S3    │  │              AGENTE IA + DASHBOARD                       │
│ (Parquets)  │  │   src/agent/                                             │
│      │      │  │                                                          │
│      ▼      │  │   Streamlit App  ←→  OllamaModel (llama3.2)             │
│ Databricks  │  │                           │                             │
│ Unity       │  │              11 herramientas (@tool)                    │
│ Catalog     │  │              Historial de conversación (4 turnos)       │
│ SQL SELECT  │  │              DuckDB fallback local                      │
└─────────────┘  └──────────────────────────────────────────────────────────┘
```

---

## Capas de Datos

### Bronze — Ingesta Raw

Lee el JSON fuente, aplana la estructura anidada `detail.payload` y guarda Parquets particionados por fecha. No transforma, solo preserva con trazabilidad completa.

```
data/raw/fintech_events_v4.json
  └── { event_id, event, user_id, detail: { payload: { amount, merchant, ... } } }
                ↓
data/bronze/events/date=2026-05-22/batch_001.parquet
  └── 44 columnas planas: event_id, event, user_id, amount, timestamp, merchant,
      category, payment_method, balance_before, balance_after, device, os, ip,
      channel, location_city, is_duplicate, ingestion_date ...
```

**Anti-duplicados:** compara `event_id` contra los ya procesados y marca `is_duplicate=True` — nunca elimina, preserva trazabilidad.

### Silver — Limpieza y Enriquecimiento

| Paso | Qué hace | Detalle |
|------|----------|---------|
| 1 | Lee Bronze | `glob("data/bronze/**/*.parquet")` + `pd.concat()` |
| 2 | Normaliza tipos | `pd.to_datetime(utc=True)`, `pd.to_numeric()`, email `.lower()` |
| 3 | Flags | `is_failed`, `is_transactional`, `ip_is_private` (RFC 1918), `geo_source` |
| 4 | Geolocalización | IPs privadas → `payload.city`; IPs públicas → `ip-api.com` (45 req/min) |
| 5 | Moneda | `GET open.er-api.com/v6/latest/COP` → `amount_usd`; cache 1h; fallback `1/4150` |
| 6 | Columnas | Renombra `amount → amount_cop`, elimina redundantes de Bronze |
| 7 | Guarda | `parquet(compression="snappy")` |

### Gold — Inteligencia de Negocio

Tres tablas listas para consumo analítico por el agente IA y Databricks:

**`gold_user_360`** — una fila por usuario:
```
user_id, user_segment, city,
total_events, total_transactions, failed_transactions, failure_rate,
total_amount_cop, total_amount_usd, avg_ticket, balance_current,
top_merchant, top_category, preferred_channel, preferred_device,
last_transaction_date, last_event_date, days_since_last_tx
```

**`gold_daily_metrics`** — una fila por día:
```
date, total_events, total_transactions, total_amount_cop, failed_count, unique_users
```

**`gold_event_summary`** — una fila por tipo de evento:
```
event, count, success_count, failed_count, pct_of_total
```

---

## Bus de Eventos — Streaming en Tiempo Real

```
EcommerceProducer (tps configurable)
  │  FintechEvent(@dataclass): event_id, timestamp, user_id, amount, ...
  │  5 tipos: PAYMENT_MADE, PURCHASE_MADE, TRANSFER_SENT, MONEY_ADDED, PAYMENT_FAILED
  ▼
EventBus (asyncio.Queue, maxsize=1000)
  │  Micro-batch: espera primer evento → drena sin bloquear → máx 50 ó flush 30s
  │  Backpressure: si cola llena, producer espera
  ▼
BronzeConsumer (async loop)
  │  asyncio.to_thread() → no bloquea el loop principal
  │  aplanar_todos() → metadatos → duplicados → Parquet
  ▼
PipelineTrigger (hilo daemon)
  │  Throttling: mínimo 60s entre ejecuciones
  │  Orden: Silver → Gold → S3 (si configurado)
  ▼
data/gold/*.parquet (siempre fresco)
```

**Envelope estándar de mensajes:**

```json
{
  "msg_type": "event | metric | record | log | alert",
  "source":   "ecommerce | crm | mobile_app | ...",
  "schema_version": "1.0",
  "message_id": "<UUID>",
  "timestamp":  "<ISO 8601 UTC>",
  "data":       { ... },
  "metadata":   { ... }
}
```

---

## APIs REST

### Receptor de Eventos — Puerto 8000

```bash
uvicorn src.bus.api_receiver:app --port 8000 --reload
```

| Método | Endpoint | Descripción |
|--------|----------|-------------|
| `POST` | `/ingest` | Publica mensaje al bus |
| `GET`  | `/health` | Estado del bus y consumer |
| `GET`  | `/pipeline/status` | Stats detalladas (bus, consumer, trigger) |
| `POST` | `/pipeline/run` | Fuerza ejecución Silver→Gold ahora |
| `DELETE` | `/pipeline/flush` | Procesa batch pendiente inmediatamente |
| `GET`  | `/docs` | Swagger UI |

### API de E-commerce / Generadora — Puerto 8001

```bash
uvicorn src.bus.ecommerce_api:app --port 8001 --reload
```

| Método | Endpoint | Descripción |
|--------|----------|-------------|
| `POST` | `/ingest` | Ingesta con validación de envelope |
| `POST` | `/ingest/batch` | Ingesta masiva hasta 500 mensajes |
| `POST` | `/simulate?n=100&tps=2` | Genera eventos en background |
| `GET`  | `/stats` | Contadores por tipo de mensaje |
| `POST` | `/events/payment` | Atajo: genera PAYMENT_MADE |
| `POST` | `/events/purchase` | Atajo: genera PURCHASE_MADE |
| `POST` | `/events/transfer` | Atajo: genera TRANSFER_SENT |
| `POST` | `/events/failure` | Atajo: genera PAYMENT_FAILED |

---

## Agente IA Conversacional

### Flujo de procesamiento

```
Pregunta del usuario
       │
       ├─► ¿Es petición de gráfico?   → _manejar_peticion_grafico()
       │                                  └─► 11 handlers por dimensión
       │                                  └─► Ollama genera SQL si no hay match
       │
       ├─► ¿Es seguimiento anafórico?  → _resolver_seguimiento_con_ollama()
       │   ("y en Bogotá?", "y los de premium?")   con historial de 4 turnos
       │
       ├─► ¿Tiene intent reconocido?   → SQL determinístico (intent_router.py)
       │   12 reglas de negocio        → DuckDB/Databricks → Ollama interpreta
       │
       ├─► ¿Palabra clave especial?
       │   alertas/diagnóstico         → detectar_alertas()
       │   comparar período            → comparar_periodos(dias_atras=7)
       │   reporte/informe             → generar_reporte_html()
       │   resumen/kpi                 → resumen_ejecutivo()
       │
       └─► Sin match → Agent(strands) con OllamaModel → tools disponibles
```

### Herramientas del agente (11 tools)

| Herramienta | Descripción |
|-------------|-------------|
| `consultar_sql` | SELECT sobre Gold (Databricks primero, DuckDB fallback) |
| `consultar_databricks` | SQL directo al warehouse Databricks Unity Catalog |
| `grafico_barras` | Gráfico de barras desde SQL → PNG |
| `grafico_tendencia_diaria` | Gráfico de línea temporal desde SQL → PNG |
| `grafico_segmentos` | Gráfico de torta desde SQL → PNG |
| `perfil_usuario_360` | Perfil completo de un usuario (sin PII) |
| `resumen_ejecutivo` | KPIs globales: usuarios, volumen, ticket, fallo, ciudades, merchants |
| `detectar_alertas` | Diagnóstico automático: 7 verificaciones con semáforo 🔴🟡🟢 |
| `comparar_periodos` | Comparativa N días vs N días anteriores con deltas % |
| `generar_reporte_html` | Reporte ejecutivo HTML autocontenido con gráficos en base64 |
| `listar_tablas` | Responde sin revelar estructura interna |

### Gráficos inteligentes

El agente genera gráficos para **11 dimensiones** de la capa Gold sin necesidad de SQL manual:

```
segmentos · ciudades · merchants/comercios · categorías · canales · dispositivos
ticket/revenue · balance/saldo · eventos · inactivos/churn · fallos · tendencias diarias
```

Si la petición no coincide con ningún patrón, Ollama genera el SQL apropiado automáticamente.

### Contexto de conversación

El agente mantiene un historial de los **últimos 4 turnos** (8 mensajes). Preguntas de seguimiento como "¿y en Bogotá?" o "¿y los del segmento premium?" se resuelven automáticamente usando ese contexto sin necesidad de reformular la pregunta.

### Detección automática de alertas

`detectar_alertas()` ejecuta 7 verificaciones sin que el usuario sepa qué preguntar:

| Verificación | Umbral crítico | Umbral advertencia |
|---|---|---|
| Tasa de fallo global | > 5% | > 3% |
| Tasa de fallo por segmento | > 5% | > 3% |
| Concentración de revenue | > 60% en un segmento | > 45% |
| Churn 30 días | > 30% de usuarios | > 20% |
| Churn profundo 60 días | > 15% | — |
| Caída de actividad diaria (3d vs semana) | > −20% | > −10% |
| Balance promedio por segmento | Negativo | < COP 50K |

---

## Reporte Ejecutivo HTML

`generar_reporte_html()` produce un archivo HTML autocontenido (sin dependencias externas) con:

- **6 KPI cards** con semáforo de color según umbrales de negocio
- **3 gráficos embebidos** en base64 (revenue por segmento, revenue por ciudad, tendencia diaria)
- **Análisis narrativo** de Ollama en formato ejecutivo de 4 bloques
- **Diagnóstico de alertas** con colores crítico/advertencia/OK
- **Comparativa 7d vs 7d anteriores** con variaciones porcentuales
- **4 tablas de datos**: segmentos, ciudades, merchants, últimos 14 días

Se activa con frases como "genera el reporte", "exportar informe", "reporte HTML".

---

## Dashboard Ejecutivo Streamlit

El dashboard principal vive en `src/agent/app.py` y funciona como consola operativa del proyecto:

- **Centro de mando**: KPIs Gold, volumen por segmento, ciudad líder, canal, tablas ejecutivas y filtros.
- **Mesa de análisis**: chat con el agente IA, preguntas sugeridas, gráficos y reportes HTML.
- **Sistema**: estado de Ollama, Databricks, capa Gold y credenciales operativas.
- **Panel de navegación responsivo**: sidebar con botones activo/inactivo diferenciados visualmente.
- **Acción operativa**: botón `Ejecutar Silver/Gold` que llama al API interno `/pipeline/run` desde Docker.
- **Tema visual**: interfaz oscura tipo terminal financiero — fondo `#050a14`, acento emerald `#10b981`, tipografía monoespaciada. Completamente rediseñada respecto a la versión inicial.
- **Gráficos interactivos**: combina matplotlib (PNG estático para reportes HTML) y Plotly (gráficos interactivos en pantalla).
- **Diseño responsive**: ajustado para escritorio ancho (max 1440 px), tablet y móvil.

En Docker, el dashboard se comunica con el receptor interno usando:

```bash
FINTECH_PIPELINE_API_URL=http://api:8000
```

En ejecución local sin Docker usa por defecto:

```bash
FINTECH_PIPELINE_API_URL=http://127.0.0.1:8000
```

---

## Seguridad

### Capa SQL (`src/agent/security.py`)

```
Operaciones BLOQUEADAS (DDL/DML):
  DROP, DELETE, UPDATE, INSERT, ALTER, CREATE, TRUNCATE

Columnas PII FILTRADAS del resultado:
  user_name, user_email, user_age, email, name, age

Límite de filas: máximo 100 por consulta del agente
```

### Capa del Agente (SYSTEM_PROMPT en `schema.py`)

El agente tiene instrucciones explícitas:
- Prohibido revelar nombres de tablas, esquema o estructura interna
- Prohibido retornar registros individuales con datos personales
- Obligatorio: agregar o anonimizar antes de responder
- Usuarios referenciados solo por `user_id`
- Toda cifra en la respuesta debe provenir de una herramienta, nunca inventada

---

## Integraciones Externas

| Servicio | Propósito | Variables `.env` | Fallback |
|----------|-----------|------------------|---------|
| **Ollama** `localhost:11434` | LLM llama3.2 para el agente | `OLLAMA_BASE_URL`, `OLLAMA_MODEL` | Dashboard sigue; respuestas IA quedan limitadas |
| **open.er-api.com** | Conversión COP → USD en Silver | `EXCHANGE_RATE_API_KEY` (opcional) | Rate fijo: `1/4150` |
| **ip-api.com** | Geolocalización por IP pública | Sin clave (45 req/min) | Usa `location_city` del payload |
| **AWS S3** | Almacena Parquets Silver/Gold | `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_REGION`, `AWS_BUCKET` | Pipeline funciona sin S3 |
| **Databricks** Unity Catalog | SQL warehouse en producción | `DATABRICKS_HOST`, `DATABRICKS_TOKEN`, `DATABRICKS_HTTP_PATH` | DuckDB local en memoria |

---

## Instalación y Ejecución

### Prerrequisitos

- Python 3.12
- [Ollama](https://ollama.com/download) instalado y corriendo (requerido para el agente)
- Credenciales AWS y Databricks (opcionales — el pipeline funciona sin ellas)

### Instalación

```bash
# 1. Clonar y entrar al directorio
git clone <repo-url> && cd fintech_pipeline_v3

# 2. Entorno virtual
python -m venv venv
venv\Scripts\activate        # Windows
source venv/bin/activate     # Linux / macOS

# 3. Dependencias
pip install -r requirements.txt

# 4. Variables de entorno
cp .env.example .env
# Editar .env con tus credenciales

# 5. Preparar Ollama (requerido para el agente)
ollama serve                 # Dejar corriendo en terminal separada
ollama pull llama3.2         # Primera vez: descarga el modelo (~2 GB)
```

### Modo 1 — Pipeline Batch (Bronze → Silver → Gold)

```bash
python src/run_pipeline.py
```

### Modo 2 — Streaming en Tiempo Real

```bash
# Terminal 1: Receptor de eventos
uvicorn src.bus.api_receiver:app --port 8000 --reload

# Terminal 2: API generadora
uvicorn src.bus.ecommerce_api:app --port 8001 --reload

# Terminal 3: Simular 100 eventos a 2 eventos/segundo
curl -X POST "http://localhost:8001/simulate?n=100&tps=2"

# Ver estado del bus
curl http://localhost:8000/health
```

### Modo 3 — Dashboard + Agente IA

```bash
# Terminal 1: dashboard (requiere pipeline batch ejecutado + Ollama corriendo)
streamlit run src/agent/app.py
# Abre en: http://localhost:8501

# Terminal 2 opcional: para que el botón "Ejecutar Silver/Gold" funcione en local
uvicorn src.bus.api_receiver:app --port 8000 --reload
```

### Modo 4 — Docker (recomendado para despliegue)

Ver [docs/DOCKER_DEPLOY.md](docs/DOCKER_DEPLOY.md) para la guía completa. Resumen rápido:

```bash
# Build
docker build -t fintech-pipeline:latest .

# Dashboard + API receptor + API ecommerce
docker compose --profile dashboard --profile api --profile ecommerce up -d dashboard api ecommerce

# Accesos locales
# Dashboard: http://localhost:8501
# API receptor: http://localhost:8000/docs
# API ecommerce: http://localhost:8001/docs

# Pipeline one-shot
docker compose --profile pipeline run --rm pipeline

# Bus streaming continuo
docker compose --profile bus up -d bus

# Desarrollo del dashboard con hot reload
docker compose --profile dev up -d dashboard-dev
```

### Modo 5 — Nube (S3 + Databricks)

```bash
# Ver guías completas:
# docs/AWS_S3_SETUP.md
# docs/DATABRICKS_SETUP.md

# Verificar conexión Databricks
python src/config/databricks_config.py

# Smoke check cloud completo
python scripts/verificar_cloud.py

# Subida manual
python -c "
from src.ingesta.uploader_s3 import subir_parquets
subir_parquets('data/silver', 'silver')
subir_parquets('data/gold',   'gold')
"
```

### Modo 6 — CI/CD, Docker Hub y dominio público

Ver [docs/CICD_DEPLOY.md](docs/CICD_DEPLOY.md) para el paso a paso completo.

El flujo actual queda así:

```text
push a GitHub
  ↓
CI: ruff + tests unitarios + Docker build smoke
  ↓
CD: tests + build/push de imagen a Docker Hub
  ↓
self-hosted runner Windows
  ↓
docker compose recrea dashboard + api + ecommerce
  ↓
health check en http://127.0.0.1:8501
```

Para Windows local gratis se usa:

- GitHub Actions como CI/CD.
- Docker Hub como registry de imagen.
- `C:\fintech_pipeline_deploy` como carpeta local de despliegue.
- GitHub self-hosted runner con labels `self-hosted`, `Windows`, `X64`.
- Docker Desktop corriendo en el equipo.

Para publicar el dashboard fuera del computador local:

- **ngrok**: recomendado para demo rápida y URL temporal.
- **Cloudflare Tunnel**: recomendado para una URL más seria sin abrir puertos.
- **DuckDNS**: subdominio gratis, pero requiere port forwarding y no sirve si tu red usa CGNAT.

### Scripts operativos de verificación

Estos scripts complementan pytest. Sirven como smoke checks manuales cuando quieres validar una corrida real, cloud o documentación:

```bash
python scripts/verificar_pipeline_completo.py
python scripts/verificar_cloud.py
python scripts/verificar_agente.py
python scripts/verificar_documentacion.py
```

---

## Tests

```bash
# Suite por defecto: excluye cloud, performance y e2e
python -m pytest

# Lint
ruff check src tests

# Unitarios
python -m pytest tests/unit -q

# Integración local
python -m pytest -m integration

# Cobertura
python -m pytest --cov --cov-report=term-missing --cov-report=xml

# Cloud real: S3, ExchangeRate y Databricks (requiere .env válido)
python -m pytest -m cloud

# UI Streamlit sin navegador real
python -m pytest tests/ui/test_dashboard_app.py -q

# E2E de dashboard con Playwright
python -m pytest -m e2e tests/ui/test_dashboard_smoke_playwright.py -v --browser chromium

# Pruebas de mutación (críticas para seguridad y lógica de negocio)
python tests/mutation/mutation_smoke.py

# Benchmarks de rendimiento
python -m pytest -m performance --benchmark-only

# Pruebas de carga (requiere APIs locales en 8000 y 8001)
locust -f tests/load/locustfile.py --host http://127.0.0.1:8001 --headless -u 20 -r 5 -t 2m
```

**Cobertura de tests:**

| Módulo | Tipo de test |
|--------|-------------|
| `bronze/ingest.py` | unit, property (Hypothesis), mutation |
| `silver/pipeline_silver.py` | unit, mutation |
| `gold/pipeline_gold.py` | unit, mutation |
| `agent/security.py` | unit, mutation |
| `agent/agent.py` | unit (routing, tools, Ollama mock) |
| `agent/schema.py` | unit |
| `bus/` | integration, load (Locust) |
| `ingesta/` | unit (moto S3) |
| `agent/app.py` | UI smoke con Streamlit AppTest y E2E con Playwright |
| `run_pipeline.py` | integration, performance benchmark |

---

## Estructura del Proyecto

```
fintech_pipeline_v3/
│
├── src/
│   ├── bronze/
│   │   ├── pipeline_bronze.py     Orquestador Bronze
│   │   ├── ingest.py              Carga JSON, aplana detail.payload
│   │   ├── metadata.py            Agrega ingestion_date, source_filename
│   │   ├── save.py                Escribe Parquet con particionado por fecha
│   │   └── simulator.py           Generador de eventos sintéticos
│   │
│   ├── silver/
│   │   └── pipeline_silver.py     7 pasos: limpieza, flags, geo, moneda, parquet
│   │
│   ├── gold/
│   │   └── pipeline_gold.py       3 tablas: user_360, daily_metrics, event_summary
│   │
│   ├── bus/
│   │   ├── event_bus_asyncio.py   EventBus, EcommerceProducer, BronzeConsumer
│   │   ├── message_schema.py      Envelope estándar + generadores sintéticos
│   │   ├── api_receiver.py        FastAPI puerto 8000 (receptor)
│   │   ├── ecommerce_api.py       FastAPI puerto 8001 (generador/ingestor)
│   │   ├── pipeline_trigger.py    Auto-trigger Silver→Gold→S3 con throttling
│   │   ├── dataset_producer.py    Productor del dataset completo
│   │   └── start_full_pipeline.py Orquestación completa modo streaming
│   │
│   ├── agent/
│   │   ├── agent.py               Agente IA: 11 tools, historial, Ollama, DuckDB
│   │   ├── app.py                 Dashboard Streamlit (3 páginas)
│   │   ├── schema.py              SYSTEM_PROMPT, GOLD_SCHEMA, sugerir_grafico
│   │   ├── security.py            Filtro SQL: bloqueo DDL/DML, redacción PII
│   │   ├── intent_router.py       Router determinístico: 12 reglas de negocio
│   │   ├── tools.py               Definiciones @tool para strands-agents
│   │   ├── charts.py              Utilidades matplotlib
│   │   └── run_agent.py           Runner standalone (sin Streamlit)
│   │
│   ├── ingesta/
│   │   ├── uploader_s3.py         Sube Parquets a AWS S3
│   │   ├── uploader_api.py        Subida vía API HTTP
│   │   └── uploader.py            Interfaz genérica
│   │
│   ├── config/
│   │   └── databricks_config.py   Conector Databricks Unity Catalog
│   │
│   ├── io/
│   │   └── parquet_io.py          I/O resiliente de Parquets con versionado
│   │
│   └── run_pipeline.py            Punto de entrada batch
│
├── tests/
│   ├── unit/                      Tests unitarios (pytest + Hypothesis)
│   ├── integration/               Tests de integración local
│   ├── cloud/                     Tests con servicios reales (S3, Databricks)
│   ├── mutation/
│   │   └── mutation_smoke.py      5 mutantes críticos: seguridad SQL, duplicados,
│   │                              failure_rate, deduplicación Silver
│   ├── performance/               Benchmarks (pytest-benchmark)
│   ├── load/
│   │   └── locustfile.py          Pruebas de carga HTTP
│   └── ui/                        Tests del dashboard Streamlit
│
├── scripts/
│   ├── verificar_pipeline_completo.py
│   ├── verificar_cloud.py
│   ├── verificar_agente.py
│   └── verificar_documentacion.py
│
├── docs/
│   ├── AWS_S3_SETUP.md                    Guía configuración AWS S3/IAM
│   ├── DATABRICKS_SETUP.md                Guía integración Databricks Unity Catalog
│   ├── DOCKER_DEPLOY.md                   Guía dockerización y despliegue
│   ├── CICD_DEPLOY.md                     CI/CD, Docker Hub, runner Windows y dominios
│   └── AGENT_CONTROL_DETERMINISTICO.md    Especificación control determinístico del agente IA
│
├── .github/workflows/
│   ├── ci.yml                     Ruff, tests unitarios y Docker build smoke
│   └── cd.yml                     Build/push Docker Hub + deploy Windows local
│
├── data/
│   ├── raw/fintech_events_v4.json Dataset fuente — NO MODIFICAR
│   ├── bronze/events/date=*/      Parquets particionados por fecha
│   ├── silver/silver_events.parquet
│   └── gold/
│       ├── gold_user_360.parquet
│       ├── gold_daily_metrics.parquet
│       └── gold_event_summary.parquet
│
├── outputs/
│   ├── charts/                    Gráficos PNG generados por el agente
│   └── reports/                   Reportes HTML ejecutivos
│
├── Dockerfile                     Imagen Docker del proyecto
├── docker-compose.yml             Orquestación de servicios con profiles
├── .dockerignore                  Excluye venv, .env, tests de la imagen
├── .env.example                   Plantilla de variables de entorno
├── requirements.txt               Dependencias Python
├── pytest.ini                     Configuración de tests
├── .coveragerc                    Configuración de cobertura
└── sonar-project.properties       Configuración base para Sonar
```

---

## Dataset Fuente

`data/raw/fintech_events_v4.json` — **2,000 eventos sintéticos** generados con Faker.

| Dimensión | Valores |
|-----------|---------|
| Tipos de evento | `USER_REGISTERED`, `MONEY_ADDED`, `PAYMENT_MADE`, `PURCHASE_MADE`, `TRANSFER_SENT`, `PAYMENT_FAILED`, `USER_PROFILE_UPDATED` |
| Segmentos | `premium`, `student`, `family`, `young_professional` |
| Ciudades | Bogotá, Medellín, Cali, Barranquilla, Cartagena |
| Comercios | Rappi, Éxito, Falabella, Nike, Netflix, Spotify, Amazon |
| Moneda | COP (convertido a USD en Silver) |
| Usuarios únicos | 489 en dataset base |

La corrida base genera **489 usuarios en Gold**. Ese valor puede cambiar tras ingesta API/Locust porque el bus puede agregar nuevos eventos y recalcular Silver/Gold.

---

## Variables de Entorno

```bash
# LLM LOCAL (REQUERIDO para el agente)
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=llama3.2

# AWS S3
AWS_ACCESS_KEY_ID=...
AWS_SECRET_ACCESS_KEY=...
AWS_SESSION_TOKEN=...        # Opcional: solo si usas credenciales temporales
AWS_REGION=us-east-1
AWS_BUCKET=fintech-pipeline

# DATABRICKS UNITY CATALOG
DATABRICKS_HOST=dbc-xxxxxxxx.cloud.databricks.com
DATABRICKS_TOKEN=dapi...
DATABRICKS_HTTP_PATH=/sql/1.0/warehouses/...
DATABRICKS_CATALOG=fintech_pipeline
DATABRICKS_SCHEMA=fintech

# APIs DE ENRIQUECIMIENTO (opcionales)
EXCHANGE_RATE_API_KEY=...    # Fallback hardcodeado si no se configura

# CONEXIÓN ENTRE SERVICIOS LOCALES / DOCKER
FINTECH_PIPELINE_API_URL=http://127.0.0.1:8000
FINTECH_RECEIVER_BASE_URL=http://127.0.0.1:8000

# TESTS DEL DASHBOARD
FINTECH_DASHBOARD_TEST_MODE=true
```

En Docker Compose, `FINTECH_PIPELINE_API_URL` y `FINTECH_RECEIVER_BASE_URL` se configuran automáticamente con nombres internos de servicio (`http://api:8000`). En ejecución local manual puedes dejarlas apuntando a `127.0.0.1`.

---

## Degradación Graceful

| Sin este servicio | Impacto | Fallback automático |
|-------------------|---------|---------------------|
| **Ollama** | Agente IA no disponible | Los datos Gold se muestran en tabla |
| **Databricks** | Sin SQL warehouse | DuckDB local con Parquets Gold en memoria |
| **AWS S3** | Sin subida a nube | Pipeline sigue funcionando 100% local |
| **ExchangeRate API** | Sin conversión dinámica | Rate fijo: `1 COP = 1/4150 USD` |
| **ip-api.com** | Sin geo por IP | Usa `location_city` del payload del evento |

---

## Estado del Proyecto

| Fase | Descripción | Estado |
|------|-------------|--------|
| 1 | Capa Bronze — ingesta, aplanado, Parquet particionado | Completa |
| 2 | Capa Silver — limpieza, geo, conversión COP/USD | Completa |
| 3 | Capa Gold — 3 tablas analíticas, métricas de negocio | Completa |
| 4 | Bus de eventos asyncio — micro-batch, FastAPI, PipelineTrigger | Completa |
| 5 | Agente IA — 11 tools, historial, gráficos inteligentes, alertas | Completa |
| 6 | Dashboard Streamlit — 3 páginas, chat IA, reportes HTML | Completa |
| 7 | AWS S3 — subida automática de Parquets | Implementado |
| 8 | Databricks Unity Catalog — integración producción | Completa con credenciales válidas |
| 9 | Dockerización — Dockerfile, docker-compose con profiles | Completa |
| 10 | Tests — unit, integration, cloud, UI, e2e, mutation, benchmark, load | Completa |
| 11 | CI — ruff, tests unitarios, Docker build smoke | Completa |
| 12 | CD — Docker Hub + self-hosted runner Windows + health check | Completa |
| 13 | Publicación externa — ngrok, Cloudflare Tunnel, DuckDNS | Documentada |

---

## Documentación Adicional

| Documento | Contenido |
|-----------|-----------|
| [docs/AWS_S3_SETUP.md](docs/AWS_S3_SETUP.md) | Configuración IAM, bucket, políticas, subida manual y automática |
| [docs/DATABRICKS_SETUP.md](docs/DATABRICKS_SETUP.md) | External Location, Unity Catalog, SQL warehouse, consultas desde el agente |
| [docs/DOCKER_DEPLOY.md](docs/DOCKER_DEPLOY.md) | Dockerfile, docker-compose, perfiles de servicio, solución de problemas |
| [docs/CICD_DEPLOY.md](docs/CICD_DEPLOY.md) | CI/CD, Docker Hub, self-hosted runner Windows/Linux y publicación con dominio/túnel |
| [docs/AGENT_CONTROL_DETERMINISTICO.md](docs/AGENT_CONTROL_DETERMINISTICO.md) | Especificación del control determinístico del agente: intenciones, reglas, anti-alucinaciones y pruebas conversacionales |
| [tests/README.md](tests/README.md) | Comandos detallados para unit, integration, cloud, performance, load, UI y mutation smoke |
