"""Tests for server-side paid-tier model gating (T1.8).

The Generate cost picker advertises premium (Anthropic) Bedrock models, but the
backend had no allowlist/entitlement check — any caller could request any model.
These tests pin the real, server-authoritative gate:

  - a FREE model (non-Anthropic, ``works_now``) is always allowed;
  - a PREMIUM model (Anthropic ``us.anthropic.*``) WITHOUT entitlement is
    rejected with HTTP 402 (and is NOT silently downgraded);
  - a PREMIUM model WITH entitlement (wallet-connected + premium enabled) is
    allowed.

Two layers are covered:
  1. The pure ``model_gate`` helpers (classification + entitlement), in isolation.
  2. The ``POST /api/generate/start`` endpoint end-to-end, with the Redis-backed
     job store mocked at the boundary and the fire-and-forget pipeline stubbed,
     using a real signed SIWE cookie (test_generate_cancel_scoping precedent).

Hermetic: no Redis / Postgres / Anthropic / Arc RPC. The entitlement env flags
are set via monkeypatch and cleared by default.
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from archimedes.api.auth_siwe import _COOKIE_NAME, _sign_session
from archimedes.services.model_gate import (
    enforce_model_entitlement,
    is_entitled_to_premium,
    is_premium_model,
)
from fastapi import HTTPException
from fastapi.testclient import TestClient

# Mirrors ui/src/data/modelPricing.json: a free non-Anthropic model and the two
# premium Anthropic ones.
_FREE_MODEL = "amazon.nova-micro-v1:0"
_PREMIUM_HAIKU = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
_PREMIUM_SONNET = "us.anthropic.claude-sonnet-4-6"

_WALLET = "0xAb5801a7D398351b8bE11C439e05C5B3259aeC9B"
_OTHER_WALLET = "0x9999999999999999999999999999999999999999"


@pytest.fixture(autouse=True)
def _clear_premium_env(monkeypatch):
    """Premium tier is OFF by default unless a test opts in."""
    monkeypatch.delenv("PREMIUM_MODELS_ENABLED", raising=False)
    monkeypatch.delenv("PREMIUM_MODELS_ALLOWLIST", raising=False)


# ── Pure classification ──────────────────────────────────────────────


class TestIsPremiumModel:
    def test_free_non_anthropic_models_are_not_premium(self):
        for mid in (
            _FREE_MODEL,
            "zai.glm-4.7-flash",
            "deepseek.v3.2",
            "us.meta.llama3-3-70b-instruct-v1:0",
            "mistral.mistral-small-2402-v1:0",
            "moonshotai.kimi-k2.5",
        ):
            assert is_premium_model(mid) is False, mid

    def test_anthropic_models_are_premium(self):
        assert is_premium_model(_PREMIUM_HAIKU) is True
        assert is_premium_model(_PREMIUM_SONNET) is True

    def test_case_and_whitespace_insensitive(self):
        # A hand-typed or oddly-cased id can't bypass the gate.
        assert is_premium_model("  US.ANTHROPIC.claude-sonnet-4-6  ") is True

    def test_empty_or_none_is_not_premium(self):
        assert is_premium_model("") is False
        assert is_premium_model(None) is False  # type: ignore[arg-type]


# ── Entitlement logic ────────────────────────────────────────────────


class TestIsEntitledToPremium:
    def test_anonymous_never_entitled(self, monkeypatch):
        monkeypatch.setenv("PREMIUM_MODELS_ENABLED", "true")
        assert is_entitled_to_premium(None) is False
        assert is_entitled_to_premium("") is False

    def test_wallet_without_premium_config_not_entitled(self):
        assert is_entitled_to_premium(_WALLET) is False

    def test_global_flag_entitles_any_connected_wallet(self, monkeypatch):
        monkeypatch.setenv("PREMIUM_MODELS_ENABLED", "1")
        assert is_entitled_to_premium(_WALLET) is True

    def test_allowlisted_wallet_entitled(self, monkeypatch):
        monkeypatch.setenv("PREMIUM_MODELS_ALLOWLIST", _WALLET)
        assert is_entitled_to_premium(_WALLET) is True
        assert is_entitled_to_premium(_WALLET.lower()) is True

    def test_non_allowlisted_wallet_not_entitled(self, monkeypatch):
        monkeypatch.setenv("PREMIUM_MODELS_ALLOWLIST", _WALLET)
        assert is_entitled_to_premium(_OTHER_WALLET) is False


# ── enforce_model_entitlement (raises 402) ───────────────────────────


class TestEnforceModelEntitlement:
    def test_free_model_always_allowed(self):
        enforce_model_entitlement(_FREE_MODEL, wallet=None)  # no raise
        enforce_model_entitlement(_FREE_MODEL, wallet=_WALLET)  # no raise

    def test_unset_model_allowed(self):
        # None → server default model, never gated here.
        enforce_model_entitlement(None, wallet=None)

    def test_premium_without_entitlement_rejected_402(self):
        with pytest.raises(HTTPException) as exc:
            enforce_model_entitlement(_PREMIUM_SONNET, wallet=_WALLET)
        assert exc.value.status_code == 402
        # Reject, do NOT downgrade — the message must say so.
        assert "not downgraded" in exc.value.detail.lower()

    def test_premium_anonymous_rejected_402(self):
        with pytest.raises(HTTPException) as exc:
            enforce_model_entitlement(_PREMIUM_HAIKU, wallet=None)
        assert exc.value.status_code == 402

    def test_premium_with_global_flag_allowed(self, monkeypatch):
        monkeypatch.setenv("PREMIUM_MODELS_ENABLED", "true")
        enforce_model_entitlement(_PREMIUM_SONNET, wallet=_WALLET)  # no raise

    def test_premium_with_allowlist_allowed(self, monkeypatch):
        monkeypatch.setenv("PREMIUM_MODELS_ALLOWLIST", _WALLET)
        enforce_model_entitlement(_PREMIUM_HAIKU, wallet=_WALLET)  # no raise


# ── Endpoint: POST /api/generate/start ───────────────────────────────


def _cookies(wallet: str) -> dict[str, str]:
    return {_COOKIE_NAME: _sign_session(wallet, time.time())}


def _client() -> TestClient:
    from archimedes.main import app

    return TestClient(app)


def _mock_store() -> MagicMock:
    """A job store whose .enqueue returns a fixed job id; boundary-mocked."""
    store = MagicMock()
    store.enqueue = AsyncMock(return_value="job-xyz")
    return store


def _patched_start():
    """Patch the job store + stub the fire-and-forget pipeline so no real work runs."""
    return (
        patch("archimedes.api.generate_routes.get_job_store", return_value=_mock_store()),
        # Stub the background pipeline wrapper so run_generation (LLM/pipeline)
        # never executes — keeps the test hermetic.
        patch("archimedes.api.generate_routes._run_with_cleanup", new=AsyncMock(return_value=None)),
    )


def _start_payload(model: str | None) -> dict:
    body: dict = {"brief": {"intent": "momentum on majors", "risk_appetite": "moderate"}}
    if model is not None:
        body["model"] = model
    return body


def test_endpoint_free_model_allowed():
    """A free (non-Anthropic) model starts a job — 202, even anonymously."""
    p_store, p_run = _patched_start()
    with p_store, p_run:
        resp = _client().post("/api/generate/start", json=_start_payload(_FREE_MODEL), cookies=_cookies(_WALLET))
    assert resp.status_code == 202, resp.text
    assert resp.json()["job_id"] == "job-xyz"


def test_endpoint_no_model_allowed():
    """No model field → server default; never gated → 202."""
    p_store, p_run = _patched_start()
    with p_store, p_run:
        resp = _client().post("/api/generate/start", json=_start_payload(None), cookies=_cookies(_WALLET))
    assert resp.status_code == 202, resp.text


def test_endpoint_premium_without_entitlement_rejected_402():
    """A premium model from a connected-but-unentitled wallet → 402, no job enqueued."""
    store = _mock_store()
    with (
        patch("archimedes.api.generate_routes.get_job_store", return_value=store),
        patch("archimedes.api.generate_routes._run_with_cleanup", new=AsyncMock(return_value=None)),
    ):
        resp = _client().post(
            "/api/generate/start",
            json=_start_payload(_PREMIUM_SONNET),
            cookies=_cookies(_WALLET),
        )
    assert resp.status_code == 402, resp.text
    # Rejected before any work — no job was enqueued.
    store.enqueue.assert_not_called()


def test_endpoint_premium_with_entitlement_allowed(monkeypatch):
    """Premium model + connected wallet + premium enabled → 202."""
    monkeypatch.setenv("PREMIUM_MODELS_ENABLED", "true")
    p_store, p_run = _patched_start()
    with p_store, p_run:
        resp = _client().post(
            "/api/generate/start",
            json=_start_payload(_PREMIUM_SONNET),
            cookies=_cookies(_WALLET),
        )
    assert resp.status_code == 202, resp.text
    assert resp.json()["job_id"] == "job-xyz"
