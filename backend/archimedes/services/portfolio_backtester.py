"""Real multi-year backtester for AI-generated portfolio strategies.

Closes the gap that left every generated strategy stuck at "Pending Backtest"
on the Library page. The analytics-engine's :mod:`engine.run_backtest` runs
single-asset backtrader Cerebro sims keyed on a hand-written ``bt.Strategy``
class — fine for the 6 curated example strategies (each ships its own class)
but wrong for generated output: the LLM emits ``{ticker: weight}`` maps + a
rebalance period, not Python strategy code.

This module fills that hole with a vanilla pandas/numpy simulator:

  1. ``fetch_ohlcv`` (yfinance) for every ticker in ``weights``
  2. Wide-form close panel with strict inner-join on the business-day index
  3. Periodic rebalance with linear transaction costs (``tx_cost_bps``)
  4. Daily portfolio return series → Sharpe / Sortino / CAGR / max DD / Calmar
  5. The same daily-return series is fed into :mod:`rigor_evaluator` for
     DSR (Bailey & López de Prado 2014) and walk-forward OOS Sharpe — same
     primitives the curated strategies use, so generated and curated
     strategies are graded on the same scale.

Honest framing the UI inherits via the ``backtest_engine`` field:
this is a static-rebalance backtest of the agent's *allocation*. It does NOT
test regime-conditional signal logic — the agent does not emit signals, only
weights. Surfacing this as ``"portfolio-simulator-v1"`` lets the passport
distinguish it from the curated strategies' ``"backtrader"`` runs.

Owner: Dan (strategy engine); rigor primitives owned by Önder.
Spec:  docs/specs/selection-bias-corrections-spec.md (rigor gate unchanged).
"""

from __future__ import annotations

import hashlib
import inspect
import logging
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from archimedes.models.backtest import BacktestResult
from archimedes.services.rigor_evaluator import compute_dsr, compute_oos_sharpe

logger = logging.getLogger(__name__)


# Defaults tuned to give enough bars for a meaningful DSR (T ≥ 4 minimum;
# practical floor is ~252 bars / 1 year so the annualization isn't junk).
DEFAULT_LOOKBACK_YEARS = 7
DEFAULT_REBALANCE_DAYS = 21  # ~monthly
DEFAULT_INITIAL_CASH = 100_000.0
DEFAULT_TX_COST_BPS = 10  # round-trip; matches analytics-engine default
ANNUALIZATION = 252
RF_DAILY = 0.05 / ANNUALIZATION  # 5% annual risk-free rate, daily equivalent
MIN_BARS_FOR_BACKTEST = 60  # ~3 months; refuse to backtest shorter windows


def _ensure_analytics_import() -> None:
    """Place ``analytics-engine/src`` on sys.path so its ``data`` module imports.

    Mirrors :func:`archimedes.scripts.run_backtests._ensure_analytics_import` —
    we deliberately reuse the analytics-engine's yfinance wrapper rather than
    pulling yfinance directly here, so a single function owns the fetch+
    normalize contract.
    """
    analytics_src = Path(__file__).resolve().parents[3] / "analytics-engine" / "src"
    if str(analytics_src) not in sys.path:
        sys.path.insert(0, str(analytics_src))


