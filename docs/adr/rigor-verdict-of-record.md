# ADR: The passport is the rigor verdict of record

> **Audience:** Archimedes team
> **Status:** **Accepted**
> **Date:** 2026-09-01
> **Owner:** Dan Browne (quant reviewer of record: Önder Akkaya)
> **Supersedes:** the read-time-derivation premise of [#868](https://github.com/aprin-labs/archimedes/issues/868) — that a strategy's rigor verdict is computed when it is read. It does **not** supersede [#821](https://github.com/aprin-labs/archimedes/issues/821), whose principle survives tightened; see "What #821 said, and what still holds".
> **Superseded-by:** —
> **Question being decided:** When is a strategy's rigor verdict decided — every time somebody looks at it, or once, at backtest time?
> **Related:** [#1746](https://github.com/aprin-labs/archimedes/issues/1746), [#1747](https://github.com/aprin-labs/archimedes/issues/1747), [#1654](https://github.com/aprin-labs/archimedes/issues/1654) (board FDR, option 1), [#1760](https://github.com/aprin-labs/archimedes/issues/1760) / PR #1766 (backtests are frozen evidence), [`num-trials-self-containment.md`](num-trials-self-containment.md), [`rigor-gate-unification.md`](rigor-gate-unification.md), [`backend/archimedes/services/passport_loader.py`](../../backend/archimedes/services/passport_loader.py), [`backend/archimedes/services/rigor_gate_version.py`](../../backend/archimedes/services/rigor_gate_version.py).

## TL;DR

**Generation, backtesting and grading are one-time events.** A strategy is graded
ONCE, at backtest time, by the real gate. That verdict is persisted on the
strategy passport together with its inputs and its provenance, and **every
surface reads the stored verdict**. A re-grade is an explicit, versioned event —
never a silent overwrite, and never a recompute on read.

The stored verdict must be written by the **real gate**, never by a fixture and
never by the generation-time synthesis verdict, and its provenance
(`graded_at`, `gate_version`, `cohort_n`) is what proves it.

Board-level FDR stays outside this: it is a live **relational** signal on the
leaderboard, off the passport ([#1654](https://github.com/aprin-labs/archimedes/issues/1654) option 1).

## The problem this decides

`strategy_passports.passes_rigor_gate` was **mixed-vintage**. Two different
things wrote it, and which one won depended on whether a background step
happened to run:

| Writer | When | What it means |
|---|---|---|
| `generation_pipeline._persist_candidate` | at synthesis, before any backtest exists | the **fusion/debate** gate's opinion (`c.rigor_verdict["passing"]`) |
| `_refresh_passport_real_metrics` / `_backtest_and_persist` | after a real backtest, if it ran and did not throw | the **real** gate's verdict over the real return series |

Both write the same column. Nothing recorded which one you were looking at. Both
post-backtest paths swallow their exceptions, so "the fusion verdict is still
sitting there" was an invisible, common state.

On top of that, the read surfaces did not agree about where the answer even
lived:

- `GET /api/strategies/{id}` **derived** a four-state badge at read time from the
  stored aggregate plus a freshly-loaded return series.
- `GET /api/strategies/passports/{id}` served the raw row, with no
  `rigor_gate_status` key at all.
- `GET /api/strategies/generated` — the Library's Generated tab — never looked at
  `strategy_passports`. It served `StrategyRecord.status` and
  `StrategyRecord.rigor_verdict`, **both written from the same synthesis blob**,
  so the tab's own demotion rule ("status says live, the gate says fail") was
  structurally unreachable.

The user-visible result (#1747): twenty-one strategies read **"Live ✓"** in the
Library while their own passports read **"Reference only — gate failed"**, with
no shared field between the two answers.

## The decision

1. **The passport row is the verdict of record.** `rigor_gate_status`
   (`pass` | `fail` | `pending` | `degenerate`), `passes_rigor_gate`,
   `graded_at`, `gate_version` and `cohort_n` are the verdict; the rigor numbers
   the same run produced (DSR, DSR p, PBO, OOS Sharpe) sit beside it.

2. **One writer.** Only the post-backtest grade writes them, and it writes all
   five together. Structurally, not by convention:
   `ingest_passport` reads `passport.passes_rigor_gate` **nowhere**; the verdict
   arrives only as an explicit `rigor_verdict=RigorVerdictWrite(...)`, whose
   `passes` is a **derived property** (`status == "pass"`), so the boolean and
   the four-state cannot be set apart.

3. **`pending` is the honest default.** A row nobody has graded says `pending`
   with `passes_rigor_gate = false` — fail-closed on admission, honest on the
   surface. A refresh that carries no grade leaves an existing verdict alone: it
   neither erases nor silently rewrites one.

4. **Provenance is mandatory.** `RigorVerdictWrite` fills `graded_at` and
   `gate_version` in `__post_init__`, so a verdict without provenance cannot be
   constructed. `gate_version` is a digest of everything that can move a verdict
   without the strategy's own returns moving — the strictness ladder, the
   always-on floors, the DSR/rf convention, the pending boundary, and a
   hand-bumped code revision. Its module docstring lists what is in and, just as
   importantly, what is deliberately out (the git SHA; board FDR).

5. **Readers read.** No surface recomputes a verdict. `_passport_rigor_status`
   — the old read-time derivation — is retained off the request path as the
   oracle the migration's backfill rule is tested against, and as documentation
   of what the four states meant before they were stored.

6. **A re-grade is an event.** Re-grading calls the same single writer with a
   fresh `RigorVerdictWrite`, which rewrites all five fields and stamps a new
   `graded_at` / `gate_version`. There is no path that changes one of them alone.

## What #821 said, and what still holds

#821's rule was: **the badge must never be a stored value the gate never
derived** — no fixture booleans, no cached passes. That rule survives, tightened
rather than reversed. What #868 added on top — that the derivation should happen
*at read time* — is the part this ADR supersedes.

The distinction that makes both true at once: #821 forbids a verdict **no gate
produced**. It does not require the gate to run on every request. A verdict the
real gate produced, over the real persisted series, stamped with which gate
produced it, is exactly what #821 asked for — and a stamped, dated verdict is
*more* auditable than one recomputed invisibly on each read, because a recompute
that starts disagreeing with yesterday's answer leaves no trace that it did.

The tightening: #821's own escape hatch, the fail-closed `False` placeholder on
curated rows, was itself being served as a verdict by one surface. A placeholder
now has its own word — `pending` — so "the gate ran and it lost" and "no gate has
looked at this" can no longer render identically.

## Consequences

**Good.**
- One field, one meaning, one writer. The Library row, the detail route and the
  passport route serve the same four-state for the same id, by construction.
- A verdict is now dated and attributable. "Which gate said that?" has an answer.
- The passport list route stopped paying a whole-cohort `get_all_daily_returns`
  per page — a query that projected and deserialized every winning row's
  `artifact_json` on an unbounded route. That is a cost saving, but it is a
  consequence, not the motive.
- The generation-time fusion verdict is no longer demoted OR promoted: it stays
  on `StrategyRecord.rigor_verdict` as the **debate record** — what the synthesis
  gate thought, worth keeping precisely because the real gate can disagree.

**Costs, stated plainly.**
- **Staleness is now visible instead of accidental.** A stored verdict can be
  older than the gate that would be applied today. That is the trade: the old
  behaviour was not fresher, it was undated. `gate_version` is how a reader
  sees it, and PR-C is how it gets closed.
- **One state gets less precise for legacy rows, in the fail-closed direction.**
  The old read path could see a row's return series and report `degenerate` for a
  zero-variance one. The backfill migration cannot (the series lives inside
  `backtest_results.artifact_json`), so a legacy generated row that today reads
  "Unevaluable — flat returns" reads "Reference only — gate failed" until PR-C
  re-grades it. Both are non-pass; neither renders green. Going forward the
  writer stores `degenerate` as itself, so this is a one-time cost on existing
  rows only.
- **Curated strategies go from a false `fail` to an honest `pending`.** Every
  curated passport row's `passes_rigor_gate` is the #821 placeholder, not a gate
  result. After the migration those rows say "not yet graded", which is true, and
  the Library's curated tab keeps its live-gate badge (the curated detail route is
  unchanged in PR-A). Grading them for real is PR-B.
- **A row can be published with no verdict.** `pending` is a real, reachable,
  non-green state; product copy has to have something to say for it ("Not yet
  graded").

## The three-PR program

| PR | Scope | State |
|---|---|---|
| **PR-A** | Generated strategies: the columns, the migration + backfill, the single writer, every generated read surface, the UI pill, the ADR. | this PR |
| **PR-B** | Curated grading: replace the `passes_rigor_gate = False` placeholder in `strategy_provider.py` with a real stored grade, and retire the curated detail route's live computation. | next |
| **PR-C** | The explicit re-grade of existing rows: a versioned event that replaces every `gate_version = 'legacy-derived'` verdict (and every `pending` the backfill could not resolve) with a real gate run. | after B |

The order is deliberate. PR-A makes the column mean one thing; PR-B makes it mean
that thing for curated rows too; PR-C fills it in for history. Doing C before A
would re-grade rows into a column that still had two meanings.

## Alternatives considered

**Read-time overlay (recompute on every request).** The shape the triage
originally proposed: leave the column alone and overlay a live verdict on each
read. Rejected by the owner. It keeps the answer undated and unattributable, it
makes the badge a function of *when you looked* (cohort-dependent inputs like PBO
move with the rest of the library), and it puts a ~6-second cohort gate run
behind a free, unauthenticated, documented agent route. It also would not have
fixed the underlying defect — the column would still be mixed-vintage; it would
just have stopped being read.

**Write the live re-grade back to `StrategyRecord.status` / `rigor_verdict`.**
Rejected: that destroys the record of what the synthesis gate thought, which is
the one thing that blob is good for, and `status == "live"` has downstream
readers (`marketplace_service.trending`) that are not about rigor at all.

**Add a `verdict_source` enum instead of a `gate_version` digest.** A source
label ("fusion" | "live_gate") separates the two vintages but does not separate
two live-gate runs under different thresholds — which is the harder and more
persistent problem. `gate_version` answers both: a fusion verdict simply never
gets one, because it never becomes a verdict at all.
