"""Alice's Adventures in Factorland — Arnott, Harvey, Kalesnik & Linnainmaa 2019.

Arnott et al. (2019) catalogue the blunders that plague naive factor investing
— overfit backtests, ignored trading costs, and aggressive tilts that do not
survive out of sample — and argue that defensive, robust exposures (low
volatility, low beta, steady profitability) hold up better than aggressive
factor chasing. This strategy builds a price-based defensive-quality composite:
reward steady, low-risk compounders; penalize high volatility and high beta.

Requires ``engine.run_multi_backtest`` (it reads every ``self.datas[i]`` each
bar to rank the universe). ``self.datas[0]`` is the market benchmark (SPY) and
is used as the beta reference.
"""

from __future__ import annotations

import math

import backtrader as bt

PAPER_ARXIV_ID: str | None = None
PAPER_TITLE = "Alice's Adventures in Factorland: Three Blunders That Plague Factor Investing"
PAPER_AUTHORS: list[str] = [
    "Robert D. Arnott",
    "Campbell R. Harvey",
    "Vitali Kalesnik",
    "Juhani T. Linnainmaa",
]
PAPER_VENUE = "The Journal of Portfolio Management"
PAPER_YEAR = 2019
PAPER_DOI = "10.3905/jpm.2019.45.4.018"
PAPER_CITATION_COUNT = 400  # Snapshot 2026-06; verify via Semantic Scholar.

# Defensive quality (low-vol, low-beta) tends to outperform in downturns.
REGIME_TAG: str = "bear"

METHODOLOGY_SUMMARY = (
    "Defensive-quality composite via price-based proxies. Each rebalance, "
    "score every asset by z(positive drift) - 0.5*z(volatility) - "
    "0.5*z(beta vs SPY), go long the highest defensive-quality names and "
    "short the lowest, hold to the next rebalance. Rewards steady, low-risk "
    "compounders over aggressive factor tilts."
)

METHODOLOGY_TEXT = (
    "Arnott, Harvey, Kalesnik & Linnainmaa (2019) caution that naive factor "
    "investing is undermined by three blunders: backtest overfitting, "
    "ignoring real-world trading costs, and crowding into aggressive tilts "
    "that do not replicate out of sample. They argue that defensive, robust "
    "exposures — low volatility, low beta, and steady profitability ('quality') "
    "— are more durable than aggressive factor chasing.\n\n"
    "v1 Archimedes adaptation (cross-asset, on the N-feed engine): we build a "
    "price-based defensive-quality composite. For each asset we compute (1) "
    "realized volatility over vol_window bars, (2) beta vs self.datas[0] (SPY) "
    "over beta_window bars via a pure-Python OLS cov/var, and (3) the mean "
    "daily return over vol_window (positive drift). The defensive-quality "
    "score is z(drift) - 0.5*z(vol) - 0.5*z(beta), where z(.) is the cross-"
    "sectional z-score across the universe. Each rebalance (~21 bars) we rank "
    "by this score, go long the highest defensive-quality fraction and short "
    "the lowest in equal weight (order_target_percent), holding to the next "
    "rebalance.\n\n"
    "Honest caveats: (1) these are PRICE-BASED PROXIES, not fundamental "
    "quality — realized vol and beta stand in for risk, and trailing drift "
    "stands in for profitability; there is no balance-sheet or earnings data "
    "here. (2) The paper studies broad equity factor portfolios, not a 5-asset "
    "multi-market basket, so it is context, not a like-for-like benchmark — "
    "paper_claimed_* are left null."
)

# Methodological survey of factor blunders; no clean Sharpe/CAGR for this
# composite on this basket → null (provenance discipline).
PAPER_CLAIMED_SHARPE: float | None = None
PAPER_CLAIMED_CAGR: float | None = None
PAPER_CLAIMED_MAX_DD: float | None = None

ASSET_UNIVERSE: list[str] = ["SPY", "NIKKEI", "GOLD", "TREASURY", "OIL"]
POSITION_SIZING = "equal_weight"
REBALANCE_FREQUENCY = "monthly"
RISK_PROFILES: list[str] = ["conservative"]

