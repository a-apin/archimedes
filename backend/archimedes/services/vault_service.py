"""Vault service — composes chain executor data into API responses."""

from __future__ import annotations

import contextlib
import json
import logging
from datetime import UTC, datetime

from archimedes.api.schemas import (
    TraceResponse,
    VaultDetailResponse,
    VaultHolding,
    VaultListResponse,
    VaultSummaryResponse,
)
from archimedes.chain.constants import MAX_MANAGEMENT_FEE_BPS, MAX_PERFORMANCE_FEE_BPS
from archimedes.chain.executor import chain_executor
from archimedes.chain.trace_publisher import trace_publisher
from archimedes.services.log_scrubber import sanitize_log_value

logger = logging.getLogger(__name__)


class VaultFeeGuardRefusal(Exception):
    """A vault exists but the #1138 fee guard refuses to surface it.

    Raised by get_vault_detail so the route can distinguish "refused" from
    "unknown address" (404): over-cap → status_code 400 with the actual fee
    values, unreadable fees → 502 (verification failed, fail-closed).
    """

    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        self.detail = detail
        super().__init__(detail)


def _fee_refusal(metrics: dict) -> tuple[int, str] | None:
    """The #1138 fee-cap gate for display surfaces.

    Returns (status_code, reason) for vaults whose immutable fee bps exceed
    the #1129 caps (hostile — the pre-cap factory could mint them and no
    setter can ever fix them) and for vaults whose fees could not be read
    (None from get_vault_metrics) — fail-closed: never render a vault we
    couldn't verify as investable. Returns None when the vault passes.
    """
    address = metrics.get("vault_address", "?")
    mgmt, perf = metrics.get("management_fee_bps"), metrics.get("performance_fee_bps")
    if mgmt is None or perf is None:
        logger.warning("Refusing to surface vault %s: fee bps unreadable (fail-closed)", sanitize_log_value(address))
        return 502, f"Could not verify on-chain fees for vault {address}; refusing (fail-closed)"
    if mgmt > MAX_MANAGEMENT_FEE_BPS or perf > MAX_PERFORMANCE_FEE_BPS:
        logger.warning(
            "Refusing to surface vault %s: fees exceed caps (managementFeeBps=%d, performanceFeeBps=%d)",
            sanitize_log_value(address),
            mgmt,
            perf,
        )
        return 400, (
            f"Vault {address} fees exceed caps: "
            f"managementFeeBps={mgmt} (cap {MAX_MANAGEMENT_FEE_BPS}), "
            f"performanceFeeBps={perf} (cap {MAX_PERFORMANCE_FEE_BPS})"
        )
    return None


