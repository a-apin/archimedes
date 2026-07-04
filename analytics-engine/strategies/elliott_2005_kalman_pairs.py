"""Kalman-filter dynamic-hedge pairs trading (Elliott, van der Hoek & Malcolm 2005).

The distance and cointegration rules both assume a *static* relationship
between the two legs over the formation window. Real pairs drift — the hedge
ratio that linked them last year is not the one that links them today, and a
stale hedge ratio is the #1 cause of static-pair blow-ups. This strategy
treats the hedge ratio (and intercept) as hidden states evolving as a random
walk, and re-estimates them every bar with a Kalman filter. The filter's
one-step forecast error is the spread; its forecast standard deviation sets a
self-scaling entry band.

Anchored to Elliott, van der Hoek & Malcolm (2005), "Pairs Trading", which
casts the spread as a mean-reverting state-space model. The linear-Gaussian
implementation here follows the standard practitioner formulation (e.g. Chan,
*Algorithmic Trading*) of a time-varying hedge ratio.
"""

from __future__ import annotations

import math

import backtrader as bt
import numpy as np

PAPER_ARXIV_ID: str | None = None
PAPER_TITLE = "Pairs Trading"
PAPER_AUTHORS: list[str] = ["Robert J. Elliott", "John van der Hoek", "William P. Malcolm"]
PAPER_VENUE = "Quantitative Finance"
PAPER_YEAR = 2005
PAPER_DOI = "10.1080/14697680500149370"
PAPER_CITATION_COUNT = 900  # Snapshot 2026-06; verify via Semantic Scholar.

REGIME_TAG: str = "regime_neutral"

METHODOLOGY_SUMMARY = (
    "Kalman-filter dynamic-hedge pairs trading. Treat the hedge ratio and "
    "intercept between two legs as random-walk states re-estimated every bar; "
    "the filter's forecast error is the spread and its forecast std sets a "
    "self-scaling entry band. Handles drifting relationships a static hedge can't."
)

METHODOLOGY_TEXT = (
    "Elliott, van der Hoek & Malcolm (2005) model the spread between two assets "
    "as a mean-reverting state observed in Gaussian noise, and use a Kalman "
    "filter to estimate it. The practitioner formulation used here makes the "
    "hedge relationship itself the hidden state:\n"
    "  - State x = [beta, intercept], evolving as a random walk "
    "(transition = identity, process-noise covariance W = delta/(1-delta) * I, "
    "with delta small so the hedge ratio moves slowly).\n"
    "  - Observation y_t = close_A_t with observation matrix H_t = [close_B_t, 1] "
    "and scalar observation-noise variance Ve.\n"
    "  - Each bar: predict (P += W), compute forecast error e_t = y_t - H_t x and "
    "its variance Q_t = H_t P H_t^T + Ve, Kalman gain K = P H_t^T / Q_t, then "
    "update x and P. e_t is the live spread; sqrt(Q_t) its one-step std.\n"
    "  - Trade self-scaling on the forecast error: enter long-spread (long A, "
    "short beta*B) when e_t < -entry * sqrt(Q_t), short-spread when "
    "e_t > +entry * sqrt(Q_t); exit when e_t reverts through "
    "+/-exit * sqrt(Q_t). A warm-up period lets beta stabilise before trading.\n\n"
    "Because beta is re-estimated every bar, the hedge tracks a drifting "
    "relationship (e.g. miners' changing leverage to the gold price) instead of "
    "freezing a formation-window value. The paper is methodological and reports "
    "no tradeable Sharpe/CAGR, so paper_claimed_* are null; the honest backtest "
    "fixture is authoritative."
)

PAPER_CLAIMED_SHARPE: float | None = None
PAPER_CLAIMED_CAGR: float | None = None
PAPER_CLAIMED_MAX_DD: float | None = None

# Demo pair: GLD/GDX (gold spot ETF vs gold-miner ETF). Their relationship
# drifts with miners' operating leverage, so it is a natural showcase for a
# dynamic hedge versus the static-hedge GLD/GDX distance strategy.
ASSET_UNIVERSE: list[str] = ["GLD", "GDX"]
POSITION_SIZING = "equal_weight"  # hedge-ratio-neutral within a fixed gross budget
REBALANCE_FREQUENCY = "daily"
RISK_PROFILES: list[str] = ["moderate", "aggressive"]

