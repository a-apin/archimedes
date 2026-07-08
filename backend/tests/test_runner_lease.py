"""Runner exactly-once lease coverage (#1043).

Target: backend/archimedes/services/redis_state.py's acquire_lease /
renew_lease / release_lease (AgentStateStore), plus a light pass over
backend/archimedes/services/runner_lease.py's RunnerLeaseGuard, which is the
production wiring both async singleton runners (oracle_runner, agent_runner)
use.

Hermetic: backed by real fakeredis (in-memory Redis emulation with Lua
support via the `fakeredis[lua]` extra — see environment.yml) — no live
Redis. Mirrors the pattern in backend/tests/marketplace/test_state.py, which
already exercises a similar (but distinct) leader-lock primitive.

Proves the three acceptance points from #1043:
  (a) N concurrent acquire attempts for the same runner_name yield exactly
      ONE holder.
  (b) renew succeeds for the holder and FAILS for a non-holder / after the
      token has changed underneath it.
  (c) release only releases if the caller still holds the current token.
"""

from __future__ import annotations

import asyncio

import fakeredis
import pytest
from archimedes.services.redis_state import KEY_LEASE_PREFIX, AgentStateStore
from archimedes.services.runner_lease import RunnerLeaseGuard


@pytest.fixture
def store() -> AgentStateStore:
    """AgentStateStore backed by fakeredis (real in-memory Redis emulation)."""
    s = AgentStateStore()
    s._redis = fakeredis.FakeAsyncRedis(decode_responses=True)
    return s


class TestAcquireLease:
    async def test_acquire_returns_a_token(self, store: AgentStateStore):
        token = await store.acquire_lease("oracle_runner", ttl_ms=5000)
        assert token is not None
        assert isinstance(token, str)

    async def test_token_shape_is_uuid_colon_fence(self, store: AgentStateStore):
        token = await store.acquire_lease("oracle_runner", ttl_ms=5000)
        assert token is not None
        uuid_part, _, fence_part = token.partition(":")
        assert len(uuid_part) == 36  # canonical uuid4 string length
        assert fence_part.isdigit()

    async def test_fencing_counter_increments_across_acquires(self, store: AgentStateStore):
        # Release-then-reacquire (a fresh lease each time) must still see a
        # strictly increasing fence — it's a dedicated INCR key, independent
        # of the lease key's own lifecycle.
        t1 = await store.acquire_lease("agent_runner", ttl_ms=5000)
        assert t1 is not None
        await store.release_lease("agent_runner", t1)
        t2 = await store.acquire_lease("agent_runner", ttl_ms=5000)
        assert t2 is not None
        fence1 = int(t1.rsplit(":", 1)[1])
        fence2 = int(t2.rsplit(":", 1)[1])
        assert fence2 > fence1

    async def test_second_acquire_while_held_returns_none(self, store: AgentStateStore):
        first = await store.acquire_lease("agent_runner", ttl_ms=5000)
        assert first is not None
        second = await store.acquire_lease("agent_runner", ttl_ms=5000)
        assert second is None

    async def test_acquire_uses_the_documented_key_namespace(self, store: AgentStateStore):
        token = await store.acquire_lease("kb_runner", ttl_ms=5000)
        assert token is not None
        r = await store._get_redis()
        stored = await r.get(f"{KEY_LEASE_PREFIX}kb_runner")
        assert stored == token

    async def test_different_runner_names_are_independent(self, store: AgentStateStore):
        oracle_token = await store.acquire_lease("oracle_runner", ttl_ms=5000)
        agent_token = await store.acquire_lease("agent_runner", ttl_ms=5000)
        assert oracle_token is not None
        assert agent_token is not None
        assert oracle_token != agent_token

    async def test_n_concurrent_acquires_yield_exactly_one_holder(self, store: AgentStateStore):
        """(a) — the exactly-once acceptance point: N racers, ONE winner."""
        n = 16
        results = await asyncio.gather(*[store.acquire_lease("agent_runner", ttl_ms=5000) for _ in range(n)])
        winners = [t for t in results if t is not None]
        losers = [t for t in results if t is None]
        assert len(winners) == 1, f"expected exactly one holder, got {len(winners)}: {winners}"
        assert len(losers) == n - 1

        # The winner is genuinely the one stored in Redis.
        r = await store._get_redis()
        stored = await r.get(f"{KEY_LEASE_PREFIX}agent_runner")
        assert stored == winners[0]

    async def test_expired_lease_can_be_reacquired_by_someone_else(self, store: AgentStateStore):
        first = await store.acquire_lease("oracle_runner", ttl_ms=1)
        assert first is not None
        await asyncio.sleep(0.05)  # let the 1ms TTL lapse
        second = await store.acquire_lease("oracle_runner", ttl_ms=5000)
        assert second is not None
        assert second != first


