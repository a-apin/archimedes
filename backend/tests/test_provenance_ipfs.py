"""T0.3 + T1.4 — commit/reveal anchoring + Pinata IPFS CID provenance.

Hermetic: the Pinata HTTP boundary (aiohttp.ClientSession) and the contract
loader are mocked — no network, no Arc RPC, no real Pinata. Mirrors the
boundary-mock convention in tests/services/test_circle_service.py.

Coverage:
  - pin → CID → fetch → verify round-trips
  - missing PINATA_JWT degrades LOUDLY (structured WARN), returns None, never raises
  - the public provenance layer EXCLUDES the executable layer (weights/code)
  - TracePublisher.commit/reveal call the real contract fns and bind the same bytes
  - supports_commit_reveal gates the publishTrace fallback (pre-#588 redeploy)
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from archimedes.chain.pinata_client import (
    PinataClient,
    build_public_provenance,
    canonical_bytes,
)
from archimedes.models.trace import DecisionType, ReasoningTrace

# ── aiohttp boundary mocks (same shape as test_circle_service) ──────────────


class _MockResp:
    """aiohttp-like response for `async with session.post(...)` / `.get(...)`."""

    def __init__(self, *, status: int, body: dict) -> None:
        self.status = status
        self._body = body

    async def json(self) -> dict:
        return self._body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False


class _MockSession:
    """aiohttp-like session; `post`/`get` return the canned response (or raise)."""

    def __init__(self, response: _MockResp | Exception) -> None:
        self._response = response
        self.last_url: str | None = None

    def post(self, url, *_args, **_kwargs):
        self.last_url = url
        if isinstance(self._response, Exception):
            raise self._response
        return self._response

    def get(self, url, *_args, **_kwargs):
        self.last_url = url
        if isinstance(self._response, Exception):
            raise self._response
        return self._response

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False


def _patch_session(session: _MockSession):
    return patch("archimedes.chain.pinata_client.aiohttp.ClientSession", return_value=session)


def _make_trace(**overrides) -> ReasoningTrace:
    defaults = {
        "id": "trace-prov-001",
        "vault_address": "0x1234567890abcdef1234567890abcdef12345678",
        "decision_type": DecisionType.REBALANCE,
        "trigger": "strategy_signal_drift",
        "timestamp": datetime(2026, 6, 24, 12, 0, 0, tzinfo=UTC),
        "reasoning": "Momentum + vol-target ensemble; rotate into sBTC",
        "confidence": 0.82,
        "strategies_referenced": ["strat-momentum-12m", "strat-voltarget"],
        "consulted_paper_hashes": ["2301.00001:abc", "2302.00002:def"],
    }
    defaults.update(overrides)
    return ReasoningTrace(**defaults)


# ── Public/executable split ─────────────────────────────────────────────────


class TestPublicProvenanceSplit:
    def test_public_layer_includes_claims_excludes_executable(self):
        payload = build_public_provenance(
            trace_id="t1",
            vault_address="0xvault",
            decision_type="rebalance",
            timestamp_iso="2026-06-24T12:00:00+00:00",
            papers_cited=["arxiv:2301.00001"],
            methodology="SMA200 cross + vol target",
            rigor={"deflated_sharpe_ratio": 1.4, "pbo_score": 0.12, "passes_rigor_gate": True},
            reasoning="rotate into sBTC",
            consulted_paper_hashes=["2301.00001:abc"],
        )
        # Publishable claims present
        assert payload["papers_cited"] == ["arxiv:2301.00001"]
        assert payload["rigor"]["deflated_sharpe_ratio"] == 1.4
        assert payload["rigor"]["pbo_score"] == 0.12
        assert payload["methodology"] == "SMA200 cross + vol target"
        # Executable layer must NOT leak — assert no alpha keys anywhere in the JSON.
        blob = json.dumps(payload).lower()
        for forbidden in ("weight", "target_alloc", "position_sizing", "code_hash", "code_path", "private"):
            assert forbidden not in blob, f"executable layer leaked: {forbidden}"

    def test_canonical_bytes_stable_under_key_order(self):
        a = {"b": 2, "a": 1}
        b = {"a": 1, "b": 2}
        assert canonical_bytes(a) == canonical_bytes(b)


# ── Pinata pin → CID ─────────────────────────────────────────────────────────


class TestPinataPin:
    @pytest.mark.asyncio
    async def test_pin_returns_cid(self):
        client = PinataClient(jwt="test-jwt")
        session = _MockSession(_MockResp(status=200, body={"IpfsHash": "QmCID123"}))
        with _patch_session(session):
            cid = await client.pin_json({"hello": "world"}, name="x")
        assert cid == "QmCID123"
        assert session.last_url.endswith("/pinning/pinJSONToIPFS")

    @pytest.mark.asyncio
    async def test_pin_non_200_returns_none(self):
        client = PinataClient(jwt="test-jwt")
        session = _MockSession(_MockResp(status=401, body={"error": "bad jwt"}))
        with _patch_session(session):
            cid = await client.pin_json({"hello": "world"})
        assert cid is None

    @pytest.mark.asyncio
    async def test_pin_network_error_returns_none(self):
        client = PinataClient(jwt="test-jwt")
        session = _MockSession(RuntimeError("network down"))
        with _patch_session(session):
            cid = await client.pin_json({"hello": "world"})
        assert cid is None


# ── No-silent-degradation (T0.5) — missing PINATA_JWT ────────────────────────


class TestMissingJwtDegradesLoudly:
    @pytest.mark.asyncio
    async def test_missing_jwt_returns_none_and_warns(self, caplog):
        client = PinataClient(jwt="")  # explicitly unconfigured
        assert client.is_configured is False
        with caplog.at_level(logging.WARNING, logger="archimedes.chain.pinata_client"):
            cid = await client.pin_json({"x": 1})
        assert cid is None
        warns = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert warns, "missing PINATA_JWT must emit a WARN — no silent degradation"
        # Structured marker so log-based alerting can fire on it.
        assert any("event=pinata_degraded" in r.getMessage() for r in warns)
        assert any("missing_PINATA_JWT" in r.getMessage() for r in warns)

    @pytest.mark.asyncio
    async def test_missing_jwt_never_raises(self):
        client = PinataClient(jwt="")
        # Must not raise even with no network mock in place.
        assert await client.pin_json({"x": 1}) is None


# ── Fetch + verify round-trip ────────────────────────────────────────────────


class TestVerifyRoundTrip:
    @pytest.mark.asyncio
    async def test_pin_fetch_verify_round_trip(self):
        client = PinataClient(jwt="test-jwt")
        payload = {"schema": "v1", "rigor": {"pbo_score": 0.1}}

        # pin
        with _patch_session(_MockSession(_MockResp(status=200, body={"IpfsHash": "QmABC"}))):
            cid = await client.pin_json(payload)
        assert cid == "QmABC"

        # fetch returns the SAME payload → verify True
        with _patch_session(_MockSession(_MockResp(status=200, body=payload))):
            assert await client.verify(cid, payload) is True

    @pytest.mark.asyncio
    async def test_verify_detects_drift(self):
        client = PinataClient(jwt="test-jwt")
        expected = {"rigor": {"pbo_score": 0.1}}
        drifted = {"rigor": {"pbo_score": 0.9}}  # gateway content drifted
        with _patch_session(_MockSession(_MockResp(status=200, body=drifted))):
            assert await client.verify("QmABC", expected) is False

    @pytest.mark.asyncio
    async def test_verify_unfetchable_cid_is_false(self):
        client = PinataClient(jwt="test-jwt")
        with _patch_session(_MockSession(_MockResp(status=404, body={}))):
            assert await client.verify("QmMissing", {"a": 1}) is False


# ── provenance_publisher glue: pin → CID → verify ────────────────────────────


class TestProvenancePublisher:
    @pytest.mark.asyncio
    async def test_pin_public_provenance_returns_cid_and_payload(self):
        from archimedes.chain.provenance_publisher import pin_public_provenance

        trace = _make_trace()
        client = PinataClient(jwt="test-jwt")
        with _patch_session(_MockSession(_MockResp(status=200, body={"IpfsHash": "QmProv"}))):
            cid, payload = await pin_public_provenance(trace, rigor={"pbo_score": 0.2}, client=client)
        assert cid == "QmProv"
        assert payload["trace_id"] == "trace-prov-001"
        assert payload["rigor"]["pbo_score"] == 0.2
        # consulted hashes carried through for source-tracking
        assert payload["consulted_paper_hashes"] == ["2301.00001:abc", "2302.00002:def"]

    @pytest.mark.asyncio
    async def test_pin_then_verify_round_trips_for_trace(self):
        from archimedes.chain.provenance_publisher import pin_public_provenance, verify_provenance

        trace = _make_trace()
        client = PinataClient(jwt="test-jwt")
        with _patch_session(_MockSession(_MockResp(status=200, body={"IpfsHash": "QmProv"}))):
            cid, payload = await pin_public_provenance(trace, client=client)
        # gateway returns the exact pinned payload
        with _patch_session(_MockSession(_MockResp(status=200, body=payload))):
            assert await verify_provenance(cid, trace, client=client) is True

    @pytest.mark.asyncio
    async def test_missing_jwt_glue_returns_none_cid_but_payload(self, caplog):
        from archimedes.chain.provenance_publisher import pin_public_provenance

        trace = _make_trace()
        client = PinataClient(jwt="")
        with caplog.at_level(logging.WARNING):
            cid, payload = await pin_public_provenance(trace, client=client)
        assert cid is None  # loudly degraded
        assert payload["trace_id"] == "trace-prov-001"  # still built for off-chain persist
        assert any("provenance_ipfs_skipped" in r.getMessage() for r in caplog.records)


# ── TracePublisher.commit / reveal (mocked contract) ─────────────────────────


class TestCommitReveal:
    def _publisher_with_v15_abi(self):
        """A TracePublisher whose mocked registry exposes commit()+reveal()."""
        from archimedes.chain.trace_publisher import TracePublisher

        registry = MagicMock()
        # supports_commit_reveal() probes hasattr(functions, 'commit'/'reveal')
        registry.functions.commit = MagicMock()
        registry.functions.reveal = MagicMock()
        loader = MagicMock()
        loader.trace_registry = registry
        return TracePublisher(loader=loader), registry

    def test_supports_commit_reveal_true_when_abi_has_fns(self):
        publisher, _ = self._publisher_with_v15_abi()
        assert publisher.supports_commit_reveal() is True

    def test_supports_commit_reveal_false_on_v1_abi(self):
        from archimedes.chain.trace_publisher import TracePublisher

        registry = MagicMock(spec=["functions"])
        # functions has neither commit nor reveal
        registry.functions = MagicMock(spec=["publishTrace", "getTracesByVault"])
        loader = MagicMock()
        loader.trace_registry = registry
        publisher = TracePublisher(loader=loader)
        assert publisher.supports_commit_reveal() is False

    @pytest.mark.asyncio
    async def test_commit_via_circle_returns_trace_id(self):
        publisher, registry = self._publisher_with_v15_abi()
        trace = _make_trace()
        trace.compute_hash()

        # Receipt with a TraceCommitted log decoding to traceId=42
        fake_receipt = MagicMock()
        fake_receipt.blockNumber = 100
        fake_receipt.logs = [MagicMock()]
        registry.events.TraceCommitted.return_value.process_log = MagicMock(return_value={"args": {"traceId": 42}})

        with (
            patch("archimedes.chain.trace_publisher.circle_signer") as signer,
            patch("archimedes.chain.trace_publisher.chain_client") as client,
        ):
            signer.is_configured = True
            signer.execute_contract = AsyncMock(return_value="0xCOMMIT")
            client.to_checksum = lambda x: x
            client.settings = MagicMock(reasoning_trace_registry_address="0xreg")
            client.w3.eth.wait_for_transaction_receipt = AsyncMock(return_value=fake_receipt)

            trade_id = b"\xab" * 32
            trace_id, tx, block, reverted = await publisher.commit(
                trace, claimed_execution_time=9999999999, trade_id=trade_id
            )

        assert tx == "0xCOMMIT"
        assert trace_id == 42
        assert block == 100
        assert reverted is False
        assert trace.commit_tx_hash == "0xCOMMIT"
        # commit() called with the real (5-arg) signature + claimedExecutionTime + tradeId
        call = signer.execute_contract.await_args
        assert call.kwargs["abi_function"] == "commit(address,bytes32,uint64,bytes32,bytes)"
        assert call.kwargs["abi_params"][2] == "9999999999"
        assert call.kwargs["abi_params"][3] == "0x" + trade_id.hex()

    @pytest.mark.asyncio
    async def test_reveal_submits_same_canonical_bytes_and_cid(self):
        publisher, _registry = self._publisher_with_v15_abi()
        trace = _make_trace()
        trace.compute_hash()
        committed_hash = trace.trace_hash

        fake_receipt = MagicMock()
        fake_receipt.blockNumber = 105

        with (
            patch("archimedes.chain.trace_publisher.circle_signer") as signer,
            patch("archimedes.chain.trace_publisher.chain_client") as client,
        ):
            signer.is_configured = True
            signer.execute_contract = AsyncMock(return_value="0xREVEAL")
            client.to_checksum = lambda x: x
            client.settings = MagicMock(reasoning_trace_registry_address="0xreg")
            client.w3.eth.wait_for_transaction_receipt = AsyncMock(return_value=fake_receipt)

            tx, block = await publisher.reveal(42, trace, storage_pointer="QmCID")

        assert tx == "0xREVEAL"
        assert block == 105
        assert trace.reveal_tx_hash == "0xREVEAL"
        assert trace.arc_tx_hash == "0xREVEAL"
        call = signer.execute_contract.await_args
        assert call.kwargs["abi_function"] == "reveal(uint256,string,bytes)"
        params = call.kwargs["abi_params"]
        assert params[0] == "42"
        assert params[1] == "QmCID"  # IPFS CID anchored as storage pointer
        # The revealed bytes hash to the committed hash (on-chain binding holds).
        from web3 import Web3

        revealed_bytes = bytes.fromhex(params[2].removeprefix("0x"))
        assert Web3.keccak(revealed_bytes).hex().removeprefix("0x") == committed_hash.removeprefix("0x")

    @pytest.mark.asyncio
    async def test_commit_returns_none_when_abi_lacks_commit(self):
        from archimedes.chain.trace_publisher import TracePublisher

        registry = MagicMock()
        registry.functions = MagicMock(spec=["publishTrace"])  # no commit/reveal
        loader = MagicMock()
        loader.trace_registry = registry
        publisher = TracePublisher(loader=loader)

        trace = _make_trace()
        trace.compute_hash()
        trace_id, tx, block, reverted = await publisher.commit(
            trace, claimed_execution_time=9999999999, trade_id=b"\xcd" * 32
        )
        assert (trace_id, tx, block, reverted) == (None, None, None, False)
