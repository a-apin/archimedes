"""Portfolio of pairs (faithful-scale distance method) — Gatev et al. 2006.

The faithful-scale replication of the Gatev, Goetzmann & Rouwenhorst design:
the paper's headline ~11% annualized excess return belongs to a *diversified
portfolio of the top ~20 pairs*, formed by minimum normalized-price distance
over a 12-month window and traded over the following 6 months — NOT to any
single pair. The library's four single-pair Gatev implementations are demo
scale; this strategy runs the actual formation → selection → trading cycle
over a 26-ETF universe via ``engine.run_multi_backtest``. Third-wave fidelity
item 2 (docs/specs/quant-roadmap.md Priority 2.1).
"""

from __future__ import annotations

import math

import backtrader as bt

PAPER_ARXIV_ID: str | None = None
PAPER_TITLE = "Pairs Trading: Performance of a Relative-Value Arbitrage Rule"
PAPER_AUTHORS: list[str] = ["Evan Gatev", "William N. Goetzmann", "K. Geert Rouwenhorst"]
PAPER_VENUE = "The Review of Financial Studies"
PAPER_YEAR = 2006
PAPER_DOI = "10.1093/rfs/hhj020"
PAPER_CITATION_COUNT = 2400  # Snapshot 2026-06; verify via Semantic Scholar.

# Market-neutral by construction across a basket of pairs.
REGIME_TAG: str = "regime_neutral"

METHODOLOGY_SUMMARY = (
    "The paper's actual design at the paper's actual scale: every 6 months, "
    "rank all pairs in a 26-ETF universe by the distance between their "
    "normalized 12-month price paths, select the top 20, then trade each "
    "selected pair for the next 6 months — open dollar-neutral on a 2-sigma "
    "divergence of the spread, close when the normalized prices cross. "
    "Diversification across ~20 simultaneous pairs is what generated the "
    "paper's ~11% annualized excess return; single pairs are not comparable."
)

METHODOLOGY_TEXT = (
    "Gatev, Goetzmann & Rouwenhorst (2006) form pairs over a 12-month "
    "formation period by minimizing the sum of squared deviations (SSD) "
    "between normalized price series (cumulative return indices), then trade "
    "the top pairs over the subsequent 6-month period: open a dollar-neutral "
    "position (long the cheap leg, short the rich leg) when the spread "
    "diverges beyond two formation-period standard deviations; close when the "
    "normalized prices cross; force-close at the end of the trading period. "
    "Their ~11% annualized excess return is for diversified portfolios of the "
    "top 5-20 pairs on the CRSP cross-section 1962-2002.\n\n"
    "This Archimedes implementation is the FAITHFUL-SCALE replication on the "
    "N-feed engine — distinct from the library's four single-pair Gatev "
    "adaptations, which stream one fixed pair with a rolling z-score. Here "
    "the full cycle is mechanical and repeated: every 126 trading bars, "
    "normalize each asset's trailing 252-bar price path to 1.0 at the window "
    "start, compute SSD for every pair combination in the universe (26 ETFs "
    "-> 325 candidate pairs), select the 20 smallest-SSD pairs, and record "
    "each pair's formation-window spread standard deviation. During the "
    "trading window each pair opens when |spread| exceeds 2 sigma (short the "
    "high normalized leg, long the low), closes when the spread changes sign "
    "(the paths cross), and everything force-closes at the window end before "
    "re-formation. Capital follows the paper's committed-capital, "
    "self-financing accounting: each selected pair is committed exposure/20 "
    "of equity, and when triggered deploys that full share long AND short "
    "(the short proceeds finance the long leg), so each leg weighs "
    "exposure/20 and gross exposure reaches ~2x equity only if all 20 pairs "
    "are simultaneously open. Returns are on committed capital (idle pairs "
    "drag, as in the paper's committed basis; we do not re-lever onto open "
    "pairs, i.e. not the paper's higher-turnover fully-invested basis). "
    "Per-feed orders are netted across pairs sharing a leg. Market orders "
    "fill at the next bar's open, which matches the paper's more "
    "conservative one-day-waiting variant.\n\n"
    "Disclosed simplifications: (1) sequential non-overlapping 6-month "
    "trading periods rather than the paper's six staggered overlapping "
    "portfolios; (2) a liquid US-listed ETF universe (sectors, country funds, "
    "metals, staples substitutes, duration pairs) rather than the CRSP stock "
    "cross-section — fewer, more internally-diversified securities, so fewer "
    "true near-substitutes than the paper had; (3) no delisting/survivorship "
    "handling needed (ETFs alive throughout the joined window). Pure-python "
    "math, no new dependencies."
)

