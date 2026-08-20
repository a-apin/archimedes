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
from archimedes.services import fusion_evaluator as fusion_evaluator_module
from archimedes.services.fusion_evaluator import (
    DEFAULT_SLIPPAGE_BPS as ENGINE_C_DEFAULT_SLIPPAGE_BPS,
)
from archimedes.services.fusion_evaluator import (
    run_dsl_backtest,
    run_dsl_backtest_variants,
)
from archimedes.services.portfolio_backtester import (
    DEFAULT_SLIPPAGE_BPS,
    DEFAULT_TX_COST_BPS,
    _simulate_portfolio,
)
from archimedes.services.strategy_dsl import FABER_2007_SPEC, validate_strategy_spec

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

    def test_fusion_evaluator_constants_match_the_analytics_engine_ssot(self) -> None:
        """fusion_evaluator (engine C) mirrors the constants too — same trade as
        portfolio_backtester (engine B), same guard needed.

        #1242 review: engine C's two cerebro.broker call sites charged commission
        only (no slippage leg) even after this SSOT constant existed, because
        nothing read it — DEFAULT_COST_MODEL had zero production callers. Retuning
        DEFAULT_COST_MODEL without retuning this mirror would silently diverge
        engine C from A and B again, exactly the failure mode the SSOT exists to
        prevent.
        """
        ssot = _default_cost_model_literals()
        assert pytest.approx(ssot["default_bps"]) == fusion_evaluator_module._DEFAULT_TX_BPS
        assert pytest.approx(ssot["slippage_bps"]) == ENGINE_C_DEFAULT_SLIPPAGE_BPS


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


class TestEngineCFloor:
    """Engine C (fusion DSL, backtrader) charges the shared floor too.

    #1242 review: the cost-SSOT work put engine B (portfolio_backtester) on the
    floor but left engine C's two cerebro.broker call sites (run_dsl_backtest,
    _run_variant_backtest) commission-only — the exact asymmetry the SSOT exists
    to close, still live on the generated-strategy path that feeds the
    leaderboard. Unlike engine B's linear numpy cost model, engine C is a full
    backtrader simulation with discrete share lots and commission-adjusted order
    sizing, so an exact-dollar round-trip assertion (as in TestEngineBFloor)
    isn't available here — commission alone can even raise the terminal value
    relative to zero-cost by shifting position sizing. What IS robust, and what
    these tests pin: holding commission fixed and only toggling the slippage
    leg must change the result. Before this fix it could not — slippage_bps was
    accepted and silently had zero effect on either call site.
    """

    @staticmethod
    def _spec():
        return validate_strategy_spec(FABER_2007_SPEC)

    def test_slippage_is_wired_on_the_base_backtest_call_site(self) -> None:
        """run_dsl_backtest's cerebro.broker call site (the first the review
        flagged). Manually replaying the pre-fix code path (setcommission only,
        no set_slippage_perc call at all) against this same spec/seed reproduces
        the comm_only figure below exactly — confirming this assertion actually
        distinguishes fixed from unfixed code, not just "some number changed".
        """
        spec = self._spec()
        tx_bps = fusion_evaluator_module._DEFAULT_TX_BPS
        comm_only = run_dsl_backtest(spec, tx_cost_bps=tx_bps, slippage_bps=0)
        floor = run_dsl_backtest(spec, tx_cost_bps=tx_bps, slippage_bps=ENGINE_C_DEFAULT_SLIPPAGE_BPS)

        assert comm_only.total_trades > 0, "test needs a strategy that actually trades"
        assert floor.equity_curve[-1] < comm_only.equity_curve[-1], (
            "adding a slippage leg on top of identical commission must make the "
            "result strictly worse — if it doesn't, slippage_bps is being "
            "accepted and silently ignored, which is the exact pre-fix defect"
        )

    def test_slippage_is_wired_on_the_variant_backtest_call_site(self) -> None:
        """_run_variant_backtest's cerebro.broker call site (the second the
        review flagged), reached only via the parameter-variant grid — a spec
        with no ``parameter_variants`` never exercises this function at all.
        """
        spec_dict = {**FABER_2007_SPEC, "parameter_variants": {"sma_200": [150, 250]}}
        spec = validate_strategy_spec(spec_dict)
        tx_bps = fusion_evaluator_module._DEFAULT_TX_BPS
        comm_only = run_dsl_backtest_variants(spec, tx_cost_bps=tx_bps, slippage_bps=0)
        floor = run_dsl_backtest_variants(spec, tx_cost_bps=tx_bps, slippage_bps=ENGINE_C_DEFAULT_SLIPPAGE_BPS)

        assert comm_only and set(comm_only) == set(floor), "expected both variants to run"
        for variant_id in comm_only:
            assert comm_only[variant_id].total_trades > 0, f"variant {variant_id} never traded"
            assert floor[variant_id].equity_curve[-1] < comm_only[variant_id].equity_curve[-1], (
                f"variant {variant_id}: slippage leg had no effect on _run_variant_backtest"
            )

    def test_default_arguments_carry_the_floor(self) -> None:
        """A caller passing no cost arguments still gets charged both legs — the
        defaults are what the debate/generation pipeline actually runs with.
        """
        spec = self._spec()
        defaulted = run_dsl_backtest(spec)
        zero_slippage = run_dsl_backtest(spec, tx_cost_bps=fusion_evaluator_module._DEFAULT_TX_BPS, slippage_bps=0)
        assert defaulted.equity_curve[-1] < zero_slippage.equity_curve[-1]


