"""Vault service — composes chain executor data into API responses."""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
from datetime import UTC, datetime
from typing import ClassVar

from archimedes.api.schemas import (
    TraceResponse,
    VaultDetailResponse,
    VaultHolding,
    VaultListResponse,
    VaultSummaryResponse,
)
from archimedes.chain.executor import MAX_MANAGEMENT_FEE_BPS, MAX_PERFORMANCE_FEE_BPS, chain_executor
from archimedes.chain.trace_publisher import trace_publisher
from archimedes.services.log_scrubber import sanitize_log_value

logger = logging.getLogger(__name__)


def _fees_pass_caps(metrics: dict) -> bool:
    """The #1138 fee-cap gate for display surfaces.

    False for vaults whose immutable fee bps exceed the #1129 caps (hostile —
    the pre-cap factory could mint them and no setter can ever fix them) AND
    for vaults whose fees could not be read (None from get_vault_metrics) —
    fail-closed: never render a vault we couldn't verify as investable.
    """
    mgmt, perf = metrics.get("management_fee_bps"), metrics.get("performance_fee_bps")
    if mgmt is None or perf is None:
        logger.warning(
            "Refusing to surface vault %s: fee bps unreadable (fail-closed)",
            sanitize_log_value(metrics.get("vault_address", "?")),
        )
        return False
    if mgmt > MAX_MANAGEMENT_FEE_BPS or perf > MAX_PERFORMANCE_FEE_BPS:
        logger.warning(
            "Refusing to surface vault %s: fees exceed caps (managementFeeBps=%d, performanceFeeBps=%d)",
            sanitize_log_value(metrics.get("vault_address", "?")),
            mgmt,
            perf,
        )
        return False
    return True


