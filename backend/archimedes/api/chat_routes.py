"""Chat API routes — per-vault chat for the Archimedes marketplace.

Endpoints:
  GET    /api/vaults/{address}/chat       — list messages (paginated)
  POST   /api/vaults/{address}/chat       — post a message
  GET    /api/vaults/{address}/chat/count  — message count

Design:
  - Reads stay public.
  - Writes require Better Auth account plus selected verified linked wallet.
    Body wallet cannot override server-resolved link; stored attribution is verified.
  - AI auto-responds to @archimedes mentions.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response

from archimedes.api.auth_guard import require_internal_agent_key
from archimedes.api.wallet_routes import require_linked_wallet
from archimedes.api.limiter import limiter
from archimedes.api.schemas import (
    ChatMessageListResponse,
    ChatMessageResponse,
    ChatPostRequest,
    ChatPostResponse,
)
from archimedes.services.chat_service import chat_service

chat_router = APIRouter(prefix="/api/vaults", tags=["chat"])


@chat_router.get("/{address}/chat", response_model=ChatMessageListResponse)
async def get_chat_messages(
    address: str,
    limit: int = Query(50, ge=1, le=200),
    before_id: int | None = Query(None, description="For pagination — get messages before this ID"),
):
    """Get chat messages for a vault, oldest-first (chat display order)."""
    messages = chat_service.get_messages(
        vault_address=address,
        limit=limit,
        before_id=before_id,
    )
    total = chat_service.get_message_count(address)
    has_more = total > limit and (before_id is None or before_id > 1)

    return ChatMessageListResponse(
        messages=[
            ChatMessageResponse(
                id=m["id"],
                vault_address=m["vault_address"],
                wallet_address=m["wallet_address"],
                message=m["message"],
                is_ai=m["is_ai"],
                verified=m.get("verified", False),
                created_at=m["created_at"],
            )
            for m in messages
        ],
        total=total,
        has_more=has_more,
    )


@chat_router.post("/{address}/chat", response_model=ChatPostResponse)
@limiter.limit("20/minute")
async def post_chat_message(
    address: str,
    body: ChatPostRequest,
    request: Request,  # noqa: ARG001 — slowapi @limiter.limit inspects param name
    response: Response,  # noqa: ARG001
    session_wallet: str = Depends(require_linked_wallet),
):
    """Post as a verified wallet linked to the canonical account."""
    if not body.message or not body.message.strip():
        raise HTTPException(status_code=400, detail="Message cannot be empty")
    if len(body.message) > 2000:
        raise HTTPException(status_code=400, detail="Message too long (max 2000 chars)")

    if body.wallet_address and body.wallet_address.lower() != session_wallet:
        raise HTTPException(status_code=403, detail="wallet_address is not linked to the authenticated account")
    wallet_address = session_wallet
    verified = True

    # post_message is fully synchronous and, when @archimedes is mentioned, makes
    # a blocking Anthropic call plus sync DB writes. Calling it directly inside
    # this async route would block the event loop and serialize concurrent
    # requests. Offload the whole sync chain to a worker thread.
    result = await asyncio.to_thread(
        chat_service.post_message,
        vault_address=address,
        wallet_address=wallet_address,
        message=body.message.strip(),
        verified=verified,
    )

    ai_response = None
    if result.get("_ai_response"):
        ai_data = result["_ai_response"]
        ai_response = ChatMessageResponse(
            id=ai_data["id"],
            vault_address=ai_data["vault_address"],
            wallet_address=ai_data["wallet_address"],
            message=ai_data["message"],
            is_ai=ai_data["is_ai"],
            verified=ai_data.get("verified", False),
            created_at=ai_data["created_at"],
        )

    return ChatPostResponse(
        message=ChatMessageResponse(
            id=result["id"],
            vault_address=result["vault_address"],
            wallet_address=result["wallet_address"],
            message=result["message"],
            is_ai=result["is_ai"],
            verified=result.get("verified", False),
            created_at=result["created_at"],
        ),
        ai_response=ai_response,
    )


@chat_router.get("/{address}/chat/count")
async def get_chat_count(address: str):
    """Get total message count for a vault."""
    count = chat_service.get_message_count(address)
    return {"vault_address": address, "message_count": count}


# ── Agent event endpoints (called by agent runner) ───────────


@chat_router.post("/{address}/chat/rebalance")
async def post_rebalance_event(address: str, body: dict, _: None = Depends(require_internal_agent_key)):
    """Post a rebalance event from the agent runner.

    Internal-only: requires X-Internal-Agent-Key header.

    Body: {"reasoning": "...", "trades": [{"direction": "sell", "amount": 1000, "symbol": "sTSLA"}]}
    """
    reasoning = body.get("reasoning", "Portfolio rebalanced")
    trades = body.get("trades")
    result = chat_service.post_rebalance_event(address, reasoning, trades)
    if result is None:
        from fastapi import HTTPException

        raise HTTPException(status_code=500, detail="Failed to post rebalance event")
    return result


@chat_router.post("/{address}/chat/regime-change")
async def post_regime_change(address: str, body: dict, _: None = Depends(require_internal_agent_key)):
    """Post a regime change event from the agent runner.

    Internal-only: requires X-Internal-Agent-Key header.

    Body: {"old_regime": "risk_on", "new_regime": "risk_off", "confidence": 0.85}
    """
    result = chat_service.post_regime_change(
        address,
        old_regime=body.get("old_regime", "unknown"),
        new_regime=body.get("new_regime", "unknown"),
        confidence=body.get("confidence", 0.0),
    )
    if result is None:
        from fastapi import HTTPException

        raise HTTPException(status_code=500, detail="Failed to post regime change")
    return result
