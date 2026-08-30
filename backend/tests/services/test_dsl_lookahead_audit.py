"""Tests for the REAL look-ahead audit of DSL-interpreted strategies.

The thing under test replaced a boolean the LLM declared about its own output
(``spec.look_ahead_safe``) with a structural proof:

  1. an AST audit of the DSL interpreter showing every bar-indexed read is at
     offset ``<= 0`` (bar t or earlier);
  2. a walk of the validated spec showing it uses nothing outside that audited
     surface;
  3. the broker cheat-on-close/open check charged on the real cerebro.

Every guard here is demonstrated adversarially — each test builds the input that
SHOULD fail the check and asserts it does. A guard nobody has watched reject
something is not a guard.
"""

from __future__ import annotations

import ast
import inspect

import backtrader as bt
import pytest
from archimedes.services import dsl_lookahead_audit as la
from archimedes.services import dsl_to_backtrader, strategy_dsl
from archimedes.services.dsl_lookahead_audit import (
    FAILED,
    PASSED_DECLARED_ONLY,
    PASSED_STRUCTURAL,
    audit_dsl_strategy,
    broker_cheat_check_passed,
    reset_interpreter_surface_cache,
    verify_interpreter_surface,
)
from archimedes.services.fusion_evaluator import apply_rigor_gate, evaluate_fusion_spec, run_dsl_backtest
from archimedes.services.strategy_dsl import FABER_2007_SPEC, StrategySpec, validate_strategy_spec


@pytest.fixture(autouse=True)
def _clean_surface_cache():
    """The interpreter audit is process-cached; tests that patch it must not leak."""
    reset_interpreter_surface_cache()
    yield
    reset_interpreter_surface_cache()


def _audit_source(source: str) -> la.InterpreterSurfaceAudit:
    """Run the interpreter bar-offset verifier over arbitrary source text.

    Used by the adversarial tests to feed the verifier code it SHOULD reject,
    without mutating the real interpreter on disk.
    """
    visitor = la._InterpreterAccessVisitor()
    visitor.visit(ast.parse(source))
    return la.InterpreterSurfaceAudit(
        bar_access_verified=not visitor.violations,
        violations=tuple(visitor.violations),
        sites_checked=len(visitor.sites),
    )


# ── 1. The interpreter surface audit ──────────────────────────────────


class TestInterpreterSurfaceAudit:
    def test_real_interpreter_passes_and_actually_checked_something(self):
        """The shipped interpreter reads only bar t and earlier.

        ``sites_checked > 0`` is load-bearing: a verifier that matched no data
        access would report "no violations" and be worthless. Assert it found
        the real reads.
        """
        audit = verify_interpreter_surface()
        assert audit.violations == ()
        assert audit.bar_access_verified is True
        assert audit.sites_checked >= 8, f"verifier matched only {audit.sites_checked} bar-access sites"
        assert audit.ok is True

    def test_no_unaudited_enum_drift_on_main(self):
        """The DSL's live enums have not grown past the audited surface."""
        assert verify_interpreter_surface().unverified_extensions == {}

    def test_verifier_finds_every_data_read_in_the_real_interpreter(self):
        """Pin the exact set of bar-indexed reads the audit covers.

        If someone adds a data read the visitor does not model, this count moves
        and the test says so, rather than the audit quietly covering less.
        """
        visitor = la._InterpreterAccessVisitor()
        visitor.visit(ast.parse(inspect.getsource(dsl_to_backtrader)))
        sources = sorted({s.split(": ", 1)[1] for s in visitor.sites})
        assert sources == [
            "data_line(...)",
            "ind[...]",
            "self.data.close[...]",
            "self.data.high[...]",
            "self.data.low[...]",
            "self.data.open[...]",
            "self.data.volume[...]",
        ]


