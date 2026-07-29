"""DEPLOY + MONITOR unit coverage for the agent journey harness (#788 slice 2).

Target: scripts/agent_journey.py (repo root — loaded via importlib, it isn't a
package). Mirrors test_agent_journey_auth.py's loader pattern. Three surfaces:

  1. ``build_vault_create_payload`` — a pure function. Cross-checked here
     against the REAL ``VaultCreateRequest`` Pydantic model
     (``archimedes.api.vault_schemas``), which is what
     ``vaults_routes.py::create_vault`` binds ``POST /api/vaults/create``'s
     body to — so a field-name/constraint drift between the harness and the
     live route fails THIS test, not a live 422 while dogfooding.
  2. ``step_deploy`` — the rigor-gate refusal (never call the endpoint for a
     missing/non-deployable winner — the #788 anti-goal: never weaken or
     bypass the rigor gate), the dry-run default (never call the endpoint
     without ``execute=True``), and the execute path's success/failure
     handling. ``client`` is a ``MagicMock`` throughout — no network, no
     ASGI, no live server.
  3. ``step_monitor`` — the read-only vault-health GET, mocked the same way.

Hermetic: no network, no Redis/Postgres, no live server, no async ASGI
context. The only real import is the Pydantic model itself (a pure schema
class with no DB/network side effects at import time).
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import MagicMock

import httpx
import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]


def _load_agent_journey():
    """Import scripts/agent_journey.py from the repo root by file path."""
    path = _REPO_ROOT / "scripts" / "agent_journey.py"
    spec = importlib.util.spec_from_file_location("agent_journey", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["agent_journey"] = module
    spec.loader.exec_module(module)
    return module


aj = _load_agent_journey()

# A live-gate-deployable winner, shaped exactly like step_readback's return.
_DEPLOYABLE_WINNER = {
    "strategy_id": "strat-abc",
    "strategy_name": "Momentum Fusion",
    "rigor_gate_status": "pass",
    "deployable": True,
}


def _deploy_kwargs(**overrides):
    kwargs = {
        "vault_name": "Test Vault",
        "vault_symbol": "TESTV",
        "management_fee_bps": 0,
        "performance_fee_bps": 0,
        "strictness_level": 1,
    }
    kwargs.update(overrides)
    return kwargs


def _expected_payload(winner=_DEPLOYABLE_WINNER, **overrides):
    """The payload step_deploy is expected to build + send for the given winner
    and (possibly overridden) deploy kwargs — used to assert the exact POST body.
    """
    kwargs = _deploy_kwargs(**overrides)
    return aj.build_vault_create_payload(
        strategy_id=winner["strategy_id"],
        name=kwargs["vault_name"],
        symbol=kwargs["vault_symbol"],
        management_fee_bps=kwargs["management_fee_bps"],
        performance_fee_bps=kwargs["performance_fee_bps"],
        strictness_level=kwargs["strictness_level"],
    )


# ── build_vault_create_payload — cross-checked against the real route contract ──


def test_payload_field_names_match_vault_create_request_exactly():
    """No stray keys, no missing keys — the two must name the same fields.

    A drift here (the route adds/renames a field the harness doesn't know
    about, or the harness invents one Pydantic doesn't) fails THIS test
    instead of surfacing as a live 422 while dogfooding.
    """
    from archimedes.api.vault_schemas import VaultCreateRequest

    payload = aj.build_vault_create_payload(strategy_id="s1", name="Vault", symbol="VLT")
    assert set(payload.keys()) == set(VaultCreateRequest.model_fields.keys())


def test_payload_is_accepted_by_the_real_pydantic_model():
    """Construct the REAL request model straight from the payload — proves
    Pydantic binding (the first thing the live route does to the body)
    accepts the harness's shape and preserves every value."""
    from archimedes.api.vault_schemas import VaultCreateRequest

    payload = aj.build_vault_create_payload(
        strategy_id="strat-123",
        name="Agent Journey Vault",
        symbol="AGTJRN",
        management_fee_bps=50,
        performance_fee_bps=1000,
        strictness_level=3,
    )
    req = VaultCreateRequest(**payload)
    assert req.name == "Agent Journey Vault"
    assert req.symbol == "AGTJRN"
    assert req.management_fee_bps == 50
    assert req.performance_fee_bps == 1000
    assert req.strategy_ids == ["strat-123"]
    assert req.strictness_level == 3
    assert req.agent_assisted is True


def test_payload_defaults_zero_fees_strictest_level_agent_assisted():
    from archimedes.api.vault_schemas import VaultCreateRequest

    payload = aj.build_vault_create_payload(strategy_id="s1", name="V", symbol="V1")
    req = VaultCreateRequest(**payload)
    assert req.management_fee_bps == 0
    assert req.performance_fee_bps == 0
    assert req.strictness_level == 1
    assert req.agent_assisted is True


def test_payload_wraps_the_single_strategy_id_in_a_list():
    payload = aj.build_vault_create_payload(strategy_id="only-one", name="V", symbol="V1")
    assert payload["strategy_ids"] == ["only-one"]


def test_payload_respects_agent_assisted_false():
    from archimedes.api.vault_schemas import VaultCreateRequest

    payload = aj.build_vault_create_payload(strategy_id="s1", name="V", symbol="V1", agent_assisted=False)
    req = VaultCreateRequest(**payload)
    assert req.agent_assisted is False


def test_out_of_range_strictness_level_is_rejected_by_the_real_model():
    """VaultCreateRequest bounds strictness_level to 1..5 (ge=1, le=5) — matches
    the CLI's own --strictness-level choices=[1..5]. A client-side clamp
    mismatch would show up here first, not as a live 422."""
    from archimedes.api.vault_schemas import VaultCreateRequest
    from pydantic import ValidationError

    payload = aj.build_vault_create_payload(strategy_id="s1", name="V", symbol="V1", strictness_level=6)
    with pytest.raises(ValidationError):
        VaultCreateRequest(**payload)


# ── step_deploy — rigor-gate refusal (the #788 anti-goal) ────────────────────


def test_no_winner_never_calls_post():
    client = MagicMock()
    result = aj.step_deploy(client, None, execute=True, **_deploy_kwargs())
    assert result is None
    client.post.assert_not_called()


def test_winner_missing_deployable_key_never_calls_post():
    """A winner dict without a 'deployable' key at all (e.g. resolution
    failed upstream) must be treated as non-deployable, not truthy-by-omission."""
    client = MagicMock()
    winner = {"strategy_id": "s1", "strategy_name": "X", "rigor_gate_status": "pending"}
    result = aj.step_deploy(client, winner, execute=True, **_deploy_kwargs())
    assert result is None
    client.post.assert_not_called()


def test_winner_deployable_false_never_calls_post():
    client = MagicMock()
    winner = {**_DEPLOYABLE_WINNER, "deployable": False, "rigor_gate_status": "fail"}
    result = aj.step_deploy(client, winner, execute=True, **_deploy_kwargs())
    assert result is None
    client.post.assert_not_called()


def test_refusal_holds_even_with_execute_true():
    """execute=True must never override the rigor gate — it only gates whether
    a DEPLOYABLE winner's payload is actually sent, not whether the gate runs."""
    client = MagicMock()
    result = aj.step_deploy(client, None, execute=True, **_deploy_kwargs())
    assert result is None
    client.post.assert_not_called()


# ── step_deploy — dry-run default (execute=False) ────────────────────────────


def test_dry_run_prints_payload_and_returns_none_without_posting(capsys):
    client = MagicMock()
    result = aj.step_deploy(client, _DEPLOYABLE_WINNER, execute=False, **_deploy_kwargs())
    assert result is None
    client.post.assert_not_called()
    out = capsys.readouterr().out
    assert "/api/vaults/create" in out
    assert "DRY RUN" in out


def test_dry_run_prints_the_exact_payload_that_would_be_sent(capsys):
    client = MagicMock()
    aj.step_deploy(client, _DEPLOYABLE_WINNER, execute=False, **_deploy_kwargs())
    out = capsys.readouterr().out
    assert _DEPLOYABLE_WINNER["strategy_id"] in out
    assert _deploy_kwargs()["vault_name"] in out


# ── step_deploy — execute=True path (mocked httpx.Client, no network) ────────


def test_execute_true_posts_expected_payload_and_returns_vault_address():
    client = MagicMock()
    response = MagicMock(status_code=200)
    response.json.return_value = {"vault_address": "0xVAULT123", "strategy_ids": ["strat-abc"]}
    client.post.return_value = response

    result = aj.step_deploy(client, _DEPLOYABLE_WINNER, execute=True, **_deploy_kwargs())

    assert result == "0xVAULT123"
    client.post.assert_called_once()
    call = client.post.call_args
    assert call.args[0] == "/api/vaults/create"
    assert call.kwargs["json"] == _expected_payload()


def test_execute_true_non_200_response_returns_none():
    client = MagicMock()
    response = MagicMock(status_code=422, text="Strategy has not passed the rigor gate")
    client.post.return_value = response

    result = aj.step_deploy(client, _DEPLOYABLE_WINNER, execute=True, **_deploy_kwargs())

    assert result is None


def test_execute_true_transport_error_returns_none():
    client = MagicMock()
    client.post.side_effect = httpx.ConnectError("boom")

    result = aj.step_deploy(client, _DEPLOYABLE_WINNER, execute=True, **_deploy_kwargs())

    assert result is None


def test_execute_true_response_missing_vault_address_key_returns_none():
    client = MagicMock()
    response = MagicMock(status_code=200)
    response.json.return_value = {"unexpected": "shape"}
    client.post.return_value = response

    result = aj.step_deploy(client, _DEPLOYABLE_WINNER, execute=True, **_deploy_kwargs())

    assert result is None


def test_execute_true_non_json_response_returns_none():
    client = MagicMock()
    response = MagicMock(status_code=200)
    response.json.side_effect = ValueError("not json")
    client.post.return_value = response

    result = aj.step_deploy(client, _DEPLOYABLE_WINNER, execute=True, **_deploy_kwargs())

    assert result is None


# ── step_monitor — read-only vault health GET ─────────────────────────────────


def test_monitor_reads_and_prints_health_dict():
    client = MagicMock()
    response = MagicMock(status_code=200)
    response.json.return_value = {
        "agent_alive": True,
        "last_rebalance": "2026-07-09T12:00:00+00:00",
        "aum_trend_pct": 1.23,
        "snapshot_count": 42,
    }
    client.get.return_value = response

    result = aj.step_monitor(client, "0xVAULT123")

    assert result is True
    client.get.assert_called_once_with("/api/vaults/0xVAULT123/health")


def test_monitor_prints_the_health_fields(capsys):
    client = MagicMock()
    response = MagicMock(status_code=200)
    response.json.return_value = {
        "agent_alive": True,
        "last_rebalance": "2026-07-09T12:00:00+00:00",
        "aum_trend_pct": 1.23,
        "snapshot_count": 42,
    }
    client.get.return_value = response

    aj.step_monitor(client, "0xVAULT123")

    out = capsys.readouterr().out
    assert "agent_alive=True" in out
    assert "snapshots=42" in out


def test_monitor_non_200_response_returns_false():
    client = MagicMock()
    response = MagicMock(status_code=503, text="unavailable")
    client.get.return_value = response

    result = aj.step_monitor(client, "0xUNKNOWN")

    assert result is False


def test_monitor_transport_error_returns_false():
    client = MagicMock()
    client.get.side_effect = httpx.ConnectError("boom")

    result = aj.step_monitor(client, "0xVAULT123")

    assert result is False


def test_monitor_non_json_response_returns_false():
    client = MagicMock()
    response = MagicMock(status_code=200)
    response.json.side_effect = ValueError("not json")
    client.get.return_value = response

    result = aj.step_monitor(client, "0xVAULT123")

    assert result is False
