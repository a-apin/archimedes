"""Marketplace routes — publish / subscribe / browse / x402 payment.

Session pattern: with get_session() as session (matches codebase convention).
pool_id is ALWAYS derived server-side via derive_pool_id (D-POOL).
"""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.exc import IntegrityError

from archimedes.api._route_helpers import strategy_provider
from archimedes.api.auth_siwe import require_verified_wallet
from archimedes.api.limiter import limiter
from archimedes.db import get_session
from archimedes.marketplace.encoding import derive_pool_id, to_bytes32
from archimedes.marketplace.service import MarketService, Subscriber
from archimedes.models.marketplace import MarketplaceAgent
from archimedes.models.strategy_generators import wallet_can_publish
from archimedes.models.strategy_store import StrategyRecord

logger = logging.getLogger(__name__)

marketplace_router = APIRouter(prefix="/api/marketplace", tags=["marketplace"])


def _get_market(request: Request) -> MarketService:
    market: MarketService | None = getattr(request.app.state, "market", None)
    if market is None:
        raise HTTPException(status_code=503, detail="Marketplace engine not available")
    return market


# ---------------------------------------------------------------------------
# POST /api/marketplace/publish
# ---------------------------------------------------------------------------


@marketplace_router.post("/publish")
@limiter.limit("3/minute")
async def publish_strategy(
    request: Request,
    response: Response,
    body: dict,
    wallet: str = Depends(require_verified_wallet),
):
    """Publish a strategy to the marketplace.

    Body: {strategy_id, vault_address?}
    pool_id is DERIVED server-side (D-POOL). Never accept it from the client.
    The platform revenue wallet is a server-side constant (ARCHIMEDES_TREASURY_WALLET),
    never client-supplied. Publisher settlement wallet is auto-provisioned via Circle DCW (D2).
    """
    market = _get_market(request)
    strategy_id = body.get("strategy_id", "").strip()
    vault_address = body.get("vault_address", "").strip() or ""

    if not strategy_id:
        raise HTTPException(status_code=400, detail="strategy_id is required")

    # The platform's revenue share is the product's business model — it MUST be
    # a server-side constant, never client-supplied. A client-set value (empty →
    # publisher keeps 100%; arbitrary → funds to any address) would let a
    # publisher opt out of the split. Pools are immutable once createPool runs,
    # so the wrong value would be baked in permanently. Fail closed: if the
    # treasury wallet is not configured, refuse to publish rather than create a
    # pool with a bad split.
    platform_wallet = os.getenv("ARCHIMEDES_TREASURY_WALLET", "").strip()
    if not platform_wallet:
        raise HTTPException(
            status_code=503,
            detail="Marketplace not configured: ARCHIMEDES_TREASURY_WALLET is unset",
        )

    # 0. Validate strategy exists in the provider
    if strategy_provider().get_strategy(strategy_id) is None:
        raise HTTPException(status_code=404, detail=f"Strategy not found: {strategy_id}")

    # 1. Reject if publisher already running for this strategy
    with get_session() as session:
        existing = (
            session.query(MarketplaceAgent)
            .filter(
                MarketplaceAgent.role == "publisher",
                MarketplaceAgent.strategy_id == strategy_id,
                MarketplaceAgent.status == "running",
            )
            .first()
        )
        if existing is not None:
            raise HTTPException(status_code=409, detail=f"Publisher already exists for strategy '{strategy_id}'")

    # 1a. D5 ownership check — only the wallet that generated a strategy (or
    #     a PLATFORM_ADMIN_WALLETS member for example strategies) can publish it.
    with get_session() as session:
        record = session.query(StrategyRecord).filter_by(id=strategy_id).first()
        if record is None:
            raise HTTPException(status_code=404, detail=f"Strategy '{strategy_id}' not found")
        if not wallet_can_publish(
            session, strategy_id=strategy_id, wallet_address=wallet, is_example=record.is_example
        ):
            raise HTTPException(status_code=403, detail="You did not generate this strategy and cannot publish it")

    # 2. Derive pool_id (D-POOL) — NEVER from client
    pool_id = derive_pool_id(strategy_id, wallet)

    # 3. Create vault if not provided
    if not vault_address:
        vault_address = await market.executor.create_vault(
            name=strategy_id,
            symbol=f"VLT-{strategy_id[:8].upper()}",
            management_fee_bps=0,
            performance_fee_bps=0,
            agent_assisted=True,
            owner_wallet=wallet,
        )

    # 3a. Publish funding gate (D7) — vault MUST hold at least
    #     MARKETPLACE_MIN_VAULT_FUNDS_RAW idle USDC (raw 6-dec).
    #     Single threshold for both new and reused vaults.
    min_funds_raw = int(os.getenv("MARKETPLACE_MIN_VAULT_FUNDS_RAW", "1000000"))
    balance = await market._usdc_balance_of(vault_address)
    if balance < min_funds_raw:
        raise HTTPException(
            status_code=402,
            detail=f"Vault USDC balance {balance} below minimum {min_funds_raw}",
        )

    # 3b. Auto-provision publisher settlement wallet (Circle DCW).
    #     Must happen before on-chain createPool so a wallet failure never
    #     leaves an on-chain pool with no way to be funded.
    from archimedes.marketplace.wallet_provisioner import provision_publisher_wallet

    try:
        agent_wallet_id, gateway_seller_address = await provision_publisher_wallet(pool_id)
    except Exception as exc:
        logger.error("Circle wallet provisioning failed for publisher %s: %s", strategy_id, exc)
        raise HTTPException(status_code=502, detail="Failed to provision publisher settlement wallet") from exc

    # 4. Check on-chain pool status, then createPool if needed (owner key only)
    splitter_addr = market.settings.payment_splitter_address
    pool_already_active = False
    try:
        sp_c = market.loader._contract(splitter_addr, "PaymentSplitter")
        sp_data = await sp_c.functions.pools(to_bytes32(pool_id)).call()
        # pools returns (creator, platform, total_collected, total_disbursed, held_balance, active)
        pool_already_active = bool(sp_data[5]) if len(sp_data) >= 6 else False
    except Exception:
        logger.warning("pools() staticcall failed; proceeding with createPool")

    if not pool_already_active:
        try:
            if market.signer.is_configured:
                await market.signer.execute_contract(
                    splitter_addr,
                    "createPool(bytes32,address,address)",
                    [to_bytes32(pool_id), wallet, platform_wallet],
                )
            else:
                c = market.loader._contract(splitter_addr, "PaymentSplitter")
                tx = await c.functions.createPool(to_bytes32(pool_id), wallet, platform_wallet).build_transaction(
                    {
                        "from": market.settings.agent_account.address,
                        "nonce": await market.loader.client.w3.eth.get_transaction_count(
                            market.settings.agent_account.address
                        ),
                        "gas": 200_000,
                        "gasPrice": await market.loader.client.w3.eth.gas_price,
                    }
                )
                signed = market.settings.agent_account.sign_transaction(tx)
                h = await market.loader.client.w3.eth.send_raw_transaction(signed.raw_transaction)
                await market.loader.client.w3.eth.wait_for_transaction_receipt(h)
        except Exception as exc:
            logger.error("createPool failed: %s", exc)
            raise HTTPException(status_code=502, detail=f"Failed to create pool on-chain: {exc}") from exc

    # 5. Insert publisher row (DB-level partial unique index prevents
    #    two running publishers for the same strategy_id — C-4).
    with get_session() as session:
        try:
            agent = MarketplaceAgent(
                role="publisher",
                strategy_id=strategy_id,
                creator_wallet=wallet.lower(),
                pool_id=pool_id,
                vault_address=vault_address,
                gateway_seller_address=gateway_seller_address,
                agent_wallet_id=agent_wallet_id,
            )
            session.add(agent)
            session.commit()
            result = agent.to_dict()
        except IntegrityError:
            session.rollback()
            raise HTTPException(
                status_code=409,
                detail=f"Publisher already exists for strategy '{strategy_id}'",
            ) from None

    # 6. Start the publisher loop
    await market.start_publisher(
        strategy_id,
        pool_id,
        vault_address,
        wallet,
        gateway_seller_address=gateway_seller_address,
        agent_wallet_id=agent_wallet_id,
    )

    result["pool_id"] = pool_id
    return result


