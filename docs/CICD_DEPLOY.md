# CI/CD Deploy Pipeline — Fintech Pipeline v3

Esta guía cubre el despliegue completo de la aplicación como un pipeline CI/CD
profesional: desde el commit hasta producción, con pruebas automáticas, build de
imagen Docker, push a registry y deploy al servidor.

El flujo general es:

```text
Developer  →  git push  →  GitHub Actions
                               │
              ┌────────────────┼────────────────────┐
              │ CI (cualquier rama)                  │
              │  lint → unit tests → docker build    │
              └──────────────────────────────────────┘
                               │ merge to main
              ┌────────────────▼────────────────────┐
              │ CD (rama main solamente)             │
              │  tests → build → push → ssh deploy  │
              │  → health check → notificación       │
              └──────────────────────────────────────┘
                               │
              ┌────────────────▼────────────────────┐
              │ Servidor de producción               │
              │  fintech-dashboard  :8501            │
              │  fintech-api        :8000            │
              │  fintech-ecommerce  :8001            │
              └──────────────────────────────────────┘
```

---

## Checklist general de avance

Usa esta tabla para saber qué está listo y qué falta antes de hacer el primer deploy.

```text
INFRAESTRUCTURA BASE
[✅] Dockerfile creado y probado
[✅] docker-compose.yml con perfiles (dashboard, api, ecommerce, bus, dev)
[✅] Volúmenes compartidos definidos (data/, outputs/, logs/)
[✅] .env.example con todas las variables necesarias
[✅] .dockerignore configurado

TESTS AUTOMATIZADOS
[✅] 136 tests unitarios (pytest)
[✅] Tests de seguridad SQL
[✅] Tests de charts y agente
[✅] Tests de integración con servicios reales (18 tests — marker: cloud)
[✅] Test de smoke del dashboard vía Playwright (7 tests — marker: e2e)

CI — GITHUB ACTIONS
[⬜] Archivo .github/workflows/ci.yml creado
[⬜] Archivo .github/workflows/cd.yml creado
[⬜] Rama main protegida (branch protection rules)
[⬜] PR obligatorio antes de merge a main

CD — REGISTRY DOCKER
[⬜] Cuenta en Docker Hub  O  repositorio en AWS ECR creado
[⬜] Secret DOCKER_USERNAME agregado en GitHub
[⬜] Secret DOCKER_PASSWORD (o DOCKER_TOKEN) agregado en GitHub
[⬜] Imagen publicada al menos una vez manualmente

CD — SERVIDOR / EC2
[⬜] Servidor con Docker y Docker Compose v2 instalados
[⬜] Usuario deploy sin contraseña sudo para docker
[⬜] Par de llaves SSH (deploy_key) generado
[⬜] Secret SSH_PRIVATE_KEY agregado en GitHub
[⬜] Secret SSH_HOST (IP o dominio del servidor)
[⬜] Secret SSH_USER (usuario del servidor)
[⬜] Carpeta del proyecto copiada al servidor (/opt/fintech)
[⬜] Archivo .env de producción en el servidor

SEGURIDAD Y DOMINIO
[⬜] Firewall: solo puertos 22, 80, 443, 8501 abiertos
[⬜] Reverse proxy Nginx configurado (opcional para HTTPS)
[⬜] Certificado SSL via Certbot/Let's Encrypt (opcional)
[⬜] Dominio apuntando al servidor (opcional)

MONITOREO POST-DEPLOY
[⬜] Health check del dashboard respondiendo
[⬜] Notificación Slack/email configurada (opcional)
[⬜] Rotación de logs con logrotate (opcional)
```

---

## 1. Herramientas del pipeline

| Herramienta | Rol | Versión mínima |
|---|---|---|
| GitHub Actions | Orquestador CI/CD | incluido en GitHub |
| Docker | Build y runtime de contenedores | 24.x |
| Docker Compose v2 | Orquestación multi-servicio | incluido en Docker |
| Docker Hub **o** AWS ECR | Registry de imágenes | cuenta gratuita |
| Ubuntu 22.04 LTS | Sistema operativo del servidor | 22.04 |
| Nginx | Reverse proxy / HTTPS (opcional) | 1.24+ |
| Certbot | Certificados SSL (opcional) | último |
| pytest 9.x | Suite de tests | ya en requirements.txt |

