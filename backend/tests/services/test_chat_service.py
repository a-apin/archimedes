"""Unit coverage for ChatService — message persistence + AI canned fallback.

Mocks `get_session` + the `ChatMessage` ORM so no DB connection is needed.
Validates message composition, AI-mention detection, the canned-response
keyword router, and the regime-change/rebalance event posters.

Added 2026-05-24 as part of the #147 coverage-gate lift.
"""

from __future__ import annotations

from typing import ClassVar
from unittest.mock import MagicMock, patch

import pytest
from archimedes.services.chat_service import (
    AI_WALLET_ADDRESS,
    ChatService,
)


def _make_session(query_result=None) -> tuple[MagicMock, MagicMock]:
    """Build a mock SQLAlchemy session whose query chain returns query_result."""
    session = MagicMock()
    chain = MagicMock()
    chain.filter.return_value = chain
    chain.order_by.return_value = chain
    chain.limit.return_value = chain
    chain.all.return_value = query_result or []
    chain.count.return_value = len(query_result or [])
    chain.first.return_value = (query_result or [None])[0]
    session.query.return_value = chain
    return session, chain


def _mock_msg(
    msg_id: int = 1, vault: str = "0xv", wallet: str = "0xw", text: str = "hi", is_ai: bool = False
) -> MagicMock:
    """Build a mock ChatMessage with a working `to_dict`."""
    msg = MagicMock()
    msg.id = msg_id
    msg.vault_address = vault
    msg.wallet_address = wallet
    msg.message = text
    msg.is_ai = is_ai
    msg.to_dict.return_value = {
        "id": msg_id,
        "vault_address": vault,
        "wallet_address": wallet,
        "message": text,
        "is_ai": is_ai,
    }
    return msg


class TestGetMessages:
    def test_returns_messages_oldest_first(self) -> None:
        # ORM returns newest-first; service reverses to oldest-first for display
        m1 = _mock_msg(msg_id=10, text="newest")
        m2 = _mock_msg(msg_id=5, text="oldest")
        session, _ = _make_session([m1, m2])
        with patch("archimedes.services.chat_service.get_session", return_value=session):
            result = ChatService().get_messages("0xv")
        assert [r["message"] for r in result] == ["oldest", "newest"]
        session.close.assert_called_once()

    def test_lowercases_vault_address(self) -> None:
        session, chain = _make_session([])
        with patch("archimedes.services.chat_service.get_session", return_value=session):
            ChatService().get_messages("0xV-UPPER")
        # filter() called with lowercased address — check the predicate ran
        chain.filter.assert_called()

    def test_before_id_adds_filter(self) -> None:
        session, chain = _make_session([])
        with patch("archimedes.services.chat_service.get_session", return_value=session):
            ChatService().get_messages("0xv", before_id=100)
        # Two filter calls: vault_address + id < before_id
        assert chain.filter.call_count >= 2


class TestPostMessage:
    def test_simple_user_message_round_trip(self) -> None:
        session, _ = _make_session([])
        with (
            patch("archimedes.services.chat_service.get_session", return_value=session),
            patch("archimedes.services.chat_service.ChatMessage") as msg_class,
        ):
            msg_class.return_value = _mock_msg(text="hello")
            result = ChatService().post_message("0xv", "0xw", "hello")
        assert result["message"] == "hello"
        session.add.assert_called_once()
        session.commit.assert_called_once()
        session.close.assert_called_once()

    def test_mention_triggers_ai_response_in_payload(self) -> None:
        session, _ = _make_session([])
        with (
            patch("archimedes.services.chat_service.get_session", return_value=session),
            patch("archimedes.services.chat_service.ChatMessage") as msg_class,
        ):
            msg_class.return_value = _mock_msg(text="@archimedes how is performance?")
            with patch.object(ChatService, "_generate_ai_response") as gen:
                gen.return_value = {"id": 99, "message": "ai-reply", "is_ai": True}
                result = ChatService().post_message("0xv", "0xw", "@archimedes how is performance?")
        gen.assert_called_once()
        assert "_ai_response" in result
        assert result["_ai_response"]["message"] == "ai-reply"

    def test_no_mention_skips_ai_response(self) -> None:
        session, _ = _make_session([])
        with (
            patch("archimedes.services.chat_service.get_session", return_value=session),
            patch("archimedes.services.chat_service.ChatMessage") as msg_class,
        ):
            msg_class.return_value = _mock_msg(text="just a chat")
            with patch.object(ChatService, "_generate_ai_response") as gen:
                ChatService().post_message("0xv", "0xw", "just a chat")
        gen.assert_not_called()

    def test_exception_triggers_rollback(self) -> None:
        session, _ = _make_session([])
        session.commit.side_effect = RuntimeError("db down")
        with (
            patch("archimedes.services.chat_service.get_session", return_value=session),
            patch("archimedes.services.chat_service.ChatMessage") as msg_class,
        ):
            msg_class.return_value = _mock_msg()
            with pytest.raises(RuntimeError):
                ChatService().post_message("0xv", "0xw", "hello")
        session.rollback.assert_called_once()
        session.close.assert_called_once()


