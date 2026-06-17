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

El workflow CD (`cd.yml`) usa **Docker Buildx** con cache de GitHub Actions para acelerar builds:

```yaml
- name: Configurar Docker Buildx
  uses: docker/setup-buildx-action@v3

- name: Build y push
  uses: docker/build-push-action@v5
  with:
    cache-from: type=gha
    cache-to: type=gha,mode=max
```

Esto reduce significativamente el tiempo de build en pushes sucesivos cuando `requirements.txt` no cambia.

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
  Valida que existan los secrets DOCKER_USERNAME y DOCKER_PASSWORD.
  Configura Docker Buildx con cache de GitHub Actions (type=gha).
  Construye la imagen con cache y la publica en Docker Hub:
    DOCKER_USERNAME/fintech-pipeline:latest
    DOCKER_USERNAME/fintech-pipeline:sha-xxxxxxx

Job 3 - deploy-windows
  Corre en tu Windows self-hosted runner.
  Verifica Docker Desktop y C:\fintech_pipeline_deploy\.env.
  Prepara carpeta de despliegue y copia docker-compose.yml + data/raw/.
  Hace docker pull de la imagen latest.
  Si la imagen no es publica, intenta login local a Docker Hub.
  La etiqueta como fintech-pipeline:latest.
  Elimina contenedores previos (fintech-dashboard, fintech-api, fintech-ecommerce) si existen.
  Recrea dashboard, api y ecommerce con --force-recreate --no-build.
  Valida health local (hasta 18 reintentos cada 5s = 90s de espera).

Job 4 - run-pipeline-windows
  Solo corre si lanzas el workflow manualmente con run_pipeline=true.
```

El paso de limpieza de contenedores previos es necesario porque Docker no permite dos contenedores con el mismo nombre global. El workflow elimina los contenedores con `docker rm -f` antes de recrearlos para evitar el error `Conflict: container name already in use`.

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

Tu dashboard ya corre localmente en:

```text
http://127.0.0.1:8501
```

Para que otra persona lo pueda abrir desde internet necesitas publicar ese
puerto local. Hay tres caminos:

```text
ngrok
  Mas rapido para demo. No necesitas abrir puertos del router.
  En plan gratis usa dominio dev de ngrok; dominio personalizado requiere plan pago.

Cloudflare Tunnel
  Mejor para demo mas seria. No necesitas abrir puertos del router.
  Con Quick Tunnel obtienes URL temporal trycloudflare.com.
  Para hostname estable necesitas un dominio agregado a Cloudflare.

DuckDNS
  Subdominio gratis tipo tuapp.duckdns.org.
  Normalmente necesitas abrir puertos en router y no estar detras de CGNAT.
```

Antes de cualquiera de las opciones, verifica:

```powershell
docker ps
Invoke-WebRequest http://127.0.0.1:8501/_stcore/health -UseBasicParsing
```

La respuesta esperada del health es:

```text
ok
```

---

### 12.1 Opcion A - ngrok

Usala si quieres compartir rapido el dashboard en una sustentacion, demo o
prueba externa.

#### Paso 1 - Crear cuenta

Entra a:

```text
https://dashboard.ngrok.com/signup
```

Crea cuenta o inicia sesion.

#### Paso 2 - Instalar ngrok en Windows

Opcion con instalador oficial:

```text
https://ngrok.com/download
```

Opcion con Windows Package Manager:

```powershell
winget install ngrok.ngrok
```

Verifica:

```powershell
ngrok version
ngrok help
```

#### Paso 3 - Configurar authtoken

En el dashboard de ngrok busca tu authtoken:

```text
https://dashboard.ngrok.com/get-started/your-authtoken
```

Configuralo:

```powershell
ngrok config add-authtoken TU_TOKEN_DE_NGROK
```

No pegues ese token en GitHub ni en capturas.

#### Paso 4 - Levantar el dashboard local

Si ya esta corriendo por Docker, solo verifica:

```powershell
Invoke-WebRequest http://127.0.0.1:8501/_stcore/health -UseBasicParsing
```

Si no esta corriendo:

```powershell
docker compose --profile dashboard up -d --force-recreate dashboard
```

#### Paso 5 - Publicar puerto 8501

Ejecuta:

```powershell
ngrok http 8501
```

Tambien puedes ser explicito:

```powershell
ngrok http http://127.0.0.1:8501
```

ngrok mostrara algo parecido a:

```text
Forwarding  https://xxxxx.ngrok-free.app -> http://localhost:8501
```

Abre la URL `https://xxxxx.ngrok-free.app` en el navegador.

