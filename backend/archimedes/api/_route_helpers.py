"""Shared helpers used by multiple per-resource routers.

Service singletons and mapping utilities that are imported by two or more
route modules live here so that each router stays self-contained.
"""

from __future__ import annotations

import logging

from archimedes.agents.strategy_architect import default_architect
from archimedes.chain.oracle_updater import OracleUpdater
from archimedes.services.asset_service import AssetService
from archimedes.services.config_service import ConfigService
from archimedes.services.strategy_provider import default_provider
from archimedes.services.vault_service import VaultService

_logger = logging.getLogger(__name__)

# ── Service singletons ─────────────────────────────────────────
asset_svc = AssetService()
vault_svc = VaultService()
config_svc = ConfigService()
oracle = OracleUpdater()
strategy_provider = default_provider()
architect = default_architect()


async def persist_trace_off_chain(trace) -> None:
    """Save a ReasoningTrace to Redis so it appears in /api/traces feed.

    Non-fatal -- failures are logged but don't break the caller.
    """
    try:
        from archimedes.services.redis_state import AgentStateStore

        state = AgentStateStore()
        try:
            await state.save_trace(
                {
                    "id": trace.id,
                    "vault_address": getattr(trace, "vault_address", "") or "",
                    "decision_type": (
                        trace.decision_type.value if hasattr(trace.decision_type, "value") else str(trace.decision_type)
                    ),
                    "trigger": getattr(trace, "trigger", "") or "",
                    "timestamp": (
                        trace.timestamp.isoformat() if hasattr(trace.timestamp, "isoformat") else str(trace.timestamp)
                    ),
                    "market_context": getattr(trace, "market_context", {}) or {},
                    "portfolio_before": getattr(trace, "portfolio_before", {}) or {},
                    "portfolio_after": getattr(trace, "portfolio_after", {}) or {},
                    "reasoning": getattr(trace, "reasoning", "") or "",
                    "confidence": getattr(trace, "confidence", 0.0) or 0.0,
                    "trades_executed": getattr(trace, "trades_executed", []) or [],
                    "strategies_referenced": getattr(trace, "strategies_referenced", []) or [],
                    "trace_hash": getattr(trace, "trace_hash", "") or "",
                    "arc_tx_hash": getattr(trace, "arc_tx_hash", None),
                    "is_verified": bool(getattr(trace, "arc_tx_hash", None)),
                }
            )
        finally:
            await state.close()
    except Exception as exc:
        _logger.warning("trace persistence failed (non-fatal): %s", exc)
