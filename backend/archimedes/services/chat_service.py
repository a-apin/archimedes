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
from archimedes.services.identity_events import emit_identity_event, ensure_wallet_identity

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

# Use the Circle dev-controlled wallet as the AI's identity in chat.
# Falls back to a labelled placeholder if the wallet address isn't configured.
# Lowercased (issue #1028): chat_messages.wallet_address now FKs to
# wallet_identities, whose primary key is enforced lowercase — an
# operator-supplied WALLET_ADDRESS env var isn't guaranteed to be.
AI_WALLET_ADDRESS = os.getenv(
    "WALLET_ADDRESS", "0xc221dcd6fe7d81ff741f94c08e61f52bea1f9ac9"
).lower()  # Circle agent wallet for AI identity

# Optional per-surface model override for vault chat. When unset (the default),
# chat rides the same cheap env-resolved model as the rest of the app via
# make_llm_backend() — NO hardcoded premium literal. Set CHAT_MODEL only if the
# chat persona genuinely needs a stronger (paid-tier) model than the generate
# default; it must be a model id the configured provider can serve.
CHAT_MODEL = os.getenv("CHAT_MODEL", "").strip() or None


def _curated_rigor_statuses(strategies: list) -> dict[str, str]:
    """LIVE tri-state rigor statuses ("pass"|"fail"|"pending") for chat context.

    ``Strategy.passes_rigor_gate`` on the in-memory provider object is a
    fail-closed sentinel (always ``False``, #821) — presenting it as a verdict
    told the vault-chat LLM that every curated strategy had failed the gate.
    This reuses the library list's memoized live-gate batch over the full
    library cohort (same cohort context, same cache entry, so a warm library
    page makes this free) and reduces each requested strategy to its tri-state
    status. Any failure returns ``{}`` and the caller omits the rigor line
    entirely — this context builder's law is "missing data is omitted, never
    invented".
    """
    if not strategies:
        return {}
    try:
        # Service→api import mirrors the existing precedent in
        # live_rigor_gate._load_strategy_code_safe; local so module import stays light.
        from archimedes.api.strategies_routes import (
            _live_rigor_results_for_strategies,
            _verdict_from_result,
        )
        from archimedes.services.strategy_provider import default_provider

        cohort = list(default_provider().list_strategies())
        cohort_ids = {c.id for c in cohort}
        cohort.extend(s for s in strategies if s.id not in cohort_ids)
        results = _live_rigor_results_for_strategies(cohort)
        return {s.id: _verdict_from_result(results.get(s.id)).status for s in strategies}
    except Exception:
        logger.debug("live rigor statuses for chat context failed (line omitted)", exc_info=True)
        return {}


