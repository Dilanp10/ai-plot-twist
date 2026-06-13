# Prep 006 — claves y secrets para cerrar el módulo

Pasos exactos para desbloquear las 6 tasks pendientes del módulo
**006 directors-filter** (T-002, T-003, T-010, T-013, T-014, T-015).

> Cronograma: lo podés correr hoy 2026-06-13 antes de las 21:00 UTC
> (cierre del 006 el mismo día) o mañana 2026-06-14 con calma.
> El path es el mismo.

---

## 1. Sacar `GEMINI_API_KEY` (Google AI Studio)

URL: https://aistudio.google.com/apikey

1. Login con cuenta Google personal.
2. Botón **"Get API key"** → **"Create API key in new project"**.
3. Nombre del proyecto: `aiplottwist-prod` (o equivalente).
4. Copiar la key — empieza con `AIza...` (~40 chars).

**Tier**: free. Limit 15 RPM, 1500 RPD (FR-006). Suficiente para
1 filter run por día (≤ 25 twists × 1 batch).

---

## 2. Sacar `GITHUB_MODELS_TOKEN`

URL: https://github.com/settings/tokens

GitHub Models usa **classic personal access tokens** (no fine-grained
todavía a 2026-06).

1. **Generate new token (classic)**.
2. Note: `aiplottwist GitHub Models access`.
3. Expiration: 90 days (o "no expiration" si te bancás re-rotar).
4. Scopes: **NINGUNO marcado** — los tokens classic sin scopes alcanzan
   para GitHub Models (es endpoint público a la `models.inference.ai.azure.com`).
5. **Generate token** → copiar (empieza con `ghp_...`, ~40 chars).

**Tier**: free personal use. Buena fallback para Gemini.

---

## 3. Setear en Fly secrets (runtime API)

Desde el repo root, **una sola llamada** (Fly trigger 1 deploy con
ambos):

```sh
fly secrets set \
  GEMINI_API_KEY='AIza...' \
  GITHUB_MODELS_TOKEN='ghp_...' \
  --app ai-plot-twist
```

Verificar que se setearon (no muestra valores, solo digest):

```sh
fly secrets list --app ai-plot-twist
# debería listar GEMINI_API_KEY y GITHUB_MODELS_TOKEN con timestamp reciente
```

Fly hace **rolling deploy automático** después del `secrets set`.
Esperar ~30s y confirmar que healthz sigue 200:

```sh
curl -sI https://ai-plot-twist.fly.dev/healthz | head -1
# HTTP/2 200
```

---

## 4. Setear en GitHub Actions secrets (CI live smokes T-013/T-014)

URL: https://github.com/Dilanp10/ai-plot-twist/settings/secrets/actions

Sumar **dos repository secrets**:

| Name | Value |
|---|---|
| `GEMINI_API_KEY` | mismo valor que Fly |
| `GITHUB_MODELS_TOKEN` | mismo valor que Fly |

> **Importante**: el `GITHUB_MODELS_TOKEN` es un PAT distinto del
> `GITHUB_TOKEN` autoinjectado por Actions. Ponelo como secret aparte.

---

## 5. Code path para cerrar 006

Orden de implementación (cada uno con su propio commit):

| # | Task | Archivos | Tests |
|---|---|---|---|
| 1 | **T-002** Gemini provider | `apps/api/app/providers/llm/gemini.py` | `tests/unit/test_gemini_provider.py` (SDK mockeado) |
| 2 | **T-003** GH Models provider | `apps/api/app/providers/llm/github_models.py` | `tests/unit/test_github_models_provider.py` (openai SDK mockeado) |
| 3 | **T-010** DI wire | `apps/api/app/main.py` (lifespan o create_app: setear `app.state.director_router` con `LLMProviderRouter([GeminiProvider(...), GitHubModelsProvider(...)])` cuando ambas keys están presentes; fallback a `None` si falta alguna) + `tests/integration/test_di_registration.py` | side-effects registry `register("director_filter", build_director_filter(...))` |
| 4 | Deploy | `fly deploy --config infra/fly.toml --remote-only` | curl `/healthz` + curl `/api/v1/internal/health/cycle` |
| 5 | **T-013** live Gemini smoke | `tests/live/test_gemini_smoke.py` + `.github/workflows/live-llm-smoke.yml` (cron `0 2 * * *`) | `@pytest.mark.live` |
| 6 | **T-014** live GH Models smoke | `tests/live/test_github_models_smoke.py` | idem |
| 7 | **T-015** observar prod | esperar al próximo tick-18-vote (21:00 UTC); revisar logs Fly por `llm_batch`, `filter_completed`; edit `specs/006-directors-filter/quickstart.md` con "Verified live run" block; flip `specs/README.md:20` de `spec-done → done` |

---

## 6. Riesgo a evitar

**No deployar T-010 sin antes setear las secrets en Fly.** Si el filter
real se registra como side-effect pero no hay keys → tick-18-vote
spawnea `run_director_filter` → router llama Gemini → `LLMProviderError`
("auth failed") → bubble → `safe_side_effect` transiciona el cycle a
`FAILED`. Es exactamente lo que pasó hoy con el ESTRENO.

**Orden seguro**:
1. `fly secrets set ...` PRIMERO (rolling deploy se gatilla solo, healthz
   sigue 200 porque T-010 todavía no está en el código).
2. Implementar T-002 + T-003 + T-010 localmente.
3. Correr full test suite (mypy strict + ruff + 411 tests).
4. `fly deploy` cuando todo verde.
5. Verificar `/internal/health/cycle` muestra `current_state` correcto.
6. Esperar al próximo tick.

---

## 7. Comandos a la mano

```sh
# Health rápido prod
curl -s https://ai-plot-twist.fly.dev/api/v1/internal/health/cycle | python -m json.tool

# Logs live durante el filter run
fly logs --app ai-plot-twist | grep -E 'llm_batch|filter_completed|llm_provider_failover'

# Manual replay si algo se rompe (T-011 ya deployado pero get_director_router devuelve 503 hasta T-010)
ADMIN_TOKEN='...'  # leer de fly secrets list local o pedirlo al PO
python -m app.scripts.rerun_filter \
  --chapter-id 216f87b5-6457-439f-8832-a9df7bba6b1e \
  --api-url https://ai-plot-twist.fly.dev \
  --admin-token "$ADMIN_TOKEN"

# Trigger manual tick-18-vote (si querés forzar antes de las 21:00 UTC)
gh workflow run tick-18-vote.yml --ref main
```

---

## 8. Done-when del módulo 006

Cuando se cumplan los 3 ítems:

- [ ] 15/15 tasks merged (todas las T-001..T-015 con commits `feat(006): T-XXX — ...`)
- [ ] Production filter run real con verdicts del LLM (≥1 batch logueado con
      `provider="gemini"` o `provider="github_models"` y latency real)
- [ ] `specs/README.md` línea 20: `006 directors-filter` status `done`