---

## 2. Prerequisitos en el repositorio

El proyecto debe estar en GitHub. Si todavía no, inicializa y sube:

```powershell
cd C:\Users\Alexander\Documents\fintech_pipeline_v3

git init
git add .
git commit -m "initial commit: fintech pipeline v3"

# Crea el repo en GitHub (sin README, sin .gitignore, sin licencia)
# Luego conecta y sube:
git remote add origin https://github.com/TU_USUARIO/fintech-pipeline-v3.git
git branch -M main
git push -u origin main
```

Verifica que `.gitignore` excluya lo crítico:

```gitignore
.env
.env.*
!.env.example
venv/
data/bronze/
data/silver/
data/gold/
outputs/
*.parquet
*.duckdb
```

---

## 3. Estrategia de ramas (Branching)

```text
main          → producción. Solo acepta PRs. CI/CD completo.
develop       → staging / integración. CI corre tests.
feature/*     → desarrollo de funcionalidades. Solo CI.
hotfix/*      → correcciones urgentes a main.
```

Flujo normal:

```text
feature/mi-cambio  →  PR a main  →  CI pasa  →  merge  →  CD despliega
```

Flujo hotfix:

```text
hotfix/fix-critico  →  PR directo a main  →  CI pasa  →  merge  →  CD
```

### Proteger la rama main en GitHub

1. Ve a tu repo → `Settings` → `Branches`.
2. Click en `Add branch protection rule`.
3. Branch name pattern: `main`.
4. Activa:
   - `Require a pull request before merging`
   - `Require status checks to pass before merging`
   - Agrega `ci / test-and-build` como required check
   - `Do not allow bypassing the above settings`
5. Guarda.

---

## 4. Secrets en GitHub

Estos secretos se leen en los workflows de GitHub Actions.

Ve a tu repo → `Settings` → `Secrets and variables` → `Actions` → `New repository secret`.

### Secretos de Docker Registry

```text
DOCKER_USERNAME   →  tu usuario de Docker Hub (ej: alexanderfintech)
DOCKER_PASSWORD   →  token de acceso Docker Hub (NO tu contraseña)
```

Para crear el token de Docker Hub:
1. Entra a https://hub.docker.com → tu usuario → `Account Settings`.
2. Ve a `Security` → `New Access Token`.
3. Nombre: `github-actions-fintech`
4. Permisos: `Read, Write, Delete`
5. Copia el token → pégalo como `DOCKER_PASSWORD` en GitHub Secrets.

Nombre de la imagen que usarás en todo el pipeline:

```text
TU_USUARIO_DOCKERHUB/fintech-pipeline:latest
TU_USUARIO_DOCKERHUB/fintech-pipeline:${{ github.sha }}
```

### Secretos del servidor de producción

```text
SSH_HOST          →  IP pública o dominio del servidor (ej: 54.123.45.67)
SSH_USER          →  usuario SSH del servidor (ej: ubuntu o deploy)
SSH_PRIVATE_KEY   →  contenido completo de la llave privada SSH (-----BEGIN...)
```

### Secretos de la aplicación (para el .env del servidor)

```text
OLLAMA_MODEL              →  llama3.2
AWS_ACCESS_KEY_ID         →  AKIA...
AWS_SECRET_ACCESS_KEY     →  ...
AWS_REGION                →  us-east-1
AWS_BUCKET                →  fintech-pipeline1
DATABRICKS_HOST           →  dbc-...cloud.databricks.com
DATABRICKS_TOKEN          →  dapi...
DATABRICKS_HTTP_PATH      →  /sql/1.0/warehouses/...
DATABRICKS_CATALOG        →  fintech_pipeline
DATABRICKS_SCHEMA         →  fintech
EXCHANGE_RATE_API_KEY     →  (si tienes, si no déjalo vacío)
```

---

## 5. Estructura de carpetas para los workflows

Crea la carpeta `.github/workflows/` en la raíz del proyecto:

```powershell
New-Item -ItemType Directory -Force ".github\workflows"
```

