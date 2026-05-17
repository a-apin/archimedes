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

STATUS = "candidate"
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
            self.buy(size=1)
