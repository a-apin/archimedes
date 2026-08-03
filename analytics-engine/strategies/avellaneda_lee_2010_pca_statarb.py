"""PCA / eigenportfolio statistical arbitrage (Avellaneda & Lee 2010).

The institutional form of stat-arb. Rather than trading hand-picked pairs,
decompose the whole universe's returns into a few principal components
("eigenportfolios" — data-driven risk factors), strip each asset's exposure to
those factors, and trade the leftover idiosyncratic residual when it strays far
from its mean. The residual is modelled as a mean-reverting Ornstein-Uhlenbeck
process and traded on its standardized "s-score".

Requires ``engine.run_multi_backtest`` (it builds the universe return matrix
from every ``self.datas[i]`` each bar). Avellaneda & Lee (2010) is the canonical
reference for the PCA-residual approach.
"""

from __future__ import annotations

import math

import backtrader as bt
import numpy as np

PAPER_ARXIV_ID: str | None = None
PAPER_TITLE = "Statistical Arbitrage in the U.S. Equities Market"
PAPER_AUTHORS: list[str] = ["Marco Avellaneda", "Jeong-Hyun Lee"]
PAPER_VENUE = "Quantitative Finance"
PAPER_YEAR = 2010
PAPER_DOI = "10.1080/14697680903124632"
PAPER_CITATION_COUNT = 1100  # Snapshot 2026-06; verify via Semantic Scholar.

# Market-neutral residual reversion — regime-agnostic by construction.
REGIME_TAG: str = "regime_neutral"

METHODOLOGY_SUMMARY = (
    "PCA statistical arbitrage. Decompose universe returns into principal-"
    "component risk factors, regress each asset on them to isolate its "
    "idiosyncratic residual, model that residual as mean-reverting (OU), and "
    "trade its standardized s-score long/short and dollar-neutral. The "
    "institutional, factor-based generalization of pairs trading."
)

METHODOLOGY_TEXT = (
    "Avellaneda & Lee (2010) trade the residuals of a factor decomposition. "
    "Their method: estimate the universe's correlation matrix over a rolling "
    "window, take the top principal components as data-driven risk factors "
    "('eigenportfolios'), regress each asset's returns on those factors to get "
    "its idiosyncratic residual, model the cumulative residual as a mean-"
    "reverting Ornstein-Uhlenbeck process, and trade its standardized s-score: "
    "enter long when s is very negative (asset cheap vs its factor exposure), "
    "enter short when s is very positive, and unwind as s reverts toward zero. "
    "They report attractive market-neutral Sharpe ratios on the broad US equity "
    "universe 1997-2007.\n\n"
    "v1 Archimedes adaptation (numpy-only, on the N-feed engine). Each bar after "
    "a lookback warm-up (default 60 bars):\n"
    "  1. Build the universe daily-return matrix R (lookback x N).\n"
    "  2. Eigendecompose its correlation matrix (np.linalg.eigh); take the top "
    "n_components eigenvectors as factors, vol-normalized into eigenportfolio "
    "weights, and form factor-return series F = R @ Q^T.\n"
    "  3. For each asset, OLS-regress its returns on F; the regression residual "
    "is the idiosyncratic return. Its cumulative sum X is the OU process.\n"
    "  4. Fit an AR(1) to X; if mean-reverting (0<b<1) form the s-score "
    "s = (X_last - m) / sigma_eq with m the AR(1) mean and sigma_eq the "
    "equilibrium std. Trade on Avellaneda-Lee bands: open long at s < -entry, "
    "open short at s > +entry, close when |s| < exit.\n"
    "  5. Size dollar-neutral and equal-weight across the names with active "
    "signals (gross ~1.0x split long/short).\n\n"
    "Honest simplifications, disclosed not hidden: (a) we trade the residual "
    "signal by taking long/short positions in the NAMES directly (dollar-neutral "
    "across active names) rather than constructing the explicit per-name "
    "'short beta * eigenportfolio' hedge AL describe — a small universe makes "
    "the cross-sectional basket a reasonable stand-in; (b) we omit AL's drift "
    "(mu) adjustment to the s-score and trade the basic standardized residual. "
    "(c) The paper's Sharpe is for a broad equity universe, NOT a 5-asset "
    "multi-market basket, so paper_claimed_* are null."
)

PAPER_CLAIMED_SHARPE: float | None = None
PAPER_CLAIMED_CAGR: float | None = None
PAPER_CLAIMED_MAX_DD: float | None = None

ASSET_UNIVERSE: list[str] = ["SPY", "NIKKEI", "GOLD", "TREASURY", "OIL"]
POSITION_SIZING = "equal_weight"  # dollar-neutral, equal-weight across active names
REBALANCE_FREQUENCY = "daily"
RISK_PROFILES: list[str] = ["moderate", "aggressive"]

CURATOR_WALLET: str | None = None
CURATOR_NOTE = (
    "The most sophisticated member of the stat-arb sleeve and the natural bridge "
    "from pairs to portfolio-scale relative value. Where the pairs strategies "
    "hedge one asset against one other, this hedges every asset against the "
    "universe's common factors and trades only what's left. On a 5-asset basket "
    "it is necessarily a small-universe demo of a method built for hundreds of "
    "names; read it as the architectural proof that the engine + passport can "
    "carry factor-based stat-arb, not as a tuned production sleeve. Honest "
    "warning from the backtest: on only 5 assets the factor hedge does NOT "
    "diversify idiosyncratic risk, so the long/short basket is not truly "
    "market-neutral and the strategy posts a >100% paper drawdown (equity goes "
    "negative at the trough). That failure is the rigor gate doing its job — "
    "the method needs a large universe to be safe, and it stays CANDIDATE here."
)
EXTRACTION_LLM: str | None = None

