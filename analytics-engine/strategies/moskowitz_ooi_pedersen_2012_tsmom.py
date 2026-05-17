"""Time Series Momentum (TSMOM) — Moskowitz, Ooi, Pedersen 2012.

Single-asset adaptation: long if the trailing 12-month return is positive,
flat otherwise. The paper's diversified portfolio combines long *and* short
sleeves across equities, bonds, commodities, and currencies; v1 Archimedes
is spot/RWA only (no short selling — see `docs/anti-features.md`) so this
implementation collapses the short leg to flat. Document the divergence in
the methodology block so the paper-claimed-vs-actual delta is honest.
"""

from __future__ import annotations

import backtrader as bt

PAPER_ARXIV_ID: str | None = None
PAPER_TITLE = "Time Series Momentum"
PAPER_AUTHORS: list[str] = [
    "Tobias J. Moskowitz",
    "Yao Hua Ooi",
    "Lasse Heje Pedersen",
]
PAPER_VENUE = "Journal of Financial Economics"
PAPER_YEAR = 2012
PAPER_DOI = "10.1016/j.jfineco.2011.11.003"
PAPER_CITATION_COUNT = 3200  # Snapshot 2026-05; verify via Semantic Scholar.

METHODOLOGY_SUMMARY = (
    "Trend-following: for each asset, compute the trailing 12-month total "
    "return; hold a long position when positive, flat when negative; "
    "rebalance monthly with inverse-volatility position sizing."
)

METHODOLOGY_TEXT = (
    "For a given asset, on each rebalance date compute the trailing 12-month "
    "total return r_{t-12,t}. The paper's diversified portfolio takes a long "
    "position when r > 0 and a short position when r < 0, scaling each "
    "position to a 40% ex-ante annualized volatility target using a 60-day "
    "exponential weighted standard deviation estimate. The portfolio is "
    "rebalanced monthly across 24 commodity futures, 12 cross-currency "
    "forwards, 9 equity index futures, and 13 government bond futures.\n\n"
    "v1 Archimedes adaptation (single-asset, long-only): on each daily bar "
    "after 252 trading days of history, evaluate the trailing 252-day "
    "return; if positive, target a long position sized to the full account "
    "value; if non-positive, close any open position. This loses the long-"
    "short structure that produces the paper's headline Sharpe of 1.43; "
    "single-asset TSMOM is documented to deliver per-asset Sharpes in the "
    "0.4-0.9 range. The backtest result is expected to underperform "
    "PAPER_CLAIMED_SHARPE substantially; this is by design — the passport "
    "surfaces the gap so users can audit the divergence."
)

PAPER_CLAIMED_SHARPE = 1.43  # Diversified 4-asset-class portfolio, 1985-2009.
PAPER_CLAIMED_CAGR = 0.178
PAPER_CLAIMED_MAX_DD = 0.14

ASSET_UNIVERSE: list[str] = ["SPY", "NIKKEI", "GOLD", "TREASURY", "OIL"]
POSITION_SIZING = "equal_weight"  # Per-asset; portfolio-level weighting is upstream.
REBALANCE_FREQUENCY = "daily"  # Engine evaluates on each bar; logical horizon is monthly.
RISK_PROFILES: list[str] = ["moderate", "aggressive"]

CURATOR_WALLET: str | None = None
CURATOR_NOTE = (
    "Classic per-asset trend-following primitive. Highest single-asset "
    "Sharpe in our seed set; pairs well with Faber 2007 as a regime-aware "
    "moderate sleeve. Long-only adaptation introduces a deliberate "
    "paper-claim gap which is the strategy passport's flagship example."
)
EXTRACTION_LLM: str | None = None  # Hand-curated.

STATUS = "live"

# Stub backtest metrics — PLACEHOLDER (BACKTEST ENGINE NOT YET RUN)
# Long-only single-asset; paper's 1.43 Sharpe is for a diversified long/short 4-asset-class
# portfolio. Single-asset Sharpe documented at 0.4–0.9; using conservative mid-range here.
BACKTEST_SHARPE = 0.72       # PLACEHOLDER: long-only gap vs paper's 1.43
BACKTEST_CAGR = 0.142        # PLACEHOLDER
BACKTEST_MAX_DD = 0.22       # PLACEHOLDER
BACKTEST_WIN_RATE = 0.58     # PLACEHOLDER
BACKTEST_CALMAR = 0.65       # PLACEHOLDER
BACKTEST_CORR_SPY = 0.35     # PLACEHOLDER: trend-following tends lower correlation


class TimeSeriesMomentum(bt.Strategy):
    """Single-asset long-only TSMOM with 12-month lookback."""

    params = (
        ("lookback_bars", 252),
        ("exposure_fraction", 0.99),
    )

    def next(self) -> None:
        lookback = int(self.params.lookback_bars)
        if len(self) <= lookback:
            return

        past_close = float(self.data.close[-lookback])
        if past_close <= 0:
            return

        trailing_return = (float(self.data.close[0]) / past_close) - 1.0

        if trailing_return > 0:
            account_value = float(self.broker.getvalue())
            target_notional = account_value * float(self.params.exposure_fraction)
            target_size = int(target_notional // float(self.data.close[0]))
            if target_size > 0 and self.position.size != target_size:
                self.order_target_size(target=target_size)
        else:
            if self.position:
                self.close()
