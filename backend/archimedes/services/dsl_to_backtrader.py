"""DSL → backtrader interpreter.

Translates a validated StrategySpec into a backtrader.Strategy subclass
at runtime. No eval/exec/importlib — the strategy is built via type()
with closures over the validated condition trees.
"""

from __future__ import annotations

import logging
from typing import Any

import backtrader as bt

from archimedes.services.strategy_dsl import DSLError, StrategySpec

logger = logging.getLogger(__name__)


# ── Condition evaluation ──────────────────────────────────────────────


def _eval_condition(
    cond: dict[str, Any],
    bar_values: dict[str, float],
) -> bool:
    """Evaluate a condition tree against current bar indicator values."""
    op = next(iter(cond))
    args = cond[op]

    if op == "and":
        return all(_eval_condition(c, bar_values) for c in args)
    if op == "or":
        return any(_eval_condition(c, bar_values) for c in args)
    if op == "not":
        return not _eval_condition(args, bar_values)

    # Comparison operators
    left = args[0]
    right = args[1]

    lv = bar_values.get(left, left) if isinstance(left, str) else left
    rv = bar_values.get(right, right) if isinstance(right, str) else right

    if op == "gt":
        return float(lv) > float(rv)
    if op == "lt":
        return float(lv) < float(rv)
    if op == "gte":
        return float(lv) >= float(rv)
    if op == "lte":
        return float(lv) <= float(rv)

    raise DSLError(f"unknown operator: {op}")


# ── Indicator wiring ──────────────────────────────────────────────────


def _make_indicator(
    data_line: bt.LineSeries,
    name: str,
    period: int,
) -> Any:
    """Return a backtrader indicator bound to the given data line.

    Must be called from within a Strategy.__init__ so that backtrader's
    auto-discovery wires the indicator's _owner correctly.
    """
    if name == "sma":
        return bt.indicators.SimpleMovingAverage(data_line, period=period)
    if name == "ema":
        return bt.indicators.ExponentialMovingAverage(data_line, period=period)
    if name == "rsi":
        return bt.indicators.RSI(data_line, period=period)
    if name == "momentum":
        return data_line / data_line(-period)
    raise DSLError(f"unsupported indicator: {name}")


# ── Strategy factory ──────────────────────────────────────────────────


def interpret_spec(spec: StrategySpec) -> type[bt.Strategy]:
    """Translate a validated StrategySpec into a backtrader.Strategy subclass.

    Returns the class (not an instance). The caller is responsible for
    wiring it into a Cerebro via cerebro.addstrategy(cls).
    """
    spec_dict = spec.to_dict()
    entry_cond = spec.entry
    exit_cond = spec.exit
    ps_type = spec.position_sizing.get("type", "full_invested_when_in_market")
    vol_target = spec.position_sizing.get("annual_pct")

    indicator_map: dict[str, tuple[str, int]] = {}
    for ind_name in spec.indicators:
        parts = ind_name.rsplit("_", 1)
        if len(parts) == 2:
            indicator_map[ind_name] = (parts[0], int(parts[1]))

    max_period = max((p for _, p in indicator_map.values()), default=0)

    class DSLStrategy(bt.Strategy):
        """Dynamically generated strategy from DSL spec."""

        params = (
            ("dsl_spec", spec_dict),
            ("exposure_fraction", 0.99),
            ("vol_target_annual", vol_target),
        )

        def __init__(self) -> None:
            self._indicators: dict[str, Any] = {}
            for alias, (name, period) in indicator_map.items():
                self._indicators[alias] = _make_indicator(self.data.close, name, period)
            self._warmup = max_period
            self._vol_target = self.params.vol_target_annual
            self._rebal_counter = 0

        def _bar_values(self) -> dict[str, float]:
            vals: dict[str, float] = {
                "close": float(self.data.close[0]),
                "open": float(self.data.open[0]),
                "high": float(self.data.high[0]),
                "low": float(self.data.low[0]),
                "volume": float(self.data.volume[0]),
            }
            for alias, ind in self._indicators.items():
                try:
                    vals[alias] = float(ind[0])
                except (IndexError, TypeError):
                    vals[alias] = float("nan")
            return vals

        def _should_rebalance(self) -> bool:
            if spec.rebalance_frequency == "daily":
                return True
            self._rebal_counter += 1
            if spec.rebalance_frequency == "weekly":
                return self._rebal_counter % 5 == 0
            if spec.rebalance_frequency == "monthly":
                return self._rebal_counter % 21 == 0
            return True

        def next(self) -> None:
            if len(self) <= self._warmup:
                return

            if not self._should_rebalance():
                return

            bar_values = self._bar_values()
            in_market = self.position.size > 0

            if not in_market:
                if _eval_condition(entry_cond, bar_values):
                    self._enter_position()
            else:
                if _eval_condition(exit_cond, bar_values):
                    self.close()

        def _enter_position(self) -> None:
            price = float(self.data.close[0])
            if price <= 0:
                return

            if ps_type == "full_invested_when_in_market":
                cash = float(self.broker.getcash())
                size = int(cash * float(self.params.exposure_fraction) / price)
                if size > 0:
                    self.order_target_size(target=size)
            elif ps_type == "volatility_target" and self._vol_target:
                # Scale position by target vol / realized vol
                if len(self) > 20:
                    recent = [float(self.data.close[-i]) / float(self.data.close[-i - 1]) - 1 for i in range(20)]
                    realized_vol = (sum(r**2 for r in recent) / len(recent)) ** 0.5 * (252**0.5)
                    if realized_vol > 0:
                        scale = min(self._vol_target / realized_vol, 2.0)
                        cash = float(self.broker.getcash())
                        size = int(cash * float(self.params.exposure_fraction) * scale / price)
                        if size > 0:
                            self.order_target_size(target=size)
                        return
                # Fallback: full invest if not enough data for vol estimate
                cash = float(self.broker.getcash())
                size = int(cash * float(self.params.exposure_fraction) / price)
                if size > 0:
                    self.order_target_size(target=size)
            else:
                # equal_weight or unknown: full invest
                cash = float(self.broker.getcash())
                size = int(cash * float(self.params.exposure_fraction) / price)
                if size > 0:
                    self.order_target_size(target=size)

    DSLStrategy.__name__ = f"DSL_{spec.name.replace(' ', '_').replace('-', '_')}"
    DSLStrategy.__qualname__ = DSLStrategy.__name__
    return DSLStrategy