class TestInterpreterVerifierRejectsFutureReads:
    """ADVERSARIAL: feed the verifier interpreters that DO read the future."""

    @pytest.mark.parametrize(
        ("body", "needle"),
        [
            # The canonical leak: bar t+1's close.
            ("            return float(self.data.close[1])", "positive bar offset [1]"),
            # A larger forward jump.
            ("            return float(self.data.high[5])", "positive bar offset [5]"),
            # An offset the verifier cannot prove — refuses to guess.
            ("            return float(self.data.close[shift])", "unprovable bar offset expression"),
            # Arithmetic that moves forward in time.
            ("            return float(self.data.close[-i + 2])", "unprovable bar-offset arithmetic"),
            # Subtracting a negative == adding.
            ("            return float(self.data.close[-i - -3])", "moves FORWARD in time"),
            # backtrader's delayed-line accessor pointed the wrong way.
            ("            return float(self.data.close(1)[0])", "positive bar offset [1]"),
        ],
    )
    def test_forward_read_is_rejected(self, body: str, needle: str):
        source = "class S:\n    def next(self):\n" + body + "\n"
        audit = _audit_source(source)
        assert audit.ok is False, f"verifier ACCEPTED a future read: {body.strip()}"
        assert any(needle in v for v in audit.violations), audit.violations

    def test_past_and_present_reads_are_accepted(self):
        """The mirror image: legitimate backtrader offsets must NOT be flagged.

        Without this, a verifier that rejects everything would pass the tests
        above while being useless.
        """
        source = (
            "class S:\n"
            "    def next(self):\n"
            "        a = float(self.data.close[0])\n"
            "        b = float(self.data.close[-1])\n"
            "        c = float(self.data.close[-i])\n"
            "        d = float(self.data.close[-i - 1])\n"
            "        return a + b + c + d\n"
        )
        audit = _audit_source(source)
        assert audit.violations == ()
        assert audit.sites_checked == 4

    def test_line_aliases_are_tracked_through_assignment(self):
        """A future read hidden behind a local alias is still caught."""
        source = "class S:\n    def next(self):\n        line = self.data.close\n        return float(line[1])\n"
        audit = _audit_source(source)
        assert audit.ok is False
        assert any("positive bar offset [1]" in v for v in audit.violations)

    def test_line_annotated_parameter_is_tracked(self):
        """``_make_indicator``-shaped code: a line arrives as a typed parameter."""
        source = "def make(data_line: bt.LineSeries):\n    return data_line(2)\n"
        audit = _audit_source(source)
        assert audit.ok is False
        assert any("positive bar offset [2]" in v for v in audit.violations)

    def test_non_line_indexing_is_not_flagged(self):
        """List/string indexing must not trip the verifier (no false positives).

        The generic AST auditor in ``rigor_evaluator`` flags ``args[1]`` and
        ``parts[1]`` in this very module — which is exactly why this verifier is
        root-based rather than a reuse of that one.
        """
        source = (
            "def f(name, args):\n"
            "    parts = name.rsplit('_', 1)\n"
            "    right = args[1]\n"
            "    return parts[0], parts[1], right\n"
        )
        audit = _audit_source(source)
        assert audit.violations == ()

    def test_a_broken_verifier_that_matches_nothing_is_not_a_pass(self):
        """``sites_checked == 0`` must not read as clean."""
        audit = _audit_source("def f():\n    return 1\n")
        assert audit.violations == ()
        assert audit.ok is False, "a verifier that matched no data access must not report OK"


class TestInterpreterAuditFailureFailsTheStrategy:
    """ADVERSARIAL, end to end: a leaking interpreter fails every spec."""

    def test_spec_fails_when_the_interpreter_audit_fails(self, monkeypatch):
        spec = validate_strategy_spec(FABER_2007_SPEC)
        assert audit_dsl_strategy(spec, broker_cheat_check=True).status == PASSED_STRUCTURAL

        broken = la.InterpreterSurfaceAudit(
            bar_access_verified=False,
            violations=("line 158: self.data.close[...] — positive bar offset [1] reads a FUTURE bar",),
            sites_checked=10,
        )
        monkeypatch.setattr(la, "verify_interpreter_surface", lambda: broken)

        audit = audit_dsl_strategy(spec, broker_cheat_check=True)
        assert audit.status == FAILED
        assert audit.passed is False
        assert any("FUTURE bar" in r for r in audit.reasons)


# ── 2. The spec-level structural walk ─────────────────────────────────