def _generated_context_lines(strategy_id: str) -> list[str]:
    """Curated-provider miss → look up a GENERATED strategy directly from the
    unified strategy_passports store, so a generated-strategy vault's chat gets
    real context instead of silence (the "unify source" decouple —
    docs/CURATED-STRATEGY-DECOUPLE-AND-CONSOLIDATE-2026-07-08.md Part A).

    No additional ownership check is applied: the vault was already bound to
    this strategy_id at deploy time (VaultMetadata.get_strategy_ids()), so
    reading it back here crosses no new trust boundary. Fail-safe: any lookup
    error yields no extra lines, never a fabricated one.
    """
    try:
        from archimedes.db import get_session
        from archimedes.services.passport_loader import get_passport

        with get_session() as session:
            record = get_passport(session, strategy_id)
            if record is None:
                return []
            d = record.to_dict()
            lines: list[str] = []
            paper_refs = d.get("paper_refs") or []
            title = (paper_refs[0].get("title") if paper_refs else None) or (d.get("methodology_summary") or "")[:60]
            if title:
                lines.append(f"Strategy: {title}")
            if d.get("methodology_summary"):
                lines.append(f"Methodology: {d['methodology_summary'][:400]}")
            if d.get("asset_universe"):
                lines.append(f"Assets: {', '.join(d['asset_universe'])}")
            rigor = "passed" if d.get("passes_rigor_gate") else "not passed"
            lines.append(f"Rigor gate: {rigor}")
            return lines
    except Exception:
        logger.debug("generated-strategy chat context lookup failed for %s (non-fatal)", strategy_id, exc_info=True)
        return []


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
        verified: bool = False,
    ) -> dict:
        """Post a user message and optionally trigger an AI response.

        `verified` is True only when caller proved and linked wallet to account;
        body-supplied identities stay False.
        """
        # Identity ledger (#1028, D1/D2): chat allows unverified attribution
        # (`verified` above) — unlike every other wallet-FK'd write path in
        # this codebase, this wallet may never have been proof-linked, so
        # nothing else guarantees it's already anchored. Anchor it BEFORE the
        # insert below: chat_messages.wallet_address FKs to wallet_identities
        # (models/chat.py) on Postgres, so anchoring it after the fact would
        # be too late — the insert itself would violate the FK. Fail-safe;
        # never blocks the request (worst case on a DB-down race: the FK
        # trips and this post fails exactly as it would have before #1028).
        ensure_wallet_identity(wallet_address, "human")

        session = get_session()
        try:
            msg = ChatMessage(
                vault_address=vault_address.lower(),
                wallet_address=wallet_address.lower(),
                message=message,
                is_ai=False,
                verified=verified,
                created_at=datetime.now(UTC),
            )
            session.add(msg)
            session.commit()
            session.refresh(msg)

            result = msg.to_dict()

            # Ledger the post itself (D2). The anchor above already
            # guarantees the wallet exists, so this is just an append.
            emit_identity_event(
                wallet=wallet_address,
                event_type="chat_posted",
                actor_class="human",
                meta={"vault_address": vault_address, "verified": verified},
            )

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
        # Identity ledger (#1028, D1/D3): the AI persona is a first-class agent
        # actor, anchored as actor_class='agent' BEFORE the insert below — same
        # FK-ordering reason as post_message (chat_messages.wallet_address FKs
        # to wallet_identities). Fail-safe; never blocks the post.
        ensure_wallet_identity(AI_WALLET_ADDRESS, "agent")

        session = get_session()
        try:
            msg = ChatMessage(
                vault_address=vault_address.lower(),
                wallet_address=AI_WALLET_ADDRESS,
                message=message,
                is_ai=True,
                verified=True,  # backend-authored — identity is the configured agent wallet
                created_at=datetime.now(UTC),
            )
            session.add(msg)
            session.commit()
            session.refresh(msg)

            result = msg.to_dict()
            result["trigger"] = trigger

            # Ledger the post itself (D2). The anchor above already
            # guarantees the wallet exists, so this is just an append.
            emit_identity_event(
                wallet=AI_WALLET_ADDRESS,
                event_type="chat_posted",
                actor_class="agent",
                meta={"vault_address": vault_address, "trigger": trigger},
            )

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
        wallet_address: str,  # noqa: ARG002 — accepted for future per-wallet personalization; current body uses vault_address + user_message only
    ) -> dict | None:
        """Generate an AI response via the provider-agnostic LLM backend.

        Routes through ``make_llm_backend(model=CHAT_MODEL)`` instead of a
        hardcoded premium Claude literal, so the cheap env default applies and
        the model is configurable (cost leak fixed). ``CHAT_MODEL`` is an
        opt-in named override for callers that want a stronger model for chat.
        """
        from archimedes.services.llm_backend import make_llm_backend

        backend = make_llm_backend(model=CHAT_MODEL)
        if not getattr(backend, "available", False):
            logger.warning("No LLM backend available — returning canned AI response")
            return self._canned_response(vault_address, user_message)

        try:
            # Get recent chat context (last 5 messages)
            recent = self.get_messages(vault_address, limit=10)
            lines = []
            for m in recent[-5:]:
                if m["is_ai"]:
                    lines.append(f"🤖 Archimedes: {m['message']}")
                else:
                    lines.append(f"👤 {m['wallet_address'][:10]}...: {m['message']}")
            context = "\n".join(lines)

            # Inject vault-specific context to prevent hallucination (#386)
            vault_context = self._build_vault_context(vault_address)

            user_prompt = (
                f"Vault: {vault_address}\n"
                f"{vault_context}\n"
                f"<chat_history>\n{context}\n</chat_history>\n\n"
                f"<user_message>{user_message}</user_message>"
            )

            ai_text = (backend.complete(AI_SYSTEM_PROMPT, user_prompt) or "").strip()
            if not ai_text:
                ai_text = "I'm analyzing the portfolio. Give me a moment."
            return self.post_ai_message(vault_address, ai_text, trigger="mention")

        except Exception:
            logger.exception("LLM call failed — falling back to canned response")
            return self._canned_response(vault_address, user_message)

    def _build_vault_context(self, vault_address: str) -> str:
        """Build vault-specific context for the LLM prompt (Issue #386).

        Fetches strategy names, methodology, assets, and rigor verdict so the
        model answers about THIS vault's actual holdings, not hallucinated ones.
        Each lookup is fail-safe — missing data is omitted, never invented.
        """
        parts = []
        try:
            from sqlalchemy import func

            from archimedes.db import get_session
            from archimedes.models.chat import VaultMetadata

            session = get_session()
            try:
                # VaultMetadata rows can be inserted in checksum-case (see
                # vaults_routes.py — no normalization at write). MetaMask hands
                # checksum addresses to the chat route too. Compare lowercase
                # to lowercase so the lookup actually finds the row.
                meta = (
                    session.query(VaultMetadata)
                    .filter(func.lower(VaultMetadata.vault_address) == vault_address.lower())
                    .first()
                )
                if meta:
                    parts.append(f"Vault name: {meta.name or vault_address[:10]}")
                    strategy_ids = meta.get_strategy_ids() if meta else []
                    if strategy_ids:
                        from archimedes.services.strategy_provider import default_provider

                        provider = default_provider()
                        found = [(sid, provider.get_strategy(sid)) for sid in strategy_ids[:3]]
                        # LIVE tri-state verdicts, batched once (M2 fix). The
                        # in-memory passes_rigor_gate is a fail-closed sentinel,
                        # not a verdict — the old line told the LLM every curated
                        # strategy was "not passed".
                        rigor_statuses = _curated_rigor_statuses([s for _, s in found if s])
                        rigor_labels = {
                            "pass": "passed (live gate)",
                            "fail": "failed (live gate)",
                            "pending": "pending — no live backtest verdict yet",
                        }
                        for sid, s in found:
                            if s:
                                parts.append(f"Strategy: {s.paper_title}")
                                if s.methodology_summary:
                                    parts.append(f"Methodology: {s.methodology_summary[:400]}")
                                if s.asset_universe:
                                    parts.append(f"Assets: {', '.join(s.asset_universe)}")
                                label = rigor_labels.get(rigor_statuses.get(s.id, ""))
                                if label:
                                    parts.append(f"Rigor gate: {label}")
                                # No live status (batch failed) → the rigor line is
                                # omitted, never invented from the sentinel.
                            else:
                                # Curated provider miss — GENERATED strategy vault
                                # (unify-source decouple): fall back to the unified
                                # strategy_passports store instead of leaving this
                                # vault's chat with no strategy context at all.
                                parts.extend(_generated_context_lines(sid))
            finally:
                session.close()
        except Exception as exc:
            logger.debug("vault context fetch failed (non-fatal): %s", exc)

        if not parts:
            return "<vault_context>No metadata available for this vault.</vault_context>"
        return "<vault_context>\n" + "\n".join(parts) + "\n</vault_context>"

    # Prefix that makes every fallback message visibly NON-AUTHORITATIVE, so a
    # creds-less visitor never mistakes a canned string for a freshly-reasoned
    # live answer (issue #752 — "claims must be true"). The live assistant did
    # NOT run; this is static, non-personalized info, and it must read that way.
    _FALLBACK_PREFIX = "⚠️ _The live assistant is temporarily unavailable — this is a static, non-personalized message (no live AI ran)._\n\n"

    def _canned_response(self, vault_address: str, user_message: str) -> dict | None:
        """Static fallback when the LLM backend is unavailable or errors.

        CRITICAL (issue #752): this path does NOT run the live agent, so it must
        not assert product guarantees (rigor controls, on-chain anchoring) as
        freshly-verified live output. Every message is prefixed as a static
        offline notice, and the body points the user at the real, independently
        verifiable surfaces (the strategy passport, the Traces tab) instead of
        restating those guarantees as a fact the agent just established.
        """
        msg_lower = user_message.lower()

        if any(w in msg_lower for w in ["performance", "return", "pnl", "profit"]):
            body = (
                "I can't pull live numbers right now. This vault's performance and every "
                "rebalance decision are recorded in the Traces tab — open it to read the "
                "history and verify it yourself."
            )
        elif any(w in msg_lower for w in ["rebalance", "adjust", "change"]):
            body = (
                "I can't analyze the live portfolio right now. Past rebalances and the "
                "reasoning behind them are listed in the Traces tab when you're ready to review."
            )
        elif any(w in msg_lower for w in ["risk", "safe", "dangerous"]):
            body = (
                "I can't give a live risk read right now. Each strategy's rigor checks and "
                "their results are shown on its strategy passport — that's the place to "
                "confirm what controls it actually passed."
            )
        elif any(w in msg_lower for w in ["strategy", "paper", "research"]):
            body = (
                "I can't look up this vault's strategies live right now. Each one carries a "
                "strategy passport with its source paper, backtest results, and paper-claim "
                "deltas — check the passport for the verifiable details."
            )
        elif any(w in msg_lower for w in ["hello", "hi", "hey", "what"]):
            body = (
                "Hi — I'm Archimedes, the vault's AI portfolio manager, but I'm offline at "
                "the moment so I can't answer live. Try again shortly, or browse the strategy "
                "passport and Traces tab in the meantime."
            )
        else:
            body = (
                "I'm offline right now and can't answer live. Try again shortly, or explore "
                "this vault's strategy passport and Traces tab for the recorded details."
            )

        return self.post_ai_message(vault_address, self._FALLBACK_PREFIX + body, trigger="mention")

    def get_message_count(self, vault_address: str) -> int:
        """Get total message count for a vault."""
        session = get_session()
        try:
            return session.query(ChatMessage).filter(ChatMessage.vault_address == vault_address.lower()).count()
        finally:
            session.close()


# Singleton
chat_service = ChatService()
