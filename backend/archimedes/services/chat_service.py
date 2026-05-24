"""Chat service — business logic for per-vault chat.

Handles:
  - Message persistence
  - Paginated message retrieval
  - AI response generation (Claude API) for @archimedes mentions
  - Auto-post on rebalance/regime events
"""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime

from archimedes.db import get_session
from archimedes.models.chat import ChatMessage

logger = logging.getLogger(__name__)

# AI persona for @archimedes mentions
AI_SYSTEM_PROMPT = """You are Archimedes, an AI portfolio manager for a DeFi vault on Arc blockchain.

You are speaking in a vault chat room. Be:
- Concise (2–3 sentences max, chat format)
- Informative about portfolio decisions, market conditions, strategy reasoning
- Professional but approachable
- Honest about risks and uncertainties

You have access to on-chain reasoning traces that explain every rebalance decision.
Reference academic research when discussing strategy choices.
Never promise returns. Always frame in terms of process and rigor."""

AI_WALLET_ADDRESS = "0x0000000000000000000000000000000000000000"  # Placeholder for AI identity


class ChatService:
    """Manages per-vault chat messages and AI responses."""

    def get_messages(
        self,
        vault_address: str,
        limit: int = 50,
        before_id: int | None = None,
    ) -> list[dict]:
        """Get messages for a vault, newest last (chat scroll-up pattern)."""
        session = get_session()
        try:
            query = session.query(ChatMessage).filter(ChatMessage.vault_address == vault_address.lower())

            if before_id:
                query = query.filter(ChatMessage.id < before_id)

            # Get up to `limit` messages, ordered oldest-first for display
            messages = query.order_by(ChatMessage.created_at.desc()).limit(limit).all()
            # Reverse so newest is at the bottom
            messages.reverse()
            return [m.to_dict() for m in messages]
        finally:
            session.close()

    def post_message(
        self,
        vault_address: str,
        wallet_address: str,
        message: str,
    ) -> dict:
        """Post a user message and optionally trigger an AI response."""
        session = get_session()
        try:
            msg = ChatMessage(
                vault_address=vault_address.lower(),
                wallet_address=wallet_address.lower(),
                message=message,
                is_ai=False,
                created_at=datetime.now(UTC),
            )
            session.add(msg)
            session.commit()
            session.refresh(msg)

            result = msg.to_dict()

            # Check for @archimedes mention — trigger AI response
            if "@archimedes" in message.lower():
                ai_response = self._generate_ai_response(vault_address, message, wallet_address)
                if ai_response:
                    result["_ai_response"] = ai_response

            return result
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def post_ai_message(
        self,
        vault_address: str,
        message: str,
        trigger: str = "mention",
    ) -> dict | None:
        """Post an AI-generated message (rebalance event, regime change, or mention response)."""
        session = get_session()
        try:
            msg = ChatMessage(
                vault_address=vault_address.lower(),
                wallet_address=AI_WALLET_ADDRESS,
                message=message,
                is_ai=True,
                created_at=datetime.now(UTC),
            )
            session.add(msg)
            session.commit()
            session.refresh(msg)

            result = msg.to_dict()
            result["trigger"] = trigger
            return result
        except Exception:
            session.rollback()
            logger.exception("Failed to post AI message")
            return None
        finally:
            session.close()

    def post_rebalance_event(
        self,
        vault_address: str,
        reasoning: str,
        trades: list[dict] | None = None,
    ) -> dict | None:
        """Auto-post a rebalance event in the vault chat (Tier 1 vaults).

        Called by the agent runner after a successful rebalance.
        """
        trade_summary = ""
        if trades:
            trade_lines = [
                f"  • {t.get('direction', '?').upper()} {t.get('amount', '?')} {t.get('symbol', '?')}"
                for t in trades[:5]
            ]
            trade_summary = "\n" + "\n".join(trade_lines)

        message = (
            f"🔄 **Rebalance executed**\n"
            f"{reasoning}{trade_summary}\n\n"
            f"_Reasoning trace anchored on-chain. View in the Traces tab._"
        )
        return self.post_ai_message(vault_address, message, trigger="rebalance")

    def post_regime_change(
        self,
        vault_address: str,
        old_regime: str,
        new_regime: str,
        confidence: float,
    ) -> dict | None:
        """Auto-post a regime change event."""
        message = (
            f"⚡ **Regime change detected**\n"
            f"Market shifted from **{old_regime}** → **{new_regime}** "
            f"(confidence: {confidence:.0%})\n\n"
            f"_Portfolio will be adjusted accordingly._"
        )
        return self.post_ai_message(vault_address, message, trigger="regime_change")

    def _generate_ai_response(
        self,
        vault_address: str,
        user_message: str,
        wallet_address: str,
    ) -> dict | None:
        """Generate an AI response using Claude or GLM API."""
        import anthropic

        api_key = os.getenv("ANTHROPIC_API_KEY", "")
        auth_token = os.getenv("ANTHROPIC_AUTH_TOKEN", "")
        base_url = os.getenv("ANTHROPIC_BASE_URL", "")
        if api_key:
            client: anthropic.Anthropic | None = anthropic.Anthropic(api_key=api_key)
        elif auth_token and base_url:
            client = anthropic.Anthropic(auth_token=auth_token, base_url=base_url)
        else:
            client = None
        if client is None:
            logger.warning("No LLM credentials set — returning canned AI response")
            return self._canned_response(vault_address, user_message)

        try:
            # Get recent chat context (last 10 messages)
            recent = self.get_messages(vault_address, limit=10)
            lines = []
            for m in recent[-5:]:
                if m["is_ai"]:
                    lines.append(f"🤖 Archimedes: {m['message']}")
                else:
                    lines.append(f"👤 {m['wallet_address'][:10]}...: {m['message']}")
            context = "\n".join(lines)

            response = client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=300,
                system=AI_SYSTEM_PROMPT,
                messages=[
                    {
                        "role": "user",
                        "content": f"Vault: {vault_address}\nRecent chat:\n{context}\n\nUser asks: {user_message}",
                    }
                ],
            )

            ai_text = (
                response.content[0].text.strip()
                if response.content
                else "I'm analyzing the portfolio. Give me a moment."
            )
            return self.post_ai_message(vault_address, ai_text, trigger="mention")

        except Exception:
            logger.exception("Claude API call failed — falling back to canned response")
            return self._canned_response(vault_address, user_message)

    def _canned_response(self, vault_address: str, user_message: str) -> dict | None:
        """Fallback AI responses when Claude API is unavailable."""
        msg_lower = user_message.lower()

        if any(w in msg_lower for w in ["performance", "return", "pnl", "profit"]):
            text = "Portfolio performance is tracked on-chain via reasoning traces. Every rebalance decision is anchored with a verifiable hash on Arc. Check the Traces tab for the full history."
        elif any(w in msg_lower for w in ["rebalance", "adjust", "change"]):
            text = "Rebalances are triggered by regime detection and strategy rotation signals. I execute them with full reasoning traces — the why, not just the what."
        elif any(w in msg_lower for w in ["risk", "safe", "dangerous"]):
            text = "Every Tier 1 strategy passes four selection-bias controls (DSR, PBO, walk-forward OOS, look-ahead audit) before admission. Rigor is the wedge."
        elif any(w in msg_lower for w in ["strategy", "paper", "research"]):
            text = "Our strategies are grounded in peer-reviewed quantitative finance research. Each one carries a strategy passport with backtest results and paper-claim deltas."
        elif any(w in msg_lower for w in ["hello", "hi", "hey", "what"]):
            text = "Hey! 👋 I'm Archimedes, your AI portfolio manager. Ask me about this vault's strategy, recent rebalances, or the research backing our positions."
        else:
            text = "Good question. I'm monitoring the portfolio and market conditions. Feel free to ask about specific strategies, performance, or our research-backed approach."

        return self.post_ai_message(vault_address, text, trigger="mention")

    def get_message_count(self, vault_address: str) -> int:
        """Get total message count for a vault."""
        session = get_session()
        try:
            return session.query(ChatMessage).filter(ChatMessage.vault_address == vault_address.lower()).count()
        finally:
            session.close()


# Singleton
chat_service = ChatService()