class TestVerifiedSurfaceSpecPasses:
    def test_faber_spec_passes_structural(self):
        spec = validate_strategy_spec(FABER_2007_SPEC)
        audit = audit_dsl_strategy(spec, broker_cheat_check=True)
        assert audit.status == PASSED_STRUCTURAL
        assert audit.passed is True
        assert audit.reasons == ()
        assert audit.interpreter_verified is True
        assert audit.broker_cheat_check is True
        assert "PASS (structural)" in audit.label

    def test_a_richer_in_surface_spec_passes(self):
        """Nested logic, several indicators, a variant grid — all in surface."""
        spec = validate_strategy_spec(
            {
                "name": "Multi-indicator",
                "asset_universe": ["SPY", "QQQ"],
                "rebalance_frequency": "weekly",
                "entry": {
                    "and": [
                        {"gt": ["close", "sma_50"]},
                        {"or": [{"lt": ["rsi_14", 70]}, {"gt": ["momentum_20", 0]}]},
                    ]
                },
                "exit": {"not": {"gte": ["close", "ema_100"]}},
                "position_sizing": {"type": "volatility_target", "annual_pct": 0.15},
                "source_arxiv_ids": ["1234.5678"],
                "look_ahead_safe": True,
                "parameter_variants": {"sma_50": [40, 50, 60]},
            }
        )
        audit = audit_dsl_strategy(spec, broker_cheat_check=True)
        assert audit.status == PASSED_STRUCTURAL, audit.reasons


class TestOutOfSurfaceSpecsFail:
    """ADVERSARIAL: constructs the interpreter audit never covered must FAIL.

    These specs are built directly as ``StrategySpec`` rather than through
    ``validate_strategy_spec``, because the DSL validator rejects most of them at
    the door. That is the point: the audit must not depend on the validator
    having run, and it must reject an unknown construct instead of assuming the
    closed enum still holds.
    """

    @staticmethod
    def _spec(**overrides) -> StrategySpec:
        base = {
            "name": "Out of surface",
            "asset_universe": ["SPY"],
            "rebalance_frequency": "monthly",
            "entry": {"gt": ["close", "sma_200"]},
            "exit": {"lt": ["close", "sma_200"]},
            "position_sizing": {"type": "full_invested_when_in_market"},
            "source_arxiv_ids": ["0706.1497"],
            "look_ahead_safe": True,
            "indicators": ["sma_200"],
        }
        base.update(overrides)
        return StrategySpec(**base)

    def test_unknown_indicator_fails(self):
        """A test-only indicator the interpreter was never audited for."""
        spec = self._spec(
            entry={"gt": ["close", "oracle_5"]},
            exit={"lt": ["close", "oracle_5"]},
            indicators=["oracle_5"],
        )
        audit = audit_dsl_strategy(spec, broker_cheat_check=True)
        assert audit.status == FAILED
        assert audit.passed is False
        assert any("'oracle'" in r and "outside the audited surface" in r for r in audit.reasons), audit.reasons

    def test_declared_look_ahead_safe_does_not_rescue_an_unknown_indicator(self):
        """The LLM's boolean has no vote — this is the whole point of the change."""
        spec = self._spec(
            entry={"gt": ["close", "oracle_5"]},
            exit={"lt": ["close", "oracle_5"]},
            indicators=["oracle_5"],
            look_ahead_safe=True,
        )
        audit = audit_dsl_strategy(spec, broker_cheat_check=True)
        assert audit.declared_intent is True, "the declaration is still recorded"
        assert audit.status == FAILED, "but it does not decide the verdict"

    def test_unknown_operator_fails(self):
        spec = self._spec(entry={"crosses_above": ["close", "sma_200"]})
        audit = audit_dsl_strategy(spec, broker_cheat_check=True)
        assert audit.status == FAILED
        assert any("crosses_above" in r for r in audit.reasons), audit.reasons

    def test_unknown_price_operand_fails(self):
        """``next_close`` is not a series the interpreter binds at bar t."""
        spec = self._spec(entry={"gt": ["next_close", "sma_200"]})
        audit = audit_dsl_strategy(spec, broker_cheat_check=True)
        assert audit.status == FAILED
        assert any("next_close" in r for r in audit.reasons), audit.reasons

    def test_indicator_alias_not_declared_by_the_spec_fails(self):
        """An alias the interpreter never binds into ``_bar_values``."""
        spec = self._spec(entry={"gt": ["close", "sma_50"]}, indicators=["sma_200"])
        audit = audit_dsl_strategy(spec, broker_cheat_check=True)
        assert audit.status == FAILED
        assert any("sma_50" in r for r in audit.reasons), audit.reasons

    def test_non_positive_indicator_period_fails(self):
        """``momentum_0`` compiles to ``data_line(-0)`` — bar t, not a past bar."""
        spec = self._spec(
            entry={"gt": ["momentum_0", 0]},
            exit={"lt": ["momentum_0", 0]},
            indicators=["momentum_0"],
        )
        audit = audit_dsl_strategy(spec, broker_cheat_check=True)
        assert audit.status == FAILED
        assert any("strictly past bar" in r for r in audit.reasons), audit.reasons

    def test_negative_variant_period_fails(self):
        """A variant grid is compiled surface too — ``momentum_-5`` reads forward."""
        spec = self._spec(
            entry={"gt": ["momentum_20", 0]},
            exit={"lt": ["momentum_20", 0]},
            indicators=["momentum_20"],
            parameter_variants={"momentum_20": [10, -5]},
        )
        audit = audit_dsl_strategy(spec, broker_cheat_check=True)
        assert audit.status == FAILED
        assert any("strictly past bar" in r for r in audit.reasons), audit.reasons

    def test_unknown_position_sizing_fails(self):
        spec = self._spec(position_sizing={"type": "kelly_optimal"})
        audit = audit_dsl_strategy(spec, broker_cheat_check=True)
        assert audit.status == FAILED
        assert any("kelly_optimal" in r for r in audit.reasons), audit.reasons

    def test_unknown_rebalance_frequency_fails(self):
        spec = self._spec(rebalance_frequency="intraday")
        audit = audit_dsl_strategy(spec, broker_cheat_check=True)
        assert audit.status == FAILED
        assert any("intraday" in r for r in audit.reasons), audit.reasons

    def test_unimplemented_indicator_fails(self):
        """``realized_vol`` is in the DSL enum but has no interpreter branch."""
        spec = self._spec(
            entry={"gt": ["realized_vol_20", 0]},
            exit={"lt": ["realized_vol_20", 0]},
            indicators=["realized_vol_20"],
        )
        audit = audit_dsl_strategy(spec, broker_cheat_check=True)
        assert audit.status == FAILED
        assert any("no audited interpreter implementation" in r for r in audit.reasons), audit.reasons


