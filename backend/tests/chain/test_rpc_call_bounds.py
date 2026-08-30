"""One JSON-RPC call must not outlast the timeout we document for it.

``ChainSettings.rpc_timeout_seconds`` is written up as the budget that keeps a
dead RPC from blowing through /health's ECS healthCheck window. It did not do
that. web3 7.x retries allowlisted methods (``eth_call``, ``eth_chainId``,
``eth_getBalance`` — 43 of them) five times by default, and the aiohttp timeout
we passed bounds ONE ATTEMPT, so the real ceiling was five times the documented
one: 16.9s measured against a stated 3s. That is what turns a brief RPC blip
into a 90s nginx 504 on /api/config/contracts instead of a 3s degraded read.

These tests pin the budget rather than the symptom. Most are arithmetic and
configuration checks so they cost nothing; one measures real wall-clock against
a local socket that accepts and never answers, because an arithmetic test alone
cannot prove the numbers reach web3.

Hermetic: the wall-clock test binds 127.0.0.1 on an ephemeral port. No outbound
network, no RPC, no services.
"""

from __future__ import annotations

import asyncio
import socket
import threading
import time

import pytest
from aiohttp import ClientTimeout
from archimedes.chain.client import (
    RPC_BACKOFF_FACTOR,
    ChainClient,
    ChainSettings,
    rpc_retry_policy,
)
from web3.providers import AsyncHTTPProvider

# web3 sleeps backoff_factor * 2**i between attempts, for i = 0 .. retries-2.
_backoff_total = lambda retries: RPC_BACKOFF_FACTOR * (2 ** (retries - 1) - 1)  # noqa: E731


