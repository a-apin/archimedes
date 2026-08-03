"""Quality Momentum — Novy-Marx (2012).

Intermediate-horizon momentum (months 7-12, skipping recent) combined with a
quality filter (information ratio over a shorter window). Standard cross-sectional
momentum chases total return; quality momentum only chases it in assets that also
exhibit consistent positive drift relative to their noise — reducing the risk of
following high-volatility "junk" rallies that subsequently reverse.

Requires ``engine.run_multi_backtest``.
"""

from __future__ import annotations

import math

import backtrader as bt

PAPER_ARXIV_ID: str | None = None
PAPER_TITLE = "Is Momentum Really Momentum?"
PAPER_AUTHORS: list[str] = ["Robert Novy-Marx"]
PAPER_VENUE = "Journal of Financial Economics"
PAPER_YEAR = 2012
PAPER_DOI = "10.1016/j.jfineco.2012.01.005"
PAPER_CITATION_COUNT = 900  # Snapshot 2026-06; verify via Semantic Scholar.

# Trend-following + quality filter — effective in bull regimes, partially
# defensive in bear because quality leg avoids high-vol junk rallies.
REGIME_TAG: str = "bull"

METHODOLOGY_SUMMARY = (
    "Composite ranking: 50% intermediate-horizon momentum (months 7-12, skipping "
    "recent) + 50% quality score (information ratio over a short window). Long top "
    "composite, short bottom composite, monthly rebalance."
)

METHODOLOGY_TEXT = (
    "Novy-Marx (2012) shows that intermediate-horizon momentum (formation window "
    "roughly months 7-12, skipping the most recent month) drives most of the "
    "standard 12-1 momentum signal. Short-horizon continuation (months 1-6) is "
    "largely noise and adds drawdown risk. The paper reports that this refined "
    "momentum proxy earns higher risk-adjusted returns and loads more cleanly on "
    "the momentum factor in factor regressions.\n\n"
    "v1 Archimedes adaptation (quality momentum composite on the N-feed engine): "
    "for each asset at each monthly rebalance we compute (1) intermediate-horizon "
    "formation return = close[-skip] / close[-lookback] - 1 (lookback=252, skip=21, "
    "so the window covers ~months 2-12 relative to today) and (2) information ratio "
    "= mean daily return / std daily return over the last ir_window=63 bars, a "
    "price-based quality proxy that captures consistent positive drift (high IR) "
    "vs. volatile noise (low IR). Both scores are z-scored across the universe and "
    "combined 50/50 into a composite. We go long the top long_frac and short the "
    "bottom short_frac by composite score.\n\n"
    "Honest caveats: (1) The paper's result is for a US stock cross-section, not a "
    "5-asset multi-market basket. Paper_claimed_* are null: the 1%/month figure "
    "does not transfer to our universe. (2) Our 'quality' is purely price-based "
    "(information ratio), not the fundamental quality dimensions (profitability, "
    "safety, growth) the AQR QMJ paper measures. It captures the 'safety' dimension "
    "(low-vol) + 'profitability' dimension (positive drift) in a single ratio, but "
    "is a loose proxy. (3) Cross-market momentum on non-synchronous closes is a "
    "known approximation disclosed here."
)

PAPER_CLAIMED_SHARPE: float | None = None
PAPER_CLAIMED_CAGR: float | None = None
PAPER_CLAIMED_MAX_DD: float | None = None

ASSET_UNIVERSE: list[str] = ["SPY", "NIKKEI", "GOLD", "TREASURY", "OIL"]
POSITION_SIZING = "equal_weight"
REBALANCE_FREQUENCY = "monthly"
RISK_PROFILES: list[str] = ["aggressive"]

CURATOR_WALLET: str | None = None
CURATOR_NOTE = (
    "Quality momentum is a tighter version of cross-sectional momentum: it filters "
    "out high-volatility assets whose momentum signal may be noise rather than "
    "genuine trend. Complementary to JT cross-sectional momentum in the library — "
    "both rank by trailing return but this one penalizes inconsistency."
)
EXTRACTION_LLM: str | None = None

STATUS = "candidate"

BACKTEST_SHARPE: float | None = None
BACKTEST_CAGR: float | None = None
BACKTEST_MAX_DD: float | None = None
BACKTEST_WIN_RATE: float | None = None
BACKTEST_CALMAR: float | None = None
BACKTEST_CORR_SPY: float | None = None

