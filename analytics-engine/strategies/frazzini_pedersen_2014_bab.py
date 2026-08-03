"""Betting Against Beta — Frazzini & Pedersen (2014).

Long leveraged low-beta assets, short deleveraged high-beta assets.
The low-beta anomaly contradicts CAPM: constrained investors' preference
for high-beta assets drives a flat or inverted Security Market Line, so
BAB earns alpha from the other side of that tilt.

Requires ``engine.run_multi_backtest`` (ranks assets cross-sectionally by
estimated rolling beta). ``self.datas[0]`` is treated as the market benchmark
(assumed SPY); the remaining feeds are the investable universe.
"""

from __future__ import annotations

import backtrader as bt

PAPER_ARXIV_ID: str | None = None
PAPER_TITLE = "Betting Against Beta"
PAPER_AUTHORS: list[str] = ["Andrea Frazzini", "Lasse Heje Pedersen"]
PAPER_VENUE = "Journal of Financial Economics"
PAPER_YEAR = 2014
PAPER_DOI = "10.1016/j.jfineco.2013.10.005"
PAPER_CITATION_COUNT = 4200  # Snapshot 2026-06; verify via Semantic Scholar.

# Low-beta strategies outperform in down / stressed markets.
REGIME_TAG: str = "bear"

METHODOLOGY_SUMMARY = (
    "Estimate rolling OLS beta for each asset versus the market benchmark. "
    "Long the lowest-beta assets (leveraged to unit beta) and short the "
    "highest-beta assets (deleveraged to unit beta), earning the spread "
    "between the Security Market Line's slope and the actual flat/inverted SML."
)

METHODOLOGY_TEXT = (
    "Frazzini & Pedersen (2014) document that the Security Market Line is "
    "empirically flat or even inverted: high-beta assets earn lower "
    "risk-adjusted returns than CAPM predicts, and low-beta assets earn "
    "higher ones. The mechanism is leverage constraints: institutional "
    "investors who cannot lever up buy high-beta equities to increase "
    "expected returns, bidding up their prices and compressing their "
    "future returns. BAB exploits this by going long a portfolio of "
    "low-beta assets leveraged to unit beta and short a portfolio of "
    "high-beta assets deleveraged to unit beta.\n\n"
    "v1 Archimedes adaptation (cross-asset, N-feed engine): each rebalance "
    "(every ~21 bars) we estimate a rolling 63-day OLS beta for each asset "
    "vs datas[0] (the market benchmark, assumed SPY) using daily close "
    "returns. Assets are ranked by beta; the bottom long_frac are held long "
    "and the top short_frac are held short, in equal weight within each leg "
    "(order_target_percent). No explicit leverage rescaling to unit beta is "
    "applied — only the sign and equal weight within each leg — because the "
    "core insight (long low-beta, short high-beta) survives without it.\n\n"
    "Honest caveats: (1) The paper's results are for a broad US equity "
    "cross-section (thousands of stocks); our 5-asset basket is a conceptual "
    "demonstration of the signal, not a replication — paper_claimed_* are "
    "left null. (2) Beta is estimated from daily price-only data with no "
    "dividend or factor adjustment; measurement error in beta is higher for "
    "a small universe. (3) With only a handful of assets the long/short "
    "buckets may each contain only one or two names, so diversification is "
    "thin and idiosyncratic risk dominates."
)

# Results are for a broad US equity cross-section — not applicable to our basket.
PAPER_CLAIMED_SHARPE: float | None = None
PAPER_CLAIMED_CAGR: float | None = None
PAPER_CLAIMED_MAX_DD: float | None = None

ASSET_UNIVERSE: list[str] = ["SPY", "NIKKEI", "GOLD", "TREASURY", "OIL"]
POSITION_SIZING = "equal_weight"
REBALANCE_FREQUENCY = "monthly"
RISK_PROFILES: list[str] = ["conservative", "moderate"]

CURATOR_WALLET: str | None = None
CURATOR_NOTE = (
    "The beta-anomaly sleeve. Complements the momentum strategies: momentum "
    "is a bull-regime signal, BAB works best in bear/stressed regimes when "
    "high-beta assets draw down sharply. Low-beta assets (Treasuries, gold) "
    "in the long leg provide natural flight-to-quality exposure."
)
EXTRACTION_LLM: str | None = None