# The paper reports the diversified top-pairs portfolio's annualized excess
# return (~11%), not a clean Sharpe or max-DD for this exact configuration —
# Sharpe/DD left null rather than guessed. Unlike the single-pair files, the
# 0.11 CAGR claim IS at this strategy's scale (a top-20-pairs portfolio),
# though on ETFs rather than the 1962-2002 CRSP universe.
PAPER_CLAIMED_SHARPE: float | None = None
PAPER_CLAIMED_CAGR: float | None = 0.11
PAPER_CLAIMED_MAX_DD: float | None = None

# 26 liquid US-listed ETFs/stocks with plausible substitute structure: index
# twins (SPY/IVV/QQQ/IWM), developed/EM country funds (EFA/EEM/EWA/EWC), the
# gold complex (GLD/GDX/SLV), staples substitutes (KO/PEP), duration pairs
# (TLT/IEF), real assets (DBC/VNQ), and the 9 SPDR sectors.
ASSET_UNIVERSE: list[str] = [
    "SPY",
    "IVV",
    "QQQ",
    "IWM",
    "EFA",
    "EEM",
    "EWA",
    "EWC",
    "GLD",
    "GDX",
    "SLV",
    "KO",
    "PEP",
    "TLT",
    "IEF",
    "DBC",
    "VNQ",
    "XLB",
    "XLE",
    "XLF",
    "XLI",
    "XLK",
    "XLP",
    "XLU",
    "XLV",
    "XLY",
]
POSITION_SIZING = "equal_weight"  # exposure/20 committed per pair, per leg (self-financing)
REBALANCE_FREQUENCY = "daily"  # daily spread monitoring; semiannual re-formation
RISK_PROFILES: list[str] = ["moderate", "aggressive"]

CURATOR_WALLET: str | None = None
CURATOR_NOTE = (
    "The first faithful-scale replication in the library and the direct test "
    "of the fidelity thesis: the second wave showed single-pair Gatev fails "
    "the gate, but the paper never claimed single pairs work — it claimed the "
    "diversified top-20 portfolio does. If the alpha survives on a modern ETF "
    "universe at the paper's own scale, this passes honestly; if it has "
    "decayed (as much of the post-publication literature suggests), the "
    "CANDIDATE verdict documents that decay with receipts."
)
EXTRACTION_LLM: str | None = None

STATUS = "candidate"

BACKTEST_SHARPE: float | None = None
BACKTEST_CAGR: float | None = None
BACKTEST_MAX_DD: float | None = None
BACKTEST_WIN_RATE: float | None = None
BACKTEST_CALMAR: float | None = None
BACKTEST_CORR_SPY: float | None = None


def select_pairs_by_ssd(closes: list[list[float]], n_pairs: int) -> list[tuple[int, int, float]]:
    """Rank all pair combinations by SSD between normalized price paths.

    ``closes`` is one aligned price window per asset (equal lengths). Each
    series is normalized to 1.0 at the window start (the paper's cumulative
    return index). Returns up to ``n_pairs`` tuples ``(i, j, spread_sigma)``
    ordered by ascending SSD, where ``spread_sigma`` is the sample standard
    deviation of the normalized spread over the window — the trading
    threshold unit. Degenerate pairs (non-positive base price or zero spread
    variance) are skipped as untradeable.
    """
    ranked: list[tuple[float, int, int, float]] = []
    for i in range(len(closes)):
        base_i = closes[i][0]
        if base_i <= 0:
            continue
        for j in range(i + 1, len(closes)):
            base_j = closes[j][0]
            if base_j <= 0:
                continue
            spreads = [a / base_i - b / base_j for a, b in zip(closes[i], closes[j], strict=True)]
            if len(spreads) < 2:
                continue
            ssd = sum(s * s for s in spreads)
            mean = sum(spreads) / len(spreads)
            var = sum((s - mean) ** 2 for s in spreads) / (len(spreads) - 1)
            sigma = math.sqrt(var)
            if sigma <= 0:
                continue
            ranked.append((ssd, i, j, sigma))
    ranked.sort(key=lambda t: t[0])
    return [(i, j, sigma) for _, i, j, sigma in ranked[:n_pairs]]


