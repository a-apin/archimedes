"""Quality Minus Junk — Asness, Frazzini & Pedersen 2019 (price-based proxy).

The paper defines quality along four dimensions (profitability, safety,
growth, payout) using accounting fundamentals. Because the analytics engine
has no fundamental data we use the information ratio (mean_return / std_return)
over a trailing window as a price-based proxy for quality. The proxy captures
the two most tractable dimensions: a positive drift maps to 'profitable', and
low daily-return volatility maps to 'safe'. This is an approximation; see
METHODOLOGY_TEXT for the honest caveats and scope of the adaptation.

Requires ``engine.run_multi_backtest`` (reads every ``self.datas[i]`` each
rebalance). ``self.datas[0]`` is the market benchmark (assumed SPY) and is
included in the quality ranking.
"""

from __future__ import annotations

import math

import backtrader as bt

PAPER_ARXIV_ID: str | None = None
PAPER_TITLE = "Quality Minus Junk"
PAPER_AUTHORS: list[str] = ["Clifford Asness", "Andrea Frazzini", "Lasse Heje Pedersen"]
PAPER_VENUE = "Review of Accounting Studies"
PAPER_YEAR = 2019
PAPER_DOI = "10.1007/s11142-018-9470-2"
PAPER_CITATION_COUNT = 1200  # Snapshot 2026-06; verify via Semantic Scholar.

# Quality is defensive — the QMJ premium is persistent across regimes but
# strongest when junk sells off; the strategy is broadly regime-neutral.
REGIME_TAG: str = "regime_neutral"

METHODOLOGY_SUMMARY = (
    "Price-based proxy for the Quality Minus Junk factor. Rank assets by "
    "information ratio (mean / std of daily returns) over a trailing window. "
    "Long the high-quality (high IR) assets, short the low-quality (low IR) "
    "assets. A cross-sectional quality tilt without fundamental data."
)

METHODOLOGY_TEXT = (
    "Asness, Frazzini & Pedersen (2019) define quality along four dimensions "
    "computed from Compustat accounting data: (1) Profitability — ROA, ROE, "
    "gross margins, and accruals; (2) Safety — leverage, Altman Z-score, and "
    "idiosyncratic volatility; (3) Growth — five-year growth rates in the "
    "profitability measures; (4) Payout — equity and debt issuance, buyback "
    "yield. Each dimension is standardized and equally weighted into a single "
    "quality score. Going long high-quality and short low-quality ('junk') "
    "equities earned a Sharpe of approximately 1.0 on US stocks from 1956 to "
    "2012, largely independent of the market, value, and momentum factors.\n\n"
    "v1 Archimedes adaptation — price-based proxy: the analytics engine does "
    "not have balance-sheet or income-statement data, so we replace the four "
    "accounting dimensions with a single price-derived proxy: the information "
    "ratio (IR) = mean(daily_return) / std(daily_return) over the trailing "
    "'lookback' bars. The IR is tractable because it simultaneously captures "
    "the two most quantifiable quality dimensions — 'profitability' (positive "
    "drift, high mean return) and 'safety' (low variance). Growth and payout "
    "have no clean price-based analogue and are omitted. Each rebalance "
    "(every ~21 bars) assets are ranked by IR, the top long_frac are held "
    "long, and the bottom short_frac are held short, each in equal weight.\n\n"
    "Honest caveats: (1) This is a loose approximation of QMJ, not a faithful "
    "implementation. The paper's Sharpe ~1.0 is for a fundamental-data quality "
    "score on a broad US equity cross-section 1956-2012; it is provided as "
    "research context, not a benchmark for this adaptation — PAPER_CLAIMED_* "
    "are left null. (2) The IR proxy conflates the 'safety' and 'profitability' "
    "dimensions and captures nothing of 'growth' or 'payout'. (3) In a small "
    "cross-asset universe (SPY, NIKKEI, GOLD, TREASURY, OIL) the concept of "
    "cross-sectional quality is economically less clean than in a 500-stock "
    "universe. (4) Short positions in the analytics engine are executed at "
    "market via order_target_percent; realistic short-selling costs are not "
    "modelled."
)