Al terminar esta guía tendrás:

```text
.github/
└── workflows/
    ├── ci.yml    ← tests + build en cualquier rama
    └── cd.yml    ← push a registry + deploy en main
```

---

## 6. Workflow CI — Tests y build

Crea el archivo `.github/workflows/ci.yml`:

```yaml
# CI — corre en cada push y en cada Pull Request
# Valida que el código compile, los tests pasen y la imagen Docker sea buildeable.

name: CI

on:
  push:
    branches: ["**"]          # cualquier rama
  pull_request:
    branches: [main, develop]

jobs:
  test-and-build:
    name: Tests + Docker build
    runs-on: ubuntu-22.04

    steps:
      # 1. Clonar el repositorio
      - name: Checkout código
        uses: actions/checkout@v4

      # 2. Configurar Python 3.12
      - name: Configurar Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.12"
          cache: "pip"

      # 3. Instalar dependencias
      - name: Instalar dependencias
        run: |
          python -m pip install --upgrade pip
          pip install -r requirements.txt

      # 4. Lint con ruff (rápido, detecta errores de sintaxis y estilo)
      - name: Lint (ruff)
        run: |
          pip install ruff
          ruff check src/ tests/ --select E,F,W --ignore E501

      # 5. Correr suite completa de tests unitarios
      - name: Tests unitarios
        run: |
          pytest tests/unit/ -v --tb=short -q
        env:
          PYTHONPATH: .
          FINTECH_DASHBOARD_TEST_MODE: "true"

      # 6. Verificar que la imagen Docker hace build sin errores
      #    (no la publicamos en CI, solo verificamos que compila)
      - name: Docker build (smoke test)
        uses: docker/build-push-action@v5
        with:
          context: .
          push: false
          tags: fintech-pipeline:ci-${{ github.sha }}
          cache-from: type=gha
          cache-to: type=gha,mode=max
```

### Cómo ejecutar los mismos pasos localmente antes de hacer push

```powershell
# Lint
pip install ruff
ruff check src/ tests/ --select E,F,W --ignore E501

# Tests
.\venv\Scripts\python.exe -m pytest tests/unit/ -v --tb=short

# Docker build local
docker build -t fintech-pipeline:local .
```

---

## 7. Workflow CD — Build, push y deploy

Crea el archivo `.github/workflows/cd.yml`:

