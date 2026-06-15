# CI/CD Deploy Pipeline - Fintech Pipeline v3

Esta guia separa el despliegue en dos caminos claros:

```text
MITAD 1 - WINDOWS GRATIS
  Tu PC con Windows + Docker Desktop + GitHub self-hosted runner.
  Es el camino activo de este proyecto ahora mismo.

MITAD 2 - LINUX GRATIS
  Una maquina virtual Linux local, un PC viejo, o un servidor gratuito.
  No requiere EC2. EC2 es solo una opcion paga/con free tier limitado.
```

La idea central:

```text
CI = integracion continua
  GitHub revisa el codigo: lint, tests y Docker build.

CD = despliegue continuo
  GitHub publica la imagen en Docker Hub y luego actualiza la app donde corre.
```

En este repositorio la rama real es:

```text
master
```

Por eso los workflows estan configurados para `master`, no para `main`.

---

## 1. Estado Actual Recomendado

Para tu caso actual, sigue este camino:

```text
Windows local gratis
CI en GitHub Actions
Docker Hub como registry
CD en tu propio Windows con self-hosted runner
Dashboard publico opcional con ngrok o Cloudflare Tunnel
```

Esto evita pagar EC2.

Limitacion importante:

```text
Si tu PC esta apagado, suspendido, sin internet, sin Docker Desktop o sin runner,
el deploy automatico y el dashboard dejan de estar disponibles.
```

---

## 2. Archivos del Proyecto

```text
.github/workflows/ci.yml
  Corre en cada push. Hace lint, tests y Docker build smoke test.

.github/workflows/cd.yml
  Corre en push a master o manualmente.
  Construye y sube la imagen a Docker Hub.
  Luego despliega en Windows usando self-hosted runner.

Dockerfile
  Construye la imagen fintech-pipeline.

docker-compose.yml
  Levanta dashboard, api, ecommerce, pipeline, bus y dev.

.env
  Variables reales locales. No se sube a GitHub.

.env.example
  Plantilla segura para documentar variables.
```

---

## 3. Secretos Necesarios en GitHub

Ruta:

```text
GitHub repo -> Settings -> Secrets and variables -> Actions -> New repository secret
```

Usa la pestaña `Secrets`, no `Variables`.

Para Windows local solo necesitas:

```text
DOCKER_USERNAME = tu usuario de Docker Hub
DOCKER_PASSWORD = token de Docker Hub, no tu contrasena
```

Los nombres deben ser exactamente esos:

```text
DOCKER_USERNAME
DOCKER_PASSWORD
```

Si los creas como `DOCKERHUB_USERNAME`, `DOCKER_TOKEN`, en otro repositorio, o
en la pestaña `Variables`, el workflow no los encontrara y Docker mostrara:

```text
Error: Username and password required
```

No necesitas estos secretos para Windows local:

```text
SSH_HOST
SSH_USER
SSH_PRIVATE_KEY
AWS_ACCESS_KEY_ID en GitHub
DATABRICKS_TOKEN en GitHub
```

Por que no:

```text
Tu .env local vive en tu PC.
El deploy corre en tu propio Windows.
No hay servidor remoto al que GitHub deba entrar por SSH.
```

Solo agregarias secretos de aplicacion en GitHub si decides que GitHub cree un
`.env` en un servidor remoto.

---

## 4. CI - Integracion Continua

El CI ya quedo listo cuando GitHub mostro verde:

```text
Lint (ruff)
Tests unitarios
Docker build smoke test
```

Comandos equivalentes en local:

```powershell
venv\Scripts\ruff.exe check src tests
venv\Scripts\pytest.exe tests\unit -v --tb=short -q
docker build -t fintech-pipeline:local .
```

El workflow CI esta en:

```text
.github/workflows/ci.yml
```

---

# MITAD 1 - WINDOWS GRATIS

Esta es la ruta que debes seguir ahora.

---

## 5. Requisitos Windows

Necesitas tener abierto o configurado:

```text
Docker Desktop
Ollama
GitHub self-hosted runner
Docker Hub secrets en GitHub
Archivo .env local
```

Verificaciones rapidas:

```powershell
docker --version
docker compose version
ollama list
```

---

## 6. Preparar Carpeta Local de Deploy

El CD de Windows usa esta carpeta fija:

```text
C:\fintech_pipeline_deploy
```

Crear carpeta y copiar `.env`:

```powershell
New-Item -ItemType Directory -Force C:\fintech_pipeline_deploy
Copy-Item .env C:\fintech_pipeline_deploy\.env
```

Ese `.env` no se sube a GitHub.

