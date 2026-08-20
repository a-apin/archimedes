"""The two machine-readable discovery surfaces must describe the API that exists.

``GET /api/agent/manifest`` and the static ``ui/public/.well-known/agent.json`` both
hand an autonomous client a list of routes to call. A stale entry there is worse than
an omission: the agent spends a request, gets a 404, and has no way to tell "wrong path"
from "endpoint down". #1293 was filed after exactly that class of dogfood friction.

So both surfaces are checked against the app's own OpenAPI document — the same source
FastAPI serves at ``/openapi.json`` — rather than against a hand-kept list that would
drift in lockstep with the thing it is supposed to guard.

The ``/api/auth/*`` prefix is the one documented exemption: those routes belong to the
Better Auth Node service (``auth/server.js``), proxied onto the same origin by nginx,
so they are genuinely absent from the FastAPI route table. The exemption is asserted to
stay exactly that narrow — widening it would quietly turn this guard off.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

# Routes served by the separate Better Auth Node service, not by FastAPI.
_EXTERNALLY_SERVED_PREFIXES = ("/api/auth/",)

_ROUTE_RE = re.compile(r"^(GET|POST|PUT|PATCH|DELETE) (/\S*)$")

_REPO_ROOT = Path(__file__).resolve().parents[2]
_AGENT_CARD = _REPO_ROOT / "ui" / "public" / ".well-known" / "agent.json"


def _openapi_index() -> dict[str, set[str]]:
    """{path -> {METHOD, ...}} straight off the live app, no hand-kept mirror."""
    from archimedes.main import app

    return {path: {m.upper() for m in ops} for path, ops in app.openapi()["paths"].items()}


def unresolved_routes(routes: list[str], index: dict[str, set[str]]) -> list[str]:
    """Which of these ``"METHOD /path"`` strings do NOT exist on the app?

    Malformed strings count as unresolved: a route an agent cannot parse is as
    useless as one that 404s, and silently ignoring them would let a typo'd
    method sail through.
    """
    missing = []
    for route in routes:
        match = _ROUTE_RE.match(route.strip())
        if match is None:
            missing.append(route)
            continue
        method, path = match.group(1), match.group(2)
        if path.startswith(_EXTERNALLY_SERVED_PREFIXES):
            continue
        if method not in index.get(path, set()):
            missing.append(route)
    return missing


def _card() -> dict:
    return json.loads(_AGENT_CARD.read_text())


def _card_routes(card: dict) -> list[str]:
    return [
        route
        for group in card["endpoints"].values()
        if isinstance(group, dict)
        for route in group.get("routes", {}).values()
    ]


async def _manifest() -> dict:
    from archimedes.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/agent/manifest")
    assert resp.status_code == 200
    return resp.json()


# ── the exemption is narrow, and stays narrow ────────────────────────────────


def test_only_better_auth_is_exempt_from_route_resolution():
    """One exemption, spelled out. Widening this list disables the guard below."""
    assert _EXTERNALLY_SERVED_PREFIXES == ("/api/auth/",)


# ── manifest ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_manifest_routes_all_resolve_against_the_live_openapi():
    manifest = await _manifest()
    routes = [route for group in manifest["endpoints"].values() for route in group["routes"].values()]
    assert routes, "manifest advertised no routes at all"
    assert unresolved_routes(routes, _openapi_index()) == []


@pytest.mark.asyncio
async def test_manifest_advertises_the_live_paper_trading_group():
    """Paper trading is the deployment path that works TODAY — an agent that cannot
    find it concludes the journey dead-ends at the T3.2-pending vault create."""
    manifest = await _manifest()
    paper = manifest["endpoints"]["paper"]
    assert paper["status"] == "live"
    assert paper["auth_required"] is True
    assert paper["routes"]["deploy"] == "POST /api/paper/deployments"
    assert paper["routes"]["stop"] == "POST /api/paper/deployments/{deployment_id}/stop"


@pytest.mark.asyncio
async def test_manifest_advertises_the_public_generation_quote():
    manifest = await _manifest()
    assert manifest["endpoints"]["generate"]["routes"]["quote"] == "GET /api/generate/quote"


@pytest.mark.asyncio
async def test_manifest_wallet_providers_match_the_accepted_literal_exactly():
    """Drift guard: the advertised set IS the set the endpoint accepts.

    Read off ``WalletChallengeRequest``'s Literal rather than restated, so a value
    added to one side and not the other fails here instead of in an agent's retry loop.
    """
    from archimedes.api.wallet_routes import WALLET_PROVIDERS

    manifest = await _manifest()
    assert manifest["auth"]["wallet_link_providers"] == list(WALLET_PROVIDERS)
    assert "headless" in WALLET_PROVIDERS
    assert manifest["auth"]["wallet_link_provider_default_for_agents"] == "headless"


# ── static agent card ────────────────────────────────────────────────────────


def test_agent_card_is_valid_json_with_an_endpoints_block():
    card = _card()
    assert card["name"] == "Archimedes"
    assert set(card["endpoints"]) >= {"read", "auth", "walletLink", "generate", "paper"}


def test_agent_card_routes_all_resolve_against_the_live_openapi():
    """The card is a static file with no CI-visible link to the API — which is
    precisely why it went stale (#1293 dogfood finding #6)."""
    routes = _card_routes(_card())
    assert routes, "agent card advertised no routes at all"
    assert unresolved_routes(routes, _openapi_index()) == []


def test_agent_card_covers_the_full_agent_journey():
    """auth -> wallet link -> quote -> generate -> paper deployment, end to end."""
    routes = set(_card_routes(_card()))
    for required in (
        "POST /api/auth/sign-in/email",
        "POST /api/wallets/challenge",
        "POST /api/wallets/verify",
        "GET /api/generate/quote",
        "POST /api/generate/start",
        "GET /api/generate/jobs/{job_id}/candidates",
        "POST /api/paper/deployments",
        "GET /api/paper/deployments/{deployment_id}",
    ):
        assert required in routes, f"agent card omits {required}"


def test_agent_card_wallet_providers_match_the_accepted_literal_exactly():
    from archimedes.api.wallet_routes import WALLET_PROVIDERS

    auth = _card()["authentication"]
    assert auth["walletLinkProviders"] == list(WALLET_PROVIDERS)
    assert auth["walletLinkProviderForAgents"] == "headless"
    # The worked example an agent will copy verbatim must use the honest value.
    assert _card()["endpoints"]["walletLink"]["challengeBody"]["provider"] == "headless"


# ── guard demonstrations: the inputs that MUST be rejected ───────────────────


def test_route_checker_rejects_a_path_the_app_does_not_serve():
    """The failing input for the resolution guard: a plausible-looking 404."""
    index = _openapi_index()
    assert unresolved_routes(["POST /api/paper/deploy"], index) == ["POST /api/paper/deploy"]
    assert unresolved_routes(["GET /api/wallets/providers"], index) == ["GET /api/wallets/providers"]


def test_route_checker_rejects_a_real_path_under_the_wrong_method():
    """Right path, wrong verb — the failure mode a path-only check would miss."""
    index = _openapi_index()
    assert "/api/wallets/challenge" in index
    assert unresolved_routes(["GET /api/wallets/challenge"], index) == ["GET /api/wallets/challenge"]


def test_route_checker_rejects_a_malformed_route_string():
    index = _openapi_index()
    assert unresolved_routes(["/api/generate/quote"], index) == ["/api/generate/quote"]
    assert unresolved_routes(["FETCH /api/generate/quote"], index) == ["FETCH /api/generate/quote"]


def test_agent_card_with_a_fabricated_endpoint_fails_the_same_check():
    """Prove the card guard bites: inject a dead route into the real card shape."""
    card = _card()
    card["endpoints"]["paper"]["routes"]["bogus"] = "POST /api/paper/does-not-exist"
    assert unresolved_routes(_card_routes(card), _openapi_index()) == ["POST /api/paper/does-not-exist"]


def test_exemption_does_not_swallow_a_lookalike_prefix():
    """``/api/authz/...`` is not ``/api/auth/...`` — a prefix check must not slip."""
    assert unresolved_routes(["GET /api/authz/whoami"], _openapi_index()) == ["GET /api/authz/whoami"]