# ---------------------------------------------------------------------------
# POST /api/marketplace/subscribe
# ---------------------------------------------------------------------------


@marketplace_router.post("/subscribe")
@limiter.limit("5/minute")
async def subscribe_strategy(
    request: Request,
    response: Response,
    body: dict,
    wallet: str = Depends(require_verified_wallet),
):
    """Subscribe to a published strategy.

    Body: {strategy_id, pool_id, sub_id, ephemeral_wallet}
    The subscription registry is Postgres-only (P7 — SubscriptionManager
    fully detached). Off-chain checks: publisher exists and is running,
    wallet not already subscribed, sub_id format valid.
    """
    market = _get_market(request)
    strategy_id = body.get("strategy_id", "").strip()
    sub_id = body.get("sub_id", "").strip()

    if not strategy_id or not sub_id:
        raise HTTPException(status_code=400, detail="strategy_id and sub_id are required")

    # 0a. Validate sub_id format — must be 0x-prefixed 66-char hex (D-BYTES32)
    if not sub_id.startswith("0x") or len(sub_id) != 66:
        raise HTTPException(status_code=400, detail="sub_id must be a 0x-prefixed 32-byte hex string (66 chars)")
    try:
        int(sub_id, 16)
    except ValueError:
        raise HTTPException(status_code=400, detail="sub_id is not valid hex") from None
    # Reject all-zero sub_id
    if int(sub_id, 16) == 0:
        raise HTTPException(status_code=400, detail="sub_id cannot be zero")

    # 0b. Validate strategy exists in the provider (M2)
    if strategy_provider().get_strategy(strategy_id) is None:
        raise HTTPException(status_code=404, detail=f"Strategy not found: {strategy_id}")

    # 1. Find the publisher
    pub_row = None
    with get_session() as session:
        pub_row = (
            session.query(MarketplaceAgent)
            .filter(
                MarketplaceAgent.role == "publisher",
                MarketplaceAgent.strategy_id == strategy_id,
                MarketplaceAgent.status == "running",
            )
            .first()
        )
    if pub_row is None:
        raise HTTPException(status_code=404, detail=f"No running publisher for strategy '{strategy_id}'")

    # 2. Reject if this wallet is already subscribed
    with get_session() as session:
        existing = (
            session.query(MarketplaceAgent)
            .filter(
                MarketplaceAgent.role == "subscriber",
                MarketplaceAgent.subscriber_wallet == wallet.lower(),
                MarketplaceAgent.strategy_id == strategy_id,
                MarketplaceAgent.status == "running",
            )
            .first()
        )
        if existing is not None:
            raise HTTPException(status_code=409, detail="Already subscribed to this strategy")

        # 2b. Reject a sub_id already registered to ANY subscription. sub_id is
        # client-supplied and keys the in-process engine
        # (pub.subscribers[sub_id]) — a duplicate would silently overwrite
        # another subscriber's engine entry. The partial unique index
        # uq_marketplace_agents_sub_id (models/marketplace.py) backs this
        # check against races.
        sub_id_taken = (
            session.query(MarketplaceAgent)
            .filter(MarketplaceAgent.role == "subscriber", MarketplaceAgent.sub_id == sub_id)
            .first()
        )
        if sub_id_taken is not None:
            raise HTTPException(status_code=409, detail="sub_id already in use")

    # 3. Use the server-derived pool_id for storage (D-POOL)
    pool_id = derive_pool_id(strategy_id, pub_row.creator_wallet)

    # 4. Provision a Circle Developer-Controlled Wallet for this subscriber
    # (replaces the old Account.create() + raw-key path — kills D2/D3).
    # If provisioning fails, the subscribe fails closed (no row inserted).
    from archimedes.marketplace.wallet_provisioner import provision_subscriber_wallet

    try:
        wallet_id, wallet_address = await provision_subscriber_wallet(sub_id)
    except Exception as exc:
        logger.warning("Circle wallet provisioning failed for sub %s: %s", sub_id, exc)
        raise HTTPException(status_code=502, detail="Failed to provision subscriber wallet") from exc

    # The subscriber's Circle wallet address is the funded address and
    # the x402 signer address. Store as ephemeral_wallet (existing column
    # name) to limit blast radius.
    ephemeral_wallet = wallet_address

    # 5. Create vault for subscriber if needed
    vault_address = ""
    try:
        vault_address = await market.executor.create_vault(
            name=f"sub-{strategy_id}",
            symbol=f"SUB-{strategy_id[:8].upper()}",
            management_fee_bps=0,
            performance_fee_bps=0,
            agent_assisted=True,
            owner_wallet=wallet,
        )
    except Exception as exc:
        logger.warning("create_vault for subscriber failed: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to create subscriber vault") from exc

    # 6. Insert subscriber row. The pre-checks in step 2/2b are racy across
    # concurrent requests (wallet provisioning + vault creation await in
    # between) — the ORM partial unique indexes (models/marketplace.py:
    # uq_marketplace_agents_sub_id, uq_marketplace_agents_running_subscriber)
    # make the insert the authoritative gate; IntegrityError maps to the same
    # 409 the pre-checks give.
    with get_session() as session:
        agent = MarketplaceAgent(
            role="subscriber",
            strategy_id=strategy_id,
            subscriber_wallet=wallet.lower(),
            sub_id=sub_id,
            pool_id=pool_id,
            vault_address=vault_address,
            ephemeral_wallet=ephemeral_wallet,
            circle_wallet_id=wallet_id,
        )
        session.add(agent)
        try:
            session.commit()
        except IntegrityError as exc:
            session.rollback()
            raise HTTPException(
                status_code=409, detail="Already subscribed to this strategy (concurrent request)"
            ) from exc
        result = agent.to_dict()

    # 7. Register subscriber with the engine
    sub = Subscriber(
        sub_id=sub_id,
        pool_id=pool_id,
        vault_address=vault_address,
        ephemeral_wallet=ephemeral_wallet,
        subscriber_wallet=wallet.lower(),
        circle_wallet_id=wallet_id,
    )
    await market.add_subscriber(strategy_id, sub)

    return result