class TestRenewLease:
    async def test_renew_succeeds_for_the_holder(self, store: AgentStateStore):
        token = await store.acquire_lease("agent_runner", ttl_ms=5000)
        assert token is not None
        ok = await store.renew_lease("agent_runner", token, ttl_ms=9000)
        assert ok is True

    async def test_renew_extends_the_ttl(self, store: AgentStateStore):
        token = await store.acquire_lease("agent_runner", ttl_ms=1000)
        assert token is not None
        r = await store._get_redis()
        await store.renew_lease("agent_runner", token, ttl_ms=60_000)
        ttl_ms = await r.pttl(f"{KEY_LEASE_PREFIX}agent_runner")
        assert ttl_ms > 30_000  # comfortably beyond the original 1s TTL

    async def test_renew_fails_for_a_non_holder_token(self, store: AgentStateStore):
        """(b) — a token that never held the lease cannot renew it."""
        token = await store.acquire_lease("agent_runner", ttl_ms=5000)
        assert token is not None
        ok = await store.renew_lease("agent_runner", "not-my-token:0", ttl_ms=9000)
        assert ok is False

    async def test_renew_fails_after_the_token_changed_underneath_it(self, store: AgentStateStore):
        """(b) — the classic lost-lease case: A held it, lost it, B took over."""
        token_a = await store.acquire_lease("agent_runner", ttl_ms=1)
        assert token_a is not None
        await asyncio.sleep(0.05)  # A's lease expires
        token_b = await store.acquire_lease("agent_runner", ttl_ms=5000)
        assert token_b is not None
        assert token_b != token_a

        # A tries to renew with its now-stale token — must fail closed.
        ok = await store.renew_lease("agent_runner", token_a, ttl_ms=9000)
        assert ok is False

        # B's own renew, meanwhile, succeeds.
        ok_b = await store.renew_lease("agent_runner", token_b, ttl_ms=9000)
        assert ok_b is True

    async def test_renew_on_nonexistent_lease_returns_false(self, store: AgentStateStore):
        ok = await store.renew_lease("never_acquired", "some-token:0", ttl_ms=5000)
        assert ok is False


class TestReleaseLease:
    async def test_release_with_correct_token_frees_the_lease(self, store: AgentStateStore):
        """(c) — release only releases if you hold the token."""
        token = await store.acquire_lease("kb_runner", ttl_ms=5000)
        assert token is not None
        await store.release_lease("kb_runner", token)

        # Freed — a new acquire now succeeds.
        new_token = await store.acquire_lease("kb_runner", ttl_ms=5000)
        assert new_token is not None
        assert new_token != token

    async def test_release_with_wrong_token_is_a_noop(self, store: AgentStateStore):
        """(c) — a stale/foreign token cannot release someone else's lease."""
        token = await store.acquire_lease("kb_runner", ttl_ms=5000)
        assert token is not None

        await store.release_lease("kb_runner", "wrong-token:99")

        # Still held — a fresh acquire attempt fails.
        second = await store.acquire_lease("kb_runner", ttl_ms=5000)
        assert second is None

        # The rightful holder can still release it itself.
        await store.release_lease("kb_runner", token)
        third = await store.acquire_lease("kb_runner", ttl_ms=5000)
        assert third is not None

    async def test_release_on_nonexistent_lease_does_not_raise(self, store: AgentStateStore):
        await store.release_lease("never_acquired", "some-token:0")  # must not raise

    async def test_release_then_renew_by_old_holder_fails(self, store: AgentStateStore):
        token = await store.acquire_lease("agent_runner", ttl_ms=5000)
        assert token is not None
        await store.release_lease("agent_runner", token)

        ok = await store.renew_lease("agent_runner", token, ttl_ms=5000)
        assert ok is False


