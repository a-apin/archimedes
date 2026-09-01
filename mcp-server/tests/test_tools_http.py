"""Every tool, at the HTTP boundary, against the response shapes the API really sends.

The bodies below are lifted from ``docs/agent-quickstart.md``'s worked examples and its
error table — the same source the CLI's tests read — so a shape change on the server side
shows up here as a failing assertion rather than as a confused agent in production.

The paywall trio is the point of this file: ``402``, ``409 wallet_link_required`` and the
``429``s must arrive at the caller as distinct, structured, actionable results. They are
the product's real answers and this server neither retries them nor hides them.
"""

from __future__ import annotations

import asyncio

import pytest
from archimedes_mcp import contract, tools
from archimedes_mcp.server import build_server
from conftest import json_response

PAID_QUOTE = {
    "payment_required": True,
    "pricing_model": "flat_v1",
    "price": "$2.000000",
    "asset": "USDC",
    "chain": "arcTestnet",
    "recipient": "0xffa7abba5f17cb8471ebf150bf808bd6fb8856c1",
    "dry_run": False,
    "halted": False,
}
FREE_QUOTE = {**PAID_QUOTE, "payment_required": False, "dry_run": True, "price": "$0.000000"}

BRIEF = {"intent": "diversified low-volatility strategy for idle USDC"}


def call_tool(name: str, arguments: dict):
    """Drive the tool through the registered MCP server, not through the bare function.

    An assertion on the plain function proves the handler is right; it does not prove the
    result survives registration, argument validation, and structured-output conversion —
    which is what an agent actually receives.
    """
    result = asyncio.run(build_server().call_tool(name, arguments))
    assert result.is_error is False, "handlers return failures as results; they must not raise"
    return result.structured_content


# ── the free-tier path: no wallet, no signature, no payment header ───


def test_quote_is_public_and_needs_no_credential(mock_api):
    recorder = mock_api(lambda r: json_response(200, PAID_QUOTE))
    body = call_tool("archimedes_quote", {})
    assert body["ok"] is True
    assert body["payment_required"] is True
    assert body["price"] == "$2.000000"
    assert "authorization" not in {k.lower() for k in recorder.last.headers}


def test_free_tier_generation_needs_no_payment(mock_api, cached_session):
    """A ``payment_required: false`` host: 202 straight away, nothing signed, nothing sent."""

    def handler(request):
        if request.url.path == "/api/generate/quote":
            return json_response(200, FREE_QUOTE)
        return json_response(202, {"job_id": "job-1", "stream_url": "/api/generate/stream/job-1", "ttl_seconds": 3600})

    recorder = mock_api(handler)

    quote = call_tool("archimedes_quote", {})
    assert quote["payment_required"] is False

    body = call_tool("archimedes_generate_start", BRIEF)
    assert body["ok"] is True
    assert body["job_id"] == "job-1"

    sent = recorder.requests[-1]
    assert "payment-signature" not in {k.lower() for k in sent.headers}
    assert "idempotency-key" not in {k.lower() for k in sent.headers}


# ── the paywall, propagated rather than swallowed ────────────────────


def test_402_is_a_structured_payment_required_result(mock_api, cached_session):
    detail = {
        "reason": "payment_required",
        "message": "Generation requires payment.",
        "quote": PAID_QUOTE,
    }
    mock_api(
        lambda r: json_response(
            402,
            {"detail": detail},
            headers={"PAYMENT-REQUIRED": "x402;price=2000000;asset=USDC"},
        )
    )

    body = call_tool("archimedes_generate_start", BRIEF)

    assert body["ok"] is False
    assert body["error"] == "payment_required"
    assert body["http_status"] == 402
    # The live quote survives the trip — the agent needs price/chain/recipient to decide.
    assert body["quote"] == PAID_QUOTE
    assert body["payment_requirements"] == "x402;price=2000000;asset=USDC"
    # The remedy names the thing this server refuses to do, and the trap.
    assert "will not sign" in body["remedy"]
    assert "Idempotency-Key" in body["remedy"]
    assert "fresh signature is a fresh real charge" in body["remedy"]


def test_402_is_not_retried(mock_api, cached_session):
    """A blind retry of a 402 is a second real charge on a signed request. One call, always."""
    recorder = mock_api(lambda r: json_response(402, {"detail": {"reason": "payment_required", "quote": PAID_QUOTE}}))
    call_tool("archimedes_generate_start", BRIEF)
    assert len(recorder.requests) == 1


def test_premium_model_402_is_a_different_error_code(mock_api, cached_session):
    """Two meanings share status 402. Collapsing them would send an agent to sign a payment
    that would not have helped."""
    mock_api(
        lambda r: json_response(
            402, {"detail": "Model 'claude-opus' is a premium (Anthropic) model and requires an entitlement."}
        )
    )
    body = call_tool("archimedes_generate_start", BRIEF)
    assert body["error"] == "entitlement_required"
    assert "silently downgraded" in body["remedy"]