# ---------------------------------------------------------------------------
# DELETE /api/marketplace/subscribe/{strategy_id}
# ---------------------------------------------------------------------------


@marketplace_router.delete("/subscribe/{strategy_id}")
@limiter.limit("10/minute")
async def unsubscribe_strategy(
    request: Request,
    response: Response,
    strategy_id: str,
    wallet: str = Depends(require_verified_wallet),
):
    """Unsubscribe current wallet from a strategy."""
    market = _get_market(request)

    with get_session() as session:
        sub_row = (
            session.query(MarketplaceAgent)
            .filter(
                MarketplaceAgent.role == "subscriber",
                MarketplaceAgent.subscriber_wallet == wallet.lower(),
                MarketplaceAgent.strategy_id == strategy_id,
                MarketplaceAgent.status == "running",
            )
            .first()
        )
        if sub_row is None:
            raise HTTPException(status_code=404, detail="Active subscription not found")
        sub_row.status = "stopped"
        session.commit()
        # Capture the DCW handles before the session closes (detached-ORM safety).
        sub_id = sub_row.sub_id
        circle_wallet_id = sub_row.circle_wallet_id
        dcw_address = sub_row.ephemeral_wallet

    await market.remove_subscriber(strategy_id, sub_id)

    # Auto-withdraw: return any remaining prepaid-fee balance from the custodial
    # Circle DCW back to the subscriber's own wallet — their exit from the
    # interim custodial fee model (issue #975). Best-effort + no-op under
    # PAYMENTS_DRY_RUN; a failed sweep never blocks the unsubscribe (balance
    # stays recoverable). The subscriber's SIWE-verified wallet is the caller.
    refund_tx = await market.refund_subscriber(
        circle_wallet_id=circle_wallet_id,
        dcw_address=dcw_address,
        to_wallet=wallet,
        sub_id=sub_id,
    )

    return {"status": "unsubscribed", "strategy_id": strategy_id, "refund_tx": refund_tx}


