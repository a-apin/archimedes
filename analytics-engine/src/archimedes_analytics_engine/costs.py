"""Transaction-cost + turnover model for the backtest engine.

Motivation (quant-roadmap Priority 2.2): execution realism is decisive — the
Kalman pairs strategy cost-bled to −1.47 Sharpe on 1174 trades. This module
provides the three reusable pieces every strategy can share:

1. :class:`CostModel` — per-side cost assumptions in bps, with per-symbol
   overrides, applied to the backtrader broker per feed.
2. :class:`TurnoverAnalyzer` — measures what was actually traded (two-way
   notional, commissions paid, per-bar commission series) so the engine can
   report turnover, cost drag, gross-vs-net Sharpe, and break-even cost.
3. :func:`no_trade_band` / :func:`position_weight` — a turnover penalty
   strategies opt into: suppress rebalances smaller than a weight band.

Conventions (documented once, used everywhere):

- **Costs are per side**: a cost of 10 bps charges 0.10% of traded notional on
  the buy AND 0.10% on the sell. This matches the legacy flat
  ``transaction_cost_bps`` behaviour (``broker.setcommission`` percent mode).
- **Turnover is one-way and annualized**: ``(two_way_notional / 2) /
  mean(equity) / years``. A strategy that replaces its whole book once a year
  has turnover 1.0.
- **"Gross" means commissions added back, slippage NOT added back** —
  slippage is embedded in the execution price and cannot be recovered from
  broker accounting. Gross metrics are therefore a *lower bound* on the true
  frictionless performance when slippage_bps > 0.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

import backtrader as bt


@dataclass(frozen=True)
class CostModel:
    """Per-side transaction-cost assumptions in basis points.

    ``default_bps`` applies to every feed unless overridden in ``per_symbol``
    (keyed by the feed name passed to the engine runners, e.g. ``"SPY"``).
    ``slippage_bps`` is broker-global (backtrader cannot apply per-feed
    slippage) and is applied to execution prices, not charged as commission.
    """

    default_bps: float = 10.0
    slippage_bps: float = 0.0
    per_symbol: dict[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.default_bps < 0:
            raise ValueError(f"default_bps must be >= 0, got {self.default_bps}")
        if self.slippage_bps < 0:
            raise ValueError(f"slippage_bps must be >= 0, got {self.slippage_bps}")
        for symbol, bps in self.per_symbol.items():
            if bps < 0:
                raise ValueError(f"per_symbol[{symbol!r}] must be >= 0, got {bps}")

    def per_side_bps(self, symbol: str | None = None) -> float:
        """Effective per-side cost in bps for a feed name (default if unknown)."""
        if symbol is not None and symbol in self.per_symbol:
            return self.per_symbol[symbol]
        return self.default_bps

    def apply_to_broker(self, cerebro: bt.Cerebro, feed_names: list[str]) -> None:
        """Install this cost model on a cerebro broker.

        Sets the default percent commission for all feeds, then a per-feed
        override for every name present in ``per_symbol``. Names in
        ``per_symbol`` that are not in ``feed_names`` are ignored (the model
        can carry a universe-wide table; only active feeds matter).
        """
        cerebro.broker.setcommission(commission=self.default_bps / 10_000)
        for name in feed_names:
            if name in self.per_symbol:
                cerebro.broker.setcommission(commission=self.per_symbol[name] / 10_000, name=name)
        if self.slippage_bps > 0:
            cerebro.broker.set_slippage_perc(perc=self.slippage_bps / 10_000)


#: The cost floor every engine charges, so that a number produced by one
#: engine can be compared with a number produced by another.
#:
#: Three engines write into ``backtest_results`` and one gate grades them all
#: without knowing which produced a row. Before this constant existed the
#: curated backtrader engine charged commission AND slippage, while the DSL
#: engine that grades *generated* strategies charged commission only — so the
#: generated path systematically flattered itself against the library it is
#: ranked beside. These values are the CLI's long-standing defaults (10 bps
#: per side, 5 bps slippage), promoted to a shared constant rather than
#: re-picked per call site.
#:
#: The portfolio backtester keeps its extra Almgren square-root impact term on
#: top of this floor. That makes it stricter than the floor, never looser,
#: which is the direction we can defend.
DEFAULT_COST_MODEL = CostModel(default_bps=10.0, slippage_bps=5.0)


def cost_model_fingerprint(model: CostModel) -> str:
    """Stable, human-readable id for a cost model, stamped on every result row.

    Two rows carrying the same fingerprint were charged the same way and are
    therefore comparable; two rows carrying different fingerprints are not,
    and the difference is now visible on the row instead of being something a
    reader has to know. Format::

        cm1:d10:s5            # no per-symbol overrides
        cm1:d10:s5:ob13fdb7c  # with overrides, digest of the sorted table

    The digest covers the override table only, so adding a symbol changes the
    fingerprint while reordering the dict does not.
    """
    base = f"cm1:d{model.default_bps:g}:s{model.slippage_bps:g}"
    if not model.per_symbol:
        return base
    payload = ";".join(f"{sym}={model.per_symbol[sym]:g}" for sym in sorted(model.per_symbol))
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:8]
    return f"{base}:o{digest}"


class TurnoverAnalyzer(bt.Analyzer):
    """Tracks traded notional and commissions actually charged by the broker.

    Output (``get_analysis()``):

    - ``traded_notional``: total two-way traded notional (|size| × price summed
      over every completed execution).
    - ``commission_paid``: total commission the broker charged.
    - ``bar_commissions``: per-bar commission list, positionally aligned with
      the TimeReturn analyzer's per-bar returns (both fire once per
      post-warm-up bar), so the engine can reconstruct gross returns.
    """

    def start(self) -> None:
        self.traded_notional = 0.0
        self.commission_paid = 0.0
        self.bar_commissions: list[float] = []
        self._current_bar_comm = 0.0

    def notify_order(self, order: bt.Order) -> None:
        # Executions are notified before the analyzer's next() for the same
        # bar, so accumulating here and flushing in next() lands each
        # commission on the bar whose TimeReturn already reflects it.
        if order.status != order.Completed:
            return
        executed = order.executed
        self.traded_notional += abs(float(executed.size)) * float(executed.price)
        self.commission_paid += float(executed.comm)
        self._current_bar_comm += float(executed.comm)

    def next(self) -> None:
        self.bar_commissions.append(self._current_bar_comm)
        self._current_bar_comm = 0.0

    def stop(self) -> None:
        # Executions notified after the last next() (none expected, but be safe).
        if self._current_bar_comm > 0 and self.bar_commissions:
            self.bar_commissions[-1] += self._current_bar_comm
            self._current_bar_comm = 0.0

    def get_analysis(self) -> dict:
        return {
            "traded_notional": self.traded_notional,
            "commission_paid": self.commission_paid,
            "bar_commissions": list(self.bar_commissions),
        }


def position_weight(strategy: bt.Strategy, data: bt.LineSeries) -> float:
    """Current portfolio weight of one feed (0.0 when flat or equity is 0)."""
    total = float(strategy.broker.getvalue())
    if total <= 0:
        return 0.0
    return float(strategy.broker.getvalue(datas=[data])) / total


def no_trade_band(current_weight: float, target_weight: float, band: float = 0.005) -> float:
    """Turnover penalty: suppress rebalances smaller than ``band`` (weight units).

    Returns ``target_weight`` when the adjustment is at least ``band`` wide,
    otherwise ``current_weight`` (i.e. don't trade). With the default 0.005, a
    drift from 25.0% to 25.4% is left alone; 25.0% to 26.0% rebalances.

    Strategies use it as a drop-in filter before ``order_target_percent``::

        w_now = position_weight(self, d)
        w_target = no_trade_band(w_now, w_model, band=0.01)
        if w_target != w_now:
            self.order_target_percent(data=d, target=w_target)
    """
    if band < 0:
        raise ValueError(f"band must be >= 0, got {band}")
    if abs(target_weight - current_weight) >= band:
        return target_weight
    return current_weight
