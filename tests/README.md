# Test Suite

This folder separates the project tests by risk and dependency level.

```text
tests/
  unit/          Fast isolated tests. No real network, no real .env.
  integration/   Local end-to-end checks using temporary folders or mocks.
  cloud/         Real S3/Databricks/ExchangeRate checks. Excluded by default.
  performance/   Pytest benchmarks for Bronze/Silver/Gold and local pipeline IO.
  load/          Locust load scenarios for the local ingestion API.
  ui/            Streamlit dashboard smoke/rendering tests.
  mutation/      Portable mutation smoke runner for critical logic.
```

Common commands:

```powershell
venv\Scripts\pytest.exe
venv\Scripts\pytest.exe tests\unit
venv\Scripts\pytest.exe -m integration
venv\Scripts\pytest.exe -m cloud
venv\Scripts\pytest.exe --cov --cov-report=term-missing
venv\Scripts\pytest.exe --cov --cov-report=term-missing --cov-report=xml
venv\Scripts\pytest.exe -n auto
venv\Scripts\pytest.exe -m performance --benchmark-only
venv\Scripts\pytest.exe tests\ui
venv\Scripts\ruff.exe check src tests
```

The existing `verificar_*.py` files remain useful as operational smoke checks.
Pytest is the automated regression suite; the verifier scripts are broader
manual or CI health checks after a real pipeline/cloud run.

Coverage uses `.coveragerc` to measure the testable core of `src/`, including
local runtime adapters for bus/API/scripts. UI screens and interactive console
launchers remain excluded from coverage.
`coverage.xml` is the report expected by `sonar-project.properties`.

Performance/load tooling installed for the next test layer:

```powershell
venv\Scripts\pytest.exe -m performance --benchmark-only
venv\Scripts\pytest.exe tests\performance --benchmark-only --benchmark-save=pipeline-baseline
venv\Scripts\locust.exe -f tests\load\locustfile.py
venv\Scripts\scalene.exe src\run_pipeline.py -- --desde-silver
```

Performance tests are excluded from the default `pytest` run. Use
`-m performance --benchmark-only` when you explicitly want timing measurements.
The benchmark file covers Bronze preparation, Silver transformations, Gold
aggregations, and a local Silver-to-Gold Parquet run using temporary folders.

For Locust, start both local APIs first in separate terminals.

Recommended isolated load-test receiver, so Locust does not append test traffic
to the normal `data/bronze/events` dataset:

```powershell
$env:FINTECH_BRONZE_EVENTS_DIR=".pytest_tmp\load\bronze\events"
$env:FINTECH_RECEIVER_AUTO_TRIGGER="0"
$env:FINTECH_RECEIVER_UPLOAD_S3="0"
venv\Scripts\uvicorn.exe src.bus.api_receiver:app --host 127.0.0.1 --port 8000
```

In a second terminal, start the public ingestion API:

```powershell
venv\Scripts\uvicorn.exe src.bus.ecommerce_api:app --host 127.0.0.1 --port 8001
```

Then open the Locust UI:

```powershell
venv\Scripts\locust.exe -f tests\load\locustfile.py --host http://127.0.0.1:8001
```

Or run it headless for CI/manual stress checks:

```powershell
venv\Scripts\locust.exe -f tests\load\locustfile.py --host http://127.0.0.1:8001 --headless -u 20 -r 5 -t 2m
```

`FINTECH_LOAD_BATCH_SIZE` controls batch size for `/ingest/batch` (default 10,
max 500). `FINTECH_LOAD_REQUIRE_RECEIVER=0` allows health checks to pass even
if only the ecommerce API is running, though the full pipeline load test should
keep the receiver on port 8000.

Do not combine `-n auto` with `--benchmark-only`: xdist is great for the normal
suite, but benchmarks should run in a single process for stable measurements.

UI tests use Streamlit's `AppTest` and set `FINTECH_DASHBOARD_TEST_MODE=1`, so
they validate rendering/navigation without calling Ollama or Databricks.

Current mutation smoke checks:

```powershell
venv\Scripts\python.exe tests\mutation\mutation_smoke.py
venv\Scripts\python.exe tests\mutation\mutation_smoke.py --list
venv\Scripts\python.exe tests\mutation\mutation_smoke.py --filter security
```

The portable mutation smoke runner temporarily mutates selected critical files,
runs targeted tests, and restores the original source after each mutant. It is
kept dependency-free so it can run on Windows while a formal mutation tool is
selected for the project.
