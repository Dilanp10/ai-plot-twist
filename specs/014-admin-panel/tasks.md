# Tasks — Módulo 014: Admin Panel

Orden de implementación: backend primero (T-001 a T-005), luego PWA (T-006), luego email (T-007).

---

## T-001 · Admin auth endpoint

**Archivos a crear/modificar:**
- `apps/api/app/routers/admin.py` (nuevo)
- `apps/api/app/auth/admin.py` (nuevo — helpers JWT admin + verify password)
- `apps/api/tests/test_admin_auth.py` (nuevo)
- `apps/api/app/main.py` (registrar router `/api/v1/admin`)

**Qué hace:**
- `POST /api/v1/admin/auth` → verifica `ADMIN_PASSWORD` con `secrets.compare_digest` → JWT admin 8h
- `verify_admin_token` dependency para proteger el resto de endpoints admin
- Tests: 200 con contraseña correcta, 401 con incorrecta, 401 sin token en endpoint protegido

---

## T-002 · Endpoint GET /admin/cycle

**Archivos a crear/modificar:**
- `apps/api/app/routers/admin.py` (agregar endpoint)
- `apps/api/tests/test_admin_cycle.py` (nuevo)

**Qué hace:**
- `GET /api/v1/admin/cycle` → retorna estado del ciclo actual + idea ganadora (twist con más votos
  al final de VOTACION, o ganador ya seleccionado) + personaje + `photo_url` del personaje
- Solo accesible con JWT admin
- Si no hay ciclo activo: 404

---

## T-003 · Presigned URL para upload a R2

**Archivos a crear/modificar:**
- `apps/api/app/routers/admin.py` (agregar endpoint)
- `apps/api/app/storage/r2.py` (agregar `generate_presigned_put_url`)
- `apps/api/tests/test_admin_upload.py` (nuevo)

**Qué hace:**
- `POST /api/v1/admin/chapters/{chapter_id}/video-upload-url`
- Genera presigned PUT URL de R2 válida por 15 minutos para subir el .mp4
- Retorna `{"upload_url": "...", "public_url": "..."}` donde `public_url` es la URL pública final
- Tests: mock del cliente R2, verificar que la URL tiene los params correctos

---

## T-004 · Endpoint PUT /admin/chapters/{id}/video

**Archivos a crear/modificar:**
- `apps/api/app/routers/admin.py` (agregar endpoint)
- `apps/api/tests/test_admin_upload.py` (agregar casos)

**Qué hace:**
- `PUT /api/v1/admin/chapters/{chapter_id}/video` con body `{"video_url": "..."}`
- Valida que `cycle.state == 'GENERACION'` — si no, 403
- Guarda `video_url` en `chapters.video_url`
- Tests: 200 en estado correcto, 403 si state ≠ GENERACION

---

## T-005 · Email de notificación vía Resend

**Archivos a crear/modificar:**
- `apps/api/app/email/resend.py` (nuevo — cliente Resend + `send_generation_email`)
- `apps/api/app/fsm/transitions.py` (agregar hook en `VOTACION → GENERACION`)
- `apps/api/tests/test_email_resend.py` (nuevo)
- `pyproject.toml` / `requirements` (agregar `resend`)

**Qué hace:**
- Al entrar en `GENERACION`, llama `send_generation_email` con la info del ganador
- Email incluye: idea ganadora, personaje, foto, link a `/admin`
- Si Resend falla: loguear error con structlog, no relanzar (FSM no se bloquea)
- Tests: mock Resend SDK, verificar payload del email, verificar que error no propaga

---

## T-006 · Ruta /admin en PWA (Svelte)

**Archivos a crear/modificar:**
- `apps/web/src/routes/admin/+page.svelte` (nuevo)
- `apps/web/src/lib/api/admin.ts` (nuevo — funciones fetch para los endpoints admin)
- `apps/web/src/lib/stores/adminStore.ts` (nuevo — token admin en memoria, no en localStorage)

**Qué hace:**

**Vista sin token:**
- Campo contraseña + botón Entrar
- Llama `POST /api/v1/admin/auth`, guarda token en memoria (store Svelte)
- Error visible si contraseña incorrecta

**Vista con token:**
- Card con idea ganadora (texto, personaje, foto del personaje)
- File picker filtrado a `.mp4`, máximo 200MB
- Al seleccionar archivo: muestra nombre + tamaño
- Al clickear "Subir y publicar":
  1. `POST /admin/chapters/{id}/video-upload-url` → presigned URL
  2. `PUT <presigned_url>` desde el browser directo a R2 (con progress bar)
  3. `PUT /admin/chapters/{id}/video` con la public_url
- Post-upload: "✅ Video listo — se publica a las 12:00 ART"
- Si state ≠ GENERACION: banner de aviso, file picker deshabilitado

---

## T-007 · Fly secrets + smoke test E2E

**Qué hace:**
- Documentar los secrets nuevos a setear en Fly:
  ```
  fly secrets set ADMIN_PASSWORD=dilan --app ai-plot-twist
  fly secrets set ADMIN_EMAIL=dilanperea10@gmail.com --app ai-plot-twist
  fly secrets set RESEND_API_KEY=re_xxx --app ai-plot-twist
  ```
- Smoke test manual: login en `/admin`, verificar que muestra ciclo, verificar email recibido
- No es una tarea de código — es una checklist de deploy y verificación

---

## Orden de ejecución

```
T-001 → T-002 → T-003 → T-004 → T-005 → T-006 → T-007
```

Cada T-NNN se implementa y se cierra antes de avanzar al siguiente.
