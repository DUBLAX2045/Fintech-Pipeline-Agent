# AWS S3 Setup - Fintech Pipeline

Esta guia explica como configurar AWS S3 e IAM para que el pipeline pueda subir
los Parquets de Silver y Gold desde Python usando `boto3`.

El flujo esperado es:

```text
Pipeline local
  -> data/silver/*.parquet
  -> data/gold/*.parquet
  -> AWS S3
  -> Databricks / agente / consultas analiticas
```

## 1. Variables que usa el proyecto

El archivo `.env` debe contener:

```env
AWS_ACCESS_KEY_ID=AKIA...
AWS_SECRET_ACCESS_KEY=...
# Solo si usas credenciales temporales AWS STS / AWS Academy / SSO
# AWS_SESSION_TOKEN=...
AWS_REGION=us-east-1
AWS_BUCKET=fintech-pipeline1
```

Reglas importantes:

- `AWS_BUCKET` es solo el nombre del bucket. No uses `s3://`.
- `AWS_REGION` debe coincidir con la region real del bucket.
- `AWS_SESSION_TOKEN` solo aplica para credenciales temporales.
- No subas `.env` a Git.

## 2. Crear el bucket S3

En AWS Console:

1. Entra a `S3`.
2. Click en `Create bucket`.
3. Elige un nombre globalmente unico, por ejemplo:

```text
fintech-pipeline-tu-nombre-2026
```

4. Selecciona la region, por ejemplo:

```text
us-east-1
```

5. Deja `Block all public access` activado.
6. Deja versioning opcional. Para desarrollo puede estar apagado.
7. Crea el bucket.

El bucket no debe ser publico. El pipeline sube objetos firmando las solicitudes
con credenciales IAM, asi que no necesita acceso publico.

## 3. CORS: no configurarlo para este pipeline

No necesitas CORS para este proyecto.

CORS solo aplica cuando un navegador web accede directamente al bucket desde
otro dominio, por ejemplo una app React subiendo archivos a S3 desde el cliente.
Este pipeline sube archivos desde Python con `boto3`, por lo que CORS no
participa.

Mantener:

```text
Block all public access: ON
CORS: sin configurar
```

## 4. Crear usuario IAM para el pipeline

En AWS Console:

1. Entra a `IAM`.
2. Ve a `Users`.
3. Click en `Create user`.
4. Nombre sugerido:

```text
fintech-pipeline-uploader
```

5. No necesitas darle acceso a la consola.
6. Continua hasta permisos, pero primero crea una policy administrada propia.

## 5. Crear policy IAM de minimo privilegio

En AWS Console:

1. Entra a `IAM`.
2. Ve a `Policies`.
3. Click en `Create policy`.
4. Abre la pestana `JSON`.
5. Pega esta policy, reemplazando `TU_BUCKET` por el nombre real del bucket:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "ListAndLocateBucket",
      "Effect": "Allow",
      "Action": [
        "s3:ListBucket",
        "s3:GetBucketLocation"
      ],
      "Resource": "arn:aws:s3:::TU_BUCKET"
    },
    {
      "Sid": "ReadWritePipelineObjects",
      "Effect": "Allow",
      "Action": [
        "s3:PutObject",
        "s3:GetObject",
        "s3:DeleteObject"
      ],
      "Resource": "arn:aws:s3:::TU_BUCKET/*"
    }
  ]
}
```

Ejemplo para `fintech-pipeline1`:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "ListAndLocateBucket",
      "Effect": "Allow",
      "Action": [
        "s3:ListBucket",
        "s3:GetBucketLocation"
      ],
      "Resource": "arn:aws:s3:::fintech-pipeline1"
    },
    {
      "Sid": "ReadWritePipelineObjects",
      "Effect": "Allow",
      "Action": [
        "s3:PutObject",
        "s3:GetObject",
        "s3:DeleteObject"
      ],
      "Resource": "arn:aws:s3:::fintech-pipeline1/*"
    }
  ]
}
```

6. Nombre sugerido:

```text
FintechPipelineS3Access
```

7. Crea la policy.

## 6. Adjuntar la policy al usuario IAM

1. Ve a `IAM` -> `Users`.
2. Selecciona `fintech-pipeline-uploader`.
3. Entra a `Permissions`.
4. Click en `Add permissions`.
5. Selecciona `Attach policies directly`.
6. Busca `FintechPipelineS3Access`.
7. Adjuntala al usuario.

## 7. Crear access key

1. Ve a `IAM` -> `Users`.
2. Selecciona `fintech-pipeline-uploader`.
3. Entra a `Security credentials`.
4. En `Access keys`, click en `Create access key`.
5. Caso de uso recomendado:

```text
Application running outside AWS
```

o, si AWS muestra opciones diferentes:

```text
Command Line Interface (CLI)
```