class VaultService:
    """Serves vault data to the API layer."""

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
                if _fee_refusal(metrics) is not None:  # issue #1138 — refuse to list
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
            # so a direct link must not bypass the listing filter. Raises (not
            # returns None) so the route can answer 400/502 with the reason
            # instead of conflating a refused vault with an unknown address.
            refusal = _fee_refusal(metrics)
            if refusal is not None:
                raise VaultFeeGuardRefusal(*refusal)
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
                returns_source=returns["returns_source"],
                recent_traces=recent_traces,
                strategy_ids=strategy_ids,
                current_regime=current_regime,
            )
        except VaultFeeGuardRefusal:
            raise  # deliberate refusal, not a read failure — let the route map it
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
            # The listing surface doesn't do a per-vault oracle-baseline
            # comparison (that needs target allocations + an oracle read per
            # token — get_vault_detail's job, not an N-vault list endpoint's).
            # A hardcoded 0.0 here would itself be a fabricated claim ("this
            # vault is flat"), so the summary row is honestly "unavailable"
            # too (#1103) — never a number the list endpoint didn't compute.
            return_24h=None,
            return_7d=None,
            return_30d=None,
            return_inception=None,
            returns_source="unavailable",
            management_fee_pct=metrics["management_fee_bps"] / 100,
            performance_fee_pct=metrics["performance_fee_bps"] / 100,
            is_agent_assisted=metrics["is_agent_assisted"],
            depositors=0,
            created_at=created_at,
        )

    async def _compute_returns(self, vault_address: str, allocations: list[VaultHolding]) -> dict:
        """Compute vault returns from the oracle price baseline snapshot in Redis.

        Reads the per-vault baseline ``ChainExecutor._write_price_baseline_if_absent``
        writes at ``vault:prices:{vault_address}`` (JSON: token address → price,
        human-scale float — the same units the current-price read below produces)
        and compares it against CURRENT oracle prices for the vault's target
        allocations.

        #1103 — this must NEVER synthesize a number when no real baseline exists.
        A prior version of this function fell back to an ASSET_EXPECTED_RETURN
        lookup table plus deterministic MD5-of-address noise dressed up as a
        computed return; that fabrication is exactly what issue #1103 named
        ("displayed vault returns are always fabricated"), and PR #1152 claimed
        to fix it without actually wiring a writer the reader here reads. Every
        "can't honestly compute this" branch below returns the SAME
        ``returns_source: "unavailable"`` shape — no numbers, ever — mirroring
        the tri-state provenance pattern ``RigorGateVerdict`` uses for the rigor
        gate (``live_rigor_gate.py``: ``source == "live_gate" | "pending"``).
        """
        unavailable = {
            "return_24h": None,
            "return_7d": None,
            "return_30d": None,
            "return_inception": None,
            "returns_source": "unavailable",
        }

        if not allocations:
            return unavailable

        import os

        import redis.asyncio as _aioredis

        logger = logging.getLogger(__name__)

        # This runs inside an async request handler, so the client MUST be the
        # asyncio one and every call awaited — a blocking sync client here
        # freezes the whole uvicorn event loop (every concurrent request on the
        # worker stalls) if Redis is slow.
        r = None
        try:
            redis_url = (
                os.getenv("REDIS_URL")
                or f"redis://{os.getenv('REDIS_HOST', 'localhost')}:{os.getenv('REDIS_PORT', '6379')}/0"
            )
            r = _aioredis.from_url(
                redis_url,
                decode_responses=True,
            )
            await r.ping()

            snapshot_key = f"vault:prices:{vault_address}"
            snapshot = await r.get(snapshot_key)
            if not snapshot:
                return unavailable

            prices_at_baseline = json.loads(snapshot)

            from archimedes.chain.contracts import get_contract_loader

            loader = get_contract_loader()

            weighted_return = 0.0
            total_weight = 0
            for alloc in allocations:
                weight = int(alloc.weight_pct * 100)  # convert back to BPS
                if weight == 0:
                    continue

                baseline_price = prices_at_baseline.get(alloc.token_address)
                if baseline_price is None or baseline_price <= 0:
                    # No baseline for this specific token — don't guess its
                    # contribution, just leave it out of the weighted sum.
                    continue

                symbol = alloc.symbol
                try:
                    if symbol == "USDC":
                        current_price = 1.0
                    else:
                        oracle = loader.oracle_for(symbol)
                        current_raw = await oracle.functions.price().call()
                        current_price = current_raw / 1e6
                except Exception:
                    continue

                total_weight += weight
                asset_return = (current_price - baseline_price) / baseline_price
                weighted_return += asset_return * (weight / 10000)

            if total_weight == 0:
                return unavailable

            # Scale to different periods (assume uniform for now).
            return {
                "return_24h": round(weighted_return * 0.033, 4),
                "return_7d": round(weighted_return * 0.233, 4),
                "return_30d": round(weighted_return, 4),
                "return_inception": round(weighted_return, 4),
                "returns_source": "oracle_baseline",
            }
        except Exception as e:
            logger.debug("Redis price baseline not available for %s: %s", sanitize_log_value(vault_address), e)
            return unavailable
        finally:
            if r is not None:
                with contextlib.suppress(Exception):
                    await r.aclose()

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
