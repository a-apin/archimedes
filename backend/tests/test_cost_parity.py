"""Cross-engine cost parity — the backend half.

Three backtest engines write into one ``backtest_results`` table and one gate
grades them without knowing which produced a row:

  A. curated strategies, backtrader (``analytics_engine.engine``)
  B. generated weight maps, pandas/numpy (``services.portfolio_backtester``)
  C. fusion DSL specs, backtrader (``services.fusion_evaluator``)

Before the cost SSOT landed, A charged commission AND slippage while B and C
charged commission only, so the engines that grade *generated* strategies
systematically flattered themselves against the curated library they are ranked
beside. Re-running the library without closing that gap would have published a
fresh set of biased numbers with more authority attached.

Literal cost parity is not reachable — B's Almgren square-root impact term needs
ADV inside a custom ``bt.CommInfoBase``. The defensible target is an identical
cost FLOOR everywhere (per-side linear bps + proportional slippage from one
source), with B's impact term kept as an additional disclosed haircut that makes
B stricter and never looser.

The model's own behaviour is tested in ``analytics-engine/tests/test_costs.py``,
where that package is installed. This file covers what lives on the backend
side: the mirrored constants, and Engine B's arithmetic.

**Why the drift check reads source instead of importing.** The backend unit-test
job does not pip-install ``./analytics-engine`` — only the analytics-engine job
does (``quality-gate.yml``). An ``importorskip`` here would therefore skip the
whole file in CI and report green while checking nothing, which is the failure
mode this work exists to remove. The constant is a source-level invariant and
the file is always in the tree, so it is read with ``ast`` — the same approach
``strategy_provider._read_module_constants`` already uses, and it never imports
or executes the module.
"""

from __future__ import annotations

import ast
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from archimedes.services.portfolio_backtester import (
    DEFAULT_SLIPPAGE_BPS,
    DEFAULT_TX_COST_BPS,
    _simulate_portfolio,
)

_COSTS_PY = (
    Path(__file__).resolve().parents[2] / "analytics-engine" / "src" / "archimedes_analytics_engine" / "costs.py"
)


def _default_cost_model_literals() -> dict[str, float]:
    """Read ``DEFAULT_COST_MODEL``'s kwargs out of costs.py without importing it."""
    tree = ast.parse(_COSTS_PY.read_text(encoding="utf-8"))
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
        if "DEFAULT_COST_MODEL" not in targets:
            continue
        call = node.value
        assert isinstance(call, ast.Call), "DEFAULT_COST_MODEL must be a direct CostModel(...) call"
        return {kw.arg: ast.literal_eval(kw.value) for kw in call.keywords if kw.arg}
    raise AssertionError(f"DEFAULT_COST_MODEL not found in {_COSTS_PY}")


def _one_rebalance_panel() -> tuple[pd.DataFrame, pd.DataFrame]:
    """Deterministic 3-bar ramp with exactly one rebalance and known turnover.

    SPY jumps +20% on the last bar against a flat TLT, so held weights drift
    from [0.5, 0.5] to [6/11, 5/11] and ``sum(|dw|)`` is exactly 1/11. Volume is
    large enough that Almgren impact is negligible, and ``gamma=0`` removes it
    entirely at the call site.
    """
    idx = pd.bdate_range("2020-01-02", periods=3)
    panel = pd.DataFrame(
        {
            "SPY": pd.Series([100.0, 100.0, 120.0], index=idx),
            "TLT": pd.Series([100.0, 100.0, 100.0], index=idx),
        }
    )
    vols = pd.DataFrame(
        {
            "SPY": pd.Series([1e9] * 3, index=idx),
            "TLT": pd.Series([1e9] * 3, index=idx),
        }
    )
    return panel, vols


KNOWN_TURNOVER = 1.0 / 11.0
PRE_COST_RETURN = 0.10  # 0.5 SPY weight * +20%


class TestMirrorDoesNotDrift:
    def test_costs_module_is_where_we_think_it_is(self) -> None:
        """Guard the guard: a moved file must fail loudly, not skip the check."""
        assert _COSTS_PY.is_file(), f"cost SSOT missing at {_COSTS_PY}"

    def test_backend_constants_match_the_analytics_engine_ssot(self) -> None:
        """portfolio_backtester mirrors the constants instead of importing them.

        The mirror exists because that module sits in the request path and the
        analytics-engine package is an optional install there — the same reason
        DEFAULT_TX_COST_BPS was already a mirrored constant. That trade is only
        reasonable if drift is impossible, which is this test's job. Retune
        DEFAULT_COST_MODEL without retuning the mirror and the two engines
        diverge again silently, and every cross-engine number on the leaderboard
        goes quietly wrong.
        """
        ssot = _default_cost_model_literals()
        assert pytest.approx(ssot["default_bps"]) == DEFAULT_TX_COST_BPS
        assert pytest.approx(ssot["slippage_bps"]) == DEFAULT_SLIPPAGE_BPS

    def test_ssot_charges_both_legs(self) -> None:
        """A zero slippage leg is the exact defect this work closed."""
        ssot = _default_cost_model_literals()
        assert ssot["default_bps"] > 0
        assert ssot["slippage_bps"] > 0


