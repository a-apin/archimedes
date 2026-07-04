"""Cointegration pairs trading (Engle-Granger + Ornstein-Uhlenbeck half-life).

Where the flagship distance rule (Gatev et al. 2006) trades the *price ratio*
of two co-moving assets, this strategy trades the residual of a *fitted
hedge relationship* and only does so when that residual is statistically
mean-reverting. It is the "proper" stat-arb construction:

  1. Estimate a hedge ratio beta by regressing leg A on leg B over a rolling
     formation window (the Engle-Granger first step).
  2. Form the spread = A - (alpha + beta * B) — the cointegrating residual.
  3. Gate on mean reversion: fit an AR(1) to the spread and require a finite,
     short Ornstein-Uhlenbeck half-life (the residual must actually revert).
  4. Trade the spread's z-score hedge-ratio-neutral (beta units of B per unit
     of A), open at +/-entry_z, close at +/-exit_z.

Anchored to Engle & Granger (1987) for the cointegration framework and
Vidyamurthy (2004) for its application to pairs trading.
"""

from __future__ import annotations

import math

import backtrader as bt
import numpy as np

PAPER_ARXIV_ID: str | None = None
PAPER_TITLE = "Co-integration and Error Correction: Representation, Estimation, and Testing"
PAPER_AUTHORS: list[str] = ["Robert F. Engle", "C. W. J. Granger"]
PAPER_VENUE = "Econometrica"
PAPER_YEAR = 1987
PAPER_DOI = "10.2307/1913236"
PAPER_CITATION_COUNT = (
    40000  # Snapshot 2026-06; one of the most-cited econometrics papers. Verify via Semantic Scholar.
)

# Relative-value / market-neutral — designed to be regime-agnostic.
REGIME_TAG: str = "regime_neutral"

METHODOLOGY_SUMMARY = (
    "Cointegration pairs trading. Regress leg A on leg B to get a hedge ratio, "
    "form the residual spread, require it to mean-revert (finite OU half-life), "
    "then trade its z-score hedge-ratio-neutral. The statistically-tested "
    "upgrade to the distance/z-score heuristic."
)

METHODOLOGY_TEXT = (
    "Engle & Granger (1987) formalize cointegration: two non-stationary price "
    "series can share a stationary linear combination (a long-run equilibrium). "
    "Their two-step procedure estimates the cointegrating vector by OLS, then "
    "tests the residual for stationarity. Vidyamurthy (2004) applies this to "
    "pairs trading: the stationary residual is a tradeable mean-reverting spread.\n\n"
    "v1 Archimedes adaptation (numpy-only, streaming on the two-feed engine):\n"
    "  1. Over a rolling formation window (default 252 bars) regress close_A on "
    "close_B with an intercept (OLS via least squares) -> hedge ratio beta and "
    "intercept alpha.\n"
    "  2. Spread_t = close_A_t - (alpha + beta * close_B_t).\n"
    "  3. Mean-reversion gate: fit an AR(1) Spread_t = c + phi * Spread_{t-1}. "
    "The Ornstein-Uhlenbeck half-life is -ln(2)/ln(phi); we only trade when "
    "0 < phi < 1 and the half-life lands in [min_half_life, max_half_life]. "
    "This is our numpy-only proxy for the Engle-Granger second-step residual "
    "stationarity test — we deliberately do NOT add statsmodels for a full "
    "Augmented Dickey-Fuller p-value; the AR(1)/half-life gate captures the "
    "same 'does the residual revert, and how fast' question the ADF test asks, "
    "and the simplification is disclosed here rather than hidden.\n"
    "  4. Trade the spread z-score (current spread vs its window mean/std) "
    "hedge-ratio-neutral: long-spread = long A and short beta units of B per "
    "unit of A; open at |z| >= entry_z (2.0), close at |z| <= exit_z (0.5).\n\n"
    "Engle & Granger is a methodology paper and reports no tradeable Sharpe or "
    "CAGR, so paper_claimed_* are null; the honest backtest fixture is "
    "authoritative."
)

# Methodology paper — no tradeable performance numbers to claim.
PAPER_CLAIMED_SHARPE: float | None = None
PAPER_CLAIMED_CAGR: float | None = None
PAPER_CLAIMED_MAX_DD: float | None = None

# Demo pair: EWA/EWC (iShares Australia vs Canada) — the textbook cointegrated
# pair (two commodity-exporter economies). See instruments.OPERATION_TO_SYMBOL.
ASSET_UNIVERSE: list[str] = ["EWA", "EWC"]
POSITION_SIZING = "equal_weight"  # hedge-ratio-neutral sizing within a fixed gross budget
REBALANCE_FREQUENCY = "daily"
RISK_PROFILES: list[str] = ["moderate", "aggressive"]

CURATOR_WALLET: str | None = None
CURATOR_NOTE = (
    "The statistically-principled member of the pairs sleeve. Versus the "
    "distance rule it adds two things: a fitted hedge ratio (so the traded "
    "spread is genuinely market-neutral, not just dollar-neutral) and a "
    "mean-reversion gate (it sits out when the residual isn't reverting). On "
    "EWA/EWC the cointegration is economically grounded in shared commodity "
    "terms-of-trade. Expect fewer, more-selective trades than the distance rule."
)
EXTRACTION_LLM: str | None = None

