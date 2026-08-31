"""The paper trace BODY — issue #1575, build step 4.

``build_paper_trace`` stops at the hash and touches neither Redis nor the
chain (the same seam ``construction_trace.py`` holds), so everything here is
pure. What is pinned is the set of choices that make a paper trace honest and
reachable, each of which is a decision someone could reasonably undo:

  * ``vault_address=""``, NOT ``construction_trace.UNBOUND_VAULT`` — the
    sentinel is world-readable while ``PUBLIC_TRACE_VAULTS`` is unarmed.
  * ``decision_type="rebalance"``, NOT ``"paper_rebalance"`` — a new value
    fails #1569's frozenset SILENTLY.
  * ``confidence == 0.0`` and ``consulted_paper_hashes == []`` — absences
    stated rather than filled with plausible values.
  * paper-ness and provenance are INSIDE the hash, so neither can be stripped.

Hermetic: no DB, no Redis, no network.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from archimedes.models.trace import DecisionType
from archimedes.services.paper_trace import (
    PROVENANCE_BACKFILL,
    PROVENANCE_SETTLE,
    build_paper_trace,
)
from archimedes.services.strategy_dsl import FABER_2007_SPEC, validate_strategy_spec

_SPEC_DICT = dict(FABER_2007_SPEC)
_SPEC = validate_strategy_spec(_SPEC_DICT)
_DEPLOYMENT = "dep-1575"
_STRATEGY = "4f2b91c0aa3e1d55"
_DECIDED = date(2026, 7, 14)

_LEGS = [
    {
        "symbol": "SPY",
        "decided_on": _DECIDED,
        "filled_on": date(2026, 7, 15),
        "side": "buy",
        "size": 182.0,
        "price": 548.21,
        "value": 182.0 * 548.21,
        "commission": 99.77,
        "cash_after": 407.06,
        "cash_before": 407.06 + 182.0 * 548.21 + 99.77,
        "position_after": 182.0,
        "position_before": 0.0,
    }
]


def _build(**overrides):
    kwargs = {
        "deployment_id": _DEPLOYMENT,
        "strategy_id": _STRATEGY,
        "spec": _SPEC,
        "spec_dict": _SPEC_DICT,
        "decision_date": _DECIDED,
        "legs": _LEGS,
    }
    kwargs.update(overrides)
    return build_paper_trace(**kwargs)


# ── The field-by-field contract ─────────────────────────────────────────────


def test_decision_type_is_the_conforming_value_not_a_paper_specific_one():
    """#1569's matcher is a frozenset {rebalance, rotation, regime_change,
    skip} and ``list_traces``' ``decision_type`` regex is the same set. A
    ``"paper_rebalance"`` value would be rejected by BOTH — and the frozenset
    fails silently, so the passport would render "no traces for this strategy"
    while traces existed. Silent unreachability on the provenance surface is
    the worst available outcome, so the design conforms rather than extends."""
    assert _build().decision_type is DecisionType.REBALANCE


def test_the_vault_is_blank_never_the_zero_address_sentinel():
    """``construction_trace.UNBOUND_VAULT`` would make an unstamped paper
    trace WORLD-READABLE: ``is_public_trace_vault`` returns True for any
    non-blank address while ``PUBLIC_TRACE_VAULTS`` is unarmed, which is the
    live state. A blank vault is fail-closed. This is pinned against the real
    predicate, not asserted."""
    from archimedes.services.construction_trace import UNBOUND_VAULT
    from archimedes.services.trace_visibility import is_public_trace_vault

    trace = _build()
    assert trace.vault_address == ""
    assert trace.vault_address != UNBOUND_VAULT
    assert is_public_trace_vault(trace.vault_address) is False
    # The control that makes the line above mean something: the sentinel this
    # module deliberately does NOT use IS public with the allowlist unarmed.
    assert is_public_trace_vault(UNBOUND_VAULT) is True


def test_strategies_referenced_is_exactly_the_one_strategy_id():
    """#1569 compares WHOLE strings. One element, no prefixes, no composite
    anchors — the two non-conforming writers the constant documents (arXiv ids
    and paper anchors on construction traces) are the cautionary example."""
    assert _build().strategies_referenced == [_STRATEGY]


def test_confidence_is_zero_and_the_absence_is_stated():
    trace = _build()
    assert trace.confidence == 0.0
    assert "no calibrated source" in trace.expected_outcome
    assert "NOT anchored" in trace.expected_outcome


def test_consulted_paper_hashes_is_empty_when_nothing_resolves():
    """Emitting ``"2301.00001:"`` would be a half-formed value that reads as
    provenance. The bare ids stay visible in market_context instead."""
    trace = _build()
    assert trace.consulted_paper_hashes == []
    assert trace.market_context["source_arxiv_ids"] == sorted(_SPEC.source_arxiv_ids)
    assert trace.market_context["source_arxiv_ids"], "the fixture spec must actually cite something"


def test_timestamp_is_the_decision_bar_not_wall_clock():
    trace = _build()
    assert trace.timestamp == datetime(2026, 7, 14, tzinfo=UTC)
    assert trace.market_context["decided_on"] == "2026-07-14"
    assert trace.market_context["filled_on"] == ["2026-07-15"]


def test_portfolio_sides_bracket_the_legs():
    trace = _build()
    assert trace.portfolio_before["holdings"]["SPY"]["size"] == 0.0
    assert trace.portfolio_after["holdings"]["SPY"]["size"] == 182.0
    assert trace.portfolio_after["cash"] == pytest.approx(407.06)
    # `symbol`/`direction`/`amount` are the shape TradeExecutedResponse
    # requires; a leg keyed on "side" 500s GET /api/traces/, which makes the
    # trace unreachable exactly as a non-conforming decision_type would.
    from archimedes.api.schemas import TradeExecutedResponse

    assert trace.trades_executed == [
        {
            "symbol": "SPY",
            "direction": "buy",
            "amount": 182.0,
            "size": 182.0,
            "price": 548.21,
            "value": 99774.22,
            "commission": 99.77,
        }
    ]
    assert TradeExecutedResponse(**trace.trades_executed[0]).direction == "buy"


def test_a_decision_with_no_legs_is_rejected():
    with pytest.raises(ValueError, match="not a decision"):
        _build(legs=[])


# ── Hash properties ─────────────────────────────────────────────────────────


def test_hash_is_stable_across_two_builds_of_the_same_decision():
    """The id is derived from the decision key, not uuid4 — ``id`` is a HASHED
    field, so a random one would make every re-derivation look like a change
    and make drift detection impossible."""
    first, second = _build(), _build()
    assert first.id == second.id
    assert first.trace_hash == second.trace_hash
    assert len(first.trace_hash.removeprefix("0x")) == 64  # keccak256


def test_a_different_decision_date_is_a_different_trace():
    assert _build().id != _build(decision_date=date(2026, 8, 14)).id


def test_paperness_is_inside_the_hash():
    """``trigger`` and ``market_context`` are both hashed fields, so a paper
    trace cannot be laundered into a live one without breaking /verify."""
    trace = _build()
    assert trace.trigger == "paper_settle"
    assert trace.market_context["venue"] == "paper"

    laundered = _build()
    laundered.trigger = "scheduled_tick"
    laundered.market_context = {**laundered.market_context, "venue": "live"}
    assert laundered.compute_hash() != trace.trace_hash


def test_backfill_provenance_is_inside_the_hash():
    """The commit-reveal threat model is entirely post-hoc trace construction.
    A trace written after the fact must admit it, unstrippably."""
    settled = _build(provenance=PROVENANCE_SETTLE)
    backfilled = _build(provenance=PROVENANCE_BACKFILL)
    assert backfilled.market_context["trace_provenance"] == "backfill"
    assert backfilled.trace_hash != settled.trace_hash


def test_an_unknown_provenance_is_rejected():
    with pytest.raises(ValueError, match="provenance"):
        _build(provenance="realtime")


def test_changing_a_leg_changes_the_hash():
    tampered = _build(legs=[{**_LEGS[0], "size": 9999.0}])
    assert tampered.trace_hash != _build().trace_hash


# ── The no-LLM guard (G8) ───────────────────────────────────────────────────

_LLM_MARKERS = (
    "llm_backend",
    "bedrock",
    "anthropic",
    "openai",
    "ollama",
    "generate_completion",
    "invoke_model",
    "converse",
)


def test_no_llm_reaches_the_paper_trace_builder():
    """G8. There is no LLM in the paper settle path and there must not be one:
    a sentence written at settle time is a post-hoc rationalisation of a
    decision a deterministic engine already made — precisely the attack the
    commit-reveal spec exists to defeat.

    A prose claim ("deterministic, no LLM") that nothing enforces is the same
    defect, harder to grep for. The claim also appears in the rendered
    reasoning, so this test is what keeps that sentence true.
    """
    source = (Path(__file__).resolve().parents[2] / "archimedes/services/paper_trace.py").read_text()
    # The module docstring explains WHY there is no LLM, so scan code lines only.
    code = "\n".join(
        line for line in source.splitlines() if not line.lstrip().startswith("#") and "LLM" not in line
    ).lower()
    hits = [marker for marker in _LLM_MARKERS if marker in code]
    assert not hits, f"paper_trace.py must not reach an LLM client; found {hits}"
    assert "no LLM produced this text" in _build().reasoning


def test_reasoning_is_derived_from_the_spec_and_the_legs():
    reasoning = _build().reasoning
    assert _STRATEGY in reasoning
    assert _SPEC.name in reasoning
    assert "2026-07-14" in reasoning
    assert "SPY" in reasoning
    assert "sha256=" in reasoning