_ANNUALIZATION = 252


class NovyMarxQualityMomentum(bt.Strategy):
    """Composite quality-momentum ranking: 50% intermediate momentum + 50% quality IR.

    Expects N>=2 data feeds. Driven by ``engine.run_multi_backtest``.
    """

    # Runner contract: this class ranks across its whole declared universe
    # (engine.required_feeds() / cli.run_command's N-feed routing branch).
    REQUIRED_FEEDS = "UNIVERSE"

    params = (
        ("lookback", 252),  # formation window for momentum
        ("skip", 21),  # skip most-recent bars (short-term reversal control)
        ("ir_window", 63),  # rolling window for information ratio (quality proxy)
        ("rebalance_every", 21),
        ("long_frac", 0.4),
        ("short_frac", 0.4),
        ("gross", 1.0),
    )

    def __init__(self) -> None:
        self._bars_since_rebalance = 0

    # ── helpers ────────────────────────────────────────────────────────────────

    def _formation_return(self, data) -> float | None:
        need = int(self.params.lookback) + 1
        if len(data) < need:
            return None
        old = float(data.close[-int(self.params.lookback)])
        recent = float(data.close[-int(self.params.skip)])
        if old <= 0:
            return None
        return recent / old - 1.0

    def _information_ratio(self, data) -> float | None:
        window = int(self.params.ir_window)
        if len(data) < window + 2:
            return None
        rets: list[float] = []
        for i in range(1, window + 1):
            prev = float(data.close[-i - 1])
            curr = float(data.close[-i])
            if prev > 0:
                rets.append(curr / prev - 1.0)
        if len(rets) < 2:
            return None
        mean_r = sum(rets) / len(rets)
        var_r = sum((r - mean_r) ** 2 for r in rets) / (len(rets) - 1)
        sigma = math.sqrt(var_r)
        if sigma <= 0.0:
            return None
        return mean_r / sigma

    @staticmethod
    def _zscore(values: list[float]) -> list[float]:
        n = len(values)
        if n < 2:
            return [0.0] * n
        mean_v = sum(values) / n
        var_v = sum((v - mean_v) ** 2 for v in values) / (n - 1)
        sigma_v = math.sqrt(var_v)
        if sigma_v <= 0.0:
            return [0.0] * n
        return [(v - mean_v) / sigma_v for v in values]

    # ── main logic ─────────────────────────────────────────────────────────────

    def next(self) -> None:
        min_bars = max(int(self.params.lookback), int(self.params.ir_window)) + 2
        if len(self) < min_bars:
            return
        self._bars_since_rebalance += 1
        if self._bars_since_rebalance < int(self.params.rebalance_every):
            return
        self._bars_since_rebalance = 0

        mom_raw: list[float] = []
        ir_raw: list[float] = []
        valid_datas: list = []

        for d in self.datas:
            mom = self._formation_return(d)
            ir = self._information_ratio(d)
            if mom is None or ir is None:
                continue
            mom_raw.append(mom)
            ir_raw.append(ir)
            valid_datas.append(d)

        n = len(valid_datas)
        if n < 2:
            return

        mom_z = self._zscore(mom_raw)
        ir_z = self._zscore(ir_raw)
        composite = [0.5 * mz + 0.5 * qz for mz, qz in zip(mom_z, ir_z, strict=True)]

        scored = sorted(zip(composite, valid_datas, strict=True), key=lambda x: x[0], reverse=True)
        n_long = max(1, int(round(n * float(self.params.long_frac))))
        n_short = max(1, int(round(n * float(self.params.short_frac))))
        if n_long + n_short > n:
            n_short = max(1, n - n_long)

        longs = {id(d) for _, d in scored[:n_long]}
        shorts = {id(d) for _, d in scored[-n_short:]}
        long_w = (float(self.params.gross) / 2.0) / n_long
        short_w = (float(self.params.gross) / 2.0) / n_short

        for _, d in scored:
            if id(d) in longs:
                self.order_target_percent(data=d, target=long_w)
            elif id(d) in shorts:
                self.order_target_percent(data=d, target=-short_w)
            else:
                self.order_target_percent(data=d, target=0.0)
