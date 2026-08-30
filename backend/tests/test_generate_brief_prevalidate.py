"""Lane 1.3c: never charge for a brief we can cheaply reject.

Before this, a gibberish brief only surfaced BRIEF_INVALID from the LLM
validator running INSIDE run_generation — after the caller had already paid
(payment happens in generate_routes.start_generation, before the job runs;
see generation_pipeline._validate_brief). This file pins two things:

  * ``cheap_brief_reject`` itself — the deterministic, no-LLM prelude, unit
    tested directly (empty / too-short / keyboard-mash rejected; coherent-
    but-vague, finance-signal, and ticker-list intents pass through to the
    real validator, matching the LLM system prompt's own worked examples).
  * the ROUTE WIRING — POST /api/generate/start returns 422 with the honest
    BRIEF_INVALID shape for a gibberish brief, and — the load-bearing
    assertion — the payment dependency is never invoked and nothing is
    enqueued. Same harness as test_generate_payment_gate.py.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

from archimedes.agents.generation_pipeline import GenerateBrief, _validate_brief, cheap_brief_reject
from tests.auth_helpers import auth_cookies

RECIPIENT = "0x00000000000000000000000000000000000000a1"

# Deliberately unambiguous keyboard-mash: no everyday word, no finance-signal
# word, not a plausible ticker list (lowercase, >5 chars each).
_GIBBERISH_BODY = {"brief": {"intent": "zxcvbnm qwiopasd lkjhgfdsa", "risk_appetite": "moderate"}}


def _client() -> TestClient:
    from archimedes.main import app

    return TestClient(app)


def _mock_store() -> MagicMock:
    store = MagicMock()
    store.enqueue = AsyncMock(return_value="job-should-not-exist")
    return store


def _close_background_coroutine(coro):
    coro.close()
    return MagicMock()


def _harness(store):
    return (
        patch("archimedes.api.generate_routes.get_job_store", return_value=store),
        patch("archimedes.api.generate_routes.asyncio.create_task", side_effect=_close_background_coroutine),
    )


# ── cheap_brief_reject — unit tests ─────────────────────────────────────────


def test_empty_intent_is_rejected():
    result = cheap_brief_reject(GenerateBrief(intent=""))
    assert result is not None
    assert "reason" in result and "hint" in result


def test_whitespace_only_intent_is_rejected():
    result = cheap_brief_reject(GenerateBrief(intent="   \t\n  "))
    assert result is not None


def test_too_short_intent_is_rejected():
    result = cheap_brief_reject(GenerateBrief(intent="ab"))
    assert result is not None


def test_keyboard_mash_is_rejected():
    result = cheap_brief_reject(GenerateBrief(intent="zxcvbnm qwiopasd lkjhgfdsa"))
    assert result is not None
    assert result["reason"]
    assert result["hint"]


def test_pure_symbols_is_rejected():
    result = cheap_brief_reject(GenerateBrief(intent="!!! ### $$$ 1234"))
    assert result is not None


def test_finance_signal_word_defers_to_llm():
    # "momentum" alone is enough — cheap check must not overreach into
    # judging whether the FULL sentence is a coherent strategy ask.
    assert cheap_brief_reject(GenerateBrief(intent="crypto with momentum")) is None


def test_vague_but_coherent_bond_alternative_defers_to_llm():
    # Straight from the validator's own system-prompt example of a VALID
    # (if vague) brief — the cheap check must not reject it.
    assert cheap_brief_reject(GenerateBrief(intent="low-vol bond alternative")) is None


def test_ticker_list_defers_to_llm():
    # All-caps short tokens with no vowels required (BTC has none) must not
    # be flagged as gibberish — tickers commonly lack recognizable "words".
    assert cheap_brief_reject(GenerateBrief(intent="BTC ETH SOL")) is None


def test_single_unrecognized_token_defers_to_llm():
    # A single odd token could be a real (unlisted) word or slang; only
    # reject once there are enough unrecognized tokens to be confident.
    assert cheap_brief_reject(GenerateBrief(intent="degen")) is None


def test_off_topic_but_grammatical_text_defers_to_llm():
    # "add flour and bake at 350F" — off-topic (a recipe), but grammatical.
    # Judging topicality is the LLM's job, not this heuristic's: the cheap
    # check must not weaken/duplicate-drift what the real validator decides.
    assert cheap_brief_reject(GenerateBrief(intent="add flour and bake at 350F")) is None


# ── _validate_brief — the shared prelude fires without ever touching the LLM ─


async def test_validate_brief_rejects_gibberish_without_calling_the_llm_backend():
    """The real validator's OWN prelude (shared with the route gate) must
    catch this before it ever constructs an LLM backend — proves the two
    call sites use the same function, not a duplicated/drifted copy."""
    with patch(
        "archimedes.services.llm_backend.make_llm_backend",
        side_effect=AssertionError("the LLM backend must never be constructed for a cheaply-rejectable brief"),
    ):
        result = await _validate_brief(GenerateBrief(intent="zxcvbnm qwiopasd lkjhgfdsa"))
    assert result["is_valid"] is False
    assert result["reason"]
    assert result["hint"]


# ── Route wiring: POST /api/generate/start ──────────────────────────────────


def test_gibberish_brief_is_422_before_payment_and_settlement(monkeypatch) -> None:
    """The guard this issue is about: a gibberish brief must be refused with
    422 BEFORE the payment gate runs — no settlement attempted, nothing
    enqueued. Payment is ON (mirrors test_generate_payment_gate.py) so this
    actually exercises the ordering, not a vacuously-skipped gate."""
    monkeypatch.setenv("GENERATION_PAYMENT_REQUIRED", "true")
    monkeypatch.setenv("GENERATION_PAYMENT_RECIPIENT", RECIPIENT)
    monkeypatch.setenv("PAYMENTS_DRY_RUN", "false")

    store = _mock_store()
    p1, p2 = _harness(store)
    paywall_spy = AsyncMock(side_effect=AssertionError("payment must never be invoked for a cheaply-rejected brief"))
    with (
        p1,
        p2,
        patch("archimedes.api.generate_routes.generation_payment.enforce_generation_payment", paywall_spy),
    ):
        resp = _client().post("/api/generate/start", json=_GIBBERISH_BODY, cookies=auth_cookies())

    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert detail["reason"] == "brief_invalid"
    assert detail["code"] == "BRIEF_INVALID"
    assert "message" in detail and detail["message"]
    assert "hint" in detail and detail["hint"]

    paywall_spy.assert_not_called()
    store.enqueue.assert_not_called()


def test_valid_brief_still_reaches_the_paywall(monkeypatch) -> None:
    """Negative control: a normal brief must NOT be caught by the new gate —
    it still reaches the payment step exactly as before (402, since no
    Payment-Signature header is presented)."""
    monkeypatch.setenv("GENERATION_PAYMENT_REQUIRED", "true")
    monkeypatch.setenv("GENERATION_PAYMENT_RECIPIENT", RECIPIENT)
    monkeypatch.setenv("PAYMENTS_DRY_RUN", "false")

    store = _mock_store()
    p1, p2 = _harness(store)
    body = {"brief": {"intent": "low-vol treasury alternative", "risk_appetite": "moderate"}}
    with p1, p2:
        resp = _client().post("/api/generate/start", json=body, cookies=auth_cookies())

    assert resp.status_code == 402
    assert resp.json()["detail"]["reason"] == "payment_required"
    store.enqueue.assert_not_called()
