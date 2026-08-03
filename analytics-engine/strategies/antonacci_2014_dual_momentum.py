"""Dual momentum (Antonacci 2014, "Dual Momentum Investing").

Combine two momentum signals. RELATIVE momentum picks the strongest asset among
a set of risky assets. ABSOLUTE momentum (a.k.a. time-series momentum) is a
trend filter: only hold that winner if its own trailing return is positive;
otherwise retreat to a defensive asset (bonds / cash). The result is a
concentrated, one-asset-at-a-time rotation that sidesteps the worst of bear
markets.

Requires ``engine.run_multi_backtest`` (it inspects every risky ``self.datas[i]``
plus the defensive leg each rebalance). Antonacci's "Global Equities Momentum"
(GEM) is the canonical implementation.
"""

from __future__ import annotations

import backtrader as bt

PAPER_ARXIV_ID: str | None = None
PAPER_TITLE = "Risk Premia Harvesting Through Dual Momentum"
PAPER_AUTHORS: list[str] = ["Gary Antonacci"]
PAPER_VENUE = "Journal of Portfolio Management (Vol. 42, No. 1); SSRN 2042750"
PAPER_YEAR = 2014
PAPER_DOI = "10.2139/ssrn.2042750"
PAPER_CITATION_COUNT = 300  # Snapshot 2026-06; verify via Semantic Scholar.

# Trend-following with a defensive switch — built for bull regimes, defends in bear.
REGIME_TAG: str = "bull"

METHODOLOGY_SUMMARY = (
    "Dual momentum: relative momentum picks the strongest risky asset; absolute "
    "momentum (a positive trailing return) decides whether to hold it at all or "
    "rotate to a defensive bond/cash asset. One asset held at a time, rebalanced "
    "monthly. Captures trend while stepping aside in downtrends."
)

METHODOLOGY_TEXT = (
    "Antonacci (2014) combines two momentum effects. RELATIVE momentum: among a "
    "set of risky assets, hold whichever has the highest trailing (e.g. 12-month) "
    "return. ABSOLUTE momentum: only hold that asset if its own trailing return "
    "beats the risk-free rate (otherwise the market is in a downtrend and you "
    "rotate to bonds/cash). His 'Global Equities Momentum' applies this to US vs "
    "international equities with a bond fallback, and he reports materially "
    "higher risk-adjusted returns and smaller drawdowns than buy-and-hold over "
    "1974-2013.\n\n"
    "v1 Archimedes adaptation (on the N-feed engine): the risky set is all "
    "universe assets EXCEPT the defensive leg; the defensive leg is TREASURY "
    "(TLT). Each rebalance (~21 bars) compute every asset's trailing return "
    "(close[0]/close[-lookback]-1, lookback 252). Relative step: pick the best "
    "risky asset. Absolute step: if its trailing return > the risk-free hurdle "
    "(~5%/yr) hold it 100%; else hold the defensive leg 100%.\n\n"
    "Honest caveats: Antonacci's headline figures are for his specific GEM asset "
    "set over his specific window, NOT this 5-asset basket — they are context, "
    "not a like-for-like benchmark, so paper_claimed_* are null. The source is a "
    "book/working paper, which typically reports no clean single-number Sharpe to "
    "lift anyway."
)

PAPER_CLAIMED_SHARPE: float | None = None
PAPER_CLAIMED_CAGR: float | None = None
PAPER_CLAIMED_MAX_DD: float | None = None

# One asset held at a time; "equal_weight" is the closest valid PositionSizing
# enum value (it trivially equal-weights its single held leg) — the engine has
# no "concentrated" member, and a single-asset-rotation matches the convention
# used by the other single-asset strategies (Faber, TSMOM).
ASSET_UNIVERSE: list[str] = ["SPY", "NIKKEI", "GOLD", "TREASURY", "OIL"]
POSITION_SIZING = "equal_weight"
REBALANCE_FREQUENCY = "monthly"
RISK_PROFILES: list[str] = ["moderate", "aggressive"]

CURATOR_WALLET: str | None = None
CURATOR_NOTE = (
    "The defensive-rotation member of the momentum sleeve. Unlike cross-sectional "
    "momentum (always long/short) or TSMOM (per-asset trend), dual momentum holds "
    "exactly one asset and explicitly retreats to TREASURY when nothing is "
    "trending up — so its appeal is drawdown control in bear markets, not raw "
    "return. TREASURY is hard-coded as the defensive leg; if it is absent from "
    "the universe the strategy holds cash instead."
)
EXTRACTION_LLM: str | None = None

STATUS = "candidate"

BACKTEST_SHARPE: float | None = None
BACKTEST_CAGR: float | None = None
BACKTEST_MAX_DD: float | None = None
BACKTEST_WIN_RATE: float | None = None
BACKTEST_CALMAR: float | None = None
BACKTEST_CORR_SPY: float | None = None

# Symbol (yfinance ticker) used as the defensive leg. TREASURY -> TLT.
_DEFENSIVE_SYMBOL = "TLT"


class DualMomentum(bt.Strategy):
    """Relative + absolute momentum rotation across the universe.

    Expects N>=2 data feeds. The defensive leg is identified by its feed name
    (TLT); if no feed is named TLT, the absolute-momentum 'risk-off' state holds
    cash. Driven by ``engine.run_multi_backtest``.
    """

    # Runner contract: this class ranks/rotates across its whole declared
    # universe (engine.required_feeds() / cli.run_command's N-feed branch).
    REQUIRED_FEEDS = "UNIVERSE"

    params = (
        ("lookback", 252),  # trailing-return window (bars)
        ("rebalance_every", 21),  # ~monthly
        ("rf_annual", 0.05),  # absolute-momentum hurdle (risk-free rate)
        ("exposure", 0.99),  # fraction of equity deployed to the held asset
    )

    def __init__(self) -> None:
        self._bars_since_rebalance = 0
        # backtrader exposes a feed's run_multi_backtest name as ``_name``;
        # getattr keeps it tidy (and survives an unnamed feed in unit tests).
        self._defensive = next((d for d in self.datas if getattr(d, "_name", None) == _DEFENSIVE_SYMBOL), None)

    def _trailing_return(self, data) -> float | None:
        need = int(self.params.lookback) + 1
        if len(data) < need:
            return None
        old = float(data.close[-int(self.params.lookback)])
        if old <= 0:
            return None
        return float(data.close[0]) / old - 1.0

    def _go_all_in(self, target_data) -> None:
        for d in self.datas:
            if d is target_data:
                self.order_target_percent(data=d, target=float(self.params.exposure))
            else:
                self.order_target_percent(data=d, target=0.0)

    def _go_to_cash(self) -> None:
        for d in self.datas:
            self.order_target_percent(data=d, target=0.0)

    def next(self) -> None:
        if len(self) <= int(self.params.lookback):
            return
        self._bars_since_rebalance += 1
        if self._bars_since_rebalance < int(self.params.rebalance_every):
            return
        self._bars_since_rebalance = 0

        risky = [d for d in self.datas if d is not self._defensive]
        scored = [(self._trailing_return(d), d) for d in risky]
        scored = [(r, d) for r, d in scored if r is not None]
        if not scored:
            return

        # Relative momentum: strongest risky asset.
        best_ret, best = max(scored, key=lambda x: x[0])

        # Absolute momentum: hold the winner only if it clears the risk-free hurdle.
        hurdle = float(self.params.rf_annual) * (int(self.params.lookback) / 252.0)
        if best_ret > hurdle:
            self._go_all_in(best)
        elif self._defensive is not None:
            self._go_all_in(self._defensive)
        else:
            self._go_to_cash()