El workflow copia automaticamente:

```text
docker-compose.yml
data/raw/
```

y crea:

```text
data/bronze/
data/silver/
data/gold/
outputs/charts/
outputs/reports/
logs/
```

---

## 7. Instalar GitHub Self-hosted Runner en Windows

En GitHub:

```text
Repo -> Settings -> Actions -> Runners -> New self-hosted runner -> Windows
```

GitHub te mostrara comandos parecidos a estos:

```powershell
mkdir C:\actions-runner
cd C:\actions-runner

# Descargar y descomprimir el runner segun el comando que te da GitHub.
.\config.cmd --url https://github.com/TU_USUARIO/TU_REPO --token TOKEN_TEMPORAL
```

Cuando pregunte:

```text
Enter the name of runner group...
Enter the name of runner...
Enter any additional labels...
Enter name of work folder...
```

puedes presionar Enter en todo.

Debe quedar con labels:

```text
self-hosted
Windows
X64
```

Ejecutar el runner:

```powershell
.\run.cmd
```

Si ves:

```text
Connected to GitHub
Listening for Jobs
```

esta bien. Significa que tu Windows esta esperando trabajos.

### Instalar como servicio opcional

Si quieres que el runner quede corriendo aunque cierres la consola:

1. Abre PowerShell como Administrador.
2. Entra a la carpeta del runner:

```powershell
cd C:\actions-runner
.\svc install
.\svc start
```

Para pruebas iniciales, `.\run.cmd` manual es suficiente.

---

## 8. CD Windows - Como Funciona

El workflow actual `.github/workflows/cd.yml` hace esto:

```text
Job 1 - test
  Corre tests unitarios en GitHub Ubuntu runner.

Job 2 - build-and-push
  Construye la imagen Docker.
  Publica:
    DOCKER_USERNAME/fintech-pipeline:latest
    DOCKER_USERNAME/fintech-pipeline:sha-xxxxxxx

Job 3 - deploy-windows
  Corre en tu Windows self-hosted runner.
  Hace docker pull de la imagen latest.
  La etiqueta como fintech-pipeline:latest.
  Recrea dashboard, api y ecommerce.
  Valida health local.

Job 4 - run-pipeline-windows
  Solo corre si lanzas el workflow manualmente con run_pipeline=true.
```

---

## 9. Ejecutar CD Windows

Antes de ejecutar:

```text
Docker Desktop abierto
Ollama corriendo
Runner escuchando con .\run.cmd
C:\fintech_pipeline_deploy\.env existente
```

Opcion 1 - automatico:

```powershell
git push origin master
```

Opcion 2 - manual desde GitHub:

```text
Repo -> Actions -> CD -> Run workflow
```

Si quieres que tambien ejecute el pipeline batch:

```text
run_pipeline = true
```

---

## 10. Verificar Deploy Windows

Dashboard:

```powershell
Invoke-WebRequest http://127.0.0.1:8501/_stcore/health -UseBasicParsing
```

APIs:

```powershell
Invoke-WebRequest http://127.0.0.1:8000/health -UseBasicParsing
Invoke-WebRequest http://127.0.0.1:8001/docs -UseBasicParsing
```

Contenedores:

```powershell
docker ps
```

URLs:

```text
Dashboard      http://127.0.0.1:8501
API receptor   http://127.0.0.1:8000/docs
API ecommerce  http://127.0.0.1:8001/docs
```

---

## 11. Comandos Manuales Windows

Si no quieres esperar al CD, puedes hacer deploy manual:

```powershell
docker login
docker pull TU_USUARIO_DOCKERHUB/fintech-pipeline:latest
docker tag TU_USUARIO_DOCKERHUB/fintech-pipeline:latest fintech-pipeline:latest
docker compose --profile dashboard up -d --force-recreate dashboard
docker compose --profile api --profile ecommerce up -d --force-recreate api ecommerce
```

Pipeline batch:

```powershell
docker compose --profile pipeline run --rm pipeline
```

Logs:

```powershell
docker compose logs -f dashboard
docker compose logs -f api
docker compose logs -f ecommerce
```

---

## 12. Publicar Dashboard Gratis desde Windows

Para compartir tu dashboard sin pagar servidor, necesitas un tunel publico al
puerto local `8501`.

### Opcion recomendada para demo rapida: ngrok

```powershell
ngrok http 8501
```

Te entrega una URL HTTPS publica. En plan gratuito puede cambiar la URL y tiene
limites, pero para demos academicas suele ser suficiente.

### Opcion alternativa: Cloudflare Tunnel