```yaml
# CD — corre SOLO en push a main (después de merge de un PR)
# Construye la imagen definitiva, la sube al registry y despliega en producción.

name: CD

on:
  push:
    branches: [main]

jobs:
  # ── JOB 1: Tests (igual que CI, evita deploy con código roto) ─────────────
  test:
    name: Verificar tests
    runs-on: ubuntu-22.04
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
          cache: "pip"
      - run: pip install -r requirements.txt
      - run: pytest tests/unit/ -q --tb=short
        env:
          PYTHONPATH: .
          FINTECH_DASHBOARD_TEST_MODE: "true"

  # ── JOB 2: Build y push al registry ──────────────────────────────────────
  build-and-push:
    name: Build y push imagen Docker
    runs-on: ubuntu-22.04
    needs: test           # solo si los tests pasan

    outputs:
      image_tag: ${{ steps.meta.outputs.tags }}

    steps:
      - uses: actions/checkout@v4

      - name: Login a Docker Hub
        uses: docker/login-action@v3
        with:
          username: ${{ secrets.DOCKER_USERNAME }}
          password: ${{ secrets.DOCKER_PASSWORD }}

      - name: Extraer metadata (tags y labels)
        id: meta
        uses: docker/metadata-action@v5
        with:
          images: ${{ secrets.DOCKER_USERNAME }}/fintech-pipeline
          tags: |
            type=sha,prefix=sha-,format=short
            type=raw,value=latest

      - name: Configurar Docker Buildx (builds multi-plataforma y caché)
        uses: docker/setup-buildx-action@v3

      - name: Build y push
        uses: docker/build-push-action@v5
        with:
          context: .
          push: true
          tags: ${{ steps.meta.outputs.tags }}
          labels: ${{ steps.meta.outputs.labels }}
          cache-from: type=gha
          cache-to: type=gha,mode=max

  # ── JOB 3: Deploy al servidor de producción vía SSH ───────────────────────
  deploy:
    name: Deploy a producción
    runs-on: ubuntu-22.04
    needs: build-and-push   # solo si el build pasó

    steps:
      - uses: actions/checkout@v4

      # Copiar docker-compose.yml al servidor
      - name: Copiar docker-compose al servidor
        uses: appleboy/scp-action@v0.1.7
        with:
          host: ${{ secrets.SSH_HOST }}
          username: ${{ secrets.SSH_USER }}
          key: ${{ secrets.SSH_PRIVATE_KEY }}
          source: "docker-compose.yml"
          target: "/opt/fintech"

      # Conectar al servidor y ejecutar el deploy
      - name: Deploy vía SSH
        uses: appleboy/ssh-action@v1.0.3
        with:
          host: ${{ secrets.SSH_HOST }}
          username: ${{ secrets.SSH_USER }}
          key: ${{ secrets.SSH_PRIVATE_KEY }}
          script: |
            set -e
            cd /opt/fintech

            # Escribir .env desde secretos de GitHub (variables de entorno del runner)
            cat > .env <<'ENVEOF'
            OLLAMA_MODEL=${{ secrets.OLLAMA_MODEL }}
            AWS_ACCESS_KEY_ID=${{ secrets.AWS_ACCESS_KEY_ID }}
            AWS_SECRET_ACCESS_KEY=${{ secrets.AWS_SECRET_ACCESS_KEY }}
            AWS_REGION=${{ secrets.AWS_REGION }}
            AWS_BUCKET=${{ secrets.AWS_BUCKET }}
            DATABRICKS_HOST=${{ secrets.DATABRICKS_HOST }}
            DATABRICKS_TOKEN=${{ secrets.DATABRICKS_TOKEN }}
            DATABRICKS_HTTP_PATH=${{ secrets.DATABRICKS_HTTP_PATH }}
            DATABRICKS_CATALOG=${{ secrets.DATABRICKS_CATALOG }}
            DATABRICKS_SCHEMA=${{ secrets.DATABRICKS_SCHEMA }}
            EXCHANGE_RATE_API_KEY=${{ secrets.EXCHANGE_RATE_API_KEY }}
            OLLAMA_BASE_URL=http://host.docker.internal:11434
            ENVEOF

            # Bajar imagen nueva del registry
            docker compose pull dashboard api ecommerce

            # Reiniciar servicios con zero-downtime mínimo
            docker compose --profile dashboard up -d --remove-orphans
            docker compose --profile api      up -d --remove-orphans
            docker compose --profile ecommerce up -d --remove-orphans

            # Esperar a que el dashboard responda (máx 90s)
            echo "Esperando health check del dashboard..."
            for i in $(seq 1 18); do
              if curl -sf http://localhost:8501/_stcore/health > /dev/null 2>&1; then
                echo "Dashboard OK"
                break
              fi
              echo "Intento $i/18 — esperando 5s..."
              sleep 5
            done

            # Limpiar imágenes viejas
            docker image prune -f

      # Verificar health desde GitHub Actions (opcional pero recomendado)
      - name: Verificar health post-deploy
        run: |
          sleep 10
          curl -f http://${{ secrets.SSH_HOST }}:8501/_stcore/health || \
            (echo "Health check FALLÓ" && exit 1)
          echo "Deploy completado y dashboard respondiendo"
```

---

## 8. Preparar el servidor de producción

### 8.1 Crear el servidor (AWS EC2 recomendado)

En AWS Console → EC2 → Launch Instance:

```text
AMI:           Ubuntu Server 22.04 LTS (x86_64)
Instance type: t3.medium   (2 vCPU, 4 GB RAM — mínimo para el stack completo)
               t3.large    (2 vCPU, 8 GB RAM — recomendado con Ollama en host)
Storage:       30 GB gp3
Key pair:      Crea un nuevo par de llaves, descarga el .pem
Security group (reglas de entrada):
  SSH    TCP 22    desde tu IP
  HTTP   TCP 80    desde 0.0.0.0/0  (si usas Nginx)
  HTTPS  TCP 443   desde 0.0.0.0/0  (si usas Nginx + SSL)
  Custom TCP 8501  desde 0.0.0.0/0  (dashboard Streamlit)
  Custom TCP 8000  desde 0.0.0.0/0  (API receptor)
  Custom TCP 8001  desde 0.0.0.0/0  (API ecommerce)
```