def test_409_wallet_link_required(mock_api, cached_session):
    mock_api(
        lambda r: json_response(409, {"detail": {"reason": "wallet_link_required", "message": "Link a wallet to pay."}})
    )
    body = call_tool("archimedes_generate_start", BRIEF)
    assert body["error"] == "wallet_link_required"
    assert "headless" in body["remedy"]
    # The faucet wall is named, because linking an empty wallet only moves you to the 402.
    assert "faucet" in body["remedy"]


def test_409_idempotency_key_already_used(mock_api, cached_session):
    mock_api(lambda r: json_response(409, {"detail": {"reason": "idempotency_key_already_used"}}))
    body = call_tool("archimedes_generate_start", BRIEF)
    assert body["error"] == "idempotency_key_already_used"
    assert "Do not re-sign" in body["remedy"]


@pytest.mark.parametrize(
    ("detail", "expected", "must_say"),
    [
        ({"reason": "generation_daily_cap", "scope": "user", "cap": 10}, "daily_cap_reached", "No payment was taken"),
        ({"reason": "generation_queue_full"}, "queue_full", "No payment was taken"),
        ("Rate limit exceeded. Please slow down and try again later.", "rate_limited", "requests-per-minute"),
    ],
)
def test_429_has_three_distinct_meanings(mock_api, cached_session, detail, expected, must_say):
    mock_api(lambda r: json_response(429, {"detail": detail}))
    body = call_tool("archimedes_generate_start", BRIEF)
    assert body["error"] == expected
    assert must_say in body["remedy"]


def test_daily_cap_carries_the_scope(mock_api, cached_session):
    mock_api(lambda r: json_response(429, {"detail": {"reason": "generation_daily_cap", "scope": "ip", "cap": 25}}))
    body = call_tool("archimedes_generate_start", BRIEF)
    assert body["scope"] == "ip"


# ── credentials: absent, expired, revoked ────────────────────────────


@pytest.mark.parametrize(
    "name",
    [s["name"] for s in contract.TOOLS if s["auth"] == contract.AUTH_CREDENTIAL],
)
def test_gated_tools_refuse_locally_without_a_credential(mock_api, name):
    """No credential, no socket. The error names both ways to fix it."""
    recorder = mock_api(lambda r: json_response(200, {}))
    arguments = {
        "archimedes_rigor_verify": {"returns": [{"date": "2026-01-02", "daily_return": 0.001}]},
        "archimedes_generate_start": BRIEF,
        "archimedes_generate_status": {"job_id": "job-1"},
    }.get(name, {})

    body = call_tool(name, arguments)

    assert body["ok"] is False
    assert body["error"] == "no_credential"
    assert "ARCHIMEDES_API_KEY" in body["remedy"]
    assert "archimedes login" in body["remedy"]
    assert recorder.requests == [], "a gated tool must not spend a request to be told it has no credential"


def test_401_on_a_session_credential_says_log_in_again(mock_api, cached_session):
    mock_api(lambda r: json_response(401, {"detail": "Authentication required"}))
    body = call_tool("archimedes_usage", {})
    assert body["error"] == "unauthenticated"
    assert "archimedes login" in body["remedy"]


def test_401_on_an_api_key_says_the_key_may_be_revoked_or_the_lane_undeployed(mock_api, api_key):
    """The honest remedy for today: a bearer key 401s on any host where the D3 lane is not
    deployed, and that is the likeliest cause — saying only 'revoked' would send an agent
    hunting a key problem that is really a deployment fact."""
    mock_api(lambda r: json_response(401, {"detail": "Authentication required"}))
    body = call_tool("archimedes_usage", {})
    assert body["error"] == "unauthenticated"
    assert "revoked" in body["remedy"]
    assert "not be deployed on this host yet" in body["remedy"]


# ── the reads ────────────────────────────────────────────────────────


def test_usage_passes_through_a_null_counter_without_fabricating_zero(mock_api, cached_session):
    payload = {
        "date": "2026-08-31",
        "user": {"used": None, "cap": 10, "error": "quota_backend_unavailable"},
        "ip": {"used": 3, "cap": 25, "error": None},
        "quote": PAID_QUOTE,
    }
    mock_api(lambda r: json_response(200, payload))
    body = call_tool("archimedes_usage", {})
    assert body["user"]["used"] is None
    assert body["user"]["error"] == "quota_backend_unavailable"


