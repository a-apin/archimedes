"""Cross-Asset Value Factor — Asness, Moskowitz & Pedersen (2013).

Long assets trading below their long-run price level (cheap); short assets
trading above it (expensive). Uses negative price deviation from a rolling
mean as a price-based cheapness proxy — the cross-asset value signal from
the "Value and Momentum Everywhere" Appendix Table AI.

Requires ``engine.run_multi_backtest``.
"""

from __future__ import annotations

import backtrader as bt

PAPER_ARXIV_ID: str | None = None
PAPER_TITLE = "Value and Momentum Everywhere"
PAPER_AUTHORS: list[str] = ["Clifford Asness", "Tobias Moskowitz", "Lasse Heje Pedersen"]
PAPER_VENUE = "The Journal of Finance"
PAPER_YEAR = 2013
PAPER_DOI = "10.1111/jofi.12021"
PAPER_CITATION_COUNT = 3800  # Snapshot 2026-06; verify via Semantic Scholar.

# Value strategies tend to outperform in recovery and bear regimes when
# expensive assets de-rate and cheap assets mean-revert.
REGIME_TAG: str = "bear"

METHODOLOGY_SUMMARY = (
    "Cross-asset price-based value: rank assets by how far their current price "
    "lies below their rolling historical mean. Long the cheapest (most below mean), "
    "short the most expensive (most above mean). Monthly rebalance."
)

METHODOLOGY_TEXT = (
    "Asness, Moskowitz & Pedersen (2013) document robust value AND momentum premia "
    "across eight asset classes (global equities, equity indices, government bonds, "
    "currencies, and commodities). Their cross-asset value measure for individual "
    "stocks is book-to-price; for asset classes they use a 5-year return reversal "
    "(cheap = assets that have underperformed over the past 5 years). The paper "
    "reports a value factor Sharpe of ~0.6 averaged across asset classes.\n\n"
    "v1 Archimedes adaptation (price-based value on the N-feed engine): without "
    "fundamental ratios (P/B, E/P, dividend yield), we use the negative of the "
    "current price's deviation from its rolling mean over value_window bars as the "
    "cheapness proxy: value_score = -(close[0] / mean(close[-value_window:]) - 1). "
    "A large positive score means the asset is trading well below its historical "
    "mean, i.e., potentially cheap. This captures the mean-reversion dimension of "
    "cross-asset value described in the paper's Appendix Table AI proxies.\n\n"
    "Honest caveats: (1) This is a price-based proxy, not a fundamental valuation "
    "ratio. It measures cheapness relative to recent history, not relative to "
    "intrinsic value. (2) Our 5-asset universe is far narrower than the paper's "
    "8-class, 40+ market coverage — paper_claimed_* are null. (3) The mean-reversion "
    "signal can conflict with momentum; in practice value and momentum tend to be "
    "negatively correlated (as the paper documents), making them useful complements. "
    "(4) Rolling mean over a fixed window is sensitive to the choice of window — a "
    "known limitation of price-based value proxies."
)

PAPER_CLAIMED_SHARPE: float | None = None
PAPER_CLAIMED_CAGR: float | None = None
PAPER_CLAIMED_MAX_DD: float | None = None

ASSET_UNIVERSE: list[str] = ["SPY", "NIKKEI", "GOLD", "TREASURY", "OIL"]
POSITION_SIZING = "equal_weight"
REBALANCE_FREQUENCY = "monthly"
RISK_PROFILES: list[str] = ["conservative", "moderate"]

CURATOR_WALLET: str | None = None
CURATOR_NOTE = (
    "The value counterweight in the library. Value and momentum tend to be "
    "negatively correlated (AMP 2013 Table VI) which makes them natural complements "
    "in a multi-strategy portfolio — blending them reduces variance without "
    "proportionally reducing expected return. In the optimizer this pair is "
    "worth explicitly tracking for the diversification benefit."
)
EXTRACTION_LLM: str | None = None

STATUS = "candidate"

BACKTEST_SHARPE: float | None = None
BACKTEST_CAGR: float | None = None
BACKTEST_MAX_DD: float | None = None
BACKTEST_WIN_RATE: float | None = None
BACKTEST_CALMAR: float | None = None
BACKTEST_CORR_SPY: float | None = None

_ANNUALIZATION = 252


class AsnessMoskowitzValue(bt.Strategy):
    """Cross-asset value: long cheap (below rolling mean), short expensive (above mean).

    Expects N>=2 data feeds. Driven by ``engine.run_multi_backtest``.
    """

    # Runner contract: this class ranks across its whole declared universe
    # (engine.required_feeds() / cli.run_command's N-feed routing branch).
    REQUIRED_FEEDS = "UNIVERSE"

    params = (
        ("value_window", 252),  # rolling mean window for cheapness proxy
        ("rebalance_every", 21),
        ("long_frac", 0.4),
        ("short_frac", 0.4),
        ("gross", 1.0),
    )

    def __init__(self) -> None:
        self._bars_since_rebalance = 0

    def _value_score(self, data) -> float | None:
        window = int(self.params.value_window)
        if len(data) < window + 1:
            return None
        closes: list[float] = [float(data.close[-i]) for i in range(window)]
        mean_price = sum(closes) / len(closes)
        if mean_price <= 0.0:
            return None
        current = float(data.close[0])
        # negative of deviation: high score = current price below mean = cheap
        return -(current / mean_price - 1.0)

    def next(self) -> None:
        if len(self) < int(self.params.value_window) + 1:
            return
        self._bars_since_rebalance += 1
        if self._bars_since_rebalance < int(self.params.rebalance_every):
            return
        self._bars_since_rebalance = 0

        scored = [(self._value_score(d), d) for d in self.datas]
        scored = [(s, d) for s, d in scored if s is not None]
        n = len(scored)
        if n < 2:
            return

        scored.sort(key=lambda x: x[0], reverse=True)  # highest score (cheapest) first

        n_long = max(1, int(round(n * float(self.params.long_frac))))
        n_short = max(1, int(round(n * float(self.params.short_frac))))
        if n_long + n_short > n:
            n_short = max(1, n - n_long)

        longs = {id(d) for _, d in scored[:n_long]}
        shorts = {id(d) for _, d in scored[-n_short:]}
        long_w = (float(self.params.gross) / 2.0) / n_long
        short_w = (float(self.params.gross) / 2.0) / n_short

        for _, d in scored:
            if id(d) in longs:
                self.order_target_percent(data=d, target=long_w)
            elif id(d) in shorts:
                self.order_target_percent(data=d, target=-short_w)
            else:
                self.order_target_percent(data=d, target=0.0)
