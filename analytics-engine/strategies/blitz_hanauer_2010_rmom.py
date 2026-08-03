"""Residual Momentum — Blitz, Huij & Martens (2011).

Standard cross-sectional momentum conflates an asset's alpha with its
systematic factor exposure. Residual momentum strips out the market
component — rank assets by their beta-adjusted formation-window return
(alpha) rather than total return. Blitz et al. show this earns a higher
Sharpe and is less correlated with conventional momentum factors.

Requires ``engine.run_multi_backtest`` (ranks assets cross-sectionally).
``self.datas[0]`` is treated as the market benchmark (assumed SPY).
"""

from __future__ import annotations

import backtrader as bt

PAPER_ARXIV_ID: str | None = None
PAPER_TITLE = "Residual Momentum"
PAPER_AUTHORS: list[str] = ["David Blitz", "Joop Huij", "Martin Martens"]
PAPER_VENUE = "Journal of Empirical Finance"
PAPER_YEAR = 2011
PAPER_DOI = "10.1016/j.jempfin.2011.08.004"
PAPER_CITATION_COUNT = 350  # Snapshot 2026-06; verify via Semantic Scholar.

# Momentum is a trend / risk-on phenomenon — strongest in bull regimes.
REGIME_TAG: str = "bull"

METHODOLOGY_SUMMARY = (
    "Each month, estimate rolling OLS beta for every asset vs the market "
    "benchmark; subtract the systematic market component from the "
    "formation-window return to obtain a residual return. Rank by residual "
    "return, long the top fraction, short the bottom fraction."
)

METHODOLOGY_TEXT = (
    "Blitz, Huij & Martens (2011) observe that standard price momentum "
    "partially reflects systematic exposure to the market factor: an asset "
    "that simply has high market beta will have a high total return in a "
    "rising market and would be ranked highly by conventional momentum, even "
    "if it produced no alpha. Residual momentum corrects for this by "
    "computing each asset's formation-window return net of its estimated "
    "market exposure:\n\n"
    "    residual_return = total_return_{[lookback, skip]} "
    "- beta * market_return_{[lookback, skip]}\n\n"
    "where beta is estimated from a rolling daily-returns OLS regression "
    "over the most recent beta_window bars (shorter than the lookback to "
    "use fresh data for the factor model). The paper applies this to a broad "
    "US and international equity cross-section and reports that residual "
    "momentum has a higher Sharpe ratio than total-return momentum, survives "
    "standard risk-factor controls, and is less correlated with conventional "
    "momentum factor portfolios (suggesting it captures a distinct source "
    "of return predictability).\n\n"
    "v1 Archimedes adaptation (cross-asset, N-feed engine): with a small "
    "asset universe rather than a stock cross-section, each rebalance "
    "(every ~21 bars) we (1) compute the total return from close[-lookback] "
    "to close[-skip] for each asset and the market, (2) estimate rolling "
    "beta over the most recent beta_window bars of daily returns, (3) compute "
    "residual_return = total_return - beta * market_total_return, and "
    "(4) rank assets by residual return — long the top long_frac, short the "
    "bottom short_frac in equal weight.\n\n"
    "Honest caveats: (1) The paper's results are for a broad equity "
    "cross-section; our 5-asset basket is a conceptual demonstration — "
    "paper_claimed_* are left null. (2) With only a handful of assets the "
    "beta adjustment may amplify measurement noise rather than reduce it. "
    "(3) The residual is only as good as the single-factor market model; "
    "multi-factor beta adjustment (FF3/FF5) would be a natural extension "
    "but is out of scope for v1."
)

# Results are for a broad equity cross-section — not applicable to our basket.
PAPER_CLAIMED_SHARPE: float | None = None
PAPER_CLAIMED_CAGR: float | None = None
PAPER_CLAIMED_MAX_DD: float | None = None

ASSET_UNIVERSE: list[str] = ["SPY", "NIKKEI", "GOLD", "TREASURY", "OIL"]
POSITION_SIZING = "equal_weight"
REBALANCE_FREQUENCY = "monthly"
RISK_PROFILES: list[str] = ["aggressive"]

CURATOR_WALLET: str | None = None
CURATOR_NOTE = (
    "The momentum sleeve's factor-adjusted member. Pairs naturally with "
    "jegadeesh_titman_1993_cross_sectional_momentum: both rank a universe, "
    "but this one penalizes assets whose past performance is explained by "
    "riding the market rather than genuine alpha. In a high-beta-rising-market "
    "regime the two signals may diverge, which is precisely the informative "
    "comparison for the strategy library."
)
EXTRACTION_LLM: str | None = None

STATUS = "candidate"

BACKTEST_SHARPE: float | None = None
BACKTEST_CAGR: float | None = None
BACKTEST_MAX_DD: float | None = None
BACKTEST_WIN_RATE: float | None = None
BACKTEST_CALMAR: float | None = None
BACKTEST_CORR_SPY: float | None = None


