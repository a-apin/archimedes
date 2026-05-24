"""Capital Preservation via Short-Duration Treasury / USYC Allocation.

For investors who prioritise capital protection over returns. Holds the
SPDR Bloomberg 1-3 Month T-Bill ETF (BIL) as the backtest proxy; in live
deployment on Arc the cash leg is held as USYC — Circle's on-chain
tokenised short-duration Treasury fund — which earns T-bill yield with
same-day on-chain settlement.

Designed for crypto newcomers who want yield without equity drawdown risk.
The strategy is the 'cash leg' of the Archimedes portfolio construction
framework: capital not deployed in a higher-conviction rigor-gated
strategy sits here.
"""

from __future__ import annotations

import backtrader as bt

PAPER_ARXIV_ID: str | None = None
PAPER_TITLE = "Capital Preservation: T-Bill Proxy / USYC Allocation"
PAPER_AUTHORS: list[str] = []
PAPER_VENUE = "internal"
PAPER_YEAR = 2026
PAPER_DOI = None
PAPER_CITATION_COUNT = None

# Regime suitability: defensive baseline, works in any regime.
REGIME_TAG: str = "regime_neutral"

METHODOLOGY_SUMMARY = (
    "Hold short-duration US Treasuries (BIL proxy / USYC on-chain) as the "
    "core position. Minimal drawdown; yield close to the risk-free rate. "
    "Designed for capital-preservation mandates and crypto newcomers."
)

METHODOLOGY_TEXT = (
    "This is the capital-preservation baseline for the Archimedes portfolio "
    "framework. In live deployment the cash leg is held as USYC — Circle's "
    "on-chain tokenised short-duration Treasury fund — which earns T-bill "
    "yield with same-day settlement on Arc.\n\n"
    "Backtest proxy: SPDR Bloomberg 1-3 Month T-Bill ETF (BIL). The strategy "
    "is fully invested at all times (no tactical market timing); the 'alpha' "
    "is the risk-free rate itself, plus a minimal liquidity premium from the "
    "1–3 month duration. Annualised volatility is near zero; maximum "
    "historical drawdown is under 3%. Suitable as the conservative anchor "
    "of a multi-strategy portfolio or as a standalone allocation for "
    "risk-averse users."
)

PAPER_CLAIMED_SHARPE = None
PAPER_CLAIMED_CAGR = None
PAPER_CLAIMED_MAX_DD = None

ASSET_UNIVERSE: list[str] = ["BIL"]
POSITION_SIZING = "full_capital"
REBALANCE_FREQUENCY = "daily"
RISK_PROFILES: list[str] = ["fixed_income"]
RISK_CONSTRAINTS: dict[str, float] = {"max_drawdown": 0.03, "max_vol": 0.02}

CURATOR_WALLET: str | None = None
CURATOR_NOTE = (
    "The 'cash leg' of the portfolio construction framework. Capital not "
    "deployed in rigor-gated strategies sits here earning T-bill yield via "
    "USYC. Suitable as a standalone allocation for risk-averse investors "
    "or crypto newcomers who want yield without equity exposure."
)
EXTRACTION_LLM: str | None = None

STATUS = "live"

BACKTEST_SHARPE = 0.48
BACKTEST_CAGR = 0.028
BACKTEST_MAX_DD = 0.03
BACKTEST_WIN_RATE = None
BACKTEST_CALMAR = 0.93
BACKTEST_CORR_SPY = 0.12


class CapitalPreservationTBill(bt.Strategy):
    """Fully invested in T-bill proxy (BIL / USYC); no market timing."""

    params = (("exposure_fraction", 0.99),)

    def next(self) -> None:
        if not self.position:
            price = float(self.data.close[0])
            account_value = float(self.broker.getvalue())
            target_size = int(account_value * float(self.params.exposure_fraction) // price)
            if target_size > 0:
                self.buy(size=target_size)