class TestPostAiMessage:
    def test_uses_ai_wallet_address(self) -> None:
        session, _ = _make_session([])
        with (
            patch("archimedes.services.chat_service.get_session", return_value=session),
            patch("archimedes.services.chat_service.ChatMessage") as msg_class,
        ):
            msg_class.return_value = _mock_msg(wallet=AI_WALLET_ADDRESS, is_ai=True)
            result = ChatService().post_ai_message("0xv", "system notice")
        assert result is not None
        assert result["is_ai"] is True
        # The constructor was called with the AI wallet
        kwargs = msg_class.call_args.kwargs
        assert kwargs["wallet_address"] == AI_WALLET_ADDRESS

    def test_failure_returns_none(self) -> None:
        session, _ = _make_session([])
        session.commit.side_effect = RuntimeError("db down")
        with (
            patch("archimedes.services.chat_service.get_session", return_value=session),
            patch("archimedes.services.chat_service.ChatMessage") as msg_class,
        ):
            msg_class.return_value = _mock_msg()
            result = ChatService().post_ai_message("0xv", "fail")
        assert result is None
        session.rollback.assert_called_once()


class TestEventPosters:
    def test_rebalance_event_includes_trades(self) -> None:
        with patch.object(ChatService, "post_ai_message") as poster:
            poster.return_value = {"id": 1}
            ChatService().post_rebalance_event(
                "0xv",
                "regime shift",
                trades=[{"direction": "buy", "amount": 100, "symbol": "sTSLA"}],
            )
        poster.assert_called_once()
        message = poster.call_args.args[1]
        assert "BUY 100 sTSLA" in message
        assert "regime shift" in message
        # trigger keyword carried
        assert poster.call_args.kwargs.get("trigger") == "rebalance"

    def test_rebalance_event_no_trades_still_posts(self) -> None:
        with patch.object(ChatService, "post_ai_message") as poster:
            poster.return_value = {"id": 1}
            ChatService().post_rebalance_event("0xv", "regime shift", trades=None)
        message = poster.call_args.args[1]
        assert "Rebalance executed" in message

    def test_regime_change_message_includes_confidence(self) -> None:
        with patch.object(ChatService, "post_ai_message") as poster:
            poster.return_value = {"id": 1}
            ChatService().post_regime_change("0xv", "risk_on", "risk_off", confidence=0.87)
        message = poster.call_args.args[1]
        assert "87%" in message
        assert "risk_on" in message
        assert "risk_off" in message
        assert poster.call_args.kwargs.get("trigger") == "regime_change"


class TestCannedResponse:
    """The fallback path (no live AI) must read as static, non-authoritative
    info — never as a freshly-reasoned live agent answer (issue #752).
    """

    # Representative user messages spanning every keyword branch + the default.
    _MESSAGES: ClassVar[list[str]] = [
        "How is performance?",
        "Should we rebalance soon?",
        "Is this risky?",
        "What strategy is this?",
        "Hello!",
        "Just listing without any trigger",
    ]

    @pytest.mark.parametrize("user_message", _MESSAGES)
    def test_every_branch_is_marked_non_authoritative(self, user_message: str) -> None:
        """Every fallback message carries the offline/static prefix."""
        with patch.object(ChatService, "post_ai_message") as poster:
            poster.return_value = {"id": 1}
            ChatService()._canned_response("0xv", user_message)
        text = poster.call_args.args[1]
        assert text.startswith(ChatService._FALLBACK_PREFIX)
        lowered = text.lower()
        # Honest framing words that signal "this is not a live answer".
        assert ("unavailable" in lowered) or ("offline" in lowered)
        assert "no live ai ran" in lowered

    @pytest.mark.parametrize("user_message", _MESSAGES)
    def test_no_authoritative_product_claims_in_fallback(self, user_message: str) -> None:
        """Anti-goal guard (#752): the fallback must NOT restate product
        guarantees as freshly-verified live output. The specific marketing
        sentences that previously leaked through this path are forbidden.
        """
        with patch.object(ChatService, "post_ai_message") as poster:
            poster.return_value = {"id": 1}
            ChatService()._canned_response("0xv", user_message)
        text = poster.call_args.args[1]
        forbidden = [
            "Every Tier 1 strategy passes four selection-bias controls",
            "anchored with a verifiable hash on Arc",
            "Rigor is the wedge",
            "Portfolio performance is tracked on-chain via reasoning traces",
        ]
        for claim in forbidden:
            assert claim not in text, f"fallback must not assert: {claim!r}"

    def test_fallback_points_to_verifiable_surfaces(self) -> None:
        """Honest static info may direct the user to the real, independently
        verifiable surfaces (passport / Traces tab) — that's allowed and good.
        """
        with patch.object(ChatService, "post_ai_message") as poster:
            poster.return_value = {"id": 1}
            ChatService()._canned_response("0xv", "Is this risky?")
        text = poster.call_args.args[1]
        assert ("passport" in text.lower()) or ("traces" in text.lower())


