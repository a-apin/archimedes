"""Risk analysis API endpoints.

Dedicated risk router that aggregates persisted strategy backtests and
on-chain vault holdings into portfolio-level risk summaries.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from functools import lru_cache

import numpy as np
import scipy.stats
from fastapi import APIRouter, Depends, Request

from archimedes.api.risk_schemas import (
    CVaRLevel,
    PortfolioCVaRResponse,
    PortfolioGreeksResponse,
    PortfolioRiskResponse,
    RiskProfileBand,
    RiskProfileBandsResponse,
    StrategyGreeks,
    StrategyRiskSummary,
)
from archimedes.feature_flags import require_quant_feature
from archimedes.services.strategy_provider import LocalStrategyProvider, default_provider

logger = logging.getLogger(__name__)

risk_router = APIRouter(prefix="/api/risk", tags=["risk"])


@lru_cache(maxsize=1)
def _strategy_provider() -> LocalStrategyProvider:
    """Lazily-constructed, cached strategy provider (see _route_helpers.py's
    strategy_provider() for the full rationale — this module has its own
    copy of the singleton rather than importing the shared one, so it gets
    its own lazy accessor). Call sites: ``_strategy_provider().foo()``."""
    return default_provider()


def _all_strategies(request: Request) -> list:
    """Curated ∪ generated strategy resolution shared by the risk endpoints
    (the "unify source" decouple — docs/CURATED-STRATEGY-DECOUPLE-AND-CONSOLIDATE-2026-07-08.md
    Part A). Generated strategies are included via the SAME #850 ownership-visibility
    rule as rest of app (public OR canonical owner, with linked-wallet legacy fallback)
    — never curated-only, so a generated strategy's portfolio risk is no longer
    silently misreported as mock. Both halves then read metrics through the SAME
    source, ``_strategy_provider().get_backtest_result(id)`` (queries the shared
    ``backtest_results`` table by id regardless of curated/generated origin) — a
    generated strategy with no persisted backtest yet contributes ``None`` metrics
    exactly like an un-backtested curated one, never a fabricated stand-in.

    Best-effort: any failure resolving the generated half degrades to curated-only
    (today's behavior) rather than breaking the risk surface.
    """
    strategies: list = list(_strategy_provider().list_strategies())
    try:
        from archimedes.api.account_auth import get_current_user
        from archimedes.api.wallet_routes import get_linked_wallet_address
        from archimedes.api.strategies_routes import _generated_strategy_responses
        from archimedes.db import get_session

        caller = get_linked_wallet_address(request)
        user = get_current_user(request)
        with get_session() as session:
            strategies.extend(_generated_strategy_responses(session, caller, user.id if user else None))
    except Exception:
        logger.debug("risk: generated strategy resolution failed (non-fatal)", exc_info=True)
    return strategies


def _status_str(s) -> str:
    """Status value for either a curated ``Strategy`` (``StrategyStatus`` enum)
    or a generated ``StrategyResponse`` (plain string) — the two object types
    ``_all_strategies`` can hand back."""
    st = getattr(s, "status", None)
    return st.value if hasattr(st, "value") else (st or "unknown")


# ── Loud-fallback telemetry (T0.5) ───────────────────────────
# The Risk Analysis UI renders correlation, drawdown and rolling-Sharpe panels
# from client-side ``mockReturns`` whenever no live source backs them, and the
# CVaR/Greeks endpoints return empty/zero levels when no persisted backtest has
# an equity curve. That mock data must NOT be silently presented as real. This
# probe reports whether the risk surface is backed by live persisted backtests
# or is falling back to mock/placeholder data, so ``/health`` can surface it.


@dataclass(frozen=True)
class RiskDataHealth:
    """Health diagnostic for the risk-analysis data surface (T0.5)."""

    status: str  # live | mock
    reason: str = ""


def risk_data_health() -> RiskDataHealth:
    """Report whether the risk surface is backed by live data or mock fallback.

    - ``live``: at least one persisted backtest carries an equity curve, so the
      CVaR/series surface is derived from real strategy history.
    - ``mock``: no persisted equity data exists, so the risk UI renders from
      client-side ``mockReturns`` placeholders. Visible-not-silent: the product
      would otherwise present placeholder tail-risk numbers as if they were real.

    Never raises — a provider/DB hiccup degrades to ``mock`` (the honest
    conservative default) with the exception surfaced in the reason.
    """
    try:
        strategies = _strategy_provider().list_strategies()
        live_curves = sum(
            1
            for s in strategies
            if (bt := _strategy_provider().get_backtest_result(s.id)) is not None and len(bt.equity_curve) >= 2
        )
    except Exception as exc:  # provider/DB unavailable → honest mock fallback
        logger.warning(
            "Risk data probe failed — assuming mock surface: %s",
            exc,
            extra={"event": "risk_data_mock", "reason": "probe_failed", "surface": "risk_analysis"},
        )
        return RiskDataHealth(status="mock", reason=f"risk data probe failed ({exc})")

    if live_curves > 0:
        return RiskDataHealth(
            status="live",
            reason=f"{live_curves} persisted backtest(s) with equity curves",
        )

    logger.warning(
        "Risk surface degraded to mock data — no persisted backtest equity curves; "
        "UI correlation/drawdown/rolling-Sharpe panels render placeholder mockReturns",
        extra={"event": "risk_data_mock", "reason": "no_equity_curves", "surface": "risk_analysis"},
    )
    return RiskDataHealth(
        status="mock",
        reason="no persisted backtest equity curves — risk UI renders placeholder mock data",
    )


# ── Risk Profile Bands ───────────────────────────────────────
# Thresholds used to classify a portfolio into one of five tiers.
# These are the same five levels as `archimedes.models.portfolio.RiskProfile`.

RISK_BANDS: list[dict] = [
    {
        "label": "fixed_income",
        "max_dd": 0.05,
        "target_sharpe": 0.30,
        "max_vol": 0.04,
        "color": "#3B82F6",  # blue
    },
    {
        "label": "conservative",
        "max_dd": 0.10,
        "target_sharpe": 0.50,
        "max_vol": 0.10,
        "color": "#22C55E",  # green
    },
    {
        "label": "moderate",
        "max_dd": 0.20,
        "target_sharpe": 0.80,
        "max_vol": 0.18,
        "color": "#D4A853",  # amber/gold
    },
    {
        "label": "aggressive",
        "max_dd": 0.35,
        "target_sharpe": 1.00,
        "max_vol": 0.30,
        "color": "#F97316",  # orange
    },
    {
        "label": "hyper_risky",
        "max_dd": 0.60,
        "target_sharpe": 1.20,
        "max_vol": 0.50,
        "color": "#EF4444",  # red
    },
]


def _classify_risk_profile(worst_max_dd: float) -> str:
    """Classify portfolio into a risk tier based on worst strategy max drawdown.

    Picks the lowest band whose max_dd >= worst_max_dd.
    Falls back to hyper_risky if worst_max_dd exceeds all bands.
    """
    for band in RISK_BANDS:
        if worst_max_dd <= band["max_dd"]:
            return band["label"]
    return "hyper_risky"


def _classify_concentration(hhi: float) -> str:
    """Qualitative label for Herfindahl-Hirschman Index."""
    if hhi < 0.15:
        return "diversified"
    if hhi < 0.25:
        return "moderate"
    return "concentrated"


def _derive_risk_level(sharpe: float | None) -> str:
    """Simple risk level from Sharpe ratio."""
    if sharpe is None:
        return "High"
    if sharpe > 1.0:
        return "Low"
    if sharpe > 0.5:
        return "Medium"
    return "High"


# ── Endpoints ────────────────────────────────────────────────


@risk_router.get("/profiles", response_model=RiskProfileBandsResponse)
async def get_risk_profiles():
    """Return the four risk-profile bands with threshold boundaries.

    Used by the frontend to render the risk-profile-vs-actual visualization.
    """
    return RiskProfileBandsResponse(
        bands=[RiskProfileBand(**b) for b in RISK_BANDS],
    )


@risk_router.get("/portfolio", response_model=PortfolioRiskResponse)
async def get_portfolio_risk(request: Request):
    """Aggregate portfolio-level risk metrics from persisted backtests.

    Computes:
      - Avg Sharpe, worst max drawdown, avg correlation to SPY, best Calmar
      - Per-strategy derived volatility (abs(cagr) / sharpe when both present)
      - Concentration HHI from on-chain vault holdings (if available)
      - Actual risk profile classification based on worst max DD

    Strategy resolution is curated ∪ generated (visible generated strategies —
    see ``_all_strategies``), not curated-only.
    """
    strategies = _all_strategies(request)

    # ── Per-strategy summaries ───────────────────────────────
    summaries: list[StrategyRiskSummary] = []
    sharpe_vals: list[float] = []
    dd_vals: list[float] = []
    corr_vals: list[float] = []
    calmar_vals: list[float] = []
    vol_vals: list[float] = []

    for s in strategies:
        bt = _strategy_provider().get_backtest_result(s.id)

        sharpe = bt.sharpe_ratio if bt else None
        max_dd = bt.max_drawdown if bt else None
        cagr = bt.cagr if bt else None
        calmar = bt.calmar_ratio if bt else None
        corr = bt.correlation_to_spy if bt else None

        # Derive annualized volatility: σ ≈ |CAGR| / Sharpe
        # (rough approximation from Sharpe = (r - rf) / σ, assuming rf ≈ 0).
        # Floor the Sharpe denominator: a sub-normal-but-positive value (e.g.
        # 1e-300) passes `> 0` yet yields a non-finite ratio that serializes to
        # `null` and breaks the UI. Drop any non-finite result.
        volatility = None
        if sharpe is not None and cagr is not None and sharpe > 1e-4:
            raw_vol = abs(cagr) / sharpe
            volatility = raw_vol if math.isfinite(raw_vol) else None

        if sharpe is not None:
            sharpe_vals.append(sharpe)
        if max_dd is not None:
            dd_vals.append(max_dd)
        if corr is not None:
            corr_vals.append(corr)
        if calmar is not None:
            calmar_vals.append(calmar)
        if volatility is not None:
            vol_vals.append(volatility)

        summaries.append(
            StrategyRiskSummary(
                id=s.id,
                paper_title=s.paper_title,
                status=_status_str(s),
                sharpe_ratio=sharpe,
                volatility=volatility,
                max_drawdown=max_dd,
                cagr=cagr,
                win_rate=bt.win_rate if bt else None,
                calmar_ratio=calmar,
                correlation_to_spy=corr,
                risk_level=_derive_risk_level(sharpe),
            )
        )

    # ── Aggregates ───────────────────────────────────────────
    avg_sharpe = sum(sharpe_vals) / len(sharpe_vals) if sharpe_vals else 0.0
    worst_dd = max(dd_vals) if dd_vals else 0.0
    avg_corr = sum(corr_vals) / len(corr_vals) if corr_vals else 0.0
    best_calmar = max(calmar_vals) if calmar_vals else 0.0
    avg_vol = sum(vol_vals) / len(vol_vals) if vol_vals else 0.0

    # ── Concentration HHI (from on-chain vault holdings) ─────
    # Try to read on-chain vault metrics for an AUM-weighted HHI.
    # Fall back to equal-weight across strategies if no vaults.
    hhi = 0.0
    holding_count = 0

    try:
        from archimedes.chain.executor import chain_executor

        vault_addresses = await chain_executor.get_all_vaults()
        if vault_addresses:
            # Use per-vault AUM as weights to compute concentration
            total_aum = 0.0
            vault_aums: list[float] = []

            for vault_addr in vault_addresses:
                try:
                    metrics = await chain_executor.get_vault_metrics(vault_addr)
                    aum = metrics.get("total_aum_usdc", 0.0)
                    if aum > 0:
                        vault_aums.append(aum)
                        total_aum += aum
                except Exception:
                    continue

            if total_aum > 0 and vault_aums:
                holding_count = len(vault_aums)
                weights = [a / total_aum for a in vault_aums]
                hhi = sum(w * w for w in weights)
    except Exception:
        logger.debug("portfolio concentration calc failed", exc_info=True)

    # Fallback: equal weight across strategies
    if hhi == 0.0 and strategies:
        holding_count = len(strategies)
        hhi = 1.0 / len(strategies)  # equal weight HHI

    return PortfolioRiskResponse(
        strategy_count=len(strategies),
        avg_sharpe=round(avg_sharpe, 3),
        worst_max_dd=round(worst_dd, 4),
        avg_correlation_spy=round(avg_corr, 3),
        best_calmar=round(best_calmar, 3),
        avg_volatility=round(avg_vol, 4),
        concentration_hhi=round(hhi, 4),
        concentration_label=_classify_concentration(hhi),
        holding_count=holding_count,
        actual_risk_profile=_classify_risk_profile(worst_dd),
        strategies=summaries,
    )


@risk_router.get("/cvar", response_model=PortfolioCVaRResponse, dependencies=[Depends(require_quant_feature)])
async def get_portfolio_cvar(request: Request):
    """Portfolio-level CVaR at 90, 95, 99% confidence from persisted backtests.

    Daily returns are derived from equity_curve via pct_change. Strategies are
    equally weighted. Returns 200 with empty levels if no equity data is available.

    Strategy resolution is curated ∪ generated (see ``_all_strategies``). A
    generated strategy with no persisted equity curve yet is skipped exactly
    like an un-backtested curated one — never a fabricated CVaR.
    """
    strategies = _all_strategies(request)

    all_returns: list[np.ndarray] = []
    for s in strategies:
        bt = _strategy_provider().get_backtest_result(s.id)
        if bt is None or len(bt.equity_curve) < 2:
            continue
        curve = np.array(bt.equity_curve, dtype=float)
        # pct_change from equity levels; skip first NaN
        rets = np.diff(curve) / curve[:-1]
        if len(rets) > 0:
            all_returns.append(rets)

    if not all_returns:
        return PortfolioCVaRResponse(
            strategy_count=len(strategies),
            lookback_days=0,
            levels=[
                CVaRLevel(
                    confidence=c,
                    var_historical=0.0,
                    cvar_historical=0.0,
                    var_parametric=0.0,
                    cvar_parametric=0.0,
                    fat_tails=False,
                    sample_size=0,
                )
                for c in (0.90, 0.95, 0.99)
            ],
        )

    # Equal-weight portfolio daily returns: average across strategies
    min_len = min(len(r) for r in all_returns)
    stacked = np.stack([r[-min_len:] for r in all_returns], axis=1)
    portfolio_returns = stacked.mean(axis=1)

    n = len(portfolio_returns)
    mu = float(portfolio_returns.mean())
    # std(ddof=1) is 0/0 -> NaN for a single daily return, and a bare NaN
    # serializes as an invalid JSON token that breaks strict parsers on the
    # Risk page. Treat a single-sample portfolio as zero-volatility, and guard
    # against any stray NaN/inf in the multi-sample path too.
    sigma = float(np.nan_to_num(portfolio_returns.std(ddof=1), nan=0.0)) if n > 1 else 0.0

    levels: list[CVaRLevel] = []
    for conf in (0.90, 0.95, 0.99):
        threshold = np.percentile(portfolio_returns, (1 - conf) * 100)
        tail = portfolio_returns[portfolio_returns <= threshold]
        cvar_hist = float(-tail.mean()) if len(tail) > 0 else float(-threshold)
        var_hist = float(-threshold)

        z = scipy.stats.norm.ppf(1 - conf)
        var_param = float(-(mu + z * sigma))
        # E[X | X <= mu + z*sigma] for normal = mu - sigma * phi(z) / (1 - conf)
        cvar_param = float(-(mu - sigma * scipy.stats.norm.pdf(z) / (1 - conf)))

        levels.append(
            CVaRLevel(
                confidence=conf,
                var_historical=round(var_hist, 6),
                cvar_historical=round(cvar_hist, 6),
                var_parametric=round(var_param, 6),
                cvar_parametric=round(cvar_param, 6),
                fat_tails=bool(cvar_hist > cvar_param),
                sample_size=n,
            )
        )

    return PortfolioCVaRResponse(
        strategy_count=len(strategies),
        lookback_days=n,
        levels=levels,
    )


def _strategy_delta(sharpe: float, cagr: float, tau: float = 30 / 365, r: float = 0.045, q: float = 0.02) -> float:
    """ATM call delta for a strategy whose implied vol is abs(cagr)/sharpe."""
    vol = abs(cagr) / sharpe if sharpe > 0 else 0.20
    return _bs_atm_greeks(vol, tau, r, q)["delta"]


def _bs_atm_greeks(sigma: float, tau: float, r: float, q: float) -> dict[str, float]:
    """Black-Scholes ATM call Greeks for spot=strike=1."""
    S = K = 1.0
    sqrt_tau = math.sqrt(tau)
    d1 = (math.log(S / K) + (r - q + 0.5 * sigma**2) * tau) / (sigma * sqrt_tau)
    d2 = d1 - sigma * sqrt_tau
    N = scipy.stats.norm.cdf
    n_pdf = scipy.stats.norm.pdf
    delta = math.exp(-q * tau) * N(d1)
    gamma = math.exp(-q * tau) * n_pdf(d1) / (S * sigma * sqrt_tau)
    theta = (
        -(S * sigma * math.exp(-q * tau) * n_pdf(d1)) / (2 * sqrt_tau)
        - r * K * math.exp(-r * tau) * N(d2)
        + q * S * math.exp(-q * tau) * N(d1)
    ) / 365
    vega = S * math.exp(-q * tau) * n_pdf(d1) * sqrt_tau / 100
    rho = K * tau * math.exp(-r * tau) * N(d2) / 100
    return {"delta": delta, "gamma": gamma, "theta": theta, "vega": vega, "rho": rho}


@risk_router.get("/greeks", response_model=PortfolioGreeksResponse, dependencies=[Depends(require_quant_feature)])
async def get_portfolio_greeks(request: Request):
    """ATM call Black-Scholes Greeks per strategy and equal-weight portfolio aggregate.

    Vol is derived from Sharpe + CAGR: sigma = abs(CAGR) / Sharpe.
    Falls back to 0.20 when not derivable. Returns 200 with zeros when no strategies exist.

    Strategy resolution is curated ∪ generated (see ``_all_strategies``).
    """
    _R = 0.045
    _Q = 0.02
    _TAU = 30 / 365
    _FALLBACK_VOL = 0.20

    strategies = _all_strategies(request)

    strategy_greeks: list[StrategyGreeks] = []
    for s in strategies:
        bt = _strategy_provider().get_backtest_result(s.id)
        sharpe = bt.sharpe_ratio if bt else None
        cagr = bt.cagr if bt else None

        implied_vol = _FALLBACK_VOL
        if sharpe is not None and cagr is not None and sharpe > 1e-4:
            raw_vol = abs(cagr) / sharpe
            if math.isfinite(raw_vol) and raw_vol > 0:
                implied_vol = raw_vol

        g = _bs_atm_greeks(implied_vol, _TAU, _R, _Q)
        strategy_greeks.append(
            StrategyGreeks(
                strategy_id=s.id,
                paper_title=s.paper_title,
                implied_vol=round(implied_vol, 6),
                delta=round(g["delta"], 6),
                gamma=round(g["gamma"], 6),
                theta=round(g["theta"], 6),
                vega=round(g["vega"], 6),
                rho=round(g["rho"], 6),
                weight=round(1.0 / len(strategies), 6) if strategies else 0.0,
            )
        )

    n = len(strategy_greeks)
    if n == 0:
        return PortfolioGreeksResponse(
            strategy_count=0,
            time_horizon_days=30,
            risk_free_rate=_R,
            implied_vol_assumption=_FALLBACK_VOL,
            strategies=[],
            portfolio_delta=0.0,
            portfolio_gamma=0.0,
            portfolio_theta=0.0,
            portfolio_vega=0.0,
            portfolio_rho=0.0,
        )

    w = 1.0 / n
    p_delta = sum(g.delta * w for g in strategy_greeks)
    p_gamma = sum(g.gamma * w for g in strategy_greeks)
    p_theta = sum(g.theta * w for g in strategy_greeks)
    p_vega = sum(g.vega * w for g in strategy_greeks)
    p_rho = sum(g.rho * w for g in strategy_greeks)
    avg_vol = sum(g.implied_vol for g in strategy_greeks) / n

    return PortfolioGreeksResponse(
        strategy_count=n,
        time_horizon_days=30,
        risk_free_rate=_R,
        implied_vol_assumption=round(avg_vol, 6),
        strategies=strategy_greeks,
        portfolio_delta=round(p_delta, 6),
        portfolio_gamma=round(p_gamma, 6),
        portfolio_theta=round(p_theta, 6),
        portfolio_vega=round(p_vega, 6),
        portfolio_rho=round(p_rho, 6),
    )