def interpret_variant(
    spec: StrategySpec,
    indicator_overrides: dict[str, int],
) -> type[bt.Strategy]:
    """Interpret spec with one variant of its parameter grid applied.

    Deep-copies the spec, overlays period overrides onto the indicator
    list and condition tree, and delegates to ``interpret_spec``.

    Args:
        spec: A validated StrategySpec (may carry parameter_variants).
        indicator_overrides: Mapping from indicator alias (e.g. ``"sma_200"``)
            to the variant period (e.g. ``150``). Keys must already appear in
            ``spec.indicators``.

    Returns:
        A backtrader.Strategy subclass configured with the overridden periods.
    """
    import copy

    # Build a new indicator list with overridden periods.
    new_indicators = list(spec.indicators)
    for alias, new_period in indicator_overrides.items():
        parts = alias.rsplit("_", 1)
        if len(parts) != 2:
            continue
        base_name = parts[0]
        new_alias = f"{base_name}_{new_period}"
        if alias in new_indicators:
            idx = new_indicators.index(alias)
            new_indicators[idx] = new_alias

    # Deep-copy condition trees and replace old alias with new alias.
    new_entry = _rewrite_indicator_aliases(spec.entry, indicator_overrides)
    new_exit = _rewrite_indicator_aliases(spec.exit, indicator_overrides)

    variant_spec = StrategySpec(
        name=f"{spec.name}_v{'_'.join(str(v) for v in indicator_overrides.values())}",
        asset_universe=list(spec.asset_universe),
        rebalance_frequency=spec.rebalance_frequency,
        entry=new_entry,
        exit=new_exit,
        position_sizing=copy.deepcopy(spec.position_sizing),
        source_arxiv_ids=list(spec.source_arxiv_ids),
        look_ahead_safe=spec.look_ahead_safe,
        indicators=new_indicators,
        parameter_variants=None,
    )

    return interpret_spec(variant_spec)


def _rewrite_indicator_aliases(
    cond: dict[str, Any],
    overrides: dict[str, int],
) -> dict[str, Any]:
    """Deep-copy a condition tree, replacing indicator aliases per overrides."""
    import copy

    cond = copy.deepcopy(cond)
    _rewrite_aliases_in_place(cond, overrides)
    return cond


def _rewrite_aliases_in_place(
    cond: dict[str, Any],
    overrides: dict[str, int],
) -> None:
    """Mutate a condition tree, replacing overridden indicator aliases."""
    op = next(iter(cond))
    args = cond[op]

    if op in ("and", "or"):
        for child in args:
            _rewrite_aliases_in_place(child, overrides)
    elif op == "not":
        _rewrite_aliases_in_place(args, overrides)
    elif op in ("gt", "lt", "gte", "lte"):
        for i, arg in enumerate(args):
            if isinstance(arg, str) and arg in overrides:
                parts = arg.rsplit("_", 1)
                if len(parts) == 2:
                    args[i] = f"{parts[0]}_{overrides[arg]}"