CURATOR_WALLET: str | None = None
CURATOR_NOTE = (
    "The defensive sleeve. Where the momentum members chase trends, this "
    "rewards steady, low-volatility, low-beta compounders — the robust "
    "exposures Arnott et al. (2019) argue survive out of sample. Disclosed "
    "clearly as price-based proxies, not fundamental quality."
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


class ArnottDefensiveQuality(bt.Strategy):
    """Rank by a defensive-quality composite; long the highest, short the lowest.

    Expects N>=2 data feeds (``self.datas[i]``); ``self.datas[0]`` is the
    market benchmark (SPY) used as the beta reference. Driven by
    ``engine.run_multi_backtest``.
    """

    # Runner contract: this class ranks across its whole declared universe
    # (engine.required_feeds() / cli.run_command's N-feed routing branch).
    REQUIRED_FEEDS = "UNIVERSE"

    params = (
        ("vol_window", 63),  # realized-vol / drift window (bars)
        ("beta_window", 63),  # beta-vs-benchmark window (bars)
        ("rebalance_every", 21),  # ~monthly
        ("long_frac", 0.4),  # long the top 40% of the universe
        ("short_frac", 0.4),  # short the bottom 40%
        ("gross", 1.0),  # total gross exposure (split across long + short legs)
    )

    def __init__(self) -> None:
        self._bars_since_rebalance = 0

    @staticmethod
    def _daily_returns(data, n: int) -> list[float] | None:
        if len(data) < n + 1:
            return None
        returns: list[float] = []
        for i in range(1, n + 1):
            prev = float(data.close[-i - 1])
            curr = float(data.close[-i])
            if prev > 0:
                returns.append((curr / prev) - 1.0)
        if len(returns) < 2:
            return None
        return returns

    def _mean_return(self, data, n: int) -> float | None:
        returns = self._daily_returns(data, n)
        if returns is None:
            return None
        return sum(returns) / len(returns)

    def _realized_vol(self, data, n: int) -> float | None:
        returns = self._daily_returns(data, n)
        if returns is None:
            return None
        mean = sum(returns) / len(returns)
        var = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
        return math.sqrt(var) * math.sqrt(_ANNUALIZATION)

    def _rolling_beta(self, data) -> float | None:
        """Pure-Python OLS beta of ``data`` returns on the benchmark returns."""
        n = int(self.params.beta_window)
        market = self.datas[0]
        asset_rets = self._daily_returns(data, n)
        market_rets = self._daily_returns(market, n)
        if asset_rets is None or market_rets is None:
            return None
        m = min(len(asset_rets), len(market_rets))
        if m < 2:
            return None
        asset_rets = asset_rets[:m]
        market_rets = market_rets[:m]
        mkt_mean = sum(market_rets) / m
        asset_mean = sum(asset_rets) / m
        cov = sum((market_rets[i] - mkt_mean) * (asset_rets[i] - asset_mean) for i in range(m)) / (m - 1)
        var = sum((market_rets[i] - mkt_mean) ** 2 for i in range(m)) / (m - 1)
        if var <= 0:
            return None
        return cov / var

    @staticmethod
    def _zscore(values: list[float]) -> list[float]:
        n = len(values)
        if n == 0:
            return []
        mean = sum(values) / n
        if n < 2:
            return [0.0 for _ in values]
        var = sum((v - mean) ** 2 for v in values) / (n - 1)
        std = math.sqrt(var)
        if std <= 0:
            return [0.0 for _ in values]
        return [(v - mean) / std for v in values]

    def next(self) -> None:
        warmup = max(int(self.params.vol_window), int(self.params.beta_window))
        if len(self) <= warmup:
            return
        self._bars_since_rebalance += 1
        if self._bars_since_rebalance < int(self.params.rebalance_every):
            return
        self._bars_since_rebalance = 0

        vw = int(self.params.vol_window)
        # Gather raw features for every asset that has full history.
        feats = []  # (data, drift, vol, beta)
        for d in self.datas:
            drift = self._mean_return(d, vw)
            vol = self._realized_vol(d, vw)
            beta = self._rolling_beta(d)
            if drift is None or vol is None or beta is None:
                continue
            if not (math.isfinite(drift) and math.isfinite(vol) and math.isfinite(beta)):
                continue
            feats.append((d, drift, vol, beta))

        n = len(feats)
        if n < 2:
            return

        drifts = self._zscore([f[1] for f in feats])
        vols = self._zscore([f[2] for f in feats])
        betas = self._zscore([f[3] for f in feats])

        scored = [(drifts[i] - 0.5 * vols[i] - 0.5 * betas[i], feats[i][0]) for i in range(n)]
        scored.sort(key=lambda x: x[0], reverse=True)  # most defensive-quality first

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
