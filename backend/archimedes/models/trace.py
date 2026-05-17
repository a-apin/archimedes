"""Reasoning trace data models — design.md § 4.4."""

from __future__ import annotations

import json
from web3 import Web3
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class DecisionType(str, Enum):
    """Type of agent decision that produced the trace."""

    PORTFOLIO_CONSTRUCTION = "construction"  # Initial portfolio build
    REBALANCE = "rebalance"  # Rebalance execution
    STRATEGY_ROTATION = "rotation"  # Strategy swapped in/out
    REGIME_CHANGE = "regime_change"  # Regime classification changed
    SKIP = "skip"  # Agent evaluated but decided not to act


@dataclass
class ReasoningTrace:
    """A structured record of an agent decision.

    Every agent action produces one of these. The hash is anchored on-chain
    via ReasoningTraceRegistry; the full trace is stored off-chain in Postgres.

    Produced by: Chuan (agent orchestrator generates after each decision)
    Consumed by: Marten (publishes hash to ReasoningTraceRegistry contract),
                 Daniel (reasoning trace viewer in UI),
                 Dan (traces reference strategies for provenance)
    """

    id: str  # UUID
    vault_address: str  # Which vault this decision applies to
    decision_type: DecisionType
    trigger: str  # What caused this decision (same as RebalanceDecision.trigger)
    timestamp: datetime = field(default_factory=datetime.utcnow)

    # Context at decision time
    market_context: dict = field(default_factory=dict)  # Regime, key prices, VIX, etc.
    portfolio_before: dict = field(default_factory=dict)  # Holdings before action
    portfolio_after: dict = field(default_factory=dict)  # Holdings after action

    # The reasoning
    reasoning: str = ""  # LLM-generated explanation of the decision
    confidence: float = 0.0  # 0-1, agent's confidence in the decision
    expected_outcome: str = ""  # What the agent expects to happen

    # Actions taken
    trades_executed: list[dict] = field(default_factory=list)  # Serialized TradeOrders
    strategies_referenced: list[str] = field(default_factory=list)  # Strategy IDs

    # On-chain anchoring
    trace_hash: str = ""  # keccak256 of the canonical trace content
    arc_tx_hash: str | None = None  # Arc transaction that recorded this hash

    # Commit-reveal temporal binding (v1.5)
    commit_tx_hash: str | None = None  # Tx that committed the hash BEFORE the trade
    commit_block_number: int | None = None  # Block number of commit tx
    reveal_tx_hash: str | None = None  # Tx that revealed full content AFTER trade
    reveal_block_number: int | None = None  # Block number of reveal tx
    trade_tx_hash: str | None = None  # The actual rebalance trade tx hash
    trade_block_number: int | None = None  # Block number of trade tx

    # Canonical field order for hash computation — must match contract's verifyTrace
    _HASH_FIELDS = (
        "id", "vault_address", "decision_type", "trigger", "timestamp",
        "market_context", "portfolio_before", "portfolio_after",
        "reasoning", "confidence", "trades_executed", "strategies_referenced",
    )

    def canonical_json(self) -> str:
        """Return the deterministic JSON string used for hashing."""
        data = {}
        for k in self._HASH_FIELDS:
            v = self.decision_type.value if k == "decision_type" else getattr(self, k, None)
            # Serialize datetime to ISO string
            if isinstance(v, datetime):
                v = v.isoformat()
            data[k] = v
        return json.dumps(data, sort_keys=True, separators=(",", ":"))

    def compute_hash(self) -> str:
        """Compute deterministic keccak256 hash of the trace content.

        This hash is what gets published to ReasoningTraceRegistry on-chain.
        Anyone can verify by calling contract.verifyTrace(id, canonicalBytes)
        which recomputes keccak256 on-chain and compares.
        """
        canonical = self.canonical_json()
        self.trace_hash = Web3.keccak(text=canonical).hex()
        return self.trace_hash

    @property
    def is_anchored(self) -> bool:
        """Whether this trace has been published on-chain."""
        return self.arc_tx_hash is not None