#### Paso 6 - Compartir URL

Comparte solo la URL publica:

```text
https://xxxxx.ngrok-free.app
```

No compartas:

```text
http://127.0.0.1:8501
```

porque `127.0.0.1` solo funciona en tu propia maquina.

#### Paso 7 - Apagar ngrok

En la terminal donde corre ngrok:

```text
CTRL + C
```

Cuando apagas ngrok, la URL publica deja de funcionar.

#### Notas importantes de ngrok

```text
Ventaja:
  Es el camino mas rapido.

Desventaja:
  La URL puede cambiar en plan gratis.

Dominio propio:
  Si quieres algo como app.tudominio.com con ngrok, normalmente necesitas un
  dominio propio y plan de ngrok que permita custom domains.
```

---

### 12.2 Opcion B - Cloudflare Tunnel

Usala si quieres publicar sin abrir puertos y con una ruta mas cercana a
produccion.

Cloudflare tiene dos estilos:

```text
Quick Tunnel
  Rapido, temporal, sin dominio propio.
  URL tipo https://algo.trycloudflare.com.

Named Tunnel con hostname
  Mas estable.
  Requiere cuenta Cloudflare y tener un dominio agregado a Cloudflare.
```

#### Camino rapido - Quick Tunnel

##### Paso 1 - Instalar cloudflared

Descarga desde:

```text
https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/
```

O con Windows Package Manager:

```powershell
winget install Cloudflare.cloudflared
```

Verifica:

```powershell
cloudflared --version
```

##### Paso 2 - Verificar dashboard local

```powershell
Invoke-WebRequest http://127.0.0.1:8501/_stcore/health -UseBasicParsing
```

##### Paso 3 - Crear tunel temporal

```powershell
cloudflared tunnel --url http://localhost:8501
```

La consola mostrara una URL similar a:

```text
https://nombre-aleatorio.trycloudflare.com
```

Abrela en el navegador y valida el dashboard.

##### Paso 4 - Apagar Quick Tunnel

En la terminal:

```text
CTRL + C
```

Cuando apagas `cloudflared`, la URL temporal deja de funcionar.

#### Camino estable - Cloudflare con dominio propio

Este camino es estable, pero requiere tener un dominio agregado a Cloudflare.
El dominio puede ser comprado en cualquier registrador. Cloudflare Tunnel puede
ser gratis, pero el dominio profesional normalmente no lo es.

##### Paso 1 - Crear cuenta Cloudflare

```text
https://dash.cloudflare.com/sign-up
```

##### Paso 2 - Agregar tu dominio

En Cloudflare:

```text
Websites -> Add a site
```

Cloudflare te pedira cambiar los nameservers del dominio en tu registrador.
Hasta que el dominio no este activo en Cloudflare, no podras crear un hostname
estable tipo:

```text
fintech.tudominio.com
```

##### Paso 3 - Crear Tunnel desde dashboard

Ruta:

```text
Cloudflare Dashboard -> Zero Trust -> Networks -> Tunnels
```

Luego:

```text
Create a tunnel
Connector type: Cloudflared
Name: fintech-dashboard
Save tunnel
```

##### Paso 4 - Instalar y ejecutar connector en Windows

Cloudflare te mostrara un comando especifico para Windows. Copialo y ejecutalo
en PowerShell. Sera parecido a:

```powershell
cloudflared.exe service install TOKEN_QUE_DA_CLOUDFLARE
```

o un comando de ejecucion directa. Usa el que te da el dashboard, porque ese
token identifica tu tunnel.

##### Paso 5 - Crear public hostname

En el tunnel:

```text
Public Hostname -> Add a public hostname
```

Configura:

```text
Subdomain: fintech
Domain: tudominio.com
Type: HTTP
URL: localhost:8501
```

Quedara:

```text
https://fintech.tudominio.com
```

##### Paso 6 - Validar

Abre:

```text
https://fintech.tudominio.com
```

Si falla, revisa:

```powershell
cloudflared tunnel list
cloudflared tunnel info fintech-dashboard
docker ps
Invoke-WebRequest http://127.0.0.1:8501/_stcore/health -UseBasicParsing
```

#### Notas importantes de Cloudflare