# ---------------------------------------------------------------------------
# DELETE /api/marketplace/publish/{strategy_id}
# ---------------------------------------------------------------------------


@marketplace_router.delete("/publish/{strategy_id}")
@limiter.limit("10/minute")
async def stop_publish(
    request: Request,
    response: Response,
    strategy_id: str,
    wallet: str = Depends(require_verified_wallet),
):
    """Stop a published strategy (creator only).

    Cascades retire to all running subscribers: marks them ``"retired"`` with
    ``stopped_at`` set and surfaces an advisory notice to call
    ``unsubscribe()`` from their own wallet (TASK 18).
    """
    market = _get_market(request)

    with get_session() as session:
        pub_row = (
            session.query(MarketplaceAgent)
            .filter(
                MarketplaceAgent.role == "publisher",
                MarketplaceAgent.creator_wallet == wallet.lower(),
                MarketplaceAgent.strategy_id == strategy_id,
                MarketplaceAgent.status == "running",
            )
            .first()
        )
        if pub_row is None:
            raise HTTPException(status_code=404, detail="Publisher not found or not owned by you")
        pub_row.status = "stopped"

        # Cascade retire to all running subscribers (TASK 18)
        sub_rows = (
            session.query(MarketplaceAgent)
            .filter(
                MarketplaceAgent.role == "subscriber",
                MarketplaceAgent.strategy_id == strategy_id,
                MarketplaceAgent.status == "running",
            )
            .all()
        )
        retired_sub_ids: list[str] = []
        for sub_row in sub_rows:
            sub_row.status = "retired"
            sub_row.stopped_at = datetime.now(UTC)
            retired_sub_ids.append(sub_row.sub_id)

        session.commit()

    if retired_sub_ids:
        logger.info(
            "Retired %d subscriber(s) for strategy %s: %s",
            len(retired_sub_ids),
            strategy_id,
            retired_sub_ids,
        )

    await market.stop_publisher(strategy_id)
    return {"status": "stopped", "strategy_id": strategy_id}


