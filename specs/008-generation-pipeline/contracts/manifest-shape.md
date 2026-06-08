# Contract: `chapters.manifest_json` Shape (v1.0)

**Module**: `008-generation-pipeline` | **Date**: 2026-06-07

This document defines the canonical shape of `chapters.manifest_json` written
by module 008 and read by module 004 (and by the PWA via 004).

For the JSON Schema of the scriptwriter's *raw* LLM output (which is a SUBSET
of the manifest), see [scriptwriter-response.schema.json](./scriptwriter-response.schema.json).

---

## Top-level keys (all required unless noted)

| Key | Type | Source | Read by |
|---|---|---|---|
| `schema_version` | `string` ("1.0") | Module 008 | future migrations |
| `panels` | `array[Panel]` | Module 008 (script + render) | Module 004 + PWA |
| `cliffhanger` | `string` | Module 008 (scriptwriter) | Module 004 + PWA |
| `next_cliffhanger_seed` | `string` | Module 008 (scriptwriter) | Module 008 (auto-continue tomorrow) |
| `winner_metadata` | `WinnerMetadata` | Module 008 (winner selector) | Module 004 (future "Por @user" attribution) |
| `generation_metadata` | `GenerationMetadata` | Module 008 (coordinator) | Ops / logs only — NOT exposed by module 004 |

## `Panel`

| Key | Type | Notes |
|---|---|---|
| `idx` | `int` 1..8 | Unique within array; contiguous. |
| `image_url` | `string` (https URL) | R2 public URL OR `PLACEHOLDER_IMAGE_URL` on failure. |
| `image_blurhash` | `string \| null` | Computed by module 008 from the image bytes; `null` for placeholders. |
| `tts_url` | `string \| null` | R2 URL or null (TTS disabled / failed). |
| `narration` | `string` | From scriptwriter, Spanish. |
| `mood` | `string` (enum) | From scriptwriter. |

## `WinnerMetadata`

| Key | Type | Notes |
|---|---|---|
| `winner_twist_id` | `uuid \| null` | null in auto-continue mode. |
| `winner_author_display_name` | `string \| null` | null in auto-continue mode. |
| `vote_count` | `int` | 0 in auto-continue. |
| `tiebreak` | `boolean` | True iff the picked winner shared `vote_count` with at least one other twist. |
| `runner_up_twist_id` | `uuid \| null` | Populated only when `tiebreak=true`. |

## `GenerationMetadata` (ops-only; NEVER exposed by module 004)

| Key | Type | Notes |
|---|---|---|
| `scriptwriter_model` | `string` | "gemini-2.0-flash" or fallback. |
| `scriptwriter_provider` | `string` | "gemini" \| "github_models". |
| `panel_provider_breakdown` | `object` | `{providerName: count}` for the chapter. |
| `tts_provider` | `string \| null` | "edge-tts" or null. |
| `started_at` | `string` (ISO) | Pipeline start. |
| `finished_at` | `string` (ISO) | Pipeline finish (success or degraded). |
| `duration_ms` | `int` | `finished_at − started_at`. |
| `degraded` | `boolean` | Mirrors `chapters.status='ready_degraded'`. |
| `degraded_reasons` | `array[string]` | Stable codes: `panel_N_render_failed`, `tts_*`, `deadline_exceeded`, `scriptwriter_retry`. |

---

## Module-04-exposed subset

When module 004's `Chapter` schema (contracts/chapters.yaml) maps:

| Module 004 field | manifest_json source |
|---|---|
| `chapter.title` | `chapters.title` (NOT inside manifest_json) |
| `chapter.synopsis` | `chapters.synopsis` (NOT inside manifest_json) |
| `chapter.panels[*]` | `manifest_json.panels[*]` (subset of keys) |
| `chapter.cliffhanger` | `manifest_json.cliffhanger` |

`winner_metadata` and `generation_metadata` are NOT exposed. Module 004's
serializer explicitly drops them.

---

## Stability and migration

- The contract above is `schema_version = "1.0"`.
- Adding a new optional top-level key bumps to `1.1` (minor; backward-compatible).
- Renaming or removing a key bumps to `2.0` (major; requires module 004
  changes).
- Reading older versions: module 004 ignores unknown keys; module 008 always
  writes the current schema. Old chapter rows may have older versions; they
  remain readable as long as the required keys are present.

---

## Example (full chapter manifest, v1.0)

```jsonc
{
  "schema_version": "1.0",
  "panels": [
    {
      "idx": 1,
      "image_url": "https://assets.aiplottwist.example/seasons/s01-el-tunel/9f3a3b5f-7e2c-4d4f-a1b2-c3d4e5f60718/1-a1b2c3d4.webp",
      "image_blurhash": "LKO2?V%2Tw=w]~RBVZRi};RPxuwH",
      "tts_url": "https://assets.aiplottwist.example/seasons/s01-el-tunel/9f3a3b5f-7e2c-4d4f-a1b2-c3d4e5f60718/1-tts-eeff0011.mp3",
      "narration": "El espejo crujió como hielo viejo…",
      "mood": "tense"
    },
    { /* ... panel 2 ... */ },
    { /* ... panel 3 ... */ }
  ],
  "cliffhanger": "Entonces escuchó la voz de su madre desde la cocina.",
  "next_cliffhanger_seed": "La madre del 1998 alterno está viva pero algo en su voz no es del todo humano.",
  "winner_metadata": {
    "winner_twist_id": "b1c2d3e4-1111-2222-3333-444455556666",
    "winner_author_display_name": "Lucía",
    "vote_count": 12,
    "tiebreak": false,
    "runner_up_twist_id": null
  },
  "generation_metadata": {
    "scriptwriter_model": "gemini-2.0-flash",
    "scriptwriter_provider": "gemini",
    "panel_provider_breakdown": {"pollinations": 2, "hf": 1, "placeholder": 0},
    "tts_provider": "edge-tts",
    "started_at": "2026-06-08T02:00:03Z",
    "finished_at": "2026-06-08T02:32:47Z",
    "duration_ms": 1964000,
    "degraded": false,
    "degraded_reasons": []
  }
}
```