class GatevPortfolioOfPairs(bt.Strategy):
    """Formation/trading cycle over the top-N SSD pairs of an N-feed universe.

    Expects >= 3 data feeds (``self.datas[i]``). Driven by
    ``engine.run_multi_backtest``.
    """

    # Runner contract: this class trades across its whole declared 26-ETF
    # universe (engine.required_feeds() / cli.run_command's N-feed branch).
    REQUIRED_FEEDS = "UNIVERSE"

    params = (
        ("formation", 252),  # 12-month formation window (bars)
        ("trading", 126),  # 6-month trading window (bars)
        ("n_pairs", 20),  # paper's top-20 portfolio
        ("entry_sigma", 2.0),  # open on 2-sigma divergence
        ("exposure", 0.95),  # committed capital; gross reaches 2x this if ALL pairs open
    )

    def __init__(self) -> None:
        self._pairs: list[dict] = []  # {i, j, base_i, base_j, sigma, side}
        self._bars_in_trading = 0
        self._current_weights: dict[int, float] = {}

    def _formation_window(self, data) -> list[float]:
        window = int(self.params.formation)
        return [float(data.close[-(window - 1 - k)]) for k in range(window)]

    def _reform(self) -> None:
        """Close everything and select a fresh top-N pair book from the
        trailing formation window. Trading starts on the next bar."""
        closes = [self._formation_window(d) for d in self.datas]
        selected = select_pairs_by_ssd(closes, int(self.params.n_pairs))
        self._pairs = [
            {
                "i": i,
                "j": j,
                "base_i": closes[i][0],
                "base_j": closes[j][0],
                "sigma": sigma,
                "side": 0,  # 0 flat; +1 short i / long j; -1 long i / short j
            }
            for i, j, sigma in selected
        ]
        self._bars_in_trading = 0
        self._apply_weights()

    def _spread(self, pair: dict) -> float | None:
        price_i = float(self.datas[pair["i"]].close[0])
        price_j = float(self.datas[pair["j"]].close[0])
        if price_i <= 0 or price_j <= 0:
            return None
        return price_i / pair["base_i"] - price_j / pair["base_j"]

    def _apply_weights(self) -> None:
        """Net per-feed target weights across all open pairs; order only on change.

        Self-financing per the paper: a pair's committed share (exposure/n_pairs)
        is deployed fully on EACH leg — the short proceeds finance the long."""
        leg_weight = float(self.params.exposure) / max(len(self._pairs), 1)
        targets: dict[int, float] = dict.fromkeys(range(len(self.datas)), 0.0)
        for pair in self._pairs:
            if pair["side"] == 0:
                continue
            # side +1: spread rich -> short leg i, long leg j (and vice versa).
            targets[pair["i"]] -= pair["side"] * leg_weight
            targets[pair["j"]] += pair["side"] * leg_weight
        for idx, target in targets.items():
            if abs(target - self._current_weights.get(idx, 0.0)) > 1e-9:
                self.order_target_percent(data=self.datas[idx], target=target)
        self._current_weights = targets

    def next(self) -> None:
        if len(self) < int(self.params.formation):
            return

        if not self._pairs or self._bars_in_trading >= int(self.params.trading):
            self._reform()
            return

        self._bars_in_trading += 1
        changed = False
        for pair in self._pairs:
            spread = self._spread(pair)
            if spread is None:
                continue
            if pair["side"] == 0:
                if abs(spread) > float(self.params.entry_sigma) * pair["sigma"]:
                    pair["side"] = 1 if spread > 0 else -1
                    changed = True
            # Close when the normalized paths cross (spread flips sign).
            elif (pair["side"] == 1 and spread <= 0) or (pair["side"] == -1 and spread >= 0):
                pair["side"] = 0
                changed = True
        if changed:
            self._apply_weights()
