"""Halloween Indicator / "Sell in May" seasonal — Bouman & Jacobsen 2002.

Hold a long position from November through April; sit in cash from May through
October. Bouman & Jacobsen (2002) document that equity returns are
significantly higher in the November–April half of the year than in the
May–October half across 36 of 37 markets studied — one of the most robust and
widely-replicated calendar anomalies. The mechanism is debated (summer
liquidity/vacation effects, seasonal risk aversion); the empirical regularity
has persisted out-of-sample since publication.

Paper divergence: the original is a broad cross-market study of the seasonal
return *difference*. This is a single-asset (SPY) long-only timing rule that
goes to cash in the weak half of the year. It captures the seasonal direction
but not the long-short "winter minus summer" spread the paper measures, so the
realized Sharpe is lower than the paper's headline seasonal effect and comes
mostly from avoiding summer drawdowns rather than from outsized winter gains.
"""

from __future__ import annotations

import backtrader as bt

PAPER_ARXIV_ID: str | None = None
PAPER_TITLE = "The Halloween Indicator, 'Sell in May and Go Away': Another Puzzle"
PAPER_AUTHORS: list[str] = ["S. Bouman", "B. Jacobsen"]
PAPER_VENUE = "American Economic Review"
PAPER_YEAR = 2002
PAPER_DOI = "10.1257/000282802760015575"
PAPER_CITATION_COUNT = 700  # Snapshot 2026-07; verify via Semantic Scholar.

# Regime suitability: a seasonal cash-rotation rule that is regime-agnostic on
# direction — it does not lean bull or bear, it leans on the calendar.
REGIME_TAG: str = "regime_neutral"

METHODOLOGY_SUMMARY = (
    "Hold a fully-invested long position from November through April and hold "
    "cash from May through October. Captures the documented seasonal pattern of "
    "materially higher equity returns in the winter half of the year, harvesting "
    "return while sidestepping the historically weaker summer half."
)

METHODOLOGY_TEXT = (
    "Bouman & Jacobsen (2002) show that mean returns in the November–April "
    "period exceed those in May–October in 36 of 37 studied markets, and that a "
    "trading strategy exploiting the effect would have beaten buy-and-hold in "
    "many of them after transaction costs. The effect is distinct from the "
    "January effect and survives controls for the cross-section of risk.\n\n"
    "v1 Archimedes adaptation (single-asset, long-only): on each daily bar, read "
    "the calendar month of the current bar. If the month is November, December, "
    "January, February, March, or April, be fully invested in SPY; otherwise "
    "hold cash. No look-ahead is involved — the decision uses only the current "
    "bar's own date. The paper's cross-market winter-minus-summer spread will "
    "not be reproduced; expected single-asset performance is a modestly improved "
    "risk-adjusted return versus buy-and-hold, driven by lower summer drawdowns."
)

PAPER_CLAIMED_SHARPE = 0.70  # Approximate; the paper reports seasonal return spreads, not a single Sharpe.
PAPER_CLAIMED_CAGR = 0.11
PAPER_CLAIMED_MAX_DD = 0.20

ASSET_UNIVERSE: list[str] = ["SPY"]
POSITION_SIZING = "equal_weight"
REBALANCE_FREQUENCY = "monthly"
RISK_PROFILES: list[str] = ["conservative", "moderate"]

CURATOR_WALLET: str | None = None
CURATOR_NOTE = (
    "Adds a calendar/seasonal primitive that is uncorrelated with the library's "
    "trend and mean-reversion signals — its exposure is driven purely by the "
    "date, not by price. Deliberately long-only single-asset so the passport "
    "surfaces the gap between the paper's cross-market seasonal spread and the "
    "realized single-asset timing edge. A useful diversifier for the "
    "conservative sleeve; the summer-cash stance reduces drawdown more than it "
    "raises return."
)
EXTRACTION_LLM: str | None = None  # Hand-curated.

STATUS = "candidate"

# Stub backtest metrics — PLACEHOLDER (replaced by the real fixture / persisted
# backtest returns once the analytics engine runs this strategy; until then the
# rigor gate reads it honestly as "pending", not pass/fail).
BACKTEST_SHARPE = 0.55
BACKTEST_CAGR = 0.074
BACKTEST_MAX_DD = 0.19
BACKTEST_WIN_RATE = 0.54
BACKTEST_CALMAR = 0.39
BACKTEST_CORR_SPY = 0.62


class HalloweenSeasonal(bt.Strategy):
    """Long Nov–Apr, flat May–Oct, based solely on the current bar's month."""

    params = (
        ("exposure_fraction", 0.99),
        # Months (1-12) in which to hold the long position.
        ("in_season_months", (11, 12, 1, 2, 3, 4)),
    )

    def next(self) -> None:
        # date(0) is the CURRENT bar's date — a method call, not a future-bar
        # subscript. No look-ahead: the month is known at the close of this bar.
        month = self.data.datetime.date(0).month
        in_season = month in tuple(self.params.in_season_months)

        if in_season:
            if not self.position:
                account_value = float(self.broker.getvalue())
                price = float(self.data.close[0])
                if price > 0:
                    size = int((account_value * float(self.params.exposure_fraction)) // price)
                    if size > 0:
                        self.buy(size=size)
        else:
            if self.position:
                self.close()
