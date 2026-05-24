import backtrader as bt

PAPER_ARXIV_ID = None
PAPER_TITLE = "Buy-and-Hold Baseline"
PAPER_AUTHORS: list[str] = []
PAPER_VENUE = "internal"
PAPER_YEAR = 2026
PAPER_DOI = None
METHODOLOGY_TEXT = (
    "Allocate the full available cash to the asset on the first bar where no "
    "position is open; hold the position to the end of the period. Serves as "
    "the long-only baseline against which paper-grounded strategies are compared."
)
PAPER_CLAIMED_SHARPE = None
PAPER_CLAIMED_CAGR = None
PAPER_CLAIMED_MAX_DD = None

# Regime suitability: buy-and-hold baseline (long-only, bull-biased).
REGIME_TAG: str = "bull"

STATUS = "live"

# Backtest results — REAL (run via scripts/regen_buy_hold_fixture.py, 2004-01-02 → 2026-04-30)
# SPY buy-and-hold over full available history including 2008-09 crisis and 2022 correction
BACKTEST_SHARPE = 0.5371
BACKTEST_CAGR = 0.0864
BACKTEST_MAX_DD = 0.5627
BACKTEST_WIN_RATE = None  # Single entry, held to end; no closed-trade win rate
BACKTEST_CALMAR = 0.1535
BACKTEST_CORR_SPY = 1.0  # IS SPY
METHODOLOGY_SUMMARY = (
    "Allocate the full available cash to the asset on the first bar; hold "
    "to the end of the period. Long-only baseline against which "
    "paper-grounded strategies are compared."
)
ASSET_UNIVERSE: list[str] = ["SPY"]
POSITION_SIZING = "equal_weight"
REBALANCE_FREQUENCY = "daily"
RISK_PROFILES: list[str] = ["moderate"]
CURATOR_NOTE = "Internal baseline strategy — not paper-grounded. Used for benchmarking only."


class PipelineBuyHold(bt.Strategy):
    def next(self):
        if not self.position:
            # Invest all available cash
            price = self.data.close[0]
            size = int(self.broker.getcash() / price)
            if size > 0:
                self.buy(size=size)