STATUS = "candidate"

BACKTEST_SHARPE: float | None = None
BACKTEST_CAGR: float | None = None
BACKTEST_MAX_DD: float | None = None
BACKTEST_WIN_RATE: float | None = None
BACKTEST_CALMAR: float | None = None
BACKTEST_CORR_SPY: float | None = None


class PCAStatArb(bt.Strategy):
    """PCA-residual mean reversion traded on the Avellaneda-Lee s-score.

    Expects N>=3 data feeds (PCA needs more assets than components). Driven by
    ``engine.run_multi_backtest``.
    """

    # Runner contract: this class trades across its whole declared universe
    # (engine.required_feeds() / cli.run_command's N-feed routing branch).
    # ASSET_UNIVERSE above has 5 entries, comfortably above the N>=3 PCA needs.
    REQUIRED_FEEDS = "UNIVERSE"

    params = (
        ("lookback", 60),  # return-matrix / PCA estimation window (bars)
        ("n_components", 2),  # number of leading principal components (factors)
        ("entry_s", 1.25),  # open when |s-score| exceeds this (Avellaneda-Lee band)
        ("exit_s", 0.5),  # close when |s-score| falls below this
        ("gross", 1.0),  # total gross exposure split across long + short names
    )

    def _returns_matrix(self) -> np.ndarray | None:
        """(lookback x N) daily simple-return matrix from the N feeds."""
        n = int(self.params.lookback)
        cols: list[list[float]] = []
        for d in self.datas:
            if len(d) < n + 1:
                return None
            series: list[float] = []
            # Oldest-first over the window.
            for i in range(n, 0, -1):
                prev = float(d.close[-i])
                curr = float(d.close[-i + 1]) if i > 1 else float(d.close[0])
                if prev <= 0:
                    return None
                series.append(curr / prev - 1.0)
            cols.append(series)
        return np.array(cols, dtype=float).T  # shape (lookback, N)

    def _s_scores(self) -> dict[int, float] | None:
        """Map feed-id -> s-score for the current bar (None if not estimable)."""
        r = self._returns_matrix()
        if r is None:
            return None
        t, n = r.shape
        k = min(int(self.params.n_components), n - 1)
        if k < 1 or t < 10:
            return None

        sigma = r.std(axis=0, ddof=1)
        if np.any(sigma <= 0):
            return None
        # Correlation-matrix eigendecomposition (symmetric -> eigh).
        corr = np.corrcoef(r, rowvar=False)
        if not np.all(np.isfinite(corr)):
            return None
        # We only need the eigenvectors (factor directions), not the eigenvalues.
        _eigvals, eigvecs = np.linalg.eigh(corr)
        top = eigvecs[:, -k:]  # leading k eigenvectors (eigh sorts ascending)

        # Vol-normalized eigenportfolio weights -> factor returns F (t x k).
        q = top / sigma[:, None]
        factors = r @ q  # (t, k)

        design = np.column_stack([np.ones(t), factors])  # intercept + k factors
        scores: dict[int, float] = {}
        for j, d in enumerate(self.datas):
            coef, *_ = np.linalg.lstsq(design, r[:, j], rcond=None)
            resid = r[:, j] - design @ coef
            x = np.cumsum(resid)  # OU process candidate
            if len(x) < 3:
                continue
            lag, cur = x[:-1], x[1:]
            if float(np.ptp(lag)) == 0.0:
                continue
            ar_design = np.column_stack([np.ones_like(lag), lag])
            a, b = np.linalg.lstsq(ar_design, cur, rcond=None)[0]
            if not (0.0 < b < 1.0):  # not mean-reverting
                continue
            m = a / (1.0 - b)
            ar_resid = cur - (a + b * lag)
            var_eq = float(np.var(ar_resid, ddof=1)) / (1.0 - b * b)
            if var_eq <= 0:
                continue
            sigma_eq = math.sqrt(var_eq)
            scores[id(d)] = (float(x[-1]) - m) / sigma_eq
        return scores or None

    def next(self) -> None:
        if len(self) <= int(self.params.lookback):
            return
        scores = self._s_scores()
        if scores is None:
            return

        entry = float(self.params.entry_s)
        exit_s = float(self.params.exit_s)

        # Decide target sign per name: -1 short, +1 long, 0 flat. Residual cheap
        # (s very negative) -> long; residual rich (s very positive) -> short.
        longs: list = []
        shorts: list = []
        for d in self.datas:
            s = scores.get(id(d))
            pos = self.getposition(d).size
            if s is None:
                if pos != 0:
                    self.order_target_percent(data=d, target=0.0)
                continue
            if pos == 0:
                if s < -entry:
                    longs.append(d)
                elif s > entry:
                    shorts.append(d)
            else:
                # In a position: hold until the s-score reverts inside the band.
                if abs(s) < exit_s:
                    self.order_target_percent(data=d, target=0.0)
                elif pos > 0:
                    longs.append(d)
                else:
                    shorts.append(d)

        half = float(self.params.gross) / 2.0
        long_w = half / len(longs) if longs else 0.0
        short_w = half / len(shorts) if shorts else 0.0
        for d in longs:
            self.order_target_percent(data=d, target=long_w)
        for d in shorts:
            self.order_target_percent(data=d, target=-short_w)