```powershell
cloudflared tunnel --url http://localhost:8501
```

Puede entregar una URL temporal publica. Para usar un dominio propio estable en
Cloudflare normalmente debes tener un dominio agregado a Cloudflare.

### Opcion con subdominio gratis: DuckDNS

```text
tu-subdominio.duckdns.org
```

DuckDNS es DNS dinamico gratis, pero normalmente requiere abrir puertos en tu
router y que tu proveedor de internet permita conexiones entrantes. Por eso es
menos comodo que ngrok o Cloudflare Tunnel para una demo rapida.

Conclusion practica:

```text
Demo rapida            -> ngrok
Demo un poco mas seria -> Cloudflare Tunnel
Subdominio gratis      -> DuckDNS, si puedes abrir puertos
Dominio profesional    -> normalmente hay que comprar dominio
```

---

# MITAD 2 - LINUX GRATIS

Esta mitad es opcional. Usala solo si mas adelante quieres desplegar en Linux
sin pagar EC2.

---

## 13. Linux sin EC2: Opciones Gratis

Si, puedes usar una maquina virtual en vez de EC2.

Opciones:

```text
Linux VM en tu Windows
  VirtualBox, VMware Workstation Player o Hyper-V.
  Instalas Ubuntu Server dentro de la VM.
  Gratis, pero tu PC debe estar encendido.

PC viejo o mini PC con Linux
  Mejor que VM si quieres algo mas estable en casa.
  Gratis si ya tienes el equipo.

Proveedor gratuito
  Puede existir free tier, pero cambia con el tiempo y puede pedir tarjeta.
  No lo tomes como garantia permanente.
```

EC2 no es obligatorio.

EC2 es solo:

```text
una maquina virtual en AWS
```

Puedes reemplazarla por:

```text
Ubuntu Server en VirtualBox
Ubuntu Server en Hyper-V
Un PC propio con Linux
Un VPS gratuito si consigues uno confiable
```

---

## 14. Requisitos Linux VM

Recomendado para la VM:

```text
Ubuntu Server 22.04 o 24.04
2 CPU
4 GB RAM minimo
20 GB disco minimo
Red en modo Bridge si quieres acceder desde tu red local
```

Si usas tunel Cloudflare/ngrok, no necesitas abrir puertos en el router.

---

## 15. Instalar Docker en Linux

Dentro de la VM Linux:

```bash
sudo apt-get update
sudo apt-get upgrade -y
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER
```

Cierra sesion y vuelve a entrar.

Verifica:

```bash
docker --version
docker compose version
```

---

## 16. Deploy Linux Manual Gratis

En la VM:

```bash
mkdir -p ~/fintech_pipeline_deploy
cd ~/fintech_pipeline_deploy
```

Copia estos archivos desde tu Windows a la VM:

```text
docker-compose.yml
.env
data/raw/
```

Puedes usar Git, SCP, carpeta compartida de VirtualBox, o copiar manualmente.

Estructura esperada:

```text
~/fintech_pipeline_deploy/
  docker-compose.yml
  .env
  data/raw/
```

Pull y deploy:

```bash
docker login
docker pull TU_USUARIO_DOCKERHUB/fintech-pipeline:latest
docker tag TU_USUARIO_DOCKERHUB/fintech-pipeline:latest fintech-pipeline:latest
docker compose --profile dashboard up -d --force-recreate dashboard
docker compose --profile api --profile ecommerce up -d --force-recreate api ecommerce
```

Verifica:

```bash
curl -f http://localhost:8501/_stcore/health
curl -f http://localhost:8000/health
```

---

## 17. CD Linux Gratis con Self-hosted Runner

Tambien puedes instalar GitHub self-hosted runner dentro de la VM Linux.

En GitHub:

```text
Repo -> Settings -> Actions -> Runners -> New self-hosted runner -> Linux
```

GitHub te dara comandos para descargar/configurar el runner.

Labels esperados:

```text
self-hosted
Linux
X64
```

Para usar Linux self-hosted, necesitas un workflow separado o adaptar
`.github/workflows/cd.yml`:

```yaml
runs-on: [self-hosted, Linux]
```

y cambiar los pasos PowerShell por Bash:

```bash
docker pull "$IMAGE_NAME:latest"
docker tag "$IMAGE_NAME:latest" fintech-pipeline:latest
docker compose --profile dashboard up -d --force-recreate --no-build dashboard
docker compose --profile api --profile ecommerce up -d --force-recreate --no-build api ecommerce
```

Recomendacion:

