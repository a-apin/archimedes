"""Vault endpoints — /api/vaults/* (excluding chat, which lives in chat_routes.py)."""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response

from archimedes.api._route_helpers import strategy_provider, vault_svc
from archimedes.api.auth_siwe import require_verified_wallet
from archimedes.api.limiter import limiter
from archimedes.chain.strategy_publisher import strategy_publisher
from archimedes.services.log_scrubber import sanitize_log_value

logger = logging.getLogger(__name__)
from archimedes.api.schemas import (
    VaultDetailResponse,
    VaultListResponse,
)
from archimedes.api.vault_schemas import (
    AllocationTarget,
    SetAllocationsRequest,
    SetAllocationsResponse,
    VaultCreateRequest,
    VaultCreateResponse,
    VaultMetadataRequest,
    VaultMetadataResponse,
)
from archimedes.chain.executor import chain_executor
from archimedes.models.chat import VaultMetadata
from archimedes.services.live_rigor_gate import verdicts_for_strategies
from archimedes.services.rigor_profiles import STRICTEST_LEVEL, clamp_level
from archimedes.services.strategy_sizer import kelly_weighted_allocations, scale_to_budget, size_strategies

vaults_router = APIRouter(prefix="/api/vaults", tags=["vaults"])


async def _anchor_strategies_async(strategy_ids: list[str]) -> None:
    """Best-effort on-chain anchoring of strategy passports via StrategyRegistry.

    Fire-and-forget: failures are logged but never raised. Matches the
    trace_publisher pattern in agent_runner.py.
    """
    for sid in strategy_ids:
        try:
            passport = strategy_provider().get_strategy(sid)
            if passport is None:
                logger.debug("anchor: strategy %s not found in provider — skipping", sanitize_log_value(sid))
                continue
            if not getattr(passport, "methodology_hash", None):
                logger.info("skipping anchor for %s: no methodology_hash", sanitize_log_value(sid))
                continue

            paper_hashes = [p.arxiv_id for p in passport.papers if p.arxiv_id]
            regime_tag = getattr(passport, "regime_tag", None)

            await strategy_publisher.anchor(
                strategy_id=passport.id,
                methodology_hash=passport.methodology_hash,
                paper_hashes=paper_hashes,
                regime_tag=regime_tag,
                metadata_uri="",
            )
            logger.info("anchored strategy %s on-chain", sanitize_log_value(sid))
        except Exception as exc:
            logger.warning("anchor failed for strategy %s (non-fatal): %s", sanitize_log_value(sid), exc)


def _strategy_rigor_status(strategy_id: str) -> tuple[bool, bool]:
    """``(found, passes_rigor_gate)`` for a strategy id, resolved across the curated
    provider AND the generated ``strategy_passports`` table — the same two sources
    ``GET /api/strategies/{id}`` uses. Server-side source of truth for the deploy
    gate (#818).

    Fail-closed: if the DB lookup raises (cannot verify a generated strategy), the
    strategy is reported as not-found so the caller refuses the deploy rather than
    waving through an unverifiable one.
    """
    strat = strategy_provider().get_strategy(strategy_id)
    if strat is not None:
        return True, bool(getattr(strat, "passes_rigor_gate", False))

    from archimedes.db import get_session
    from archimedes.services.passport_loader import get_passport

    try:
        with get_session() as session:
            record = get_passport(session, strategy_id)
            if record is not None:
                return True, bool(getattr(record, "passes_rigor_gate", False))
    except Exception:
        logger.exception("rigor-status lookup failed for %s — failing closed", sanitize_log_value(strategy_id))
        return False, False
    return False, False


