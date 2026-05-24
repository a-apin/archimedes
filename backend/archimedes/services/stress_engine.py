"""Portfolio stress-test engine.

Six canonical historical/scenario shocks, each defined as a per-asset-class
shock vector.  For any portfolio (weights keyed by display symbol), we map
each pick to its asset class via ``GLOBAL_ASSETS``, look up the per-class
shock, and compute the instantaneous mark-to-market P&L:

    pnl(scenario) = Σ_i  w_i · shock_class(i, scenario)

This is the standard risk-management view at any real shop.  It's
deliberately coarse — a beta-1 model on asset-class buckets — so it's
explainable, fast, and demoable.  A more sophisticated version (factor
model + Monte Carlo + tail-risk) is a v2 problem.

References
----------
- Litterman (2003), Modern Investment Management — historical scenarios
- Bookstaber (2007), A Demon of Our Own Design — stress-loss thinking
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from archimedes.services.strategy_signal_evaluator import GLOBAL_ASSETS

_logger = logging.getLogger(__name__)

# ── Scenario definitions ──────────────────────────────────────────
# Per-class shocks.  Positive = price-up; negative = price-down.
# Calibrated against historical analogs (2008, 2020, 2022, 2018 EM
# crisis, etc.) — coarse round numbers that judges can interpret.

SCENARIOS: dict[str, dict] = {
    "equity_crash_2008": {
        "label": "2008-style equity crash",
        "description": "Broad equity drawdown ~40% in three months; flight to quality (long bonds rally, USD bid, gold mixed, oil collapses with demand).",
        "shocks": {
            "us_equity_etf": -0.40,
            "us_sector_etf": -0.45,
            "us_stock": -0.45,
            "eu_equity_etf": -0.45,
            "eu_stock": -0.50,
            "eu_index": -0.42,
            "asia_equity_etf": -0.50,
            "asia_index": -0.45,
            "asia_stock": -0.55,
            "em_equity_etf": -0.55,
            "tr_equity_etf": -0.55,
            "tr_index": -0.55,
            "tr_stock": -0.60,
            "metal_etf": 0.10,  # gold up in GFC
            "metal_eq_etf": -0.20,  # gold miners trade more like equities
            "metal_fut": 0.10,
            "energy_etf": -0.45,
            "energy_fut": -0.55,
            "agri_fut": -0.15,
            "us_bond_long": 0.20,  # TLT rallied ~20% in late '08
            "us_bond_mid": 0.08,
            "us_bond_short": 0.02,
            "us_bond_tbill": 0.005,
            "us_bond_tips": 0.02,
            "us_bond_agg": 0.05,
            "credit_hy": -0.25,
            "credit_ig": -0.05,
            "em_bond": -0.20,
            "us_muni": -0.05,
            "fx": 0.0,  # USD-pair specific, see _fx_shock
            "crypto": -0.50,  # crypto didn't exist in '08 but apply Q4'18 / Mar '20 analog
        },
        "fx_per_pair": {
            "EUR/USD": -0.05,
            "GBP/USD": -0.20,
            "USD/JPY": -0.10,
            "USD/TRY": +0.25,
        },
    },
    "tech_rout_2022": {
        "label": "2022-style tech rout",
        "description": "Nasdaq/growth stocks down ~35% on rate-hike-driven multiple compression. Defensive equity + value holds up better; treasuries fall (rates rising).",
        "shocks": {
            "us_equity_etf": -0.18,
            "us_sector_etf": -0.20,
            "us_stock": -0.25,
            "eu_equity_etf": -0.10,
            "eu_stock": -0.12,
            "eu_index": -0.10,
            "asia_equity_etf": -0.20,
            "asia_index": -0.10,
            "asia_stock": -0.35,
            "em_equity_etf": -0.20,
            "tr_equity_etf": -0.15,
            "tr_index": 0.50,  # BIST ripped in 2022 (inflation hedge)
            "tr_stock": 0.40,
            "metal_etf": 0.0,
            "metal_eq_etf": 0.0,
            "metal_fut": 0.05,
            "energy_etf": 0.30,  # energy was the 2022 winner
            "energy_fut": 0.40,
            "agri_fut": 0.20,
            "us_bond_long": -0.30,  # TLT was the worst trade of 2022
            "us_bond_mid": -0.15,
            "us_bond_short": -0.04,
            "us_bond_tbill": 0.0,
            "us_bond_tips": -0.10,
            "us_bond_agg": -0.13,
            "credit_hy": -0.12,
            "credit_ig": -0.18,
            "em_bond": -0.20,
            "us_muni": -0.10,
            "fx": 0.0,
            "crypto": -0.65,  # BTC -65% in 2022
        },
        "fx_per_pair": {
            "EUR/USD": -0.07,
            "GBP/USD": -0.10,
            "USD/JPY": +0.13,
            "USD/TRY": +0.40,
        },
    },
    "covid_crash_2020": {
        "label": "COVID-style liquidity crash",
        "description": "March-2020 style: everything correlates to 1 in a panic — equities, credit, even gold and treasuries de-risk together before central bank intervention.",
        "shocks": {
            "us_equity_etf": -0.32,
            "us_sector_etf": -0.35,
            "us_stock": -0.35,
            "eu_equity_etf": -0.35,
            "eu_stock": -0.35,
            "eu_index": -0.32,
            "asia_equity_etf": -0.30,
            "asia_index": -0.25,
            "asia_stock": -0.35,
            "em_equity_etf": -0.32,
            "tr_equity_etf": -0.35,
            "tr_index": -0.30,
            "tr_stock": -0.40,
            "metal_etf": -0.05,  # gold actually sold off briefly
            "metal_eq_etf": -0.25,
            "metal_fut": -0.05,
            "energy_etf": -0.60,
            "energy_fut": -0.70,  # WTI went negative briefly
            "agri_fut": -0.15,
            "us_bond_long": 0.08,
            "us_bond_mid": 0.04,
            "us_bond_short": 0.01,
            "us_bond_tbill": 0.0,
            "us_bond_tips": 0.0,
            "us_bond_agg": 0.02,
            "credit_hy": -0.20,
            "credit_ig": -0.10,
            "em_bond": -0.18,
            "us_muni": -0.08,
            "fx": 0.0,
            "crypto": -0.55,  # BTC -50% in mid-March 2020
        },
        "fx_per_pair": {
            "EUR/USD": -0.05,
            "GBP/USD": -0.10,
            "USD/JPY": 0.0,
            "USD/TRY": +0.15,
        },
    },
    "energy_supercycle": {
        "label": "Energy supercycle",
        "description": "Supply-shock-driven oil rally (+60%) and broad commodity strength. Energy stocks rip; tech/duration suffer from higher discount rates.",
        "shocks": {
            "us_equity_etf": -0.05,
            "us_sector_etf": -0.05,
            "us_stock": -0.08,
            "eu_equity_etf": -0.05,
            "eu_stock": -0.05,
            "eu_index": -0.05,
            "asia_equity_etf": -0.10,
            "asia_index": -0.08,
            "asia_stock": -0.12,
            "em_equity_etf": 0.05,
            "tr_equity_etf": -0.10,
            "tr_index": -0.08,
            "tr_stock": -0.12,
            "metal_etf": 0.15,
            "metal_eq_etf": 0.25,
            "metal_fut": 0.20,
            "energy_etf": 0.55,
            "energy_fut": 0.60,
            "agri_fut": 0.25,
            "us_bond_long": -0.12,
            "us_bond_mid": -0.06,
            "us_bond_short": -0.02,
            "us_bond_tbill": 0.0,
            "us_bond_tips": 0.05,  # TIPS love inflation
            "us_bond_agg": -0.05,
            "credit_hy": -0.05,
            "credit_ig": -0.07,
            "em_bond": -0.05,
            "us_muni": -0.04,
            "fx": 0.0,
            "crypto": 0.05,
        },
        "fx_per_pair": {
            "EUR/USD": -0.05,
            "GBP/USD": -0.05,
            "USD/JPY": +0.10,
            "USD/TRY": +0.20,
        },
    },
    "em_fx_crisis": {
        "label": "EM/FX crisis",
        "description": "1998/2018-style EM blow-up: USD strengthens sharply vs EM (TRY, EM equities crash), DM equity wobbles but recovers, gold catches a bid.",
        "shocks": {
            "us_equity_etf": -0.08,
            "us_sector_etf": -0.10,
            "us_stock": -0.10,
            "eu_equity_etf": -0.12,
            "eu_stock": -0.12,
            "eu_index": -0.10,
            "asia_equity_etf": -0.18,
            "asia_index": -0.15,
            "asia_stock": -0.20,
            "em_equity_etf": -0.30,
            "tr_equity_etf": -0.45,
            "tr_index": -0.20,  # local-currency BIST often UP in TRY crisis (real-asset effect)
            "tr_stock": -0.25,
            "metal_etf": 0.10,
            "metal_eq_etf": 0.05,
            "metal_fut": 0.08,
            "energy_etf": -0.10,
            "energy_fut": -0.10,
            "agri_fut": 0.05,
            "us_bond_long": 0.05,
            "us_bond_mid": 0.03,
            "us_bond_short": 0.01,
            "us_bond_tbill": 0.0,
            "us_bond_tips": 0.0,
            "us_bond_agg": 0.02,
            "credit_hy": -0.15,
            "credit_ig": -0.05,
            "em_bond": -0.25,
            "us_muni": -0.02,
            "fx": 0.0,
            "crypto": -0.30,
        },
        "fx_per_pair": {
            "EUR/USD": -0.08,
            "GBP/USD": -0.10,
            "USD/JPY": +0.05,
            "USD/TRY": +0.60,
        },
    },
    "crypto_winter": {
        "label": "Crypto winter (BTC -70%)",
        "description": "Crypto-specific deleveraging — exchange failures, stablecoin runs. Limited contagion to traditional assets but tech beta gets hit.",
        "shocks": {
            "us_equity_etf": -0.04,
            "us_sector_etf": -0.06,
            "us_stock": -0.06,
            "eu_equity_etf": -0.02,
            "eu_stock": -0.02,
            "eu_index": -0.02,
            "asia_equity_etf": -0.04,
            "asia_index": -0.03,
            "asia_stock": -0.05,
            "em_equity_etf": -0.05,
            "tr_equity_etf": -0.03,
            "tr_index": -0.03,
            "tr_stock": -0.04,
            "metal_etf": 0.05,
            "metal_eq_etf": 0.02,
            "metal_fut": 0.04,
            "energy_etf": -0.05,
            "energy_fut": -0.05,
            "agri_fut": 0.0,
            "us_bond_long": 0.03,
            "us_bond_mid": 0.02,
            "us_bond_short": 0.01,
            "us_bond_tbill": 0.0,
            "us_bond_tips": 0.0,
            "us_bond_agg": 0.02,
            "credit_hy": -0.05,
            "credit_ig": -0.02,
            "em_bond": -0.05,
            "us_muni": -0.01,
            "fx": 0.0,
            "crypto": -0.70,
        },
        "fx_per_pair": {
            "EUR/USD": -0.01,
            "GBP/USD": -0.02,
            "USD/JPY": 0.0,
            "USD/TRY": +0.05,
        },
    },
}


@dataclass
class StressResult:
    """One scenario × portfolio outcome."""

    scenario: str
    label: str
    description: str
    portfolio_pnl: float  # decimal (e.g. -0.235 = -23.5%)
    per_asset_pnl: list[dict]  # [{symbol, weight, asset_class, shock_pct, contribution, covered}]
    portfolio_value_after: float  # 1.0 → 1+pnl (synth side; USDC unchanged)
    coverage_pct: float = 1.0  # fraction of synth weight actually covered by the scenario
    uncovered_symbols: list[str] = None  # symbols whose asset_class wasn't in the scenario dict


def _shock_for(asset_class: str, symbol: str, scenario_def: dict) -> tuple[float, bool]:
    """Look up the per-class shock; FX uses a per-pair override.

    Returns (shock, was_covered).  Missing asset_class → (0.0, False).
    A silent 0% loss on an uncovered class is exactly the demo-day
    failure mode this engine is supposed to prevent, so callers should
    track coverage.
    """
    if asset_class == "fx":
        fx_shocks = scenario_def.get("fx_per_pair", {})
        if symbol in fx_shocks:
            return float(fx_shocks[symbol]), True
        _logger.warning("stress: FX pair %r not in %r scenario", symbol, scenario_def.get("label"))
        return 0.0, False
    shocks = scenario_def.get("shocks", {})
    if asset_class in shocks:
        return float(shocks[asset_class]), True
    _logger.warning(
        "stress: asset_class %r (%s) not in %r scenario — defaulting to 0%% shock",
        asset_class,
        symbol,
        scenario_def.get("label"),
    )
    return 0.0, False


def _resolve_asset_class(symbol: str) -> str | None:
    """Recover asset_class from display symbol via GLOBAL_ASSETS."""
    for _synth, (_yf, display, ac, _ex) in GLOBAL_ASSETS.items():
        if display == symbol:
            return ac
    return None


def stress_one(
    allocations: list[dict],
    scenario_id: str,
    usdc_weight: float = 0.0,
) -> StressResult:
    """Apply a single scenario to a portfolio.

    ``allocations`` is the advisor-output list of {symbol, weight, asset_class}
    dicts.  ``usdc_weight`` is accepted for API symmetry but unused — USDC is
    a settlement asset and never shocked.  Returns the portfolio P&L plus
    per-asset contributions and a coverage_pct field so the caller can warn
    the user when a non-trivial fraction of their book wasn't covered by
    the scenario (e.g. yfinance-failed path with asset_class='unknown').
    """
    scenario_def = SCENARIOS.get(scenario_id)
    if scenario_def is None:
        raise ValueError(f"Unknown scenario: {scenario_id}")

    per_asset: list[dict] = []
    portfolio_pnl = 0.0
    covered_weight = 0.0
    total_weight = 0.0
    uncovered_symbols: list[str] = []
    for a in allocations:
        sym = a["symbol"]
        ac = a.get("asset_class") or _resolve_asset_class(sym) or "unknown"
        # If the upstream tagged the row 'unknown', try one more resolution
        # before falling through to a zero-shock silent result.
        if ac == "unknown":
            resolved = _resolve_asset_class(sym)
            if resolved:
                ac = resolved
        w = float(a.get("weight") or 0.0)
        shock, covered = _shock_for(ac, sym, scenario_def)
        contribution = w * shock
        portfolio_pnl += contribution
        total_weight += w
        if covered:
            covered_weight += w
        else:
            uncovered_symbols.append(sym)
        per_asset.append(
            {
                "symbol": sym,
                "asset_class": ac,
                "weight": round(w, 4),
                "shock_pct": round(shock, 4),
                "contribution_pct": round(contribution, 4),
                "covered": covered,
            }
        )

    coverage_pct = (covered_weight / total_weight) if total_weight > 1e-9 else 1.0
    return StressResult(
        scenario=scenario_id,
        label=scenario_def["label"],
        description=scenario_def["description"],
        portfolio_pnl=round(portfolio_pnl, 4),
        per_asset_pnl=per_asset,
        portfolio_value_after=round(1.0 + portfolio_pnl, 4),
        coverage_pct=round(coverage_pct, 4),
        uncovered_symbols=uncovered_symbols,
    )


def stress_all(
    allocations: list[dict],
    usdc_weight: float = 0.0,
) -> list[StressResult]:
    """Run every scenario; return sorted by P&L (worst first)."""
    results = [stress_one(allocations, sid, usdc_weight) for sid in SCENARIOS]
    results.sort(key=lambda r: r.portfolio_pnl)
    return results


def list_scenarios() -> list[dict]:
    """For UI dropdowns / explanations."""
    return [{"id": sid, "label": d["label"], "description": d["description"]} for sid, d in SCENARIOS.items()]
