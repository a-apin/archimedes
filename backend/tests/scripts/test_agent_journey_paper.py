"""PAPER-leg coverage for the agent journey harness (#1268 spine step).

Target: scripts/agent_journey.py (repo root — loaded via importlib, it isn't a
package). Mirrors test_agent_journey_deploy.py's loader pattern. Three
surfaces:

  1. ``build_paper_deploy_payload`` + ``PAPER_SUMMARY_FIELDS`` — cross-checked
     against the REAL ``deployment_summary()`` service output (built on a tmp
     sqlite via ``create_deployment``), so a field rename on the service side
     fails THIS test, not a live readback while dogfooding. This is the
     promise-checked-against-the-truth pattern (see test_agent_journey_deploy's
     VaultCreateRequest pin).
  2. ``step_paper`` skip semantics — no winner / no persisted strategy_id
     means the endpoint is never called.
  3. ``step_paper`` request + failure handling — the exact body posted, the
     201-only success contract, and created-but-unreadable failing the leg.

Hermetic: the httpx client is a MagicMock for the step tests; the truth-side
pin uses tmp sqlite only (no network, no Redis, no live server).
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]


def _load_agent_journey():
    """Import scripts/agent_journey.py from the repo root by file path."""
    path = _REPO_ROOT / "scripts" / "agent_journey.py"
    spec = importlib.util.spec_from_file_location("agent_journey_paper", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["agent_journey_paper"] = module
    spec.loader.exec_module(module)
    return module


aj = _load_agent_journey()

_WINNER = {
    "strategy_id": "strat-paper-1",
    "strategy_name": "Momentum Fusion",
    "rigor_gate_status": "pending",  # paper deliberately has no gate precondition
    "deployable": False,
}

# A valid DSL spec (test_paper_routes_auth precedent) for the truth-side pin.
_SPEC = {
    "name": "journey paper pin",
    "asset_universe": ["SPY"],
    "rebalance_frequency": "daily",
    "entry": {"gt": ["momentum_20", 0]},
    "exit": {"lt": ["momentum_20", -0.99]},
    "position_sizing": {"type": "full_invested_when_in_market"},
    "source_arxiv_ids": ["0706.1497"],
    "look_ahead_safe": True,
}


def _summary(deployment_id: str = "pd-1") -> dict:
    """A response shaped like the real deployment_summary() output."""
    return {
        "deployment_id": deployment_id,
        "strategy_id": _WINNER["strategy_id"],
        "deployed_at": "2026-08-19T00:00:00+00:00",
        "status": "active",
        "days": 0,
        "total_return": 0.0,
        "drift_detected_at": None,
        "series": [],
    }


def _response(status_code: int, payload: dict | None = None, text: str = "") -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = text
    if payload is not None:
        resp.json.return_value = payload
    else:
        resp.json.side_effect = ValueError("no json")
    return resp


# ── 1. The promise, checked against the truth ────────────────────────────────


@pytest.fixture()
def _tmp_db(tmp_path):
    from tests.db_isolation import redirect_to_tmp_sqlite

    yield from redirect_to_tmp_sqlite(tmp_path)


def test_summary_fields_exist_in_the_real_service_output(_tmp_db):
    """Every field the harness prints must exist in what deployment_summary()
    actually returns — built through the real create path, not a fixture."""
    from archimedes.db import get_session, init_db
    from archimedes.services.paper_trading import create_deployment, deployment_summary

    init_db()
    with get_session() as session:
        dep = create_deployment(
            session,
            strategy_id=_WINNER["strategy_id"],
            spec_dict=dict(_SPEC),
            owner_wallet=None,
            owner_user_id="user-journey-pin",
        )
        summary = deployment_summary(session, dep)
    missing = [f for f in aj.PAPER_SUMMARY_FIELDS if f not in summary]
    assert not missing, f"harness prints fields the service no longer returns: {missing}"


def test_payload_shape_is_the_single_required_field():
    assert aj.build_paper_deploy_payload("s-1") == {"strategy_id": "s-1"}


# ── 2. Skip semantics: never call the endpoint without a persisted winner ────


def test_no_winner_skips_without_calling():
    client = MagicMock()
    assert aj.step_paper(client, None) is None
    client.post.assert_not_called()
    client.get.assert_not_called()


def test_winner_without_strategy_id_skips_without_calling():
    client = MagicMock()
    assert aj.step_paper(client, {"strategy_name": "unpersisted", "strategy_id": None}) is None
    client.post.assert_not_called()


# ── 3. Request contract + failure handling ───────────────────────────────────


def test_success_posts_exact_body_and_reads_back():
    client = MagicMock()
    client.post.return_value = _response(201, _summary())
    client.get.return_value = _response(200, _summary())
    assert aj.step_paper(client, dict(_WINNER)) == "pd-1"
    client.post.assert_called_once_with("/api/paper/deployments", json={"strategy_id": _WINNER["strategy_id"]})
    client.get.assert_called_once_with("/api/paper/deployments/pd-1")


def test_non_201_fails_the_leg():
    client = MagicMock()
    client.post.return_value = _response(422, text='{"detail": "no spec"}')
    assert aj.step_paper(client, dict(_WINNER)) is None
    client.get.assert_not_called()


def test_created_but_unreadable_fails_the_leg():
    """The readback is part of the leg: a deployment the owner cannot GET back
    is a failure, not a footnote."""
    client = MagicMock()
    client.post.return_value = _response(201, _summary())
    client.get.return_value = _response(404, text="not found")
    assert aj.step_paper(client, dict(_WINNER)) is None


def test_malformed_201_body_fails_cleanly():
    client = MagicMock()
    client.post.return_value = _response(201, {"unexpected": "shape"})
    assert aj.step_paper(client, dict(_WINNER)) is None
    client.get.assert_not_called()


def test_200_is_not_success_the_contract_is_201_only():
    """The route's success is 201 Created, and the harness pins EXACTLY that:
    a 200 carrying an otherwise valid summary body must still fail the leg —
    a relaxed any-2xx check would mask a route/proxy behavior change the
    harness exists to surface. (Review: the 422 test alone cannot prove this;
    this test fails against a `status_code not in (200, 201)` relaxation.)"""
    client = MagicMock()
    client.post.return_value = _response(200, _summary())
    assert aj.step_paper(client, dict(_WINNER)) is None
    client.get.assert_not_called()
