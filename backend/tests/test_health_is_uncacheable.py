"""Liveness endpoints must be uncacheable by any intermediary (issue #1520).

`/health` matched no CloudFront `ordered_cache_behavior`, so it fell through to
the default one, whose `html` cache policy has `default_ttl = 60`. Sending no
`Cache-Control` from the app let CloudFront cache it — measured on the live site
as `x-cache: Hit from cloudfront` with `age: 17`, and, on the error side, 504s
returning in 0.07s: far too fast to have reached the origin.

A cached health check is not a health check. It keeps answering "ok" for up to a
minute after the origin starts failing, which is the plausible-substitute
degradation this codebase treats as the primary defect class — the honest
degraded state is a loud absence, not a stale success.

The CloudFront behaviour is the other half and needs a terraform apply. This
half travels with the app, so it holds under any CDN and any future front door.

All tests are hermetic — no network, no .env, no DB.
Run: env -i HOME=$HOME PATH=$PATH PYTHONPATH=backend python -m pytest \
       backend/tests/test_health_is_uncacheable.py -q
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

# Read-only endpoint checks go through ASGITransport, not TestClient: entering
# TestClient's context manager runs the app's startup lifespan, which seeds the
# corpus and warms loader caches for every test that runs afterwards in the same
# process. Precedent: backend/tests/test_risk_routes.py.


def _health_routes() -> list[str]:
    """Every registered GET route whose path is a liveness path.

    Enumerated from the app rather than hard-coded, so a health endpoint added
    later is covered the day it is added instead of the day someone remembers
    this file exists.
    """
    from archimedes.main import app

    return sorted(
        {
            r.path
            for r in app.routes
            if getattr(r, "path", "").startswith(("/health", "/api/health")) and "GET" in getattr(r, "methods", set())
        }
    )


def test_the_enumeration_actually_finds_routes():
    """Guards the guard: an empty list would make every test below vacuous."""
    routes = _health_routes()
    assert len(routes) >= 4, routes
    assert "/health" in routes


class TestEveryLivenessRouteForbidsCaching:
    @pytest.mark.parametrize("path", _health_routes())
    async def test_route_sends_no_store(self, path: str):
        """MUTATION: drop the `_no_store(response)` call in any health handler.

        Asserts on the response regardless of status code — the AMM probe answers
        503 through a JSONResponse it builds itself, and a cacheable 503 is the
        worse half to get wrong: it pins a transient failure in front of every
        viewer at that edge until the TTL expires.
        """
        from archimedes.main import app

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get(path)

        cache_control = response.headers.get("cache-control", "")
        assert "no-store" in cache_control, (
            f"{path} returned Cache-Control={cache_control!r} with status "
            f"{response.status_code} — a cacheable liveness response can report "
            f"a stale 'ok' after the origin has started failing"
        )

    @pytest.mark.parametrize("path", _health_routes())
    async def test_route_sets_no_max_age_an_intermediary_could_honour(self, path: str):
        """CONTROL: `no-store` alongside a positive `max-age` is contradictory.

        Without this, `Cache-Control: no-store, max-age=3600` would satisfy the
        test above while still handing a CDN a number to act on.
        """
        from archimedes.main import app

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get(path)

        cache_control = response.headers.get("cache-control", "")
        for directive in cache_control.replace(" ", "").split(","):
            if directive.startswith("max-age="):
                assert directive == "max-age=0", f"{path} sent {directive!r} beside no-store"


class TestTheHeaderIsNotAppliedIndiscriminately:
    """The fix must be scoped to liveness paths, not bolted onto everything.

    MUTATION: apply no-store in middleware for every request. That would pass
    every test above while silently disabling HTML caching sitewide — the
    `html` policy's 60s TTL is deliberate and unrelated to this bug.
    """

    async def test_the_root_route_is_left_cacheable(self):
        from archimedes.main import app

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/")

        assert "no-store" not in response.headers.get("cache-control", "")


class TestTheHandlerBuiltResponsesCarryItToo:
    """A handler returning its own JSONResponse never touches the injected
    ``response``, so those branches need the headers passed explicitly.

    Found by mutation: deleting ``headers=_no_store_headers()`` from the AMM
    chain-disconnected 503 left the whole file green, because in a hermetic run
    ``is_connected()`` raises and the route exits through the outer handler
    instead. The branch was reachable in production and untested here — exactly
    the shape where a cacheable 503 would pin a transient RPC blip in front of
    every viewer at an edge. Mocked at the chain-client boundary, per the repo
    convention of mocking boundaries rather than internals.
    """

    async def test_the_chain_disconnected_503_is_uncacheable(self, monkeypatch):
        """MUTATION: drop `headers=_no_store_headers()` from that JSONResponse."""
        from unittest.mock import AsyncMock

        from archimedes.chain.client import chain_client
        from archimedes.main import app

        monkeypatch.setattr(chain_client, "is_connected", AsyncMock(return_value=False))

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/health/amm")

        assert response.status_code == 503
        assert response.json()["status"] == "chain_disconnected"
        assert "no-store" in response.headers.get("cache-control", "")

    async def test_the_amm_failure_503_is_uncacheable(self, monkeypatch):
        """MUTATION: drop the headers from the outer exception JSONResponse."""
        from unittest.mock import AsyncMock

        from archimedes.chain.client import chain_client
        from archimedes.main import app

        monkeypatch.setattr(chain_client, "is_connected", AsyncMock(side_effect=RuntimeError("boom")))

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/health/amm")

        assert response.status_code == 503
        assert "no-store" in response.headers.get("cache-control", "")