```text
Ventaja:
  No requiere abrir puertos del router.

Desventaja:
  URL temporal con Quick Tunnel; hostname estable requiere dominio propio.

Recomendacion:
  Usa Quick Tunnel para probar hoy.
  Usa dominio propio + Tunnel si quieres una URL seria y estable.
```

---

### 12.3 Opcion C - DuckDNS

DuckDNS te da un subdominio gratis:

```text
tu-subdominio.duckdns.org
```

Pero a diferencia de ngrok y Cloudflare Tunnel, DuckDNS no crea un tunel. Solo
hace que el nombre apunte a tu IP publica. Por eso normalmente necesitas:

```text
IP publica real
Port forwarding en el router
Firewall de Windows permitiendo el puerto
Docker exponiendo el puerto 8501
```

Si tu proveedor usa CGNAT, DuckDNS puede actualizar bien el dominio, pero nadie
podra entrar desde internet a tu PC. En ese caso usa ngrok o Cloudflare Tunnel.

#### Paso 1 - Crear cuenta DuckDNS

Entra a:

```text
https://www.duckdns.org
```

Inicia sesion con alguno de los metodos disponibles.

#### Paso 2 - Crear subdominio

En DuckDNS:

```text
sub domain: fintech-alexander
add domain
```

Tu URL sera:

```text
fintech-alexander.duckdns.org
```

Guarda:

```text
DOMAIN = fintech-alexander
TOKEN  = token que muestra DuckDNS
```

No subas el token a GitHub.

#### Paso 3 - Probar actualizacion de IP publica

En PowerShell:

```powershell
$domain = "fintech-alexander"
$token = "TU_TOKEN_DUCKDNS"
$url = "https://www.duckdns.org/update?domains=$domain&token=$token&ip="
Invoke-WebRequest -Uri $url -UseBasicParsing
```

Respuesta esperada:

```text
OK
```

Si devuelve:

```text
KO
```

revisa dominio o token.

#### Paso 4 - Automatizar actualizacion de IP

Crea carpeta:

```powershell
New-Item -ItemType Directory -Force C:\duckdns
```

Crea archivo:

```text
C:\duckdns\update-duckdns.ps1
```

Contenido:

```powershell
$domain = "fintech-alexander"
$token = "TU_TOKEN_DUCKDNS"
$log = "C:\duckdns\duckdns.log"
$url = "https://www.duckdns.org/update?domains=$domain&token=$token&ip="

try {
    $response = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 20
    "$(Get-Date -Format s) $($response.Content)" | Out-File -FilePath $log -Append -Encoding utf8
} catch {
    "$(Get-Date -Format s) ERROR $($_.Exception.Message)" | Out-File -FilePath $log -Append -Encoding utf8
}
```

Prueba:

```powershell
powershell -ExecutionPolicy Bypass -File C:\duckdns\update-duckdns.ps1
Get-Content C:\duckdns\duckdns.log -Tail 5
```

#### Paso 5 - Crear tarea programada en Windows

Ejecuta PowerShell como Administrador:

```powershell
$action = New-ScheduledTaskAction `
  -Execute "powershell.exe" `
  -Argument "-ExecutionPolicy Bypass -File C:\duckdns\update-duckdns.ps1"

$trigger = New-ScheduledTaskTrigger `
  -Once `
  -At (Get-Date).Date `
  -RepetitionInterval (New-TimeSpan -Minutes 5) `
  -RepetitionDuration (New-TimeSpan -Days 3650)

Register-ScheduledTask `
  -TaskName "DuckDNS Fintech Updater" `
  -Action $action `
  -Trigger $trigger `
  -Description "Actualiza IP publica de DuckDNS para fintech pipeline"
```

Verifica:

```powershell
Get-ScheduledTask -TaskName "DuckDNS Fintech Updater"
```

#### Paso 6 - Fijar IP local de tu PC

En tu router, reserva una IP para tu PC, por ejemplo:

```text
192.168.1.50
```

Tambien puedes revisar tu IP local con:

```powershell
ipconfig
```

Busca la interfaz activa y el campo:

```text
IPv4 Address
```

#### Paso 7 - Abrir puerto en el router

Entra al panel del router y crea port forwarding:

```text
External port: 8501
Internal IP:   192.168.1.50
Internal port: 8501
Protocol:      TCP
```

Cada router cambia el nombre:

```text
Port Forwarding
Virtual Server
NAT
Applications
```