CURATOR_WALLET: str | None = None
CURATOR_NOTE = (
    "The adaptive member of the pairs sleeve. It shares GLD/GDX with the "
    "flagship distance strategy on purpose: same pair, but a time-varying "
    "hedge ratio instead of a fixed one, so the two make a clean A/B on whether "
    "dynamic hedging earns its keep on a relationship known to drift. Self-"
    "scaling entry bands mean it adapts its thresholds to changing volatility "
    "without re-tuning."
)
EXTRACTION_LLM: str | None = None

STATUS = "candidate"

BACKTEST_SHARPE: float | None = None
BACKTEST_CAGR: float | None = None
BACKTEST_MAX_DD: float | None = None
BACKTEST_WIN_RATE: float | None = None
BACKTEST_CALMAR: float | None = None
BACKTEST_CORR_SPY: float | None = None


class KalmanPairsTrading(bt.Strategy):
    """Time-varying hedge ratio via a Kalman filter; self-scaling spread bands.

    Expects exactly two data feeds: ``self.datas[0]`` (leg A, the observation)
    and ``self.datas[1]`` (leg B, the regressor). Driven by
    ``engine.run_pairs_backtest``.
    """

    # Runner contract (issue #887): this class hard-indexes self.datas[1], so a
    # single-feed run must fail closed with a typed error, not an IndexError.
    REQUIRED_FEEDS = 2

    params = (
        ("delta", 1e-4),  # state-drift scale; W = delta/(1-delta) * I (small => slow hedge drift)
        ("ve", 1e-3),  # observation-noise variance
        ("entry", 1.5),  # enter when |forecast error| > entry * forecast std
        ("exit", 0.5),  # exit when |forecast error| < exit * forecast std
        ("warmup", 30),  # bars to let beta stabilise before trading
        ("leg_fraction", 0.5),  # gross ~1.0x; leg A notional = leg_fraction * equity
    )

    def __init__(self) -> None:
        # Hidden state x = [beta, intercept]; covariance P.
        self._x = np.zeros(2, dtype=float)
        self._p = np.eye(2, dtype=float)  # diffuse-ish prior
        self._w = (float(self.params.delta) / (1.0 - float(self.params.delta))) * np.eye(2, dtype=float)
        self._seen = 0

    def _target_sizes(self, beta: float) -> tuple[int, int]:
        equity = float(self.broker.getvalue())
        leg_notional = equity * float(self.params.leg_fraction)
        price_a = float(self.datas[0].close[0])
        price_b = float(self.datas[1].close[0])
        if price_a <= 0 or price_b <= 0:
            return 0, 0
        size_a = int(leg_notional // price_a)
        size_b = int(abs(beta) * size_a)
        return size_a, size_b

    def next(self) -> None:
        y = float(self.datas[0].close[0])  # observation: leg A price
        h = np.array([float(self.datas[1].close[0]), 1.0], dtype=float)  # [leg B price, 1]

        # ── Kalman predict + update ─────────────────────────────────
        p_pred = self._p + self._w
        e = y - float(h @ self._x)  # one-step forecast error == live spread
        q = float(h @ p_pred @ h) + float(self.params.ve)  # forecast variance
        if q <= 0:
            return
        k = (p_pred @ h) / q  # Kalman gain (2-vector)
        self._x = self._x + k * e
        self._p = p_pred - np.outer(k, h @ p_pred)
        self._seen += 1

        beta = float(self._x[0])
        std = math.sqrt(q)

        if self._seen < int(self.params.warmup) or std <= 0:
            return

        in_position = bool(self.getposition(self.datas[0]).size) or bool(self.getposition(self.datas[1]).size)

        # Exit when the forecast error reverts inside the inner band.
        if in_position and abs(e) <= float(self.params.exit) * std:
            self.close(data=self.datas[0])
            self.close(data=self.datas[1])
            return
        if in_position:
            return
        if abs(e) < float(self.params.entry) * std:
            return

        size_a, size_b = self._target_sizes(beta)
        if size_a <= 0 or size_b <= 0:
            return

        b_dir = 1.0 if beta >= 0 else -1.0
        if e > 0:
            # A rich vs its Kalman hedge -> short spread: short A, long beta*B.
            self.order_target_size(data=self.datas[0], target=-size_a)
            self.order_target_size(data=self.datas[1], target=int(b_dir * size_b))
        else:
            # A cheap -> long spread: long A, short beta*B.
            self.order_target_size(data=self.datas[0], target=size_a)
            self.order_target_size(data=self.datas[1], target=int(-b_dir * size_b))
