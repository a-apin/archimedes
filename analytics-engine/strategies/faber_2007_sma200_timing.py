"""SMA200 Tactical Asset Allocation — Faber 2007.

Long the asset when its closing price is above the 200-day simple moving
average; in cash otherwise. The simplest published trend-filter that
materially reduces left-tail drawdowns versus buy-and-hold. Single-asset by
construction — drops cleanly into the analytics-engine per-instrument loop.
"""

from __future__ import annotations

import backtrader as bt

PAPER_ARXIV_ID: str | None = None
PAPER_TITLE = "A Quantitative Approach to Tactical Asset Allocation"
PAPER_AUTHORS: list[str] = ["Mebane T. Faber"]
PAPER_VENUE = "The Journal of Wealth Management"
PAPER_YEAR = 2007
PAPER_DOI = "10.3905/jwm.2007.674809"
PAPER_CITATION_COUNT = 850  # Snapshot 2026-05; verify via Semantic Scholar.

METHODOLOGY_SUMMARY = (
    "Hold a long position in the asset when its monthly close is above the "
    "10-month simple moving average; otherwise hold cash. A simple binary "
    "trend filter intended to clip drawdowns while preserving most upside."
)

METHODOLOGY_TEXT = (
    "Faber's quantitative tactical-asset-allocation model evaluates each "
    "asset's monthly close against its 10-month simple moving average. If "
    "the close is above the SMA, the position is fully invested; if below, "
    "the position is moved to cash (T-bills in the paper). The model is "
    "applied across five asset classes (US equity, foreign equity, bonds, "
    "commodities, REITs) in an equal-weighted portfolio, rebalanced "
    "monthly.\n\n"
    "v1 Archimedes adaptation: 10 trading months ≈ 210 daily bars; we use "
    "a 200-day SMA for tractability on daily data. Single-asset "
    "implementation — portfolio-level weighting across operations is the "
    "responsibility of upstream allocation logic. Cash leg returns are "
    "modeled as zero (conservative); the paper assumes 90-day T-bill "
    "yield."
)

PAPER_CLAIMED_SHARPE = 0.78
PAPER_CLAIMED_CAGR = 0.1127
PAPER_CLAIMED_MAX_DD = 0.095  # For the 5-asset combined portfolio; single-asset is worse.

ASSET_UNIVERSE: list[str] = ["SPY", "NIKKEI", "GOLD", "TREASURY", "OIL"]
POSITION_SIZING = "equal_weight"
REBALANCE_FREQUENCY = "daily"  # Implementation evaluates signal on every bar; Faber's original uses monthly closes.
RISK_PROFILES: list[str] = ["conservative", "moderate"]

CURATOR_WALLET: str | None = None
CURATOR_NOTE = (
    "Best-in-class drawdown control for a binary trend filter. Mandatory "
    "inclusion in any tactical-allocation library; the 200-day SMA cross "
    "is also the regime-detection signal Önder's classifier uses, which "
    "lets us anchor the strategy and regime narrative to the same line in "
    "the chart."
)
EXTRACTION_LLM: str | None = None

STATUS = "live"

# Stub backtest metrics — superseded by real values in backtest_fixtures.json.
# strategy_provider ignores these when a fixture entry exists; kept as documentation fallback.
BACKTEST_SHARPE = 0.51
BACKTEST_CAGR = 0.073
BACKTEST_MAX_DD = 0.20
BACKTEST_WIN_RATE = 0.55
BACKTEST_CALMAR = 0.37
BACKTEST_CORR_SPY = 0.62


class FaberSMA200(bt.Strategy):
    """Long when close > 200d SMA; flat otherwise."""

    params = (
        ("sma_period", 200),
        ("exposure_fraction", 0.99),
    )

    def __init__(self) -> None:
        self.sma = bt.indicators.SimpleMovingAverage(
            self.data.close, period=int(self.params.sma_period)
        )

    def next(self) -> None:
        if len(self) < int(self.params.sma_period):
            return

        price = float(self.data.close[0])
        sma_value = float(self.sma[0])

        if price > sma_value:
            account_value = float(self.broker.getvalue())
            target_size = int(account_value * float(self.params.exposure_fraction) // price)
            if target_size > 0 and self.position.size != target_size:
                self.order_target_size(target=target_size)
        else:
            if self.position:
                self.close()
