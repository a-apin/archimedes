"""Cross-sectional momentum (Jegadeesh & Titman 1993).

The strategy wave 1 could NOT express, and the reason the engine grew an N-feed
runner. Each rebalance, rank the whole universe by trailing return, go long the
winners and short the losers in equal weight, and hold for a month. Returns come
from the cross-section (winners keep winning relative to losers), not from any
single asset's direction.

Requires ``engine.run_multi_backtest`` (it reads every ``self.datas[i]`` each
bar to rank the universe). Jegadeesh & Titman (1993) is the canonical academic
documentation of the momentum anomaly.
"""

from __future__ import annotations

import backtrader as bt

PAPER_ARXIV_ID: str | None = None
PAPER_TITLE = "Returns to Buying Winners and Selling Losers: Implications for Stock Market Efficiency"
PAPER_AUTHORS: list[str] = ["Narasimhan Jegadeesh", "Sheridan Titman"]
PAPER_VENUE = "The Journal of Finance"
PAPER_YEAR = 1993
PAPER_DOI = "10.1111/j.1540-6261.1993.tb04702.x"
PAPER_CITATION_COUNT = 18000  # Snapshot 2026-06; verify via Semantic Scholar.

# Momentum is a trend phenomenon — strongest in trending (bull) regimes.
REGIME_TAG: str = "bull"

METHODOLOGY_SUMMARY = (
    "Cross-sectional momentum. Each month rank the universe by trailing "
    "(skip-recent) return, go long the top performers and short the bottom "
    "performers in equal weight, hold one month, repeat. The first strategy in "
    "the library that ranks across a universe rather than trading one asset."
)

METHODOLOGY_TEXT = (
    "Jegadeesh & Titman (1993) document that US stocks sorted on their prior "
    "3-12 month returns exhibit momentum: past winners outperform past losers "
    "over the next 3-12 months. Their canonical design uses a J-month formation "
    "window, skips the most recent month (to avoid the short-term reversal "
    "effect), and holds a winners-minus-losers portfolio for K months. They "
    "report ~1% per month for the zero-cost winner-minus-loser portfolio on US "
    "equities 1965-1989.\n\n"
    "v1 Archimedes adaptation (cross-asset, on the N-feed engine): with a small "
    "asset universe rather than a stock cross-section, each rebalance (every "
    "~21 bars) we rank assets by their formation-window return "
    "(close[-skip]/close[-lookback] - 1; lookback 252, skip 21), then go long "
    "the top fraction and short the bottom fraction in equal weight "
    "(order_target_percent), holding to the next rebalance.\n\n"
    "Honest caveats: (1) the paper's ~1%/month is for a broad single-market "
    "stock cross-section, NOT a 5-asset multi-market basket, so it is context, "
    "not a like-for-like benchmark — paper_claimed_* are left null. (2) The demo "
    "universe mixes US ETFs, a Tokyo index, and futures whose daily closes are "
    "not simultaneous; cross-market momentum on non-synchronous closes is a "
    "known approximation, disclosed here rather than hidden."
)

# Cross-section anomaly reported as monthly excess return on a stock universe;
# not a clean Sharpe/CAGR for this basket → null (provenance discipline).
PAPER_CLAIMED_SHARPE: float | None = None
PAPER_CLAIMED_CAGR: float | None = None
PAPER_CLAIMED_MAX_DD: float | None = None

ASSET_UNIVERSE: list[str] = ["SPY", "NIKKEI", "GOLD", "TREASURY", "OIL"]
POSITION_SIZING = "equal_weight"
REBALANCE_FREQUENCY = "monthly"
RISK_PROFILES: list[str] = ["aggressive"]

CURATOR_WALLET: str | None = None
CURATOR_NOTE = (
    "The momentum sleeve's cross-sectional member and the first strategy that "
    "actually ranks a universe. Where TSMOM (Moskowitz-Ooi-Pedersen) asks 'is "
    "this asset trending up on its own?', this asks 'which assets are winning "
    "relative to the others?' — a different, complementary momentum signal. "
    "Long/short, so it can profit in down markets if losers fall faster than "
    "winners."
)
EXTRACTION_LLM: str | None = None

STATUS = "candidate"

BACKTEST_SHARPE: float | None = None
BACKTEST_CAGR: float | None = None
BACKTEST_MAX_DD: float | None = None
BACKTEST_WIN_RATE: float | None = None
BACKTEST_CALMAR: float | None = None
BACKTEST_CORR_SPY: float | None = None


class CrossSectionalMomentum(bt.Strategy):
    """Rank the universe by trailing return; long the top, short the bottom.

    Expects N>=2 data feeds (``self.datas[i]``). Driven by
    ``engine.run_multi_backtest``.
    """

    # Runner contract: this class ranks across its whole declared universe
    # (engine.required_feeds() / cli.run_command's N-feed routing branch).
    REQUIRED_FEEDS = "UNIVERSE"

    params = (
        ("lookback", 252),  # formation window (bars)
        ("skip", 21),  # skip most-recent bars (short-term reversal control)
        ("rebalance_every", 21),  # ~monthly
        ("long_frac", 0.4),  # long the top 40% of the universe
        ("short_frac", 0.4),  # short the bottom 40%
        ("gross", 1.0),  # total gross exposure (split across long + short legs)
    )

    def __init__(self) -> None:
        self._bars_since_rebalance = 0

    def _formation_return(self, data) -> float | None:
        need = int(self.params.lookback) + 1
        if len(data) < need:
            return None
        old = float(data.close[-int(self.params.lookback)])
        recent = float(data.close[-int(self.params.skip)])
        if old <= 0:
            return None
        return recent / old - 1.0

    def next(self) -> None:
        if len(self) <= int(self.params.lookback):
            return
        self._bars_since_rebalance += 1
        if self._bars_since_rebalance < int(self.params.rebalance_every):
            return
        self._bars_since_rebalance = 0

        scored = [(self._formation_return(d), d) for d in self.datas]
        scored = [(r, d) for r, d in scored if r is not None]
        n = len(scored)
        if n < 2:
            return
        scored.sort(key=lambda x: x[0], reverse=True)  # best first

        n_long = max(1, int(round(n * float(self.params.long_frac))))
        n_short = max(1, int(round(n * float(self.params.short_frac))))
        # Never let the long and short buckets overlap.
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
