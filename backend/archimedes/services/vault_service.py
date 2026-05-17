"""Vault service — composes chain executor data into API responses."""

from __future__ import annotations

from datetime import datetime, timezone

from archimedes.chain.executor import chain_executor
from archimedes.chain.trace_publisher import trace_publisher
from archimedes.api.schemas import (
    VaultSummaryResponse,
    VaultDetailResponse,
    VaultHolding,
    VaultListResponse,
    TraceResponse,
)


class VaultService:
    """Serves vault data to the API layer."""

    async def list_vaults(
        self,
        tier: int | None = None,
        sort_by: str = "aum",
        order: str = "desc",
        limit: int = 20,
        offset: int = 0,
    ) -> VaultListResponse:
        """List all vaults with summary data."""
        try:
            vault_addresses = await chain_executor.get_all_vaults()
        except Exception as e:
            import logging
            logging.getLogger(__name__).error(f"Failed to get vault addresses: {e}")
            return VaultListResponse(vaults=[], total=0)

        summaries: list[VaultSummaryResponse] = []

        for addr in vault_addresses:
            try:
                metrics = await chain_executor.get_vault_metrics(addr)
                summary = self._metrics_to_summary(metrics)
                if tier is not None and summary.tier != tier:
                    continue
                summaries.append(summary)
            except Exception as e:
                import logging
                logging.getLogger(__name__).warning(f"Skipping vault {addr}: {e}")
                continue

        # Sort
        sort_key = sort_by if sort_by != "return_inception" else "return_inception"
        summaries.sort(
            key=lambda v: getattr(v, sort_key, 0) or 0,
            reverse=(order == "desc"),
        )

        # Paginate
        total = len(summaries)
        summaries = summaries[offset : offset + limit]

        return VaultListResponse(vaults=summaries, total=total)

    async def get_vault_detail(self, address: str) -> VaultDetailResponse | None:
        """Get full vault detail."""
        try:
            metrics = await chain_executor.get_vault_metrics(address)
            portfolio = await chain_executor.read_portfolio(address)

            # Build holdings
            holdings = [
                VaultHolding(
                    symbol=h.symbol,
                    token_address=h.token_address,
                    amount=h.amount,
                    value_usdc=h.value_usdc,
                    weight_pct=h.weight * 100,
                )
                for h in portfolio.holdings
            ]

            # Get recent traces
            trace_count = await trace_publisher.get_trace_count(address)
            recent_traces = await self._get_recent_traces(address, limit=5)

            # Resolve name/symbol from off-chain metadata
            name, symbol = await self._get_vault_names(address)

            return VaultDetailResponse(
                address=address,
                name=name or f"Vault {metrics['tier']}",
                symbol=symbol or f"v{address[:6]}",
                tier=metrics["tier"],
                creator=metrics["creator"],
                aum_usdc=metrics["total_aum_usdc"],
                share_price=metrics["share_price_usdc"],
                is_agent_assisted=metrics["is_agent_assisted"],
                management_fee_pct=metrics["management_fee_bps"] / 100,
                performance_fee_pct=metrics["performance_fee_bps"] / 100,
                high_water_mark=metrics["high_water_mark"],
                holdings=holdings,
                target_allocations=[],  # Would need to parse from contract
                return_24h=0.0,
                return_7d=0.0,
                return_30d=0.0,
                return_inception=0.0,
                recent_traces=recent_traces,
            )
        except Exception:
            return None

    def _metrics_to_summary(self, metrics: dict) -> VaultSummaryResponse:
        return VaultSummaryResponse(
            address=metrics["vault_address"],
            name=f"Vault T{metrics['tier']}",
            symbol=f"v{metrics['vault_address'][:6]}",
            tier=metrics["tier"],
            creator=metrics["creator"],
            aum_usdc=metrics["total_aum_usdc"],
            share_price=metrics["share_price_usdc"],
            return_24h=0.0,
            return_7d=0.0,
            return_30d=0.0,
            return_inception=0.0,
            management_fee_pct=metrics["management_fee_bps"] / 100,
            performance_fee_pct=metrics["performance_fee_bps"] / 100,
            is_agent_assisted=metrics["is_agent_assisted"],
            depositors=0,
            created_at=datetime.now(timezone.utc).isoformat(),
        )

    async def _get_vault_names(self, address: str) -> tuple[str | None, str | None]:
        """Resolve vault display name and symbol from off-chain metadata."""
        try:
            from archimedes.db import get_session
            from archimedes.models.chat import VaultMetadata

            session = get_session()
            try:
                meta = (
                    session.query(VaultMetadata)
                    .filter(VaultMetadata.vault_address == address)
                    .first()
                )
                if meta:
                    return meta.name, meta.symbol
            finally:
                session.close()
        except Exception:
            pass
        return None, None

    async def _get_recent_traces(self, vault_address: str, limit: int = 5) -> list[TraceResponse]:
        """Get recent reasoning traces for a vault (from on-chain)."""
        traces: list[TraceResponse] = []
        try:
            trace_ids = await trace_publisher.loader.trace_registry.functions.getTracesByVault(
                vault_address
            ).call()

            for trace_id in reversed(trace_ids[-limit:]):
                detail = await trace_publisher.get_trace_by_id(trace_id)
                if detail:
                    traces.append(
                        TraceResponse(
                            id=str(trace_id),
                            vault_address=vault_address,
                            decision_type="rebalance",  # Default
                            trigger="unknown",
                            timestamp=datetime.fromtimestamp(
                                detail["timestamp"], tz=timezone.utc
                            ).isoformat(),
                            reasoning="On-chain trace",
                            confidence=0.0,
                            trace_hash=detail["trace_hash"],
                            arc_tx_hash=None,
                            is_verified=True,
                        )
                    )
        except Exception:
            pass
        return traces
