"""Dividend Yields and Expected Stock Returns — Fama & French 1988.

Fama & French (1988) show that the dividend yield (D/P) of stocks predicts
their subsequent returns: high-yield names earn higher expected returns,
especially over longer horizons, and the effect is most useful as a defensive,
income-like tilt. We have NO fundamental dividend data in the demo universe, so
this strategy uses a PRICE-BASED PROXY for the "steady income compounding"
characteristic that high-yield assets tend to exhibit — explicitly a loose
proxy, not a faithful dividend-yield sort.

Requires ``engine.run_multi_backtest`` (it reads every ``self.datas[i]`` each
bar to rank the universe). ``self.datas[0]`` is the market benchmark (SPY).
"""

from __future__ import annotations

import math

import backtrader as bt

PAPER_ARXIV_ID: str | None = None
PAPER_TITLE = "Dividend Yields and Expected Stock Returns"
PAPER_AUTHORS: list[str] = ["Eugene F. Fama", "Kenneth R. French"]
PAPER_VENUE = "Journal of Financial Economics"
PAPER_YEAR = 1988
PAPER_DOI = "10.1016/0304-405X(88)90020-7"
PAPER_CITATION_COUNT = 6000  # Snapshot 2026-06; verify via Semantic Scholar.

# High-yield defensive assets tend to outperform in downturns.
REGIME_TAG: str = "bear"

METHODOLOGY_SUMMARY = (
    "Dividend-yield tilt via a price-based proxy. Each rebalance, rank the "
    "universe by a 'yield proxy score' = mean daily return / max drawdown "
    "(income-like steadiness), go long the steadiest compounders and short "
    "the least steady, hold to the next rebalance. A loose proxy for the "
    "high-D/P sort Fama-French (1988) study — NOT a true dividend sort."
)

METHODOLOGY_TEXT = (
    "Fama & French (1988) regress future stock returns on the dividend yield "
    "(D/P) and find that dividend yield predicts subsequent returns: stocks "
    "with high D/P earn higher expected returns over the following months and "
    "years, with the explanatory power rising with the return horizon. High-"
    "yield names also behave defensively, providing an income-like compounding "
    "profile that holds up better in downturns.\n\n"
    "v1 Archimedes adaptation (cross-asset, on the N-feed engine): the demo "
    "universe has NO fundamental dividend data, so we CANNOT compute a real "
    "D/P. Instead we use a price-based PROXY for the 'steady income "
    "compounding' characteristic high-yield assets exhibit. For each asset we "
    "compute a yield proxy score = (mean daily return over the lookback) / "
    "(max drawdown over the lookback + epsilon). A higher score means steadier "
    "compounding with shallower drawdowns — the return signature of an income-"
    "like holding. Each rebalance (~21 bars) we rank the universe by this "
    "score, go long the top fraction and short the bottom fraction in equal "
    "weight (order_target_percent), holding to the next rebalance.\n\n"
    "Honest caveats: (1) this is a LOOSE PROXY and explicitly NOT a faithful "
    "dividend-yield sort — return-to-drawdown stability only correlates with "
    "the income-like profile of high-D/P assets; it does not measure dividend "
    "yield. (2) Fama-French (1988) study a broad single-market stock cross-"
    "section, not a 5-asset multi-market basket, so it is context, not a like-"
    "for-like benchmark — paper_claimed_* are left null."
)

# Predictive-regression anomaly on a stock universe; no clean Sharpe/CAGR for
# this proxy on this basket → null (provenance discipline).
PAPER_CLAIMED_SHARPE: float | None = None
PAPER_CLAIMED_CAGR: float | None = None
PAPER_CLAIMED_MAX_DD: float | None = None

ASSET_UNIVERSE: list[str] = ["SPY", "NIKKEI", "GOLD", "TREASURY", "OIL"]
POSITION_SIZING = "equal_weight"
REBALANCE_FREQUENCY = "monthly"
RISK_PROFILES: list[str] = ["conservative", "moderate"]

CURATOR_WALLET: str | None = None
CURATOR_NOTE = (
    "The income-tilt member of the library, expressed as a price-based proxy "
    "because the demo universe lacks dividend data. Rewards assets that "
    "compound steadily with shallow drawdowns — the return signature of a "
    "high-yield defensive holding. Disclosed clearly as a proxy, not a true "
    "dividend-yield sort."
)
EXTRACTION_LLM: str | None = None

STATUS = "candidate"

BACKTEST_SHARPE: float | None = None
BACKTEST_CAGR: float | None = None
BACKTEST_MAX_DD: float | None = None
BACKTEST_WIN_RATE: float | None = None
BACKTEST_CALMAR: float | None = None
BACKTEST_CORR_SPY: float | None = None

_EPSILON = 1e-9


class FamaFrenchDividendYield(bt.Strategy):
    """Rank the universe by a price-based dividend-yield proxy; long top, short bottom.

    Expects N>=2 data feeds (``self.datas[i]``); ``self.datas[0]`` is the
    market benchmark (SPY). Driven by ``engine.run_multi_backtest``.
    """

    # Runner contract: this class ranks across its whole declared universe
    # (engine.required_feeds() / cli.run_command's N-feed routing branch).
    REQUIRED_FEEDS = "UNIVERSE"

    params = (
        ("lookback", 252),  # trailing window (bars) for the proxy score
        ("rebalance_every", 21),  # ~monthly
        ("long_frac", 0.4),  # long the top 40% of the universe
        ("short_frac", 0.4),  # short the bottom 40%
        ("gross", 1.0),  # total gross exposure (split across long + short legs)
    )

    def __init__(self) -> None:
        self._bars_since_rebalance = 0

    def _mean_daily_return(self, data, n: int) -> float | None:
        if len(data) < n + 1:
            return None
        returns: list[float] = []
        for i in range(1, n + 1):
            prev = float(data.close[-i - 1])
            curr = float(data.close[-i])
            if prev > 0:
                returns.append((curr / prev) - 1.0)
        if not returns:
            return None
        return sum(returns) / len(returns)

    def _max_drawdown(self, data, n: int) -> float | None:
        if len(data) < n + 1:
            return None
        # Oldest -> newest closes over the trailing window (exclude current bar).
        closes = [float(data.close[-i]) for i in range(n, 0, -1)]
        if not closes:
            return None
        peak = closes[0]
        max_dd = 0.0
        for price in closes:
            if price > peak:
                peak = price
            if peak > 0:
                dd = (peak - price) / peak
                if dd > max_dd:
                    max_dd = dd
        return max_dd

    def _yield_proxy_score(self, data) -> float | None:
        n = int(self.params.lookback)
        mean_ret = self._mean_daily_return(data, n)
        max_dd = self._max_drawdown(data, n)
        if mean_ret is None or max_dd is None:
            return None
        return mean_ret / (max_dd + _EPSILON)

    def next(self) -> None:
        if len(self) <= int(self.params.lookback):
            return
        self._bars_since_rebalance += 1
        if self._bars_since_rebalance < int(self.params.rebalance_every):
            return
        self._bars_since_rebalance = 0

        scored = [(self._yield_proxy_score(d), d) for d in self.datas]
        scored = [(s, d) for s, d in scored if s is not None and math.isfinite(s)]
        n = len(scored)
        if n < 2:
            return
        scored.sort(key=lambda x: x[0], reverse=True)  # steadiest first

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