```text
Mantener activo cd.yml para Windows.
Crear otro workflow solo si realmente vas a usar Linux.
```

---

## 18. Publicar Linux Gratis con Tunel

Dentro de la VM Linux puedes usar:

```bash
ngrok http 8501
```

o:

```bash
cloudflared tunnel --url http://localhost:8501
```

Esto evita abrir puertos del router.

Si quieres usar DuckDNS:

```text
tu-subdominio.duckdns.org
```

pero probablemente necesitaras:

```text
IP publica residencial
port forwarding en router
firewall abierto
```

---

## 19. Windows vs Linux: Cual Elegir

```text
Quiero avanzar ya, gratis y sin complicarme:
  Windows + Docker Desktop + self-hosted runner + ngrok.

Quiero aprender deploy tipo servidor pero gratis:
  Linux VM + Docker + self-hosted runner + Cloudflare Tunnel/ngrok.

Quiero produccion real 24/7:
  Servidor externo, VPS o cloud. Puede ser pago.

Quiero dominio profesional:
  Normalmente hay que comprar dominio.
  Para demo gratis usa ngrok, Cloudflare Tunnel temporal o DuckDNS.
```

---

## 20. Checklist Windows Actual

```text
[ ] CI pasa en GitHub
[ ] DOCKER_USERNAME creado en GitHub Secrets
[ ] DOCKER_PASSWORD creado en GitHub Secrets
[ ] Imagen visible en Docker Hub
[ ] Docker Desktop abierto
[ ] Ollama corriendo
[ ] C:\fintech_pipeline_deploy\.env existe
[ ] Self-hosted runner Windows muestra "Listening for Jobs"
[ ] GitHub Actions -> CD -> Run workflow ejecuta sin error
[ ] Dashboard responde en http://127.0.0.1:8501
[ ] API responde en http://127.0.0.1:8000/docs
[ ] Ecommerce responde en http://127.0.0.1:8001/docs
[ ] Tunel publico configurado si necesitas compartir la demo
```

---

## 21. Troubleshooting

### No aparece el workflow CD

Revisa:

```powershell
git status
git add .github/workflows/cd.yml
git commit -m "ci(cd): configurar deploy windows"
git push origin master
```

Luego entra en:

```text
GitHub repo -> Actions
```

No busques el workflow en `Settings -> Actions -> Runners`; esa pantalla solo
administra runners.

### El runner no recibe trabajos

Debe verse:

```text
Listening for Jobs
```

Y el workflow debe tener:

```yaml
runs-on: [self-hosted, Windows]
```

### Error `pwsh: command not found`

`pwsh` es PowerShell 7. Muchos Windows tienen solo Windows PowerShell clasico,
cuyo ejecutable es `powershell`.

Este proyecto usa `shell: powershell` en el deploy Windows para no obligarte a
instalar PowerShell 7.

Si prefieres usar `pwsh`, instala PowerShell 7 y asegurate de que quede en el
PATH del usuario que ejecuta el runner.

### Falta el .env

El CD falla si no existe:

```text
C:\fintech_pipeline_deploy\.env
```

Solucion:

```powershell
New-Item -ItemType Directory -Force C:\fintech_pipeline_deploy
Copy-Item .env C:\fintech_pipeline_deploy\.env
```

### Ecommerce falla por api undefined

Usa ambos perfiles:

```powershell
docker compose --profile api --profile ecommerce up -d api ecommerce
```

### Dashboard no responde

Revisa:

```powershell
docker ps
docker compose logs --tail=100 dashboard
Invoke-WebRequest http://127.0.0.1:8501/_stcore/health -UseBasicParsing
```

### Docker Hub login falla en CD

Revisa GitHub Secrets:

```text
DOCKER_USERNAME
DOCKER_PASSWORD
```

`DOCKER_PASSWORD` debe ser token de Docker Hub, no tu contrasena normal.

Tambien revisa:

```text
1. Que esten en Repository secrets, no en Variables.
2. Que esten en el mismo repositorio donde corre Actions.
3. Que los nombres sean exactamente DOCKER_USERNAME y DOCKER_PASSWORD.
4. Que DOCKER_USERNAME sea tu usuario de Docker Hub, no tu email.
5. Que DOCKER_PASSWORD sea un Access Token vigente de Docker Hub.
```

---

## 22. Commit Recomendado

Cuando todo este listo:

```powershell
git add .github/workflows/cd.yml .github/workflows/ci.yml docker-compose.yml docs/CICD_DEPLOY.md docs/DOCKER_DEPLOY.md
git commit -m "ci(cd): organizar despliegue windows y linux"
git push origin master
```