def test_rigor_verify_returns_the_capped_verdict_verbatim(mock_api, cached_session):
    payload = {
        "passes": True,
        "trials": 4,
        "n_bars": 260,
        "legs_evaluated": 2,
        "legs_runnable": 2,
        "legs_total": 4,
        "legs_not_run": ["pbo", "look_ahead"],
        "verdict_capped": True,
        "dsr": {"status": "pass", "deflated_sharpe": 0.62, "dsr_p_value": 0.03},
        "pbo": {"status": "not_evaluable", "reason": "needs a trial matrix"},
        "oos_consistency": {"status": "pass", "oos_sharpe": 0.44},
        "look_ahead": {"status": "not_evaluable", "reason": "needs strategy source"},
        "rf_convention": "excess_tbill_series",
    }
    recorder = mock_api(lambda r: json_response(200, payload))

    body = call_tool(
        "archimedes_rigor_verify",
        {"returns": [{"date": "2026-01-02", "daily_return": 0.001}], "trials": 4},
    )

    assert body["ok"] is True
    # Not re-derived, not summarised, not "helpfully" flattened into a boolean.
    assert body["verdict_capped"] is True
    assert body["legs_not_run"] == ["pbo", "look_ahead"]
    assert body["rf_convention"] == "excess_tbill_series"
    assert recorder.last.url.path == "/api/rigor/verify"


def test_rigor_verify_rejects_zero_trials_without_a_request(mock_api, cached_session):
    recorder = mock_api(lambda r: json_response(200, {}))
    body = call_tool("archimedes_rigor_verify", {"returns": [{"date": "2026-01-02", "daily_return": 0.0}], "trials": 0})
    assert body["error"] == "invalid_request"
    assert recorder.requests == []


def test_strategy_read_is_public_and_preserves_the_four_state_gate(mock_api):
    mock_api(lambda r: json_response(200, {"id": "s1", "rigor_gate_status": "pending", "passes_rigor_gate": False}))
    body = call_tool("archimedes_strategy", {"strategy_id": "s1"})
    assert body["rigor_gate_status"] == "pending"
    assert body["passes_rigor_gate"] is False


def test_strategy_404_explains_that_existence_is_private(mock_api):
    mock_api(lambda r: json_response(404, {"detail": "Strategy not found"}))
    body = call_tool("archimedes_strategy", {"strategy_id": "nope"})
    assert body["error"] == "not_found"
    assert "existence is private" in body["remedy"]


def test_leaderboard_scope_is_forwarded_as_a_query_parameter(mock_api):
    recorder = mock_api(lambda r: json_response(200, {"scope": "curated", "entries": []}))
    body = call_tool("archimedes_leaderboard", {"scope": "own", "limit": 5})
    assert body["ok"] is True
    assert recorder.last.url.params["scope"] == "own"
    assert recorder.last.url.params["limit"] == "5"
    # And the response's own scope is what actually happened, which is why it is passed through.
    assert body["scope"] == "curated"


def test_leaderboard_omits_unset_optional_parameters(mock_api):
    recorder = mock_api(lambda r: json_response(200, {"scope": "curated", "entries": []}))
    call_tool("archimedes_leaderboard", {})
    assert "scope" not in recorder.last.url.params


def test_corpus_search_forwards_the_query(mock_api):
    recorder = mock_api(lambda r: json_response(200, {"papers": [], "total": 0}))
    body = call_tool("archimedes_corpus_search", {"search": "momentum crash"})
    assert body["ok"] is True
    assert recorder.last.url.params["search"] == "momentum crash"
    assert recorder.last.url.path == "/api/papers/"


def test_passport_read(mock_api):
    mock_api(lambda r: json_response(200, {"strategy_id": "s1", "status": "published"}))
    body = call_tool("archimedes_passport", {"strategy_id": "s1"})
    assert body["ok"] is True
    assert body["strategy_id"] == "s1"


def test_generate_status_passthrough(mock_api, cached_session):
    mock_api(lambda r: json_response(200, {"job_id": "j", "state": "done", "best_strategy_id": "s1"}))
    body = call_tool("archimedes_generate_status", {"job_id": "j"})
    assert body["state"] == "done"
    assert body["best_strategy_id"] == "s1"


# ── transport ────────────────────────────────────────────────────────


def test_a_dead_socket_is_its_own_error_not_a_gate_verdict(mock_api, cached_session):
    import httpx

    def handler(request):
        raise httpx.ConnectError("connection refused")

    mock_api(handler)
    body = call_tool("archimedes_generate_start", BRIEF)
    assert body["error"] == "network_error"
    assert "nothing was charged" in body["remedy"]


def test_a_non_json_success_body_is_reported_not_guessed(mock_api):
    import httpx

    mock_api(lambda r: httpx.Response(200, text="<html>maintenance</html>"))
    body = call_tool("archimedes_quote", {})
    assert body["error"] == "malformed_response"


def test_a_server_body_cannot_flip_the_ok_flag(mock_api):
    """The success/failure signal must not be settable by the payload it describes."""
    mock_api(lambda r: json_response(200, {"ok": False, "payment_required": True}))
    body = call_tool("archimedes_quote", {})
    assert body["ok"] is True


def test_handlers_are_reachable_directly_too():
    """The registry is the same set of callables the server registers — no wrapper layer."""
    assert tools.HANDLERS["archimedes_quote"].__name__ == "archimedes_quote"