# Fundamental-data Sharpe on the full US stock cross-section — not a
# like-for-like benchmark for this price-proxy cross-asset adaptation.
PAPER_CLAIMED_SHARPE: float | None = None
PAPER_CLAIMED_CAGR: float | None = None
PAPER_CLAIMED_MAX_DD: float | None = None

ASSET_UNIVERSE: list[str] = ["SPY", "NIKKEI", "GOLD", "TREASURY", "OIL"]
POSITION_SIZING = "equal_weight"
REBALANCE_FREQUENCY = "monthly"
RISK_PROFILES: list[str] = ["conservative", "moderate"]

CURATOR_WALLET: str | None = None
CURATOR_NOTE = (
    "Quality sleeve. Uses information ratio as a price-based proxy for the "
    "Asness-Frazzini-Pedersen quality score. Pairs well with the momentum "
    "strategies (Jegadeesh-Titman, TSMOM) as a defensive complement — quality "
    "and momentum are historically lowly correlated. Status is 'candidate' "
    "until backtest metrics are populated."
)
EXTRACTION_LLM: str | None = None

STATUS = "candidate"

BACKTEST_SHARPE: float | None = None
BACKTEST_CAGR: float | None = None
BACKTEST_MAX_DD: float | None = None
BACKTEST_WIN_RATE: float | None = None
BACKTEST_CALMAR: float | None = None
BACKTEST_CORR_SPY: float | None = None


class NoVyMarxQMJ(bt.Strategy):
    """Rank assets by information ratio (quality proxy); long top, short bottom.

    Expects N>=2 data feeds (``self.datas[i]``). Driven by
    ``engine.run_multi_backtest``.
    """

    # Runner contract: this class ranks across its whole declared universe
    # (engine.required_feeds() / cli.run_command's N-feed routing branch).
    REQUIRED_FEEDS = "UNIVERSE"

    params = (
        ("lookback", 252),  # window for IR computation (bars)
        ("rebalance_every", 21),  # ~monthly rebalance
        ("long_frac", 0.4),  # long the top 40% by IR
        ("short_frac", 0.4),  # short the bottom 40% by IR
        ("gross", 1.0),  # total gross exposure split evenly across legs
    )

    def __init__(self) -> None:
        self._bars_since_rebalance = 0

    def _daily_returns(self, data, n: int) -> list[float]:
        """Return the last *n* daily simple returns for *data* (oldest first).

        Uses close prices at negative indices: close[-1] is yesterday,
        close[-n] is n bars ago.  Returns an empty list when history is
        insufficient.
        """
        need = n + 1  # need n+1 closes to compute n returns
        if len(data) < need:
            return []
        rets: list[float] = []
        for i in range(n, 0, -1):  # i goes from n down to 1
            prev = float(data.close[-i - 1])
            curr = float(data.close[-i])
            if prev > 0:
                rets.append(curr / prev - 1.0)
        return rets

    def _information_ratio(self, data) -> float | None:
        """Compute the information ratio over the lookback window.

        Returns None when history is insufficient or std is zero.
        """
        rets = self._daily_returns(data, int(self.params.lookback))
        n = len(rets)
        if n < 2:
            return None
        mean = sum(rets) / n
        variance = sum((r - mean) ** 2 for r in rets) / (n - 1)
        if variance <= 0.0:
            return None
        return mean / math.sqrt(variance)

    def next(self) -> None:
        if len(self) <= int(self.params.lookback):
            return
        self._bars_since_rebalance += 1
        if self._bars_since_rebalance < int(self.params.rebalance_every):
            return
        self._bars_since_rebalance = 0

        scored = [(self._information_ratio(d), d) for d in self.datas]
        scored = [(ir, d) for ir, d in scored if ir is not None]
        n = len(scored)
        if n < 2:
            return
        scored.sort(key=lambda x: x[0], reverse=True)  # highest IR first

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
