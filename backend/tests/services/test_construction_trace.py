"""Unit coverage for the construction reasoning-trace builder.

Pure-function tests for `build_construction_trace`. Verifies that:
 - the payload reflects the proposal + guardrail inputs honestly,
 - regime falls back to the explicit unknown sentinel when missing,
 - confidence is never fabricated (stays 0.0 per the honesty rule),
 - the hash is computed deterministically and bound to content.

Added 2026-05-24 as part of the #147 coverage-gate lift.
"""

from __future__ import annotations

from datetime import UTC, datetime

from archimedes.agents.generation_json import (
    ArchitectProposal,
    StrategySelection,
)
from archimedes.models.trace import DecisionType
from archimedes.services.construction_trace import (
    UNBOUND_VAULT,
    build_construction_trace,
)
from archimedes.services.strategy_guardrail import GuardrailResult


def _selection(strategy_id: str = "faber_2007_sma200", paper: str = "Faber (2007)") -> StrategySelection:
    return StrategySelection(
        strategy_id=strategy_id,
        weight=0.6,
        rationale=f"Selected because {strategy_id} matches the user intent.",
        paper_citation=paper,
    )


def _proposal(
    *,
    regime: str | None = "risk_on",
    risk_notes: str = "Volatility moderate.",
    selections: list[StrategySelection] | None = None,
) -> ArchitectProposal:
    return ArchitectProposal(
        intent="Income with capital preservation",
        risk_profile="moderate",
        capital_usdc=10_000.0,
        regime=regime,
        selected=selections or [_selection()],
        overall_reasoning="Diversified across trend + carry to absorb regime shifts.",
        risk_notes=risk_notes,
        model_id="claude-sonnet-4-test",
        created_at=datetime.now(UTC),
    )


def _guardrail(
    *,
    weights: dict[str, float] | None = None,
    usyc: float = 0.3,
    dropped: list[str] | None = None,
    adjustments: list[str] | None = None,
) -> GuardrailResult:
    return GuardrailResult(
        strategy_weights=weights or {"faber_2007_sma200": 0.7},
        usyc_weight=usyc,
        risk_profile="moderate",
        dropped=dropped or [],
        adjustments=adjustments or [],
    )


class TestPortfolioAfter:
    def test_strategy_weights_round_to_six_places(self) -> None:
        guard = _guardrail(weights={"foo": 0.123456789})
        trace = build_construction_trace(_proposal(), guard)
        assert trace.portfolio_after["strategy_weights"]["foo"] == 0.123457

    def test_usyc_weight_round_to_six_places(self) -> None:
        guard = _guardrail(usyc=0.333333333)
        trace = build_construction_trace(_proposal(), guard)
        assert trace.portfolio_after["usyc_weight"] == 0.333333

    def test_dropped_sorted(self) -> None:
        guard = _guardrail(dropped=["zeta", "alpha", "mu"])
        trace = build_construction_trace(_proposal(), guard)
        assert trace.portfolio_after["dropped"] == ["alpha", "mu", "zeta"]

    def test_paper_citations_emitted_when_present(self) -> None:
        sel = _selection(paper="Moskowitz, Ooi, Pedersen (2012)")
        trace = build_construction_trace(_proposal(selections=[sel]), _guardrail())
        assert trace.portfolio_after["paper_citations"][sel.strategy_id]

    def test_paper_citations_omitted_when_empty(self) -> None:
        sel = _selection(paper="")
        trace = build_construction_trace(_proposal(selections=[sel]), _guardrail())
        assert sel.strategy_id not in trace.portfolio_after["paper_citations"]


class TestMarketContext:
    def test_known_regime_passed_through(self) -> None:
        trace = build_construction_trace(_proposal(regime="risk_off"), _guardrail())
        assert trace.market_context["regime"] == "risk_off"

    def test_missing_regime_falls_back_to_explicit_unknown(self) -> None:
        trace = build_construction_trace(_proposal(regime=None), _guardrail())
        assert "unknown" in trace.market_context["regime"].lower()

    def test_risk_profile_and_capital_recorded(self) -> None:
        trace = build_construction_trace(_proposal(), _guardrail())
        assert trace.market_context["risk_profile"] == "moderate"
        assert trace.market_context["capital_usdc"] == 10_000.0