class TestEngineBFloor:
    """Engine B charges the shared floor, plus its own disclosed haircut."""

    def test_charges_commission_and_slippage_on_turnover(self) -> None:
        panel, vols = _one_rebalance_panel()
        rets, _ = _simulate_portfolio(
            panel=panel,
            volume_panel=vols,
            target_weights={"SPY": 0.5, "TLT": 0.5},
            rebalance_days=2,
            initial_cash=100_000.0,
            tx_cost_bps=DEFAULT_TX_COST_BPS,
            slippage_bps=DEFAULT_SLIPPAGE_BPS,
            gamma=0.0,
        )
        expected_floor = KNOWN_TURNOVER * ((DEFAULT_TX_COST_BPS + DEFAULT_SLIPPAGE_BPS) / 10_000.0)
        assert rets[2] == pytest.approx(PRE_COST_RETURN - expected_floor, abs=1e-12)

    def test_costed_run_is_strictly_below_zero_cost_run(self) -> None:
        panel, vols = _one_rebalance_panel()
        shared = {
            "panel": panel,
            "volume_panel": vols,
            "target_weights": {"SPY": 0.5, "TLT": 0.5},
            "rebalance_days": 2,
            "initial_cash": 100_000.0,
            "gamma": 0.0,
        }
        costed, _ = _simulate_portfolio(**shared, tx_cost_bps=DEFAULT_TX_COST_BPS, slippage_bps=DEFAULT_SLIPPAGE_BPS)
        free, _ = _simulate_portfolio(**shared, tx_cost_bps=0, slippage_bps=0)

        assert costed[2] < free[2]
        assert free[2] == pytest.approx(PRE_COST_RETURN, abs=1e-12)

    def test_slippage_is_a_separable_leg(self) -> None:
        """Dropping slippage must move the number by exactly its own share.

        If this passes only because slippage got folded into the commission
        term, the floor is not actually shared and the parity claim is false.
        """
        panel, vols = _one_rebalance_panel()
        shared = {
            "panel": panel,
            "volume_panel": vols,
            "target_weights": {"SPY": 0.5, "TLT": 0.5},
            "rebalance_days": 2,
            "initial_cash": 100_000.0,
            "gamma": 0.0,
            "tx_cost_bps": DEFAULT_TX_COST_BPS,
        }
        with_slip, _ = _simulate_portfolio(**shared, slippage_bps=DEFAULT_SLIPPAGE_BPS)
        without_slip, _ = _simulate_portfolio(**shared, slippage_bps=0)

        assert without_slip[2] - with_slip[2] == pytest.approx(
            KNOWN_TURNOVER * (DEFAULT_SLIPPAGE_BPS / 10_000.0), abs=1e-12
        )

    def test_almgren_sits_on_top_of_the_floor_never_replaces_it(self) -> None:
        """B stays stricter than the floor once impact is on.

        This is the asymmetry we chose to accept rather than eliminate, so it
        needs a test that says which direction it runs in.
        """
        panel, vols = _one_rebalance_panel()
        shared = {
            "panel": panel,
            "volume_panel": vols,
            "target_weights": {"SPY": 0.5, "TLT": 0.5},
            "rebalance_days": 2,
            "initial_cash": 100_000.0,
            "tx_cost_bps": DEFAULT_TX_COST_BPS,
            "slippage_bps": DEFAULT_SLIPPAGE_BPS,
        }
        floor_only, _ = _simulate_portfolio(**shared, gamma=0.0)
        with_impact, _ = _simulate_portfolio(**shared, gamma=0.5)

        assert with_impact[2] <= floor_only[2]

    def test_default_arguments_carry_the_floor(self) -> None:
        """A caller passing no cost arguments still gets charged both legs.

        The defect was an engine defaulting to commission-only, so the defaults
        are the thing worth pinning, not just the explicit path.
        """
        panel, vols = _one_rebalance_panel()
        defaulted, _ = _simulate_portfolio(
            panel=panel,
            volume_panel=vols,
            target_weights={"SPY": 0.5, "TLT": 0.5},
            rebalance_days=2,
            initial_cash=100_000.0,
            gamma=0.0,
        )
        expected_floor = KNOWN_TURNOVER * ((DEFAULT_TX_COST_BPS + DEFAULT_SLIPPAGE_BPS) / 10_000.0)
        assert not np.isnan(defaulted[2])
        assert defaulted[2] == pytest.approx(PRE_COST_RETURN - expected_floor, abs=1e-12)