def _deployable_levels(strategy_ids: list[str]) -> dict[str, tuple[bool, int | None, bool]]:
    """``{id: (found, min_passing_level, blocked_by_floor)}`` at the chosen level.

    Curated strategies get a LIVE per-level verdict computed over the whole-library
    cohort (``verdicts_for_strategies``), so the multiple-testing correction is
    the same one the badge uses — the strictness slider genuinely re-grades them.
    Generated strategies fall back to their persisted level-1 badge (min level 1
    if the badge holds, else ``None``): the slider does not loosen generated
    deploys in this version, because a generated strategy's look-ahead provenance
    is a closed-DSL self-attestation, not the AST audit the live re-grade runs, so
    re-grading it here would be apples-to-oranges. This is fail-safe (a generated
    strategy that fails its badge simply can't be deployed at a looser level yet).

    Never raises: any resolution failure degrades an id to not-deployable.
    """
    ids = list(dict.fromkeys(strategy_ids))  # de-dup, preserve order
    try:
        provider_strats = strategy_provider().list_strategies()
    except Exception:
        logger.exception("deploy gate: provider list failed — failing closed")
        provider_strats = []
    provider_by_id = {s.id: s for s in provider_strats}

    curated_verdicts: dict = {}
    if any(sid in provider_by_id for sid in ids):
        try:
            curated_verdicts = verdicts_for_strategies(provider_strats)
        except Exception:
            logger.exception("deploy gate: curated verdict batch failed — failing closed")
            curated_verdicts = {}

    out: dict[str, tuple[bool, int | None, bool]] = {}
    for sid in ids:
        verdict = curated_verdicts.get(sid)
        if verdict is not None:
            out[sid] = (True, verdict.min_passing_level, verdict.blocked_by_floor)
        elif sid in provider_by_id:
            # Curated but the verdict batch failed — fail closed (found, not deployable).
            out[sid] = (True, None, False)
        else:
            # Generated (or unknown): badge-gated fallback.
            found, passes_badge = _strategy_rigor_status(sid)
            out[sid] = (found, (STRICTEST_LEVEL if passes_badge else None), False)
    return out


def _assert_strategies_pass_rigor(strategy_ids: list[str], strictness_level: int = STRICTEST_LEVEL) -> None:
    """Fail-closed deploy precondition (#818): every strategy bound to a vault must
    resolve and pass the rigor gate at the caller's chosen ``strictness_level``.
    Raises 422 otherwise. The frontend Deploy gate (#782) is defense-in-depth;
    THIS is the guarantee a non-UI caller cannot route around.

    The always-on correctness floors (look-ahead, positive OOS, DSR ≥ 0.50) hold
    at every level, so no ``strictness_level`` bypasses the gate — a user can only
    trade statistical confidence for breadth, never admit a broken/curve-fit
    strategy. A strategy with no computed verdict (placeholder) is correctly
    refused.
    """
    level = clamp_level(strictness_level)

    # Fast, exact badge path at the strictest level: a badge-fail can never pass
    # level 1, so no live per-level ladder computation is needed. This also keeps
    # the default deploy path unchanged for callers that don't opt into strictness.
    if level <= STRICTEST_LEVEL:
        for sid in strategy_ids:
            found, passes = _strategy_rigor_status(sid)
            if not found:
                raise HTTPException(status_code=422, detail=f"Strategy '{sid}' not found — cannot deploy.")
            if not passes:
                raise HTTPException(
                    status_code=422,
                    detail=(
                        f"Strategy '{sid}' has not passed the rigor gate — refusing to deploy "
                        "(server-side rigor enforcement)."
                    ),
                )
        return

    # Looser-than-badge level: consult the live per-level ladder.
    levels = _deployable_levels(strategy_ids)
    for sid in strategy_ids:
        found, min_level, blocked = levels.get(sid, (False, None, False))
        if not found:
            raise HTTPException(status_code=422, detail=f"Strategy '{sid}' not found — cannot deploy.")
        if min_level is not None and min_level <= level:
            continue
        if blocked:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Strategy '{sid}' fails an always-on rigor floor (look-ahead / positive OOS / "
                    "DSR ≥ 0.50) — it cannot be deployed at any strictness level."
                ),
            )
        if min_level is None:
            raise HTTPException(
                status_code=422,
                detail=f"Strategy '{sid}' does not pass the rigor gate even at the most permissive level.",
            )
        raise HTTPException(
            status_code=422,
            detail=f"Strategy '{sid}' requires strictness level ≥ {min_level}; level {level} was selected.",
        )


