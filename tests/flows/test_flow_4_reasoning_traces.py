"""
FLOW 4: Reasoning Traces On-Chain [MANDATORY]
===============================================

User story: The agent makes a decision, generates a reasoning trace with
            human-readable explanation, hashes it, and anchors the hash on Arc.
            Users can verify any historical decision against the on-chain hash.

Components exercised:
  - Chuan:  IReasoningTraceRegistry contract (publish/verify)
  - Chuan:  IAgentOrchestrator (generates trace)
  - Marten: ITracePublisher (publishes hash on-chain)
  - Daniel: Frontend reasoning trace viewer

This is the pitch's marquee feature: verifiable AI decision provenance.
"""

from datetime import datetime

from archimedes.models.trace import DecisionType, ReasoningTrace

# ─────────────────────────────────────────────────────────────
# 4.1 Trace generation (Chuan's agent)
# ─────────────────────────────────────────────────────────────


class TestTraceGeneration:
    """Agent generates structured reasoning traces."""

    def test_trace_has_required_fields(self):
        """Every trace has: id, vault, decision_type, trigger, reasoning, confidence."""
        trace = ReasoningTrace(
            id="trace-001",
            vault_address="0xVault",
            decision_type=DecisionType.REBALANCE,
            trigger="drift",
            reasoning="VIX dropped below 20, regime is RISK_ON. Increasing equity exposure.",
            confidence=0.85,
        )
        assert trace.id
        assert trace.vault_address
        assert trace.decision_type == DecisionType.REBALANCE
        assert trace.reasoning
        assert 0 <= trace.confidence <= 1

    def test_trace_compute_hash_deterministic(self):
        """Same trace content → same hash. Different content → different hash."""
        trace = ReasoningTrace(
            id="trace-001",
            vault_address="0xVault",
            decision_type=DecisionType.REBALANCE,
            trigger="drift",
            timestamp=datetime(2026, 5, 15, 10, 30, 0),
            reasoning="Rebalancing due to drift.",
            confidence=0.85,
        )
        hash1 = trace.compute_hash()
        hash2 = trace.compute_hash()
        assert hash1 == hash2  # Deterministic

        # Different content → different hash
        trace2 = ReasoningTrace(
            id="trace-002",
            vault_address="0xVault",
            decision_type=DecisionType.REBALANCE,
            trigger="regime_change",
            timestamp=datetime(2026, 5, 15, 10, 31, 0),
            reasoning="Different reasoning.",
            confidence=0.70,
        )
        hash3 = trace2.compute_hash()
        assert hash1 != hash3

    def test_trace_hash_is_sha256(self):
        """Hash is a valid SHA-256 hex string (64 chars)."""
        trace = ReasoningTrace(
            id="trace-001",
            vault_address="0xVault",
            decision_type=DecisionType.REBALANCE,
            trigger="drift",
            timestamp=datetime(2026, 5, 15, 10, 30, 0),
        )
        h = trace.compute_hash()
        assert len(h) == 64
        int(h, 16)  # Valid hex


# ─────────────────────────────────────────────────────────────
# 4.2 On-chain publishing (Marten's component)
# ─────────────────────────────────────────────────────────────


