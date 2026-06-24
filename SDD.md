# Software Design Document — **AI Plot Twist**

> **Document type:** Software Design Document (SDD) — GitHub Spec Kit format
> **Project codename:** `ai-plot-twist`
> **Version:** `0.1.0-draft` (MVP, fase cerrada)
> **Status:** `DRAFT v0.7 — TODOS los módulos 001..011 spec-done; SDD listo para entrar a fase de implementación`
> **Owner:** Lead Software Architect
> **Last updated:** 2026-06-07 (ronda 6: cierre del spec-out completo del MVP)
> **Target timezone:** `America/Argentina/Buenos_Aires` (UTC-3, sin DST). **Todos** los horarios de este documento están anclados a este TZ y se almacenan en DB como `TIMESTAMPTZ`.

---

## Table of Contents

1. [Executive Summary & System Goals](#1-executive-summary--system-goals)
2. [System Architecture & Zero-Cost Data Flow](#2-system-architecture--zero-cost-data-flow)
3. [Database Schema & State Machine](#3-database-schema--state-machine)
4. [Core Backend Logic & Automation — *The Directors' Engine*](#4-core-backend-logic--automation--the-directors-engine)
5. [API Contracts & Endpoints](#5-api-contracts--endpoints)
6. [User Behavior & Acceptance Criteria](#6-user-behavior--acceptance-criteria)
7. [Open Questions](#7-open-questions)
8. [Risks & Mitigations](#8-risks--mitigations)

---

## 1. Executive Summary & System Goals

### 1.1 Producto

**AI Plot Twist** es un juego social-narrativo de ciclo diario en el que una comunidad cerrada co-escribe una serie generada por IA, capítulo a capítulo, durante una temporada. Cada 24 horas se libera **un capítulo nuevo** cuya trama está dictada por la propuesta más votada del día anterior. El producto se materializa como una **PWA (Progressive Web App)** liviana servida en Cloudflare Pages, alimentada por una API HTTP en Fly.io.

### 1.2 Experiencia del usuario en el ciclo de 24 h

| Hora local (ART) | Estado del juego | Qué hace el usuario | Qué hace el sistema |
|---|---|---|---|
| **12:00 PM** | `ESTRENO` | Recibe push/notificación, abre la WebApp y consume el capítulo del día (secuencia de **3–4 imágenes + texto + TTS opcional**) que cierra con un *cliffhanger*. | Publica los assets pre-generados en la noche previa. Marca `chapter.released_at`. |
| **12:01 – 17:59** | `RECEPCION_IDEAS` | Envía hasta **N propuestas** (`twist`) de continuación, máx. 280 chars. | Persiste en `twists` con `status='pending_review'`. Rate-limit por usuario. |
| **18:00 – 22:59** | `VOTACION` | Vota propuestas filtradas (un voto por propuesta, máx. K votos por usuario). | A las 18:00 corre **Director's Filter** (LLM); a las 18:00:30 expone `vote-feed`. |
| **23:00 – 11:59** | `GENERACION` | Espera el próximo estreno. Puede revisar capítulos anteriores. | Selecciona ganador, compone prompts, llama LLM de guion, llama pipeline T2I, sube assets a R2, marca capítulo siguiente como `ready`. |

### 1.3 Objetivos del prototipo (Goals)

| ID | Goal | Métrica de éxito | Comentario |
|---|---|---|---|
| **G-1** | **Costo de infraestructura = $0/mes** | Factura cloud mensual = USD 0,00 | Solo se usan free tiers documentados (ver §2.4). |
| **G-2** | **Ciclo automatizado 100%** sin intervención humana | 7 días consecutivos sin manual override en transiciones de estado | Excepto kill-switch operativo. |
| **G-3** | **Retención D1 ≥ 60 %** y **D7 ≥ 30 %** en cohorte cerrada (10–40 usuarios) | Eventos `chapter_view` por usuario por día | Validación de engagement del loop. |
| **G-4** | **Latencia p95 de generación nocturna ≤ 50 min** dentro de la ventana de 60 min | Métrica `generation_pipeline_duration_seconds` | Para no impactar el estreno de las 12:00. |
| **G-5** | **Escalabilidad inicial controlada**: soportar hasta **200 usuarios concurrentes** en pico de 12:00 PM sin degradar p95 < 500 ms en `GET /chapters/today` | Stress test con `k6` | Asegura que el free tier de Fly.io alcanza para la fase cerrada. |
| **G-6** | **Idempotencia y resiliencia**: cualquier transición de estado puede reintentarse sin corromper datos | Test de chaos: re-disparar el cron 3× sobre el mismo estado | Crítico porque GitHub Actions cron puede tener jitter o reintentos. |

### 1.4 Non-Goals (Out of Scope)

| ID | Item explícitamente fuera de alcance | Por qué |
|---|---|---|
| **NG-1** | Generación de **video real** (e.g. Sora, Runway, AnimateDiff) | Costo y tiempo de render exceden la ventana de 60 min sin GPU local. MVP usa secuencia de imágenes + texto + TTS. |
| **NG-2** | Onboarding público abierto y App Store / Play Store | Fase cerrada por invitación. PWA instalable es suficiente. |
| **NG-3** | Monetización (suscripciones, in-app, ads, NFT) | No aplica para validación de engagement. |
| **NG-4** | Multi-idioma | MVP solo en **español rioplatense**. |
| **NG-5** | Moderación humana en tiempo real / panel de admin completo | Solo CLI scripts + un endpoint de kill-switch protegido. |
| **NG-6** | Múltiples temporadas en paralelo | MVP corre **una sola temporada activa** a la vez. |
| **NG-7** | Recuperación de cuenta robusta (2FA, OAuth, password reset) | Auth = invite-code + magic device token. Si pierden el dispositivo se re-invita. |
| **NG-8** | Generación de personajes 3D / consistencia visual estricta entre capítulos | Se documenta como riesgo (R-3) pero no se mitiga con embeddings/LoRA en MVP. |
| **NG-9** | Backups offsite, DR, multi-región | Free tier de Neon ya incluye PITR de 7 días; suficiente para MVP. |
| **NG-10** | Analytics propias (PostHog self-host, etc.) | Logs estructurados + métricas básicas en DB. |

---

## 2. System Architecture & Zero-Cost Data Flow

### 2.1 Diagrama conceptual de bloques

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                                  CLIENT TIER                                  │
│                                                                              │
│   ┌────────────────────────────┐     ┌──────────────────────────────────┐    │
│   │  PWA (Svelte + Vite + TS)  │◄────┤  Service Worker (offline cache)  │    │
│   │  hosted on Cloudflare Pages│     └──────────────────────────────────┘    │
│   └────────────┬───────────────┘                                              │
│                │ HTTPS (JSON, Bearer JWT)                                     │
└────────────────┼─────────────────────────────────────────────────────────────┘
                 │
┌────────────────▼─────────────────────────────────────────────────────────────┐
│                              EDGE / DELIVERY TIER                             │
│                                                                              │
│   ┌──────────────────────┐   ┌────────────────────────────────────────────┐  │
│   │ Cloudflare R2        │   │ Cloudflare DNS + free TLS + WAF basic      │  │
│   │ (assets: jpg/webp,   │   │                                            │  │
│   │ mp3 TTS, manifest)   │   │ Public bucket subdomain: assets.example    │  │
│   └──────────────────────┘   └────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────────────────────┘
                 │
                 │ presigned URLs / public CDN reads
                 ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│                                APPLICATION TIER                               │
│                                                                              │
│   ┌─────────────────────────────────────────────────────────────────────┐    │
│   │            FastAPI (Python 3.11) — single Fly.io machine            │    │
│   │            shared-cpu-1x / 256MB, auto-stop=off                     │    │
│   │                                                                     │    │
│   │  ┌───────────┐ ┌────────────┐ ┌──────────────┐ ┌────────────────┐  │    │
│   │  │ HTTP API  │ │ Auth (JWT) │ │ State Engine │ │ Internal Hooks │  │    │
│   │  │ /v1/*     │ │ HS256      │ │ (FSM)        │ │ /internal/*    │  │    │
│   │  └───────────┘ └────────────┘ └──────────────┘ └────────────────┘  │    │
│   │  ┌──────────────────────┐  ┌───────────────────────────────────┐   │    │
│   │  │ Director's Filter    │  │ Generation Pipeline (async tasks) │   │    │
│   │  │ (LLM moderation)     │  │ via FastAPI BackgroundTasks +     │   │    │
│   │  │                      │  │ asyncio.Queue + SQL-based locks   │   │    │
│   │  └──────────────────────┘  └───────────────────────────────────┘   │    │
│   └──────────────┬──────────────────────────────────────────────────────┘    │
│                  │                                                            │
└──────────────────┼────────────────────────────────────────────────────────────┘
                   │
       ┌───────────┼────────────────┬──────────────────────┐
       │           │                │                      │
       ▼           ▼                ▼                      ▼
┌────────────┐ ┌─────────┐ ┌──────────────────┐ ┌─────────────────────┐
│ Neon       │ │ Gemini  │ │ GitHub Models    │ │ Pollinations.ai     │
│ Postgres   │ │ Free    │ │ API (fallback    │ │ Image API           │
│ (DB)       │ │ Tier    │ │ LLM)             │ │ + HuggingFace IF    │
│ TIMESTAMPTZ│ │ JSON    │ │                  │ │ (fallback)          │
│ + jsonb    │ │ mode    │ │                  │ │                     │
└────────────┘ └─────────┘ └──────────────────┘ └─────────────────────┘
                                                          │
                                                          ▼
                                                ┌──────────────────────┐
                                                │ Coqui TTS / Edge-TTS │
                                                │ (free, optional)     │
                                                └──────────────────────┘

┌──────────────────────────────────────────────────────────────────────────────┐
│                           ORCHESTRATION / HEARTBEAT                           │
│                                                                              │
│   GitHub Actions cron — 4 workflows (12:00, 18:00, 23:00, 23:55 ART)         │
│   curl -X POST  https://api/internal/transition  -H "X-Tick-Signature: …"    │
│   Provides reliable, free, externally-triggered FSM transitions.             │
└──────────────────────────────────────────────────────────────────────────────┘
```

### 2.2 Componentes — responsabilidad y razón de elección

| Componente | Rol | Justificación (zero-cost) |
|---|---|---|
| **PWA Svelte + Vite** | Cliente | Bundle pequeño (<60 KB gz), instalable, push opcional, sin runtime cost. |
| **Cloudflare Pages** | Static hosting frontend | Unlimited bandwidth en free tier, deploy desde GitHub. |
| **Cloudflare R2** | Storage de imágenes/audio/manifest | 10 GB free + **egress $0** (clave para no quemar bandwidth en estreno). |
| **Fly.io (1× shared-cpu-1x, 256MB)** | API + workers in-process | Free tier: 3 VMs gratis. Suficiente para 200 conc. con Uvicorn workers=1. |
| **FastAPI + Uvicorn** | HTTP framework | Async nativo (clave para esperar Pollinations), Pydantic v2 = contratos estrictos. |
| **Neon Postgres (free)** | DB transaccional | 0.5 GB + PITR 7 días + branching gratis. `SERIALIZABLE` para votos. |
| **GitHub Actions cron** | Heartbeat externo | Free 2000 min/mes, cron confiable, dispara `/internal/transition` con HMAC. |
| **Google Gemini Free Tier** | LLM principal (filtro + guion) | `gemini-2.0-flash` free: 15 RPM / 1.500 RPD — sobra para 1 filtro + 1 guion/día. |
| **GitHub Models API** | LLM fallback | Free para uso personal; `gpt-4o-mini` o `Llama-3.3-70B` para failover. |
| **Pollinations.ai** | T2I principal | Sin API key, sin rate limit duro documentado, HTTP GET con prompt. |
| **HuggingFace Inference API** | T2I fallback | Free con HF token; modelos SDXL / Flux Schnell. |
| **Edge-TTS (Python lib)** | Narración opcional | Gratis (Microsoft endpoint público), voces en español rioplatense disponibles. |

### 2.3 Flujo de datos detallado — paso a paso por cada fase

#### 2.3.1 Fase 1 — `ESTRENO` (12:00:00 ART)

```
[GH Actions cron @ 12:00] ─► POST /internal/transition {to: ESTRENO}
                                     │
                                     ▼
                          [FastAPI State Engine]
                          - lock advisory pg_try_advisory_lock(seasonId)
                          - SELECT current cycle row FOR UPDATE
                          - assert state IN ('GENERACION','PENDING_RELEASE')
                          - SELECT chapter WHERE day = D AND status='ready'
                          - UPDATE chapter SET released_at = now(), status='live'
                          - UPDATE cycle SET state='ESTRENO', state_entered_at=now()
                          - INSERT INTO state_transitions (...)
                          - release advisory lock
                                     │
                                     ▼
                          [Push fan-out (best-effort)]
                          - WebPush a quienes habilitaron suscripción
                          - Idempotencia: notification_id = chapter_id
```

Cliente (PWA): el SW expone `GET /api/v1/chapters/today` que devuelve el manifest con URLs públicas a R2. Caches `stale-while-revalidate` 60 s.

#### 2.3.2 Fase 2 — `RECEPCION_IDEAS` (12:01:00 – 17:59:59 ART)

```
[Usuario] ─► POST /api/v1/twists/submit  {chapter_id, content}
                                     │
                                     ▼
                          [FastAPI handler]
                          - JWT auth → user_id
                          - SELECT cycle for chapter_id; assert state='RECEPCION_IDEAS'
                          - assert now() < state_entered_at + INTERVAL '6 hours'
                          - rate-limit: COUNT twists WHERE user_id=? AND chapter_id=? < MAX_TWISTS_PER_USER (default 3)
                          - validate len(content) <= 280, normalize whitespace
                          - INSERT INTO twists (id, chapter_id, user_id, content,
                            status='pending_review', submitted_at=now())
                          - return 201 {twist_id, queue_position}
```

Transición automática a `VOTACION` a las 18:00:00 desencadenada por cron externo (§2.3.3 incluye el filtro).

#### 2.3.3 Fase 3 — `VOTACION` (18:00:00 – 22:59:59 ART)

```
[GH Actions cron @ 18:00] ─► POST /internal/transition {to: VOTACION}
                                     │
                                     ▼
                          [FastAPI State Engine]
                          - acquire advisory lock(seasonId)
                          - assert cycle.state='RECEPCION_IDEAS'
                          - UPDATE cycle SET state='FILTERING'
                          - spawn BackgroundTask: director_filter(chapter_id)
                          - return 202 (accepted)

                          [director_filter(chapter_id)]  (async)
                          ┌──────────────────────────────────────────────┐
                          │ 1. SELECT twists WHERE chapter_id=? AND      │
                          │    status='pending_review'                   │
                          │ 2. Chunk en batches de 25                    │
                          │ 3. Para cada batch:                          │
                          │    - LLM call (Gemini, JSON-mode)            │
                          │    - Update twists.status IN                 │
                          │      ('approved','rejected_offensive',       │
                          │       'rejected_incoherent','rejected_spam') │
                          │    - persist twists.director_reason          │
                          │ 4. UPDATE cycle SET state='VOTACION',        │
                          │    state_entered_at=now()                    │
                          └──────────────────────────────────────────────┘
                                     │
                                     ▼
[Usuario] ─► GET /api/v1/twists/vote-feed → propuestas approved, ordenadas por
                                            random_seed estable derivado del cycle_id
[Usuario] ─► POST /api/v1/twists/vote { twist_id }
            (UPSERT atómico en tabla votes con UNIQUE(user_id, twist_id))
```

#### 2.3.4 Fase 4 — `GENERACION` (23:00:00 – 11:59:59 ART)

```
[GH Actions cron @ 23:00] ─► POST /internal/transition {to: GENERACION}
                                     │
                                     ▼
                          [FastAPI State Engine]
                          - assert cycle.state='VOTACION'
                          - UPDATE cycle SET state='GENERACION'
                          - spawn BackgroundTask: generation_pipeline(chapter_id)

[generation_pipeline]
  ┌─────────────────────────────────────────────────────────────────────────┐
  │ STEP 1 — Pick winner (atomic, deterministic)                            │
  │   SELECT t.id, t.content, COUNT(v.id) AS votes                          │
  │   FROM twists t LEFT JOIN votes v ON v.twist_id=t.id                    │
  │   WHERE t.chapter_id=? AND t.status='approved'                          │
  │   GROUP BY t.id                                                         │
  │   ORDER BY votes DESC, t.submitted_at ASC, t.id ASC                     │
  │   LIMIT 1                                                               │
  │   ► tiebreak determinístico: votes DESC → submitted_at ASC → id ASC      │
  │                                                                         │
  │ STEP 2 — Compose scriptwriter prompt (Gemini, JSON-mode)                │
  │   Input: season bible + last 3 chapter summaries + winner_twist         │
  │   Output: JSON {                                                        │
  │     title, synopsis, panels: [                                          │
  │       {idx, narration, visual_prompt, mood, tts_text}, …                │
  │     ], next_cliffhanger_seed                                            │
  │   }                                                                     │
  │                                                                         │
  │ STEP 3 — For each panel (3..4):                                         │
  │   3a. visual_prompt + style_tag + seed → Pollinations URL               │
  │       https://image.pollinations.ai/prompt/{enc}?width=1024&height=1024 │
  │       &seed={int}&model=flux&nologo=true                                │
  │   3b. fetch with timeout=120s, retries=3 (exponential backoff)          │
  │   3c. if all retries fail → HuggingFace SDXL fallback                   │
  │   3d. upload bytes to R2: assets/{season}/{chapter}/panel_{idx}.webp    │
  │   3e. (optional) Edge-TTS render tts_text → mp3 → R2                    │
  │                                                                         │
  │ STEP 4 — Persist                                                        │
  │   INSERT next_chapter row, status='ready', manifest_json={…}            │
  │   UPDATE cycle SET state='PENDING_RELEASE'                              │
  │                                                                         │
  │ STEP 5 — Health gate                                                    │
  │   if any panel missing → status='ready_degraded'                        │
  │   (admin alert by structured log + sentry-free webhook to Discord)      │
  └─────────────────────────────────────────────────────────────────────────┘
```

### 2.4 Estrategia de integración de IA sin costo

| Capa | Servicio primario | Fallback | Mecanismo | Límites relevantes |
|---|---|---|---|---|
| LLM filtro 18:00 | **Google Gemini** `gemini-2.0-flash` | GitHub Models `gpt-4o-mini` | `google-genai` SDK con `response_mime_type=application/json` y `response_schema` Pydantic | 15 RPM / 1.500 RPD. Usamos 1 filtro/día → margen total. |
| LLM guion 23:00 | **Google Gemini** `gemini-2.0-flash` | GitHub Models `Llama-3.3-70B` | Mismo SDK, prompt distinto | 1 llamada/día. |
| T2I generación nocturna | **Pollinations.ai** | HuggingFace Inference (`SDXL-base` o `FLUX.1-schnell`) | HTTP GET (Pollinations) / POST (HF). Sin auth en Pollinations; HF requiere token gratuito. | Pollinations: best-effort, sin SLA. HF: ~5–10 imgs/min free. |
| TTS (opcional) | **Edge-TTS** (lib `edge-tts`) | Coqui TTS local en CI runner | Stream MP3 desde endpoint público de Microsoft Edge | Sin auth. Best-effort. |
| Webhook GPU local (futuro) | **N/A en MVP** | — | Reservado para v0.2: backend hace `POST /generate` a Cloudflare Tunnel → ComfyUI API local. Documentado en §7 OQ-3. | — |

**Patrón de integración asíncrona para T2I sin GPU local:** el backend no espera el render en una conexión HTTP del usuario. Las llamadas a Pollinations/HF son lanzadas dentro de `generation_pipeline`, una `BackgroundTask` disparada por el cron de las 23:00. Si Pollinations tarda > 120 s o devuelve 5xx, el backoff lleva al fallback HF. La descarga del binario se hace en streaming (`httpx.AsyncClient.stream`) y se sube a R2 con presigned PUT.

---

## 3. Database Schema & State Machine

Motor: **PostgreSQL 16** (Neon). Migrations: Alembic. Convención: snake_case, IDs `bigserial` para entidades estables, `uuid` para entidades expuestas al cliente (no enumerables), timestamps `TIMESTAMPTZ NOT NULL DEFAULT now()`.

### 3.1 Esquema relacional (DDL resumido)

```sql
-- =====================================================================
-- INVITES & USERS & AUTH
-- (refinado por el módulo 002-auth-invite-flow: invites es tabla propia
--  y users.invite_code es FK a invites.code)
-- =====================================================================
CREATE TABLE invites (
    code              TEXT PRIMARY KEY
        CHECK (code ~ '^[A-Z2-7]{4}-[A-Z2-7]{4}$'),
    issued_by         TEXT NOT NULL,           -- 'po', 'admin:juan', etc.
    issued_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at        TIMESTAMPTZ NOT NULL,
    status            TEXT NOT NULL
        CHECK (status IN ('unused','redeemed','revoked','expired')),
    redeemed_at       TIMESTAMPTZ,
    redeemed_by_user  BIGINT REFERENCES users(id) ON DELETE SET NULL,
    note              TEXT,
    CHECK ((status = 'redeemed') = (redeemed_at IS NOT NULL))
);
CREATE INDEX idx_invites_status  ON invites(status);
CREATE INDEX idx_invites_expires ON invites(expires_at) WHERE status = 'unused';

CREATE TABLE users (
    id              BIGSERIAL PRIMARY KEY,
    public_id       UUID NOT NULL UNIQUE DEFAULT gen_random_uuid(),
    display_name    TEXT NOT NULL CHECK (char_length(display_name) BETWEEN 2 AND 24),
    invite_code     TEXT NOT NULL REFERENCES invites(code),   -- FK (refinement v0.2)
    device_token    TEXT NOT NULL UNIQUE
        CHECK (char_length(device_token) = 64),               -- SHA-256 hex de device_secret
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    is_banned       BOOLEAN NOT NULL DEFAULT FALSE
);
CREATE INDEX idx_users_invite_code ON users(invite_code);
CREATE INDEX idx_users_last_seen
    ON users(last_seen_at DESC) WHERE is_banned = FALSE;

-- Rate-limit sliding window (módulo 002, usado por /auth/redeem-invite y otros)
CREATE TABLE rate_limit_buckets (
    bucket_key      TEXT NOT NULL,             -- e.g. 'redeem:ip:1.2.3.4'
    window_start    TIMESTAMPTZ NOT NULL,      -- date_trunc('hour', now())
    count           INT NOT NULL DEFAULT 1 CHECK (count >= 0),
    PRIMARY KEY (bucket_key, window_start)
);

-- =====================================================================
-- SEASONS & CHAPTERS
-- =====================================================================
CREATE TABLE seasons (
    id              BIGSERIAL PRIMARY KEY,
    slug            TEXT NOT NULL UNIQUE,          -- e.g. 's01-el-tunel'
    title           TEXT NOT NULL,
    bible_json      JSONB NOT NULL,                -- world rules, character refs, tone
    started_on      DATE NOT NULL,
    ended_on        DATE,
    is_active       BOOLEAN NOT NULL DEFAULT TRUE
);
CREATE UNIQUE INDEX uniq_one_active_season
    ON seasons(is_active) WHERE is_active = TRUE; -- enforces single active season

CREATE TABLE chapters (
    id              BIGSERIAL PRIMARY KEY,
    public_id       UUID NOT NULL UNIQUE DEFAULT gen_random_uuid(),
    season_id       BIGINT NOT NULL REFERENCES seasons(id) ON DELETE CASCADE,
    day_index       INT    NOT NULL,                -- 1, 2, 3…
    title           TEXT   NOT NULL,
    synopsis        TEXT   NOT NULL,
    manifest_json   JSONB  NOT NULL,                -- {panels:[{idx, image_url, tts_url, narration, mood}], cliffhanger}
    status          TEXT   NOT NULL CHECK (status IN
                        ('draft','generating','ready','ready_degraded','live','archived')),
    released_at     TIMESTAMPTZ,                    -- null until live
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (season_id, day_index)
);
CREATE INDEX idx_chapters_status_release ON chapters(status, released_at);

-- =====================================================================
-- DAILY CYCLE (1:1 con cada chapter "live" del día)
-- =====================================================================
CREATE TABLE cycles (
    id                  BIGSERIAL PRIMARY KEY,
    season_id           BIGINT NOT NULL REFERENCES seasons(id),
    chapter_id          BIGINT NOT NULL REFERENCES chapters(id),   -- chapter en pantalla
    next_chapter_id     BIGINT REFERENCES chapters(id),            -- chapter en generación
    state               TEXT NOT NULL CHECK (state IN
                            ('ESTRENO','RECEPCION_IDEAS','FILTERING',
                             'VOTACION','GENERACION','PENDING_RELEASE','FAILED')),
    state_entered_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    cycle_date          DATE NOT NULL,
    UNIQUE (season_id, cycle_date)
);
CREATE INDEX idx_cycles_state ON cycles(state);

CREATE TABLE state_transitions (
    id              BIGSERIAL PRIMARY KEY,
    cycle_id        BIGINT NOT NULL REFERENCES cycles(id) ON DELETE CASCADE,
    from_state      TEXT NOT NULL,
    to_state        TEXT NOT NULL,
    triggered_by    TEXT NOT NULL,        -- 'cron','admin','retry'
    trigger_id      TEXT,                  -- gh-actions run-id, request-id, etc.
    payload_json    JSONB,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_st_cycle ON state_transitions(cycle_id, created_at DESC);

-- =====================================================================
-- TWISTS (propuestas de usuarios)
-- =====================================================================
CREATE TABLE twists (
    id              BIGSERIAL PRIMARY KEY,
    public_id       UUID NOT NULL UNIQUE DEFAULT gen_random_uuid(),
    chapter_id      BIGINT NOT NULL REFERENCES chapters(id) ON DELETE CASCADE,
    user_id         BIGINT NOT NULL REFERENCES users(id),
    content         TEXT NOT NULL CHECK (char_length(content) BETWEEN 5 AND 280),
    status          TEXT NOT NULL CHECK (status IN
                        ('pending_review','approved',
                         'rejected_offensive','rejected_incoherent','rejected_spam',
                         'deleted_by_user')),
    director_reason TEXT,                   -- short LLM justification
    submitted_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    reviewed_at     TIMESTAMPTZ,
    deleted_at      TIMESTAMPTZ              -- soft delete (OQ-6: user-initiated)
);
CREATE INDEX idx_twists_chapter_status ON twists(chapter_id, status);
CREATE INDEX idx_twists_user_chapter   ON twists(user_id, chapter_id);

-- =====================================================================
-- WEB PUSH SUBSCRIPTIONS (OQ-5: VAPID)
-- =====================================================================
CREATE TABLE push_subscriptions (
    id              BIGSERIAL PRIMARY KEY,
    user_id         BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    endpoint        TEXT NOT NULL UNIQUE,
    p256dh_key      TEXT NOT NULL,
    auth_key        TEXT NOT NULL,
    user_agent      TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_success_at TIMESTAMPTZ,
    failure_count   INT NOT NULL DEFAULT 0
);
CREATE INDEX idx_push_user ON push_subscriptions(user_id);

-- =====================================================================
-- VOTES
-- =====================================================================
CREATE TABLE votes (
    id              BIGSERIAL PRIMARY KEY,
    twist_id        BIGINT NOT NULL REFERENCES twists(id) ON DELETE CASCADE,
    user_id         BIGINT NOT NULL REFERENCES users(id),
    chapter_id      BIGINT NOT NULL REFERENCES chapters(id),  -- denorm para rate-limit
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (twist_id, user_id)            -- one vote per user per twist
);
CREATE INDEX idx_votes_twist ON votes(twist_id);
CREATE INDEX idx_votes_user_chapter ON votes(user_id, chapter_id);

-- =====================================================================
-- IDEMPOTENCY / RATE-LIMIT
-- =====================================================================
CREATE TABLE idempotency_keys (
    key             TEXT PRIMARY KEY,
    user_id         BIGINT REFERENCES users(id),
    request_hash    TEXT NOT NULL,
    response_json   JSONB NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_idem_created ON idempotency_keys(created_at);
```

### 3.2 Constantes de negocio (Pydantic settings)

| Constante | Default | Descripción |
|---|---|---|
| `MAX_TWISTS_PER_USER_PER_CHAPTER` | 3 | Límite de propuestas. |
| `MAX_VOTES_PER_USER_PER_CHAPTER` | 5 | Límite de votos (impide voto-bombing). |
| `TWIST_MIN_LEN`, `TWIST_MAX_LEN` | 5, 280 | Validación frontend + backend + DB. |
| `DIRECTOR_BATCH_SIZE` | 25 | Twists por llamada LLM. |
| `T2I_TIMEOUT_S` | 120 | Por panel. |
| `T2I_MAX_RETRIES` | 3 | Por panel, exponential backoff (2s, 6s, 18s). |
| `PIPELINE_HARD_DEADLINE_S` | 3300 (55 min) | Si no termina, marca `ready_degraded`. |
| `CYCLE_TIMES.estreno` | `12:00` (ART) | Hora de estreno. **Configurable** vía env. |
| `CYCLE_TIMES.vote_open` | `18:00` (ART) | Cierre de propuestas / apertura de votación. **Configurable**. |
| `CYCLE_TIMES.generation_open` | `23:00` (ART) | Apertura de generación. **Configurable**. |
| `TENTATIVE_SEASON_LENGTH` | 7 | Capítulos por temporada (tentativo, no enforced en DB). |

### 3.3 Máquina de estados (FSM)

```
                           ┌────────────────────┐
                           │  PENDING_RELEASE   │◄──────────┐
                           │ (chapter ya listo) │           │
                           └──────────┬─────────┘           │
                                      │ cron 12:00          │
                                      ▼                     │
                           ┌────────────────────┐           │
                           │      ESTRENO       │           │
                           │   (12:00–12:00:59) │           │
                           └──────────┬─────────┘           │
                                      │ auto                │
                                      ▼                     │
                           ┌────────────────────┐           │
                           │ RECEPCION_IDEAS    │           │
                           │  (12:01–17:59)     │           │
                           └──────────┬─────────┘           │
                                      │ cron 18:00          │
                                      ▼                     │
                           ┌────────────────────┐           │
                           │     FILTERING      │           │
                           │ (Director Filter)  │           │
                           └──────────┬─────────┘           │
                                      │ filter ok           │
                                      ▼                     │
                           ┌────────────────────┐           │
                           │     VOTACION       │           │
                           │  (18:00–22:59)     │           │
                           └──────────┬─────────┘           │
                                      │ cron 23:00          │
                                      ▼                     │
                           ┌────────────────────┐           │
                           │    GENERACION      │           │
                           │  (23:00–11:59)     │           │
                           └──────────┬─────────┘           │
                                      │ pipeline ok         │
                                      └─────────────────────┘
                                      │ pipeline ko ▶ FAILED → admin escalation
                                      ▼
                           ┌────────────────────┐
                           │      FAILED        │
                           │ (kill-switch open) │
                           └────────────────────┘
```

#### 3.3.1 Tabla canónica de transiciones

| `from_state` | `to_state` | Trigger | Pre-condición | Side-effects |
|---|---|---|---|---|
| `PENDING_RELEASE` | `ESTRENO` | `cron @ 12:00` | `chapters.status='ready' OR 'ready_degraded'` | `chapter.status='live'`, `released_at=now()`, push fan-out |
| `ESTRENO` | `RECEPCION_IDEAS` | `cron @ 12:01` (o auto-tick interno tras 60 s) | — | — |
| `RECEPCION_IDEAS` | `FILTERING` | `cron @ 18:00` | — | Spawn `director_filter` task |
| `FILTERING` | `VOTACION` | `director_filter task complete` | Al menos 1 twist `approved` **o** flag `allow_empty_vote=true` | — |
| `FILTERING` | `FAILED` | `director_filter task failure (retries exhausted)` | — | Alert admin |
| `VOTACION` | `GENERACION` | `cron @ 23:00` | — | Spawn `generation_pipeline` task |
| `GENERACION` | `PENDING_RELEASE` | `generation_pipeline complete` | `next_chapter.status IN ('ready','ready_degraded')` | — |
| `GENERACION` | `FAILED` | `generation_pipeline timeout > PIPELINE_HARD_DEADLINE_S` | — | Alert admin; emergency fallback (reusar capítulo cliffhanger genérico) |
| `*` | `*` | `admin override` | header `X-Admin-Token` válido | log a `state_transitions.triggered_by='admin'` |

#### 3.3.2 Reglas de invariancia

1. **Mutex de cycle:** todas las mutaciones de estado adquieren `pg_advisory_xact_lock(hashtext('cycle:' || cycle.id))`. Esto neutraliza reintentos concurrentes del cron.
2. **Idempotencia del trigger:** `state_transitions.trigger_id` UNIQUE PARCIAL por `(cycle_id, to_state, trigger_id)` — un mismo `gh-actions run-id` no produce dos transiciones.
3. **No-skip:** `to_state` debe estar en la lista de transiciones legales desde `from_state`. Cualquier otra cosa retorna `409 Conflict`.
4. **Time-fence:** las transiciones que se disparan por reloj rechazan si `now() < state_entered_at + min_dwell_time`. `min_dwell_time` por estado evita doble disparo accidental.

---

## 4. Core Backend Logic & Automation — *The Directors' Engine*

### 4.1 Cronjobs / heartbeat externo

Implementación: **GitHub Actions** workflows con `schedule: cron` (UTC, ajustado a ART). Razón: Fly.io con `auto-stop` puede dormir; un cron in-process no es confiable. GH Actions es gratis, audita logs, y reintenta automáticamente.

```yaml
# .github/workflows/tick-12.yml
name: tick-12-estreno
on:
  schedule:
    - cron: '0 15 * * *'   # 15:00 UTC = 12:00 ART
  workflow_dispatch:
jobs:
  tick:
    runs-on: ubuntu-latest
    steps:
      - name: Compute HMAC and POST
        env:
          TICK_SECRET: ${{ secrets.TICK_SECRET }}
          API_URL: ${{ secrets.API_URL }}
        run: |
          ts=$(date -u +%s)
          body=$(jq -nc --arg to ESTRENO --arg ts "$ts" --arg run "$GITHUB_RUN_ID" \
                  '{to:$to, ts:($ts|tonumber), trigger_id:$run}')
          sig=$(printf '%s' "$body" | openssl dgst -sha256 -hmac "$TICK_SECRET" -binary | base64)
          curl --fail -sS -X POST "$API_URL/api/v1/internal/transition" \
               -H "Content-Type: application/json" \
               -H "X-Tick-Signature: $sig" \
               -d "$body"
```

Cuatro workflows simétricos:

| Workflow | Cron UTC | Cron ART | `to_state` |
|---|---|---|---|
| `tick-12-estreno` | `0 15 * * *` | 12:00 | `ESTRENO` |
| `tick-18-vote` | `0 21 * * *` | 18:00 | `FILTERING` (luego auto a `VOTACION`) |
| `tick-23-generate` | `0 2 * * *` | 23:00 | `GENERACION` |
| `tick-2355-watchdog` | `55 2 * * *` | 23:55 | `WATCHDOG` (health-check, no muta estado) |

Validación server-side del tick:
- Header `X-Tick-Signature` = `HMAC-SHA256(TICK_SECRET, body)`.
- `body.ts` debe estar dentro de ±300 s de `now()` (anti-replay).
- `body.trigger_id` único en `state_transitions`.

### 4.2 Director's Filter — Prompt Engineering técnico

#### 4.2.1 Criterios de evaluación

| Criterio | Definición operativa | Acción |
|---|---|---|
| **Ofensivo** | Lenguaje de odio, violencia explícita gráfica, contenido sexual con menores, doxxing real, amenazas dirigidas. | `rejected_offensive` |
| **Incoherente** | No es una continuación narrativa: spam, palabras random, copy-paste del capítulo, irrelevancia total al cliffhanger. | `rejected_incoherent` |
| **Off-thread** | Introduce elementos que violan la **bible** de la temporada (ej. un dragón en una serie cyberpunk realista) sin justificación interna. | `rejected_incoherent` |
| **Spam / duplicado** | Similitud léxica > 0.85 (Levenshtein normalizado) con otra propuesta del mismo capítulo del mismo usuario, o repetición de palabras. | `rejected_spam` |
| **Aprobado** | Continuación coherente, dentro de la bible, no ofensiva, original. | `approved` |

#### 4.2.2 Prompt (system + user) en JSON-mode

**System prompt** (estable, versionado en `prompts/director_v1.txt`):
```
Sos el "Director" de una serie narrativa colaborativa. Tu trabajo es
clasificar propuestas de continuación de capítulo siguiendo CRITERIOS
ESTRICTOS. Sos riguroso pero justo: NO censurás opiniones impopulares
ni giros oscuros si son coherentes con el tono. Sí rechazás contenido
ofensivo, incoherente, spam o que viole la "bible" de la temporada.

Devolvés EXCLUSIVAMENTE un JSON válido conforme al schema dado.
No agregás texto fuera del JSON. No inventás propuestas: clasificás
solamente las que te entregan.

CRITERIOS:
- offensive: odio, violencia gráfica gratuita, sexual con menores,
  doxxing, amenazas reales.
- incoherent: no continúa la trama, no responde al cliffhanger,
  ignora elementos centrales del capítulo, viola la bible.
- spam: muy similar a otra propuesta, palabras random, repetición.
- approved: todo lo demás. Aceptá giros raros, humor, terror, drama.

REGLAS DE ORO:
1. Si dudás entre "approved" y "rejected", aprobá.
2. No moralizás. No agregás warnings. No explicás tu rol.
3. El campo "reason" tiene MAX 80 caracteres y está en español.
```

**User prompt** (template, render con Jinja2):
```
=== BIBLE DE LA TEMPORADA ===
{{ season.bible_json | tojson }}

=== ÚLTIMOS 3 CAPÍTULOS (synopsis) ===
{% for c in last_chapters %}
- Día {{ c.day_index }} — {{ c.title }}: {{ c.synopsis }}
{% endfor %}

=== CAPÍTULO ACTUAL (cliffhanger a resolver) ===
Día {{ current.day_index }} — {{ current.title }}
Synopsis: {{ current.synopsis }}
Cliffhanger: {{ current.manifest_json.cliffhanger }}

=== PROPUESTAS A CLASIFICAR ===
{% for t in batch %}
[{{ t.public_id }}] {{ t.content }}
{% endfor %}

Devolvé el JSON.
```

**Response schema** (`response_schema` Pydantic, enforced por Gemini):
```python
class DirectorVerdict(BaseModel):
    twist_id: str = Field(..., description="public_id UUID del twist")
    decision: Literal["approved","rejected_offensive",
                      "rejected_incoherent","rejected_spam"]
    reason: str = Field(..., max_length=80)

class DirectorBatchResponse(BaseModel):
    verdicts: list[DirectorVerdict]
```

#### 4.2.3 Algoritmo `director_filter`

```python
async def director_filter(chapter_id: int) -> None:
    async with cycle_lock(chapter_id):
        twists = await repo.list_pending_twists(chapter_id)
        if not twists:
            await repo.transition_cycle(chapter_id, to="VOTACION")
            return

        ctx = await repo.build_director_context(chapter_id)
        verdicts: dict[str, DirectorVerdict] = {}

        for batch in chunked(twists, DIRECTOR_BATCH_SIZE):
            try:
                resp = await llm.gemini_call(
                    system=DIRECTOR_SYSTEM_PROMPT,
                    user=render_director_user_prompt(ctx, batch),
                    response_schema=DirectorBatchResponse,
                    temperature=0.2,
                    max_output_tokens=2048,
                )
            except (RateLimitError, ServiceUnavailable):
                resp = await llm.github_models_fallback(...)
            for v in resp.verdicts:
                verdicts[v.twist_id] = v

        # Default-deny if LLM omitió un twist (defensive)
        for t in twists:
            v = verdicts.get(str(t.public_id))
            if v is None:
                await repo.update_twist_status(
                    t.id, "rejected_incoherent",
                    reason="No clasificado por el filtro (fail-closed).")
            else:
                await repo.update_twist_status(t.id, v.decision, v.reason)

        await repo.transition_cycle(chapter_id, to="VOTACION")
```

### 4.3 Selección del ganador (23:00)

```sql
-- Determinístico, idempotente
WITH ranked AS (
  SELECT t.id, t.public_id, t.content,
         COUNT(v.id) AS vote_count,
         t.submitted_at,
         ROW_NUMBER() OVER (
           ORDER BY COUNT(v.id) DESC, t.submitted_at ASC, t.id ASC
         ) AS rn
  FROM twists t
  LEFT JOIN votes v ON v.twist_id = t.id
  WHERE t.chapter_id = :chapter_id AND t.status = 'approved'
  GROUP BY t.id
)
SELECT * FROM ranked WHERE rn = 1;
```

**Regla de empate (G-6):** orden por `(votes DESC, submitted_at ASC, id ASC)`. Es **determinístico** y **conocido por los usuarios** (se documenta en /faq). Premia a quien propuso primero la idea ganadora — refuerza el incentivo a participar temprano.

**Caso degenerado (cerrado en OQ-4):** si no hay ningún twist `approved` al cerrar la votación, **el sistema continúa la trama autónomamente**. El LLM-escritor se invoca con `winner_twist = NULL` y un *system prompt* extendido que le indica:

> "Continuá la historia de manera coherente con la bible y el cliffhanger. Resolvé el cliffhanger del capítulo previo y plantá uno nuevo. No menciones que no hubo propuestas; narrá normal."

El capítulo resultante se marca `status='ready'` (no `ready_degraded` — degraded queda reservado para fallas de pipeline). Se loguea el evento `cycle_autocontinued` con `cycle_id` para observabilidad.

### 4.4 Armado del prompt compositivo para T2I

El LLM-escritor produce, por panel, un campo `visual_prompt` ya en inglés y optimizado para diffusion. El pipeline lo decora con `style_tag` y semilla:

```python
def compose_t2i_url(panel: Panel, style: SeasonStyle, seed: int) -> str:
    full_prompt = (
        f"{panel.visual_prompt}, "
        f"{style.global_tags}, "        # e.g. "cinematic, 35mm film, moody lighting"
        f"{style.negative_hint}"        # e.g. "no text, no watermark"
    )
    encoded = urllib.parse.quote(full_prompt, safe="")
    return (
        f"https://image.pollinations.ai/prompt/{encoded}"
        f"?width=1024&height=1024&seed={seed}"
        f"&model=flux&nologo=true&enhance=false"
    )
```

`seed` se deriva de `hash(chapter_id, panel.idx)` para reproducibilidad: si hay que re-renderizar, sale igual.

### 4.5 Abstracción `ImageProvider` (cerrada en OQ-3)

Aunque el MVP solo necesita Pollinations + HuggingFace, **todo acceso a T2I pasa por una interface única desde el día 1**. Esto bloquea el acoplamiento del pipeline a un proveedor específico y permite incorporar GPU local (ComfyUI vía Cloudflare Tunnel) en v0.2 con cero cambios en `generation_pipeline`.

#### 4.5.1 Contrato

```python
# app/providers/image/base.py
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Literal

@dataclass(frozen=True)
class ImageRequest:
    prompt: str                       # ya compuesto: visual_prompt + style + negatives
    seed: int                         # derivado de hash(chapter_id, panel_idx)
    width: int = 1024
    height: int = 1024
    aspect: Literal["1:1","16:9","9:16"] = "1:1"
    style_tag: str | None = None      # p.ej. "flux", "sdxl-cinematic"

@dataclass(frozen=True)
class ImageResult:
    bytes_: bytes                     # contenido binario (webp/png/jpg)
    mime_type: str                    # "image/webp" | "image/png" | "image/jpeg"
    provider: str                     # nombre canónico: "pollinations" | "hf" | "local_comfy"
    model: str                        # "flux", "sdxl-base-1.0", "comfy:dreamshaper-v8", …
    latency_ms: int
    cost_usd: float = 0.0             # 0 en free tiers; informativo para futuras decisiones

class ImageProviderError(Exception): ...
class ImageProviderRateLimited(ImageProviderError): ...
class ImageProviderUnavailable(ImageProviderError): ...
class ImageProviderInvalidOutput(ImageProviderError): ...

class ImageProvider(ABC):
    name: str                                    # canonical id

    @abstractmethod
    async def health(self) -> bool: ...
    """Health-check rápido (<2s). False ⇒ no se intentan generaciones contra este provider."""

    @abstractmethod
    async def generate(self, req: ImageRequest) -> ImageResult: ...
    """Genera UNA imagen. Debe levantar las excepciones tipadas de arriba.
       NO debe reintentar internamente — la política de reintentos vive en el router."""

    @property
    @abstractmethod
    def capabilities(self) -> dict: ...
    """Reporta features: {'max_resolution': 1536, 'supports_seed': True, …}."""
```

#### 4.5.2 Implementaciones

| Provider class | `name` | Modo | Cuándo se usa | Notas |
|---|---|---|---|---|
| `PollinationsProvider` | `"pollinations"` | HTTP GET sin auth | Default primario en MVP | URL pattern documentado en §4.4. Health ping a `https://image.pollinations.ai/` |
| `HuggingFaceProvider` | `"hf"` | HTTP POST con `HF_TOKEN` | Fallback en MVP | Endpoint `/models/{model}` con `model="black-forest-labs/FLUX.1-schnell"`. |
| `LocalComfyProvider` | `"local_comfy"` | HTTP POST a Cloudflare Tunnel | **Reservado v0.2** — no se implementa en MVP | Construye workflow JSON de ComfyUI, hace `POST /prompt`, polling de `/history/{prompt_id}`. |

#### 4.5.3 Router — `ImageProviderRouter`

`generation_pipeline` **nunca** instancia un provider concreto. Recibe un `ImageProviderRouter` que aplica la política de fallback:

```python
class ImageProviderRouter:
    def __init__(self, chain: list[ImageProvider],
                 max_retries_per_provider: int = T2I_MAX_RETRIES,
                 backoff: BackoffPolicy = exp_backoff(2, 6, 18)):
        self.chain = chain  # orden = prioridad

    async def render(self, req: ImageRequest) -> ImageResult:
        last_exc: Exception | None = None
        for provider in self.chain:
            if not await provider.health():
                continue
            for attempt in range(self.max_retries_per_provider):
                try:
                    return await provider.generate(req)
                except ImageProviderRateLimited:
                    break                           # saltar al siguiente provider
                except ImageProviderUnavailable as e:
                    last_exc = e
                    await asyncio.sleep(self.backoff(attempt))
                except ImageProviderInvalidOutput as e:
                    last_exc = e                    # no reintenta, siguiente provider
                    break
        raise ImageProviderUnavailable("All providers exhausted") from last_exc
```

#### 4.5.4 Configuración del chain

| Entorno | `chain` (orden) | Justificación |
|---|---|---|
| **MVP (default)** | `[PollinationsProvider, HuggingFaceProvider]` | Cero costo, cero auth en primario. |
| **v0.2 (GPU local online)** | `[LocalComfyProvider, PollinationsProvider, HuggingFaceProvider]` | Calidad y control prioridad 1; nube como red de seguridad. |
| **Dev / tests** | `[FakeImageProvider]` | Imágenes 1×1 deterministas, sin red. |

#### 4.5.5 Invariantes que el router garantiza al pipeline

1. **Atomicidad por panel:** o devuelve `ImageResult` válido, o levanta `ImageProviderUnavailable`. Nunca `None`, nunca payload corrupto.
2. **Observabilidad:** cada intento se loguea como `image_provider_attempt {provider, attempt, outcome, latency_ms}`.
3. **No-retry semántico:** el router NO reintenta sobre `ImageProviderInvalidOutput` (e.g. respuesta NSFW con `nologo=true` ignorado) — falla rápido y baja al siguiente provider.
4. **Health gating:** un provider con `health()=False` no consume retries.

Con esta abstracción, la migración a GPU local en v0.2 se reduce a (a) implementar `LocalComfyProvider`, (b) cambiar el orden del `chain` en config. **Cero cambios en `generation_pipeline`, cero cambios en DB, cero cambios en API pública.**

### 4.6 Health gate y kill-switch

- Endpoint `GET /api/v1/internal/health/cycle` retorna estado actual, last transition, latencia de pipeline.
- Endpoint `POST /api/v1/internal/kill-switch` (admin) congela todas las transiciones y muestra al cliente un banner *"Hoy no hay capítulo nuevo, volvemos mañana"*.

---

## 5. API Contracts & Endpoints

Convenciones:
- Base URL: `https://api.aiplottwist.example/api/v1`
- Content-Type: `application/json; charset=utf-8`
- Auth: `Authorization: Bearer <jwt>` excepto `/auth/*` e `/internal/*` (HMAC).
- Errores: RFC 7807 (Problem Details), p.ej.:
  ```json
  {"type":"about:blank","title":"Submission window closed",
   "status":409,"code":"window_closed","detail":"…","instance":"/twists/submit"}
  ```
- Versionado: `v1` en path. Breaking changes ⇒ `v2`.
- Pagination: cursor-based via `?cursor=<opaque>&limit=<int<=100>`.

### 5.1 `GET /api/v1/chapters/today`

**Descripción:** retorna el manifest del capítulo `live` del día.

**Auth:** opcional (sirve también a anónimos para preview).

**Query params:** ninguno.

**Response 200:**
```json
{
  "chapter": {
    "id": "9f3a…-uuid",
    "season": { "slug": "s01-el-tunel", "title": "El Túnel" },
    "day_index": 7,
    "title": "Lo que había detrás del espejo",
    "synopsis": "Mariana cruza el umbral y descubre que…",
    "released_at": "2026-06-07T15:00:00Z",
    "cycle_state": "RECEPCION_IDEAS",
    "windows": {
      "submit_until":  "2026-06-07T21:00:00Z",
      "vote_from":     "2026-06-07T21:00:00Z",
      "vote_until":    "2026-06-08T02:00:00Z",
      "next_release":  "2026-06-08T15:00:00Z"
    },
    "panels": [
      {
        "idx": 1,
        "image_url": "https://assets.aiplottwist.example/s01/d07/panel_1.webp",
        "image_blurhash": "LKO2?V%2Tw=w]~RBVZRi};RPxuwH",
        "tts_url": "https://assets.aiplottwist.example/s01/d07/panel_1.mp3",
        "narration": "El espejo crujió como hielo viejo…",
        "mood": "tense"
      }
      /* … 3 a 4 panels … */
    ],
    "cliffhanger": "Una voz —la suya— le respondió desde el otro lado."
  }
}
```

**Response 404:** `{"code":"no_live_chapter","detail":"No hay capítulo activo."}` (kill-switch o failover).

**Cache:** `Cache-Control: public, max-age=60, stale-while-revalidate=600`.

### 5.2 `POST /api/v1/twists/submit`

**Descripción:** crea una propuesta. Solo válido durante `RECEPCION_IDEAS`.

**Auth:** requerido.

**Headers:**
- `Idempotency-Key: <uuid>` requerido. Reintentos con misma key retornan 200 + body original.

**Request:**
```json
{
  "chapter_id": "9f3a…-uuid",
  "content": "Mariana acepta hablar con su reflejo y este le confiesa que es del año 1998."
}
```

**Validaciones (orden):**
1. `chapter_id` existe y pertenece a la temporada activa.
2. Cycle.state == `RECEPCION_IDEAS` ∧ `now() < windows.submit_until`.
3. `len(content) ∈ [5, 280]`, después de `strip()` y normalización Unicode NFKC.
4. Usuario no banneado.
5. `count(twists WHERE user=me, chapter=this) < MAX_TWISTS_PER_USER_PER_CHAPTER`.

**Response 201:**
```json
{
  "twist": {
    "id": "b1c2…-uuid",
    "chapter_id": "9f3a…-uuid",
    "content": "Mariana acepta hablar con su reflejo…",
    "status": "pending_review",
    "submitted_at": "2026-06-07T16:42:11Z"
  },
  "remaining_submissions": 2
}
```

**Errores específicos:**
- `409 window_closed` — fuera de la ventana de envío.
- `409 over_quota` — alcanzó `MAX_TWISTS_PER_USER_PER_CHAPTER`.
- `422 invalid_content` — fuera de rango de longitud o caracteres prohibidos.

### 5.3 `GET /api/v1/twists/vote-feed`

**Descripción:** feed de propuestas `approved` del capítulo del día, listas para votar.

**Auth:** requerido.

**Query params:**
- `cursor` (opaque), `limit` (default 25, max 100).
- `sort` ∈ `random|recent|hot` (default `random`, seed estable por `cycle_id` y `user_id` para evitar refresh-gaming).

**Pre-condición:** cycle.state == `VOTACION`. Fuera de ventana ⇒ `409 window_closed`.

**Response 200:**
```json
{
  "items": [
    {
      "id": "b1c2…-uuid",
      "content": "Mariana acepta hablar con su reflejo…",
      "vote_count": 12,
      "has_my_vote": false
    }
    /* … */
  ],
  "page": {
    "next_cursor": "eyJv…",
    "limit": 25,
    "total_approved": 87
  },
  "user_quota": {
    "votes_used": 1,
    "votes_remaining": 4
  }
}
```

### 5.4 `POST /api/v1/twists/vote`

**Descripción:** registra un voto. Atómico: `INSERT … ON CONFLICT DO NOTHING`.

**Auth:** requerido.

**Headers:**
- `Idempotency-Key: <uuid>` requerido.

**Request:**
```json
{ "twist_id": "b1c2…-uuid" }
```

**Validaciones:**
1. cycle.state == `VOTACION`.
2. Twist existe ∧ pertenece al capítulo en curso ∧ `status='approved'`.
3. Usuario no votó previamente este twist (UNIQUE).
4. `count(votes WHERE user=me, chapter=this) < MAX_VOTES_PER_USER_PER_CHAPTER`.

**SQL ejecutado:**
```sql
WITH ins AS (
  INSERT INTO votes (twist_id, user_id, chapter_id)
  VALUES (:twist_id, :user_id, :chapter_id)
  ON CONFLICT (twist_id, user_id) DO NOTHING
  RETURNING id
)
SELECT COUNT(*) FROM ins;
```

**Response 200:**
```json
{
  "twist_id": "b1c2…-uuid",
  "new_vote_count": 13,
  "user_quota": { "votes_used": 2, "votes_remaining": 3 }
}
```

**Errores:**
- `409 window_closed` — fuera de `VOTACION`.
- `409 already_voted` — usuario ya votó esa propuesta.
- `409 over_quota` — alcanzó `MAX_VOTES_PER_USER_PER_CHAPTER`.

### 5.5 `DELETE /api/v1/twists/{public_id}` (OQ-6)

**Descripción:** soft-delete de una propuesta propia. Permitido **solo** durante `RECEPCION_IDEAS` (antes de las 18:00). Después del cierre, la propuesta es inmutable.

**Auth:** requerido.

**Validaciones:**
1. Twist existe y `twists.user_id == jwt.user_id`.
2. `cycle.state == 'RECEPCION_IDEAS'` ∧ `now() < windows.submit_until`.
3. `twists.status == 'pending_review'` (no se pueden borrar twists ya filtrados — caso defensivo si el filtro se adelanta).

**SQL:**
```sql
UPDATE twists
   SET status = 'deleted_by_user',
       deleted_at = now()
 WHERE public_id = :public_id
   AND user_id   = :user_id
   AND status    = 'pending_review'
RETURNING id;
```

**Response 200:** `{"twist_id": "b1c2…-uuid", "deleted_at": "2026-06-07T16:55:00Z", "remaining_submissions": 3}`

**Errores:**
- `403 forbidden_not_owner` — twist no es del usuario.
- `409 window_closed` — fuera de `RECEPCION_IDEAS`.
- `409 already_filtered` — el filtro ya corrió.

**Nota (corregido en módulo 005, ronda 4):** se mantiene la fila en DB con `status='deleted_by_user'` para auditoría. **No** libera quota de `MAX_TWISTS_PER_USER_PER_CHAPTER` (evita spam-then-delete-loop). La quota usada es una propiedad calculada: `count(twists WHERE user_id=? AND chapter_id=?)` — **todos los estados incluyendo `deleted_by_user`**. La quota libre = `MAX − quota_usada`. Esto implementa explícitamente la regla "borrar no libera quota". Ver `specs/005-twists-submission/research.md` R-003 y `docs/adr/0002-quota-counts-deleted.md`.

### 5.6 `POST /api/v1/push/subscribe` (OQ-5 — Web Push / VAPID)

**Descripción:** registra el endpoint Web Push del navegador del usuario.

**Auth:** requerido.

**Request:**
```json
{
  "endpoint": "https://fcm.googleapis.com/fcm/send/eXp…",
  "keys": { "p256dh": "BNc…", "auth": "tBHI…" },
  "user_agent": "Mozilla/5.0 …"
}
```

**Response 201:** `{"subscription_id": 42}`

**Side-effects:** UPSERT por `endpoint` UNIQUE. Si el endpoint ya existía vinculado a otro `user_id` (cambio de cuenta en el mismo navegador), se reasigna.

**Server-side push trigger:** el `state_engine`, tras `chapter.status='live'`, hace fan-out con `pywebpush.send_web_push(...)` por cada subscripción. Failures con `gone` (404/410) eliminan la subscripción.

### 5.7 Auth endpoints (módulo 002 — refinado)

| Método | Path | Propósito |
|---|---|---|
| `POST`   | `/auth/redeem-invite` | Canjea `invite_code` + `display_name`. **Respuesta:** `{user, jwt, device_secret, jwt_expires_at}`. El cliente persiste `jwt` + `device_secret` en IndexedDB. Rate-limit 5/hora/IP, 404 colapsado en errores de código. |
| `POST`   | `/auth/refresh` | Reissue de JWT a partir de `device_secret`. **Respuesta:** `{jwt, jwt_expires_at}`. Comparación de hash en tiempo constante. Usuarios baneados → 401 colapsado. |
| `GET`    | `/auth/me` | Devuelve el usuario autenticado y actualiza `last_seen_at`. Banned → 403. |

JWT: HS256, claims `{sub, iss, aud, iat, exp (90d), jti (ULID)}`. Verificación con leeway 60 s. Detalle completo en [specs/002-auth-invite-flow/contracts/auth.yaml](./specs/002-auth-invite-flow/contracts/auth.yaml).

### 5.8 Endpoints auxiliares (no críticos al loop)

| Método | Path | Propósito |
|---|---|---|
| `GET`    | `/me/twists` | Lista de propuestas propias del capítulo actual, con status. |
| `GET`    | `/chapters/{id}` | Lectura de capítulo arbitrario (archivo). |
| `GET`    | `/seasons/{slug}` | Detalle de temporada activa + bible pública. |
| `DELETE` | `/push/subscriptions/{id}` | Desuscribir push. |
| `POST`   | `/internal/transition` | Heartbeat (HMAC) — §4.1. |
| `POST`   | `/internal/kill-switch` | Admin freeze. |
| `GET`    | `/internal/health/cycle` | Health & métricas. |

---

## 6. User Behavior & Acceptance Criteria

### 6.1 Casos de uso

#### UC-1 — *Happy path*: usuario propone, gana y ve su idea realizada

```
Pre: Usuario "Luz" tiene JWT válido, cycle en RECEPCION_IDEAS.

1. Luz abre la PWA a las 13:00 ART; ve capítulo Día 7 y su cliffhanger.
2. Escribe "Mariana acepta hablar con su reflejo y este le confiesa que es del año 1998."
3. POST /twists/submit → 201, status=pending_review.
4. A las 18:00 ART, Director's Filter clasifica la propuesta como approved.
5. Luz vuelve a las 19:30, ve su propia propuesta en /vote-feed.
6. A las 22:30 su propuesta tiene 12 votos (la más alta).
7. A las 23:00 GENERACION arranca; el ganador es su twist.
8. A las 23:42 generation_pipeline completa; capítulo Día 8 status=ready.
9. A las 12:00 del día 8, Luz recibe push y abre la app: el nuevo capítulo
   refleja su propuesta. La UI muestra "Plot twist propuesto por @Luz".

Criterio de aceptación AC-1:
  - chapters[day=8].manifest_json.winner_twist_id == twist creado por Luz.
  - UI renderiza badge "Por @Luz" en la apertura del Día 8.
  - Evento analytics `winner_attributed` con user_id=Luz.
```

#### UC-2 — Envío fuera de hora (después de 18:00)

```
1. Usuario "Tomás" intenta POST /twists/submit a las 18:00:07.
2. Backend: cycle.state ya es FILTERING (o VOTACION).
3. Response: 409 {"code":"window_closed",
     "detail":"La ventana de propuestas cerró a las 18:00 ART.",
     "next_window":{"opens_at":"2026-06-08T15:00:00Z"}}
4. PWA muestra modal "Llegaste tarde. Probá mañana a partir de las 12:00."

Criterio AC-2:
  - Ningún twist con submitted_at >= state_entered_at(FILTERING) existe en DB.
  - Toast/modal con el mensaje exacto se renderiza.
  - El cliente NO reintenta automáticamente.
```

#### UC-3 — Empate exacto a las 23:00

```
1. Twists A y B tienen 14 votos cada uno al cierre de VOTACION.
2. Tiebreak determinístico: ORDER BY votes DESC, submitted_at ASC, id ASC.
3. Si A.submitted_at < B.submitted_at → A gana.
4. La UI del día siguiente muestra: "Plot twist propuesto por @autorDeA
   (en empate con @autorDeB, ganó por orden de envío)."

Criterio AC-3:
  - SQL de selección retorna exactamente 1 fila para el tiebreak conocido.
  - manifest_json.winner_metadata contiene
      {tiebreak: true, runner_up_twist_id: B.id}
  - El runner-up recibe attribution secundaria en la UI (transparencia).
```

#### UC-4 — Director rechaza propuesta ofensiva

```
1. Usuario envía contenido ofensivo a las 14:00.
2. A las 18:00 director_filter clasifica → rejected_offensive.
3. El twist NO aparece en /vote-feed.
4. El usuario, al consultar /me/twists, ve su propuesta con
   status=rejected_offensive y reason="Contenido ofensivo."
5. Se incrementa un contador interno user.offense_count; al 3er strike
   el usuario queda is_banned=TRUE (manual unban por admin).

Criterio AC-4:
  - Twist rechazado NO aparece bajo /vote-feed nunca.
  - El propio usuario ve el rechazo y la razón.
  - Otros usuarios NO ven el contenido rechazado en ningún feed.
```

#### UC-5 — Pipeline de generación falla parcialmente

```
1. Panel 3 de 4 falla todos los retries en Pollinations y HF.
2. generation_pipeline marca next_chapter.status='ready_degraded'.
3. manifest_json.panels[2].image_url = placeholder oficial
   (asset estático en R2: "still loading…").
4. A las 12:00 el capítulo sale igual (no se rompe el loop).
5. Admin recibe alerta vía Discord webhook.

Criterio AC-5:
  - El estreno NO se atrasa por una falla parcial.
  - status='ready_degraded' visible en /internal/health/cycle.
  - Discord webhook enviado dentro de los 30s de detectada la falla.
```

### 6.2 Criterios de aceptación técnicos del pipeline automatizado

| ID | Criterio | Cómo se mide |
|---|---|---|
| **AT-1** | Una transición de estado es **idempotente**: re-disparar el mismo `trigger_id` no produce efectos duplicados. | Test integración: invocar `/internal/transition` 3× con mismo body → solo 1 fila en `state_transitions`. |
| **AT-2** | El cron de las 12:00 cumple p99 < 30 s de delta entre `cron fire` y `chapter.released_at`. | Métrica `cron_delivery_lag_seconds`. |
| **AT-3** | El Director's Filter procesa hasta **500 twists** en ≤ 4 min usando solo el free tier de Gemini. | Stress test sintético. |
| **AT-4** | La selección de ganador es **determinística** ante reintentos: misma DB ⇒ mismo ganador. | Test: ejecutar `pick_winner(chapter_id)` 10× → siempre mismo `twist_id`. |
| **AT-5** | El pipeline de generación cumple `PIPELINE_HARD_DEADLINE_S` (3300 s) en p95. | Métrica `generation_pipeline_duration_seconds_p95`. |
| **AT-6** | Si Pollinations falla, HuggingFace toma sin intervención humana, y el panel queda disponible. | Test chaos: bloquear DNS de `image.pollinations.ai` y verificar capítulo final. |
| **AT-7** | No existen ventanas en las que dos estados son `current` simultáneamente. | Query test: `SELECT COUNT(*) FROM cycles WHERE state IN (...) AND cycle_date=today` == 1. |
| **AT-8** | El `vote-feed` con `sort=random` es **estable** dentro de una sesión de votación para un mismo usuario (no se reordena con cada refresh). | Llamar dos veces con misma JWT → mismo orden. |
| **AT-9** | El sistema soporta `GET /chapters/today` con 200 RPS sostenidas durante 60 s sin error y p95 < 500 ms. | `k6 run` en CI semanal. |
| **AT-10** | Ningún componente del MVP requiere pago: factura cloud mensual = USD 0. | Auditoría manual al cierre de mes. |

---

## 7. Open Questions

> Las preguntas abiertas iniciales fueron cerradas en la primera ronda de revisión con el Product Owner (2026-06-07). Ver §Apéndice A para el detalle de decisiones. Las únicas OQ vivas son las que siguen.

| ID | Pregunta | Status | Resolución / acción |
|---|---|---|---|
| **OQ-7** | TZ: confirmamos `America/Argentina/Buenos_Aires` como anclaje único, ¿correcto? | `open` | Asumido en draft; pendiente OK explícito del PO. |
| **OQ-8** | ¿La temporada tiene final fijo (escrito por PO) o termina al capítulo N con un cierre generado? | `parked` | Postergada por decisión del PO (2026-06-07). La duración de temporada queda flexible (`TENTATIVE_SEASON_LENGTH=7` como hint). Se re-aborda cuando se acerque el final de la 1ª temporada. No bloquea MVP. |
| **OQ-9** | ¿Los **horarios** (12/18/23) son los definitivos o se ajustarán en pruebas? | `closed` | Constantes `CYCLE_TIMES.*` en env; workflows de GH Actions se regeneran con `scripts/render_cron_workflows.py`. |

---

## 8. Risks & Mitigations

| ID | Riesgo | Probabilidad | Impacto | Mitigación |
|---|---|---|---|---|
| **R-1** | Pollinations.ai introduce rate-limit o se vuelve inestable | Media | Alto | Fallback HF + opcionalmente Together (free tier) como tercer proveedor. Cachear retries. |
| **R-2** | Gemini Free Tier cambia cuotas durante la fase de prueba | Baja | Alto | Capa `LLMProvider` con switch en runtime a GitHub Models. |
| **R-3** | Inconsistencia visual entre capítulos (los personajes "cambian de cara") | Alta | Medio | MVP acepta el costo. v0.2 evalúa LoRA / IP-Adapter con GPU local. |
| **R-4** | Fly.io free tier suspende la VM por inactividad mientras corre `generation_pipeline` | Baja | Alto | `auto_stop=false` en `fly.toml`; healthcheck propio que mantiene activo. |
| **R-5** | Neon free tier alcanza el límite de 0.5 GB | Baja (en MVP) | Medio | Archivar manifests viejos a R2 después de 30 días; mantener solo metadata en DB. |
| **R-6** | GitHub Actions cron tiene jitter de hasta 15 min en horarios pico | Media | Medio | Watchdog a las 12:05 reenvía la transición si `chapter.released_at IS NULL`. |
| **R-7** | Voto-bombing / sock-puppets dentro del grupo cerrado | Media | Bajo (es family-and-friends) | UNIQUE(twist_id, user_id) + `MAX_VOTES_PER_USER`. Auditoría manual de patrones sospechosos. |
| **R-8** | LLM "alucina" y aprueba contenido ofensivo | Media | Alto | Post-filter de listas negras (regex de slurs en español) ejecutado DESPUÉS del LLM. Defense in depth. |
| **R-9** | Pollinations devuelve imagen con watermark/NSFW pese al flag `nologo` | Baja | Medio | Validación con CLIP free (HF embeddings) opcional; fallback a HF si confianza baja. |
| **R-10** | Dependencia de un solo Lead Architect (bus factor) | Alta | Alto | Este SDD + Spec Kit `plan.md` + `tasks.md` reducen el bus factor. |

---

### Apéndice A — Decisiones de diseño cerradas en este SDD

**Ronda 0 (alineación inicial, 2026-06-07):**

1. Cliente único: **PWA**.
2. Backend: **FastAPI (Python 3.11)** por encaje con ecosistema LLM y validación con Pydantic v2.
3. T2I: **APIs públicas gratuitas** (Pollinations → HF). Sin GPU local en MVP. Interface `ImageProvider` permite swap a v0.2.
4. Hosting: **Fly.io + Neon + Cloudflare Pages + R2 + GitHub Actions cron**. Cero costo verificado.
5. TZ anclado: **America/Argentina/Buenos_Aires**.
6. FSM con **mutex advisory en PG** + **trigger_id único** garantizan idempotencia ante reintentos.

**Ronda 1 (cierre de OQ-1, 2, 4, 5, 6 con PO, 2026-06-07):**

7. **Auth (OQ-1, decisión del Architect):** invite-code + device-token anónimo. Sin email, sin password, sin OAuth. Onboarding: el PO emite códigos, el usuario los canjea junto a su `display_name`, el server genera `device_token` (hash sha256 de un secret random + UA) y devuelve un JWT HS256 de larga duración (90 días). Pérdida de dispositivo ⇒ re-invitación. Justificación: mínima fricción para grupo cerrado familiar, sin coste de servicio de email.
8. **Capítulo 0 (OQ-2):** el PO escribirá manualmente la `bible_json` de la temporada y el **manifest** completo del Capítulo 0 (synopsis, panels[], visual_prompts, narración, cliffhanger). El pipeline T2I renderiza las imágenes desde esos prompts. Operativamente: un script CLI `scripts/seed_chapter_zero.py --season s01 --manifest cap0.yaml` ingesta el YAML y dispara solo el step T2I del pipeline.
9. **Duración de temporada:** **tentativa 7 capítulos** (1 semana). No se enforza en DB; queda como constante `TENTATIVE_SEASON_LENGTH`. El cierre formal de temporada se decide cap-a-cap.
10. **Sin twists aprobados (OQ-4):** el LLM-escritor continúa la trama autónomamente desde el cliffhanger (ver §4.3 actualizado). El capítulo se marca `ready` (no `ready_degraded`) y se loguea evento `cycle_autocontinued`.
11. **Web Push (OQ-5):** habilitado con VAPID. Suscripción opt-in en el primer estreno. Se dispara solo en la transición a `ESTRENO`. Librería: `pywebpush`. Ver §5.6.
12. **Borrar/editar twist (OQ-6):** borrar **sí** (soft delete, solo antes de las 18:00 y solo si `status='pending_review'`); editar **no**. La quota gastada **no se restaura** por borrado, para evitar gaming. Ver §5.5.
13. **Horarios configurables:** los slots 12:00/18:00/23:00 viven en `CYCLE_TIMES.*` (env) y los cron de GitHub Actions se regeneran con `scripts/render_cron_workflows.py`. El PO podrá ajustarlos durante las pruebas.

**Ronda 2 (cierre de OQ-3, 2026-06-07):**

14. **Abstracción `ImageProvider` (OQ-3):** se define la interface, las excepciones tipadas y el `ImageProviderRouter` en §4.5 desde el MVP. El chain del MVP es `[Pollinations, HuggingFace]`; v0.2 prependerá `LocalComfyProvider` sin tocar `generation_pipeline`, la DB ni la API. **Justificación:** invertir el costo de la abstracción ahora (≈ 80 LOC) evita un refactor de pipeline cuando se incorpore GPU local.

**Ronda 3 (refinamientos surgidos del módulo 002, 2026-06-07):**

15. **Tabla `invites` propia (refina §3.1):** se eleva el `invite_code` de los users de TEXT+índice a una tabla `invites` con `status` lifecycle (`unused/redeemed/revoked/expired`), `expires_at`, `redeemed_by_user` FK y `note`. `users.invite_code` pasa a ser FK a `invites.code`. **Por qué ahora:** permite que el PO emita/revoque/audite códigos vía CLI (`pnpm issue-invite|revoke-invite|list-invites`) y deja trail completo de quién canjeó qué.
16. **`/auth/refresh` agregado (§5.7):** el SDD original prescribía JWT 90 d pero no documentaba cómo se reissue. Se añade `POST /auth/refresh` que toma el `device_secret` persistido por el cliente y devuelve un JWT fresco. Hash SHA-256 hex comparado en tiempo constante con `hmac.compare_digest`. **Por qué:** evitar el cliff de 90 d que forzaría re-invitación.
17. **Respuesta de `/auth/redeem-invite` ampliada:** ahora retorna `{user, jwt, device_secret, jwt_expires_at}` en lugar de solo `{user, jwt}`. El `device_secret` es base64url 32 bytes; su hash SHA-256 hex se guarda en `users.device_token` (validado por `CHECK char_length=64`). El cliente persiste `jwt` + `device_secret` en IndexedDB.
18. **Formato del código (cierre operativo):** `XXXX-XXXX` base32 RFC 4648 (alfabeto `A-Z2-7`, sin `0/1/I/L/O`) con 1 char de check digit derivado de `sha256(first_7)[0:5]`. El check digit se valida antes del DB lookup, ahorrando round-trip y tightening del rate-limit en brute force.
19. **Rate-limit `rate_limit_buckets` (tabla nueva en §3.1):** sliding window de 1 hora por IP, backed por Postgres (no Redis). Se usa primero en `/auth/redeem-invite`; queda disponible para los endpoints que vengan (votos, twists). 5 intentos/hora/IP en redemption.
20. **Open Questions del dominio auth (no bloquean MVP):**
    - **OQ-AUTH-1**: multi-device por usuario (hoy single-device).
    - **OQ-AUTH-2**: JWKS / asymmetric JWT (hoy solo HS256).
    - **OQ-AUTH-3**: `/auth/logout` server-side que rota `device_token`.

**Ronda 5 (orden de spec-out de los módulos restantes, 2026-06-07):**

21. **Decisión de orden: 009 antes que 008.** El `specs/README.md` declara que el módulo 008 (generation-pipeline) depende del módulo 009 (image-providers). Se especifica primero el productor (009) y después el consumidor (008), en lugar del orden numérico del nombre. Motivos:
    1. **Dependencia de contrato:** 008 importa `ImageProvider`, `ImageProviderRouter` y las excepciones tipadas (`Unavailable` / `RateLimited` / `InvalidOutput`) de 009. El `spec.md` de 008 referencia esas firmas concretas; especificarlas a posteriori dejaría a 008 con prosa hand-wavy ("supongamos un router con fallback"). El mismo patrón se aplicó ya entre 006 (productor de `LLMProvider`) y 008 (consumidor).
    2. **Tamaño asimétrico:** 009 es un módulo conceptualmente chico (interface + 2 impls + router + Fake para tests). 008 es el más grande del proyecto (winner selection determinística + scriptwriter LLM + orquestación panel-a-panel + uploads a R2 + persistencia de manifest + integración con la FSM). Especificar primero el chico permite que el grande lo referencie con paths concretos en su `plan.md` y su `tasks.md`.
    3. **Estrategia de mock alineada:** los tests de 008 dependen del `FakeImageProvider` declarado en 009. Spec-out de 008 sin 009 obliga a inventar una superficie de mock que probablemente discrepe con la real.
    4. **Paralelismo en *implementación* (no en spec):** una vez especificados ambos, en fase de coding 009 y 008 pueden desarrollarse en paralelo con stubs. Pero para el spec-out, el orden es estrictamente secuencial.
    5. **Riesgo de re-trabajo:** si 008 se especifica primero asumiendo una interfaz, y 009 termina divergiendo (p.ej., una excepción extra o un argumento opcional), 008 requiere un patch. Spec-out en orden de dependencia elimina ese riesgo.

    **Numeración de carpetas:** se mantiene `008-generation-pipeline` y `009-image-providers` para preservar agrupación temática en el filesystem (T2I cerca de "generation"). El orden temporal de spec-out es 007 → **009** → **008** → 010 → 011. La numeración refleja la familia técnica; el orden de elaboración refleja el grafo de dependencias.

**Ronda 6 (pivote de scope a video, 2026-06-16):**

22. **Cambio de entregable final del capítulo:** de cómic (imágenes T2I + texto) a **video corto generado (30-40 s)**. Driver: alineación con la visión del PO, que descubrió post-MVP que el output esperado era audiovisual y no estático. El pivote habilita además una rampa futura a T2V paid (Kling / Runway / Luma) post-validación de engagement, sin reescribir el pipeline.

23. **Stack T2V free para MVP:** **HF LTX-Video** (primary) → **Pollinations video** (fallback). Justificación:
    - Únicos providers con API oficial **gratuita** y latencia compatible con la ventana `GENERACION` (23:00 → 12:00 ART).
    - **Kling free vía API oficialmente NO existe** — el plan free de su web (6 gens/día) no expone endpoints públicos; los "kling free APIs" que circulan en GitHub son reverse-engineerings del navegador que violan ToS y se caen sin aviso. Descartado para MVP por riesgo operativo y por Gate 1.
    - `fal.ai` y `Replicate` ofrecen credit one-time pero pasan a paid al agotarse → no sostienen Gate 1 a mediano plazo.

24. **Largo de capítulo: 30-40 s reales, NO 1 minuto.** Composición: **4-6 clips T2V de 5-8 s** + edge-tts narración + transiciones ffmpeg. Justificación:
    - Ningún provider gratuito entrega clips > 10 s en una sola llamada (estado del arte 2026-Q2: LTX 5 s, CogVideoX 6 s, Pollinations 3-5 s).
    - 1 minuto real implica paid (~USD 0.30-1.50 por video según provider) → rompe Gate 1 (USD 0 / mes en beta cerrada).
    - 4-6 clips entran en la ventana `GENERACION` (13 h) con buffer de reintentos por clip individual.
    - El usuario percibe "video" real (no slideshow estático) gracias al stitch con transiciones + audio narrado superpuesto.

25. **Estrategia de degradación en 3 niveles** dentro del `VideoProviderRouter`: primary T2V → fallback T2V → degradación a **pipeline T2I del módulo 009 (cómic legacy)**. El módulo 009 **NO se borra**: queda como "último recurso" que garantiza que el capítulo siempre se entregue (nunca cae en `FAILED` por falla total de la cadena de video). El cliente PWA renderiza apropiadamente según el `manifest_kind` recibido (`video_mp4` vs `comic_panels`).

26. **Stubs paid:** `KlingProvider`, `RunwayProvider`, `LumaProvider` se crean en el módulo nuevo con `raise NotImplementedError`. Razón: cuando el PO decida monetizar/escalar, son ~50 LOC cada uno y se enchufan al router sin tocar `generation_pipeline`. Gate 4 preservado — los proveedores externos solo se acceden vía la abstracción `VideoProvider` / `VideoProviderRouter`.

27. **Estructura del cambio de spec (qué se crea, qué se delta, qué no se toca):**
    1. **Nuevo módulo** `specs/012-video-providers/` — 8 archivos canónicos del Spec Kit (spec, plan, research, data-model, contracts/, quickstart, checklists/, tasks), espejo estructural de `009-image-providers`.
    2. **Delta sobre** `specs/008-generation-pipeline/` — el panel pipeline pasa a video pipeline: orquesta clips T2V → ffmpeg stitch → edge-tts mix → upload de `.mp4` a R2 → persistencia de manifest con `manifest_kind='video_mp4'`. Persistencia del manifest legacy `'comic_panels'` permanece para el path de degradación.
    3. **Módulo 009 sin cambios funcionales** — pasa a ser invocado por el router de video como último fallback. Sus contratos no se rompen.
    4. **Orden de spec-out:** 012 antes que el delta de 008 (mismo principio que Ronda 5 #21 — productor antes que consumidor).
    5. **Numeración del módulo:** se elige 012 (siguiente correlativo) y no, p. ej., `008b-video`, para mantener convención de carpetas planas y permitir referencias futuras estables.

**Ronda 7 (pivote a I2V con catálogo de personajes, 2026-06-24):**

28. **Cambio de entregable T2V → I2V Kling (pago):** primary chain pasa de T2V free (HF LTX + Pollinations) a I2V con Kling AI plan Standard pago (~USD 4.66/mes anual). Driver: T2V free deforma rostros e introduce inconsistencias entre clips, rompiendo el contrato emocional cuando el "personaje del capítulo" es central a la narrativa. I2V resuelve identidad visual fijando una foto-semilla. **Gate 1 (USD 0/mo) se relaja a "USD ≤ 5/mo" en MVP paid** — formaliza la rampa a paid prevista en Ronda 6 #26.

29. **Selección obligatoria de personaje en propuesta:** cada `twist` requiere un `character_id` FK a una nueva tabla `characters` con catálogo hardcoded en seed Alembic. La UI muestra carrusel de cards 1:1; form inválido sin selección. Razón: I2V necesita una imagen-semilla; vincular esa imagen a la propuesta antes del cierre de RECEPCION_IDEAS deja el binding determinístico y evita coordinación posterior.

30. **Composición 14s fija = intro 2s + Kling 10s + outro 2s:** el scriptwriter pasa de generar 4-6 clips a una sola escena. El video final lo arma `stitch_pipeline` con ffmpeg concat demuxer. Razón: 10s es el sweet-spot del plan Standard de Kling (1 crédito/gen), y el formato fijo simplifica el budget tracking (1 cap = 1 crédito/día + buffer reruns).

31. **Intro = drawtext dinámico ffmpeg; outro = mp4 fijo en R2:** intro genera 2s sobre un fondo PNG fijo con `drawtext text=winner_display_name`. Outro 2s es un mp4 pre-rendered con CTA fijo, subido una sola vez a R2 (`static/outro.mp4`). Razón: cero costo de generación, parametrizable, no consume créditos Kling; el outro fijo da estética uniforme.

32. **Cadena de degradación 4 niveles:** Kling I2V (primary) → T2V HF (fallback 1) → T2V Pollinations (fallback 2) → T2I del módulo 009 (degradación final, cómic legacy). El cliente PWA renderiza según `manifest_kind` (`video_mp4` vs `comic_panels`). Razón: garantizar que el capítulo siempre se entregue (nunca `FAILED`) preservando inversión previa en T2V (Ronda 6 #25).

33. **Budget tracking + kill-switch:** tabla `kling_usage_month (year_month PK, credits_used INT, credits_max INT)`. Pre-check antes de cada `KlingI2VProvider.generate()`; si quedan < 20% del cap → salta a fallback. Cap configurable via `KLING_BUDGET_CREDITS_MAX`. Razón: contención dura del costo paid; sin esto un bug en reruns puede agotar plan completo en 1 día.

34. **Estructura del cambio de spec (qué se crea, qué se delta, qué no se toca):**
    1. **Nuevo módulo** `specs/013-characters-catalog/` — 8 archivos canónicos del Spec Kit.
    2. **Deltas:**
       - `specs/005-twists-submission/` — FK `character_id` NOT NULL en proposals + migration.
       - `specs/012-video-providers/` — nueva ABC `ImageToVideoProvider`, `KlingI2VProvider` activo, budget table.
       - `specs/008-generation-pipeline/` — `scriptwriter_response_v3` (1 escena), `clip_pipeline.py` I2V, `stitch_pipeline.py` reescrito a 14s.
       - `specs/010-pwa-client/` — `CharacterPicker.svelte` + form change.
    3. **Sin cambios:** módulo 009 (T2I), 011 (web-push), 003 (FSM), 007 (voting), 001-004.
    4. **Orden de spec-out:** 013 → delta 005 → delta 012 → delta 008 → delta 010 (productor antes que consumidor, principio Ronda 5 #21 y Ronda 6 #27.4).
    5. **Numeración del módulo:** se elige 013 (siguiente correlativo), consistente con Ronda 6 #27.5.

### Apéndice B — Próximos artefactos del Spec Kit a producir

- `specs/plan.md` — desglose de fases de implementación con criterios de "done" por fase.
- `specs/tasks.md` — work-breakdown granular para PRs.
- `specs/constitution.md` — principios de proyecto (zero-cost, idempotencia, transparencia con el usuario).
- `prompts/director_v1.txt`, `prompts/scriptwriter_v1.txt` — versionados en repo.
- ADRs (Architecture Decision Records) por decisión no-trivial: ADR-001 `FastAPI vs Node`, ADR-002 `GitHub Actions cron vs APScheduler`, ADR-003 `Tiebreak determinístico`.
- `specs/012-video-providers/` — spec-out del nuevo módulo de proveedores T2V (pivote Ronda 6, 2026-06-16). 8 archivos canónicos espejo estructural de `009-image-providers`. Bloquea el delta de `008-generation-pipeline`.
- `specs/013-characters-catalog/` — spec-out del nuevo módulo de catálogo de personajes (Ronda 7, 2026-06-24). 8 archivos canónicos. Bloquea los deltas de 005, 012, 008 y 010.
- `specs/005-twists-submission/` delta — FK `character_id` NOT NULL + migration strategy.
- `specs/012-video-providers/` delta — `ImageToVideoProvider` ABC + `KlingI2VProvider` real + budget table.
- `specs/008-generation-pipeline/` delta — `scriptwriter_response_v3`, `clip_pipeline.py` I2V, `stitch_pipeline.py` 14s.
- `specs/010-pwa-client/` delta — `CharacterPicker.svelte` + form.
- `docs/adr/0008-i2v-kling-character-catalog.md` — ADR formal del pivote.

---

*Fin del documento.*
