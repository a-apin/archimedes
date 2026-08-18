# ADR: `num_trials` is self-contained — a strategy's rigor depends only on that strategy

> **Audience:** Archimedes team
> **Status:** **Accepted, pending quant sign-off** (Önder Akkaya, portfolio math — see "Ratification" below)
> **Date:** 2026-07-09 (reversal shipped); spec addendum 2026-07-14; amended 2026-08-19 (fixture-leak class)
> **Owner:** Dan Browne (quant reviewer of record: Önder Akkaya)
> **Supersedes:** the `N + library_size` DSR convention from [#770](https://github.com/a-apin/archimedes/issues/770) / #811 / [#820](https://github.com/a-apin/archimedes/issues/820)
> **Superseded-by:** —
> **Question being decided:** What is the multiple-testing trial count `num_trials` that deflates a strategy's Sharpe ratio — does the size of the strategy library enter it?
> **Related:** [`backend/archimedes/agents/generation_pipeline.py:645-660`](../../backend/archimedes/agents/generation_pipeline.py) (`_society_num_trials`), [`backend/archimedes/api/selection_bias_routes.py:300-315`](../../backend/archimedes/api/selection_bias_routes.py), [`docs/specs/selection-bias-corrections-spec.md` § 1.3](../specs/selection-bias-corrections-spec.md) (addendum #1075), [`rigor-gate-unification.md`](rigor-gate-unification.md).

## TL;DR

**DSR deflation happens inside the debate society, and `num_trials` is the candidate pool
*that search actually considered* — never `N + library_size`.** A curated single-paper
implementation is graded at `num_trials = 1`, because there is no search of ours to deflate:
the paper's headline configuration is the only configuration we tried. **Cross-strategy
comparison belongs on the Leaderboard and the Marketplace, not in the per-strategy gate.**
Promoting a strategy into a bigger library must not retroactively change its Deflated
Sharpe.

## Context

The Deflated Sharpe Ratio (Bailey & López de Prado) corrects a Sharpe ratio for
multiple-testing: if you tried `num_trials` configurations and reported the best, the
expected maximum Sharpe under the null is inflated, and DSR deflates by exactly that
expectation-of-maximum term. The whole question is therefore **what counts as a trial**.

The answer drifted three times:

| When | Commit / PR | Convention |
|---|---|---|
| 2026-06-29 | `737b3117` ([#770](https://github.com/a-apin/archimedes/issues/770)) | `num_trials = n_candidates + library_size` on the society path — the library treated as a second selection layer |
| 2026-07-03 | `a47edde` ([#820](https://github.com/a-apin/archimedes/issues/820)) | the same additive formula unified across the live and fusion generation paths |
| — | `dfa8fc1` | DSR deflation made to fire on *every* rigor-gate path (and the PBO floor documented honestly) |
| **2026-07-09** | **`371a908` + `c8e0436`** | **reversed** — self-contained trial count, core sites then the remaining sites with green tests |
| 2026-07-14 | `b4481b8` (spec addendum #1075) | the spec addendum superseding the #770 formula, plus a verdict methodology marker |

The `+ library_size` convention has a defect that is easiest to see as a thought
experiment: **curating one more strategy into the library retroactively lowers the DSR of
every strategy already in it.** Nothing about an existing strategy changed — not its
returns, not its methodology, not the search that produced it — yet its p-value moved. A
rigor verdict that mutates when an unrelated strategy is added is not a property of the
strategy; it is a property of the catalogue. It also means a strategy's passport is not
reproducible from the strategy's own artifacts, which contradicts the externally-verifiable
provenance commitment in [`k1-generation-external-rigor-gate.md`](k1-generation-external-rigor-gate.md).

The second half of the problem is the curated library. A curated implementation of a
published paper is not the output of *our* search — we did not try 40 configurations and
report the best; we implemented the configuration the paper published. Deflating it by the
size of our library charges it for a search that never happened.

The unification of the rigor gate ([`rigor-gate-unification.md`](rigor-gate-unification.md))
made this decidable: with one gate path there is exactly one place the trial count is
sourced, and with one generation path
([`debate-society-sole-generation-pipeline.md`](debate-society-sole-generation-pipeline.md))
"the pool the search considered" is well-defined.

## Decision

**`num_trials` is self-contained: it counts only the search that produced *this* strategy.**

1. **Deflation happens inside the debate society**, where the search happens.
   `_society_num_trials(selection_pool_size)` returns `max(1, selection_pool_size)` —
   the N generated candidates the winner was chosen from, **not** `N + library_size`
   ([`generation_pipeline.py:645-660`](../../backend/archimedes/agents/generation_pipeline.py)).
   Candidates carry their own `dsr_num_trials` and are re-scored at the same count
   (`generation_pipeline.py:484,514-516`), so a re-computation reproduces the original
   verdict.
2. **Curated single-paper implementations are graded at `num_trials = 1`**
   ([`selection_bias_routes.py:~312`](../../backend/archimedes/api/selection_bias_routes.py)).
   With `num_trials = 1` the expectation-of-maximum term collapses and the strategy is
   judged purely on its own return series — which is the honest description of what a
   single-paper implementation is.
3. **Cross-strategy comparison belongs on the Leaderboard and the Marketplace, not in the
   per-strategy gate.** "Is this strategy statistically sound on its own evidence?" and "is
   this strategy better than the other 40?" are different questions with different
   audiences. Conflating them made the gate answer neither well. The gate answers the first;
   ranking answers the second.
4. **The convention is named in the payload.** Gate output carries
   `"num_trials_convention": "self_contained_v2"`
   ([`generation_pipeline.py:394-395`](../../backend/archimedes/agents/generation_pipeline.py)),
   so a passport records which convention produced its verdict and old and new verdicts are
   distinguishable rather than silently mixed.
5. **The effective-N correlation correction is unchanged.** Where several return series are
   compared, `N_eff = N / (1 + (N-1)ρ̄)` still relaxes the penalty for correlated
   strategies. Self-containment changes *what N is*, not the correlation adjustment.

## Consequences

### Positive
- **A strategy's rigor verdict is a property of the strategy.** It does not move when the
  library grows, so a passport is reproducible from the strategy's own artifacts — which is
  what "externally verifiable" was supposed to mean.
- **Curated strategies are graded for the search we actually did**, which is none.
- **The gate has one job.** Selection-bias soundness, per strategy. Ranking moved to where
  ranking belongs.
- **Verdicts are labelled by convention**, so the reversal is auditable rather than a silent
  re-grade.

### Negative / costs we accept — recorded honestly
- **This is a loosening, and it moves in the direction that flatters us.** `num_trials` can
  only shrink relative to formula (A), so DSR p-values can only improve. A reviewer is
  entitled to be suspicious of a rigor change that raises pass rates, and the code comments
  say so explicitly ("it raises curated pass rates by removing a cross-strategy penalty").
  The argument for it is a correctness argument, not a results argument — but the results
  moved, and that must not be buried.
- **No curated-library pass count is stated here or anywhere in the docs.** Three
  strategies previously reported as passing were found to be grading equity-like return
  series (~18.5% annual vol) that arrived through a data-feed fallback in the backtest
  loader. The pass rate on corrected data is not established. Any number in a document is a
  claim we cannot currently support; the live rigor gate is the only answer.
- **We give up a real signal.** There genuinely is a portfolio-level multiple-testing
  problem: selecting from a 40-strategy library *is* a selection event for the user doing
  the selecting. This decision moves that concern to the Leaderboard/Marketplace surface
  rather than solving it — and that surface does not yet implement a selection-bias
  correction of its own. **This is an open gap, not a solved one.**
- **Two conventions exist in the historical record.** Verdicts computed before 2026-07-09
  used formula (A); `num_trials_convention` distinguishes them, but any longitudinal
  comparison of pass rates across that boundary is invalid.

## Ratification — the sign-off has not happened

Two code comments name Önder Akkaya's portfolio-math sign-off as required:

- [`generation_pipeline.py:~657`](../../backend/archimedes/agents/generation_pipeline.py) —
  *"Needs Önder's sign-off (portfolio math) — it changes DSR p-values."*
- [`selection_bias_routes.py:~310`](../../backend/archimedes/api/selection_bias_routes.py) —
  *"REVERSES the prior library-size deflation (#770/#820) — needs Önder's sign-off; it
  raises curated pass rates by removing a cross-strategy penalty."*

**That sign-off has never been obtained.** The spec addendum (#1075) records the same
pending state for the earlier formula. The change is nevertheless live on every rigor-gate
path and has been since 2026-07-09, which is why this ADR is marked **Accepted, pending
quant sign-off** rather than simply Accepted: it is accepted because it is what the code
does, and pending because the review the code itself asks for has not happened.

**Action:** Önder Akkaya to review the self-contained convention (both the society
`n_candidates` count and the curated `num_trials = 1` case) and either sign off — at which
point this ADR becomes plain `Accepted` — or dispute it, at which point it needs a
superseding ADR, not a code patch. This is the single largest outstanding rigor risk in the
tree, because it is a loosening of a statistical control that the product's core claim
rests on.

## Alternatives considered
- **`N + library_size` (formula A, #770) — rejected**, and reversed. Makes a strategy's
  p-value a function of unrelated strategies; breaks reproducibility of a passport.
- **`num_trials = library_size` for the curated serving path — rejected** for the same
  reason: it charges a single-paper implementation for a search we did not run.
- **Deflate curated strategies by the paper's own reported configuration count — considered,
  not adopted.** Defensible in principle (the *authors'* search is a real multiple-testing
  event), but the count is rarely reported and would be a guess. `num_trials = 1` is the
  honest floor; the residual author-side selection bias is a known, unmodelled term.
- **Keep a portfolio-level correction inside the gate — rejected** as answering the wrong
  question at the wrong surface; see the open gap above.

## Amendment (2026-08-19): the fixture-leak class

The convention marker (Decision #4) protects verdicts computed *by the gate*. The
2026-08 test-suite/chaff audit found a failure class it does not protect against:
**surfaces that serve rigor values from the frozen strategy fixtures instead of a live
gate run.** The fixture fields (`Strategy.dsr_p_value`, `num_trials_in_selection`,
`pbo_score`, `deflated_sharpe_ratio`, `out_of_sample_sharpe`, and the
`passes_rigor_gate` boolean) are stored values the live gate never rewrites — most
predate the 2026-07-09 reversal, so a surface serving them next to a live badge
**silently mixes conventions**, exactly what the marker exists to prevent, without ever
touching the marker.

Live instance (audit finding M1, fixed in
[PR #1272](https://github.com/a-apin/archimedes/pull/1272)): `/api/strategies/advisor`
served five fixture statistics — including a fixture-era `num_trials_in_selection` —
with only the badge live-corrected, so its numbers could disagree with
`GET /api/selection-bias/gate` for the same strategy at the same moment. The sibling
finding (M2, same PR) was the *sentinel* variant: readers presenting the fail-closed
in-memory `passes_rigor_gate = False` (#821) to LLM prompts as if it were a verdict.

**Rule this amendment records:** a served rigor statistic or verdict must come from a
live `run_rigor_gate` computation (directly or via the honest memoized batch in
`rigor_cache` — a cache hit serves exactly what the miss would have computed). Fixture
fields may appear only as the explicitly-documented fallback when the live gate cannot
run for a strategy (#868 contract), and the fail-closed sentinel is never a verdict —
downstream consumers render "pending"/omission, not "fail". The leaderboard (#868), the
library badge (#821), the advisor, the portfolio LLM prompt, and vault chat (#1272) now
all comply; any new consumer of these fields starts from this rule.
