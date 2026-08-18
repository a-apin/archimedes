# Quantitative Methodology Docs

> The math layer of Archimedes, written for teammates, judges, and anyone reading a
> strategy passport. Author: Önder Akkaya (quant / math lane). Written 2026-06-12.

These four docs explain the statistics and portfolio math behind the
rigor-as-the-wedge story. They are the conceptual companions to the canonical
operational spec [`../specs/selection-bias-corrections-spec.md`](../specs/selection-bias-corrections-spec.md)
and the judge-facing summary [`../rigor-methods.md`](../rigor-methods.md); where
thresholds or formulas overlap, the spec and the code
([`../../backend/archimedes/services/rigor_evaluator.py`](../../backend/archimedes/services/rigor_evaluator.py),
[`../../backend/archimedes/services/portfolio_optimizer.py`](../../backend/archimedes/services/portfolio_optimizer.py))
are authoritative.

| Doc | What it covers |
|---|---|
| [`methodology.md`](methodology.md) | The full quantitative methodology — selection-bias corrections (DSR, PBO/CSCV, walk-forward OOS, look-ahead, FDR vs FWER, circular block bootstrap) and portfolio construction (MVO, GMV, Max-Sharpe, Kelly, HRP, Black–Litterman, Ledoit–Wolf shrinkage). |
| [`backtest-interpretation.md`](backtest-interpretation.md) | How to read a backtest adversarially — the red flags (IS/OOS cliff, parameter sensitivity, smooth curves, concentration, regime turnover, correlation clustering) and green lights, each mapped to the detector in our codebase. |
| [`admission-criteria.md`](admission-criteria.md) | The four-gate Tier-1 admission contract, the CANDIDATE → VALIDATED flow, principled exceptions, and post-admission monitoring. |
| [`strategy-library.md`](strategy-library.md) | A per-strategy reference for the strategy files in `analytics-engine/strategies/`, grouped by sleeve, with the honest paper-vs-v1 adaptation caveats. |

**Read in this order** if you are new: `methodology.md` → `backtest-interpretation.md`
→ `admission-criteria.md` → `strategy-library.md`.
