# Tier-1 Strategy Admission

> **Status:** Living reference. Written 2026-06-12.
> **Author:** Önder Akkaya (quant / math lane).
> **Audience:** Anyone deciding whether a strategy belongs in the
> Archimedes-Verified (Tier 1) library, and anyone auditing why a given strategy
> passed or failed.
> **Canonical sources this doc must stay consistent with:**
> [`../specs/selection-bias-corrections-spec.md`](../specs/selection-bias-corrections-spec.md)
> (the frozen control contract) and the live implementation
> [`../../backend/archimedes/services/rigor_evaluator.py`](../../backend/archimedes/services/rigor_evaluator.py)
> (`RigorGateResult.passes_all`). Where this doc and those drift, **the spec and the
> code win** — the thresholds below are transcribed from `passes_all`, not invented.

Tier 1 ("Archimedes Verified 🏆") strategies get full agent autonomy and are
eligible for live vault deployment. The bar to enter is the **four-primitive
admission gate**. This doc states each control's threshold, the promotion flow, the
principled exceptions, and what monitoring continues *after* admission.

---

## The four controls and their thresholds

A strategy is admitted only when **all four** controls pass simultaneously. These
are exactly the conditions checked in `RigorGateResult.passes_all`:

| # | Control | Function | Threshold (the literal gate) |
|---|---|---|---|
| 1 | Deflated Sharpe Ratio | `compute_dsr` | `dsr_p_value ≥ 0.90` (and not `None`) — recalibrated from 0.95 in PR #901 |
| 2 | Probability of Backtest Overfitting | `compute_pbo` | `pbo_score < 0.5` (and not `None`) |
| 3a | Walk-forward OOS Sharpe — absolute floor | `compute_oos_sharpe` | `oos_sharpe > 0` (and not `None`) |
| 3b | Walk-forward OOS Sharpe — the cliff | `compute_oos_sharpe` | `oos_sharpe / in_sample_sharpe ≥ 0.5` |
| 3c | CPCV path stability (when computed) | `compute_cpcv_oos_sharpe` | `cpcv_positive_fraction ≥ 0.5` |
| 4 | Look-ahead static audit | `look_ahead_audit` | `look_ahead_passed == True` |

Notes on each threshold, with the *why* behind the number:

### 1. DSR p-value ≥ 0.90

The Deflated Sharpe Ratio (Bailey & López de Prado 2014) returns a probability that
the true Sharpe is positive *after* deflating for multiple testing and
non-normality. The bar was originally the conventional 95%-confidence threshold and was
**recalibrated to `0.90` in PR #901**: admission requires 90% confidence that the Sharpe
is not a selection-and-luck artifact. When `num_trials = 1` no deflation is applied (there was no selection);
the orchestrator passes `N = len(strategy_library)` so the correction is real, and
the effective-N correction (`average_correlation`) prevents a correlated parameter
sweep from being over-penalized as `N` independent tests. See
[`methodology.md`](methodology.md) §1 for the full formula.

### 2. PBO < 0.5

