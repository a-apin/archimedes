"""Multi-Horizon Time-Series Momentum — Hurst, Ooi & Pedersen 2017,
"A Century of Evidence on Trend-Following Investing".

Blend time-series-momentum signals across three horizons — roughly 1 month, 3
months, and 12 months — and take a long position scaled by the net signal.
Hurst, Ooi & Pedersen (2017) show that a diversified trend-following strategy
combining short-, medium-, and long-term momentum has delivered positive,
crisis-robust returns for over a century across asset classes, and that blending
horizons is more robust than any single lookback (which is prone to a
period-specific "best" parameter).

Paper divergence: the original is a fully diversified, long-short, multi-asset
managed-futures program sized by volatility. This is a single-asset (SPY),
long-only adaptation: each horizon votes long/flat on SPY's own trend, and the
net vote scales exposure between cash and fully invested. The long-short and
cross-asset diversification that drive the paper's high Sharpe and low
correlation are absent, so the single-asset realized Sharpe is materially lower;
the value here is a horizon-diversified trend signal that is less fragile than a
single-lookback rule like SMA-200.
"""

from __future__ import annotations

import backtrader as bt

PAPER_ARXIV_ID: str | None = None
PAPER_TITLE = "A Century of Evidence on Trend-Following Investing"
PAPER_AUTHORS: list[str] = ["B. Hurst", "Y. H. Ooi", "L. H. Pedersen"]
PAPER_VENUE = "Journal of Portfolio Management"
PAPER_YEAR = 2017
PAPER_DOI = "10.3905/jpm.2017.44.1.015"
PAPER_CITATION_COUNT = 400  # Snapshot 2026-07; verify via Semantic Scholar.

# Regime suitability: trend-following is biased to trending (often bull) regimes
# and can whipsaw in choppy markets; horizon-blending softens but does not remove this.
REGIME_TAG: str = "bull"

METHODOLOGY_SUMMARY = (
    "Combine time-series-momentum signals over ~1-month, ~3-month, and "
    "~12-month lookbacks: each horizon votes long when SPY's return over that "
    "window is positive, flat otherwise. The net vote (fraction of horizons "
    "trending up) scales exposure from cash to fully invested. Blending "
    "horizons is more robust than any single lookback."
)

METHODOLOGY_TEXT = (
    "Hurst, Ooi & Pedersen (2017) construct a diversified trend-following "
    "strategy from equally-weighted 1-, 3-, and 12-month time-series-momentum "
    "signals across dozens of instruments, volatility-scaled and long-short. "
    "They document positive returns in most decades since 1880, with especially "
    "strong performance during sustained equity drawdowns ('crisis alpha').\n\n"
    "v1 Archimedes adaptation (single-asset, long-only): on each daily bar after "
    "252 bars of history, compute SPY's simple return over 21, 63, and 252 "
    "trading days using bars-ago closes (close[0] vs close[-L]). Each horizon "
    "contributes +1 if its return is positive, 0 otherwise; the net signal is "
    "the average of the three, in [0, 1]. Target equity weight = net_signal "
    "(so 0, 1/3, 2/3, or fully invested), rebalanced toward via "
    "order_target_percent. No short leg, no cross-asset diversification, no "
    "volatility scaling — the passport surfaces that these omissions are why the "
    "single-asset Sharpe falls well short of the paper's diversified program."
)

PAPER_CLAIMED_SHARPE = 1.0  # Diversified multi-asset long-short program, volatility-scaled.
PAPER_CLAIMED_CAGR = 0.11
PAPER_CLAIMED_MAX_DD = 0.26

ASSET_UNIVERSE: list[str] = ["SPY"]
POSITION_SIZING = "equal_weight"
REBALANCE_FREQUENCY = "monthly"
RISK_PROFILES: list[str] = ["moderate", "aggressive"]

CURATOR_WALLET: str | None = None
CURATOR_NOTE = (
    "Adds a horizon-diversified trend signal that is more robust than the "
    "library's single-lookback trend rules (Faber SMA-200, 52-week high) — it "
    "blends short/medium/long momentum so no single period dominates. Long-only "
    "single-asset by design; the passport surfaces the large gap to the paper's "
    "long-short, multi-asset, volatility-scaled managed-futures Sharpe so the "
    "user sees exactly which mechanisms were dropped for the on-chain spot "
    "adaptation."
)
EXTRACTION_LLM: str | None = None  # Hand-curated.

STATUS = "candidate"

# Stub backtest metrics — PLACEHOLDER (replaced by the real fixture / persisted
# backtest returns once the analytics engine runs this strategy; until then the
# rigor gate reads it honestly as "pending", not pass/fail).
BACKTEST_SHARPE = 0.52
BACKTEST_CAGR = 0.079
BACKTEST_MAX_DD = 0.22
BACKTEST_WIN_RATE = 0.41
BACKTEST_CALMAR = 0.36
BACKTEST_CORR_SPY = 0.74


class MultiHorizonTrend(bt.Strategy):
    """Long-only exposure scaled by the fraction of trend horizons pointing up."""

    params = (
        ("lookbacks", (21, 63, 252)),
        ("max_weight", 0.99),
    )

    def next(self) -> None:
        lookbacks = tuple(int(x) for x in self.params.lookbacks)
        longest = max(lookbacks)
        if len(self) <= longest:
            return

        current_close = float(self.data.close[0])
        if current_close <= 0:
            return

        votes = 0
        for lb in lookbacks:
            past_close = float(self.data.close[-lb])  # bars-ago close (backtrader convention)
            if past_close > 0 and (current_close / past_close - 1.0) > 0.0:
                votes += 1

        net_signal = votes / len(lookbacks)  # 0, 1/3, 2/3, or 1
        target_weight = min(net_signal, float(self.params.max_weight))
        self.order_target_percent(target=target_weight)
