"""Runner exactly-once lease guard — shared by the async singleton runners (#1043).

``oracle_runner`` and ``agent_runner`` are funds-adjacent singletons: exactly
one live copy of each may perform on-chain writes at a time (``agent_runner``'s
rebalance in particular is NOT idempotent — it issues absolute swap amounts,
so a second concurrent copy could double-trade a vault). ``RunnerLeaseGuard``
wraps the Redis lease primitives in ``services/redis_state.py`` with the
acquire-then-independently-renew lifecycle both runners need:

  1. Block at startup (``acquire_forever``) until this process holds the
     lease — a second live copy simply waits here.
  2. Once held, run an INDEPENDENT background renewal loop
     (``start_renewal``) on its own clock (~ttl/3), separate from the
     caller's tick loop — a single tick can run minutes, so renewal must
     never be gated on tick completion.
  3. Expose ``is_valid`` as the single fail-closed signal callers gate
     on-chain writes on: it is False until the lease is actually held, and
     flips False the instant a renewal's compare-and-set fails OR errors
     (Redis unreachable is treated as "lease not provably held", never as
     "assume we still have it").
  4. Best-effort release on SIGTERM (``install_sigterm_release``) — TTL
     expiry remains the real safety net if the process is killed harder
     (SIGKILL) or the release itself fails.

``kb_runner`` does NOT use this class — it is sync (``time.sleep``, not
asyncio) and uses its own small sync-Redis counterpart (see
``services/kb_runner.py``) that talks to the SAME Redis keys via the SAME
compare-and-set/delete Lua scripts imported from ``redis_state.py``.
"""

from __future__ import annotations

import asyncio
import logging
import signal

from archimedes.services.redis_state import AgentStateStore

logger = logging.getLogger(__name__)


class RunnerLeaseGuard:
    """Acquire, independently renew, and fail-closed-gate a runner's exactly-once lease."""

    def __init__(
        self,
        runner_name: str,
        ttl_ms: int,
        renew_interval_s: float,
        *,
        store: AgentStateStore | None = None,
        acquire_retry_s: float = 5.0,
    ) -> None:
        self.runner_name = runner_name
        self.ttl_ms = ttl_ms
        self.renew_interval_s = renew_interval_s
        self.acquire_retry_s = acquire_retry_s
        self.store = store or AgentStateStore()
        self._token: str | None = None
        self._lease_valid = False
        self._renew_task: asyncio.Task | None = None
        self._sigterm_release_task: asyncio.Task | None = None

    @property
    def is_valid(self) -> bool:
        """True only while THIS process currently believes it holds the lease.

        Fail-closed: starts False, and any renewal failure/error flips it
        False immediately. Callers must skip on-chain writes while this is
        False — see ``chain/oracle_runner.py`` / ``chain/agent_runner.py``.
        """
        return self._lease_valid

    async def acquire_forever(self) -> None:
        """Block, retrying with backoff, until the lease is acquired.

        A second live copy of the runner simply waits here — this is the
        exactly-once admission gate for the whole process. Redis being
        unreachable is treated exactly like the lease being held elsewhere:
        we do not proceed without provable exclusivity, so this never gives
        up and never "assumes" acquisition on error.
        """
        attempt = 0
        while True:
            attempt += 1
            try:
                token = await self.store.acquire_lease(self.runner_name, self.ttl_ms)
            except Exception:
                logger.exception(
                    "[lease:%s] acquire attempt %d errored (Redis unreachable?) — retrying in %.0fs",
                    self.runner_name,
                    attempt,
                    self.acquire_retry_s,
                )
                token = None

            if token is not None:
                self._token = token
                self._lease_valid = True
                logger.info("[lease:%s] acquired (token=%s)", self.runner_name, token)
                return

            logger.info(
                "[lease:%s] held elsewhere — waiting (attempt %d, retry in %.0fs)",
                self.runner_name,
                attempt,
                self.acquire_retry_s,
            )
            await asyncio.sleep(self.acquire_retry_s)

    def start_renewal(self) -> asyncio.Task:
        """Start the independent background renewal loop and return its task.

        Runs on its own ~ttl/3 schedule, NOT tied to the caller's tick clock.
        """
        self._renew_task = asyncio.create_task(self._renew_loop(), name=f"lease-renew-{self.runner_name}")
        return self._renew_task

    async def _renew_loop(self) -> None:
        while True:
            await asyncio.sleep(self.renew_interval_s)
            await self._renew_or_reacquire()

    async def _renew_or_reacquire(self) -> None:
        """One renewal tick: renew the held lease, or try to reclaim a lost one.

        On any renew failure/error, flips ``_lease_valid`` False immediately
        (fail closed) and then makes ONE non-blocking attempt to re-acquire —
        if that fails too, the next renewal tick tries again, giving the
        "keep trying to re-acquire" cadence #1043 asks for without blocking
        this loop or the caller's tick loop.
        """
        if self._token is not None:
            try:
                ok = await self.store.renew_lease(self.runner_name, self._token, self.ttl_ms)
            except Exception:
                logger.exception(
                    "[lease:%s] renew errored (Redis unreachable?) — treating lease as LOST (fail closed)",
                    self.runner_name,
                )
                ok = False

            if ok:
                logger.debug("[lease:%s] renewed", self.runner_name)
                self._lease_valid = True
                return

            logger.error(
                "[lease:%s] renew compare-and-set FAILED — lease lost to another holder; "
                "on-chain writes are SKIPPED until it is re-acquired",
                self.runner_name,
            )
            self._lease_valid = False
            self._token = None

        # Not currently held (lost above, or never acquired) — try to reclaim
        # it. Single attempt; if Redis is down or another copy still holds
        # it, the NEXT renewal tick retries.
        try:
            token = await self.store.acquire_lease(self.runner_name, self.ttl_ms)
        except Exception:
            logger.exception("[lease:%s] re-acquire attempt errored", self.runner_name)
            token = None

        if token is not None:
            self._token = token
            self._lease_valid = True
            logger.warning("[lease:%s] RE-ACQUIRED after loss", self.runner_name)

    async def release(self) -> None:
        """Best-effort release — safe to call multiple times / on shutdown."""
        if self._token is None:
            return
        token, self._token = self._token, None
        self._lease_valid = False
        try:
            await self.store.release_lease(self.runner_name, token)
            logger.info("[lease:%s] released", self.runner_name)
        except Exception:
            logger.warning("[lease:%s] release failed (TTL expiry will reclaim it)", self.runner_name, exc_info=True)

    def install_sigterm_release(self) -> None:
        """Best-effort: release the lease on SIGTERM.

        Fire-and-forget — this does NOT alter process shutdown semantics.
        TTL expiry remains the real safety net (SIGKILL, an event-loop-less
        thread, or a platform without signal handlers all skip this).
        """
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return

        def _on_sigterm() -> None:
            logger.warning("[lease:%s] SIGTERM received — best-effort lease release", self.runner_name)
            # Keep a reference — an unreferenced task can be garbage
            # collected before it completes (asyncio gotcha), which would
            # silently drop the release.
            self._sigterm_release_task = asyncio.ensure_future(self.release())

        try:
            loop.add_signal_handler(signal.SIGTERM, _on_sigterm)
        except (NotImplementedError, RuntimeError) as exc:
            logger.debug("[lease:%s] could not install SIGTERM handler: %s", self.runner_name, exc)