#### Paso 8 - Permitir puerto en Firewall de Windows

PowerShell como Administrador:

```powershell
New-NetFirewallRule `
  -DisplayName "Fintech Dashboard 8501" `
  -Direction Inbound `
  -Protocol TCP `
  -LocalPort 8501 `
  -Action Allow
```

#### Paso 9 - Verificar acceso local

```powershell
Invoke-WebRequest http://127.0.0.1:8501/_stcore/health -UseBasicParsing
```

#### Paso 10 - Verificar acceso desde internet

Desde un celular usando datos moviles, no Wi-Fi, abre:

```text
http://fintech-alexander.duckdns.org:8501
```

Si funciona en datos moviles, ya esta expuesto publicamente.

#### Paso 11 - Problemas comunes con DuckDNS

```text
El dominio resuelve pero no abre:
  Falta port forwarding o firewall.

Funciona en Wi-Fi pero no desde datos moviles:
  Probablemente estas probando desde la misma red. Prueba fuera de casa.

No funciona desde ningun lado:
  Puede haber CGNAT. Usa ngrok o Cloudflare Tunnel.

Quiero HTTPS:
  DuckDNS solo resuelve DNS. Para HTTPS necesitas reverse proxy/certificado,
  por ejemplo Caddy, Nginx o Cloudflare Tunnel.
```

#### Nota de seguridad para DuckDNS

DuckDNS expone directamente tu puerto local a internet. Para demo academica es
mejor ngrok o Cloudflare Tunnel, porque puedes apagar el tunel al terminar y no
dejas un puerto abierto permanentemente.

---

### 12.4 Recomendacion Final

Para tu caso actual:

```text
1. Usa ngrok primero.
2. Si quieres algo mas serio sin abrir puertos, usa Cloudflare Tunnel.
3. Usa DuckDNS solo si entiendes port forwarding y tu internet no tiene CGNAT.
```

Orden recomendado para probar:

```powershell
# 1. Confirmar dashboard
Invoke-WebRequest http://127.0.0.1:8501/_stcore/health -UseBasicParsing

# 2. Demo rapida
ngrok http 8501

# 3. Alternativa sin abrir puertos
cloudflared tunnel --url http://localhost:8501
```

Fuentes oficiales:

```text
ngrok Agent CLI Quickstart:
https://ngrok.com/docs/getting-started

ngrok Domains:
https://ngrok.com/docs/universal-gateway/domains

Cloudflare Tunnel:
https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/get-started/create-remote-tunnel/

Cloudflare Quick Tunnels:
https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/do-more-with-tunnels/trycloudflare/

DuckDNS install:
https://www.duckdns.org/install.jsp
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

### Conflict: container name already in use

Significa que ya existe un contenedor con ese nombre, por ejemplo:

```text
fintech-dashboard
fintech-api
fintech-ecommerce
```

Docker no permite dos contenedores con el mismo nombre global aunque vengan de
carpetas distintas. El CD Windows ya elimina esos contenedores antes de
recrearlos.

Solucion manual:

```powershell
docker rm -f fintech-dashboard fintech-api fintech-ecommerce
docker compose --profile dashboard up -d --force-recreate dashboard
docker compose --profile api --profile ecommerce up -d --force-recreate api ecommerce
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

En el deploy Windows el workflow intenta primero:

```powershell
docker pull DOCKER_USERNAME/fintech-pipeline:latest
```

Si tu repositorio de Docker Hub es publico, no deberia necesitar login local.
Si es privado, el workflow intentara login local despues del primer pull fallido.

Si el login local sigue fallando pero el build/push en GitHub pasa, prueba una
de estas dos rutas:

```text
Ruta simple para demo:
  Deja el repositorio Docker Hub como publico.

Ruta privada:
  Ejecuta docker login manualmente en el mismo Windows donde corre el runner.
  Usa el usuario de Docker Hub y el mismo Access Token.
```

---

## 22. Commit Recomendado

Cuando todo este listo:

```powershell
git add .github/workflows/cd.yml .github/workflows/ci.yml docker-compose.yml `
        docs/CICD_DEPLOY.md docs/DOCKER_DEPLOY.md docs/AGENT_CONTROL_DETERMINISTICO.md `
        README.md src/agent/app.py src/agent/agent.py tests/ui/test_dashboard_app.py
git commit -m "feat: rediseno dashboard dark theme + mejoras CD + control deterministico agente"
git push origin master
```
