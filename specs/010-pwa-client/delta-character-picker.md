# Delta v2 — CharacterPicker UI (Ronda 7 pivot)

**Applies to**: `specs/010-pwa-client/` | **Date**: 2026-06-24
**Triggered by**: SDD Ronda 7 (decisions #28-#34, ADR-0008). The proposal
form now requires the user to pick a character before submitting a twist.
**Read alongside**: original `specs/010-pwa-client/` artefacts;
`specs/013-characters-catalog/contracts/characters.openapi.yaml` (the
`GET /characters` schema); `specs/005-twists-submission/delta-i2v-character.md`
(the new request shape for `POST /twists/submit`).

---

## 1. What changes, what stays, what is new

### Stays untouched
- App shell, router, install prompts, service worker, Lighthouse CI —
  all untouched.
- The existing twist submission flow scaffolding from
  `specs/005-twists-submission/`'s PWA phase (`TwistModal.svelte`,
  `MyTwistsPanel.svelte`, `twist-api.ts`, `twist-store.ts`).
- A11y conventions (focus ring, keyboard nav, ARIA labels).
- Spanish UI strings convention.

### Changes (existing components modified)

- **`twist-api.ts`** — gain a typed wrapper:
  ```ts
  export async function listCharacters(): Promise<Character[]>;
  ```
  Calls `GET /characters` with the auth header. Honors the `ETag`/`If-None-Match`
  protocol: stores the last ETag in `sessionStorage` and replays it; on `304`
  returns the cached list.
- **`twist-store.ts`** — gain `selectedCharacterId: number | null` runes
  state and a `selectCharacter(id: number)` mutator. The store also
  caches the catalog itself (the response of `listCharacters`) for the
  session.
- **`TwistModal.svelte`** —
  - Renders `<CharacterPicker />` **above** the text input.
  - Disables the submit button if `selectedCharacterId === null`.
  - On submit, sends `{content, character_id, idempotency_key}` to
    `POST /twists`. Maps `422 invalid_character` to a Spanish toast
    `"Ese personaje no está disponible. Elegí otro."`.
- **`MyTwistsPanel.svelte`** — each row now shows the character's
  thumbnail + display_name (read from the joined `character` block
  returned by `GET /me/twists` per delta-i2v-character.md).

### New (added tasks)

- **T-015** — `CharacterPicker.svelte` component (see §3).
- **T-016** — `character-api.ts` typed client + `Character` TS type
  generated from the OpenAPI in module 013.
- **T-017** — Visual regression test (Playwright + Percy or equivalent
  pixel-diff): screenshots of `TwistModal` with and without a selected
  character; assert the picker renders.

---

## 2. New dependencies

- None. Pure Svelte 5 + Vite; no third-party carousel lib.
- A 1-line CSS-driven horizontal scroll-snap container is enough for
  10-12 cards. Mobile-first; the desktop fallback re-uses the same
  scroller.

---

## 3. `CharacterPicker.svelte` — component contract

### Props

```svelte
<script lang="ts">
  type Character = {
    id: number;
    slug: string;
    display_name: string;
    photo_url: string;
    aspect_ratio: '1:1' | '9:16' | '16:9';
  };

  let { characters, selected_id, on_select } = $props<{
    characters: Character[];
    selected_id: number | null;
    on_select: (id: number) => void;
  }>();
</script>
```

### Markup (sketch)

```svelte
<div class="character-picker" role="radiogroup" aria-label="Elegí un personaje">
  {#each characters as c}
    <button
      type="button"
      role="radio"
      aria-checked={selected_id === c.id}
      class="card"
      class:selected={selected_id === c.id}
      onclick={() => on_select(c.id)}
    >
      <img src={c.photo_url} alt={c.display_name} width="96" height="96" loading="lazy" />
      <span class="name">{c.display_name}</span>
    </button>
  {/each}
</div>
```

### Behavior

- **Scroll-snap** horizontal: each card snaps to the start; smooth
  scrolling is enabled.
- **Keyboard**: Left / Right arrows move focus between cards; Enter /
  Space selects. Tab cycles past the picker as a single composite
  control (radiogroup semantics).
- **Loading**: while `characters` is empty (await fetch), show 5
  placeholder skeleton cards (CSS-only, no extra component).
- **Empty catalog**: if `characters.length === 0` (misconfigured deploy),
  show a Spanish error block — *"No hay personajes disponibles. Avisale al
  admin."* — and keep the submit button disabled.
- **Image error**: if `<img>` fails to load (R2 down), show the
  `display_name` text card with a placeholder background. Selection still
  works.

### Styling notes

- Cards are 96×96 px on mobile (snap container scrolls 1 card per
  swipe), 128×128 on desktop ≥ 768 px.
- Selected card has a 3 px accent border + slight scale-up (1.03).
- Photos render `object-fit: cover` regardless of aspect_ratio (which is
  always `1:1` in MVP; the field is captured for future flexibility).

---

## 4. State flow

1. **Mount** — `TwistModal` calls `twistStore.ensureCatalogLoaded()` once
   per session. The store calls `listCharacters()`, caches the result
   plus the ETag.
2. **Pick** — `CharacterPicker.on_select` calls
   `twistStore.selectCharacter(id)`.
3. **Submit** — `TwistModal.handleSubmit` reads
   `twistStore.selectedCharacterId` and includes it in the POST body.
4. **Reset** — after a successful submission, `twistStore.reset()`
   clears `content` **and** `selectedCharacterId`.
5. **422 invalid_character** — the API client throws a typed error
   `TwistInvalidCharacterError`; `TwistModal` catches and re-fetches the
   catalog (the chosen character may have been disabled mid-session),
   then unselects the now-stale id and surfaces the toast.

---

## 5. Acceptance for "delta done"

- [ ] `character-api.ts` + `Character` TS type ship.
- [ ] `CharacterPicker.svelte` renders the catalog as a scroll-snap row;
      a11y radiogroup semantics; keyboard nav works.
- [ ] `TwistModal` cannot submit until a character is selected;
      visual + Playwright tests cover this.
- [ ] `MyTwistsPanel` shows character thumbnail + display_name per row.
- [ ] `twist-store.ts` caches the catalog for the session; ETag round-trip
      avoids duplicate downloads.
- [ ] Empty catalog state and image-error state both render gracefully.
- [ ] Lighthouse mobile score does **not** drop versus the baseline
      (the picker is CSS-only; no heavy imports).
- [ ] All visual regressions in T-017 are blessed by the PO.
- [ ] All a11y checks from the original module 010 (focus order, ARIA,
      contrast) still pass.