### 8.2 Instalar Docker en el servidor

Conecta al servidor:

```bash
ssh -i tu-llave.pem ubuntu@TU_IP
```

Ejecuta el script de instalación:

```bash
# Actualizar paquetes
sudo apt-get update && sudo apt-get upgrade -y

# Instalar Docker (método oficial)
curl -fsSL https://get.docker.com | sudo sh

# Agregar tu usuario al grupo docker (evita sudo en cada comando docker)
sudo usermod -aG docker $USER

# Cerrar sesión y volver a entrar para aplicar el grupo
exit
ssh -i tu-llave.pem ubuntu@TU_IP

# Verificar
docker --version
docker compose version
```

Salida esperada:

```text
Docker version 26.x.x, build ...
Docker Compose version v2.x.x
```

### 8.3 Crear usuario deploy (recomendado para seguridad)

```bash
# Crear usuario deploy
sudo useradd -m -s /bin/bash deploy
sudo usermod -aG docker deploy

# Configurar sudo solo para docker (sin contraseña)
echo "deploy ALL=(ALL) NOPASSWD: /usr/bin/docker, /usr/local/bin/docker" | \
  sudo tee /etc/sudoers.d/deploy-docker

# Cambiar al usuario deploy
sudo su - deploy
```

### 8.4 Generar par de llaves SSH para el deploy

En tu máquina local (PowerShell):

```powershell
# Generar llave SSH dedicada para GitHub Actions
ssh-keygen -t ed25519 -C "github-actions-deploy" -f "$env:USERPROFILE\.ssh\fintech_deploy_key" -N ""

# Ver la llave pública (la necesitas en el servidor)
Get-Content "$env:USERPROFILE\.ssh\fintech_deploy_key.pub"

# Ver la llave privada (la necesitas en GitHub Secrets)
Get-Content "$env:USERPROFILE\.ssh\fintech_deploy_key"
```

Copia la **llave pública** al servidor:

```bash
# En el servidor, como usuario deploy
mkdir -p ~/.ssh
chmod 700 ~/.ssh

# Pega la llave pública (cat fintech_deploy_key.pub)
echo "ssh-ed25519 AAAA... github-actions-deploy" >> ~/.ssh/authorized_keys
chmod 600 ~/.ssh/authorized_keys
```

Copia el **contenido completo** de `fintech_deploy_key` (la privada) al secret `SSH_PRIVATE_KEY` en GitHub. Incluye el encabezado y el pie:

```text
-----BEGIN OPENSSH PRIVATE KEY-----
...todo el contenido...
-----END OPENSSH PRIVATE KEY-----
```

### 8.5 Preparar la carpeta del proyecto en el servidor

```bash
# Como usuario deploy en el servidor
sudo mkdir -p /opt/fintech
sudo chown deploy:deploy /opt/fintech

# Crear estructura de directorios de runtime
mkdir -p /opt/fintech/data/{raw,bronze,silver,gold}
mkdir -p /opt/fintech/outputs/{charts,reports}
mkdir -p /opt/fintech/logs

# Copiar el dataset fuente inicial (solo data/raw/)
# Desde tu máquina local:
scp -i tu-llave.pem -r data/raw/ ubuntu@TU_IP:/opt/fintech/data/
```

### 8.6 Crear el .env de producción en el servidor

```bash
# En el servidor como usuario deploy
nano /opt/fintech/.env
```

Contenido del `.env` de producción:

