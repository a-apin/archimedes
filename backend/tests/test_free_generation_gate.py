"""Free path: account required, wallet optional for the first N generations (#1643).

The owner's 2026-08-31 product review reversed the 2026-08-19 "generation
requires a wallet, no free allowance" directive. This file is the executable
statement of the corrected policy:

  1. **The gate.** With ``GENERATION_PAYMENT_REQUIRED=true`` and NO linked
     wallet, calls 1/2/3 are served (202) and call 4 is refused
     ``409 wallet_link_required`` — asserted against the real
     ``free_generation_grants`` rows after every call, so a gate that "exists"
     but never decrements cannot pass.
  2. **The adversarial pass** (CLAUDE.md: a guard must be shown to REJECT
     something). Four separate inputs that must be refused: a ledger seeded
     at the allowance so call *one* is blocked; the allowance switched off so
     the pre-#1643 behaviour returns exactly; two claims racing for the same
     slot, where the unique constraint must reject the second; and — owner
     decision D1, 2026-08-31, recorded on #1653 — an account whose email is
     not verified, which is refused with a 409 that names BOTH unlocks, with
     the verified account's identical request served free as the contrast.
  3. **The anti-goals**, each pinned as its own test: the account requirement
     is never relaxed (no wallet-only path), the paid tier from generation #4
     is untouched, the allowance is lifetime rather than daily, and no
     allowance is spent while the paywall flag is off.
  4. **Honest reporting.** ``GET /api/account/usage`` shows 3 for a fresh
     account, decrements with real use, and reports ``null`` — never a
     fabricated ``0`` or ``3`` — when the ledger cannot be read.
  5. **The funnel stages** the new transitions emit.

Hermetic: tmp-file SQLite (``tests.db_isolation.redirect_to_tmp_sqlite``), a
dependency-overridden ``require_current_user``, a mocked job store and a
backgrounded task closed rather than run — the harness from
``test_generation_credits.py`` / ``test_generate_credits_route.py``. No live
Redis, Postgres, LLM or Circle facilitator.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from archimedes.api import account_usage_routes, generate_routes
from archimedes.api.account_auth import CurrentUser, require_current_user
from archimedes.db import get_session
from archimedes.models.free_generation_grant import (
    GRANT_RELEASED,
    GRANT_USED,
    FreeGenerationGrantRecord,
    claim_free_grant,
    release_grant,
    used_count,
)
from archimedes.services import free_generations, generation_payment
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError

RECIPIENT = "0x00000000000000000000000000000000000000a1"
WALLET = "0x" + "ab" * 20
USER = "user-free-1"

_BODY = {"brief": {"intent": "low-vol treasury alternative", "risk_appetite": "moderate"}}


@pytest.fixture(autouse=True)
def _use_tmp_db(tmp_path):
    from tests.db_isolation import redirect_to_tmp_sqlite

    yield from redirect_to_tmp_sqlite(tmp_path)


@pytest.fixture(autouse=True)
def _hermetic_env(monkeypatch):
    """Neutralise every env var this gate reads.

    A developer ``.env`` (or a sibling test that set one) leaking
    ``FREE_GENERATIONS_PER_ACCOUNT`` or ``PAYMENTS_HALT`` in would silently
    change what these assertions mean.
    """
    for var in (
        "FREE_GENERATIONS_PER_ACCOUNT",
        "GENERATION_PAYMENT_REQUIRED",
        "GENERATION_PAYMENT_RECIPIENT",
        "GENERATION_PAYMENTS_DRY_RUN",
        "PAYMENTS_DRY_RUN",
        "PAYMENTS_HALT",
        "PREMIUM_MODELS_ENABLED",
        "PREMIUM_MODELS_ALLOWLIST",
    ):
        monkeypatch.delenv(var, raising=False)


@contextmanager
def _as_account(user_id: str = USER, *, wallet: str | None = None, email_verified: bool = True):
    """Signed-in account with an explicitly chosen wallet-link + verification state.

    ``require_current_user`` is dependency-overridden (the
    ``test_generate_credits_route.py`` idiom) so the account id is a literal
    the ledger assertions can seed against; ``get_linked_wallet_address`` is
    patched at the route module (the ``test_generate_payment_gate.py`` idiom)
    because "signed in but no wallet" is the exact state this issue is about.

    ``email_verified`` defaults to ``True`` — the state in which the free tier
    exists at all (owner decision D1) — so every test above reads as the policy
    it is asserting. ``TestEmailVerifiedUnlock`` passes ``False`` explicitly.
    """
    from archimedes.main import app

    app.dependency_overrides[require_current_user] = lambda: CurrentUser(
        user_id, "Free Tier Test", f"{user_id}@example.com", email_verified
    )
    try:
        with patch.object(generate_routes, "get_linked_wallet_address", return_value=wallet):
            yield
    finally:
        app.dependency_overrides.pop(require_current_user, None)


def _mock_store(job_id: str = "job-free") -> MagicMock:
    store = MagicMock()
    store.enqueue = AsyncMock(return_value=job_id)
    return store


def _close_background_coroutine(coro):
    coro.close()
    return MagicMock()


@contextmanager
def _routed(store):
    with (
        patch.object(generate_routes, "get_job_store", return_value=store),
        patch.object(generate_routes.asyncio, "create_task", side_effect=_close_background_coroutine),
    ):
        yield


def _paywall_on(monkeypatch) -> None:
    """The production shape: the gate flag on with a recipient configured."""
    monkeypatch.setenv("GENERATION_PAYMENT_REQUIRED", "true")
    monkeypatch.setenv("GENERATION_PAYMENT_RECIPIENT", RECIPIENT)
    monkeypatch.setenv("PAYMENTS_DRY_RUN", "false")


def _client() -> TestClient:
    from archimedes.main import app

    return TestClient(app)


def _start(body: dict | None = None, store: MagicMock | None = None):
    with _routed(store or _mock_store()):
        return _client().post("/api/generate/start", json=body or _BODY)


def _grants(user_id: str = USER) -> list[FreeGenerationGrantRecord]:
    with get_session() as session:
        return list(
            session.query(FreeGenerationGrantRecord)
            .filter(FreeGenerationGrantRecord.user_id == user_id)
            .order_by(FreeGenerationGrantRecord.seq)
            .all()
        )


def _seed_used(user_id: str, n: int) -> None:
    """Put *n* genuinely spent free generations in the ledger."""
    with get_session() as session:
        for _ in range(n):
            grant = claim_free_grant(session, user_id=user_id, allowance=n)
            assert grant is not None
        session.commit()


# ── 1. The gate: three free, then the wallet gate ────────────────────────


class TestTheGate:
    def test_calls_1_2_3_are_free_without_a_wallet_and_call_4_is_gated(self, monkeypatch):
        """The acceptance criterion, asserted against real counter state.

        The per-call ledger read is what makes this a real test: a gate that
        merely *exists* but never decrements would serve all four calls, and a
        gate that decremented without gating would serve them too. Only a gate
        that does both passes every assertion below.
        """
        _paywall_on(monkeypatch)

        with _as_account(wallet=None):
            for expected_used in (1, 2, 3):
                resp = _start()
                assert resp.status_code == 202, resp.text
                rows = _grants()
                assert [r.status for r in rows] == [GRANT_USED] * expected_used
                assert [r.seq for r in rows] == list(range(1, expected_used + 1))
                # The slot is bound to the job it funded, not left dangling.
                assert rows[-1].job_id == "job-free"

            fourth = _start()

        assert fourth.status_code == 409, fourth.text
        assert fourth.json()["detail"]["reason"] == "wallet_link_required"
        # Refusing must not consume a fourth slot.
        assert len(_grants()) == 3

    def test_the_allowance_is_lifetime_not_a_daily_bucket(self, monkeypatch):
        """Anti-goal: the free counter must not be confused with the 36h quota.

        Nothing in the code path re-reads a clock, so the only way this could
        regress is by moving the counter into ``generation_quota``'s
        day-buckets — at which point the rows this asserts on would not exist.
        Pinned as a durable row check rather than a time-travel test: the
        counter's durability IS its lifetime-ness.
        """
        _paywall_on(monkeypatch)
        with _as_account(wallet=None):
            for _ in range(3):
                assert _start().status_code == 202

        with get_session() as session:
            # Rows on disk, no TTL column, no expiry mechanism anywhere.
            assert used_count(session, USER) == 3
        assert all(r.created_at is not None for r in _grants())


# ── 2. Adversarial: the guard rejecting ──────────────────────────────────


class TestTheGuardRejects:
    def test_a_ledger_seeded_at_the_allowance_blocks_call_one(self, monkeypatch):
        """CLAUDE.md's "a guard must be shown to reject something".

        Distinct from the test above: nothing was generated in THIS process, so
        the refusal can only come from the gate reading persisted state. A gate
        that counted in-memory calls, or that only refused after three
        successes it had itself served, passes the previous test and fails this
        one.
        """
        _paywall_on(monkeypatch)
        _seed_used(USER, 3)

        with _as_account(wallet=None):
            resp = _start()

        assert resp.status_code == 409, resp.text
        assert resp.json()["detail"]["reason"] == "wallet_link_required"
        assert len(_grants()) == 3  # no fourth row was written

    def test_allowance_zero_restores_the_pre_1643_gate_on_the_very_first_call(self, monkeypatch):
        """The kill switch has to actually kill it."""
        _paywall_on(monkeypatch)
        monkeypatch.setenv("FREE_GENERATIONS_PER_ACCOUNT", "0")

        with _as_account(wallet=None):
            resp = _start()

        assert resp.status_code == 409, resp.text
        assert resp.json()["detail"]["reason"] == "wallet_link_required"
        assert _grants() == []

    def test_two_claims_racing_for_one_slot_and_the_constraint_rejects_the_second(self):
        """The over-grant this table's unique constraint exists to make impossible.

        Without ``uq_free_generation_grants_user_seq``, N concurrent
        first-generation requests each read ``used_count == 0`` and each get a
        free run. Driven at the model layer because that is where the race
        resolves; the row the loser tried to write is built by hand so the
        collision is the one a real race produces (same user, same ``seq``).
        """
        with get_session() as session:
            first = claim_free_grant(session, user_id=USER, allowance=3)
            assert first is not None and first.seq == 1
            session.commit()

        with get_session() as session, pytest.raises(IntegrityError):
            session.add(
                FreeGenerationGrantRecord(
                    user_id=USER,
                    seq=1,  # the seq a concurrent claimer would have computed
                    status=GRANT_USED,
                    created_at=datetime.now(UTC),
                )
            )
            session.flush()

        assert len(_grants()) == 1

    def test_a_claim_error_falls_through_to_the_wallet_gate_not_to_a_free_run(self, monkeypatch):
        """Fail-closed on generosity: an unreadable ledger must not grant.

        The opposite choice is the unrecoverable one — a database blip handing
        out uncapped free LLM runs with nothing recording that it happened.
        """
        _paywall_on(monkeypatch)
        monkeypatch.setattr(
            free_generations,
            "_session",
            MagicMock(side_effect=RuntimeError("ledger unreachable")),
        )

        with _as_account(wallet=None):
            resp = _start()

        assert resp.status_code == 409, resp.text
        assert resp.json()["detail"]["reason"] == "wallet_link_required"


# ── 2b. Owner decision D1: the allowance unlocks on a VERIFIED email ─────


class TestEmailVerifiedUnlock:
    """The 2026-08-31 owner decision recorded on #1653.

    Free generations gate on a verified email, not on account creation alone —
    accounts are free and unlimited, a working inbox is not, so verification is
    what prices disposable-account farming of free LLM runs (and doubles as the
    carrot for verifying). The gate must REFUSE an unverified claim; it must
    not refuse the *request*, which still has the wallet path it always had.
    """

    def test_an_unverified_account_is_refused_with_the_dual_unlock_409(self, monkeypatch):
        """The guard rejecting: identical request, unverified account, no free run.

        Also the message contract. The 409 is now two different dead ends
        wearing one status code, and telling a caller the wrong one costs it a
        wasted request: this account's cheapest way forward is the inbox it
        already owns, so the message must name verification AND the wallet.
        """
        _paywall_on(monkeypatch)

        with _as_account(wallet=None, email_verified=False):
            resp = _start()

        assert resp.status_code == 409, resp.text
        detail = resp.json()["detail"]
        assert detail["reason"] == "wallet_link_required"  # the machine contract is unchanged
        assert detail["free_generations_locked_reason"] == "email_unverified"
        assert "erify your email" in detail["message"], detail["message"]
        assert "wallets/challenge" in detail["message"], "the wallet unlock must still be offered"
        # Nothing was claimed, so verifying later still buys the full allowance.
        assert _grants() == []
        assert free_generations.remaining(USER) == 3

    def test_the_same_call_from_a_verified_account_is_served_free(self, monkeypatch):
        """The contrast pair — the ONLY difference is ``email_verified``.

        Without this, the test above would also pass if the free path had been
        deleted outright rather than gated.
        """
        _paywall_on(monkeypatch)

        with _as_account(wallet=None, email_verified=True):
            resp = _start()

        assert resp.status_code == 202, resp.text
        assert [(r.seq, r.status) for r in _grants()] == [(1, GRANT_USED)]

    def test_an_unverified_claim_never_reads_the_ledger(self, monkeypatch):
        """The refusal is decided before any DB work.

        Asserted on the session factory's ``call_count``, NOT by making it
        raise: ``claim`` deliberately swallows every exception into "no free
        slot", so a raising mock would be caught and this test would pass
        against a gate that read the ledger first — a test that cannot fail
        its own claim. (Checked: with the verification guard deleted, the
        raising version still passed; this version fails.)
        """
        session_factory = MagicMock(side_effect=AssertionError("the ledger must not be touched"))
        monkeypatch.setattr(free_generations, "_session", session_factory)

        assert free_generations.claim(USER, email_verified=False) is None
        assert session_factory.call_count == 0, "an unverified account must cost the database nothing"

    def test_an_unverified_account_with_a_wallet_still_reaches_the_paywall(self, monkeypatch):
        """Unverified is a lock on the FREE tier, not a block on the product.

        The paid path is exactly what it was before #1643 for this caller: a
        402 with the x402 requirements, from generation one.
        """
        _paywall_on(monkeypatch)

        with _as_account(wallet=WALLET, email_verified=False):
            resp = _start()

        assert resp.status_code == 402, resp.text
        assert "PAYMENT-REQUIRED" in resp.headers
        assert _grants() == []

    def test_an_exhausted_verified_account_gets_the_wallet_message_not_the_carrot(self, monkeypatch):
        """The other half of the 409 fork: no carrot where verifying changes nothing.

        This account has already verified and spent all three, so pointing it
        at its inbox would be a dead end dressed as a way forward.
        """
        _paywall_on(monkeypatch)
        _seed_used(USER, 3)

        with _as_account(wallet=None, email_verified=True):
            resp = _start()

        assert resp.status_code == 409, resp.text
        detail = resp.json()["detail"]
        assert detail["free_generations_locked_reason"] is None
        assert "free generations are used up" in detail["message"]
        assert "erify your email" not in detail["message"]

    def test_the_kill_switch_reports_no_lock_rather_than_an_empty_promise(self, monkeypatch):
        """``FREE_GENERATIONS_PER_ACCOUNT=0`` must not dangle a carrot.

        "Verify your email to unlock 0 free generations" is a promise with
        nothing behind it. A disabled policy reports no lock at all, which the
        UI renders as silence.
        """
        monkeypatch.setenv("FREE_GENERATIONS_PER_ACCOUNT", "0")
        assert free_generations.locked_reason(email_verified=False) is None
        assert free_generations.locked_reason(email_verified=True) is None

    def test_claim_cannot_be_called_without_stating_the_verification_state(self):
        """The keyword argument is required on purpose.

        A future call site that forgets it would silently reopen the free tier
        to unverified accounts; instead it does not run at all.
        """
        with pytest.raises(TypeError):
            free_generations.claim(USER)  # type: ignore[call-arg]

    def test_the_unverified_refusal_still_emits_wallet_gate_shown(self, monkeypatch):
        """The funnel keeps seeing the gate — the boundary just has two exits now."""
        _paywall_on(monkeypatch)
        stages: list[str] = []

        async def _capture(_request, stage):
            stages.append(stage)

        monkeypatch.setattr(generate_routes, "record_funnel", _capture)
        with _as_account(wallet=None, email_verified=False):
            assert _start().status_code == 409

        assert stages == ["wallet_gate_shown"]


# ── 3. The anti-goals ────────────────────────────────────────────────────


class TestAntiGoals:
    def test_no_wallet_only_without_account_path(self, monkeypatch):
        """The account requirement is absolute, free tier or not.

        A wallet is presented and the free allowance is untouched; the request
        is still refused, because ``require_current_user`` runs first and this
        change did not move it.
        """
        _paywall_on(monkeypatch)
        with (
            patch.object(generate_routes, "get_linked_wallet_address", return_value=WALLET),
            _routed(_mock_store()),
        ):
            resp = _client().post("/api/generate/start", json=_BODY)

        assert resp.status_code == 401, resp.text
        assert _grants() == []

    def test_generation_four_with_a_wallet_still_hits_the_paywall(self, monkeypatch):
        """The paid tier from #4 onward is byte-for-byte unchanged."""
        _paywall_on(monkeypatch)
        _seed_used(USER, 3)

        with _as_account(wallet=WALLET):
            resp = _start()

        assert resp.status_code == 402, resp.text
        assert "PAYMENT-REQUIRED" in resp.headers
        assert resp.json()["detail"]["reason"] == "payment_required"

    def test_flag_off_serves_the_request_and_spends_no_allowance(self, monkeypatch):
        """With nothing gated, nothing is spent.

        Burning a lifetime allowance while generation is free for everyone
        would silently consume a user's free runs for no benefit to anyone.
        """
        monkeypatch.delenv("GENERATION_PAYMENT_REQUIRED", raising=False)

        with _as_account(wallet=None):
            resp = _start()

        assert resp.status_code == 202, resp.text
        assert _grants() == []

    def test_the_free_tier_does_not_buy_a_premium_model(self, monkeypatch):
        """The entitlement gate (T1.8) still refuses, and the slot is handed back.

        Two properties in one: a free generation buys the default model, not
        an Anthropic one; and a slot claimed for a request that never queued is
        released rather than silently costing the account a free run.
        """
        _paywall_on(monkeypatch)

        with _as_account(wallet=None):
            resp = _start({**_BODY, "model": "us.anthropic.claude-sonnet-4-6"})

        assert resp.status_code == 402, resp.text
        rows = _grants()
        assert [r.status for r in rows] == [GRANT_RELEASED]
        assert rows[0].job_id is None
        assert free_generations.remaining(USER) == 3  # nothing was really spent

    def test_a_failed_enqueue_hands_the_free_slot_back(self, monkeypatch):
        """The other half of the release path: the queue itself erroring."""
        _paywall_on(monkeypatch)
        store = _mock_store()
        store.enqueue = AsyncMock(side_effect=RuntimeError("queue down"))

        with _as_account(wallet=None), pytest.raises(RuntimeError):
            _start(store=store)

        rows = _grants()
        assert [r.status for r in rows] == [GRANT_RELEASED]
        assert free_generations.remaining(USER) == 3

    def test_a_released_slot_is_reusable_and_does_not_shift_the_allowance(self):
        """A released row must not consume allowance, nor free an extra one."""
        with get_session() as session:
            first = claim_free_grant(session, user_id=USER, allowance=3)
            release_grant(session, first.id)
            session.commit()
            assert used_count(session, USER) == 0

        # Three real generations remain available after the release.
        with get_session() as session:
            for _ in range(3):
                assert claim_free_grant(session, user_id=USER, allowance=3) is not None
            assert claim_free_grant(session, user_id=USER, allowance=3) is None
            session.commit()

        # The released row kept its ordinal; the live ones took fresh ones.
        assert [(r.seq, r.status) for r in _grants()] == [
            (1, GRANT_RELEASED),
            (2, GRANT_USED),
            (3, GRANT_USED),
            (4, GRANT_USED),
        ]


