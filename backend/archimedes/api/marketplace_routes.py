"""Marketplace routes — publish / subscribe / browse / x402 payment.

Session pattern: with get_session() as session (matches codebase convention).
pool_id is ALWAYS derived server-side via derive_pool_id (D-POOL).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from archimedes.api._route_helpers import strategy_provider
from archimedes.api.auth_siwe import require_verified_wallet
from archimedes.api.limiter import limiter
from archimedes.db import get_session
from archimedes.marketplace.encoding import derive_pool_id, to_bytes32
from archimedes.marketplace.service import MarketService, Subscriber
from archimedes.models.marketplace import MarketplaceAgent
from archimedes.models.strategy_generators import wallet_can_publish
from archimedes.models.strategy_store import StrategyRecord


# ─── x402 Gateway Models ────────────────────────────────────────────────


class PaymentNotification(BaseModel):
    """Webhook payload from the x402 payment gateway."""

    sub_id: str = Field(..., description="0x-hex subscriber ID (bytes32)")
    tx_hash: str = Field(default="", description="On-chain transaction hash")
    amount_usdc_raw: int = Field(default=0, description="Amount paid in USDC raw (6 decimals)")
    status: str = Field(default="confirmed", description="Payment status: confirmed | failed")


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
    body: dict,
    wallet: str = Depends(require_verified_wallet),
):
    """Publish a strategy to the marketplace.

    Body: {strategy_id, vault_address?, platform_wallet?}
    pool_id is DERIVED server-side (D-POOL). Never accept it from the client.
    """
    market = _get_market(request)
    strategy_id = body.get("strategy_id", "").strip()
    vault_address = body.get("vault_address", "").strip() or ""
    platform_wallet = body.get("platform_wallet", "").strip() or ""

    if not strategy_id:
        raise HTTPException(status_code=400, detail="strategy_id is required")

    # 0. Validate strategy exists in the provider
    if strategy_provider.get_strategy(strategy_id) is None:
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
        if not wallet_can_publish(session, strategy_id=strategy_id, wallet_address=wallet, is_example=record.is_example):
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

    # 4. Check on-chain pool status, then createPool if needed (owner key only)
    splitter_addr = market.settings.payment_splitter_address
    pool_already_active = False
    try:
        sp_c = market.loader._contract(splitter_addr, "PaymentSplitter")
        sp_data = await sp_c.functions.pools(to_bytes32(pool_id)).call()
        # pools returns (creator, platform, total_collected, total_disbursed, active)
        pool_already_active = bool(sp_data[4]) if len(sp_data) >= 5 else False
    except Exception:
        logger.warning("pools() staticcall failed; proceeding with createPool")

    if not pool_already_active:
        try:
            if market.signer.is_configured:
                await market.signer.execute_contract(
                    splitter_addr,
                    "createPool(bytes32,address,address)",
                    [to_bytes32(pool_id), wallet, platform_wallet or wallet],
                )
            else:
                c = market.loader._contract(splitter_addr, "PaymentSplitter")
                tx = await c.functions.createPool(
                    to_bytes32(pool_id), wallet, platform_wallet or wallet
                ).build_transaction(
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

    # 5. Insert publisher row — write pool_id into the real pool_id column
    with get_session() as session:
        agent = MarketplaceAgent(
            role="publisher",
            strategy_id=strategy_id,
            creator_wallet=wallet,
            pool_id=pool_id,
            vault_address=vault_address,
        )
        session.add(agent)
        session.commit()
        result = agent.to_dict()

    # 6. Start the publisher loop
    await market.start_publisher(strategy_id, pool_id, vault_address, wallet)

    result["pool_id"] = pool_id
    return result


# ---------------------------------------------------------------------------
# POST /api/marketplace/subscribe
# ---------------------------------------------------------------------------


@marketplace_router.post("/subscribe")
@limiter.limit("5/minute")
async def subscribe_strategy(
    request: Request,
    body: dict,
    wallet: str = Depends(require_verified_wallet),
):
    """Subscribe to a published strategy.

    Body: {strategy_id, pool_id, sub_id, ephemeral_wallet, initial_deposit_usdc}
    The browser wallet already called USDC.approve + SubscriptionManager.subscribe
    on-chain (D-SUB). The backend trusts the sub_id from the on-chain event.
    """
    market = _get_market(request)
    strategy_id = body.get("strategy_id", "").strip()
    pool_id = body.get("pool_id", "").strip()
    sub_id = body.get("sub_id", "").strip()
    ephemeral_wallet = body.get("ephemeral_wallet", "").strip()

    if not strategy_id or not pool_id or not sub_id or not ephemeral_wallet:
        raise HTTPException(status_code=400, detail="strategy_id, pool_id, sub_id, and ephemeral_wallet are required")

    # 0a. Validate sub_id format — must be 0x-prefixed 66-char hex (D-BYTES32)
    if not sub_id.startswith("0x") or len(sub_id) != 66:
        raise HTTPException(status_code=400, detail="sub_id must be a 0x-prefixed 32-byte hex string (66 chars)")
    try:
        int(sub_id, 16)
    except ValueError:
        raise HTTPException(status_code=400, detail="sub_id is not valid hex") from None
    # Reject all-zero sub_id (on-chain will never return this)
    if int(sub_id, 16) == 0:
        raise HTTPException(status_code=400, detail="sub_id cannot be zero")

    # 0b. Validate strategy exists in the provider (M2)
    if strategy_provider.get_strategy(strategy_id) is None:
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
                MarketplaceAgent.subscriber_wallet == wallet,
                MarketplaceAgent.strategy_id == strategy_id,
                MarketplaceAgent.status == "running",
            )
            .first()
        )
        if existing is not None:
            raise HTTPException(status_code=409, detail="Already subscribed to this strategy")

    # 3. Validate on-chain (skip in dry-run)
    if not market.dry_run:
        try:
            sm_addr = market.settings.subscription_manager_address
            c = market.loader._contract(sm_addr, "SubscriptionManager")
            sub_data = await c.functions.subscriptions(to_bytes32(sub_id)).call()
            # sub_data: (subscriber, pool_id, ephemeral_wallet, reserved_usdc, webhook_url, active, created_at)
            if len(sub_data) < 6 or not sub_data[5]:
                raise HTTPException(status_code=400, detail="Subscription not active on-chain")

            # P0 (H1): on-chain subscriber must match the authenticated caller
            onchain_subscriber = sub_data[0]
            if isinstance(onchain_subscriber, bytes):
                onchain_subscriber = "0x" + onchain_subscriber.hex()
            onchain_subscriber = onchain_subscriber.lower()
            if onchain_subscriber != wallet.lower():
                raise HTTPException(
                    status_code=403,
                    detail=f"On-chain subscriber ({onchain_subscriber}) does not match wallet ({wallet})",
                )

            # P0 (H1): on-chain pool_id must match the derived pool_id for this strategy
            onchain_pool_bytes = sub_data[1]
            derived_pool = derive_pool_id(strategy_id, pub_row.creator_wallet)
            if isinstance(onchain_pool_bytes, bytes) and onchain_pool_bytes != to_bytes32(derived_pool):
                raise HTTPException(
                    status_code=400,
                    detail="On-chain pool_id does not match derived pool_id for this strategy",
                )
        except HTTPException:
            raise
        except Exception as exc:
            logger.warning("on-chain subscription validation failed: %s", exc)
            raise HTTPException(status_code=400, detail="Could not validate subscription on-chain") from exc

    # 4. Create vault for subscriber if needed
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

    # 5. Insert subscriber row
    with get_session() as session:
        agent = MarketplaceAgent(
            role="subscriber",
            strategy_id=strategy_id,
            subscriber_wallet=wallet,
            sub_id=sub_id,
            pool_id=pool_id,
            vault_address=vault_address,
            ephemeral_wallet=ephemeral_wallet,
        )
        session.add(agent)
        session.commit()
        result = agent.to_dict()

    # 6. Register subscriber with the engine
    sub = Subscriber(
        sub_id=sub_id,
        pool_id=pool_id,
        vault_address=vault_address,
        ephemeral_wallet=ephemeral_wallet,
        subscriber_wallet=wallet,
    )
    await market.add_subscriber(strategy_id, sub)

    return result


# ---------------------------------------------------------------------------
# DELETE /api/marketplace/subscribe/{strategy_id}
# ---------------------------------------------------------------------------


@marketplace_router.delete("/subscribe/{strategy_id}")
async def unsubscribe_strategy(
    request: Request,
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
                MarketplaceAgent.subscriber_wallet == wallet,
                MarketplaceAgent.strategy_id == strategy_id,
                MarketplaceAgent.status == "running",
            )
            .first()
        )
        if sub_row is None:
            raise HTTPException(status_code=404, detail="Active subscription not found")
        sub_row.status = "stopped"
        session.commit()
        sub_id = sub_row.sub_id

    await market.remove_subscriber(strategy_id, sub_id)
    return {"status": "unsubscribed", "strategy_id": strategy_id}


# ---------------------------------------------------------------------------
# POST /api/marketplace/payment-webhook  (x402 gateway callback)
# ---------------------------------------------------------------------------


@marketplace_router.post("/payment-webhook")
async def payment_webhook(
    request: Request,
    body: PaymentNotification,
):
    """Receive x402 gateway payment confirmation.

    Called by the x402 payment gateway when a subscriber's off-chain
    payment is confirmed.  Records the payment in Redis so the next
    tick can verify it without an on-chain ``chargeActions`` call.

    This endpoint is intentionally **unauthenticated** — the gateway
    signs requests out of band; in production, validate a shared secret
    or HMAC header before processing.
    """
    market = _get_market(request)

    if body.status != "confirmed":
        logger.info("x402 payment not confirmed for %s (status=%s)", body.sub_id, body.status)
        return {"status": "ignored", "sub_id": body.sub_id}

    await market.state.save_payment(
        body.sub_id,
        {
            "paid": True,
            "amount_usdc_raw": body.amount_usdc_raw,
            "tx_hash": body.tx_hash,
            "gateway_status": body.status,
        },
    )
    logger.info(
        "x402 payment recorded for %s (amount=%d, tx=%s)",
        body.sub_id, body.amount_usdc_raw, body.tx_hash,
    )
    return {"status": "recorded", "sub_id": body.sub_id}


# ---------------------------------------------------------------------------
# DELETE /api/marketplace/publish/{strategy_id}
# ---------------------------------------------------------------------------


@marketplace_router.delete("/publish/{strategy_id}")
async def stop_publish(
    request: Request,
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
                MarketplaceAgent.creator_wallet == wallet,
                MarketplaceAgent.strategy_id == strategy_id,
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
            len(retired_sub_ids), strategy_id, retired_sub_ids,
        )

    await market.stop_publisher(strategy_id)
    return {"status": "stopped", "strategy_id": strategy_id}


# ---------------------------------------------------------------------------
# GET /api/marketplace/published
# ---------------------------------------------------------------------------


@marketplace_router.get("/published")
async def list_published(request: Request):
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
        d = row.to_dict()
        subs = market.publishers.get(d["strategy_id"], None)
        d["subscriber_count"] = len(subs.subscribers) if subs else 0
        d["events"] = await market.state.get_events(d["strategy_id"], count=5)
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
# GET /api/marketplace/published/{strategy_id}
# ---------------------------------------------------------------------------


@marketplace_router.get("/published/{strategy_id}")
async def get_strategy_detail(request: Request, strategy_id: str):
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

        subscriber_rows = (
            session.query(MarketplaceAgent)
            .filter(
                MarketplaceAgent.role == "subscriber",
                MarketplaceAgent.strategy_id == strategy_id,
            )
            .all()
        )

    d = row.to_dict()
    d["subscribers"] = [s.to_dict() for s in subscriber_rows]
    d["events"] = await market.state.get_events(strategy_id, count=50)

    pub = market.publishers.get(strategy_id)
    d["subscriber_count"] = len(pub.subscribers) if pub else 0
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
                MarketplaceAgent.subscriber_wallet == wallet,
            )
            .order_by(MarketplaceAgent.created_at.desc())
            .all()
        )

    return [r.to_dict() for r in rows]
