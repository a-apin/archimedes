"""Chat API routes — per-vault chat for the Archimedes marketplace.

Endpoints:
  GET    /api/vaults/{address}/chat       — list messages (paginated)
  POST   /api/vaults/{address}/chat       — post a message
  GET    /api/vaults/{address}/chat/count  — message count

Design (per ecosystem-design-spec.md § 16–17):
  - Fully open: any connected wallet can read/write
  - Wallet address = identity
  - AI auto-responds to @archimedes mentions
"""

from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response

from archimedes.api.auth_guard import require_internal_agent_key
from archimedes.api.limiter import limiter
from archimedes.api.schemas import (
    ChatMessageListResponse,
    ChatMessageResponse,
    ChatPostRequest,
    ChatPostResponse,
)
from archimedes.services.chat_service import chat_service

_WALLET_RE = re.compile(r"^0x[a-fA-F0-9]{40}$")

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
):
    """Post a message to a vault's chat. Triggers AI response if @archimedes is mentioned."""
    if not body.message or not body.message.strip():
        raise HTTPException(status_code=400, detail="Message cannot be empty")
    if not body.wallet_address or not _WALLET_RE.match(body.wallet_address):
        raise HTTPException(status_code=422, detail="wallet_address must match ^0x[a-fA-F0-9]{40}$")
    if len(body.message) > 2000:
        raise HTTPException(status_code=400, detail="Message too long (max 2000 chars)")

    result = chat_service.post_message(
        vault_address=address,
        wallet_address=body.wallet_address,
        message=body.message.strip(),
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
            created_at=ai_data["created_at"],
        )

    return ChatPostResponse(
        message=ChatMessageResponse(
            id=result["id"],
            vault_address=result["vault_address"],
            wallet_address=result["wallet_address"],
            message=result["message"],
            is_ai=result["is_ai"],
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