class TestReasoningComposition:
    def test_risk_notes_appended(self) -> None:
        trace = build_construction_trace(_proposal(risk_notes="Drawdown capped at 12%."), _guardrail())
        assert "Drawdown capped at 12%" in trace.reasoning

    def test_no_risk_notes_no_section(self) -> None:
        trace = build_construction_trace(_proposal(risk_notes=""), _guardrail())
        assert "Risk notes:" not in trace.reasoning

    def test_guardrail_adjustments_appended_as_bullets(self) -> None:
        guard = _guardrail(adjustments=["Normalized weights", "Capped sBTC at 20%"])
        trace = build_construction_trace(_proposal(), guard)
        assert "Normalized weights" in trace.reasoning
        assert "Capped sBTC at 20%" in trace.reasoning
        assert "Guardrail adjustments:" in trace.reasoning

    def test_no_adjustments_no_section(self) -> None:
        trace = build_construction_trace(_proposal(), _guardrail(adjustments=[]))
        assert "Guardrail adjustments:" not in trace.reasoning


class TestTraceStructure:
    def test_default_vault_is_unbound_sentinel(self) -> None:
        trace = build_construction_trace(_proposal(), _guardrail())
        assert trace.vault_address == UNBOUND_VAULT

    def test_explicit_vault_address_honored(self) -> None:
        addr = "0x" + "a" * 40
        trace = build_construction_trace(_proposal(), _guardrail(), vault_address=addr)
        assert trace.vault_address == addr

    def test_decision_type_is_portfolio_construction(self) -> None:
        trace = build_construction_trace(_proposal(), _guardrail())
        assert trace.decision_type == DecisionType.PORTFOLIO_CONSTRUCTION

    def test_confidence_never_fabricated(self) -> None:
        trace = build_construction_trace(_proposal(), _guardrail())
        # Honesty rule: no calibrated source yet, so confidence MUST be 0.0
        assert trace.confidence == 0.0

    def test_trigger_is_user_request(self) -> None:
        trace = build_construction_trace(_proposal(), _guardrail())
        assert trace.trigger == "user_request"

    def test_expected_outcome_states_no_backtest_yet(self) -> None:
        trace = build_construction_trace(_proposal(), _guardrail())
        # Must surface the absence — never claim confidence we can't justify
        assert "no confidence score" in trace.expected_outcome.lower()
        assert "claude-sonnet-4-test" in trace.expected_outcome

    def test_strategies_referenced_passed_through(self) -> None:
        a = _selection("faber")
        b = _selection("tsmom")
        trace = build_construction_trace(_proposal(selections=[a, b]), _guardrail())
        assert set(trace.strategies_referenced) == {"faber", "tsmom"}

    def test_trades_executed_is_empty_for_construction(self) -> None:
        trace = build_construction_trace(_proposal(), _guardrail())
        assert trace.trades_executed == []

    def test_portfolio_before_is_empty_for_construction(self) -> None:
        trace = build_construction_trace(_proposal(), _guardrail())
        assert trace.portfolio_before == {}


class TestHashStability:
    def test_hash_is_populated(self) -> None:
        trace = build_construction_trace(_proposal(), _guardrail())
        assert trace.trace_hash
        assert trace.trace_hash != "0x"

    def test_hash_changes_with_content(self) -> None:
        t1 = build_construction_trace(_proposal(), _guardrail())
        t2 = build_construction_trace(_proposal(), _guardrail(weights={"x": 0.5, "y": 0.5}))
        assert t1.trace_hash != t2.trace_hash

    def test_arc_tx_hash_left_unset(self) -> None:
        trace = build_construction_trace(_proposal(), _guardrail())
        # Hard seam — chain publish is not this module's job
        assert trace.arc_tx_hash is None