STATUS = "candidate"

BACKTEST_SHARPE: float | None = None
BACKTEST_CAGR: float | None = None
BACKTEST_MAX_DD: float | None = None
BACKTEST_WIN_RATE: float | None = None
BACKTEST_CALMAR: float | None = None
BACKTEST_CORR_SPY: float | None = None


class FrazziniPedersenBAB(bt.Strategy):
    """Rank assets by rolling OLS beta; long low-beta, short high-beta.

    Expects N>=2 data feeds (``self.datas[i]``). ``self.datas[0]`` is the
    market benchmark (SPY). Driven by ``engine.run_multi_backtest``.
    """

    # Runner contract: this class ranks across its whole declared universe
    # (engine.required_feeds() / cli.run_command's N-feed routing branch).
    REQUIRED_FEEDS = "UNIVERSE"

    params = (
        ("lookback", 63),  # rolling window for beta estimation (bars)
        ("rebalance_every", 21),  # ~monthly rebalance
        ("min_history", 64),  # minimum bars before any position is taken
        ("long_frac", 0.4),  # long the bottom 40% (lowest beta) of the universe
        ("short_frac", 0.4),  # short the top 40% (highest beta)
        ("gross", 1.0),  # total gross exposure split equally across legs
    )

    def __init__(self) -> None:
        self._bars_since_rebalance = 0

    # ------------------------------------------------------------------
    # Pure-Python helpers (no numpy)
    # ------------------------------------------------------------------

    def _daily_returns(self, data, n: int) -> list[float]:
        """Collect n most-recent daily simple returns.

        Returns a list of length n where element 0 is the oldest and element
        n-1 is the most recent. ``data.close[-i]`` accesses i bars back from
        the current bar (close[0] is the current close).
        """
        rets: list[float] = []
        # We need n consecutive (prev, curr) close pairs.
        # Pair i: prev = close[-(i+1)], curr = close[-i]  (i from n down to 1)
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

        Returns None if there is insufficient history or variance is zero.
        """
        n = int(self.params.lookback)
        # Need n+1 closes to produce n returns.
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

    # ------------------------------------------------------------------
    # backtrader entry point
    # ------------------------------------------------------------------

    def next(self) -> None:
        if len(self) <= int(self.params.min_history):
            return
        self._bars_since_rebalance += 1
        if self._bars_since_rebalance < int(self.params.rebalance_every):
            return
        self._bars_since_rebalance = 0

        # Compute betas for all assets EXCEPT the market benchmark (datas[0]).
        # If there are no other assets, nothing to rank.
        candidates = self.datas[1:] if len(self.datas) > 1 else []
        if not candidates:
            # Single-asset universe: flatten to zero.
            self.order_target_percent(data=self.datas[0], target=0.0)
            return

        scored: list[tuple[float, object]] = []
        for d in candidates:
            beta = self._rolling_beta(d)
            if beta is not None:
                scored.append((beta, d))

        n = len(scored)
        if n < 1:
            return
        if n == 1:
            # Cannot form both legs — flatten.
            self.order_target_percent(data=scored[0][1], target=0.0)
            return

        scored.sort(key=lambda x: x[0])  # ascending: lowest beta first

        n_long = max(1, int(round(n * float(self.params.long_frac))))
        n_short = max(1, int(round(n * float(self.params.short_frac))))
        # Prevent overlap.
        if n_long + n_short > n:
            n_short = max(1, n - n_long)

        longs = {id(d) for _, d in scored[:n_long]}
        shorts = {id(d) for _, d in scored[-n_short:]}
        long_w = (float(self.params.gross) / 2.0) / n_long
        short_w = (float(self.params.gross) / 2.0) / n_short

        # Market benchmark is never held directly; set to flat.
        self.order_target_percent(data=self.datas[0], target=0.0)

        for _, d in scored:
            if id(d) in longs:
                self.order_target_percent(data=d, target=long_w)
            elif id(d) in shorts:
                self.order_target_percent(data=d, target=-short_w)
            else:
                self.order_target_percent(data=d, target=0.0)
