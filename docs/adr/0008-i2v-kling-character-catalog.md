# ADR-0008 — Pivote a Image-to-Video con catálogo de personajes (Ronda 7)

**Status**: Proposed
**Date**: 2026-06-24
**Deciders**: Dilan Perea (PO + lead dev)
**Links**: SDD §Apéndice A decisiones #28-#34, módulo 012, módulo 013 (nuevo), deltas 005/008/010, Gate 1 / Gate 4

---

## Context

El módulo 012 (Ronda 6, 2026-06-16) implementó un pipeline **Text-to-Video (T2V)** con HF LTX-Video → Pollinations → T2I como degradación final. Tras evaluar samples de T2V free, el PO identificó dos problemas operativos serios:

1. **Deformación de rostros**: los providers T2V free generan caras inconsistentes entre clips de la misma escena. Para una serie donde el "personaje principal" del capítulo es central a la narrativa, esto rompe el contrato emocional con el usuario.
2. **Inconsistencia entre clips**: 4-6 clips T2V de 5s c/u con prompts distintos producen estética, paleta y rostros distintos. El stitch ffmpeg + edge-tts no compensa la disonancia.

El estado-del-arte 2026-Q2 en consistencia de rostros se logra con **Image-to-Video (I2V)**: la red toma una imagen-semilla (rostro/personaje) + un prompt de movimiento, y el output preserva la identidad facial. Kling AI ofrece I2V en plan paid accesible (~USD 4.66/mes anual, plan Standard).

## Decision

Pivote arquitectónico: la primary chain pasa a **I2V con Kling**, con catálogo de personajes hardcoded del que el usuario debe elegir uno al proponer un twist.

1. **Selección obligatoria de personaje** — cada `twist` requiere `character_id` FK a una nueva tabla `characters`. UI con carrusel; form inválido sin selección.
2. **I2V Kling como primary** — nueva ABC `ImageToVideoProvider` con signature `(image_url, prompt, duration_s)`. `KlingI2VProvider` (hoy stub) se promueve a primary.
3. **Composición fija 14s** — intro 2s (drawtext con winner_name) + Kling 10s + outro 2s (mp4 fijo en R2). Scriptwriter pasa de 4-6 clips a 1 escena.
4. **Cadena de degradación 4 niveles** — Kling I2V → T2V HF → T2V Pollinations → T2I del módulo 009.
5. **Budget tracking** — tabla `kling_usage_month`. Pre-check + kill-switch si quedan < 20% del cap.

## Rationale

1. Consistencia de rostros es el driver primario; I2V resuelve la identidad visual fijando una foto-semilla.
2. Gate 1 (USD 0/mo) se relaja a "USD ≤ 5/mo" en MVP paid — formaliza la rampa a paid prevista en Ronda 6 #26.
3. Gate 4 preservado: providers externos solo se acceden vía ABCs.
4. Pivote reversible — T2V no se elimina, queda como fallback. Switch via `chain_env`.
5. Composición fija = predicibilidad de costo (1 crédito Kling/día + buffer reruns explícito).

## Boundary

- T2I del módulo 009 sigue siendo fallback final; contrato sin cambios.
- Intro overlay dinámico (drawtext) sólo aplica al nombre del ganador. Otros overlays requieren nuevo ADR.
- Catálogo hardcoded en seed Alembic. Admin UI para gestión queda para v0.2 (fuera de scope).
- 14s es composición fija para MVP; variaciones futuras requieren ADR.

## Consequences

- Nuevo módulo `specs/013-characters-catalog/` (8 archivos canónicos).
- Deltas: 005 (`character_id` NOT NULL + migration), 012 (I2V ABC + Kling activo + budget), 008 (scriptwriter v3 + stitch 14s), 010 (CharacterPicker UI).
- Migración Alembic: tablas `characters`, `kling_usage_month`; FK en `proposals`.
- Env nuevos: `KLING_API_KEY`, `KLING_PLAN_TIER`, `KLING_BUDGET_CREDITS_MAX`, `GENERATION_INTRO_BG_URL`, `GENERATION_OUTRO_URL`.
- R2 layout: `static/characters/<slug>.webp`, `static/outro.mp4`, `static/intro_bg.png`.
- Orden de spec-out: 013 → delta 005 → delta 012 → delta 008 → delta 010 (productor antes que consumidor; principio Ronda 5 #21 + Ronda 6 #27.4).

## TBDs (resolver en `research.md` de cada spec-out)

- **Plan Kling exacto + cap créditos/mes** → `specs/012-video-providers/research.md`.
- **Lista 8-12 personajes seed** → `specs/013-characters-catalog/research.md`.
- **Migration strategy para proposals en prod** → `specs/005-twists-submission/research.md` (depende de estado de tabla en prod).
- **Aspect ratio del foto del personaje** → `specs/013-characters-catalog/research.md` (recomendación inicial 1:1).