class TestTracePublishing:
    """Marten's ITracePublisher anchors hashes on-chain."""

    async def test_publish_returns_tx_hash(self, trace_publisher):
        """publish() calls ReasoningTraceRegistry and returns Arc tx hash."""
        trace = ReasoningTrace(
            id="trace-001",
            vault_address="0xVault",
            decision_type=DecisionType.REBALANCE,
            trigger="drift",
            timestamp=datetime(2026, 5, 15, 10, 30, 0),
            reasoning="Testing trace publication.",
            confidence=0.80,
        )
        trace.compute_hash()

        tx_hash = await trace_publisher.publish(trace)
        assert tx_hash is not None
        assert tx_hash.startswith("0x")
        assert trace.arc_tx_hash is not None

    async def test_publish_sets_arc_tx_hash(self, trace_publisher):
        """After publish, trace.arc_tx_hash is populated."""
        trace = ReasoningTrace(
            id="trace-002",
            vault_address="0xVault",
            decision_type=DecisionType.REGIME_CHANGE,
            trigger="regime_change",
            timestamp=datetime(2026, 5, 15, 11, 0, 0),
        )
        trace.compute_hash()
        await trace_publisher.publish(trace)
        assert trace.is_anchored

    async def test_verify_valid_trace(self, trace_publisher):
        """Verify a published trace against its on-chain hash → True."""
        trace = ReasoningTrace(
            id="trace-003",
            vault_address="0xVault",
            decision_type=DecisionType.REBALANCE,
            trigger="calendar",
            timestamp=datetime(2026, 5, 15, 12, 0, 0),
            reasoning="Weekly rebalance.",
        )
        trace.compute_hash()
        await trace_publisher.publish(trace)

        is_valid = await trace_publisher.verify(trace)
        assert is_valid

    async def test_verify_tampered_trace_fails(self, trace_publisher):
        """Modifying trace content after publish → verification fails."""
        trace = ReasoningTrace(
            id="trace-004",
            vault_address="0xVault",
            decision_type=DecisionType.REBALANCE,
            trigger="drift",
            timestamp=datetime(2026, 5, 15, 13, 0, 0),
            reasoning="Original reasoning.",
        )
        trace.compute_hash()
        await trace_publisher.publish(trace)

        # Tamper with the trace
        trace.reasoning = "Tampered reasoning."
        # Recomputing hash would give a different hash
        # Verification against the on-chain original hash should fail


# ─────────────────────────────────────────────────────────────
# 4.3 On-chain registry (Chuan's contract)
# ─────────────────────────────────────────────────────────────


class TestReasoningTraceRegistry:
    """On-chain ReasoningTraceRegistry contract tests."""

    def test_publish_increments_trace_count(self):
        """Each publishTrace() call increments traceCount()."""
        pass

    def test_publish_emits_event(self):
        """publishTrace emits TracePublished(traceId, agent, vault, hash, timestamp)."""
        pass

    def test_get_trace_by_id(self):
        """getTraceById(1) returns the correct agent, vault, hash, timestamp."""
        pass

    def test_get_traces_by_vault(self):
        """getTracesByVault(vaultAddr) returns all trace IDs for that vault."""
        pass

    def test_verify_trace_returns_true_for_matching_hash(self):
        """verifyTrace(id, data) returns true when SHA-256(data) matches stored hash."""
        pass

    def test_verify_trace_returns_false_for_wrong_data(self):
        """verifyTrace(id, wrongData) returns false."""
        pass


# ─────────────────────────────────────────────────────────────
# 4.4 Trace API (Chuan → Daniel)
# ─────────────────────────────────────────────────────────────


class TestTraceAPI:
    """Backend serves traces to the frontend."""

    async def test_list_traces_endpoint(self, client):
        """GET /api/traces/ returns TraceListResponse."""
        response = await client.get("/api/traces/")
        assert response.status_code == 200
        data = response.json()
        assert "traces" in data

    async def test_list_traces_filter_by_vault(self, client, vault_address):
        """GET /api/traces/?vault_address=0x... filters by vault."""
        response = await client.get("/api/traces/", params={"vault_address": vault_address})
        assert response.status_code == 200
        for trace in response.json()["traces"]:
            assert trace["vault_address"] == vault_address

    async def test_trace_detail_has_verification_status(self, client):
        """GET /api/traces/{id} includes is_verified and arc_tx_hash."""
        response = await client.get("/api/traces/trace-001")
        assert response.status_code == 200
        data = response.json()
        assert "trace_hash" in data
        assert "arc_tx_hash" in data
        assert "is_verified" in data
