"""Low Idiosyncratic Volatility — Ang, Hodrick, Xing & Zhang 2006.

Ang et al. (2006) document that stocks with high idiosyncratic volatility
(residual vol relative to the Fama-French model) earn anomalously LOW
subsequent returns. Going long low-idiovol and short high-idiovol generates
a significant premium. This is a puzzle for standard risk-return theory and
has attracted extensive follow-up work.

This adaptation computes each asset's idiosyncratic volatility as the
standard deviation of its CAPM residuals (asset return minus beta-scaled
market return) over a rolling window. Assets are ranked by idiosyncratic
vol; we long the lowest (the 'quality' end) and short the highest.

Requires ``engine.run_multi_backtest``. ``self.datas[0]`` is the market
benchmark (assumed SPY) used for beta estimation.
"""

from __future__ import annotations

import math

import backtrader as bt

PAPER_ARXIV_ID: str | None = None
PAPER_TITLE = "The Cross-Section of Volatility and Expected Returns"
PAPER_AUTHORS: list[str] = [
    "Andrew Ang",
    "Robert J. Hodrick",
    "Yuhang Xing",
    "Xiaoyan Zhang",
]
PAPER_VENUE = "The Journal of Finance"
PAPER_YEAR = 2006
PAPER_DOI = "10.1111/j.1540-6261.2006.00836.x"
PAPER_CITATION_COUNT = 6500  # Snapshot 2026-06; verify via Semantic Scholar.

# Low-volatility anomaly is strongest in defensive/bear regimes when
# investors flee to lower-risk assets.
REGIME_TAG: str = "bear"

METHODOLOGY_SUMMARY = (
    "Rank assets by CAPM idiosyncratic volatility (std of beta-adjusted "
    "residual returns). Long the lowest-idiovol assets, short the highest. "
    "Exploits the low-volatility anomaly: high idiosyncratic vol predicts "
    "lower subsequent returns, contradicting the risk-return tradeoff."
)

METHODOLOGY_TEXT = (
    "Ang, Hodrick, Xing & Zhang (2006) show that stocks with high "
    "idiosyncratic volatility — measured as the standard deviation of "
    "Fama-French three-factor model residuals over the prior month — earn "
    "significantly lower returns over the following month. The effect is "
    "economically large: the authors report a -1.06%/month premium for a "
    "value-weighted quintile spread (long low-idiovol, short high-idiovol) "
    "on US stocks 1963-2000, robust to controlling for size, value, momentum, "
    "liquidity, and return reversals. The anomaly contradicts the standard "
    "risk-return intuition: investors appear to overpay for high-idiovol "
    "'lottery stocks', possibly due to underdiversification, leverage "
    "constraints, or preference for positive skewness.\n\n"
    "v1 Archimedes adaptation — CAPM residuals over a cross-asset universe: "
    "we simplify the three-factor model to a single-factor CAPM where the "
    "market proxy is self.datas[0] (SPY). For each asset we (1) estimate a "
    "rolling OLS beta over 'beta_window' bars using the covariance formula "
    "beta = cov(r_asset, r_mkt) / var(r_mkt), implemented in pure Python; "
    "(2) compute residual daily returns r_resid = r_asset - beta * r_mkt "
    "over the most recent 'vol_window' bars; (3) take the standard deviation "
    "of those residuals as the idiosyncratic volatility score. Assets are "
    "ranked by idiosyncratic vol ASCENDING (lower is better); the bottom "
    "long_frac are held long and the top short_frac are held short, each in "
    "equal weight, with positions rebalanced every ~21 bars.\n\n"
    "Honest caveats: (1) Ang et al.'s findings apply to a broad individual-"
    "stock cross-section, not a 5-asset multi-market basket; the -1.06%/month "
    "figure is context, not a benchmark for this adaptation — PAPER_CLAIMED_* "
    "are left null. (2) We use a single-factor CAPM rather than the Fama-"
    "French three-factor model; omitting the size and value factors means our "
    "residuals are noisier estimates of true idiosyncratic vol. (3) In a "
    "small cross-asset universe (SPY, NIKKEI, GOLD, TREASURY, OIL) the "
    "low-idiovol anomaly has a weaker theoretical footing because the assets "
    "are already diversified proxies rather than individual equities. "
    "(4) Short-selling costs are not modelled."
)

# Stock-level monthly-return anomaly — not a like-for-like benchmark for
# this cross-asset price-data adaptation.
PAPER_CLAIMED_SHARPE: float | None = None
PAPER_CLAIMED_CAGR: float | None = None
PAPER_CLAIMED_MAX_DD: float | None = None

ASSET_UNIVERSE: list[str] = ["SPY", "NIKKEI", "GOLD", "TREASURY", "OIL"]
POSITION_SIZING = "equal_weight"
REBALANCE_FREQUENCY = "monthly"
RISK_PROFILES: list[str] = ["conservative"]

