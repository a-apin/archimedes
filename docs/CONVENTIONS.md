# Documentation conventions

> **status:** current
> **owner:** Dan Browne
> **updated:** 2026-07-28
> **superseded-by:** —

How to decide where a new doc goes, what to call it, what to put at the top of it, and when
to retire it. If you are adding a doc, read this first — a doc filed in the wrong place is
harder to find than one that was never written.

## 1. Where a new doc goes — first match wins

Walk the list top to bottom and stop at the first row that fits.

| If the doc… | It goes in | Notes |
| --- | --- | --- |
| records a decision that closed off alternatives | `docs/adr/` | One decision per ADR. See § 5. |
| freezes an interface, schema, or protocol others build against | `docs/specs/` | Normative; changes need a superseding spec or an ADR. |
| is a step-by-step operational procedure someone will follow under pressure | `docs/runbooks/` | Commands, expected output, rollback. |
| is a reusable prompt or issue skeleton | `docs/prompts/` | Copy-paste artifacts for agent work. |
| reports a point-in-time investigation (audit, benchmark, cost model, analysis) | `docs/audits/`, `docs/benchmarks/`, `docs/cost-estimates/`, `docs/analysis/` | Dated artifacts; see the naming exception in § 2. |
| hands context from one contributor or session to the next | `docs/handovers/` | Expected to go stale; that is fine. |
| is a generated map, tree, or diagram | `docs/reference/`, `docs/diagrams/` | Regenerate rather than hand-edit. |
| describes how a live subsystem works today | `docs/` root | The default for durable, current architecture and product docs. |
| is history superseded by something else | `docs/archive/` | Must carry an `ARCHIVED` banner naming its replacement. |

Then **add a row to [`README.md`](README.md) in the same commit.** A doc not listed in the
index does not exist.

## 2. Naming

- `lower-kebab-case.md`. No spaces, no underscores, no capitals.
- The slug is a **stable identifier** — other docs, ADRs, and PR descriptions link to it.
  Renaming breaks links; prefer editing the doc in place, or add a `superseded-by` pointer
  and leave the old slug where it is.
- **No dates in filenames**, with one exception: point-in-time artifacts (audits,
  benchmarks, cost snapshots, handovers) that will never be updated in place may carry a
  `-YYYY-MM-DD` suffix, because their identity *is* the date.
- Name the subject, not the event: `aurora-postgres-alembic-datastore.md`, not
  `db-decision-final-v2.md`.

## 3. Front matter

Every doc under `docs/` opens with a blockquote block directly under its `# Title`:

```
> **status:** current | reference | draft | archived
> **owner:** <name>
> **updated:** YYYY-MM-DD
> **superseded-by:** <path> or —
```

- **status** — `current` (describes the live system), `reference` (durable background that
  does not track the system), `draft` (not yet trustworthy), `archived` (history; lives
  under `archive/`).
- **owner** — one named human. Not a team, not "the team".
- **updated** — the date the *content* was last checked against reality, not the date a
  typo was fixed.
- **superseded-by** — set this instead of deleting. Deleting a doc breaks every inbound
  link; superseding preserves the trail.

ADRs use the richer five-field variant documented in [`adr/README.md`](adr/README.md).

## 4. Staleness — the 60-day rule

A `current` doc whose `updated` date is **more than 60 days old** is presumed stale. When
you touch a doc past that line, do one of three things and record it in the front matter:

1. **Re-verify** — check the claims against the running system, bump `updated`.
2. **Demote** — change `status` to `reference` if it was never meant to track the system.
3. **Archive** — move it under `archive/` with an `ARCHIVED` banner and a `superseded-by`
   pointer.

Corollary: **an absent number beats a substituted one.** When a doc cannot state a value
honestly, write `—` or "unestablished" and say where the live source is; do not reach for the
nearest plausible figure. This is the documentation instance of a repo-wide principle —
*fail-soft is correct for optional configuration and wrong for anything a claim depends on*
([`architectural-principles.md`](architectural-principles.md) § fail-soft). A doc that
silently substitutes reads exactly like a doc that is right.

Corollary: **anything that decays fast does not belong in a doc at all.** Counts, contract
addresses, service inventories, test totals, and status live in the live source (`GET
/api/config/contracts`, `pytest --collect-only -q | tail -1`, `README.md` § Status) or in a
generated reference. A stale number in a doc is worse than no number, because readers act on
it without checking.

## 5. ADR lifecycle

One decision per ADR, named for the decision, never edited to say something different.

```
Proposed → Accepted → Superseded-by-<slug>
                   ↘ Rejected
```

- **Proposed** — written, not yet decided. The only status still open to argument.
- **Accepted** — decided and in force. Do not relitigate an `Accepted` ADR in a spec, a
  handover, or a code comment. Open a superseding ADR instead.
- **Rejected** — considered and declined. Kept so the option is not re-proposed blind.
- **Superseded-by-`<slug>`** — reversed or replaced. **The record stays**; the reasoning is
  still the history. Set `Superseded-by:` on the old ADR *and* `Supersedes:` on the new one
  in the same commit, so the chain reads in both directions. This directory identifies ADRs
  by slug rather than number, so the slug stands in for the number — e.g.
  `Superseded-by-debate-society-sole-generation-pipeline`.

An ADR is never deleted and never silently rewritten. If the decision changed, the change is
itself a decision and gets its own record.