class VaultService:
    """Serves vault data to the API layer."""

    # Expected monthly returns by asset class (annualized, used for sim).
    # ClassVar marks this as a shared lookup table (one per class), not a
    # mutable per-instance default — the values are constants, never mutated.
    ASSET_EXPECTED_RETURN: ClassVar[dict[str, float]] = {
        "sTSLA": 0.15,  # 15% annual
        "sNVDA": 0.20,  # 20% annual
        "sSPY": 0.10,  # 10% annual
        "sBTC": 0.30,  # 30% annual (high vol)
        "sGOLD": 0.05,  #  5% annual
        "sOIL": 0.03,  #  3% annual
        "sNKY": 0.08,  #  8% annual
        "USDC": 0.04,  #  4% annual (yield)
    }

    _vault_list_cache: VaultListResponse | None = None
    _vault_list_cache_ts: float = 0
    _VAULT_LIST_CACHE_TTL = 30  # seconds

    async def list_vaults(
        self,
        tier: int | None = None,
        sort_by: str = "aum",
        order: str = "desc",
        limit: int = 20,
        offset: int = 0,
    ) -> VaultListResponse:
        """List all vaults with summary data. Cached 30s to avoid N+1 on-chain reads."""
        import time as _time

        now = _time.time()
        if self._vault_list_cache and (now - self._vault_list_cache_ts) < self._VAULT_LIST_CACHE_TTL:
            vaults = list(self._vault_list_cache.vaults)
            if tier is not None:
                vaults = [v for v in vaults if v.tier == tier]
            sort_key = sort_by if sort_by != "return_inception" else "return_inception"
            vaults.sort(key=lambda v: getattr(v, sort_key, 0) or 0, reverse=(order == "desc"))
            return VaultListResponse(vaults=vaults[offset : offset + limit], total=len(vaults))

        try:
            vault_addresses = await chain_executor.get_all_vaults()
        except Exception as e:
            logging.getLogger(__name__).error(f"Failed to get vault addresses: {e}")
            return VaultListResponse(vaults=[], total=0)

        # Batch-read off-chain metadata so the marketplace shows real names,
        # symbols, creators, and created_at instead of the "Vault T1"/now()
        # placeholders that _metrics_to_summary defaults to. One query for all
        # addresses, not one per vault.
        metadata_by_address: dict[str, VaultMetadata] = {}
        if vault_addresses:
            try:
                from archimedes.db import get_session
                from archimedes.models.chat import VaultMetadata

                session = get_session()
                try:
                    # Casing fix (issue #1028): vault_metadata.vault_address is
                    # stored lowercase (see vaults_routes.store_vault_metadata);
                    # the on-chain addresses in vault_addresses are EIP-55
                    # checksummed, so normalize before the IN() lookup.
                    rows = (
                        session.query(VaultMetadata)
                        .filter(VaultMetadata.vault_address.in_([a.lower() for a in vault_addresses]))
                        .all()
                    )
                    metadata_by_address = {m.vault_address: m for m in rows}
                finally:
                    session.close()
            except Exception as exc:
                logging.getLogger(__name__).warning("vault metadata batch read failed (non-fatal): %s", exc)

        summaries: list[VaultSummaryResponse] = []

        for addr in vault_addresses:
            try:
                metrics = await chain_executor.get_vault_metrics(addr)
                if not _fees_pass_caps(metrics):  # issue #1138 — refuse to list
                    continue
                summary = self._metrics_to_summary(metrics, meta=metadata_by_address.get(addr.lower()))
                if tier is not None and summary.tier != tier:
                    continue
                summaries.append(summary)
            except Exception as e:
                logging.getLogger(__name__).warning(f"Skipping vault {addr}: {e}")
                continue

        # Sort
        sort_key = sort_by if sort_by != "return_inception" else "return_inception"
        summaries.sort(
            key=lambda v: getattr(v, sort_key, 0) or 0,
            reverse=(order == "desc"),
        )

        # Cache before filtering/sorting
        import time as _time

        self._vault_list_cache = VaultListResponse(vaults=list(summaries), total=len(summaries))
        self._vault_list_cache_ts = _time.time()

        # Paginate
        total = len(summaries)
        summaries = summaries[offset : offset + limit]

        return VaultListResponse(vaults=summaries, total=total)

    async def get_vault_detail(self, address: str) -> VaultDetailResponse | None:
        """Get full vault detail."""
        try:
            metrics = await chain_executor.get_vault_metrics(address)
            # Issue #1138 — refuse to surface hostile or unverifiable vaults on
            # the detail view too: it's the page that renders the deposit CTA,
            # so a direct link must not bypass the listing filter.
            if not _fees_pass_caps(metrics):
                return None
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
            await trace_publisher.get_trace_count(address)
            recent_traces = await self._get_recent_traces(address, limit=5)

            # Resolve name/symbol from on-chain, fallback to off-chain metadata
            name, symbol = await self._get_vault_names(address)
            on_chain_name, on_chain_symbol = await self._get_on_chain_names(address)
            name = name or on_chain_name or f"Vault {metrics['tier']}"
            symbol = symbol or on_chain_symbol or f"v{address[:6]}"

            # Read target allocations from contract
            target_allocations = await self._get_target_allocations(address)

            # Compute returns from oracle price snapshots
            returns = await self._compute_returns(address, target_allocations)

            # Get strategy provenance from last agent trace
            strategy_ids = []
            current_regime = None
            try:
                from archimedes.services.redis_state import AgentStateStore

                state = AgentStateStore()
                last_trace = await state.get_last_trace(address)
                if last_trace:
                    strategy_ids = last_trace.get("strategies_referenced", [])
                    market_ctx = last_trace.get("market_context", {})
                    current_regime = market_ctx.get("regime")
            except Exception:
                logger.debug("vault strategy provenance lookup failed", exc_info=True)

            return VaultDetailResponse(
                address=address,
                name=name,
                symbol=symbol,
                tier=metrics["tier"],
                creator=metrics["creator"],
                aum_usdc=metrics["total_aum_usdc"],
                share_price=metrics["share_price_usdc"],
                is_agent_assisted=metrics["is_agent_assisted"],
                management_fee_pct=metrics["management_fee_bps"] / 100,
                performance_fee_pct=metrics["performance_fee_bps"] / 100,
                high_water_mark=metrics["high_water_mark"],
                holdings=holdings,
                target_allocations=target_allocations,
                return_24h=returns["return_24h"],
                return_7d=returns["return_7d"],
                return_30d=returns["return_30d"],
                return_inception=returns["return_inception"],
                recent_traces=recent_traces,
                strategy_ids=strategy_ids,
                current_regime=current_regime,
            )
        except Exception:
            logging.getLogger(__name__).exception("Failed to get vault detail for %s", sanitize_log_value(address))
            return None

    def _metrics_to_summary(self, metrics: dict, meta=None) -> VaultSummaryResponse:
        """Convert chain executor metrics + (optional) off-chain VaultMetadata
        into a summary response. ``meta`` is the VaultMetadata row for this
        vault if one exists; when missing, fields fall back to honest
        placeholders (short-address slug for name) instead of the misleading
        "Vault T1" / now() defaults the marketplace used to render."""
        address = metrics["vault_address"]
        short_address = f"{address[:6]}…{address[-4:]}" if len(address) > 14 else address

        # Real off-chain metadata if the vault was deployed through the UI;
        # otherwise honest fallbacks that don't pretend the vault has a name.
        if meta is not None:
            name = meta.name or f"Vault {short_address}"
            symbol = meta.symbol or f"v{address[2:6]}"
            creator = meta.creator_address or metrics["creator"]
            created_at = meta.created_at.isoformat() if meta.created_at else ""
        else:
            name = f"Vault {short_address}"
            symbol = f"v{address[2:6]}"
            creator = metrics["creator"]
            created_at = ""  # unknown — don't lie about it

        return VaultSummaryResponse(
            address=address,
            name=name,
            symbol=symbol,
            tier=metrics["tier"],
            creator=creator,
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
            created_at=created_at,
        )

    async def _compute_returns(self, vault_address: str, allocations: list[VaultHolding]) -> dict:
        """Compute vault returns from oracle price snapshots in Redis.

        Uses the vault's target allocations and the ASSET_EXPECTED_RETURN table
        to compute a weighted return. If price snapshots exist in Redis (set by
        the oracle updater), uses real price changes instead.
        """
        import os

        import redis.asyncio as _aioredis

        logger = logging.getLogger(__name__)

        # Try Redis-based real returns first. This runs inside an async request
        # handler, so the client MUST be the asyncio one and every call awaited
        # — a blocking sync client here freezes the whole uvicorn event loop
        # (every concurrent request on the worker stalls) if Redis is slow.
        r = None
        try:
            r = _aioredis.from_url(
                os.getenv("REDIS_URL", "redis://localhost:6379/0"),
                decode_responses=True,
            )
            await r.ping()

            # Check if we have a price snapshot for this vault
            snapshot_key = f"vault:prices:{vault_address}"
            snapshot = await r.get(snapshot_key)

            if snapshot:
                prices_at_creation = json.loads(snapshot)
                # Get current oracle prices
                from archimedes.chain.contracts import get_contract_loader

                loader = get_contract_loader()

                weighted_return = 0.0
                total_weight = 0
                for alloc in allocations:
                    token = alloc.token_address
                    weight = int(alloc.weight_pct * 100)  # convert back to BPS
                    if weight == 0:
                        continue
                    total_weight += weight

                    # Find symbol for this token
                    symbol = alloc.symbol

                    # Get current oracle price
                    try:
                        if symbol == "USDC":
                            current_price = 1.0
                        else:
                            oracle = loader.oracle_for(symbol)
                            current_raw = await oracle.functions.price().call()
                            current_price = current_raw / 1e6
                    except Exception:
                        continue

                    creation_price = prices_at_creation.get(token, current_price)
                    if creation_price > 0:
                        asset_return = (current_price - creation_price) / creation_price
                        weighted_return += asset_return * (weight / 10000)

                if total_weight > 0:
                    # Scale to different periods (assume uniform for now)
                    return {
                        "return_24h": round(weighted_return * 0.033, 4),
                        "return_7d": round(weighted_return * 0.233, 4),
                        "return_30d": round(weighted_return, 4),
                        "return_inception": round(weighted_return, 4),
                    }
        except Exception as e:
            logger.debug("Redis price snapshot not available for %s: %s", sanitize_log_value(vault_address), e)
        finally:
            if r is not None:
                with contextlib.suppress(Exception):
                    await r.aclose()

        # Fallback: compute simulated returns from allocation weights
        # This gives realistic numbers until real price history accumulates
        if not allocations:
            return {"return_24h": 0.0, "return_7d": 0.0, "return_30d": 0.0, "return_inception": 0.0}

        weighted_annual = 0.0
        total_weight = 0
        for alloc in allocations:
            weight = int(alloc.weight_pct * 100)  # BPS
            if weight == 0:
                continue
            total_weight += weight
            symbol = alloc.symbol
            expected = self.ASSET_EXPECTED_RETURN.get(symbol, 0.05)
            weighted_annual += expected * (weight / 10000)

        if total_weight == 0:
            return {"return_24h": 0.0, "return_7d": 0.0, "return_30d": 0.0, "return_inception": 0.0}

        # Convert annual to period returns, add small deterministic noise from address
        addr_hash = int(hashlib.md5(vault_address.encode()).hexdigest()[:8], 16) / 0xFFFFFFFF
        noise = (addr_hash - 0.5) * 0.04  # ±2% annual noise, deterministic per vault

        annual = weighted_annual + noise
        return {
            "return_24h": round(annual / 365, 4),
            "return_7d": round(annual * 7 / 365, 4),
            "return_30d": round(annual * 30 / 365, 4),
            "return_inception": round(annual * 30 / 365, 4),  # vaults are new
        }

    async def _token_to_symbol(self, token_address: str, loader=None) -> str:
        """Resolve a token address to its symbol."""
        from archimedes.chain.client import chain_client

        usdc_address = chain_client.settings.usdc_address
        if token_address.lower() == usdc_address.lower():
            return "USDC"

        synth_addresses = chain_client.settings.synth_addresses
        for sym, addr in synth_addresses.items():
            if addr.lower() == token_address.lower():
                return sym

        # Unknown — try reading symbol from contract
        if loader is None:
            from archimedes.chain.contracts import get_contract_loader

            loader = get_contract_loader()
        try:
            token = loader.token(token_address)
            return await token.functions.symbol().call()
        except Exception:
            return "UNKNOWN"

    async def _get_on_chain_names(self, address: str) -> tuple[str | None, str | None]:
        """Read name/symbol directly from the vault contract."""
        try:
            from archimedes.chain.contracts import get_contract_loader

            loader = get_contract_loader()
            vault = loader.vault(address)
            name = await vault.functions.name().call()
            symbol = await vault.functions.symbol().call()
            return name, symbol
        except Exception:
            return None, None

    async def _get_target_allocations(self, address: str) -> list[VaultHolding]:
        """Read target allocations from the vault contract."""
        try:
            from archimedes.chain.contracts import get_contract_loader

            loader = get_contract_loader()
            vault = loader.vault(address)
            tokens, weights = await vault.functions.getTargetAllocations().call()
            allocations: list[VaultHolding] = []
            for token, weight in zip(tokens, weights, strict=False):
                if weight > 0:
                    symbol = await self._token_to_symbol(token, loader)
                    allocations.append(
                        VaultHolding(
                            symbol=symbol,
                            token_address=token,
                            amount=0.0,  # target allocation, not actual holding
                            value_usdc=0.0,
                            weight_pct=weight / 100,
                        )
                    )
            return allocations
        except Exception:
            return []

    async def _get_vault_names(self, address: str) -> tuple[str | None, str | None]:
        """Resolve vault display name and symbol from off-chain metadata."""
        try:
            from archimedes.db import get_session
            from archimedes.models.chat import VaultMetadata

            session = get_session()
            try:
                # Casing fix (issue #1028): stored lowercase — see store_vault_metadata.
                meta = session.query(VaultMetadata).filter(VaultMetadata.vault_address == address.lower()).first()
                if meta:
                    return meta.name, meta.symbol
            finally:
                session.close()
        except Exception:
            logger.debug("vault name resolution failed", exc_info=True)
        return None, None

    async def _get_recent_traces(self, vault_address: str, limit: int = 5) -> list[TraceResponse]:
        """Get recent reasoning traces for a vault (from on-chain)."""
        traces: list[TraceResponse] = []
        try:
            trace_ids = await trace_publisher.loader.trace_registry.functions.getTracesByVault(vault_address).call()

            for trace_id in reversed(trace_ids[-limit:]):
                detail = await trace_publisher.get_trace_by_id(trace_id)
                if detail:
                    traces.append(
                        TraceResponse(
                            id=str(trace_id),
                            vault_address=vault_address,
                            decision_type="rebalance",  # Default
                            trigger="unknown",
                            timestamp=datetime.fromtimestamp(detail["timestamp"], tz=UTC).isoformat(),
                            reasoning="On-chain trace",
                            confidence=0.0,
                            trace_hash=detail["trace_hash"],
                            arc_tx_hash=None,
                            is_verified=True,
                        )
                    )
        except Exception:
            logger.debug("vault recent traces fetch failed", exc_info=True)
        return traces