```env
# LLM — Ollama corre en el HOST, no en Docker
OLLAMA_BASE_URL=http://host.docker.internal:11434
OLLAMA_MODEL=llama3.2

# AWS S3
AWS_ACCESS_KEY_ID=AKIAxxxxxxxxxxxxxx
AWS_SECRET_ACCESS_KEY=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
AWS_REGION=us-east-1
AWS_BUCKET=fintech-pipeline1

# Databricks
DATABRICKS_HOST=dbc-xxxxxxxx.cloud.databricks.com
DATABRICKS_TOKEN=dapiXXXXXXXXXXXXXXXX
DATABRICKS_HTTP_PATH=/sql/1.0/warehouses/XXXXXXXX
DATABRICKS_CATALOG=fintech_pipeline
DATABRICKS_SCHEMA=fintech

# API externa
EXCHANGE_RATE_API_KEY=tu_clave
```

---

## 9. Primer deploy manual (bootstrap)

Antes de que el pipeline automático funcione, haz el primer deploy manual para
verificar que todo está conectado.

En el servidor:

```bash
cd /opt/fintech

# Hacer login al registry
docker login -u TU_USUARIO_DOCKERHUB

# Construir y subir la imagen desde tu máquina local primero:
# (Desde PowerShell en tu PC)
# docker build -t TU_USUARIO/fintech-pipeline:latest .
# docker push TU_USUARIO/fintech-pipeline:latest

# En el servidor, bajar y levantar
docker compose pull dashboard
docker compose --profile dashboard up -d

# Verificar
docker compose ps
docker compose logs -f dashboard
```

Visita en el navegador:

```text
http://TU_IP:8501
```

Deberías ver el dashboard Streamlit de Fintech 360.

---

## 10. Ejecutar el pipeline Bronze → Gold en producción

El pipeline batch es un servicio one-shot (no queda corriendo). Ejecútalo la
primera vez manualmente y luego via cron o workflow manual:

```bash
# Ejecución manual en el servidor
cd /opt/fintech
docker compose --profile pipeline run --rm pipeline

# Verificar que se crearon los Parquets Gold
ls -lh data/gold/
```

Para automatizarlo con cron en el servidor:

```bash
# Abrir crontab del usuario deploy
crontab -e

# Agregar: ejecutar pipeline todos los días a las 2 AM
0 2 * * * cd /opt/fintech && docker compose --profile pipeline run --rm pipeline >> logs/pipeline_cron.log 2>&1
```

Para lanzarlo como workflow manual en GitHub Actions, agrega esto a `cd.yml`:

```yaml
  # Job adicional: pipeline manual bajo demanda
  run-pipeline:
    name: Ejecutar pipeline Gold (manual)
    runs-on: ubuntu-22.04
    if: github.event_name == 'workflow_dispatch'
    steps:
      - name: Ejecutar pipeline en servidor
        uses: appleboy/ssh-action@v1.0.3
        with:
          host: ${{ secrets.SSH_HOST }}
          username: ${{ secrets.SSH_USER }}
          key: ${{ secrets.SSH_PRIVATE_KEY }}
          script: |
            cd /opt/fintech
            docker compose --profile pipeline run --rm pipeline
```

Y al principio de `cd.yml` agrega el trigger:

```yaml
on:
  push:
    branches: [main]
  workflow_dispatch:    # permite ejecutar manualmente desde GitHub UI
```

---

## 11. Nginx como reverse proxy (opcional — para HTTPS)

Si quieres exponer el dashboard en `https://tu-dominio.com` en lugar de
`http://IP:8501`:

```bash
# Instalar Nginx y Certbot
sudo apt-get install -y nginx certbot python3-certbot-nginx

# Crear configuración de Nginx
sudo nano /etc/nginx/sites-available/fintech
```

Contenido de la configuración Nginx:

```nginx
server {
    listen 80;
    server_name tu-dominio.com www.tu-dominio.com;

    location / {
        proxy_pass         http://localhost:8501;
        proxy_http_version 1.1;
        proxy_set_header   Upgrade $http_upgrade;
        proxy_set_header   Connection "upgrade";
        proxy_set_header   Host $host;
        proxy_set_header   X-Real-IP $remote_addr;
        proxy_cache_bypass $http_upgrade;
    }

    # API receptor
    location /api/ {
        proxy_pass http://localhost:8000/;
    }
}
```

```bash
# Activar el sitio
sudo ln -s /etc/nginx/sites-available/fintech /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx

# Obtener certificado SSL (reemplaza tu-dominio.com)
sudo certbot --nginx -d tu-dominio.com -d www.tu-dominio.com
```