# ---------------------------------------------------------------------------
# GET /api/marketplace/published
# ---------------------------------------------------------------------------


@marketplace_router.get("/published")
@limiter.limit("30/minute")
async def list_published(request: Request, response: Response):
    """List all running publishers with subscriber counts."""
    market = _get_market(request)

    with get_session() as session:
        rows = (
            session.query(MarketplaceAgent)
            .filter(MarketplaceAgent.role == "publisher", MarketplaceAgent.status == "running")
            .all()
        )

    results = []
    for row in rows:
        # PUBLIC surface — attribution only (redaction by construction).
        d = row.public_dict()
        subs = market.publishers.get(row.strategy_id, None)
        d["subscriber_count"] = len(subs.subscribers) if subs else 0
        d["events"] = await market.state.get_events(row.strategy_id, count=5)
        results.append(d)

    return results


# ---------------------------------------------------------------------------
# GET /api/marketplace/my-published
# ---------------------------------------------------------------------------


@marketplace_router.get("/my-published")
async def list_my_published(
    request: Request,
    wallet: str = Depends(require_verified_wallet),
):
    """Same as GET /published but scoped to the caller's own publisher rows.

    Powers the 'Published' tab in Strategies.jsx.
    """
    market = _get_market(request)
    with get_session() as session:
        rows = (
            session.query(MarketplaceAgent)
            .filter(MarketplaceAgent.role == "publisher", MarketplaceAgent.creator_wallet == wallet.lower())
            .all()
        )
    results = []
    for row in rows:
        d = row.to_dict()
        subs = market.publishers.get(d["strategy_id"], None)
        d["subscriber_count"] = len(subs.subscribers) if subs else 0
        results.append(d)
    return results


# ---------------------------------------------------------------------------
# POST /api/marketplace/publish/{strategy_id}/withdraw
# ---------------------------------------------------------------------------


