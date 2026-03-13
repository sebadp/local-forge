# LocalForge - Guía de Setup y Testing

## Requisitos previos

- Docker y Docker Compose
- Cuenta de Meta Developer (gratis): https://developers.facebook.com
- Cuenta de ngrok (gratis): https://ngrok.com
- (Opcional) GPU NVIDIA con `nvidia-container-toolkit` instalado

### Instalar nvidia-container-toolkit (solo si tenés GPU NVIDIA)

```bash
# Agregar clave GPG
curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg --yes

# Agregar repositorio
curl -s -L https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' | sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list

# Instalar
sudo apt-get update && sudo apt-get install -y nvidia-container-toolkit

# Configurar Docker y reiniciar
sudo nvidia-ctk runtime configure --runtime=docker && sudo systemctl restart docker
```

---

## Paso 1: ngrok

1. Crear cuenta en https://dashboard.ngrok.com/signup
2. Ir a **Your Authtoken** (https://dashboard.ngrok.com/get-started/your-authtoken)
3. Copiar el authtoken

Vas a necesitar:
- `NGROK_AUTHTOKEN` = el authtoken
- `NGROK_DOMAIN` = tu dominio ngrok (ej: `mi-app.ngrok-free.app`)

> **Nota:** Con el plan free, podés crear un dominio estático gratuito en **Universal Gateway > Domains**. Esto evita tener que reconfigurar el webhook en Meta después de cada restart. Si no configurás un dominio fijo, ngrok asigna una URL random cada vez.

---

## Paso 2: Meta Developer - Crear App

### 2.1 Crear la app

1. Ir a https://developers.facebook.com/apps
2. Clickear **"Create App"**
3. Seleccionar **"Other"** como caso de uso, luego **"Business"** como tipo
4. Ponerle un nombre (ej: "LocalForge") y clickear **"Create App"**

### 2.2 Agregar WhatsApp

1. En el dashboard de la app, buscar **"WhatsApp"** en la lista de productos
2. Clickear **"Set Up"**
3. Te lleva a **WhatsApp > Getting Started**

### 2.3 Obtener credenciales

En la página **WhatsApp > API Setup** (https://developers.facebook.com/apps/YOUR_APP_ID/whatsapp-business/wa-dev-console/):

1. **Phone Number ID**: aparece debajo de "From" en la sección de envío de prueba. Es un número largo (ej: `123456789012345`)
2. **Temporary Access Token**: clickear **"Generate"** -- este token expira en 24hs. Para uno permanente, ver la sección más abajo
3. **Agregar destinatario de prueba**: clickear **"Manage phone number list"**, agregar tu número personal y confirmar con el código de verificación que te llega por WhatsApp

Vas a necesitar:
- `WHATSAPP_ACCESS_TOKEN` = el token generado
- `WHATSAPP_PHONE_NUMBER_ID` = el Phone Number ID

### 2.4 App Secret

1. Ir a **App Settings > Basic** (https://developers.facebook.com/apps/YOUR_APP_ID/settings/basic/)
2. En **"App Secret"**, clickear **"Show"** y copiar

Vas a necesitar:
- `WHATSAPP_APP_SECRET` = el App Secret

### 2.5 Verify Token

Elegí un string secreto cualquiera. Va a ser usado para que Meta verifique que tu webhook es legítimo.

Ejemplo: `mi_token_secreto_123`

Vas a necesitar:
- `WHATSAPP_VERIFY_TOKEN` = el string que elegiste

### 2.6 Tu número de WhatsApp

Tu número personal con código de país y el 9 para móviles argentinos, sin `+` ni espacios.

Ejemplo: si tu número es +54 9 11 1234-5678, usás `5491112345678`

> **Nota Argentina:** WhatsApp envía los números con el formato `549XXXXXXXXXX` pero la API de Meta espera `54XXXXXXXXXX` (sin el 9). La app maneja esta conversión automáticamente.

Vas a necesitar:
- `ALLOWED_PHONE_NUMBERS` = tu número (ej: `5491112345678`). Se pueden poner varios separados por coma.

---

## Paso 3: Configurar .env

```bash
cd localforge
cp .env.example .env
```

Editar `.env` con los valores obtenidos. Los valores mínimos obligatorios:

```env
# === WhatsApp Cloud API ===
WHATSAPP_ACCESS_TOKEN=EAAxxxxxxx...
WHATSAPP_PHONE_NUMBER_ID=123456789012345
WHATSAPP_VERIFY_TOKEN=mi_token_secreto_123
WHATSAPP_APP_SECRET=abcdef1234567890
ALLOWED_PHONE_NUMBERS=5491112345678

# === Ollama ===
OLLAMA_BASE_URL=http://localhost:11435
OLLAMA_MODEL=qwen3:8b

# === ngrok ===
NGROK_AUTHTOKEN=2xxxxxxxxxxxxxxxxxxxxxxxxxxxxx
NGROK_DOMAIN=tu-dominio.ngrok-free.app
```

> **Importante:** El compose usa `network_mode: host` y Ollama escucha en el puerto **11435** (no el default 11434). Por eso `OLLAMA_BASE_URL` debe ser `http://localhost:11435`.

El `.env.example` incluye todas las variables opcionales (guardrails, tracing, agent mode, Telegram, etc.). Revisalo para personalizar tu setup.

### 3.1 Configurar Langfuse (Tracing)

La aplicación incluye integración con **Langfuse** para monitoreo y trazabilidad de las interacciones con el LLM.

1. Al levantar los contenedores con Docker, Langfuse estará disponible en `http://localhost:3000`.
2. Ingresá y creá una cuenta local (te pedirá nombre, email y contraseña).
3. Creá un nuevo "Project" (ej. "LocalForge").
4. En el menú de la izquierda, andá a **Settings** -> **API Keys**.
5. Hacé clic en **"Create new API keys"**.
6. Copiá la **Public Key** y la **Secret Key** y agregalas a tu archivo `.env`:

```env
LANGFUSE_PUBLIC_KEY=pk-lf-...
LANGFUSE_SECRET_KEY=sk-lf-...
LANGFUSE_HOST=http://localhost:3000
```

*(Si dejás estas variables vacías, la app iniciará correctamente pero no enviará los logs a Langfuse).*

---

## Paso 4: Configurar Telegram (opcional)

Si querés usar Telegram además de (o en lugar de) WhatsApp, seguí estos pasos. Si solo usás WhatsApp, podés saltear esta sección.

### 4.1 — Crear el bot

1. Abrir Telegram y buscar `@BotFather`
2. Enviar `/newbot` → elegir nombre (ej: "Mi LocalForge") y username (ej: `milocalforge_bot`)
3. Copiar el token que devuelve BotFather (formato: `123456:ABC-...`, sin el prefijo `bot`)

### 4.2 — Configurar variables en `.env`

Primero generá el webhook secret en la terminal:

```bash
openssl rand -hex 32
```

Copiá el resultado y completá el `.env`:

```env
TELEGRAM_ENABLED=true
TELEGRAM_BOT_TOKEN=123456:ABC-...
TELEGRAM_WEBHOOK_SECRET=a1b2c3d4e5f6...
TELEGRAM_WEBHOOK_URL=https://tu-dominio.ngrok-free.app
```

> **Errores comunes:**
> - **`TELEGRAM_ENABLED`**: verificá que diga `true`, no `false`. Si queda en `false` (el default), la app arranca sin Telegram y no verás ningún error.
> - **`TELEGRAM_BOT_TOKEN`**: copiar solo el token (ej: `123456:ABC-...`), **sin** el prefijo `bot`. La app agrega `bot` internamente en la URL de la API. Si copiás `bot123456:ABC-...` las llamadas fallan con 401.
> - **`TELEGRAM_WEBHOOK_SECRET`**: pegar el resultado del `openssl` directamente. **No** usar `$(openssl rand -hex 32)` en el `.env` — los archivos `.env` no ejecutan comandos shell.
> - **`TELEGRAM_WEBHOOK_URL`**: debe incluir `https://` al principio. Es la misma URL base de ngrok (tu `NGROK_DOMAIN` con `https://` adelante). Si ponés solo el dominio sin protocolo, el registro del webhook falla silenciosamente.

### 4.3 — Exponer y registrar el webhook

Telegram y WhatsApp comparten la misma aplicación y el mismo túnel ngrok. La URL base es la misma — lo que cambia es el path (`/webhook` para WhatsApp, `/telegram/webhook` para Telegram).

**Opción A — Automático (recomendado):** si configuraste `TELEGRAM_WEBHOOK_URL` en el paso anterior, la app registra el webhook automáticamente al iniciar. Verificá que los logs muestren:

```
Telegram webhook registered: https://tu-dominio.ngrok-free.app/telegram/webhook
Telegram integration enabled
```

Si no aparecen estos logs, revisá que `TELEGRAM_ENABLED=true` y que `TELEGRAM_BOT_TOKEN` tenga valor.

**Opción B — Manual:** si preferís no usar `TELEGRAM_WEBHOOK_URL`, podés registrar el webhook manualmente después de levantar la app:

```bash
curl -X POST "https://api.telegram.org/botTU_TOKEN/setWebhook" \
  -d "url=https://tu-dominio.ngrok-free.app/telegram/webhook" \
  -d "secret_token=TU_WEBHOOK_SECRET"
```

Respuesta esperada: `{"ok":true,"result":true,"description":"Webhook was set"}`

### 4.4 — Whitelist de usuarios (recomendado)

Para que solo vos puedas usar el bot, obtené tu chat ID enviando cualquier mensaje al bot y revisando los logs:

```bash
docker compose --profile dev logs -f localforge | grep "tg_"
```

Vas a ver algo como `tg_123456789`. Copiá el número y agregalo al `.env`:

```bash
ALLOWED_TELEGRAM_CHAT_IDS=123456789
```

Reiniciá para aplicar: `docker compose --profile dev restart localforge`

### 4.5 — Verificar

| Caso | Acción | Resultado esperado |
|------|--------|--------------------|
| Mensaje de texto | Enviar "Hola" al bot | El bot responde |
| Nota de voz | Grabar y enviar audio | El bot transcribe y responde |
| Comando | Enviar `/remember test` | Responde "Remembered: test" |
| Recordatorio | "Avisame en 1 minuto" | Llega mensaje al minuto |
| Webhook inválido | POST con secret incorrecto | HTTP 403 |

---

## Paso 5: Levantar los servicios

El stack usa **Docker Compose profiles** para separar entornos:

| Profile | Servicios incluidos | Cuándo usar |
|---------|-------------------|-------------|
| `dev` | localforge + ollama + ngrok + langfuse | Desarrollo local (todo incluido) |
| `prod` | localforge + langfuse | Producción (Ollama y túnel son externos) |

> **Importante:** Siempre hay que especificar `--profile`. Sin profile, Docker Compose no selecciona ningún servicio y dice "no service selected".

### Sin GPU (CPU only)

```bash
docker compose --profile dev up -d
```

### Con GPU NVIDIA

```bash
docker compose -f docker-compose.yml -f docker-compose.gpu.yml --profile dev up -d
```

### Descargar los modelos

```bash
# Chat (obligatorio)
docker compose --profile dev exec ollama ollama pull qwen3:8b

# Vision (para imágenes)
docker compose --profile dev exec ollama ollama pull llava:7b

# Embeddings (para búsqueda semántica)
docker compose --profile dev exec ollama ollama pull nomic-embed-text
```

Esto descarga ~6GB en total. Solo se hace la primera vez (queda persistido en el volume `ollama_data`).

> **Nota:** Si no descargás `nomic-embed-text`, la app funciona igual pero sin búsqueda semántica (inyecta todas las memorias en vez de solo las relevantes). Podés deshabilitarla con `SEMANTIC_SEARCH_ENABLED=false` en `.env`.

### Verificar que todo levantó

```bash
# Ver estado de los containers
docker compose --profile dev ps

# Liveness check (proceso vivo)
curl http://localhost:8000/health
```

Respuesta esperada:
```json
{"status":"ok"}
```

```bash
# Readiness check (DB + Ollama respondiendo)
curl http://localhost:8000/ready
```

Respuesta esperada:
```json
{"status":"ok","checks":{"db":"ok","ollama":"ok"}}
```

Si Ollama todavía está descargando el modelo, `/ready` puede dar `"ollama": "error: not responding"` (HTTP 503). Esperá a que termine el pull. `/health` siempre da 200 si el proceso está vivo.

### Verificar ngrok

```bash
docker compose --profile dev logs ngrok
```

Si configuraste `NGROK_DOMAIN` en `.env`, deberías ver:
```
t=... lvl=info msg="started tunnel" ... url=https://tu-dominio.ngrok-free.app
```

### Verificar Langfuse (Observabilidad)

El stack incluye un servidor local de Langfuse para ver las trazas de ejecución.
1. Ingresá a `http://localhost:3000`
2. Si es la primera vez, creá una cuenta local (los datos quedan en tu máquina).
3. Creá un nuevo "Project" (ej. "LocalForge").
4. Entrá a Settings -> API Keys, generá un par nuevo y pegalos en tu `.env`.
5. Reiniciá el asistente: `docker compose --profile dev restart localforge`

---

## Paso 6: Configurar Webhook en Meta

1. Ir a **WhatsApp > Configuration** en tu app de Meta (https://developers.facebook.com/apps/YOUR_APP_ID/whatsapp-business/wa-settings/)
2. En la sección **Webhook**, clickear **"Edit"**
3. Completar:
   - **Callback URL**: `https://TU-URL-NGROK/webhook`
   - **Verify Token**: el mismo string que pusiste en `WHATSAPP_VERIFY_TOKEN` en el `.env`
4. Clickear **"Verify and Save"**

Si todo está bien, Meta envía un GET a tu webhook, recibe el challenge de vuelta, y guarda la configuración. Si falla, revisá:
- Que los containers estén corriendo (`docker compose --profile dev ps`)
- Que ngrok esté conectado (`docker compose --profile dev logs ngrok`)
- Que el verify token coincida exactamente
- Que la URL sea correcta (con `https://` y `/webhook` al final)

5. **Suscribirse a mensajes**: en la misma página, en el campo **"Webhook fields"**, clickear **"Manage"** y activar **"messages"**

> **Nota:** Con ngrok free, la URL cambia en cada restart. Tenés que reconfigurar el webhook en Meta cada vez.

---

## Paso 7: Probar end-to-end

1. Abrí WhatsApp en tu celular
2. Mandá un mensaje al número de test de Meta (el que aparece en API Setup como "From")
   - Si nunca le escribiste, primero tenés que iniciar la conversación enviando el mensaje template que Meta sugiere en la sección de test
3. Esperá la respuesta del LLM

### Ver logs en tiempo real

```bash
docker compose --profile dev logs -f localforge
```

Deberías ver el flujo:
```
INFO app.webhook.router: Incoming [5491112345678]: Hola!
INFO app.whatsapp.client: Outgoing  [541112345678]: Hola! En qué puedo ayudarte?
```

### Probar comandos

Una vez que el chat funciona, probá los comandos:

1. Mandá `/remember mi cumple es el 15 de marzo` → debería responder "Remembered: ..."
2. Mandá `/memories` → debería listar el dato guardado
3. Mandá un mensaje normal preguntando por tu cumpleaños → el LLM debería saberlo
4. Mandá `/help` → debería listar todos los comandos disponibles
5. Mandá `/clear` → guarda un snapshot de la sesión y borra el historial (las memorias persisten)

### Verificar persistencia

1. Verificar que existe `data/localforge.db` con datos:
   ```bash
   sqlite3 data/localforge.db "SELECT * FROM memories;"
   ```
2. Verificar que `data/MEMORY.md` refleja las memorias guardadas
3. Reiniciar la app (`docker compose --profile dev restart localforge`) y verificar que el historial y memorias persisten

### Troubleshooting

| Problema | Causa probable | Solución |
|---|---|---|
| "no service selected" | Falta `--profile` en el comando | Agregar `--profile dev` o `--profile prod` |
| No llegan mensajes al webhook | Webhook no configurado o ngrok caído | Verificar `docker compose --profile dev logs ngrok` y config en Meta |
| `/ready` da `"ollama": "error"` | Ollama no está corriendo | `docker compose --profile dev logs ollama`, verificar que el container esté up |
| Respuesta muy lenta (>60s) | CPU sin GPU, modelo grande | Usar modelo más chico: `OLLAMA_MODEL=qwen3:4b` |
| Error 403 en webhook verify | Verify token no coincide | Comparar `.env` con lo puesto en Meta |
| Mensaje no se responde | Número no en whitelist | Verificar `ALLOWED_PHONE_NUMBERS` en `.env` |
| `Invalid webhook signature` en logs | App Secret incorrecto | Verificar `WHATSAPP_APP_SECRET` en `.env` |
| Meta no envía mensajes | No suscrito a "messages" | Activar "messages" en Webhook fields |
| Error 131030 "Recipient not in allowed list" | Número no registrado como destinatario de prueba en Meta | Agregar número en API Setup > "Manage phone number list" |
| Error 401 Unauthorized | Access token expirado | Generar nuevo token en API Setup o usar token permanente (ver abajo) |
| Error 400 "permission" | Token sin permisos correctos | Verificar que el System User tenga `whatsapp_business_messaging` |
| Ollama 404 "model not found" | Modelo no descargado | `docker compose --profile dev exec ollama ollama pull <modelo>` |
| Docker build falla con "Temporary failure resolving" | DNS no funciona dentro de Docker (común en hosts IPv6-only) | Buildear con `docker build --network host -t localforge-localforge .` y luego `docker compose --profile dev up -d` (ver abajo) |

---

## Tests automatizados

```bash
# Setup inicial (crea .venv, instala deps + pre-commit hooks)
make dev

# Correr todo: lint + typecheck + tests
make check

# Solo tests
make test

# O directo con el venv
.venv/bin/python -m pytest tests/ -v

# Desde el container Docker
docker compose --profile dev run --rm localforge python -m pytest tests/ -v
```

Los pre-commit hooks corren automáticamente en cada `git commit`:
1. **ruff** — lint con autofix
2. **ruff-format** — formateo
3. **mypy** — type checking sobre `app/`
4. **pytest** — tests completos

---

## Token permanente (opcional)

El token temporal de Meta expira en 24hs. Para obtener uno permanente:

1. Ir a **Business Settings** (https://business.facebook.com/settings/)
2. **System Users** > **Add** > crear un System User con rol Admin
3. Clickear en el System User > **Generate New Token**
4. Seleccionar tu app y los permisos: `whatsapp_business_messaging`, `whatsapp_business_management`
5. Copiar el token generado y reemplazar `WHATSAPP_ACCESS_TOKEN` en `.env`
6. Reiniciar: `docker compose --profile dev restart localforge`

---

## Comandos útiles

> Todos los comandos usan `--profile dev`. Para producción, reemplazar por `--profile prod`.

```bash
# Levantar todo (dev)
docker compose --profile dev up -d

# Levantar con GPU (dev)
docker compose -f docker-compose.yml -f docker-compose.gpu.yml --profile dev up -d

# Parar todo
docker compose --profile dev down

# Ver logs de un servicio
docker compose --profile dev logs -f localforge
docker compose --profile dev logs -f ollama
docker compose --profile dev logs -f ngrok

# Reiniciar solo localforge (después de cambiar .env)
docker compose --profile dev restart localforge

# Cambiar modelo
docker compose --profile dev exec ollama ollama pull qwen3:8b
# Luego cambiar OLLAMA_MODEL en .env y reiniciar localforge

# Listar modelos descargados
docker compose --profile dev exec ollama ollama list

# Rebuild después de cambiar código
docker compose --profile dev up -d --build localforge

# Si el build falla con "Temporary failure resolving" (problema DNS/IPv6):
docker build --network host -t localforge-localforge .
docker compose --profile dev up -d
# Con GPU:
docker build --network host -t localforge-localforge .
docker compose -f docker-compose.yml -f docker-compose.gpu.yml --profile dev up -d

# Verificar readiness (DB + Ollama)
curl http://localhost:8000/ready
```
