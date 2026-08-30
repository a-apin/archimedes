"""The display routes stop claiming a verification they never performed (#1407).

`GET /api/traces/` and `GET /api/traces/{id}` both fall back to an on-chain-only
projection when the off-chain store has no record for a trace — or is
unreachable, which that path does not distinguish. Both hard-coded
`is_verified=True` with `arc_tx_hash` left `None` and **nothing compared
anywhere**: no hash re-derivation, no receipt check beyond "an object exists at
this id". `Reasoning.jsx` rendered that boolean as a green check reading
*"verified"*, so the flagship provenance page asserted a verification on traces
where zero hashes had been looked at.

#1356 fixed `decision_type` and `total` on this exact path; its Evidence E.2
listed `is_verified` as a third fabrication on the same path, and PR #1394
scoped it out.

**What was fixed is the claim, not the boolean.** `is_verified` stays true here,
because the anchor genuinely is confirmed — we read it out of the registry.
Flipping it false would be a different fabrication: `Portfolio.jsx` renders the
false branch as *"anchor pending — registry write didn't complete yet"*, an
invented denial of the one thing this path is certain about. What was never true
is the implication that a hash was *compared*, and `verification_mode` now
carries that, reusing #1359's vocabulary so the display routes and the verify
route cannot invent two different words for one state.

Hermetic: `AgentStateStore` and the chain `trace_publisher` are mocked at the
boundary, same as `test_traces_verify_modes.py`.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


_ON_CHAIN = {"vault": "0xVault", "trace_hash": "0xaabbccdd", "timestamp": 1_700_000_000, "agent": "0xAgent"}

#: The two ways the off-chain store fails to answer. The route treats them
#: identically on purpose — from here they are the same fact, "nothing was
#: compared" — and both must produce the same honest label.
STORE_SILENT = {
    "empty": AsyncMock(return_value=([], 0)),
    "unreachable": AsyncMock(side_effect=ConnectionError("redis down")),
}
STORE_SILENT_DETAIL = {
    "empty": AsyncMock(return_value=None),
    "unreachable": AsyncMock(side_effect=ConnectionError("redis down")),
}


async def _list_with(list_traces_mock):
    from archimedes.main import app
    from archimedes.services.redis_state import AgentStateStore

    with (
        patch.object(AgentStateStore, "list_traces", list_traces_mock),
        patch.object(AgentStateStore, "close", AsyncMock()),
        patch("archimedes.chain.trace_publisher.trace_publisher") as pub,
    ):
        pub.get_total_trace_count = AsyncMock(return_value=1)
        pub.get_trace_by_id = AsyncMock(return_value=dict(_ON_CHAIN))
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            return await client.get("/api/traces/?limit=10")


async def _detail_with(get_trace_mock):
    from archimedes.main import app
    from archimedes.services.redis_state import AgentStateStore

    with (
        patch.object(AgentStateStore, "get_trace", get_trace_mock),
        patch.object(AgentStateStore, "close", AsyncMock()),
        patch("archimedes.chain.trace_publisher.trace_publisher") as pub,
        patch("archimedes.api.traces_routes.trace_publisher", create=True),
    ):
        pub.get_trace_by_id = AsyncMock(return_value=dict(_ON_CHAIN))
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            return await client.get("/api/traces/1")


@pytest.mark.parametrize("store_state", ["empty", "unreachable"])
async def test_list_fallback_reports_anchored_only_not_a_verification(store_state):
    resp = await _list_with(STORE_SILENT[store_state])
    assert resp.status_code == 200
    trace = resp.json()["traces"][0]

    # The claim under test: not a bare verified-with-nothing-compared.
    assert trace["verification_mode"] == "anchored_only"
    # And the absent reference stays absent rather than being invented.
    assert trace["arc_tx_hash"] is None


@pytest.mark.parametrize("store_state", ["empty", "unreachable"])
async def test_detail_fallback_reports_anchored_only_not_a_verification(store_state):
    resp = await _detail_with(STORE_SILENT_DETAIL[store_state])
    assert resp.status_code == 200
    trace = resp.json()

    assert trace["verification_mode"] == "anchored_only"
    assert trace["arc_tx_hash"] is None


async def test_a_trace_is_never_verified_with_nothing_compared_and_no_tx_hash():
    """The issue's own wording, asserted directly.

    Kept separate from the mode assertions because it is the invariant that
    matters even if the vocabulary is renamed later: a response must not
    simultaneously claim a plain verification and carry no evidence of one.
    """
    for resp in (await _list_with(STORE_SILENT["unreachable"]), await _detail_with(STORE_SILENT_DETAIL["unreachable"])):
        body = resp.json()
        trace = body["traces"][0] if "traces" in body else body
        unqualified_claim = trace["is_verified"] and trace.get("verification_mode") is None
        assert not (unqualified_claim and trace["arc_tx_hash"] is None), (
            "trace claims is_verified with no verification_mode and no arc_tx_hash — "
            "nothing on this path compares anything, so that is a fabricated verification"
        )


async def test_the_off_chain_path_makes_no_verification_claim_at_all():
    """When the store DOES answer, the route replays what was stored and
    compares nothing itself — so it reports no mode rather than borrowing one."""
    from archimedes.main import app
    from archimedes.services.redis_state import AgentStateStore

    stored = {
        "id": "trace-1",
        "vault_address": "0xVault",
        "trace_hash": "0xaabbccdd",
        "arc_tx_hash": "0xTxHash",
        "is_verified": True,
    }
    with (
        patch.object(AgentStateStore, "list_traces", AsyncMock(return_value=([stored], 1))),
        patch.object(AgentStateStore, "close", AsyncMock()),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/traces/?limit=10")

    trace = resp.json()["traces"][0]
    assert trace["verification_mode"] is None
    assert trace["is_verified"] is True  # replayed from the store, not invented here


def test_display_and_verify_routes_share_one_vocabulary():
    """The two surfaces must not grow separate words for the same state (#1359).

    The issue asked for exactly this alignment. Comparing the annotations keeps
    a later edit to one schema from silently forking the vocabulary.
    """
    import typing

    from archimedes.api.schemas import TraceResponse, TraceVerifyResponse

    display = TraceResponse.model_fields["verification_mode"].annotation
    verify = TraceVerifyResponse.model_fields["verification_mode"].annotation

    display_values = set(typing.get_args(typing.get_args(display)[0]))
    verify_values = set(typing.get_args(verify))
    assert display_values == verify_values, (
        f"display route says {sorted(display_values)}, verify route says {sorted(verify_values)} — "
        "two vocabularies for one concept is how the surfaces drift apart"
    )
    assert "anchored_only" in display_values