CURATOR_WALLET: str | None = None
CURATOR_NOTE = (
    "Low-volatility sleeve. Long low-idiosyncratic-vol, short high-idiovol. "
    "One of the most cited anomaly papers in empirical asset pricing (~6500 "
    "citations). Defensive profile — regime tag 'bear' reflects the anomaly's "
    "stronger performance during risk-off episodes. Status is 'candidate' "
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


class AngHodrickLowIdioVol(bt.Strategy):
    """Long low-idiovol assets, short high-idiovol assets.

    Expects N>=2 data feeds (``self.datas[i]``). ``self.datas[0]`` is the
    market benchmark used for CAPM beta estimation. Driven by
    ``engine.run_multi_backtest``.
    """

    # Runner contract: this class ranks across its whole declared universe
    # (engine.required_feeds() / cli.run_command's N-feed routing branch).
    REQUIRED_FEEDS = "UNIVERSE"

    params = (
        ("vol_window", 22),  # bars for residual-vol estimation
        ("beta_window", 63),  # bars for rolling OLS beta estimation (~quarter)
        ("rebalance_every", 21),  # ~monthly rebalance
        ("long_frac", 0.4),  # long the lowest 40% by idiovol
        ("short_frac", 0.4),  # short the highest 40% by idiovol
        ("gross", 1.0),  # total gross exposure split evenly across legs
    )

    def __init__(self) -> None:
        self._bars_since_rebalance = 0

    def _daily_returns(self, data, n: int) -> list[float]:
        """Return the last *n* daily simple returns for *data* (oldest first).

        Returns an empty list when history is insufficient.
        """
        need = n + 1
        if len(data) < need:
            return []
        rets: list[float] = []
        for i in range(n, 0, -1):  # i = n, n-1, ..., 1  (oldest → newest)
            prev = float(data.close[-i - 1])
            curr = float(data.close[-i])
            if prev > 0:
                rets.append(curr / prev - 1.0)
        return rets

    def _rolling_beta(self, data) -> float | None:
        """Estimate CAPM beta of *data* vs self.datas[0] over beta_window bars.

        beta = cov(r_asset, r_mkt) / var(r_mkt), computed in pure Python.
        Returns None when history is insufficient or market variance is zero.
        """
        n = int(self.params.beta_window)
        mkt = self.datas[0]
        r_asset = self._daily_returns(data, n)
        r_mkt = self._daily_returns(mkt, n)
        # Align lengths (both should be n but guard against differing history).
        length = min(len(r_asset), len(r_mkt))
        if length < 2:
            return None
        r_asset = r_asset[-length:]
        r_mkt = r_mkt[-length:]

        mean_a = sum(r_asset) / length
        mean_m = sum(r_mkt) / length
        cov = sum((r_asset[i] - mean_a) * (r_mkt[i] - mean_m) for i in range(length)) / (length - 1)
        var_m = sum((r_mkt[i] - mean_m) ** 2 for i in range(length)) / (length - 1)
        if var_m <= 0.0:
            return None
        return cov / var_m

    def _idiosyncratic_vol(self, data) -> float | None:
        """Compute idiosyncratic volatility for *data*.

        Idiosyncratic vol = std(r_asset - beta * r_mkt) over vol_window bars.
        Returns None when history is insufficient or estimation fails.
        """
        beta = self._rolling_beta(data)
        if beta is None:
            return None

        n = int(self.params.vol_window)
        mkt = self.datas[0]
        r_asset = self._daily_returns(data, n)
        r_mkt = self._daily_returns(mkt, n)
        length = min(len(r_asset), len(r_mkt))
        if length < 2:
            return None
        r_asset = r_asset[-length:]
        r_mkt = r_mkt[-length:]

        residuals = [r_asset[i] - beta * r_mkt[i] for i in range(length)]
        mean_r = sum(residuals) / length
        variance = sum((r - mean_r) ** 2 for r in residuals) / (length - 1)
        if variance < 0.0:
            return None
        return math.sqrt(variance)

    def next(self) -> None:
        # Need at least beta_window bars of history before we can act.
        need = max(int(self.params.beta_window), int(self.params.vol_window))
        if len(self) <= need:
            return
        self._bars_since_rebalance += 1
        if self._bars_since_rebalance < int(self.params.rebalance_every):
            return
        self._bars_since_rebalance = 0

        # Score each asset; skip market benchmark (datas[0]) from the ranking
        # because including it would always assign beta=1 and residual vol=0.
        scored: list[tuple[float, object]] = []
        for d in self.datas[1:]:  # exclude market benchmark
            iv = self._idiosyncratic_vol(d)
            if iv is not None:
                scored.append((iv, d))
        n = len(scored)
        if n < 2:
            return
        scored.sort(key=lambda x: x[0])  # ascending: lowest idiovol first

        n_long = max(1, int(round(n * float(self.params.long_frac))))
        n_short = max(1, int(round(n * float(self.params.short_frac))))
        # Never let the long and short buckets overlap.
        if n_long + n_short > n:
            n_short = max(1, n - n_long)

        longs = {id(d) for _, d in scored[:n_long]}
        shorts = {id(d) for _, d in scored[-n_short:]}
        long_w = (float(self.params.gross) / 2.0) / n_long
        short_w = (float(self.params.gross) / 2.0) / n_short

        # Always flatten the market benchmark — it is used only as a factor.
        self.order_target_percent(data=self.datas[0], target=0.0)

        for _, d in scored:
            if id(d) in longs:
                self.order_target_percent(data=d, target=long_w)
            elif id(d) in shorts:
                self.order_target_percent(data=d, target=-short_w)
            else:
                self.order_target_percent(data=d, target=0.0)