@pytest.fixture
def silent_rpc():
    """A listening socket that never answers — connect() succeeds, read hangs.

    The read-timeout path is the one that matters: a blackholed NAT and an
    overloaded RPC both look like this to the client, and both are what the
    retry multiplier turns into a minute-long request.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    sock.listen(8)  # backlog only; nothing ever accept()s
    yield f"http://127.0.0.1:{sock.getsockname()[1]}"
    sock.close()


class TestBudgetArithmetic:
    @pytest.mark.parametrize("total", [0.05, 0.5, 1.0, 3.0, 8.0, 30.0])
    @pytest.mark.parametrize("retries", [1, 2, 3, 5])
    def test_the_returned_policy_always_fits_inside_the_total(self, total, retries):
        """No hole in the matrix. The tight budgets matter most: they are the ones
        where honouring the requested attempt count would blow the budget, and
        shortening the timeout while keeping the attempts multiplies it right back."""
        per_attempt, attempts = rpc_retry_policy(total, retries)
        assert per_attempt > 0, "a non-positive per-attempt timeout fails every call instantly"
        worst_case = attempts * per_attempt + _backoff_total(attempts)
        assert worst_case <= total + 1e-9, (
            f"{attempts} attempts of {per_attempt:.4f}s worst-case {worst_case:.4f}s > budget {total}s"
        )

    def test_a_budget_too_small_to_split_drops_to_one_attempt_and_says_so(self, caplog):
        """0.2s over 5 attempts leaves 1.875s of mandatory backoff on its own, so
        a per-attempt timeout that fit would have to be negative."""
        with caplog.at_level("WARNING"):
            per_attempt, attempts = rpc_retry_policy(0.2, 5)
        assert (per_attempt, attempts) == (0.2, 1), "keeping 5 attempts here spends 1.0s against a 0.2s budget"
        assert "cannot cover" in caplog.text, "a silently degraded retry policy is the failure mode, not the fix"

    def test_a_feasible_budget_keeps_every_attempt_it_was_asked_for(self):
        """Guards the guard above: a fallback that also fired on healthy budgets
        would satisfy every bound in this file while quietly deleting all retries."""
        assert rpc_retry_policy(3.0, 2)[1] == 2
        assert rpc_retry_policy(10.0, 3)[1] == 3

    def test_retries_below_one_still_yields_one_attempt(self):
        assert rpc_retry_policy(3.0, 0) == rpc_retry_policy(3.0, 1)


class TestProviderConfiguration:
    def test_the_attempt_count_reaches_the_provider(self):
        settings = ChainSettings(arc_rpc_url="http://127.0.0.1:1", rpc_retries=2)
        provider = ChainClient(settings).w3.provider
        assert provider.exception_retry_configuration.retries == 2

    def test_the_provider_timeout_is_the_derived_one_not_the_raw_total(self):
        settings = ChainSettings(arc_rpc_url="http://127.0.0.1:1", rpc_timeout_seconds=3.0, rpc_retries=2)
        timeout = ChainClient(settings).w3.provider._request_kwargs["timeout"]
        assert isinstance(timeout, ClientTimeout), "aiohttp session eviction reads .total off a ClientTimeout"
        assert timeout.total == rpc_retry_policy(3.0, 2)[0]
        assert timeout.total < 3.0, "passing the whole budget as the per-attempt timeout is the bug this fixes"

    def test_which_failures_retry_is_unchanged_from_web3s_default(self):
        """Only the attempt COUNT is ours. Narrowing the error set would silently
        stop retrying transient connection resets."""
        ours = ChainClient(ChainSettings(arc_rpc_url="http://127.0.0.1:1")).w3.provider
        assert set(ours.exception_retry_configuration.errors) == set(
            AsyncHTTPProvider("http://127.0.0.1:1").exception_retry_configuration.errors
        )
        assert set(ours.exception_retry_configuration.method_allowlist) == set(
            AsyncHTTPProvider("http://127.0.0.1:1").exception_retry_configuration.method_allowlist
        )

    def test_web3s_own_default_is_still_the_five_this_fix_defends_against(self):
        """Anti-vacuity. If upstream ever drops to 1 attempt the tests above keep
        passing while proving nothing, so pin the number they are defending against."""
        assert AsyncHTTPProvider("http://127.0.0.1:1").exception_retry_configuration.retries == 5


class TestMeasuredWallClock:
    async def test_a_retried_method_finishes_inside_the_budget(self, silent_rpc):
        """eth_chainId is on web3's retry allowlist — the multiplied path.

        Budget 1.0s over 2 attempts. Unpatched (whole budget per attempt, 5 of
        them) this is 5 x 1.0 + 1.875 = 6.9s; dropping only the retry config is
        5 x 0.4375 + 1.875 = 4.1s. The 2.0s assertion sits well clear of both.
        """
        client = ChainClient(ChainSettings(arc_rpc_url=silent_rpc, rpc_timeout_seconds=1.0, rpc_retries=2))
        started = time.perf_counter()
        with pytest.raises((TimeoutError, OSError)):
            await client.w3.eth.chain_id
        elapsed = time.perf_counter() - started
        assert elapsed < 2.0, f"eth_chainId took {elapsed:.2f}s against a 1.0s budget — the retry multiplier is back"

    async def test_the_liveness_probe_reports_down_instead_of_hanging(self, silent_rpc):
        client = ChainClient(ChainSettings(arc_rpc_url=silent_rpc, rpc_timeout_seconds=1.0, rpc_retries=2))
        started = time.perf_counter()
        connected = await client.is_connected()
        elapsed = time.perf_counter() - started
        assert connected is False
        assert elapsed < 2.0, f"is_connected() took {elapsed:.2f}s against a 1.0s budget"

    async def test_concurrent_calls_do_not_serialise_into_a_longer_stall(self, silent_rpc):
        """/health and /api/config/contracts fire several reads per request; the
        budget has to hold per-call under concurrency, not just in isolation."""
        client = ChainClient(ChainSettings(arc_rpc_url=silent_rpc, rpc_timeout_seconds=1.0, rpc_retries=2))
        started = time.perf_counter()
        await asyncio.gather(*(client.is_connected() for _ in range(4)))
        elapsed = time.perf_counter() - started
        assert elapsed < 2.0, f"4 concurrent probes took {elapsed:.2f}s — they are serialising"


def _run_with_deadline(fn, deadline: float):
    """Run ``fn`` in a daemon thread and give up after ``deadline`` seconds.

    The unbounded provider this class guards does not fail slowly, it fails
    never — restoring it made the suite hang until the job timeout instead of
    reporting anything. A dead thread is left to the interpreter; the point is
    that the assertion fails with a readable message.
    """
    box: dict[str, object] = {}

    def target():
        started = time.perf_counter()
        try:
            box["value"] = fn()
        except BaseException as exc:
            box["error"] = exc
        box["elapsed"] = time.perf_counter() - started

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    thread.join(timeout=deadline)
    return (not thread.is_alive()), box


class TestSettlementBalanceRead:
    """``SettlementSweeper._usdc_balance_of`` is the sync twin of the above.

    It had no timeout of any kind, and it runs inside ``asyncio.to_thread`` — so
    a dark RPC did not merely stall one sweep, it parked a worker from the
    default executor pool indefinitely, and enough of those stall every other
    ``to_thread`` caller in the process. Measured unpatched: still blocked after
    40s and climbing.
    """

    @pytest.fixture
    def sweeper(self, monkeypatch, silent_rpc):
        from unittest.mock import MagicMock

        from archimedes.marketplace import settlement

        monkeypatch.setenv("RPC_URL", silent_rpc)
        # The module constants are read at import; shrink them so the assertions
        # below cost about a second rather than the 10s production budget.
        monkeypatch.setattr(settlement, "_RPC_TOTAL_BUDGET_SECONDS", 1.0)
        monkeypatch.setattr(settlement, "_RPC_ATTEMPTS", 2)

        sweeper = settlement.SettlementSweeper.__new__(settlement.SettlementSweeper)
        sweeper._settings = MagicMock(usdc_address="0x" + "11" * 20)
        return sweeper

    def test_the_balance_read_gives_up_inside_its_budget(self, sweeper):
        finished, box = _run_with_deadline(lambda: sweeper._usdc_balance_of("0x" + "22" * 20), deadline=2.0)
        assert finished, "balance read still running after 2.0s against a 1.0s budget — it is unbounded again"
        assert isinstance(box.get("error"), Exception), f"expected a timeout, got {box.get('value')!r}"
        assert "timed out" in str(box["error"]).lower(), f"finished for the wrong reason: {box['error']!r}"
        assert box["elapsed"] < 2.0

    def test_the_provider_carries_both_halves_of_the_policy(self, sweeper, monkeypatch):
        """The wall-clock test above would still pass if only the timeout landed
        and the attempt count silently stayed at web3's five, because 2 x 0.44s
        and 5 x 0.44s both finish well inside a 2s deadline."""
        from web3 import Web3

        captured: dict = {}
        original = Web3.HTTPProvider

        def spy(endpoint_uri=None, **kwargs):
            captured.update(kwargs)
            return original(endpoint_uri, **kwargs)

        monkeypatch.setattr(Web3, "HTTPProvider", spy)
        finished, _ = _run_with_deadline(lambda: sweeper._usdc_balance_of("0x" + "22" * 20), deadline=3.0)
        assert finished

        assert captured["request_kwargs"]["timeout"] > 0, "requests with no timeout waits forever"
        assert captured["exception_retry_configuration"].retries == 2, "web3's default of 5 multiplies the budget"
        worst_case = 2 * captured["request_kwargs"]["timeout"] + _backoff_total(2)
        assert worst_case <= 1.0 + 1e-9
