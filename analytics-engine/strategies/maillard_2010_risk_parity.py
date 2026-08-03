"""Risk parity / inverse-volatility weighting (Maillard, Roncalli & Teïletche 2010).

Instead of equal-dollar weights (where the most volatile asset dominates the
portfolio's risk), weight each asset so it contributes comparable risk. This
implementation uses inverse-volatility weights — the diagonal special case of
the equal-risk-contribution (ERC) portfolio that Maillard et al. analyse, exact
when assets are equally correlated. Long-only, fully invested, rebalanced
monthly.

Requires ``engine.run_multi_backtest`` (it estimates each ``self.datas[i]``'s
volatility across the universe every rebalance).
"""

from __future__ import annotations

import math

import backtrader as bt

PAPER_ARXIV_ID: str | None = None
PAPER_TITLE = "The Properties of Equally Weighted Risk Contribution Portfolios"
PAPER_AUTHORS: list[str] = ["Sébastien Maillard", "Thierry Roncalli", "Jérôme Teïletche"]
PAPER_VENUE = "The Journal of Portfolio Management"
PAPER_YEAR = 2010
PAPER_DOI = "10.3905/jpm.2010.36.4.060"
PAPER_CITATION_COUNT = 1500  # Snapshot 2026-06; verify via Semantic Scholar.

# Diversification / risk-balancing — designed to hold across regimes.
REGIME_TAG: str = "regime_neutral"

METHODOLOGY_SUMMARY = (
    "Inverse-volatility risk parity. Each month weight every asset inversely to "
    "its recent volatility and normalize to fully invested, long-only — so a "
    "calm bond and a wild commodity contribute comparable risk rather than the "
    "volatile one dominating. The diagonal case of equal-risk-contribution."
)

METHODOLOGY_TEXT = (
    "Maillard, Roncalli & Teïletche (2010) formalize the equal-risk-contribution "
    "(ERC) portfolio, where every asset contributes the same share of total "
    "portfolio variance — a middle ground between minimum-variance and "
    "equal-weight that is far less concentrated than either. They show that when "
    "all pairwise correlations are equal, the ERC solution reduces exactly to "
    "INVERSE-VOLATILITY weights: w_i proportional to 1/sigma_i.\n\n"
    "v1 Archimedes adaptation (on the N-feed engine): we implement the "
    "inverse-volatility form rather than the full iterative ERC optimizer "
    "(which needs a numerical solver we deliberately don't add). Each rebalance "
    "(~21 bars) estimate each asset's daily-return volatility over a lookback "
    "(default 63 bars), set w_i ~ 1/sigma_i, normalize so the long-only weights "
    "sum to the deployed exposure, and order_target_percent to those weights. "
    "We disclose this as the inverse-vol approximation to ERC (exact under equal "
    "correlations) rather than claiming the full ERC portfolio.\n\n"
    "The paper is methodological (portfolio-construction properties), not a "
    "tradeable backtest with a headline Sharpe, so paper_claimed_* are null; the "
    "honest backtest fixture is authoritative."
)

PAPER_CLAIMED_SHARPE: float | None = None
PAPER_CLAIMED_CAGR: float | None = None
PAPER_CLAIMED_MAX_DD: float | None = None

ASSET_UNIVERSE: list[str] = ["SPY", "NIKKEI", "GOLD", "TREASURY", "OIL"]
POSITION_SIZING = "risk_parity"
REBALANCE_FREQUENCY = "monthly"
RISK_PROFILES: list[str] = ["conservative", "moderate"]

CURATOR_WALLET: str | None = None
CURATOR_NOTE = (
    "The diversification anchor of the multi-asset shelf. It is the only "
    "long-only, fully-invested, all-weather member — no ranking, no shorting, no "
    "rotation — so it is the natural low-turnover core to pair with the more "
    "tactical momentum and pairs sleeves. Inverse-vol weighting means the bond "
    "leg (TREASURY) typically carries the largest weight and oil/equities the "
    "smallest, balancing risk rather than dollars."
)
EXTRACTION_LLM: str | None = None

STATUS = "candidate"

BACKTEST_SHARPE: float | None = None
BACKTEST_CAGR: float | None = None
BACKTEST_MAX_DD: float | None = None
BACKTEST_WIN_RATE: float | None = None
BACKTEST_CALMAR: float | None = None
BACKTEST_CORR_SPY: float | None = None


class RiskParityInverseVol(bt.Strategy):
    """Inverse-volatility long-only weights, rebalanced monthly.

    Expects N>=2 data feeds (``self.datas[i]``). Driven by
    ``engine.run_multi_backtest``.
    """

    # Runner contract: this class weights across its whole declared universe
    # (engine.required_feeds() / cli.run_command's N-feed routing branch).
    REQUIRED_FEEDS = "UNIVERSE"

    params = (
        ("lookback", 63),  # volatility-estimation window (~3 months)
        ("rebalance_every", 21),  # ~monthly
        ("exposure", 0.99),  # fully-invested target (sum of long weights)
    )

    def __init__(self) -> None:
        self._bars_since_rebalance = 0

    def _volatility(self, data) -> float | None:
        n = int(self.params.lookback)
        if len(data) < n + 1:
            return None
        rets: list[float] = []
        for i in range(n):
            prev = float(data.close[-i - 1])
            curr = float(data.close[-i])
            if prev <= 0:
                return None
            rets.append(curr / prev - 1.0)
        if len(rets) < 2:
            return None
        mean = sum(rets) / len(rets)
        var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
        sigma = math.sqrt(var)
        return sigma if sigma > 0 else None

    def next(self) -> None:
        if len(self) <= int(self.params.lookback):
            return
        self._bars_since_rebalance += 1
        if self._bars_since_rebalance < int(self.params.rebalance_every):
            return
        self._bars_since_rebalance = 0

        inv_vols = [(self._volatility(d), d) for d in self.datas]
        inv_vols = [(1.0 / s, d) for s, d in inv_vols if s is not None]
        total = sum(w for w, _ in inv_vols)
        if total <= 0:
            return

        deployed = {id(d): (w / total) * float(self.params.exposure) for w, d in inv_vols}
        for d in self.datas:
            self.order_target_percent(data=d, target=deployed.get(id(d), 0.0))
