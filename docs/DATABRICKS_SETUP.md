# Integracion Databricks - Catalogo Fintech

## Arquitectura

```text
Pipeline local
  -> Parquet Silver/Gold
  -> AWS S3
  -> Databricks SQL Warehouse + Unity Catalog
  -> Agente IA / consultas SQL
```

## 1. Variables de entorno requeridas

```env
DATABRICKS_HOST=dbc-xxxxxxxx.cloud.databricks.com
DATABRICKS_TOKEN=dapiXXXXXXXXXXXXXXXX
DATABRICKS_HTTP_PATH=/sql/1.0/warehouses/XXXXXXXX
DATABRICKS_CATALOG=fintech_pipeline
DATABRICKS_SCHEMA=fintech
```

Donde obtener cada valor:

- `DATABRICKS_HOST`: SQL Warehouses -> tu warehouse -> Connection details -> Server hostname. En `.env` va sin `https://`.
- `DATABRICKS_HTTP_PATH`: SQL Warehouses -> tu warehouse -> Connection details -> HTTP path.
- `DATABRICKS_TOKEN`: User settings -> Developer -> Access tokens.
- `DATABRICKS_CATALOG` y `DATABRICKS_SCHEMA`: deben existir o crearse en Unity Catalog.

## 2. Verificacion de conectividad

```bash
python scripts/verificar_cloud.py
python src/config/databricks_config.py
```

El validador revisa:

- `SELECT 1` contra el SQL Warehouse.
- Catalogo visible.
- Schema visible.
- Tablas visibles en el schema.

## 3. Crear catalogo y schema

Ejecuta esto en Databricks SQL Editor si el catalogo/schema no existen:

```sql
CREATE CATALOG IF NOT EXISTS fintech_pipeline;
CREATE SCHEMA IF NOT EXISTS fintech_pipeline.fintech;
```

## 4. Registrar tablas externas sobre S3

El pipeline sube archivos Parquet, no Delta. Por eso las tablas externas deben
crearse con `USING PARQUET`, no como tablas Delta.

Antes de registrar tablas sobre S3, Unity Catalog debe tener una storage
credential y una external location con permisos sobre el bucket. Valida si ya
existen:

```sql
SHOW EXTERNAL LOCATIONS;
```

Si no hay external locations visibles, un admin de Databricks debe crear una
storage credential y una external location para el bucket S3. Luego las tablas
pueden apuntar a los objetos que sube el pipeline:

```sql
CREATE TABLE IF NOT EXISTS fintech_pipeline.fintech.gold_user_360
USING PARQUET
LOCATION 's3://TU_BUCKET/gold/gold_user_360.parquet';

CREATE TABLE IF NOT EXISTS fintech_pipeline.fintech.gold_daily_metrics
USING PARQUET
LOCATION 's3://TU_BUCKET/gold/gold_daily_metrics.parquet';

CREATE TABLE IF NOT EXISTS fintech_pipeline.fintech.gold_event_summary
USING PARQUET
LOCATION 's3://TU_BUCKET/gold/gold_event_summary.parquet';
```

Si Databricks rechaza esas rutas con errores de external location o permisos,
el problema ya no esta en el token SQL ni en Python: esta en la configuracion de
Unity Catalog para leer S3.

## 5. Queries de ejemplo

```sql
SELECT user_segment, COUNT(*) AS usuarios,
       ROUND(SUM(total_amount_cop), 0) AS volumen_cop,
       ROUND(AVG(avg_ticket), 0) AS ticket_promedio
FROM fintech_pipeline.fintech.gold_user_360
GROUP BY user_segment
ORDER BY volumen_cop DESC;

SELECT city, ROUND(AVG(failure_rate) * 100, 1) AS tasa_fallo_pct
FROM fintech_pipeline.fintech.gold_user_360
GROUP BY city
ORDER BY tasa_fallo_pct DESC;
```

## 6. Solucion de problemas

| Error | Causa probable | Solucion |
| --- | --- | --- |
| `Faltan variables de entorno` | `.env` incompleto | Completa `DATABRICKS_HOST`, `DATABRICKS_TOKEN`, `DATABRICKS_HTTP_PATH` |
| `Catalogo no existe` | `DATABRICKS_CATALOG` no coincide o no fue creado | Crea el catalogo o corrige `.env` |
| `Schema no existe` | `DATABRICKS_SCHEMA` no coincide o no fue creado | Crea el schema o corrige `.env` |
| `No hay tablas visibles` | Las tablas externas no fueron registradas | Crea las tablas `USING PARQUET` |
| `No external location found` | Unity Catalog no tiene acceso gobernado a S3 | Crea storage credential + external location |
| `403` o `AccessDenied` sobre S3 | Permisos S3/Unity Catalog insuficientes | Revisa IAM, bucket policy y external location |
| `PARQUET_TYPE_ILLEGAL TIMESTAMP(NANOS,true)` | Parquet escrito con timestamps en nanosegundos | Regenera Silver/Gold con `python src/run_pipeline.py --desde-silver` y sube de nuevo a S3 |