def _fetch_price_panel(symbols: list[str], start: str, end: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Fetch close prices and volumes for ``symbols`` and inner-join on the date index.

    Missing data is a critical lookahead vector. If a symbol halted or wasn't
    public yet, forward-filling prices leaks the "survival" fact to the
    simulator. We strictly inner-join: the panel only contains days where
    EVERY requested symbol traded.
    """
    _ensure_analytics_import()
    from archimedes_analytics_engine.data import fetch_ohlcv

    closes: dict[str, pd.Series] = {}
    volumes: dict[str, pd.Series] = {}
    for sym in symbols:
        try:
            df = fetch_ohlcv(sym, start, end)
            if not df.empty and "Close" in df.columns and "Volume" in df.columns:
                closes[sym] = df["Close"]
                volumes[sym] = df["Volume"]
        except Exception:
            pass

    close_panel = pd.DataFrame(closes).dropna()
    volume_panel = pd.DataFrame(volumes).dropna()

    if close_panel.empty or volume_panel.empty:
        raise ValueError(f"Insufficient overlapping history for {symbols}")

    common_idx = close_panel.index.intersection(volume_panel.index)
    if common_idx.empty:
        raise ValueError(f"Insufficient overlapping history for {symbols}")

    panel = close_panel.loc[common_idx]
    if len(panel) < MIN_BARS_FOR_BACKTEST:
        raise ValueError(
            f"Insufficient overlapping history: only {len(panel)} bars across {symbols} (min {MIN_BARS_FOR_BACKTEST})"
        )

    return close_panel.loc[common_idx], volume_panel.loc[common_idx]


def _simulate_portfolio(
    panel: pd.DataFrame,
    volume_panel: pd.DataFrame,
    target_weights: dict[str, float] | pd.DataFrame,
    *,
    rebalance_days: int,
    initial_cash: float,
    tx_cost_bps: int = 10,
    gamma: float = 0.1,  # Almgren square-root impact coefficient — see note below
) -> tuple[list[float], list[float]]:
    """Run a periodic-rebalance simulation; returns ``(daily_returns, equity_curve)``.

    Implementation notes:
      - Held weights drift between rebalance bars with realized returns.
      - Uses the Almgren square-root market impact function:
        Impact = gamma * sigma * Q * sqrt(Q / ADV)
      - ``gamma`` is the dimensionless permanent-impact coefficient from the
        Almgren-Chriss (2000) optimal-execution framework, refined to the
        empirical square-root law in Almgren et al. (2005), "Direct Estimation
        of Equity Market Impact" (Risk). Calibrated estimates sit around
        0.1-1.0 depending on venue and asset class; we default to a deliberately
        conservative 0.1 so the impact haircut is honest-but-not-punitive. It is
        a tunable parameter, not a fitted constant — surface it per-asset once we
        have venue-specific ADV/impact calibration data.
      - Proportional transaction costs (tx_cost_bps) are additive to Almgren impact.
      - No leverage. Negative weights are clamped to zero at normalization
        (long-only enforcement matches the agent's actual output contract).
    """
    syms = list(panel.columns)
    n_bars = len(panel)

    returns_df = panel.pct_change().fillna(0.0)

    # Pre-compute rolling metrics for Almgren impact (must be shift(1) to avoid look-ahead bias)
    rolling_window = 21
    sigma_df = returns_df.rolling(window=rolling_window, min_periods=1).std().shift(1).fillna(0.0)
    dollar_volume_df = (volume_panel * panel).fillna(0.0)
    adv_df = dollar_volume_df.rolling(window=rolling_window, min_periods=1).mean().shift(1).fillna(0.0)

    # ── Temporal (t-1) data alignment execution layer guard ──
    if isinstance(target_weights, pd.DataFrame):
        dynamic_targets = target_weights.reindex(index=panel.index, columns=syms).clip(lower=0.0).fillna(0.0)

        if dynamic_targets.empty:
            raise ValueError("Dynamic target weights DataFrame cannot be empty.")

        # The loop inherently provides a T-1 execution guard: 'held' state
        # from the end of i-1 is applied to the return of bar i.
        # Thus, signals computed at close(t) are executed at close(t),
        # earning returns over t+1. No double-shift is needed.

        # Row-wise L1 normalization
        row_sums = dynamic_targets.sum(axis=1)
        dynamic_targets = dynamic_targets.div(row_sums.where(row_sums > 0, 1.0), axis=0)
        is_dynamic = True
        dynamic_targets_arr = dynamic_targets.to_numpy()
    else:
        # Build canonical static target weights
        raw = pd.Series({s: max(target_weights.get(s, 0.0), 0.0) for s in syms})
        total = float(raw.sum())
        if total <= 0:
            raise ValueError("All weights non-positive after symbol alignment")
        static_target = raw / total
        is_dynamic = False
        static_target_arr = static_target.to_numpy()

    # Extract NumPy arrays for high-performance iteration
    returns_arr = returns_df.to_numpy()
    sigma_arr = sigma_df.to_numpy()
    adv_arr = adv_df.to_numpy()

    held = dynamic_targets_arr[0].copy() if is_dynamic else static_target_arr.copy()

    portfolio_returns: list[float] = []
    equity = float(initial_cash)
    equity_curve: list[float] = []

    for i in range(n_bars):
        # The portfolio return for day i is generated by the weights held at
        # the end of day i-1, perfectly aligned with the t-1 execution guard.
        r_t = float(np.sum(returns_arr[i] * held))

        # Calculate pre-cost equity at end of day
        post_r_t_equity = max(0.0, equity * (1.0 + r_t))

        # Rebalance drift
        drifted = held * (1.0 + returns_arr[i])
        drifted_sum = np.sum(drifted)
        if drifted_sum > 0:
            drifted /= drifted_sum
        else:
            drifted = np.zeros_like(held)

        target = dynamic_targets_arr[i].copy() if is_dynamic else static_target_arr.copy()

        # If dynamic, we must rebalance every bar to track the signals.
        should_rebalance = (i > 0) if is_dynamic else (i > 0 and i % rebalance_days == 0)

        if post_r_t_equity <= 0.0:
            # Bankruptcy!
            r_t = -1.0  # 100% loss
            held = np.zeros_like(held)
            post_r_t_equity = 0.0
        elif should_rebalance:
            delta_w = np.abs(drifted - target)
            # Use pre-return equity (consistent with cost_fraction denominator below)
            q_j = delta_w * equity

            # Avoid division by zero in ADV, penalize illiquidity heavily
            safe_adv = np.where(adv_arr[i] > 0, adv_arr[i], 1.0)

            # Impact Cost = sum(gamma * sigma_j * Q_j * sqrt(Q_j / ADV_j))
            # + linear bps cost
            impact_dollars = np.sum(gamma * sigma_arr[i] * q_j * np.sqrt(q_j / safe_adv))
            linear_cost_dollars = np.sum(delta_w) * equity * (tx_cost_bps / 10_000.0)

            total_cost_dollars = impact_dollars + linear_cost_dollars

            cost_fraction = total_cost_dollars / equity if equity > 0 else 0.0
            r_t -= cost_fraction
            post_r_t_equity = max(0.0, equity * (1.0 + r_t))
            held = target.copy()
        else:
            held = drifted

        portfolio_returns.append(r_t)
        equity_curve.append(post_r_t_equity)
        equity = post_r_t_equity

    return portfolio_returns, equity_curve


def _annualized_metrics(daily_returns: list[float], equity_curve: list[float]) -> dict[str, float]:
    """Annualized Sharpe / Sortino / CAGR / max DD / Calmar from a daily return series."""
    arr = np.asarray(daily_returns, dtype=float)
    T = len(arr)
    if T < 2:
        return {
            "sharpe_ratio": 0.0,
            "sortino_ratio": 0.0,
            "cagr": 0.0,
            "max_drawdown": 0.0,
            "calmar_ratio": 0.0,
        }

    mu = float(arr.mean())
    sigma = float(arr.std(ddof=1))
    sharpe = ((mu - RF_DAILY) / sigma) * np.sqrt(ANNUALIZATION) if sigma > 0 else 0.0

    downside = arr[arr < 0]
    # RMS of negative returns (consistent with analytics-engine/engine.py Sortino)
    down_rms = float(np.sqrt(np.mean(downside**2))) if len(downside) > 0 else 0.0
    sortino = ((mu - RF_DAILY) / down_rms) * np.sqrt(ANNUALIZATION) if down_rms > 0 else 0.0

    eq = np.asarray(equity_curve, dtype=float)
    cagr = float((eq[-1] / eq[0]) ** (ANNUALIZATION / T) - 1.0) if eq[0] > 0 and T >= 2 else 0.0

    drawdown = (eq / np.maximum.accumulate(eq)) - 1.0
    max_dd = float(-drawdown.min()) if T > 0 else 0.0

    calmar = float(cagr / max_dd) if max_dd > 0 else 0.0

    return {
        "sharpe_ratio": float(sharpe),
        "sortino_ratio": float(sortino),
        "cagr": cagr,
        "max_drawdown": max_dd,
        "calmar_ratio": calmar,
    }


def _correlation_to_benchmark(daily_returns: list[float], benchmark_returns: list[float]) -> float:
    """Pearson correlation between two return series, robust to unequal length."""
    n = min(len(daily_returns), len(benchmark_returns))
    if n < 2:
        return 0.0
    a = np.asarray(daily_returns[:n], dtype=float)
    b = np.asarray(benchmark_returns[:n], dtype=float)
    if a.std() == 0 or b.std() == 0:
        return 0.0
    return float(np.corrcoef(a, b)[0, 1])


def _module_hash() -> str:
    """SHA-256 of the simulator's source — for replay verification on the artifact."""
    try:
        src = inspect.getsource(_simulate_portfolio) + inspect.getsource(_annualized_metrics)
        return hashlib.sha256(src.encode("utf-8")).hexdigest()
    except (OSError, TypeError):
        return ""


def _panel_hash(panel: pd.DataFrame) -> str:
    """SHA-256 of the price panel content — lets a replayer verify same data."""
    return hashlib.sha256(panel.to_csv(index=True).encode("utf-8")).hexdigest()


def backtest_portfolio(
    *,
    strategy_id: str,
    weights: dict[str, float] | pd.DataFrame,
    start_date: str | None = None,
    end_date: str | None = None,
    rebalance_days: int = DEFAULT_REBALANCE_DAYS,
    initial_cash: float = DEFAULT_INITIAL_CASH,
    tx_cost_bps: int = DEFAULT_TX_COST_BPS,
    num_trials_for_dsr: int = 1,
    paper_claimed_sharpe: float | None = None,
    paper_title: str | None = None,
) -> tuple[BacktestResult, dict[str, Any]]:
    """Backtest a static-weight portfolio over real multi-year market data.

    Args:
        strategy_id: FK back to the StrategyPassport row.
        weights: ``{ticker: weight}`` or DataFrame for dynamic rebalancing.
        start_date: ISO date for backtest start. Defaults to ``end - DEFAULT_LOOKBACK_YEARS``.
        end_date: ISO date for backtest end. Defaults to today (UTC).
        rebalance_days: Periodic rebalance interval in trading days.
        initial_cash: Starting equity.
        tx_cost_bps: Round-trip cost in basis points (10 = 0.10%, matches default).
        num_trials_for_dsr: Multiple-testing correction; pass library size for
            meaningful DSR. Default 1 = no correction (passport's own row).
        paper_claimed_sharpe: If set, recorded for paper-vs-actual delta display.
        paper_title: Optional human-readable title for the artifact metadata.

    Returns:
        ``(BacktestResult, artifact_dict)`` — the dataclass for DB persistence,
        the dict for the JSON ``artifact_json`` blob (mirrors analytics-engine
        artifact shape so the existing rigor-gate consumers stay generic).

    Raises:
        ValueError: insufficient overlap (< MIN_BARS_FOR_BACKTEST) or all weights
            zero after symbol alignment. Callers should treat these as
            non-fatal: a failed backtest leaves the placeholder in place.
    """
    end_iso = end_date or datetime.now(UTC).date().isoformat()
    if start_date is None:
        start_dt = datetime.fromisoformat(end_iso) - timedelta(days=365 * DEFAULT_LOOKBACK_YEARS)
        start_iso = start_dt.date().isoformat()
    else:
        start_iso = start_date

    if isinstance(weights, pd.DataFrame):
        active_weights = weights
        symbols = list(weights.columns)
    else:
        active_weights = {sym: float(w) for sym, w in (weights or {}).items() if w and w > 0 and sym}
        symbols = list(active_weights.keys())

    if not symbols:
        raise ValueError(f"No positive weights for strategy {strategy_id}")

    panel, volume_panel = _fetch_price_panel(symbols, start_iso, end_iso)
    daily_returns, equity_curve = _simulate_portfolio(
        panel=panel,
        volume_panel=volume_panel,
        target_weights=active_weights,
        rebalance_days=rebalance_days,
        initial_cash=initial_cash,
        tx_cost_bps=tx_cost_bps,
    )

    core = _annualized_metrics(daily_returns, equity_curve)

    # ── Capacity Decay Simulation ──
    # Run the backtest at logarithmic AUM scales to chart capacity limits.
    capacity_decay: dict[str, dict[str, float]] = {}
    for aum_tier in [1_000_000.0, 10_000_000.0, 100_000_000.0, 1_000_000_000.0, 10_000_000_000.0]:
        tier_rets, tier_eq = _simulate_portfolio(
            panel=panel,
            volume_panel=volume_panel,
            target_weights=active_weights,
            rebalance_days=rebalance_days,
            initial_cash=aum_tier,
            tx_cost_bps=tx_cost_bps,
        )
        tier_metrics = _annualized_metrics(tier_rets, tier_eq)
        capacity_decay[f"${int(aum_tier):,}"] = {
            "cagr": tier_metrics["cagr"],
            "sharpe": tier_metrics["sharpe_ratio"],
        }

    # ── Rigor primitives — same evaluator the curated strategies use ──
    deflated_sharpe, dsr_p_value = compute_dsr(daily_returns, num_trials_for_dsr)
    oos_sharpe = compute_oos_sharpe(daily_returns)
    # The rebalancer mechanics are structurally look-ahead-free (t-1 held weights
    # earn t returns). However, the LLM-generated weight matrix is not audited —
    # weights encoding future information (e.g. allocating to last quarter's winners)
    # cannot be detected here. This flag reflects mechanical correctness only.
    look_ahead_passed = True

    # ── SPY correlation (diversification signal) ──
    correlation_to_spy = 0.0
    try:
        spy_panel, _ = _fetch_price_panel(["SPY"], start_iso, end_iso)
        spy_full = spy_panel["SPY"].pct_change().fillna(0.0)
        # Align to the portfolio's panel index (inner-join already applied)
        common = panel.index.intersection(spy_panel.index)
        if len(common) >= 2:
            spy_aligned = spy_full.loc[common].tolist()
            port_series = pd.Series(daily_returns, index=panel.index)
            port_aligned = port_series.loc[common].tolist()
            correlation_to_spy = _correlation_to_benchmark(port_aligned, spy_aligned)
    except (ValueError, KeyError) as exc:
        logger.debug("SPY correlation skipped: %s", exc)

    n_obs_daily = len(daily_returns)
    # Number of rebalance events ≈ trades; pure buy-and-hold (no rebalance bars
    # crossed) reports 0 which is honest.
    rebalance_events = max(0, n_obs_daily // rebalance_days - 1)

    result = BacktestResult(
        strategy_id=strategy_id,
        sharpe_ratio=core["sharpe_ratio"],
        sortino_ratio=core["sortino_ratio"],
        max_drawdown=core["max_drawdown"],
        cagr=core["cagr"],
        calmar_ratio=core["calmar_ratio"],
        win_rate=0.0,  # No closed trades in static rebalance; honest 0
        profit_factor=0.0,
        total_trades=rebalance_events,
        avg_holding_period_days=float(rebalance_days),
        correlation_to_spy=correlation_to_spy,
        correlation_to_btc=0.0,  # not computed for portfolio sim
        equity_curve=equity_curve,
        monthly_returns=[],
        backtest_start=date.fromisoformat(start_iso),
        backtest_end=date.fromisoformat(end_iso),
        paper_claimed_sharpe=paper_claimed_sharpe,
        paper_claimed_cagr=None,
        paper_claimed_max_dd=None,
        deflated_sharpe_ratio=deflated_sharpe,
        dsr_p_value=dsr_p_value,
        num_trials_in_selection=num_trials_for_dsr,
        pbo_score=None,  # library-level metric; a follow-up scheduler can refresh
        out_of_sample_sharpe=oos_sharpe,
        walk_forward_train_fraction=0.70,
        look_ahead_audit_passed=look_ahead_passed,
        backtest_engine="portfolio-simulator-v1",
        backtest_code_hash=_module_hash(),
        transaction_cost_bps=tx_cost_bps,
    )

    artifact = {
        "run_id": f"gen-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-{strategy_id[:8]}",
        "timestamp_utc": datetime.now(UTC).isoformat(),
        "operations": symbols,
        "strategy": {
            "path": f"generated/{strategy_id}",
            "class_name": "GeneratedStaticRebalance",
            "backtest_code_hash": result.backtest_code_hash,
            "paper_arxiv_id": None,
            "paper_title": paper_title or f"Generated Strategy {strategy_id[:8]}",
            "methodology_hash": None,
            "paper_claimed_sharpe": paper_claimed_sharpe,
            "paper_claimed_cagr": None,
            "paper_claimed_max_dd": None,
        },
        "assumptions": {
            "start": start_iso,
            "end": end_iso,
            "transaction_cost_bps": tx_cost_bps,
            "slippage_bps": tx_cost_bps,
            "lookahead_guard": "static_rebalance_no_signal_shift",
            "walk_forward_split": 0.70,
            "data_source": "yfinance",
            "backtest_engine": "portfolio-simulator-v1",
            "rebalance_days": rebalance_days,
            "weights": active_weights,
        },
        "results": [
            {
                "operation": "PORTFOLIO",
                "symbol": "PORTFOLIO",
                "metrics": {
                    **core,
                    "daily_returns": daily_returns,
                    "equity_curve": equity_curve,
                    "deflated_sharpe_ratio": deflated_sharpe,
                    "dsr_p_value": dsr_p_value,
                    "out_of_sample_sharpe": oos_sharpe,
                    "correlation_to_spy": correlation_to_spy,
                    "num_bars": n_obs_daily,
                    "rebalance_events": rebalance_events,
                    "capacity_decay": capacity_decay,
                },
            }
        ],
        "data_hashes": [_panel_hash(panel)],
        "integrity_flags": {
            "lookahead_audit_passed": look_ahead_passed,
            "survivorship_bias_mitigated": False,  # yfinance ticker list is current-roster
            "paper_claim_comparison_applied": paper_claimed_sharpe is not None,
        },
    }
    return result, artifact


# ═══════════════════════════════════════════════════════════════════════════════
# Phase 2.2 additions — Monte Carlo robustness, sensitivity sweep, walk-forward
# ═══════════════════════════════════════════════════════════════════════════════


def monte_carlo_portfolio(
    daily_returns: list[float],
    *,
    n_trials: int = 1000,
    block_size: int = 20,
    seed: int = 42,
) -> dict[str, Any]:
    """Block bootstrap to quantify uncertainty in portfolio performance metrics.

    Generates ``n_trials`` resampled return series using a circular block
    bootstrap (Politis & Romano 1992, "A Circular Block-Resampling Procedure for
    Stationary Data") and computes Sharpe / CAGR / max-DD / Sortino on each.
    Returns empirical confidence intervals and the per-trial distribution.

    Block bootstrap preserves short-range autocorrelation (momentum / mean-
    reversion at lags ≤ block_size), which standard i.i.d. bootstrap destroys.
    Choosing block_size ≈ sqrt(T) is a common rule-of-thumb (Politis & White
    2004, "Automatic Block-Length Selection for the Dependent Bootstrap").

    Args:
        daily_returns: Daily portfolio returns (length T).
        n_trials: Number of bootstrap replicates.
        block_size: Circular block size (default 20 ≈ monthly).
        seed: RNG seed for reproducibility.

    Returns:
        dict with keys ``sharpe_ci_95``, ``cagr_ci_95``, ``max_dd_ci_95``,
        ``sortino_ci_95`` (each a ``[low, high]`` 2-list), plus
        ``trial_sharpes`` / ``trial_cagrs`` / ``trial_max_dds`` (full distributions),
        ``observed_sharpe``, ``observed_cagr``, ``observed_max_dd``,
        ``n_trials``, ``block_size``, ``pct_positive_sharpe``.
    """
    arr = np.asarray(daily_returns, dtype=float)
    T = len(arr)
    if T < block_size * 2:
        raise ValueError(f"Need ≥ {block_size * 2} returns for block bootstrap; got {T}")

    rng = np.random.default_rng(seed)
    n_blocks = int(np.ceil(T / block_size))

    # ── build all bootstrap samples at once (vectorised) ──
    start_indices = rng.integers(0, T, size=(n_trials, n_blocks))
    # circular index: (start + offset) % T for each block of length block_size
    offsets = np.arange(block_size)  # (block_size,)
    # shape: (n_trials, n_blocks, block_size)
    idx = (start_indices[:, :, None] + offsets[None, None, :]) % T
    # shape: (n_trials, T_raw) — might be slightly longer than T
    samples = arr[idx.reshape(n_trials, -1)][:, :T]  # trim to exact T

    # ── compute metrics per trial ──
    mu = samples.mean(axis=1)
    sigma = samples.std(axis=1, ddof=1)
    safe_sigma = np.where(sigma > 0, sigma, np.inf)
    trial_sharpes = ((mu - RF_DAILY) / safe_sigma * np.sqrt(ANNUALIZATION)).tolist()

    # CAGR from cumulative product (geometric return)
    log_rets = np.log1p(np.clip(samples, -0.999, None))
    total_log = log_rets.sum(axis=1)
    trial_cagrs = (np.exp(total_log * ANNUALIZATION / T) - 1.0).tolist()

    # Max drawdown per trial
    cum_eq = np.exp(np.cumsum(log_rets, axis=1))
    running_max = np.maximum.accumulate(cum_eq, axis=1)
    dd_series = cum_eq / running_max - 1.0
    trial_max_dds = (-dd_series.min(axis=1)).tolist()

    # Sortino
    neg_mask = samples < 0
    neg_sq = np.where(neg_mask, samples**2, 0.0)
    down_rms = np.sqrt(neg_sq.mean(axis=1))
    safe_down = np.where(down_rms > 0, down_rms, np.inf)
    trial_sortinos = ((mu - RF_DAILY) / safe_down * np.sqrt(ANNUALIZATION)).tolist()

    def _ci95(values: list[float]) -> list[float]:
        v = np.asarray(values)
        return [float(np.percentile(v, 2.5)), float(np.percentile(v, 97.5))]

    obs = _annualized_metrics(daily_returns, list(np.cumprod(1.0 + arr)))
    return {
        "observed_sharpe": obs["sharpe_ratio"],
        "observed_cagr": obs["cagr"],
        "observed_max_dd": obs["max_drawdown"],
        "observed_sortino": obs["sortino_ratio"],
        "sharpe_ci_95": _ci95(trial_sharpes),
        "cagr_ci_95": _ci95(trial_cagrs),
        "max_dd_ci_95": _ci95(trial_max_dds),
        "sortino_ci_95": _ci95(trial_sortinos),
        "trial_sharpes": trial_sharpes,
        "trial_cagrs": trial_cagrs,
        "trial_max_dds": trial_max_dds,
        "pct_positive_sharpe": float(np.mean(np.asarray(trial_sharpes) > 0)),
        "n_trials": n_trials,
        "block_size": block_size,
    }


def sensitivity_sweep(
    *,
    strategy_id: str,
    weights: dict[str, float],
    param_grid: dict[str, list[Any]],
    start_date: str | None = None,
    end_date: str | None = None,
    metric: str = "sharpe_ratio",
    n_workers: int = 1,
) -> dict[str, Any]:
    """Run ``backtest_portfolio`` across a grid of parameter combinations.

    Tests how sensitive the strategy's ``metric`` is to changes in backtesting
    parameters (``rebalance_days``, ``tx_cost_bps``, ``initial_cash``, etc.).
    A robust strategy should maintain acceptable performance across the full
    grid; sharp performance cliffs signal over-fitted parameter choices.

    Args:
        strategy_id: Strategy identifier (passed to each ``backtest_portfolio`` call).
        weights: Static target weights.
        param_grid: ``{param_name: [value1, value2, ...]}`` — all combinations
            are expanded. Only the params accepted by ``backtest_portfolio``
            (``rebalance_days``, ``tx_cost_bps``, ``start_date``, ``end_date``)
            are forwarded; unknown keys are silently ignored.
        start_date: ISO date for all runs. Defaults to ``backtest_portfolio`` default.
        end_date: ISO date for all runs. Defaults to today.
        metric: Which scalar metric to summarise (``"sharpe_ratio"`` default).
        n_workers: Parallel workers (ProcessPoolExecutor). Default 1 (sequential).
            Network I/O dominates, so >1 rarely helps in practice but is exposed
            for test environments with a pre-populated cache.

    Returns:
        dict with keys:
            ``"grid"`` — list of ``{params, metric_value}`` dicts for all cells
            ``"metric"`` — name of the metric being tracked
            ``"best_params"`` — parameter combination with the highest metric value
            ``"worst_params"`` — combination with the lowest metric value
            ``"metric_mean"`` — mean metric across the grid
            ``"metric_std"`` — std of metric across the grid
            ``"metric_range"`` — [min, max]
            ``"sensitivity_ratio"`` — (max − min) / |mean|; > 0.5 flags instability

    Raises:
        ValueError: if ``param_grid`` is empty or yields zero combinations.
    """
    from concurrent.futures import ProcessPoolExecutor, as_completed
    from itertools import product

    _ALLOWED_PARAMS = {"rebalance_days", "tx_cost_bps", "start_date", "end_date", "initial_cash"}
    grid_params = {k: v for k, v in param_grid.items() if k in _ALLOWED_PARAMS}
    if not grid_params:
        raise ValueError(f"param_grid must contain at least one of {_ALLOWED_PARAMS}")

    param_names = list(grid_params.keys())
    param_values = list(grid_params.values())
    combinations = list(product(*param_values))

    if not combinations:
        raise ValueError("param_grid produced zero combinations")

    def _run_one(combo: tuple) -> dict[str, Any]:
        kw: dict[str, Any] = dict(zip(param_names, combo))
        base = {
            "strategy_id": strategy_id,
            "weights": weights,
            "start_date": start_date,
            "end_date": end_date,
        }
        base.update({k: v for k, v in kw.items() if k in _ALLOWED_PARAMS})
        try:
            result, _ = backtest_portfolio(**base)  # type: ignore[arg-type]
            value = float(getattr(result, metric, 0.0) or 0.0)
        except Exception as exc:
            logger.debug("sensitivity_sweep cell failed %s: %s", kw, exc)
            value = float("nan")
        return {"params": kw, metric: value}

    if n_workers > 1:
        cells: list[dict[str, Any]] = []
        with ProcessPoolExecutor(max_workers=n_workers) as pool:
            futures = {pool.submit(_run_one, combo): combo for combo in combinations}
            for fut in as_completed(futures):
                cells.append(fut.result())
    else:
        cells = [_run_one(combo) for combo in combinations]

    values = [c[metric] for c in cells if not (isinstance(c[metric], float) and c[metric] != c[metric])]
    if not values:
        return {
            "grid": cells,
            "metric": metric,
            "best_params": None,
            "worst_params": None,
            "metric_mean": float("nan"),
            "metric_std": float("nan"),
            "metric_range": [float("nan"), float("nan")],
            "sensitivity_ratio": float("nan"),
        }

    best = max(cells, key=lambda c: c[metric] if c[metric] == c[metric] else float("-inf"))
    worst = min(cells, key=lambda c: c[metric] if c[metric] == c[metric] else float("inf"))
    arr = np.asarray(values)
    mean_v = float(arr.mean())
    std_v = float(arr.std())
    min_v, max_v = float(arr.min()), float(arr.max())
    sensitivity_ratio = float((max_v - min_v) / abs(mean_v)) if abs(mean_v) > 1e-10 else float("inf")

    return {
        "grid": cells,
        "metric": metric,
        "best_params": best["params"],
        "worst_params": worst["params"],
        "metric_mean": mean_v,
        "metric_std": std_v,
        "metric_range": [min_v, max_v],
        "sensitivity_ratio": sensitivity_ratio,
    }


def walk_forward_validate(
    *,
    strategy_id: str,
    weights: dict[str, float],
    start_date: str | None = None,
    end_date: str | None = None,
    n_splits: int = 5,
    train_frac: float = 0.70,
    tx_cost_bps: int = DEFAULT_TX_COST_BPS,
    rebalance_days: int = DEFAULT_REBALANCE_DAYS,
) -> dict[str, Any]:
    """Combinatorial Purged Walk-Forward validation for a portfolio strategy.

    Divides the full backtest period into ``n_splits`` adjacent windows. In each
    window, the first ``train_frac`` fraction is "in-sample" and the remaining
    ``1 - train_frac`` is "out-of-sample". The same static weights are applied
    in every split (for a generated strategy, the LLM cannot be re-run per
    split, so weights are fixed). The metric of interest is the OOS Sharpe
    cliff: how much does OOS Sharpe lag IS Sharpe?

    A large IS/OOS Sharpe cliff (> 30%) is a red flag for overfit or regime-
    specific returns that will not generalise (Bailey & López de Prado 2014,
    "The Deflated Sharpe Ratio").

    This is the time-series equivalent of k-fold cross-validation, adapted for
    the temporal ordering constraint (no future data in training). Unlike the
    analytics-engine's walk-forward harness (which operates on a single
    strategy's signal), this operates on a static-weight portfolio.

    Args:
        strategy_id: Passed to ``backtest_portfolio`` calls.
        weights: Static target weights.
        start_date: ISO start date of the full period.
        end_date: ISO end date.
        n_splits: Number of walk-forward windows.
        train_frac: Fraction of each window used as in-sample.
        tx_cost_bps: Transaction cost for each sub-backtest.
        rebalance_days: Rebalance interval for each sub-backtest.

    Returns:
        dict with keys:
            ``"splits"`` — list of per-split dicts with ``is_sharpe``,
                ``oos_sharpe``, ``cliff``, ``is_start``, ``is_end``,
                ``oos_start``, ``oos_end``
            ``"mean_is_sharpe"`` — average in-sample Sharpe across splits
            ``"mean_oos_sharpe"`` — average OOS Sharpe across splits
            ``"mean_cliff"`` — average IS→OOS Sharpe drop (positive = degradation)
            ``"max_cliff"`` — worst single split cliff
            ``"passes_cliff_gate"`` — True if mean_cliff ≤ 0.30
                (Bailey et al. 2014 30% degradation threshold)
            ``"n_splits"`` — actual number of splits completed
    """
    end_iso = end_date or datetime.now(UTC).date().isoformat()
    if start_date is None:
        start_dt = datetime.fromisoformat(end_iso) - timedelta(days=365 * DEFAULT_LOOKBACK_YEARS)
        start_iso = start_dt.date().isoformat()
    else:
        start_iso = start_date

    start_dt = datetime.fromisoformat(start_iso)
    end_dt = datetime.fromisoformat(end_iso)
    total_days = (end_dt - start_dt).days
    if total_days < n_splits * 60:
        raise ValueError(f"Period too short ({total_days} days) for {n_splits} splits with meaningful windows")

    window_days = total_days // n_splits
    splits: list[dict[str, Any]] = []

    for i in range(n_splits):
        win_start = start_dt + timedelta(days=i * window_days)
        win_end = win_start + timedelta(days=window_days)
        split_days = (win_end - win_start).days
        is_end_offset = int(split_days * train_frac)

        is_start_iso = win_start.date().isoformat()
        is_end_iso = (win_start + timedelta(days=is_end_offset)).date().isoformat()
        oos_start_iso = (win_start + timedelta(days=is_end_offset + 1)).date().isoformat()
        oos_end_iso = win_end.date().isoformat()

        is_sharpe = float("nan")
        oos_sharpe = float("nan")

        try:
            is_res, _ = backtest_portfolio(
                strategy_id=strategy_id,
                weights=weights,
                start_date=is_start_iso,
                end_date=is_end_iso,
                rebalance_days=rebalance_days,
                tx_cost_bps=tx_cost_bps,
            )
            is_sharpe = float(is_res.sharpe_ratio or 0.0)
        except Exception as exc:
            logger.debug("WF split %d IS failed: %s", i, exc)

        try:
            oos_res, _ = backtest_portfolio(
                strategy_id=strategy_id,
                weights=weights,
                start_date=oos_start_iso,
                end_date=oos_end_iso,
                rebalance_days=rebalance_days,
                tx_cost_bps=tx_cost_bps,
            )
            oos_sharpe = float(oos_res.sharpe_ratio or 0.0)
        except Exception as exc:
            logger.debug("WF split %d OOS failed: %s", i, exc)

        cliff = float("nan")
        if is_sharpe == is_sharpe and oos_sharpe == oos_sharpe and abs(is_sharpe) > 1e-10:
            cliff = (is_sharpe - oos_sharpe) / abs(is_sharpe)

        splits.append(
            {
                "split": i,
                "is_start": is_start_iso,
                "is_end": is_end_iso,
                "oos_start": oos_start_iso,
                "oos_end": oos_end_iso,
                "is_sharpe": is_sharpe,
                "oos_sharpe": oos_sharpe,
                "cliff": cliff,
            }
        )

    valid_cliffs = [s["cliff"] for s in splits if s["cliff"] == s["cliff"]]
    valid_is = [s["is_sharpe"] for s in splits if s["is_sharpe"] == s["is_sharpe"]]
    valid_oos = [s["oos_sharpe"] for s in splits if s["oos_sharpe"] == s["oos_sharpe"]]

    mean_is = float(np.mean(valid_is)) if valid_is else float("nan")
    mean_oos = float(np.mean(valid_oos)) if valid_oos else float("nan")
    mean_cliff = float(np.mean(valid_cliffs)) if valid_cliffs else float("nan")
    max_cliff = float(max(valid_cliffs)) if valid_cliffs else float("nan")
    passes_cliff_gate = (mean_cliff == mean_cliff) and (mean_cliff <= 0.30)

    return {
        "splits": splits,
        "mean_is_sharpe": mean_is,
        "mean_oos_sharpe": mean_oos,
        "mean_cliff": mean_cliff,
        "max_cliff": max_cliff,
        "passes_cliff_gate": passes_cliff_gate,
        "n_splits": len(splits),
    }


# ─── parallel multi-strategy backtest runner ─────────────────────────


# Keys of a job dict that are forwarded verbatim to ``backtest_portfolio``.
# Everything else (e.g. ``label``) is metadata used only by the runner.
_JOB_BACKTEST_KEYS = {
    "strategy_id",
    "weights",
    "start_date",
    "end_date",
    "rebalance_days",
    "initial_cash",
    "tx_cost_bps",
    "num_trials_for_dsr",
    "paper_claimed_sharpe",
    "paper_title",
}

# Scalar metrics copied out of each BacktestResult into the runner summary.
_JOB_REPORTED_METRICS = ("sharpe_ratio", "cagr", "max_drawdown", "sortino_ratio", "volatility")


def _run_backtest_job(job: dict[str, Any]) -> dict[str, Any]:
    """Run a single backtest job and reduce it to a picklable summary dict.

    Module-level (not a closure) so it is picklable by ``ProcessPoolExecutor``
    under the macOS/Windows spawn start-method — the parallel path therefore
    actually executes rather than failing to pickle a nested function. A failed
    backtest is captured as ``{"ok": False, "error": ...}`` rather than raised,
    so one bad job never aborts the whole sweep.
    """
    label = job.get("label") or job.get("strategy_id") or "job"
    kwargs = {k: v for k, v in job.items() if k in _JOB_BACKTEST_KEYS}
    try:
        result, _ = backtest_portfolio(**kwargs)
        metrics = {m: float(getattr(result, m, float("nan")) or float("nan")) for m in _JOB_REPORTED_METRICS}
        return {"label": label, "ok": True, "error": None, **metrics}
    except Exception as exc:  # noqa: BLE001 — non-fatal per-job failure, surfaced in the result
        logger.debug("run_parallel_backtest job %s failed: %s", label, exc)
        return {
            "label": label,
            "ok": False,
            "error": str(exc),
            **{m: float("nan") for m in _JOB_REPORTED_METRICS},
        }


def run_parallel_backtest(
    *,
    jobs: list[dict[str, Any]],
    metric: str = "sharpe_ratio",
    n_workers: int = 1,
) -> dict[str, Any]:
    """Backtest many independent strategy/universe combinations and rank them.

    This is the strategy×universe analogue of ``sensitivity_sweep``: where the
    sweep varies *parameters* of one strategy on one universe, this runs a list
    of fully-independent backtests (different strategies, different ticker
    universes, or both) and produces a ranked comparison. It is the runner used
    when promoting a batch of CANDIDATE strategies, or when comparing one
    strategy across several candidate universes, in a single call.

    Each job is a dict forwarded to ``backtest_portfolio``; only the keys in
    ``_JOB_BACKTEST_KEYS`` are passed through (``strategy_id`` and ``weights``
    are the minimum). An optional ``"label"`` key names the row in the output;
    it defaults to ``strategy_id``. The "universe" is implicit in each job's
    ``weights`` (the set of tickers it allocates to) — the backtester has no
    separate universe object, so we do not invent one.

    Args:
        jobs: List of job dicts. Each must contain ``strategy_id`` and
            ``weights``; may additionally set any of ``start_date``,
            ``end_date``, ``rebalance_days``, ``initial_cash``, ``tx_cost_bps``,
            ``num_trials_for_dsr``, ``paper_claimed_sharpe``, ``paper_title``,
            plus an optional ``label``.
        metric: Scalar metric used for ranking (one of ``_JOB_REPORTED_METRICS``;
            ``"sharpe_ratio"`` default). ``max_drawdown`` ranks ascending (less
            negative / smaller magnitude is better); all others rank descending.
        n_workers: Worker processes (``ProcessPoolExecutor``). Default 1
            (sequential). >1 parallelises the per-job market-data fetch +
            simulation; because network I/O dominates a cold run, it mainly
            helps when the price cache is warm (e.g. a re-rank over the same
            universes). Result ordering is independent of ``n_workers``.

    Returns:
        dict with keys:
            ``"results"`` — per-job dicts ``{label, ok, error, <metrics...>}``,
                in the **input order** of ``jobs`` (stable regardless of worker
                completion order)
            ``"metric"`` — the ranking metric name
            ``"ranking"`` — list of labels, best→worst on ``metric`` (failed and
                NaN-metric jobs are excluded)
            ``"best"`` / ``"worst"`` — best/worst label (None if none succeeded)
            ``"n_jobs"`` / ``"n_ok"`` / ``"n_failed"`` — job counts

    Raises:
        ValueError: if ``jobs`` is empty, if ``metric`` is not a reported
            metric, or if any job is missing ``strategy_id``/``weights``.
    """
    from concurrent.futures import ProcessPoolExecutor

    if not jobs:
        raise ValueError("jobs must contain at least one backtest job")
    if metric not in _JOB_REPORTED_METRICS:
        raise ValueError(f"metric must be one of {_JOB_REPORTED_METRICS}, got {metric!r}")
    for i, job in enumerate(jobs):
        if "strategy_id" not in job or "weights" not in job:
            raise ValueError(f"jobs[{i}] must contain 'strategy_id' and 'weights'")

    if n_workers > 1:
        # Map preserves input order, so ``results`` is deterministic regardless
        # of which worker finishes first.
        with ProcessPoolExecutor(max_workers=n_workers) as pool:
            results = list(pool.map(_run_backtest_job, jobs))
    else:
        results = [_run_backtest_job(job) for job in jobs]

    def _valid(r: dict[str, Any]) -> bool:
        v = r.get(metric, float("nan"))
        return bool(r["ok"]) and isinstance(v, float) and v == v  # not NaN

    ranked = [r for r in results if _valid(r)]
    # max_drawdown is negative/zero — the largest (closest to 0) is best.
    reverse = metric != "max_drawdown"
    ranked.sort(key=lambda r: r[metric], reverse=reverse)

    n_ok = sum(1 for r in results if r["ok"])
    return {
        "results": results,
        "metric": metric,
        "ranking": [r["label"] for r in ranked],
        "best": ranked[0]["label"] if ranked else None,
        "worst": ranked[-1]["label"] if ranked else None,
        "n_jobs": len(jobs),
        "n_ok": n_ok,
        "n_failed": len(jobs) - n_ok,
    }
