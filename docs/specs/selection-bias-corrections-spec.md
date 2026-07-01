# Selection-Bias Corrections — Implementation Spec

> **Date:** 2026-05-13 (Day 3)
> **Owner:** Önder (math + statistics)
> **Consumers:** Dan (strategy validation gate), Daniel R. (analytics-engine
> output), Daniel S. (passport UI), Chuan (orchestrator's rigor gate)
> **Status:** Draft — ready for Önder review and implementation
> **Prerequisite reading:** [`../agora_project_analysis.md`](../archive/agora_project_analysis.md)
> § 5.3, [`./strategy-passport-spec.md`](./strategy-passport-spec.md),
> [`../architectural-principles.md`](../architectural-principles.md)

## Why this exists

The strongest red-team critique of an LLM-driven strategy-extraction pipeline
is **multiple-testing inflation**. If we evaluate N candidate strategies on
historical data and pick the top K by backtest Sharpe, we are running an
N-way selection experiment without selection-bias control. McLean & Pontiff
(2016) showed published cross-sectional predictors lost ~26% of return
out-of-sample and ~58% post-publication; Bailey, Borwein, López de Prado &
Zhu (2014) demonstrated that under realistic multiple-testing conditions
the in-sample-optimal strategy frequently does not even dominate the median
out-of-sample. Either failure mode produces a "validated" strategy that
fails in production.

**Archimedes' wedge against the 96 other AI-portfolio submissions at the
last HackMoney is that we apply the textbook corrections.** This spec
defines the contract.

## What this spec covers

Three corrections, each populating specific fields on
[`backend/archimedes/models/backtest.py`](../../backend/archimedes/models/backtest.py)
`BacktestResult`:

1. **Deflated Sharpe Ratio (DSR)** — Sharpe corrected for non-normality and
   multiple testing (Bailey & López de Prado 2014).
2. **Probability of Backtest Overfitting (PBO)** — CSCV-framework probability
   that the in-sample-optimal strategy underperforms the median out-of-
   sample (Bailey, Borwein, López de Prado & Zhu 2014).
3. **Walk-forward OOS Sharpe** — held-out-of-sample slice metric, separate
   from in-sample. The analytics-engine already exposes `walk_forward_split`
   and `out_of_sample_sharpe` slots; this spec defines how they're populated.

The fourth column, `look_ahead_audit_passed`, is a static-analysis check
already wired by Daniel R.'s engine — covered briefly at the end for
completeness.

## 1. Deflated Sharpe Ratio (DSR)

**Reference:** Bailey, D. H., & López de Prado, M. (2014). The Deflated
Sharpe Ratio: Correcting for Selection Bias, Backtest Overfitting and
Non-Normality. *Journal of Portfolio Management* 40(5), 94–107.

### Inputs

| Field | Source | Notes |
|---|---|---|
| `daily_returns: list[float]` | analytics-engine `BacktestResult.daily_returns` | Already populated |
| `num_trials: int` | caller (orchestrator) | See § 1.3 for sourcing rules |
| `annualization: int` | constant `252` | Daily bars assumption |

### Formula

Convention: `SR_hat` is the **per-bar** Sharpe ratio (un-annualized). If the
caller carries an annualized Sharpe, divide by `sqrt(annualization_factor)`
before computing DSR (annualization is purely a display transform). Skewness
`gamma_3` and **raw (Pearson) kurtosis** `gamma_4` (γ₄ = 3 for normal) are
likewise computed on the per-bar return series. Note: the `(γ₄ − 1)/4` coefficient
in eq. 8 below is derived for the raw-kurtosis convention; passing Fisher
*excess* kurtosis would bias the denominator by a constant `(3/4)·ŜR²`.

Given `T` per-bar returns, the per-bar Sharpe `SR_hat`, and `N` independent
trials in the selection set, the DSR is the probability that the true Sharpe
exceeds zero:

```
gamma_E = 0.5772156649        # Euler-Mascheroni
Phi_inv = standard normal inverse CDF (scipy.stats.norm.ppf)

# Bailey-López de Prado (2014) approximation for E[max_N] across N iid
# normal Sharpe estimates with unit variance:
E_max_N = (1 - gamma_E) * Phi_inv(1 - 1/N)
        + gamma_E       * Phi_inv(1 - 1/(N * e))

# Per-bar SR_hat has variance 1/(T - 1) under the iid normal null, so the
# expected best-of-N under the null is scaled by sqrt(1/(T - 1)):
SR_zero = sqrt(1 / (T - 1)) * E_max_N

# Standard normal CDF of the variance-corrected z statistic:
DSR = Phi( (SR_hat - SR_zero) * sqrt(T - 1)
         / sqrt(1 - gamma_3 * SR_hat + ((gamma_4 - 1) / 4) * SR_hat^2) )
```

`dsr_p_value` is the resulting probability (0 to 1). Higher = more confident
the true Sharpe is positive after correcting for the N-way selection.

### Outputs

Populate on `BacktestResult`:

- `deflated_sharpe_ratio: float` — the corrected Sharpe value (Sharpe units,
  not probability)
- `dsr_p_value: float` — probability the true Sharpe > 0
- `num_trials_in_selection: int` — the N value used

### Sourcing N (`num_trials_in_selection`)

For v1, `N` is the number of distinct strategies evaluated by the analytics-
engine in the same "selection round" — i.e. the size of the curated library
at evaluation time. The orchestrator passes this in as a single integer.

**Concretely:** when Önder's `evaluate(strategy, ...)` is called by the
orchestrator, the orchestrator also passes
`num_trials = len(strategy_provider.list_strategies())`. The default in the
absence of context is `1` (no correction), but a warning is logged if N=1
when more than one strategy exists in the library.

For LLM-extracted candidates (the arxiv pipeline demo), `N` should reflect
the candidate pool size from the most recent extraction pass, not the
library size — the LLM tried K methodology variants and we picked the best.
This will be wired in T5 (post-hackathon if not reached).

#### Addendum (#770) — the agentic society path: N + library_size

The agentic society (PR #766) generates **N candidates** (`n_candidates`), backtests
**all** of them, rigor-gates **all**, and keeps the **best**. Selection-from-N is itself
a multiple-testing search distinct from the library context the winner then joins, so the
effective trial count on the society path is the **sum**:

```
num_trials = n_candidates + library_size            # the chosen formula (A)
```

**Rationale.** The survivor was chosen through two independent selection layers — it beat
`n_candidates - 1` siblings in the society round *and* it is being promoted into a library
of `library_size` prior strategies. Both are searches over which the maximum was taken, so
both inflate the observed Sharpe under the Bailey & López de Prado (2014) best-of-N null;
the additive count is the conservative, defensible deflation. It can only make the gate
**stricter** (anti-goal compliant): with `n_candidates ≥ 1` it is always `≥ library_size`,
the prior (under-deflating) value.

**Why not effective-N (correlation-adjusted) here.** `compute_dsr` already accepts an
`average_correlation` and can deflate by effective *independent* trials
`N_eff = N / (1 + (N-1)ρ̄)` — the principled refinement when candidates are correlated
(same brief, overlapping universes). We deliberately ship the **simple additive count
first** (smaller blast radius, strictly conservative) and leave the candidate-pool
correlation correction as a follow-up; over-deflation (treating correlated candidates as
independent) only *tightens* the gate, never loosens it, so the simple form is safe to ship.

**Scope note.** This additive correction applies to the **live society generation path**
(`agents/generation_pipeline.py`). The `/api/selection-bias/gate` route grades the
**existing persisted library**, where the selection set is the library itself — there is no
fresh candidate pool, so that route keeps `num_trials = library_size`.

> **Owner sign-off:** the choice of formula (additive A vs. effective-N B) is Önder's
> statistics call. This addendum records approach **A** as the shipped default pending his
> review; promoting to B is a follow-up if candidate-pool over-deflation proves material.

## 2. Probability of Backtest Overfitting (PBO)

**Reference:** Bailey, D. H., Borwein, J., López de Prado, M., & Zhu, J.
(2014). The Probability of Backtest Overfitting. SSRN 2326253.

### Inputs

| Field | Source | Notes |
|---|---|---|
| `returns_matrix: dict[str, list[float]]` | orchestrator | Map of `strategy_id → daily returns` for all N candidate strategies aligned on the same T dates. The evaluator converts this to an internal `np.ndarray` of shape `(T, N)` (column order pinned by sorted `strategy_id`) before running CSCV — the dict-keyed input is what the orchestrator naturally carries and preserves the strategy-id mapping needed for the `dict[str, float]` return value. |
| `S: int` | constant `16` | Number of CSCV partitions; 16 is the paper's recommended default |
| `selection_metric: callable` | default `sharpe_ratio` | The function used to pick the "best" strategy |

### Algorithm (CSCV — Combinatorially Symmetric Cross-Validation)

1. Partition the T-day return matrix into `S` equal-size submatrices along
   the time axis.
2. Enumerate every combination `C(S, S/2)` of S/2 submatrices forming the
   in-sample set; the complement is out-of-sample.
3. For each split, rank all N strategies in-sample by `selection_metric`,
   identify the strategy with the highest in-sample rank, then look up its
   out-of-sample rank.
4. The relative OOS rank `omega = rank_OOS / N` produces the logit
   `lambda = log( omega / (1 - omega) )`.
5. `PBO = P(lambda <= 0)` — the fraction of splits in which the in-sample-
   best strategy underperforms the OOS median.

### Outputs

Populate on `BacktestResult`:

- `pbo_score: float` — probability of backtest overfitting (0 to 1, lower
  is better)

A `pbo_score >= 0.5` means the in-sample-optimal strategy is expected to
underperform the median strategy out-of-sample — the strategy fails the
rigor gate.

### Computation cadence

PBO is a **library-level** metric, not a per-strategy metric. Compute once
per analytics-engine run across all strategies in the library, then attach
the same `pbo_score` to each strategy's `BacktestResult` from that run.
Re-compute when the library changes.

## 3. Walk-forward Out-of-Sample Sharpe

The analytics-engine already declares `walk_forward_split` (train fraction,
default 0.70) and `out_of_sample_sharpe` on its result dataclass. Önder's
`IBacktestEvaluator` should:

1. Split `daily_returns` by `walk_forward_train_fraction` along the time
   axis. No shuffling.
2. Run the strategy logic over the **train** slice for any
   parameter-tuning the strategy supports (v1 strategies are
   non-parameterized so this is a no-op).
3. Apply the chosen parameters to the **test** slice and compute Sharpe
   over the test slice alone.
4. Populate `out_of_sample_sharpe` with the test-slice Sharpe.

The rigor gate requires `out_of_sample_sharpe / sharpe_ratio >= 0.5` —
i.e. the OOS Sharpe must be at least half the in-sample Sharpe.

## 4. Look-ahead audit

`look_ahead_audit_passed: bool` is already set by Daniel R.'s engine via
[`analytics-engine/.../engine.py`](../../analytics-engine/src/archimedes_analytics_engine/engine.py)
`_lookahead_audit_passed()`, which checks that the broker is not configured
with `coc` (close-on-close) or `coo` (close-on-open). That covers the
backtrader-level look-ahead vector; if you add additional static checks
(e.g. AST analysis of the strategy file for forward-bar references) wire
the result into the same field.

## API surface

Önder's `IBacktestEvaluator.evaluate` signature already takes the strategy
and price data. Extend it once to accept `num_trials`:

```python
def evaluate(
    self,
    strategy: Strategy,
    price_data: dict[str, list[float]],
    start_date: str | None = None,
    end_date: str | None = None,
    num_trials: int = 1,  # NEW — for DSR multiple-testing correction
) -> BacktestResult: ...
```

A new method for library-level PBO:

```python
def compute_pbo(
    self,
    returns_matrix: dict[str, list[float]],  # strategy_id -> daily returns
    s_partitions: int = 16,
) -> dict[str, float]:  # strategy_id -> pbo_score (all identical)
    """Compute PBO across the full strategy library."""
```

## Acceptance criteria for v1

- [ ] `BacktestResult.deflated_sharpe_ratio` and `dsr_p_value` populated for
      every backtest run with `num_trials > 1`.
- [ ] `num_trials_in_selection` recorded so the correction is reproducible.
- [ ] `pbo_score` computed once per library run and attached to every
      strategy's result from that run.
- [ ] `out_of_sample_sharpe` populated per strategy via walk-forward split.
- [ ] `BacktestResult.passes_rigor_gate` returns `True` only when all four
      controls are present and pass their thresholds.
- [ ] Unit tests: a hand-constructed return series with known properties
      reproduces a DSR and PBO matching reference values (within tolerance).

## Numerical sanity-check examples (for unit test seed)

All three cases use the per-bar convention: `SR_per_bar = SR_annualized /
sqrt(252)` for daily bars, and skew / **raw (Pearson) kurtosis** (γ₄ = 3 for
normal) computed on the per-bar series. Reference values below were computed
against `scipy.stats.norm.ppf` and `norm.cdf`; pin the unit tests to these to
catch implementation drift.

| Case | `SR_ann` | `T` | `skew` | `raw_kurt` | `N` | `SR_zero` (per-bar) | `z` | `dsr_p_value` |
|---|---|---|---|---|---|---|---|---|
| A — strong | 1.8 | 2520 | −0.4 | 6.2  | 10   | 0.0314 |  3.994 | ~1.0000 |
| B — borderline | 0.9 | 1260 | −0.2 | 5.0  | 20   | 0.0536 |  0.110 | ~0.5439 |
| C — failure | 0.3 | 504  |  0.0 | 3.0  | 1000 | 0.1451 | −2.831 | ~0.0023 |

Case A is the slam-dunk: a long, smooth backtest with a small library.
Case B is the "credibly positive but not at the 95% bar" boundary used to
exercise the gate threshold. Case C is the multiple-testing failure mode —
a weak Sharpe pulled out of a thousand-trial selection should *not* clear
the gate, even with arbitrarily clean residuals.

## What this spec deliberately does not specify

- Exact numerical-library choice (scipy vs. numpy vs. statsmodels) — Önder
  picks.
- Specific test fixtures or property-based test setup — Önder owns.
- The PBO `S` parameter beyond the default `16` — known-good per the paper.
- Encryption / privacy of trial counts (a v2 concern; v1 is fully public).

## References

Bailey, D. H., & López de Prado, M. (2014). The Deflated Sharpe Ratio:
Correcting for Selection Bias, Backtest Overfitting and Non-Normality.
*Journal of Portfolio Management* 40(5), 94–107.
<https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2460551>

Bailey, D. H., Borwein, J., López de Prado, M., & Zhu, J. (2014). The
Probability of Backtest Overfitting. SSRN 2326253.
<https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2326253>

McLean, R. D., & Pontiff, J. (2016). Does Academic Research Destroy Stock
Return Predictability? *Journal of Finance* 71(1), 5–32.
<https://onlinelibrary.wiley.com/doi/abs/10.1111/jofi.12365>
