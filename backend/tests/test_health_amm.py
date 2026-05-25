"""Tests for /health/amm endpoint (Issue #309)."""

from __future__ import annotations
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient


@pytest.mark.asyncio
async def test_health_amm_returns_200_or_503_never_404():
    from archimedes.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/health/amm")
    assert resp.status_code in (200, 503), f"Expected 200 or 503, got {resp.status_code}"
    data = resp.json()
    assert "pools" in data or "status" in data


@pytest.mark.asyncio
async def test_health_amm_503_when_chain_disconnected():
    from archimedes.main import app

    with patch("archimedes.chain.client.chain_client.is_connected", new=AsyncMock(return_value=False)):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/health/amm")
    assert resp.status_code == 503
    data = resp.json()
    assert data["status"] == "amm_pools_not_initialized"
    assert data["pool_count"] == 0


@pytest.mark.asyncio
async def test_health_amm_200_when_pools_active():
    from archimedes.main import app

    mock_call_result = AsyncMock(return_value="0x1234567890123456789012345678901234567890")
    mock_get_pool_call = MagicMock()
    mock_get_pool_call.call = mock_call_result
    mock_functions = MagicMock()
    mock_functions.getPool.return_value = mock_get_pool_call
    mock_router = MagicMock()
    mock_router.functions = mock_functions
    mock_loader = MagicMock()
    mock_loader.amm_router = mock_router
    with (
        patch("archimedes.chain.client.chain_client.is_connected", new=AsyncMock(return_value=True)),
        patch("archimedes.chain.contracts.get_contract_loader", return_value=mock_loader),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/health/amm")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["pool_count"] > 0


@pytest.mark.asyncio
async def test_health_main_no_regression():
    from archimedes.main import app

    with patch("archimedes.chain.client.chain_client.is_connected", new=AsyncMock(return_value=False)):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert "status" in data
    assert "service" in data