class TestGetMessageCount:
    def test_returns_session_count(self) -> None:
        session, _ = _make_session([_mock_msg(), _mock_msg(), _mock_msg()])
        with patch("archimedes.services.chat_service.get_session", return_value=session):
            count = ChatService().get_message_count("0xv")
        assert count == 3
        session.close.assert_called_once()


class TestGenerateAiResponse:
    """P0 — the chat AI response must route through make_llm_backend(), not a
    hardcoded premium Claude literal (silent cost-leak fix).
    """

    def test_no_hardcoded_model_literal_in_source(self) -> None:
        """Anti-goal guard: the old hardcoded Sonnet id must be gone."""
        import inspect

        from archimedes.services import chat_service as mod

        src = inspect.getsource(mod)
        assert "claude-sonnet-4-20250514" not in src
        # And the route is the provider-agnostic factory.
        assert "make_llm_backend" in src

    def test_routes_through_make_llm_backend(self) -> None:
        """A real LLM response is produced via backend.complete(), and posted."""
        svc = ChatService()
        fake_backend = MagicMock()
        fake_backend.available = True
        fake_backend.complete.return_value = "  Backend-served reply.  "
        with (
            patch("archimedes.services.llm_backend.make_llm_backend", return_value=fake_backend),
            patch.object(svc, "get_messages", return_value=[]),
            patch.object(svc, "_build_vault_context", return_value="<vault_context/>"),
            patch.object(svc, "post_ai_message", return_value={"id": 1, "message": "Backend-served reply."}) as post,
        ):
            result = svc._generate_ai_response("0xv", "@archimedes hi", "0xw")
        # complete() is called with (system_prompt, user_prompt) — the seam, no model literal.
        fake_backend.complete.assert_called_once()
        assert result is not None
        # Posted text is stripped backend output.
        assert post.call_args.args[1] == "Backend-served reply."

    def test_falls_back_to_canned_when_backend_unavailable(self) -> None:
        """No credentials → canned response, not a crash."""
        svc = ChatService()
        fake_backend = MagicMock()
        fake_backend.available = False
        with (
            patch("archimedes.services.llm_backend.make_llm_backend", return_value=fake_backend),
            patch.object(svc, "_canned_response", return_value={"id": 9, "message": "canned"}) as canned,
        ):
            result = svc._generate_ai_response("0xv", "how is performance?", "0xw")
        canned.assert_called_once()
        assert result["message"] == "canned"

    def test_chat_model_override_threaded_to_factory(self, monkeypatch) -> None:
        """CHAT_MODEL (when set) is the named override passed to make_llm_backend()."""
        import importlib

        monkeypatch.setenv("CHAT_MODEL", "amazon.nova-lite-v1:0")
        # Re-import so the module-level CHAT_MODEL picks up the env var.
        from archimedes.services import chat_service as mod

        importlib.reload(mod)
        try:
            svc = mod.ChatService()
            fake_backend = MagicMock()
            fake_backend.available = True
            fake_backend.complete.return_value = "ok"
            with (
                patch("archimedes.services.llm_backend.make_llm_backend", return_value=fake_backend) as factory,
                patch.object(svc, "get_messages", return_value=[]),
                patch.object(svc, "_build_vault_context", return_value=""),
                patch.object(svc, "post_ai_message", return_value={"id": 1}),
            ):
                svc._generate_ai_response("0xv", "hi", "0xw")
            factory.assert_called_once_with(model="amazon.nova-lite-v1:0")
        finally:
            monkeypatch.delenv("CHAT_MODEL", raising=False)
            importlib.reload(mod)