The Probability of Backtest Overfitting (Bailey, Borwein, López de Prado & Zhu
2014) is the CSCV-estimated fraction of IS/OOS splits in which the in-sample winner
underperforms the OOS median. **`PBO ≥ 0.5` means the in-sample-optimal strategy is
expected to underperform the median strategy out-of-sample** — the strategy (more
precisely, the *library's selection procedure*) fails the gate. This matches the
spec exactly: "A `pbo_score >= 0.5` means the in-sample-optimal strategy is expected
to underperform the median strategy out-of-sample — the strategy fails the rigor
gate." PBO is library-level: one value per analytics-engine run, recomputed when the
library changes.

### 3. Walk-forward OOS Sharpe — floor + cliff

Two sub-checks. The **absolute floor** (`oos_sharpe > 0`) means a negative
out-of-sample Sharpe can never pass, no matter how strong the in-sample result. The
**cliff check** (`oos_sharpe / in_sample_sharpe ≥ 0.5`) requires the out-of-sample
edge to retain at least half the in-sample edge — equivalently, **the OOS-to-IS
Sharpe degradation must be no worse than ~50%**. This is consistent with the
Bailey–López de Prado finding that overfit strategies cliff hard out-of-sample; a
≥50%-retained edge is the line between "degraded but real" and "memorized." The IS
Sharpe is computed on the first-70% slice only (see `run_rigor_gate`), so the ratio
cannot be inflated by leaking OOS data into the denominator. When a real
combinatorial OOS matrix is available, the CPCV path-stability check
(`cpcv_positive_fraction ≥ 0.5`) is an additional requirement; until then it is
honestly reported as `MISSING` and does not silently pass.

### 4. Look-ahead audit PASS

The static AST audit (`look_ahead_audit`) must return `passed = True` (zero
warnings). Any flagged forward-data access — negative pandas shift, positive feed
index, forecast-named call, or an ambiguous negative subscript — blocks admission
until a reviewer resolves it. A strategy that cannot be parsed also fails (a
`SyntaxError` returns `passed = False`).

> **Cross-reference to `rigor-methods.md`.** The judge-facing summary in
> [`../rigor-methods.md`](../rigor-methods.md) lists a fourth row "Total trades in
> backtest ≥ 10 (avoid sparse-trade illusions)." That is an *analytics-engine
> data-sufficiency* precondition applied upstream of the four statistical gates
> here — a strategy with too few trades cannot produce a meaningful return series in
> the first place. The four controls above are the statistical gate enforced in
> `RigorGateResult.passes_all`; the trade-count minimum is the data-quality
> prerequisite that must hold before those controls are even computed.

---

## The CANDIDATE → VALIDATED promotion flow

Every strategy carries a status. Promotion is gated; demotion is automatic on
re-evaluation failure.

```
                 generate / ingest
                        │
                        ▼
                  ┌───────────┐
                  │ CANDIDATE │   ← admitted to the library, NOT yet trusted
                  └─────┬─────┘      for live deployment or full agent autonomy
                        │
          run_rigor_gate(strategy_id, daily_returns,
            num_trials=len(library), pbo_scores=…,
            strategy_code=…, average_correlation=…)
                        │
            ┌───────────┴────────────┐
            │  RigorGateResult         │
            │  .passes_all == True?    │
            └───────────┬────────────┘
                 yes ▼          ▼ no
            ┌───────────┐   stays CANDIDATE
            │ VALIDATED │   (gate_details shows exactly which
            └───────────┘    control failed: FAIL / MISSING)
```

Mechanics:

1. **A strategy enters as `CANDIDATE`.** It is in the library, its numbers are
   visible on its passport, but it is *not* eligible for live deployment or full
   agent autonomy.
2. **The gate runs via `run_rigor_gate(...)`**, which orchestrates all four controls
   and returns a `RigorGateResult`. The caller passes
   `num_trials = len(strategy_library)`, the pre-computed library-level
   `pbo_scores`, the strategy source for the look-ahead audit, and the library's
   `average_correlation` for the DSR effective-N correction.
3. **Promotion to `VALIDATED` requires `passes_all == True`.** Every gate must pass.
   If any returns `None` (insufficient/degenerate data) or fails its threshold, the
   strategy stays `CANDIDATE`.
4. **Transparency, not a black box.** `RigorGateResult.gate_details` renders each
   control as `PASS (p=0.97)` / `FAIL (PBO=0.61, need < 0.5)` / `MISSING`. The UI
   shows this for *candidates and validated strategies alike* — a failing strategy
   shows exactly which gate it tripped. This is the design intent: rigor as
   transparency, not as a hidden score. Paper-claim deltas are surfaced alongside,
   never collapsed into an aggregate.
5. **Re-evaluation can demote.** Because PBO is library-level, adding or removing a
   strategy can change every member's PBO; a re-run that pushes a previously-passing
   strategy's PBO `≥ 0.5` (or any other gate below threshold) returns it to
   `CANDIDATE`. Validation is a *standing* property, not a one-time stamp.

How many of the library's strategies pass all four gates is deliberately not written
down here — the live rigor gate is the only authority on which strategies currently pass; see the PASS/CANDIDATE badges in the app and `backend/archimedes/services/live_rigor_gate.py`.
The rest remain honest CANDIDATEs with their failing gate shown openly. (This
paragraph previously named Faber 2007 as a passing strategy, contradicting
[`../analysis/faber-dsr-finding.md`](../analysis/faber-dsr-finding.md) and
[`../rigor-methods.md`](../rigor-methods.md), which both record it as failing.)

---

## Principled exceptions

The gate is a hard filter by default. But two situations warrant a *documented,
reviewer-approved* exception — never a silent threshold weakening (weakening
thresholds is an explicit anti-goal).

### A. Diversification benefit vs. a marginally lower DSR

A strategy that *just* misses the `0.90` DSR bar but is **genuinely
decorrelated** from the rest of the validated set can be more valuable to the
portfolio than a higher-DSR strategy that duplicates an existing bet. The portfolio
math is the justification: adding a low-correlation sleeve lowers portfolio variance
(its marginal contribution to variance, `kelly_risk_decomposition`, is small) and
raises the diversification ratio, even at a modestly lower standalone Sharpe.

This ties directly to the **fusion-quality** concept: the library is judged not as a
bag of independent strategies but as a *constructable portfolio*. A candidate's value
includes its incremental diversification, measurable via
`compute_average_pairwise_correlation(...)` against the validated set. When a
reviewer grants this exception, two things are mandatory: (1) the decorrelation must
be *real and measured* (low `ρ̄` against the validated set, not asserted), and (2)
the exception is recorded on the passport so the lower DSR and the diversification
rationale are both visible. The DSR's own effective-N machinery already encodes part
of this logic — correlated trials are penalized harder, decorrelated ones less.

> An exception is a documented portfolio-construction decision, not a relaxation of
> the statistic. The DSR number shown does not change; what changes is the *admission
> decision*, with the reason attached.

### B. CPCV / data-sufficiency `MISSING` on a short but sound series

When a strategy's series is too short to form a combinatorial OOS matrix, the CPCV
check reports `MISSING` rather than failing. The four core controls (DSR, PBO,
single-holdout OOS, look-ahead) can still all pass. Promotion is permissible on the
core four, with the `MISSING` CPCV explicitly noted — and the strategy is flagged for
CPCV re-evaluation once more history accrues. This is *not* a weakened threshold; it
is honest reporting of an uncomputable check, with a follow-up obligation.

Both exceptions share a rule: **the number is never altered, the decision is
documented, and the caveat is visible on the passport.**

---

## Post-admission monitoring

Admission is the beginning of trust, not the end. A VALIDATED strategy is monitored
on three axes:

### 1. Live-vs-backtest tracking

The whole point of the OOS gate is to predict live behavior; we then *check that
prediction*. Live realized returns are compared against the backtest's expected
distribution. The natural tolerance is the Sharpe confidence band from
`compute_sharpe_ci(...)` (Lo 2002): if live Sharpe drifts persistently outside the
backtest's CI, the edge is decaying — consistent with McLean & Pontiff (2016)'s
finding that published predictors lose ~26% of their return out-of-sample and ~58%
post-publication. A strategy whose live performance falls materially below its
backtest band is a re-evaluation (and possible demotion) trigger.

### 2. Regime drift

Each strategy carries a `REGIME_TAG` (`bull` / `bear` / `regime_neutral`). A
strategy validated largely in one regime is at risk when the regime changes. The
**regime-conditional γ multiplier** (`REGIME_GAMMA_MULTIPLIER`, Ang & Bekaert 2002)
is the live defense: in `risk_off`/`crisis` the optimizer's effective risk aversion
rises (2×/4×), pulling allocations toward minimum-variance so a single-regime
strategy is not sized as if its favorable regime will persist. Monitoring watches for
the regime detector flipping and for the strategy's live Sharpe degrading
specifically when its tagged regime ends.

### 3. Library re-coupling (PBO recompute)

Because PBO is library-level, the validated set must be re-evaluated whenever the
library changes. Adding a new strategy that is highly correlated with the existing
set can raise PBO across the board (and lower the DSR effective-N benefit); removing
a strategy can change every neighbor's verdict. The discipline: **recompute
`compute_pbo(...)` and `compute_average_pairwise_correlation(...)` on every library
mutation**, and re-run `run_rigor_gate(...)` for affected members. A strategy that
no longer passes returns to `CANDIDATE` automatically.

---

## Summary

- Admission = all four controls pass in `RigorGateResult.passes_all`:
  DSR `p ≥ 0.90`, PBO `< 0.5`, OOS Sharpe `> 0` and OOS/IS `≥ 0.5`
  (plus CPCV `positive_fraction ≥ 0.5` when computable), look-ahead `PASS`.
- Promotion is `CANDIDATE → VALIDATED`; failures stay `CANDIDATE` with the failing
  gate shown openly; re-evaluation can demote.
- Exceptions are documented portfolio-construction decisions (genuine
  diversification benefit; `MISSING` uncomputable checks) — never silent threshold
  weakening.
- Monitoring continues post-admission: live-vs-backtest tracking against the Sharpe
  CI, regime drift, and PBO recompute on every library change.