class TestEnumDriftIsNotInheritedAsAPass:
    """ADVERSARIAL: widening the DSL enum without re-auditing must not pass."""

    def test_new_indicator_added_to_the_dsl_enum_still_fails_the_audit(self, monkeypatch):
        monkeypatch.setattr(
            strategy_dsl,
            "INDICATOR_NAMES",
            frozenset(strategy_dsl.INDICATOR_NAMES | {"oracle"}),
        )
        reset_interpreter_surface_cache()

        surface = verify_interpreter_surface()
        assert surface.unverified_extensions["indicators"] == ("oracle",)

        spec = TestOutOfSurfaceSpecsFail._spec(
            entry={"gt": ["close", "oracle_5"]},
            exit={"lt": ["close", "oracle_5"]},
            indicators=["oracle_5"],
        )
        assert audit_dsl_strategy(spec, broker_cheat_check=True).status == FAILED

    def test_a_spec_avoiding_the_new_construct_still_passes(self, monkeypatch):
        """Drift fails the constructs that use it, not everything else."""
        monkeypatch.setattr(
            strategy_dsl,
            "INDICATOR_NAMES",
            frozenset(strategy_dsl.INDICATOR_NAMES | {"oracle"}),
        )
        reset_interpreter_surface_cache()
        spec = validate_strategy_spec(FABER_2007_SPEC)
        assert audit_dsl_strategy(spec, broker_cheat_check=True).status == PASSED_STRUCTURAL


# ── 3. The broker execution-timing check ──────────────────────────────