---

## 12. Monitoreo y logs

### Ver estado de contenedores en el servidor

```bash
cd /opt/fintech

# Estado general
docker compose ps

# Uso de recursos en tiempo real
docker stats

# Logs del dashboard en tiempo real
docker compose logs -f dashboard

# Últimas 100 líneas de logs del API
docker compose logs --tail=100 api
```

### Health check del dashboard

```bash
# Verificación simple
curl -f http://localhost:8501/_stcore/health && echo "OK" || echo "FALLO"

# Desde tu máquina local
curl -f http://TU_IP:8501/_stcore/health
```

### Rotación de logs (recomendado para producción)

```bash
sudo nano /etc/logrotate.d/fintech-docker
```

```text
/opt/fintech/logs/*.log {
    daily
    rotate 14
    compress
    delaycompress
    missingok
    notifempty
    create 0640 deploy deploy
}
```

---

## 13. Rollback

Si el deploy automático rompe producción, vuelve a la imagen anterior:

### Opción 1 — Rollback rápido en el servidor

```bash
cd /opt/fintech

# Ver imágenes disponibles (la anterior tiene el tag sha-XXXXXXX)
docker images | grep fintech-pipeline

# Editar docker-compose.yml para usar el sha anterior
# image: TU_USUARIO/fintech-pipeline:sha-abc1234

# Reiniciar con la imagen anterior
docker compose --profile dashboard up -d

# Verificar
docker compose ps
curl -f http://localhost:8501/_stcore/health
```

### Opción 2 — Revertir el commit en GitHub y redesplegar

```bash
# En tu máquina local
git log --oneline -5       # identifica el commit bueno
git revert HEAD            # revierte el último commit
git push origin main       # dispara el CD con la versión revertida
```

### Opción 3 — Re-lanzar el CD workflow anterior

En GitHub → tu repo → `Actions` → busca el run exitoso anterior → `Re-run jobs`.

---

## 14. Solución de problemas del pipeline

| Problema | Causa probable | Solución |
|---|---|---|
| `CI falla en lint` | Errores de estilo en código nuevo | Ejecuta `ruff check src/ tests/` localmente y corrige |
| `CI falla en tests` | Test roto o funcionalidad cambiada | Ve a Actions → ver log → corregir código o test |
| `CI falla en docker build` | Error en Dockerfile o requirements.txt | Construye localmente: `docker build .` |
| `CD falla en docker push` | Credenciales Docker Hub incorrectas | Verifica `DOCKER_USERNAME` y `DOCKER_PASSWORD` en Secrets |
| `CD falla en ssh deploy` | IP/usuario/llave SSH incorrectos | Verifica `SSH_HOST`, `SSH_USER`, `SSH_PRIVATE_KEY` |
| `Dashboard no responde post-deploy` | Puerto 8501 bloqueado o contenedor caído | Revisa firewall EC2 y `docker compose logs dashboard` |
| `Ollama no responde desde Docker` | Ollama no escucha en 0.0.0.0 | En el host: `OLLAMA_HOST=0.0.0.0 ollama serve` |
| `host.docker.internal no resuelve` | Linux sin extra_hosts | El compose ya tiene `extra_hosts: host.docker.internal:host-gateway` |
| `Parquets Gold no encontrados` | Pipeline batch no ha corrido | Ejecuta `docker compose --profile pipeline run --rm pipeline` |
| `Error 403 en S3` | Credenciales AWS incorrectas en .env | Verifica el `.env` en `/opt/fintech/.env` del servidor |
| `Databricks no conecta` | Token expirado o host incorrecto | Actualiza `DATABRICKS_TOKEN` en `.env` y reinicia |

---

## 15. Ejecutar el pipeline completo de CI/CD por primera vez

Sigue estos pasos exactamente en orden:

