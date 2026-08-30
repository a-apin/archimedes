"""Oracle runner — periodic loop that fetches prices and pushes them on-chain.

Run as a standalone process:
    python -m archimedes.chain.oracle_runner

Env:
    ORACLE_INTERVAL_SECONDS  — how often to push prices (default: 60)
    ARC_OWNER_PRIVATE_KEY    — required for on-chain pushes
    ORACLE_LEASE_TTL_MS      — exactly-once lease TTL (default: 300000 = 5min)
    ORACLE_LEASE_RENEW_S     — lease renewal interval (default: 90s, ~ttl/3)

Exactly-once (#1043): this runner is a funds-adjacent singleton — two live
copies pushing prices concurrently is wasted gas at best and a source of
stale/racing on-chain state at worst. On startup it blocks until it holds a
Redis lease (``RunnerLeaseGuard``); a second copy simply waits. The lease is
renewed on an INDEPENDENT background schedule, and every on-chain price push
is gated on the lease still being held — if it's lost mid-run, this runner
fails closed (skips the push, keeps retrying) rather than risking a
duplicate write.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys

from archimedes.chain.oracle_updater import OracleUpdater
from archimedes.services.runner_lease import RunnerLeaseGuard

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)

INTERVAL = int(os.getenv("ORACLE_INTERVAL_SECONDS", "60"))

RUNNER_NAME = "oracle_runner"
LEASE_TTL_MS = int(os.getenv("ORACLE_LEASE_TTL_MS", "300000"))  # 5 min
LEASE_RENEW_INTERVAL_S = int(os.getenv("ORACLE_LEASE_RENEW_S", "90"))  # ~ttl/3


async def run() -> None:
    updater = OracleUpdater()
    lease = RunnerLeaseGuard(RUNNER_NAME, LEASE_TTL_MS, LEASE_RENEW_INTERVAL_S)

    logger.info(f"Oracle runner starting — updating every {INTERVAL}s; acquiring exactly-once lease…")
    await lease.acquire_forever()
    lease.start_renewal()
    lease.install_sigterm_release()
    logger.info(f"Oracle runner started — updating every {INTERVAL}s")

    while True:
        try:
            prices = await updater.fetch_prices()
            if prices:
                if lease.is_valid:
                    tx = await updater.push_prices_on_chain(prices)
                    if tx:
                        logger.info(f"Price push complete — first tx: {tx}")
                    else:
                        # NOT "owner key not configured" (#1525 adjudication):
                        # this runner pushes exclusively via the Circle
                        # Developer-Controlled-Wallets API — there is no raw
                        # owner private key on this path at all, so that
                        # phrasing is a holdover from a pre-Circle design and
                        # is almost never the actual cause. `tx` is falsy
                        # whenever zero submissions reached a terminal-success
                        # state this cycle, and every one of those causes
                        # (missing Circle creds, a failed/timed-out tx, a
                        # rejected price) already logs its own WARNING/ERROR
                        # with the real reason inside push_prices_on_chain —
                        # this line only duplicated that detail, with the
                        # wrong cause attached. Downgraded to DEBUG and
                        # reworded rather than removed outright, so a quiet
                        # cycle (nothing to push, nothing wrong) still leaves
                        # a trace at debug verbosity.
                        logger.debug("Prices fetched — no tx reached a terminal-success state this cycle")
                else:
                    logger.error(
                        "[lease:%s] lease NOT held — SKIPPING on-chain price push this cycle "
                        "(fail-closed; will retry once the lease is re-acquired)",
                        RUNNER_NAME,
                    )
            else:
                logger.warning("No prices fetched this cycle")
        except Exception:
            logger.exception("Oracle cycle failed — will retry next interval")

        await asyncio.sleep(INTERVAL)


if __name__ == "__main__":
    # Standalone entrypoint: load .env into os.environ so the SSOT per-synth
    # oracle-address resolution (chain/client.py reads ARC_<SYMBOL>_ORACLE_ADDRESS
    # via os.getenv) sees .env overrides even when this runs as a bare `python -m
    # archimedes.chain.oracle_runner` (no FastAPI main.load_dotenv, no docker
    # env_file). Mirrors main.py; override=False so an exported env / docker
    # env_file wins. Under __main__ so importing this module in tests never loads
    # .env. (Copilot #765)
    from dotenv import load_dotenv

    load_dotenv("../.env", override=False)
    load_dotenv(".env", override=False)
    asyncio.run(run())
