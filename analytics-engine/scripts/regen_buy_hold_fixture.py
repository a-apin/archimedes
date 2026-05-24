"""Regenerate the pipeline_buy_hold entry in backtest_fixtures.json.

Fetches real SPY data via yfinance, runs the buy-and-hold backtest,
computes DSR + OOS Sharpe + Kelly, then writes the corrected fixture.

Usage:
    cd analytics-engine
    uv run python scripts/regen_buy_hold_fixture.py          # dry-run (print only)
    uv run python scripts/regen_buy_hold_fixture.py --write  # update fixtures
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "src"))

from archimedes_analytics_engine.data import fetch_ohlcv
from archimedes_analytics_engine.engine import BuyAndHoldStrategy, run_backtest

# ── Config ────────────────────────────────────────────────────────────────────
BACKTEST_START = "2004-01-02"
BACKTEST_END = "2026-05-01"  # yfinance end is exclusive; includes bars through 2026-04-30
INITIAL_CASH = 100_000.0
TX_COST_BPS = 10
_FIXTURE_PATH = ROOT / "strategies" / "backtest_fixtures.json"
_EULER = 0.5772156649
_ANN = 252


# ── Pure-numpy normal CDF (Zelen & Severo rational approximation) ─────────────


def _norm_cdf(x: float) -> float:
    t = 1.0 / (1.0 + 0.2316419 * abs(x))
    poly = t * (0.319381530 + t * (-0.356563782 + t * (1.781477937 + t * (-1.821255978 + t * 1.330274429))))
    p = 1.0 - (1.0 / math.sqrt(2 * math.pi)) * math.exp(-0.5 * x * x) * poly
    return p if x >= 0 else 1.0 - p


def _norm_ppf(p: float) -> float:
    """Rational approximation (Acklam, max error < 1.2e-7)."""
    a = [
        -3.969683028665376e01,
        2.209460984245205e02,
        -2.759285104469687e02,
        1.383577518672690e02,
        -3.066479806614716e01,
        2.506628277459239e00,
    ]
    b = [
        -5.447609879822406e01,
        1.615858368580409e02,
        -1.556989798598866e02,
        6.680131188771972e01,
        -1.328068155288572e01,
    ]
    c = [
        -7.784894002430293e-03,
        -3.223964580411365e-01,
        -2.400758277161838e00,
        -2.549732539343734e00,
        4.374664141464968e00,
        2.938163982698783e00,
    ]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e00, 3.754408661907416e00]
    p_lo, p_hi = 0.02425, 1 - 0.02425
    if p_lo <= p <= p_hi:
        q = p - 0.5
        r = q * q
        num = (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q
        den = ((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1
        return num / den
    q = math.sqrt(-2 * math.log(p if p < p_lo else 1 - p))
    sign = 1.0 if p < p_lo else -1.0
    num = ((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]
    den = (((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1
    return sign * num / den


# ── Stats helpers (no scipy) ──────────────────────────────────────────────────


def _skew(arr: np.ndarray) -> float:
    m = arr - arr.mean()
    m2 = float(np.mean(m**2))
    if m2 == 0:
        return 0.0
    return float(np.mean(m**3)) / m2**1.5


def _raw_kurtosis(arr: np.ndarray) -> float:
    """Pearson (raw) kurtosis — normal distribution = 3. DSR formula requires this."""
    m = arr - arr.mean()
    m2 = float(np.mean(m**2))
    if m2 == 0:
        return 0.0
    return float(np.mean(m**4)) / m2**2


# ── Rigor metrics ─────────────────────────────────────────────────────────────


def compute_dsr(
    daily_returns: list[float],
    num_trials: int,
) -> tuple[float | None, float | None]:
    arr = np.asarray(daily_returns, dtype=float)
    n = len(arr)
    if n < 4 or float(np.ptp(arr)) == 0.0:
        return None, None
    sigma = float(arr.std(ddof=1))
    if sigma <= 0:
        return None, None

    sr_hat = float(arr.mean()) / sigma
    g3 = _skew(arr)
    g4 = _raw_kurtosis(arr)  # Pearson raw kurtosis; DSR formula uses (γ₄−1)/4
    trials = max(1, num_trials)

    if trials == 1:
        e_max = 0.0
    else:
        e_max = (1 - _EULER) * _norm_ppf(1 - 1 / trials) + _EULER * _norm_ppf(1 - 1 / (trials * math.e))

    sr_zero = math.sqrt(1.0 / (n - 1)) * e_max
    denom_sq = 1.0 - g3 * sr_hat + ((g4 - 1.0) / 4.0) * sr_hat**2
    if denom_sq <= 0:
        return None, None

    z = (sr_hat - sr_zero) * math.sqrt(n - 1) / math.sqrt(denom_sq)
    return (
        round((sr_hat - sr_zero) * math.sqrt(_ANN), 6),
        round(_norm_cdf(z), 6),
    )


def compute_oos_sharpe(
    daily_returns: list[float],
    train_fraction: float = 0.70,
) -> float | None:
    arr = np.asarray(daily_returns, dtype=float)
    if len(arr) < 10:
        return None
    oos = arr[int(len(arr) * train_fraction) :]
    if len(oos) < 5 or float(np.ptp(oos)) == 0.0:
        return None
    sigma = float(oos.std(ddof=1))
    if sigma <= 0:
        return None
    return round(float((oos.mean() / sigma) * math.sqrt(_ANN)), 6)


def compute_kelly(
    daily_returns: list[float],
    rf_annual: float = 0.05,
    fractional: float = 0.5,
) -> float | None:
    arr = np.asarray(daily_returns, dtype=float)
    if len(arr) < 4 or float(np.ptp(arr)) == 0.0:
        return None
    sigma_d = float(arr.std(ddof=1))
    if sigma_d <= 0:
        return None
    mu_ann = float(arr.mean()) * _ANN
    sigma_sq_ann = sigma_d**2 * _ANN
    excess = mu_ann - rf_annual
    if excess <= 0:
        return 0.0
    return round(float(np.clip(fractional * excess / sigma_sq_ann, 0.0, 1.0)), 6)


# ── Rigor gate ────────────────────────────────────────────────────────────────


def _compute_passes_rigor_gate(
    passes_validation: bool,
    dsr: float | None,
    dsr_p: float | None,
    num_trials: int | None,
    full_sharpe: float | None,
    oos_sharpe: float | None,
    pbo_score: float | None,
    look_ahead_passed: bool | None,
) -> bool:
    """Recompute the rigor gate from fresh metrics.

    Mirrors BacktestResult.passes_rigor_gate criteria 1–6:
      1. Base validation passes (Sharpe/DD/CAGR/trade-count).
      2. DSR value, p-value, and num_trials all populated.
      3. DSR p-value >= 0.95.
      4. PBO score populated and < 0.5 (strictly; >= 0.5 fails).
      5. Look-ahead audit passed.
      6. OOS Sharpe populated; if full Sharpe > 0, OOS/full >= 0.5.

    Note: omits the sharpe_vs_paper >= 0.5 check (criterion 7) because
    pipeline_buy_hold has no paper_claimed_sharpe. If this helper is ever
    reused for a paper-grounded strategy, add that check.
    """
    if not passes_validation:
        return False
    if dsr is None or dsr_p is None or num_trials is None:
        return False
    if dsr_p < 0.95:
        return False
    if pbo_score is None or pbo_score >= 0.5:
        return False
    if look_ahead_passed is not True:
        return False
    if oos_sharpe is None:
        return False
    return not (full_sharpe is not None and full_sharpe > 0 and oos_sharpe / full_sharpe < 0.5)


# ── Main ──────────────────────────────────────────────────────────────────────


def main(write: bool = False) -> None:
    try:
        fixtures = json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        raise SystemExit(
            f"Cannot read fixture file {_FIXTURE_PATH}: {exc}\n"
            f"Ensure the fixture file exists at {_FIXTURE_PATH} and contains valid JSON."
        ) from exc
    num_trials = len(fixtures)
    existing = fixtures.get("pipeline_buy_hold", {})

    print(f"Fetching SPY {BACKTEST_START} → {BACKTEST_END}…")
    prices = fetch_ohlcv("SPY", BACKTEST_START, BACKTEST_END)
    print(f"  {len(prices)} bars  ({prices.index[0].date()} → {prices.index[-1].date()})")

    print("Running buy-and-hold backtest…")
    result = run_backtest(
        prices,
        strategy_cls=BuyAndHoldStrategy,
        initial_cash=INITIAL_CASH,
        transaction_cost_bps=TX_COST_BPS,
        slippage_bps=0,
    )
    print(
        f"  CAGR: {result.cagr:.2%}"
        f"  Sharpe: {result.sharpe_ratio:.4f}"
        f"  Max DD: {result.max_drawdown_pct:.2f}%"
        f"  Final: ${result.final_value:,.0f}"
    )

    daily = result.daily_returns
    dsr, dsr_p = compute_dsr(daily, num_trials)
    oos = compute_oos_sharpe(daily)
    kelly = compute_kelly(daily)
    print(f"  DSR: {dsr}  p={dsr_p}  OOS Sharpe: {oos}  Kelly: {kelly}")

    if result.max_drawdown_pct is None:
        raise SystemExit("Backtest returned no max_drawdown_pct; cannot write a valid fixture.")
    max_dd_frac = result.max_drawdown_pct / 100.0
    calmar = None
    if result.cagr is not None and max_dd_frac > 0:
        calmar = round(result.cagr / max_dd_frac, 7)

    entry = {
        "sharpe_ratio": round(result.sharpe_ratio, 10) if result.sharpe_ratio is not None else None,
        "sortino_ratio": (round(result.sortino_ratio, 10) if result.sortino_ratio is not None else None),
        "max_drawdown": round(max_dd_frac, 16),
        "cagr": round(result.cagr, 16) if result.cagr is not None else None,
        "calmar_ratio": calmar,
        "win_rate": None,
        "profit_factor": None,
        "total_trades": result.total_trades,
        "avg_holding_period_days": None,
        "correlation_to_spy": 1.0,
        "correlation_to_btc": None,
        "out_of_sample_sharpe": oos,
        "look_ahead_audit_passed": result.look_ahead_audit_passed,
        "backtest_engine": "backtrader",
        "transaction_cost_bps": TX_COST_BPS,
        "backtest_start": result.backtest_start,
        "backtest_end": result.backtest_end,
        "paper_claimed_sharpe": None,
        "paper_claimed_cagr": None,
        "paper_claimed_max_dd": None,
        "deflated_sharpe_ratio": dsr,
        "dsr_p_value": dsr_p,
        "num_trials_in_selection": num_trials,
        # PBO requires all strategies' daily returns simultaneously; carry forward.
        "pbo_score": existing.get("pbo_score"),
        "passes_rigor_gate": _compute_passes_rigor_gate(
            passes_validation=(
                result.sharpe_ratio is not None
                and result.sharpe_ratio > 0.5
                and max_dd_frac < 0.5
                and result.cagr is not None
                and result.cagr < 10.0
                and (result.total_trades < 2 or result.total_trades >= 10)
            ),
            dsr=dsr,
            dsr_p=dsr_p,
            num_trials=num_trials,
            full_sharpe=result.sharpe_ratio,
            oos_sharpe=oos,
            pbo_score=existing.get("pbo_score"),
            look_ahead_passed=result.look_ahead_audit_passed,
        ),
        "kelly_fraction": kelly,
    }

    print("\n── New fixture entry ──────────────────────────────────")
    print(json.dumps({"pipeline_buy_hold": entry}, indent=2))

    if write:
        fixtures["pipeline_buy_hold"] = entry
        _FIXTURE_PATH.write_text(json.dumps(fixtures, indent=2) + "\n", encoding="utf-8")
        print(f"\n✓ Written to {_FIXTURE_PATH}")


if __name__ == "__main__":
    main(write="--write" in sys.argv)