@vaults_router.get("/", response_model=VaultListResponse)
async def list_vaults(
    tier: int | None = Query(None, ge=1, le=2),
    sort_by: str = Query("aum", pattern="^(aum|return_24h|return_7d|sharpe|created_at)$"),
    order: str = Query("desc", pattern="^(asc|desc)$"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    """List vaults for the marketplace leaderboard."""
    return await vault_svc.list_vaults(tier=tier, sort_by=sort_by, order=order, limit=limit, offset=offset)


@vaults_router.post("/create", response_model=VaultCreateResponse)
@limiter.limit("5/minute")
async def create_vault(
    req: VaultCreateRequest,
    request: Request,  # used for slowapi rate-limit keying AND funnel attribution (#787)
    response: Response,  # noqa: ARG001 — slowapi @limiter.limit inspects param name
    wallet: str = Depends(require_verified_wallet),
):
    """Deploy a new vault on Arc via VaultFactory.

    SIWE-gated: vault creation spends the backend signer's gas on-chain, so it
    must require an authenticated wallet (rate-limiting alone left it open to an
    unauthenticated gas-drain).

    Non-custodial (T0.2): the SIWE-verified ``wallet`` is passed as the vault's
    owner. ``create_vault`` creates the vault with the backend signer, then
    transfers Ownable ownership to this user and pins the backend as the
    rebalance-only agent — so ``owner == user`` and ``agent == backend``. A
    compromised backend/agent key can rebalance but can NOT re-point the oracle,
    widen slippage, pause, or otherwise drain the vault. This re-lands the intent
    of reverted PR #646 without changing the live 5-arg createVault selector.
    """
    # Server-side rigor gate (#818): refuse to deploy any strategy that hasn't
    # passed the rigor gate AT THE REQUESTED STRICTNESS, BEFORE spending gas. The
    # #782 frontend Deploy gate is defense-in-depth; this is the guarantee a
    # direct/non-UI API call can't bypass. Always-on floors hold at every level.
    _assert_strategies_pass_rigor(req.strategy_ids, req.strictness_level)

    try:
        vault_address = await chain_executor.create_vault(
            name=req.name,
            symbol=req.symbol,
            management_fee_bps=req.management_fee_bps,
            performance_fee_bps=req.performance_fee_bps,
            agent_assisted=req.agent_assisted,
            owner_wallet=wallet,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        # Don't leak the raw exception string to the client (DB/chain internals);
        # log the full detail server-side and return a generic message.
        logger.exception("Vault deployment failed")
        raise HTTPException(status_code=500, detail="Vault deployment failed") from exc

    # Conversion funnel (#787): the bottom of funnel — a vault was actually
    # deployed for this visitor. Fail-safe; never blocks the response.
    from archimedes.api.funnel_middleware import record_funnel

    await record_funnel(request, "vault_deployed")

    return VaultCreateResponse(vault_address=vault_address, strategy_ids=req.strategy_ids)


@vaults_router.get("/{address}/health")
async def get_vault_health(address: str):
    """Get vault health snapshot including live Sharpe drift vs backtest baseline."""
    from archimedes.services.vault_monitor import vault_monitor

    return await vault_monitor.get_vault_health(address)


@vaults_router.get("/{address}", response_model=VaultDetailResponse)
async def get_vault_detail(address: str):
    """Get full vault detail including holdings, performance, traces."""
    from fastapi import HTTPException

    detail = await vault_svc.get_vault_detail(address)
    if detail is None:
        raise HTTPException(status_code=404, detail="Vault not found")
    return detail


# ── Vault Metadata (off-chain) ───────────────────────────────


@vaults_router.post("/metadata", response_model=VaultMetadataResponse)
@limiter.limit("10/minute")
async def store_vault_metadata(
    req: VaultMetadataRequest,
    request: Request,  # noqa: ARG001 — slowapi @limiter.limit inspects param name
    response: Response,  # noqa: ARG001 — slowapi @limiter.limit inspects param name
    wallet: str = Depends(require_verified_wallet),
):
    """Store off-chain vault metadata (strategy associations, display name).

    SIWE-gated and owner-scoped: this write triggers `_anchor_strategies_async`,
    a backend-signed on-chain transaction. Previously anyone could overwrite any
    vault's metadata and spend gas. The authenticated wallet now becomes the
    metadata owner on first write, and subsequent writes require the caller to
    be that owner.
    """
    # Server-side rigor enforcement on the client-signed deploy path — the real
    # choke point. The UI creates the vault on-chain from the user's own wallet
    # (bypassing POST /create), then links strategies here, so THIS is where a
    # strategy↔vault link is refused unless the strategy passes at the requested
    # strictness. Always-on floors hold at every level, so the link can never
    # bind a look-ahead-biased / OOS-negative / worse-than-coin-flip strategy.
    if req.strategy_ids:
        _assert_strategies_pass_rigor(req.strategy_ids, req.strictness_level)

    from archimedes.db import get_session

    session = get_session()
    try:
        meta = session.query(VaultMetadata).filter(VaultMetadata.vault_address == req.vault_address).first()
        if meta is None:
            meta = VaultMetadata(vault_address=req.vault_address)
            session.add(meta)
        elif meta.creator_address and meta.creator_address.lower() != wallet.lower():
            # An owner already claimed this vault's metadata; only they may edit it.
            raise HTTPException(status_code=403, detail="Not authorized to edit this vault's metadata.")

        meta.name = req.name
        meta.symbol = req.symbol
        # Bind ownership to the authenticated wallet — never trust a
        # caller-supplied creator_address (that was the spoofing vector).
        meta.creator_address = wallet.lower()
        meta.set_strategy_ids(req.strategy_ids)
        session.commit()
        session.refresh(meta)

        # Fire-and-forget on-chain strategy anchoring (best-effort, non-fatal)
        if req.strategy_ids:
            asyncio.create_task(  # noqa: RUF006 — intentional fire-and-forget; anchoring is best-effort and non-fatal
                _anchor_strategies_async(req.strategy_ids)
            )

        return VaultMetadataResponse(**meta.to_dict())
    except HTTPException:
        # Auth/ownership failures (401/403) must pass through unchanged, not be
        # masked as a 500 by the broad handler below.
        session.rollback()
        raise
    except Exception as exc:
        session.rollback()
        # Generic message to the client; full detail logged server-side only.
        logger.exception("Vault metadata update failed")
        raise HTTPException(status_code=500, detail="Vault metadata update failed") from exc
    finally:
        session.close()


@vaults_router.get("/{address}/metadata", response_model=VaultMetadataResponse)
async def get_vault_metadata(address: str):
    """Get off-chain vault metadata (strategy associations, display name)."""
    from fastapi import HTTPException

    from archimedes.db import get_session

    session = get_session()
    try:
        meta = session.query(VaultMetadata).filter(VaultMetadata.vault_address == address).first()
        if meta is None:
            raise HTTPException(status_code=404, detail="No metadata for this vault")
        return VaultMetadataResponse(**meta.to_dict())
    finally:
        session.close()


@vaults_router.post("/{address}/derive-allocations", response_model=SetAllocationsResponse)
@limiter.limit("20/minute")
async def derive_vault_allocations(
    address: str,
    req: SetAllocationsRequest,
    request: Request,  # noqa: ARG001 — slowapi @limiter.limit inspects param name; path param routes the request
    response: Response,  # noqa: ARG001 — reserved for slowapi rate-limit headers
    wallet: str = Depends(require_verified_wallet),  # noqa: ARG001 — SIWE gate (#917): auth required, wallet unused in body
):
    """Derive target allocations from selected strategies.

    Gated behind a verified SIWE session (#917): the derivation runs a full
    strategy scan + on-chain reads, so it is not exposed to unauthenticated
    callers who could amplify it into a compute-DoS.
    """
    from archimedes.chain.client import chain_client
    from archimedes.services.strategy_signal_evaluator import strategy_evaluator

    strategies = strategy_provider().list_strategies()

    if req.strategy_ids:
        strategies = [s for s in strategies if s.id in req.strategy_ids]

    if not strategies:
        usdc_floor_bps = int(req.usdc_floor_pct * 100)
        synth_budget_bps = 10000 - usdc_floor_bps
        synth_addrs = {k: v for k, v in chain_client.settings.synth_addresses.items() if v}
        per_synth = synth_budget_bps // max(len(synth_addrs), 1)
        allocations = [
            AllocationTarget(symbol=sym, token_address=addr, weight_bps=per_synth) for sym, addr in synth_addrs.items()
        ]
        allocations.append(
            AllocationTarget(
                symbol="USDC",
                token_address=chain_client.settings.usdc_address,
                weight_bps=usdc_floor_bps,
            )
        )
        return SetAllocationsResponse(
            allocations=allocations,
            total_bps=sum(a.weight_bps for a in allocations),
            strategy_count=0,
        )

    synth_assets = [sym for sym, addr in chain_client.settings.synth_addresses.items() if addr]
    all_signals = await asyncio.to_thread(
        strategy_evaluator.evaluate_strategies,
        strategies,
        synth_assets,
    )
    usdc_floor = req.usdc_floor_pct / 100.0

    # Strategy-level Kelly sizing (roadmap Priority 3.1): each gate-passing
    # strategy receives passport-half-Kelly × risk-profile multiplier of the
    # capital; CANDIDATEs and gate-failers size to zero (the gate is not
    # bypassable via deployment); unclaimed budget stays in USDC.
    #
    # Honour the user's deploy strictness: a strategy the user is allowed to
    # deploy at level L should also RECEIVE capital, not be silently zeroed by the
    # stricter level-1 badge check. At the badge level the fast badge path is
    # exact, so keep the default (deployable_ids=None → passes_rigor_gate).
    deployable_ids: set[str] | None = None
    lvl = clamp_level(req.strictness_level)
    if lvl > STRICTEST_LEVEL:
        levels = _deployable_levels([s.id for s in strategies])
        deployable_ids = {
            sid for sid, (found, ml, _blocked) in levels.items() if found and ml is not None and ml <= lvl
        }
    sized_fractions = size_strategies(strategies, req.risk_profile, deployable_ids=deployable_ids)
    sized_fractions = scale_to_budget(sized_fractions, 1.0 - usdc_floor)
    excluded = sorted(sid for sid, frac in sized_fractions.items() if frac <= 0.0)
    target_weights = kelly_weighted_allocations(all_signals, sized_fractions, usdc_floor=usdc_floor)

    allocations: list[AllocationTarget] = []

    symbol_to_addr = {"USDC": chain_client.settings.usdc_address}
    symbol_to_addr.update(chain_client.settings.synth_addresses)

    for symbol, weight in target_weights.items():
        token_address = symbol_to_addr.get(symbol)
        if not token_address:
            continue
        weight_bps = int(round(weight * 10000))
        if weight_bps > 0:
            allocations.append(
                AllocationTarget(
                    symbol=symbol,
                    token_address=token_address,
                    weight_bps=weight_bps,
                )
            )

    total = sum(a.weight_bps for a in allocations)
    if total > 0 and total != 10000:
        scale = 10000 / total
        for a in allocations:
            a.weight_bps = int(round(a.weight_bps * scale))
        allocations = [a for a in allocations if a.weight_bps > 0]
        total = sum(a.weight_bps for a in allocations)
        if total != 10000 and allocations:
            largest = max(allocations, key=lambda a: a.weight_bps)
            largest.weight_bps += 10000 - total

    return SetAllocationsResponse(
        allocations=allocations,
        total_bps=sum(a.weight_bps for a in allocations),
        strategy_count=len(strategies),
        risk_profile=req.risk_profile,
        sized_strategies={sid: frac for sid, frac in sized_fractions.items() if frac > 0.0},
        excluded_strategy_ids=excluded,
    )