class TestRunnerLeaseGuard:
    """Light coverage of the production wiring both async runners share."""

    async def test_acquire_forever_returns_once_free(self, store: AgentStateStore):
        guard = RunnerLeaseGuard("oracle_runner", ttl_ms=5000, renew_interval_s=999, store=store)
        assert guard.is_valid is False
        await guard.acquire_forever()
        assert guard.is_valid is True

    async def test_acquire_forever_waits_out_a_held_lease_then_wins(self, store: AgentStateStore):
        # Someone else holds a short-lived lease; acquire_forever must keep
        # retrying (fast backoff for the test) until it expires, then win.
        held_by_other = await store.acquire_lease("agent_runner", ttl_ms=50)
        assert held_by_other is not None

        guard = RunnerLeaseGuard("agent_runner", ttl_ms=5000, renew_interval_s=999, store=store, acquire_retry_s=0.02)
        await asyncio.wait_for(guard.acquire_forever(), timeout=2.0)
        assert guard.is_valid is True

    async def test_renew_or_reacquire_flips_invalid_on_cas_failure(self, store: AgentStateStore):
        guard = RunnerLeaseGuard("agent_runner", ttl_ms=5000, renew_interval_s=999, store=store)
        await guard.acquire_forever()
        assert guard.is_valid is True

        # Simulate losing the lease behind the guard's back: force-expire it
        # and let someone else take it over.
        r = await store._get_redis()
        await r.delete(f"{KEY_LEASE_PREFIX}agent_runner")
        other_token = await store.acquire_lease("agent_runner", ttl_ms=5000)
        assert other_token is not None

        await guard._renew_or_reacquire()
        # The guard's stale token no longer matches — fail closed.
        assert guard.is_valid is False

    async def test_renew_or_reacquire_reclaims_within_the_same_tick_if_free(self, store: AgentStateStore):
        # When the renew fails because the lease simply vanished (TTL lapsed,
        # nobody else grabbed it) rather than because another holder took
        # over, _renew_or_reacquire falls through to a same-tick re-acquire
        # attempt — recovery latency is one renewal interval, not two.
        guard = RunnerLeaseGuard("agent_runner", ttl_ms=5000, renew_interval_s=999, store=store)
        await guard.acquire_forever()

        r = await store._get_redis()
        await r.delete(f"{KEY_LEASE_PREFIX}agent_runner")
        await guard._renew_or_reacquire()
        assert guard.is_valid is True  # reclaimed in the same call

    async def test_renew_or_reacquire_stays_invalid_while_held_elsewhere(self, store: AgentStateStore):
        guard = RunnerLeaseGuard("agent_runner", ttl_ms=5000, renew_interval_s=999, store=store)
        await guard.acquire_forever()

        r = await store._get_redis()
        await r.delete(f"{KEY_LEASE_PREFIX}agent_runner")
        other_token = await store.acquire_lease("agent_runner", ttl_ms=5000)
        assert other_token is not None

        await guard._renew_or_reacquire()
        assert guard.is_valid is False  # someone else holds it — stays fail-closed

        # Once THEY release it, the guard's next tick reclaims it.
        await store.release_lease("agent_runner", other_token)
        await guard._renew_or_reacquire()
        assert guard.is_valid is True

    async def test_release_is_a_noop_if_never_acquired(self, store: AgentStateStore):
        guard = RunnerLeaseGuard("agent_runner", ttl_ms=5000, renew_interval_s=999, store=store)
        await guard.release()  # must not raise
        assert guard.is_valid is False

    async def test_release_then_reacquire_by_someone_else(self, store: AgentStateStore):
        guard = RunnerLeaseGuard("agent_runner", ttl_ms=5000, renew_interval_s=999, store=store)
        await guard.acquire_forever()
        await guard.release()
        assert guard.is_valid is False

        # Freed — someone else can now take it.
        other = await store.acquire_lease("agent_runner", ttl_ms=5000)
        assert other is not None