STATUS = "candidate"

BACKTEST_SHARPE: float | None = None
BACKTEST_CAGR: float | None = None
BACKTEST_MAX_DD: float | None = None
BACKTEST_WIN_RATE: float | None = None
BACKTEST_CALMAR: float | None = None
BACKTEST_CORR_SPY: float | None = None


class CointegrationPairsTrading(bt.Strategy):
    """Hedge-ratio-neutral z-score reversion gated on a finite OU half-life.

    Expects exactly two data feeds: ``self.datas[0]`` (leg A) and
    ``self.datas[1]`` (leg B). Driven by ``engine.run_pairs_backtest``.
    """

    # Runner contract (issue #887): this class hard-indexes self.datas[1], so a
    # single-feed run must fail closed with a typed error, not an IndexError.
    REQUIRED_FEEDS = 2

    params = (
        ("lookback", 252),  # formation window for OLS hedge ratio + spread stats
        ("entry_z", 2.0),
        ("exit_z", 0.5),
        ("min_half_life", 1.0),  # bars; reject sub-bar (numerical) reversion
        ("max_half_life", 126.0),  # ~6 months; reject too-slow / non-reverting residuals
        ("leg_fraction", 0.5),  # gross ~1.0x; leg A notional = leg_fraction * equity
    )

    def _window(self, feed_idx: int, n: int) -> np.ndarray:
        """Most-recent-first window of the last ``n`` closes for a feed."""
        return np.array([float(self.datas[feed_idx].close[-i]) for i in range(n)], dtype=float)

    @staticmethod
    def _ols_hedge(a: np.ndarray, b: np.ndarray) -> tuple[float, float]:
        """OLS of a on b with intercept -> (alpha, beta)."""
        design = np.column_stack([np.ones_like(b), b])
        coef, *_ = np.linalg.lstsq(design, a, rcond=None)
        return float(coef[0]), float(coef[1])

    @staticmethod
    def _half_life(spread: np.ndarray) -> float | None:
        """OU half-life from an AR(1) fit on the spread (oldest->newest order)."""
        if len(spread) < 3:
            return None
        lagged = spread[:-1]
        current = spread[1:]
        if float(np.ptp(lagged)) == 0.0:
            return None
        design = np.column_stack([np.ones_like(lagged), lagged])
        coef, *_ = np.linalg.lstsq(design, current, rcond=None)
        phi = float(coef[1])
        if not (0.0 < phi < 1.0):  # not mean-reverting (phi>=1) or oscillatory (phi<=0)
            return None
        return -math.log(2.0) / math.log(phi)

    def _signal(self) -> tuple[float, float] | None:
        """Return (z_score, beta) for the current bar, or None if no tradeable signal."""
        window = int(self.params.lookback)
        if len(self) <= window:
            return None
        a_desc = self._window(0, window)  # most-recent-first
        b_desc = self._window(1, window)
        # Oldest-first for the regression / AR(1) fit.
        a = a_desc[::-1]
        b = b_desc[::-1]

        alpha, beta = self._ols_hedge(a, b)
        spread = a - (alpha + beta * b)

        half_life = self._half_life(spread)
        if half_life is None or not (float(self.params.min_half_life) <= half_life <= float(self.params.max_half_life)):
            return None

        sigma = float(spread.std(ddof=1))
        if sigma <= 0:
            return None
        z = (spread[-1] - float(spread.mean())) / sigma
        return z, beta

    def _target_sizes(self, beta: float) -> tuple[int, int]:
        equity = float(self.broker.getvalue())
        leg_notional = equity * float(self.params.leg_fraction)
        price_a = float(self.datas[0].close[0])
        price_b = float(self.datas[1].close[0])
        if price_a <= 0 or price_b <= 0:
            return 0, 0
        size_a = int(leg_notional // price_a)
        # Hedge-ratio-neutral: beta units of B per unit of A.
        size_b = int(abs(beta) * size_a)
        return size_a, size_b

    def next(self) -> None:
        signal = self._signal()
        in_position = bool(self.getposition(self.datas[0]).size) or bool(self.getposition(self.datas[1]).size)

        if signal is None:
            # Residual no longer reverting (or warm-up): unwind any open spread.
            if in_position:
                self.close(data=self.datas[0])
                self.close(data=self.datas[1])
            return

        z, beta = signal

        if in_position and abs(z) <= float(self.params.exit_z):
            self.close(data=self.datas[0])
            self.close(data=self.datas[1])
            return
        if in_position:
            return
        if abs(z) < float(self.params.entry_z):
            return

        size_a, size_b = self._target_sizes(beta)
        if size_a <= 0 or size_b <= 0:
            return

        # beta sign orients leg B: for a normal positive cointegration beta,
        # long-spread = long A / short B. A negative beta flips B's direction.
        b_dir = 1.0 if beta >= 0 else -1.0
        if z >= float(self.params.entry_z):
            # Spread rich (A expensive vs hedge) -> short spread: short A, long beta*B.
            self.order_target_size(data=self.datas[0], target=-size_a)
            self.order_target_size(data=self.datas[1], target=int(b_dir * size_b))
        else:
            # Spread cheap -> long spread: long A, short beta*B.
            self.order_target_size(data=self.datas[0], target=size_a)
            self.order_target_size(data=self.datas[1], target=int(-b_dir * size_b))
