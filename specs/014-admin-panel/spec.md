# Módulo 014 — Admin Panel

## Objetivo

Panel web protegido con contraseña que permite al operador (PO) ver la idea ganadora del ciclo
actual y subir el video del capítulo manualmente. Reemplaza el pipeline I2V automático durante
la fase en que Kling API no está contratada.

## Flujo completo

```
23:00 ART → FSM entra en GENERACION
  → API envía email al operador vía Resend con:
      - Idea ganadora (texto completo)
      - Personaje elegido (nombre + foto)
      - Link directo a /admin

Operador:
  1. Abre /admin en la PWA
  2. Ingresa contraseña (ADMIN_PASSWORD env var)
  3. Ve el resumen del ganador
  4. Elige archivo .mp4 desde su dispositivo
  5. Lo sube directamente a R2 (via presigned URL)
  6. Confirma → API guarda la URL en chapters.video_url

12:00 ART → FSM publica ESTRENO con el video ya en R2
```

## Decisiones de diseño

| # | Decisión | Valor |
|---|---|---|
| D-1 | Contraseña | `ADMIN_PASSWORD` env var (nunca hardcodeada). Valor inicial: `dilan` |
| D-2 | Auth admin | JWT separado, claim `{"scope": "admin"}`, expiración 8h, firmado con `JWT_SECRET` existente |
| D-3 | Upload video | Presigned URL de R2 — el browser sube directo a R2, la API solo genera la URL y confirma |
| D-4 | Email | Resend free tier (3000 emails/mes). Env var `RESEND_API_KEY` |
| D-5 | Ruta PWA | `/admin` — no listada en navegación, acceso solo por URL directa |
| D-6 | Formato video | `.mp4` únicamente, máximo 200MB |
| D-7 | Estado FSM | El operador solo puede subir video cuando `cycle.state = 'GENERACION'` |
| D-8 | Video URL | Se guarda en `chapters.video_url` (columna ya existe en el modelo) |

## Endpoints nuevos

### POST /api/v1/admin/auth
- Body: `{"password": "..."}`
- Verifica contra `ADMIN_PASSWORD` (comparación con `secrets.compare_digest`)
- Response: `{"token": "<jwt admin 8h>"}`
- Rate limit: máximo 5 intentos fallidos por IP

### GET /api/v1/admin/cycle
- Header: `Authorization: Bearer <admin jwt>`
- Response: estado actual del ciclo + idea ganadora + personaje + foto URL

### POST /api/v1/admin/chapters/{chapter_id}/video-upload-url
- Header: `Authorization: Bearer <admin jwt>`
- Genera presigned PUT URL de R2 para subir el .mp4
- Response: `{"upload_url": "...", "public_url": "..."}`

### PUT /api/v1/admin/chapters/{chapter_id}/video
- Header: `Authorization: Bearer <admin jwt>`
- Body: `{"video_url": "..."}`
- Guarda la URL en `chapters.video_url`
- Solo permitido si `cycle.state = 'GENERACION'`

## Email de notificación

- **Trigger**: hook en la transición FSM `VOTACION → GENERACION`
- **Destinatario**: `ADMIN_EMAIL` env var
- **Contenido**: idea ganadora, personaje, foto del personaje, link a `/admin`
- **Proveedor**: Resend (`resend` Python SDK)
- **Fallback**: si Resend falla, loguear error + continuar FSM (no bloquear)

## Pantallas PWA

### /admin (sin token)
- Campo contraseña + botón Entrar
- Error si contraseña incorrecta

### /admin (con token válido)
- Header: "Admin · AI Plot Twist"
- Card con idea ganadora: texto, personaje, foto
- Sección upload: file picker (.mp4, max 200MB) + progress bar
- Botón "Publicar video" (habilitado solo cuando hay archivo seleccionado)
- Estado post-upload: "✅ Video listo para publicar a las 12:00"
- Si state ≠ GENERACION: banner "No hay ciclo en generación actualmente"

## Variables de entorno nuevas

```
ADMIN_PASSWORD=dilan          # contraseña del panel admin
ADMIN_EMAIL=dilanperea10@gmail.com  # destinatario del email de notificación
RESEND_API_KEY=re_xxx         # API key de Resend
```

Agregadas via `fly secrets set` — nunca en el repositorio.

## Tests requeridos

- Unit: `verify_admin_password` (correcto, incorrecto, timing-safe)
- Unit: `generate_admin_jwt` + `verify_admin_jwt`
- Integration: `POST /admin/auth` (200, 401)
- Integration: `GET /admin/cycle` (200 con token, 401 sin token)
- Integration: `PUT /admin/chapters/{id}/video` (200, 403 si state ≠ GENERACION)
- Unit: `send_generation_email` (mock Resend, verifica payload)