class TestBrokerCheatCheck:
    def test_a_default_cerebro_passes(self):
        assert broker_cheat_check_passed(bt.Cerebro(stdstats=False)) is True

    def test_cheat_on_close_is_caught(self):
        """ADVERSARIAL: a broker that fills on the signal's own bar close."""
        cerebro = bt.Cerebro(stdstats=False)
        cerebro.broker.set_coc(True)
        assert broker_cheat_check_passed(cerebro) is False

    def test_cheat_on_open_is_caught(self):
        """ADVERSARIAL: a broker that fills on the signal bar's open."""
        cerebro = bt.Cerebro(stdstats=False)
        cerebro.broker.set_coo(True)
        assert broker_cheat_check_passed(cerebro) is False

    def test_a_cerebro_without_broker_params_fails_closed(self):
        """An unverifiable broker is not a verified one."""

        class _NoBroker:
            pass

        assert broker_cheat_check_passed(_NoBroker()) is False

    def test_a_cheating_broker_fails_the_whole_audit(self):
        spec = validate_strategy_spec(FABER_2007_SPEC)
        audit = audit_dsl_strategy(spec, broker_cheat_check=False)
        assert audit.status == FAILED
        assert any("cheat on close/open" in r for r in audit.reasons)


class TestBrokerCheckIsWiredIntoTheDslBacktestPath:
    """ADVERSARIAL, end to end through ``run_dsl_backtest``."""

    def test_normal_run_records_a_passing_broker_check(self):
        spec = validate_strategy_spec(FABER_2007_SPEC)
        metrics = run_dsl_backtest(spec)
        assert metrics.broker_cheat_check_passed is True

    def test_a_cheating_broker_reaches_the_rigor_verdict_as_failed(self, monkeypatch):
        """Simulate a future misconfiguration of the broker and follow it out.

        Patches ``bt.Cerebro`` (the boundary — the broker's configuration), not
        the audit under test, then asserts the failure survives all the way to
        ``RigorVerdict.look_ahead_audit`` and flips ``passing`` off.
        """

        class _CheatingCerebro(bt.Cerebro):
            """A Cerebro misconfigured to fill on the signal's own bar.

            A subclass, not a factory function: backtrader's ``findowner`` does
            ``isinstance(obj, bt.Cerebro)`` during strategy construction, so the
            replacement has to remain a type.
            """

            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self.broker.set_coc(True)

        monkeypatch.setattr(bt, "Cerebro", _CheatingCerebro)

        spec = validate_strategy_spec(FABER_2007_SPEC)
        metrics = run_dsl_backtest(spec)
        assert metrics.broker_cheat_check_passed is False

        verdict = apply_rigor_gate(metrics, spec=spec)
        assert verdict.look_ahead_audit == FAILED
        assert verdict.look_ahead_clean is False
        assert verdict.passing is False

    def test_sleeve_aggregation_is_fail_closed_on_an_unchecked_sleeve(self):
        from archimedes.services.fusion_evaluator import _combine_broker_checks

        assert _combine_broker_checks([True, True]) is True
        assert _combine_broker_checks([True, False]) is False
        assert _combine_broker_checks([True, None]) is None
        assert _combine_broker_checks([False, None]) is False
        assert _combine_broker_checks([]) is None


# ── 4. Demotion of the self-declared boolean ──────────────────────────