6. Copia:

```text
Access key ID
Secret access key
```

El secret se muestra una sola vez.

## 8. Configurar `.env`

Ejemplo:

```env
AWS_ACCESS_KEY_ID=AKIAxxxxxxxxxxxxxxxx
AWS_SECRET_ACCESS_KEY=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
AWS_REGION=us-east-1
AWS_BUCKET=fintech-pipeline1
```

Si usas credenciales temporales:

```env
AWS_ACCESS_KEY_ID=ASIAxxxxxxxxxxxxxxxx
AWS_SECRET_ACCESS_KEY=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
AWS_SESSION_TOKEN=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
AWS_REGION=us-east-1
AWS_BUCKET=fintech-pipeline1
```

## 9. Validar la conexion S3

Desde la raiz del proyecto:

```powershell
venv\Scripts\python.exe -B src\ingesta\uploader_s3.py
```

Salida esperada:

```text
Verificando conexion S3...
OK Bucket s3://TU_BUCKET (us-east-1) accesible
OK Escritura S3 validada con healthcheck temporal
OK Limpieza del healthcheck validada
```

Tambien puedes validar toda la nube:

```powershell
venv\Scripts\python.exe -B scripts\verificar_cloud.py
```

## 10. Subir Parquets manualmente

Primero genera Silver y Gold:

```powershell
venv\Scripts\python.exe -B src\run_pipeline.py --desde-silver
```

Luego sube a S3:

```powershell
venv\Scripts\python.exe -B -c "from src.ingesta.uploader_s3 import subir_parquets; subir_parquets('data/silver','silver'); subir_parquets('data/gold','gold')"
```

Rutas esperadas en S3:

```text
s3://TU_BUCKET/silver/silver_events.parquet
s3://TU_BUCKET/gold/gold_user_360.parquet
s3://TU_BUCKET/gold/gold_daily_metrics.parquet
s3://TU_BUCKET/gold/gold_event_summary.parquet
```

## 11. Relacion con Databricks

Estas credenciales AWS del `.env` son para Python y `boto3`.

Databricks no usa `AWS_ACCESS_KEY_ID` ni `AWS_SECRET_ACCESS_KEY` del `.env`.
Para que Databricks lea S3 necesitas una configuracion separada de Unity
Catalog:

```text
Storage Credential
External Location
Tablas externas USING PARQUET
```

Ver:

```text
docs/DATABRICKS_SETUP.md
```

## 12. Solucion de problemas

| Error | Causa probable | Solucion |
| --- | --- | --- |
| `InvalidClientTokenId` | Access key/secret mal copiados, desactivados o de otra cuenta | Crea una nueva access key y actualiza `.env` |
| `Unable to locate credentials` | `.env` no cargado o variables faltantes | Verifica `AWS_ACCESS_KEY_ID` y `AWS_SECRET_ACCESS_KEY` |
| `403 Forbidden` en `HeadBucket` | Bucket no existe en esa cuenta, nombre incorrecto o falta permiso | Revisa `AWS_BUCKET`, policy IAM y cuenta AWS |
| `NoSuchBucket` | Nombre de bucket incorrecto | Usa solo el nombre real, sin `s3://` |
| `Could not connect to endpoint URL` | Region/bucket/endpoint incorrecto o red bloqueada | Verifica `AWS_REGION` y conectividad |
| `AccessDenied` en `PutObject` | Falta `s3:PutObject` | Corrige la policy IAM |
| `AccessDenied` en `DeleteObject` | Falta `s3:DeleteObject` | Agrega `s3:DeleteObject` o acepta que el healthcheck no pueda limpiar |
| `SignatureDoesNotMatch` | Secret incorrecto o caracteres extra en `.env` | Copia de nuevo la secret key, sin espacios ni comillas |
| Databricks no ve S3 | Falta external location | Configura Unity Catalog, no las keys de Python |

## 13. Buenas practicas

- No uses access keys del usuario root.
- Usa un usuario IAM dedicado para el pipeline.
- Aplica minimo privilegio: solo el bucket del proyecto.
- Mantener `Block all public access` activado.
- No guardar secretos en Git.
- Rotar o desactivar access keys que ya no uses.
- Usar `AWS_SESSION_TOKEN` si tus credenciales son temporales.
- Para produccion, preferir roles/IAM Identity Center cuando sea posible.

## 14. Checklist final

```text
[ ] Bucket creado
[ ] Block all public access activado
[ ] Policy IAM creada con TU_BUCKET correcto
[ ] Policy adjunta al usuario IAM
[ ] Access key creada
[ ] .env actualizado
[ ] src/ingesta/uploader_s3.py pasa
[ ] scripts/verificar_cloud.py pasa
[ ] Parquets Silver/Gold suben a s3://TU_BUCKET/
```