# ── 4. GET /api/account/usage ────────────────────────────────────────────


class TestUsageReporting:
    @pytest.fixture(autouse=True)
    def _no_redis(self, monkeypatch):
        """The daily-cap half of the response is not what these tests measure."""
        monkeypatch.setattr(
            account_usage_routes.GenerationQuota,
            "peek",
            AsyncMock(return_value=0),
        )
        monkeypatch.setattr(account_usage_routes.GenerationQuota, "close", AsyncMock(return_value=None))

    def _usage(self) -> dict:
        resp = _client().get("/api/account/usage")
        assert resp.status_code == 200, resp.text
        return resp.json()

    def test_fresh_account_reports_three_and_decrements_with_real_use(self, monkeypatch):
        _paywall_on(monkeypatch)

        with _as_account(wallet=None):
            fresh = self._usage()
            assert fresh["free_generations_allowance"] == 3
            assert fresh["free_generations_remaining"] == 3
            assert fresh["free_generations_error"] is None

            assert _start().status_code == 202
            assert self._usage()["free_generations_remaining"] == 2

            assert _start().status_code == 202
            assert _start().status_code == 202
            spent = self._usage()

        assert spent["free_generations_remaining"] == 0
        # The daily caps are a separate axis and are still reported separately.
        assert spent["user"]["used"] == 0

    def test_an_unverified_account_is_reported_as_LOCKED_with_its_balance_intact(self, monkeypatch):
        """Owner decision D1's display half: the lock is its own field.

        "3 slots, not yet unlocked" and "0 slots left" are different situations
        with different answers, so folding the lock into the count (either way)
        would destroy the distinction the banner needs.
        """
        _paywall_on(monkeypatch)

        with _as_account(wallet=None, email_verified=False):
            body = self._usage()

        assert body["free_generations_locked_reason"] == "email_unverified"
        assert body["free_generations_remaining"] == 3  # waiting, not spent
        assert body["free_generations_error"] is None  # a lock is not a failure

    def test_a_verified_account_reports_no_lock(self, monkeypatch):
        _paywall_on(monkeypatch)

        with _as_account(wallet=None, email_verified=True):
            body = self._usage()

        assert body["free_generations_locked_reason"] is None

    def test_an_unreadable_ledger_reports_null_never_a_fabricated_number(self, monkeypatch):
        """Adversarial: the failure a naive implementation renders as "0 left"
        (locking out a fresh account) or "3 left" (promising runs the gate will
        refuse). Both are claims the code does not keep."""
        monkeypatch.setattr(
            free_generations,
            "_session",
            MagicMock(side_effect=RuntimeError("ledger unreachable")),
        )

        with _as_account(wallet=None):
            body = self._usage()

        assert body["free_generations_remaining"] is None
        assert body["free_generations_error"] == "free_generation_backend_unavailable"