class TestDeclaredOnlyIsNotAPass:
    def test_no_spec_degrades_to_declared_only_and_does_not_pass(self):
        audit = audit_dsl_strategy(None, broker_cheat_check=True)
        assert audit.status == PASSED_DECLARED_ONLY
        assert audit.passed is False
        assert "NOT AUDITED" in audit.label

    def test_no_broker_check_degrades_to_declared_only(self):
        """An incomplete audit does not get to claim a structural pass."""
        spec = validate_strategy_spec(FABER_2007_SPEC)
        audit = audit_dsl_strategy(spec, broker_cheat_check=None)
        assert audit.status == PASSED_DECLARED_ONLY
        assert audit.passed is False
        assert audit.interpreter_verified is True

    def test_gate_treats_declared_only_as_a_leak_failure(self):
        """The LEAK criterion: only ``passed_structural`` clears it.

        This is the regression guard for the demotion itself. Before this
        change ``look_ahead_clean`` was the literal ``True`` on the next line of
        ``apply_rigor_gate``, so this verdict passed.
        """
        from tests.services.test_fusion_evaluator import _make_high_sharpe_metrics, _two_variant_set

        metrics = _make_high_sharpe_metrics(data_source="csv:spy.csv")
        variants = _two_variant_set(metrics.equity_curve, data_source="csv:spy.csv")

        declared_only = apply_rigor_gate(metrics, variants_metrics=variants, spec=None)
        assert declared_only.look_ahead_audit == PASSED_DECLARED_ONLY
        assert declared_only.look_ahead_clean is False
        assert declared_only.passing is False, "a self-declared boolean must not clear the LEAK criterion"

        # Same numbers, same everything — the ONLY delta is a real audit subject.
        audited = apply_rigor_gate(metrics, variants_metrics=variants, spec=validate_strategy_spec(FABER_2007_SPEC))
        assert audited.look_ahead_audit == PASSED_STRUCTURAL
        assert audited.passing is True, "the other gate legs must be unchanged"

    def test_declared_intent_is_recorded_but_never_gates(self):
        spec = validate_strategy_spec(FABER_2007_SPEC)
        verdict = apply_rigor_gate(
            _stub_metrics_for(spec),
            spec=spec,
        )
        assert verdict.look_ahead_declared is True
        assert verdict.look_ahead_audit in {PASSED_STRUCTURAL, PASSED_DECLARED_ONLY, FAILED}


def _stub_metrics_for(spec):
    from archimedes.services.fusion_evaluator import BacktestMetrics

    return BacktestMetrics(
        sharpe_ratio=1.0,
        sortino_ratio=1.0,
        max_drawdown=0.1,
        cagr=0.1,
        calmar_ratio=1.0,
        win_rate=0.5,
        total_trades=10,
        avg_holding_period_days=5.0,
        equity_curve=[100_000.0 * (1.001**i) for i in range(300)],
        monthly_returns=[],
        backtest_start=None,
        backtest_end=None,
        data_source="csv:test.csv",
        broker_cheat_check_passed=True,
    )


# ── 5. Threading: verdict → passport blob → API ───────────────────────


class TestVerdictThreadsThroughToThePassport:
    def test_evaluate_fusion_spec_produces_a_structural_verdict(self):
        result = evaluate_fusion_spec(FABER_2007_SPEC)
        assert result.success, result.error
        assert result.rigor.look_ahead_audit == PASSED_STRUCTURAL
        assert result.rigor.look_ahead_declared is True
        assert result.rigor.look_ahead_reasons == ()
        assert "self-declared" not in result.rigor.look_ahead_label

    def test_rigor_verdict_dict_carries_the_three_state_field(self):
        from archimedes.agents.debate_engine import _rigor_verdict_dict

        blob = _rigor_verdict_dict(evaluate_fusion_spec(FABER_2007_SPEC))
        assert blob["look_ahead_audit"] == PASSED_STRUCTURAL
        assert blob["lookahead_audit_passed"] is True
        assert blob["look_ahead_declared"] is True

    def test_audit_source_label_is_derived_not_hardcoded(self):
        """``look_ahead_audit_source`` used to be a flat ``"self_attested"``."""
        from archimedes.agents.generation_pipeline import _look_ahead_audit_source

        assert _look_ahead_audit_source({"look_ahead_audit": PASSED_STRUCTURAL}) == "dsl_structural_audit"
        assert _look_ahead_audit_source({"look_ahead_audit": PASSED_DECLARED_ONLY}) == "self_attested"
        assert _look_ahead_audit_source({"look_ahead_audit": FAILED}) == "self_attested"
        # A verdict blob written before this landed.
        assert _look_ahead_audit_source({}) == "self_attested"

    def test_to_dict_is_json_shaped(self):
        audit = audit_dsl_strategy(validate_strategy_spec(FABER_2007_SPEC), broker_cheat_check=True)
        d = audit.to_dict()
        assert d["status"] == PASSED_STRUCTURAL
        assert d["reasons"] == []
        assert d["surface_version"] == la.VERIFIED_SURFACE_VERSION
        assert isinstance(d["label"], str)
