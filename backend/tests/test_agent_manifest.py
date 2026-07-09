"""Tests for GET /api/agent/manifest — the agent-discoverability manifest."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient


@pytest.mark.asyncio
async def test_agent_manifest_returns_200_with_expected_top_level_keys():
    """The manifest is a public GET returning the documented top-level contract."""
    from archimedes.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/agent/manifest")

    assert resp.status_code == 200
    data = resp.json()
    assert set(data.keys()) == {"name", "blurb", "docs", "auth", "endpoints", "faucet"}
    assert data["name"] == "Archimedes"


@pytest.mark.asyncio
async def test_agent_manifest_auth_scheme_is_siwe():
    """The auth block advertises SIWE / EIP-4361 with the Arc chain id."""
    from archimedes.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/agent/manifest")

    data = resp.json()
    assert data["auth"]["scheme"] == "SIWE"
    assert data["auth"]["spec"] == "EIP-4361"
    assert data["auth"]["chain_id"] == 5042002


@pytest.mark.asyncio
async def test_agent_manifest_endpoint_groups_present():
    """All the documented endpoint groups are present, each with a status + routes."""
    from archimedes.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/agent/manifest")

    endpoints = resp.json()["endpoints"]
    expected_groups = {"read", "auth", "generate", "deploy", "marketplace", "monitor"}
    assert set(endpoints.keys()) == expected_groups
    for group in expected_groups:
        assert "status" in endpoints[group]
        assert "routes" in endpoints[group]
        assert endpoints[group]["routes"], f"{group} routes must be non-empty"


@pytest.mark.asyncio
async def test_agent_manifest_deploy_and_marketplace_marked_pending():
    """Deploy / marketplace / monitor are honestly marked pending the T3.2 redeploy (#588)."""
    from archimedes.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/agent/manifest")

    endpoints = resp.json()["endpoints"]
    for group in ("deploy", "marketplace", "monitor"):
        assert "#588" in endpoints[group]["status"]

    # READ / AUTH / GENERATE are live, not pending.
    for group in ("read", "auth", "generate"):
        assert endpoints[group]["status"] == "live"


@pytest.mark.asyncio
async def test_agent_manifest_includes_faucet_url():
    """The Circle faucet URL is surfaced for funding a test wallet."""
    from archimedes.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/agent/manifest")

    assert resp.json()["faucet"]["url"] == "https://faucet.circle.com/"