# ── 5. Funnel stages emitted at the gate ─────────────────────────────────


class TestFunnelEmission:
    def test_a_free_run_emits_generation_started_and_free_generation_used(self, monkeypatch):
        _paywall_on(monkeypatch)
        stages: list[str] = []

        async def _capture(_request, stage):
            stages.append(stage)

        monkeypatch.setattr(generate_routes, "record_funnel", _capture)
        with _as_account(wallet=None):
            assert _start().status_code == 202

        assert stages == ["generation_started", "free_generation_used"]

    def test_the_wallet_gate_emits_wallet_gate_shown(self, monkeypatch):
        _paywall_on(monkeypatch)
        _seed_used(USER, 3)
        stages: list[str] = []

        async def _capture(_request, stage):
            stages.append(stage)

        monkeypatch.setattr(generate_routes, "record_funnel", _capture)
        with _as_account(wallet=None):
            assert _start().status_code == 409

        assert stages == ["wallet_gate_shown"]

    def test_a_released_slot_is_never_counted_as_a_free_generation_used(self, monkeypatch):
        """The reason the stage is emitted after the enqueue, not at claim time."""
        _paywall_on(monkeypatch)
        store = _mock_store()
        store.enqueue = AsyncMock(side_effect=RuntimeError("queue down"))
        stages: list[str] = []

        async def _capture(_request, stage):
            stages.append(stage)

        monkeypatch.setattr(generate_routes, "record_funnel", _capture)
        with _as_account(wallet=None), pytest.raises(RuntimeError):
            _start(store=store)

        assert stages == []


# ── 6. The reversed policy is not still claimed anywhere ─────────────────


def test_the_paywall_module_no_longer_claims_there_is_no_free_allowance():
    """The stale-claim check, as a test rather than only a grep in a PR body."""
    import inspect

    doc = inspect.getdoc(generation_payment) or ""
    assert "no free path" not in doc
    assert "#1643" in doc, "the docstring must say what replaced the old policy"
