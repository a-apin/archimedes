"""Pairs Trading (distance / relative-value) — Gatev, Goetzmann & Rouwenhorst 2006.

Trade the spread between two co-moving assets rather than either asset
outright: when the price relationship diverges far from its recent norm, go
long the relatively cheap leg and short the relatively rich one, then unwind
as the spread reverts. Market-neutral by construction — the first multi-asset
strategy in the library and the reason the analytics engine grew a two-feed
runner (``engine.run_pairs_backtest``).
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

# Regime suitability: relative-value / market-neutral — designed to be regime-agnostic.
REGIME_TAG: str = "regime_neutral"

METHODOLOGY_SUMMARY = (
    "Trade the spread between two historically co-moving assets. When the "
    "spread diverges beyond a threshold (here a 2-sigma rolling z-score), go "
    "long the cheap leg and short the rich leg dollar-neutral; close when the "
    "spread reverts toward its mean. Profits come from temporary mispricing "
    "of close substitutes, not directional market exposure."
)

METHODOLOGY_TEXT = (
    "Gatev, Goetzmann & Rouwenhorst match securities into pairs by minimum "
    "distance between normalized historical price series over a 12-month "
    "formation period, then trade in the subsequent 6-month period: open a "
    "position when the pair's price spread diverges by more than two "
    "formation-period standard deviations, and close it when the spread "
    "reverts (the normalized prices cross). The headline result on US "
    "equities 1962-2002 is an average annualized excess return of up to ~11% "
    "for self-financing portfolios of the top pairs, robust to conservative "
    "transaction costs.\n\n"
    "v1 Archimedes adaptation: a streaming single-pair implementation. Rather "
    "than a discrete formation/trading split, we compute the price ratio "
    "(close_A / close_B) and its rolling mean and standard deviation over a "
    "``lookback`` window (default 252 bars), then a z-score. We open "
    "dollar-neutral (long cheap leg / short rich leg) when |z| >= entry_z "
    "(default 2.0) and close when |z| <= exit_z (default 0.5). Gross exposure "
    "is capped at ~1.0x (0.5 per leg). NOTE: the paper's ~11% figure is for a "
    "diversified portfolio of the top 20 equity pairs; a single ETF pair is "
    "NOT directly comparable, so the paper-claim delta on this passport should "
    "be read as indicative context, not a like-for-like benchmark."
)

# Paper reports an annualized excess return (~11%) for the diversified top-pairs
# portfolio, not a clean single-pair Sharpe or max-drawdown — left null rather than guessed.
PAPER_CLAIMED_SHARPE: float | None = None
PAPER_CLAIMED_CAGR: float | None = 0.11  # Diversified top-20-pairs excess return; see caveat above.
PAPER_CLAIMED_MAX_DD: float | None = None

# Two-leg universe. Primary demo pair is GLD/GDX (gold spot ETF vs gold-miner ETF);
# SPY/IVV is the near-cointegrated correctness sanity-check pair.
ASSET_UNIVERSE: list[str] = ["GLD", "GDX"]
POSITION_SIZING = "equal_weight"  # dollar-neutral 0.5 per leg
REBALANCE_FREQUENCY = "daily"
RISK_PROFILES: list[str] = ["moderate", "aggressive"]

CURATOR_WALLET: str | None = None
CURATOR_NOTE = (
    "The library's first market-neutral strategy and its first multi-asset "
    "one. Low correlation to SPY by construction makes it a genuine "
    "diversifier against the trend/momentum sleeve. The GLD/GDX pair has a "
    "clean economic linkage (miners are levered to the gold price), which is "
    "the kind of fundamental tether GGR argue underpins durable pair "
    "convergence."
)
EXTRACTION_LLM: str | None = None

STATUS = "candidate"

# Real backtest metrics are authoritative in backtest_fixtures.json (computed by
# scripts/regen_fixtures.py via engine.run_pairs_backtest on GLD/GDX). Documentation
# fallbacks left null until the fixture is generated.
BACKTEST_SHARPE: float | None = None
BACKTEST_CAGR: float | None = None
BACKTEST_MAX_DD: float | None = None
BACKTEST_WIN_RATE: float | None = None
BACKTEST_CALMAR: float | None = None
BACKTEST_CORR_SPY: float | None = None


class PairsDistanceTrading(bt.Strategy):
    """Dollar-neutral z-score reversion on the spread between two co-moving assets.

    Expects exactly two data feeds: ``self.datas[0]`` (leg A) and
    ``self.datas[1]`` (leg B). Driven by ``engine.run_pairs_backtest``.
    """

    # Runner contract (issue #887): this class hard-indexes self.datas[1], so a
    # single-feed run must fail closed with a typed error, not an IndexError.
    REQUIRED_FEEDS = 2

    params = (
        ("lookback", 252),
        ("entry_z", 2.0),
        ("exit_z", 0.5),
        ("leg_fraction", 0.5),  # gross ~1.0x split dollar-neutral across the two legs
    )

    def _zscore(self) -> float | None:
        window = int(self.params.lookback)
        if len(self) <= window:
            return None
        ratios: list[float] = []
        for i in range(window):
            price_b = float(self.datas[1].close[-i])
            if price_b <= 0:
                return None
            ratios.append(float(self.datas[0].close[-i]) / price_b)
        if len(ratios) < 2:
            return None
        mean = sum(ratios) / len(ratios)
        var = sum((r - mean) ** 2 for r in ratios) / (len(ratios) - 1)
        sigma = math.sqrt(var)
        if sigma <= 0:
            return None
        return (ratios[0] - mean) / sigma

    def _target_sizes(self) -> tuple[int, int]:
        account_value = float(self.broker.getvalue())
        leg_notional = account_value * float(self.params.leg_fraction)
        price_a = float(self.datas[0].close[0])
        price_b = float(self.datas[1].close[0])
        size_a = int(leg_notional // price_a) if price_a > 0 else 0
        size_b = int(leg_notional // price_b) if price_b > 0 else 0
        return size_a, size_b

    def next(self) -> None:
        z = self._zscore()
        if z is None:
            return

        in_position = bool(self.getposition(self.datas[0]).size) or bool(self.getposition(self.datas[1]).size)

        # Close on reversion toward the mean.
        if in_position and abs(z) <= float(self.params.exit_z):
            self.close(data=self.datas[0])
            self.close(data=self.datas[1])
            return

        if in_position:
            return

        # Open on divergence beyond the entry band.
        if abs(z) < float(self.params.entry_z):
            return

        size_a, size_b = self._target_sizes()
        if size_a <= 0 or size_b <= 0:
            return

        if z >= float(self.params.entry_z):
            # Ratio rich: leg A expensive relative to leg B → short A, long B.
            self.order_target_size(data=self.datas[0], target=-size_a)
            self.order_target_size(data=self.datas[1], target=size_b)
        else:
            # Ratio cheap: leg A cheap relative to leg B → long A, short B.
            self.order_target_size(data=self.datas[0], target=size_a)
            self.order_target_size(data=self.datas[1], target=-size_b)