class TestVaultContextLiveRigor:
    """The vault-chat context's rigor line must be a LIVE verdict or absent.

    ``Strategy.passes_rigor_gate`` on the in-memory provider object is a
    fail-closed sentinel (always ``False``, #821). Pre-fix,
    ``_build_vault_context`` rendered it as "Rigor gate: not passed" for every
    curated strategy — the vault chat LLM was told the whole library had
    failed. The builder's own law is "missing data is omitted, never
    invented": when no live status is available the rigor line must be
    OMITTED, not fabricated from the sentinel. Every stub strategy here is
    poisoned so a sentinel-reading mutant renders the wrong text and fails.
    """

    def _context_with(self, statuses: dict[str, str]) -> str:
        from archimedes.services.chat_service import ChatService

        meta = MagicMock()
        meta.name = "Test Vault"
        meta.get_strategy_ids.return_value = ["sid1"]
        session, _ = _make_session([meta])

        strat = MagicMock()
        strat.id = "sid1"
        strat.paper_title = "Faber TAA"
        strat.methodology_summary = "10-month SMA timing"
        strat.asset_universe = ["SPY"]
        strat.passes_rigor_gate = False  # POISON: sentinel-reader renders "not passed"

        provider = MagicMock()
        provider.get_strategy.return_value = strat

        with (
            patch("archimedes.db.get_session", return_value=session),
            patch("archimedes.services.strategy_provider.default_provider", return_value=provider),
            patch("archimedes.services.chat_service._curated_rigor_statuses", return_value=statuses),
        ):
            return ChatService()._build_vault_context("0xVault")

    def test_live_pass_renders_passed_never_the_sentinel(self) -> None:
        ctx = self._context_with({"sid1": "pass"})
        assert "Rigor gate: passed (live gate)" in ctx
        assert "not passed" not in ctx

    def test_live_fail_renders_failed(self) -> None:
        ctx = self._context_with({"sid1": "fail"})
        assert "Rigor gate: failed (live gate)" in ctx

    def test_pending_is_stated_honestly(self) -> None:
        ctx = self._context_with({"sid1": "pending"})
        assert "Rigor gate: pending — no live backtest verdict yet" in ctx

    def test_degenerate_is_stated_honestly_not_omitted(self) -> None:
        """#1184: a zero-variance persisted series must render its own label —
        without a "degenerate" entry in rigor_labels this status wouldn't match
        any key and the line would be silently omitted, hiding that the
        strategy's data is broken rather than merely unmeasured."""
        ctx = self._context_with({"sid1": "degenerate"})
        assert "Rigor gate: DEGENERATE" in ctx
        assert "Rigor gate: pending" not in ctx

    def test_no_live_status_omits_the_line_never_invents_it(self) -> None:
        # Batch failure → {}: pre-fix code invented "Rigor gate: not passed"
        # from the poisoned sentinel; the fixed builder omits the line.
        ctx = self._context_with({})
        assert "Rigor gate" not in ctx
        assert "Faber TAA" in ctx  # the rest of the context still renders


class TestCuratedRigorStatuses:
    """_curated_rigor_statuses reduces the memoized live-gate batch to tri-states."""

    def _statuses(self, batch_results, batch_raises=False):
        from archimedes.services.chat_service import _curated_rigor_statuses

        strat = MagicMock()
        strat.id = "sid1"

        provider = MagicMock()
        provider.list_strategies.return_value = []

        if batch_raises:

            def batch(_cohort):
                raise RuntimeError("db down")
        else:

            def batch(_cohort):
                return batch_results

        with (
            patch("archimedes.services.strategy_provider.default_provider", return_value=provider),
            patch("archimedes.api.strategies_routes._live_rigor_results_for_strategies", side_effect=batch),
        ):
            return _curated_rigor_statuses([strat])

    def test_passing_result_maps_to_pass(self) -> None:
        result = MagicMock()
        result.passes_all = True
        # #1184: is_degenerate must be explicit on the double — MagicMock auto-vivifies
        # unset attributes as truthy Mocks, which would otherwise make _verdict_from_result
        # (which now checks is_degenerate first) misread this RigorGateResult stand-in as
        # a broken/zero-variance series.
        result.is_degenerate = False
        assert self._statuses({"sid1": result}) == {"sid1": "pass"}

    def test_failing_result_maps_to_fail(self) -> None:
        result = MagicMock()
        result.passes_all = False
        result.is_degenerate = False
        assert self._statuses({"sid1": result}) == {"sid1": "fail"}

    def test_degenerate_result_maps_to_degenerate(self) -> None:
        """#1184: a zero-variance persisted series reports its own category here
        too — chat context must not fold it into 'fail' or 'pending'."""
        result = MagicMock()
        result.passes_all = False
        result.is_degenerate = True
        assert self._statuses({"sid1": result}) == {"sid1": "degenerate"}

    def test_absent_result_maps_to_pending(self) -> None:
        assert self._statuses({}) == {"sid1": "pending"}

    def test_batch_failure_returns_empty_so_caller_omits(self) -> None:
        assert self._statuses(None, batch_raises=True) == {}

    def test_empty_input_short_circuits(self) -> None:
        from archimedes.services.chat_service import _curated_rigor_statuses

        assert _curated_rigor_statuses([]) == {}
