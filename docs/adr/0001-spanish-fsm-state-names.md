# ADR-0001 — Spanish FSM State Names

**Status**: Accepted  
**Date**: 2026-06-07  
**Deciders**: Dilan Perea (PO + lead dev)  
**Links**: Research R-009 (`specs/003-cycle-fsm/research.md#r-009`), Gate 6 (`.specify/memory/constitution.md`)

---

## Context

Gate 6 of the project constitution requires all identifiers in code (file names,
classes, functions, variables, DB columns) to be **English**, while all user-facing
strings are in **español rioplatense**.

The daily-cycle FSM (module 003) defines seven states:

| State | English translation |
|---|---|
| `PENDING_RELEASE` | pending release |
| `ESTRENO` | premiere / release day |
| `RECEPCION_IDEAS` | idea reception |
| `FILTERING` | filtering |
| `VOTACION` | voting |
| `GENERACION` | generation |
| `FAILED` | failed |

`FILTERING` and `FAILED` are already English; the remaining five are Spanish.
The question is whether the Spanish state names violate Gate 6.

## Decision

The FSM state names are **explicitly exempted** from Gate 6 as domain terms.

## Rationale

These states are the *ubiquitous language* of the product: the PO uses
`ESTRENO`, `VOTACION`, and `RECEPCION_IDEAS` in product conversations,
analytics dashboards, stakeholder reports, and Discord alerts. They map
directly to concepts in the product bible and community communications.

Translating them to English ("PREMIERE", "IDEA_COLLECTION", "VOTING",
"GENERATION") would create a permanent **translation tax** on every
conversation between product and engineering: every bug report, every
analytics query, every runbook would require a mental translation.

Domain-Driven Design (Eric Evans, *DDD*, 2003) identifies this as the
*ubiquitous language* principle: consistency between the spoken and written
word is more valuable than any single coding convention.

**Boundary — what this exception covers:**

- ✅ FSM state *values* stored in `cycles.state` and `state_transitions.to_state`
  (e.g. `"ESTRENO"`, `"RECEPCION_IDEAS"`)
- ✅ Literal constants used to compare against those values in Python
  (e.g. `state == "RECEPCION_IDEAS"`)
- ✅ Type aliases/Literals that enumerate the valid states

**Boundary — what this exception does NOT cover:**

- ❌ Field names, function names, variable names, log keys (remain English)
- ❌ Any new domain term not listed above (must go through Gate 6 or a new ADR)
- ❌ UI strings (always Spanish, governed by Gate 6 as usual)

So `cycle.state = "ESTRENO"` is correct; `ciclo.estado = "ESTRENO"` is not.

## Consequences

- Engineers reading the codebase see Spanish literals in state comparisons.
  This is intentional and should not be "fixed".
- New FSM states (if any) must go through the same ADR process before
  using a Spanish name in code.
- The Gate 6 checklist in the constitution carries a footnote pointing here.