@marketplace_router.post("/publish/{strategy_id}/withdraw")
async def withdraw_publisher_earnings(
    strategy_id: str,
    request: Request,
    wallet: str = Depends(require_verified_wallet),
):
    """Manual creator-initiated disbursement (M1' fix). Runs Stage A+B+C for the
    caller's own publisher row, then calls PaymentSplitter.withdraw for the
    freshly-updated held_balance."""
    market = _get_market(request)
    with get_session() as session:
        pub = (
            session.query(MarketplaceAgent)
            .filter(
                MarketplaceAgent.role == "publisher",
                MarketplaceAgent.strategy_id == strategy_id,
                MarketplaceAgent.creator_wallet == wallet.lower(),
            )
            .first()
        )
    if pub is None:
        raise HTTPException(status_code=404, detail="No publisher row found for this strategy/wallet")

    sweeper = market._sweeper
    await sweeper.sweep_publisher(pub)  # Stage A + B, refresh pool state

    splitter_addr = market.settings.payment_splitter_address
    sp_c = market.loader._contract(splitter_addr, "PaymentSplitter")
    pool_data = await sp_c.functions.pools(to_bytes32(pub.pool_id)).call()
    held_balance = pool_data[4]  # (creator, platform, total_collected, total_disbursed, held_balance, active)

    if held_balance <= 0:
        return {"status": "nothing_to_withdraw", "held_balance": 0}

    tx = await sweeper.withdraw_publisher(pub, held_balance)
    if tx is None:
        raise HTTPException(status_code=502, detail="Withdraw failed — check logs")
    return {"status": "withdrawn", "amount_raw": held_balance, "tx_hash": tx}


# ---------------------------------------------------------------------------
# GET /api/marketplace/published/{strategy_id}
# ---------------------------------------------------------------------------


@marketplace_router.get("/published/{strategy_id}")
@limiter.limit("30/minute")
async def get_strategy_detail(request: Request, response: Response, strategy_id: str):
    """Get one published strategy + subscriber summaries + recent events."""
    market = _get_market(request)

    with get_session() as session:
        row = (
            session.query(MarketplaceAgent)
            .filter(
                MarketplaceAgent.role == "publisher",
                MarketplaceAgent.strategy_id == strategy_id,
                MarketplaceAgent.status == "running",
            )
            .first()
        )
        if row is None:
            raise HTTPException(status_code=404, detail=f"Publisher '{strategy_id}' not found")

        subscriber_count_db = (
            session.query(MarketplaceAgent)
            .filter(
                MarketplaceAgent.role == "subscriber",
                MarketplaceAgent.strategy_id == strategy_id,
                MarketplaceAgent.status == "running",
            )
            .count()
        )

    # PUBLIC endpoint (attribution only, redaction by construction — public_dict).
    # Subscriber IDENTITY is deliberately absent: subscribers are consumers, not
    # authors, so the public surface exposes only subscriber_count (data-arch
    # decision, PR #958 review). The full subscriber list is a separate,
    # authenticated view for the publisher + each subscriber's own row (tracked
    # follow-up). Never leak sub_id / ephemeral_wallet / payment plumbing here.
    d = row.public_dict()
    d["events"] = await market.state.get_events(strategy_id, count=50)

    pub = market.publishers.get(strategy_id)
    # Prefer the live engine count; fall back to the DB count if the publisher
    # has not been rehydrated into the in-process engine yet.
    d["subscriber_count"] = len(pub.subscribers) if pub else subscriber_count_db
    d["is_running"] = pub is not None

    return d


# ---------------------------------------------------------------------------
# GET /api/marketplace/my-subscriptions
# ---------------------------------------------------------------------------


@marketplace_router.get("/my-subscriptions")
async def my_subscriptions(
    _request: Request,
    wallet: str = Depends(require_verified_wallet),
):
    """Current wallet's subscriptions."""
    with get_session() as session:
        rows = (
            session.query(MarketplaceAgent)
            .filter(
                MarketplaceAgent.role == "subscriber",
                MarketplaceAgent.subscriber_wallet == wallet.lower(),
            )
            .order_by(MarketplaceAgent.created_at.desc())
            .all()
        )

    return [r.to_dict() for r in rows]