class TestRowAttribution:
    """A row nobody can attribute cannot be compared with one you can."""

    def test_insert_refuses_an_unattributed_row(self) -> None:
        """Fail closed rather than defaulting the engine tag.

        backtest_engine has been a column since the 2026-08-03 provenance
        audit, but nothing enforced it. Now that three engines feed the table
        and one gate ranks them together, an unattributed row is one whose cost
        basis cannot be established — and it would sit on the leaderboard beside
        rows whose cost basis can. A default tag would be a guess wearing a
        provenance column's name, so the write is refused instead.
        """
        from archimedes.models.backtest import BacktestResult
        from archimedes.models.backtest_store import Base
        from archimedes.services.backtest_repository import insert_backtest_if_missing
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker

        engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
        Base.metadata.create_all(bind=engine)
        session = sessionmaker(bind=engine)()

        unattributed = BacktestResult(
            strategy_id="no-engine",
            sharpe_ratio=1.0,
            sortino_ratio=1.0,
            max_drawdown=0.1,
            cagr=0.1,
            calmar_ratio=1.0,
            win_rate=0.5,
            profit_factor=1.2,
            total_trades=10,
            avg_holding_period_days=5.0,
            correlation_to_spy=None,
            correlation_to_btc=None,
        )
        with pytest.raises(ValueError, match="unattributed"):
            insert_backtest_if_missing(
                session,
                strategy_id="no-engine",
                content_hash="deadbeef",
                result=unattributed,
                source_pipeline="test",
            )

    def test_cost_model_id_survives_the_store_round_trip(self) -> None:
        """The fingerprint has to reach the DB, not stop at a boundary.

        backtest_engine already had this failure mode: a real column that
        appeared in zero API schemas, so the information stopped at the store.
        """
        from archimedes.models.backtest import BacktestResult
        from archimedes.models.backtest_store import BacktestResultRecord

        result = BacktestResult(
            strategy_id="round-trip",
            sharpe_ratio=1.0,
            sortino_ratio=1.0,
            max_drawdown=0.1,
            cagr=0.1,
            calmar_ratio=1.0,
            win_rate=0.5,
            profit_factor=1.2,
            total_trades=10,
            avg_holding_period_days=5.0,
            correlation_to_spy=None,
            correlation_to_btc=None,
            backtest_engine="backtrader",
            cost_model_id="cm1:d10:s5",
            look_ahead_audit_source="broker_config_only",
        )
        row = BacktestResultRecord.from_backtest_result(
            strategy_id="round-trip",
            content_hash="abc123",
            result=result,
            source_pipeline="test",
        )
        restored = row.to_backtest_result()

        assert restored.cost_model_id == "cm1:d10:s5"
        assert restored.look_ahead_audit_source == "broker_config_only"
        assert restored.backtest_engine == "backtrader"

    def test_missing_correlation_stays_none_instead_of_zero(self) -> None:
        """0.0 asserts "uncorrelated to SPY", which is a claim nothing measured."""
        from archimedes.models.backtest import BacktestResult
        from archimedes.models.backtest_store import BacktestResultRecord

        result = BacktestResult(
            strategy_id="no-corr",
            sharpe_ratio=1.0,
            sortino_ratio=1.0,
            max_drawdown=0.1,
            cagr=0.1,
            calmar_ratio=1.0,
            win_rate=0.5,
            profit_factor=1.2,
            total_trades=10,
            avg_holding_period_days=5.0,
            correlation_to_spy=None,
            correlation_to_btc=None,
            backtest_engine="backtrader",
        )
        row = BacktestResultRecord.from_backtest_result(
            strategy_id="no-corr",
            content_hash="def456",
            result=result,
            source_pipeline="test",
        )
        restored = row.to_backtest_result()

        assert restored.correlation_to_spy is None
        assert restored.correlation_to_btc is None
