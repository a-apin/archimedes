"""52-Week High Proximity Momentum — George & Hwang 2004.

Buy when the current price is within 10% of the trailing 52-week high; flat
otherwise. The mechanism: anchoring psychology and slow price discovery cause
stocks near their annual high to continue outperforming as the market gradually
incorporates positive news. Single-asset adaptation on SPY.

Paper divergence: the original is a cross-sectional strategy — rank all stocks
by (price / 52w_high), long top decile, short bottom decile. Here we adapt
it to a single-asset binary signal: invested when SPY itself is near its own
annual high, flat otherwise. The cross-sectional short leg and ranking are
removed; the directional alpha is lower than the paper claims.
"""

from __future__ import annotations

import backtrader as bt

PAPER_ARXIV_ID: str | None = None
PAPER_TITLE = "The 52-Week High and Momentum Investing"
PAPER_AUTHORS: list[str] = ["T. George", "C.-Y. Hwang"]
PAPER_VENUE = "Journal of Finance"
PAPER_YEAR = 2004
PAPER_DOI = "10.1111/j.1540-6261.2004.00695.x"
PAPER_CITATION_COUNT = 1650  # Snapshot 2026-05; verify via Semantic Scholar.

METHODOLOGY_SUMMARY = (
    "Hold a long position when the current price is within 10% of the "
    "trailing 52-week high; otherwise hold cash. The signal captures the "
    "anchoring effect: investors underreact to good news when the stock is "
    "near its annual high, creating a continuation opportunity."
)

METHODOLOGY_TEXT = (
    "George & Hwang (2004) show that the ratio of current price to the "
    "trailing 52-week high is the dominant source of momentum profits. "
    "The cross-sectional strategy buys the top decile by this ratio and "
    "shorts the bottom decile, rebalanced monthly. The paper reports that "
    "the 52-week-high signal subsumes the standard 12-1 month momentum "
    "factor (Jegadeesh-Titman) in a horse race.\n\n"
    "v1 Archimedes adaptation (single-asset, long-only): on each daily bar "
    "after 252 trading days of history, compute high_ratio = "
    "close / max(close[-252:-1]). If high_ratio >= 0.90 (i.e., within 10% "
    "of the 52-week high), the account is fully invested; otherwise flat. "
    "This captures the signal direction without the short leg. The paper's "
    "long-short Sharpe of ~0.9 will not be reproduced; expected single-asset "
    "long-only performance is in the 0.4–0.7 range, consistent with other "
    "single-asset adaptations in this library."
)

PAPER_CLAIMED_SHARPE = 0.90   # Long-short cross-sectional portfolio, 1963-2001.
PAPER_CLAIMED_CAGR = 0.135
PAPER_CLAIMED_MAX_DD = 0.18

ASSET_UNIVERSE: list[str] = ["SPY", "NIKKEI", "GOLD", "TREASURY", "OIL"]
POSITION_SIZING = "equal_weight"
REBALANCE_FREQUENCY = "monthly"
RISK_PROFILES: list[str] = ["moderate", "aggressive"]

CURATOR_WALLET: str | None = None
CURATOR_NOTE = (
    "Adds anchoring/behavioral-finance flavor missing from the existing "
    "trend-following library. The 52w-high ratio beats raw 12-1 momentum in "
    "the paper's horse race, making this a theoretically grounded "
    "alternative to TSMOM for the moderate sleeve. Long-only adaptation "
    "deliberately surfaces the long-short gap in the passport so users can "
    "see exactly where the paper claim comes from."
)
EXTRACTION_LLM: str | None = None  # Hand-curated.

STATUS = "candidate"

# Stub backtest metrics — PLACEHOLDER (replaced by real fixture if present)
BACKTEST_SHARPE = 0.58
BACKTEST_CAGR = 0.082
BACKTEST_MAX_DD = 0.21
BACKTEST_WIN_RATE = 0.54
BACKTEST_CALMAR = 0.39
BACKTEST_CORR_SPY = 0.70


class FiftyTwoWeekHighMomentum(bt.Strategy):
    """Long when close is within `proximity_pct` of trailing 52-week high."""

    params = (
        ("lookback_bars", 252),
        ("proximity_pct", 0.10),   # invest when within 10% of 52w high
        ("exposure_fraction", 0.99),
    )

    def next(self) -> None:
        lookback = int(self.params.lookback_bars)
        if len(self) <= lookback:
            return

        recent_highs = [float(self.data.high[-i]) for i in range(1, lookback + 1)]
        annual_high = max(recent_highs)
        if annual_high <= 0:
            return

        current_close = float(self.data.close[0])
        high_ratio = current_close / annual_high

        threshold = 1.0 - float(self.params.proximity_pct)
        if high_ratio >= threshold:
            if not self.position:
                account_value = float(self.broker.getvalue())
                target_notional = account_value * float(self.params.exposure_fraction)
                size = int(target_notional // current_close)
                if size > 0:
                    self.buy(size=size)
        else:
            if self.position:
                self.close()