```text
Paso 1  — Sube el código a GitHub con: git push origin main

Paso 2  — Ve a GitHub → tu repo → Actions
           Deberías ver el workflow "CI" ejecutándose.

Paso 3  — Verifica que CI pase los 136 tests.
           Si falla, lee el log, corrige y vuelve a hacer push.

Paso 4  — Si CI pasó, el workflow "CD" empieza automáticamente.

Paso 5  — CD: "Tests" → debe pasar.

Paso 6  — CD: "Build y push imagen Docker" → debe publicar en Docker Hub.
           Verifica en hub.docker.com que la imagen aparece.

Paso 7  — CD: "Deploy a producción" → debe conectar al servidor vía SSH,
           bajar la imagen nueva y reiniciar los contenedores.

Paso 8  — CD: "Verificar health post-deploy" → debe retornar 200 OK.

Paso 9  — Abre http://TU_IP:8501 en el navegador.
           El dashboard Fintech 360 debe estar disponible.

Paso 10 — Ejecuta el pipeline Gold manualmente en el servidor:
           docker compose --profile pipeline run --rm pipeline

Paso 11 — Usa el dashboard → "Mesa de análisis" → haz una pregunta.
           El agente debe responder con datos reales de Gold.
```

---

## 16. Checklist por etapa

### Etapa 1 — Repositorio listo

```text
[ ] git init y primer push a GitHub
[ ] .gitignore excluye .env, data/bronze, data/silver, data/gold, venv/
[ ] Rama main protegida con PR obligatorio
[ ] CI status check requerido para merge
```

### Etapa 2 — CI funcionando

```text
[ ] .github/workflows/ci.yml creado y pusheado
[ ] Workflow CI aparece en GitHub Actions
[ ] Los 136 tests pasan en el runner de GitHub
[ ] Docker build smoke test pasa en CI
```

### Etapa 3 — Registry listo

```text
[ ] Cuenta Docker Hub creada
[ ] Token de acceso Docker Hub generado
[ ] Secrets DOCKER_USERNAME y DOCKER_PASSWORD en GitHub
[ ] Primera imagen publicada manualmente: docker push TU_USUARIO/fintech-pipeline:latest
[ ] Imagen visible en hub.docker.com
```

### Etapa 4 — Servidor listo

```text
[ ] EC2 Ubuntu 22.04 creado (t3.medium mínimo)
[ ] Security Group con puertos 22, 8501, 8000, 8001 abiertos
[ ] Docker y Docker Compose v2 instalados
[ ] Usuario deploy con permisos docker
[ ] Par de llaves SSH generado
[ ] Llave pública en ~/.ssh/authorized_keys del servidor
[ ] Secretos SSH_HOST, SSH_USER, SSH_PRIVATE_KEY en GitHub
[ ] Carpeta /opt/fintech creada con permisos
[ ] data/raw/ copiado al servidor
[ ] .env de producción creado en /opt/fintech/.env
[ ] Ollama instalado y corriendo en el host con OLLAMA_HOST=0.0.0.0
[ ] Modelo llama3.2 descargado en el servidor: ollama pull llama3.2
```

### Etapa 5 — CD funcionando

```text
[ ] .github/workflows/cd.yml creado
[ ] Primer merge a main dispara el CD automáticamente
[ ] Build y push al registry completan sin error
[ ] Deploy SSH ejecuta sin error
[ ] Health check post-deploy pasa
[ ] Dashboard accesible en http://TU_IP:8501
```

### Etapa 6 — Producción verificada

```text
[ ] Pipeline batch Gold ejecutado: docker compose --profile pipeline run --rm pipeline
[ ] Parquets Gold presentes en /opt/fintech/data/gold/
[ ] Dashboard muestra KPIs reales
[ ] Agente responde preguntas con datos de Gold
[ ] Docker sync de gráficos funciona (outputs/charts/ tiene PNGs)
[ ] API FastAPI responde en http://TU_IP:8000/docs
[ ] Logs rotativos configurados (logrotate)
[ ] Cron de pipeline batch configurado (2 AM diario)
```

### Etapa 7 — Seguridad y monitoreo (opcional avanzado)

```text
[ ] Dominio apuntando al servidor
[ ] Nginx como reverse proxy
[ ] Certificado SSL via Certbot (HTTPS)
[ ] Alertas por email/Slack si el health check falla
[ ] Backup de data/gold/ a S3 (cron diario)
[ ] Rotación de access keys AWS cada 90 días
```