class BlitzHanauerRMOM(bt.Strategy):
    """Rank assets by beta-adjusted formation-window return; long top, short bottom.

    Expects N>=2 data feeds (``self.datas[i]``). ``self.datas[0]`` is the
    market benchmark (SPY). Driven by ``engine.run_multi_backtest``.
    """

    # Runner contract: this class ranks across its whole declared universe
    # (engine.required_feeds() / cli.run_command's N-feed routing branch).
    REQUIRED_FEEDS = "UNIVERSE"

    params = (
        ("lookback", 252),  # formation window start (bars back)
        ("skip", 21),  # skip most-recent bars (short-term reversal control)
        ("rebalance_every", 21),  # ~monthly rebalance
        ("beta_window", 63),  # rolling window for beta estimation (bars)
        ("long_frac", 0.4),  # long the top 40% by residual return
        ("short_frac", 0.4),  # short the bottom 40%
        ("gross", 1.0),  # total gross exposure split equally across legs
    )

    def __init__(self) -> None:
        self._bars_since_rebalance = 0

    # ------------------------------------------------------------------
    # Pure-Python helpers (no numpy)
    # ------------------------------------------------------------------

    def _formation_return(self, data) -> float | None:
        """Total return from close[-lookback] to close[-skip]."""
        lookback = int(self.params.lookback)
        skip = int(self.params.skip)
        need = lookback + 1
        if len(data) < need:
            return None
        old = float(data.close[-lookback])
        recent = float(data.close[-skip])
        if old <= 0:
            return None
        return recent / old - 1.0

    def _daily_returns(self, data, n: int) -> list[float]:
        """Collect n most-recent daily simple returns (oldest first).

        Uses the n+1 most recent closes: close[-(n)] … close[0].
        Returns a list of length n; returns [] on any zero-price or
        insufficient-history condition.
        """
        if len(data) < n + 1:
            return []
        rets: list[float] = []
        for i in range(n, 0, -1):
            prev = float(data.close[-(i + 1)])
            curr = float(data.close[-i])
            if prev <= 0:
                return []
            rets.append(curr / prev - 1.0)
        return rets

    @staticmethod
    def _sample_cov(xs: list[float], ys: list[float]) -> float | None:
        """Sample covariance of two equal-length lists."""
        n = len(xs)
        if n < 2 or len(ys) != n:
            return None
        mx = sum(xs) / n
        my = sum(ys) / n
        return sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True)) / (n - 1)

    @staticmethod
    def _sample_var(xs: list[float]) -> float | None:
        """Sample variance of a list."""
        n = len(xs)
        if n < 2:
            return None
        mx = sum(xs) / n
        return sum((x - mx) ** 2 for x in xs) / (n - 1)

    def _rolling_beta(self, data) -> float | None:
        """OLS beta of data's daily returns vs self.datas[0] daily returns.

        Estimated over the most recent beta_window bars.
        Returns None if there is insufficient history or market variance is zero.
        """
        n = int(self.params.beta_window)
        if len(data) < n + 1 or len(self.datas[0]) < n + 1:
            return None
        asset_rets = self._daily_returns(data, n)
        mkt_rets = self._daily_returns(self.datas[0], n)
        if len(asset_rets) != n or len(mkt_rets) != n:
            return None
        cov = self._sample_cov(asset_rets, mkt_rets)
        var = self._sample_var(mkt_rets)
        if cov is None or var is None or var <= 0:
            return None
        return cov / var

    def _residual_return(self, data) -> float | None:
        """Beta-adjusted formation-window return.

        residual = total_asset_return - beta * total_market_return
        """
        asset_ret = self._formation_return(data)
        mkt_ret = self._formation_return(self.datas[0])
        if asset_ret is None or mkt_ret is None:
            return None
        beta = self._rolling_beta(data)
        if beta is None:
            # Fall back to unadjusted total return if beta cannot be estimated.
            return asset_ret
        return asset_ret - beta * mkt_ret

    # ------------------------------------------------------------------
    # backtrader entry point
    # ------------------------------------------------------------------

    def next(self) -> None:
        if len(self) <= int(self.params.lookback):
            return
        self._bars_since_rebalance += 1
        if self._bars_since_rebalance < int(self.params.rebalance_every):
            return
        self._bars_since_rebalance = 0

        # Rank all assets EXCEPT the market benchmark (datas[0]).
        candidates = self.datas[1:] if len(self.datas) > 1 else []
        if not candidates:
            self.order_target_percent(data=self.datas[0], target=0.0)
            return

        scored: list[tuple[float, object]] = []
        for d in candidates:
            rr = self._residual_return(d)
            if rr is not None:
                scored.append((rr, d))

        n = len(scored)
        if n < 2:
            return

        scored.sort(key=lambda x: x[0], reverse=True)  # best first

        n_long = max(1, int(round(n * float(self.params.long_frac))))
        n_short = max(1, int(round(n * float(self.params.short_frac))))
        # Prevent overlap.
        if n_long + n_short > n:
            n_short = max(1, n - n_long)

        longs = {id(d) for _, d in scored[:n_long]}
        shorts = {id(d) for _, d in scored[-n_short:]}
        long_w = (float(self.params.gross) / 2.0) / n_long
        short_w = (float(self.params.gross) / 2.0) / n_short

        # Market benchmark held flat.
        self.order_target_percent(data=self.datas[0], target=0.0)

        for _, d in scored:
            if id(d) in longs:
                self.order_target_percent(data=d, target=long_w)
            elif id(d) in shorts:
                self.order_target_percent(data=d, target=-short_w)
            else:
                self.order_target_percent(data=d, target=0.0)
