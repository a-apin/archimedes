"""On-chain oracle-freshness probe (#1371) — the first backend caller of
``PriceOracle.isFresh()`` / ``lastUpdated()``.

Every deployed ``PriceOracle`` on Arc testnet has been stale since the T3.2
redeploy (2026-07-09) — the on-chain updater address has nonce 0, no push has
ever landed (#1341 owns the cause). Nothing reported this: ``isFresh()`` is a
view function that exists precisely to answer "is this oracle current?" and
had zero callers anywhere in ``backend/`` or ``ui/src/``. This module is the
loud, visible-absence probe CLAUDE.md's fail-soft principle requires for
anything a claim depends on (docs/architectural-principles.md § fail-soft):
a chain-read failure must never be presented as "assume fresh".

**Design constraint — the trap this probe exists to avoid.** The signal MUST
come from an on-chain ``lastUpdated()`` / ``isFresh()`` read, never from
oracle-runner process liveness, a systemd unit, or a Redis "last fetch"
heartbeat. ``oracle_updater.push_prices_on_chain()`` can loop happily every
60s and log "Pushed sSPY" forever while the push itself reverts on-chain —
that is exactly #1341's failure mode, and a liveness-based probe would have
read green for all 42+ days of it. Only a direct chain read can catch it.

**The probed set is derived, never hard-coded.** ``_push_set_symbols()``
reads ``oracle_updater.YFINANCE_MAP`` / ``CRYPTO_MAP`` (read-only — this
module never imports anything that would let it touch those maps; owning them
is #1341's lane) and keeps the entries that are actually on-chain synths,
mirroring the same leading-``"s"`` filter ``oracle_updater.fetch_prices()``
already applies to ``YFINANCE_MAP`` at :184 (excluding the ``^GSPC``/``^VIX``
regime-signal index tickers, which are never pushed on-chain). Today this
resolves to ``{"sSPY", "sBTC"}`` — 2 of the 281 deployed oracles
(``universe.ON_CHAIN_SYNTHS``) — but the probe re-derives it on every call, so
a push-map change lands here automatically with no edit required.

**2-of-281-fresh must never read as globally healthy.** ``oracle_probed_count``
and ``oracle_universe_count`` are both always present on the returned
``OracleHealth`` so a caller can never collapse "the push set is 100% fresh"
into "the oracle subsystem is healthy" — the gap between the two counts is
the whole point of carrying both.

Hermetic test entry point: mock ``archimedes.chain.contracts.get_contract_loader``
(see ``backend/tests/chain/test_oracle_updater.py`` for the exact idiom this
module's tests mirror) — never patch internals.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class OracleHealth:
    """Health diagnostic for the on-chain price-oracle push set (#1371).

    - ``status``: ``"fresh"`` (every probed oracle read succeeded and reported
      ``isFresh() == True``), ``"stale"`` (at least one probed oracle is not
      fresh, but at least one chain read succeeded), or ``"unknown"`` (every
      chain read failed — the state is genuinely unobtainable, not
      confirmed-stale; mirrors the confirmed-absent vs. unobtainable
      distinction in ``oracle_updater._get_reference_price_int``, #587 part 2).
    - ``oracle_fresh``: ``True`` iff EVERY probed oracle read succeeded AND
      reported fresh. Never ``True`` on a partial or total read failure —
      fail-soft ("assume fresh when we can't tell") is wrong here.
    - ``oracle_oldest_age_s``: the oldest ``now - lastUpdated()`` in seconds
      across the oracles that were successfully read; ``None`` only when zero
      reads succeeded (there is no age to report).
    - ``oracle_probed_count``: size of the derived push set (today 2 —
      sSPY + sBTC), independent of how many reads actually succeeded.
    - ``oracle_universe_count``: the full on-chain-deploy-eligible universe
      size (281-class), derived from ``universe.ON_CHAIN_SYNTHS`` — never a
      literal, so a universe change is picked up automatically.
    - ``reason``: human-readable detail, always carrying an explicit
      ``chain_read_failed`` / ``probe_error`` marker on failure so a probe
      outage can never be mistaken for "all oracles fresh" downstream.
    """

    status: str  # "fresh" | "stale" | "unknown"
    oracle_fresh: bool
    oracle_oldest_age_s: int | None
    oracle_probed_count: int
    oracle_universe_count: int
    reason: str = ""


def _push_set_symbols() -> list[str]:
    """Derive the currently-pushed oracle symbols from oracle_updater's maps.

    Read-only: imports ``YFINANCE_MAP`` / ``CRYPTO_MAP`` but never mutates or
    re-exports them. Filters to real on-chain synths the same way
    ``oracle_updater.fetch_prices()`` does at :184 (``k.startswith("s")``),
    which excludes the ``^GSPC`` / ``^VIX`` regime-signal index tickers that
    also live in ``YFINANCE_MAP`` but are not synths and are never pushed.
    """
    from archimedes.chain.oracle_updater import CRYPTO_MAP, YFINANCE_MAP

    equity_synths = {s for s in YFINANCE_MAP if s.startswith("s")}
    crypto_synths = set(CRYPTO_MAP)
    return sorted(equity_synths | crypto_synths)


def _universe_count() -> int:
    """The full 281-class on-chain-deploy-eligible universe size, never a literal."""
    from archimedes.universe import ON_CHAIN_SYNTHS

    return len(ON_CHAIN_SYNTHS)


async def oracle_health() -> OracleHealth:
    """Probe on-chain freshness for the currently-pushed oracle symbols.

    Reads each probed oracle's ``isFresh()`` and ``lastUpdated()`` through the
    existing chain client / contract loader
    (``chain.contracts.get_contract_loader().oracle_for(symbol)``) — the same
    call shape ``oracle_updater._get_reference_price_int`` uses for ``price()``.

    Never raises. Every failure path returns an ``OracleHealth`` with
    ``oracle_fresh=False`` and an explicit marker in ``reason`` — see the
    module docstring's fail-soft note. A read failure is NEVER silently
    absent and NEVER reported as fresh.
    """
    universe_count = _universe_count()

    try:
        symbols = _push_set_symbols()
    except Exception as exc:
        logger.warning("oracle_health: could not derive push set: %s", exc)
        return OracleHealth(
            status="unknown",
            oracle_fresh=False,
            oracle_oldest_age_s=None,
            oracle_probed_count=0,
            oracle_universe_count=universe_count,
            reason=f"oracle_health probe_error: could not derive push set ({exc})",
        )

    probed_count = len(symbols)
    if probed_count == 0:
        logger.warning("oracle_health: push set is empty — nothing to probe")
        return OracleHealth(
            status="unknown",
            oracle_fresh=False,
            oracle_oldest_age_s=None,
            oracle_probed_count=0,
            oracle_universe_count=universe_count,
            reason="oracle_health probe_error: no push-set symbols configured",
        )

    from archimedes.chain.contracts import get_contract_loader

    now = time.time()
    ages: list[int] = []
    all_fresh_flags: list[bool] = []
    errors: list[str] = []

    try:
        loader = get_contract_loader()
    except Exception as exc:
        logger.warning("oracle_health: could not obtain contract loader: %s", exc)
        return OracleHealth(
            status="unknown",
            oracle_fresh=False,
            oracle_oldest_age_s=None,
            oracle_probed_count=probed_count,
            oracle_universe_count=universe_count,
            reason=f"oracle_health chain_read_failed: contract loader unavailable ({exc})",
        )

    for symbol in symbols:
        try:
            oracle = loader.oracle_for(symbol)
            is_fresh = await oracle.functions.isFresh().call()
            last_updated = await oracle.functions.lastUpdated().call()
            ages.append(max(0, int(now - int(last_updated))))
            all_fresh_flags.append(bool(is_fresh))
        except Exception as exc:
            errors.append(f"{symbol}: {exc}")

    if not ages:
        # Every probed read failed — unobtainable, not confirmed-stale (mirrors
        # oracle_updater._get_reference_price_int's confirmed-absent vs.
        # unobtainable distinction). Fail loud: oracle_fresh stays False.
        # Note: the loud, greppable HEALTH_ORACLE_STALE marker (metric-filtered
        # in infra/cloudwatch.tf) is emitted once by the /health handler in
        # main.py, not here — this WARNing is the probe-level detail log.
        logger.warning(
            "oracle_health: chain read failed for all %d probed oracle(s): %s",
            probed_count,
            "; ".join(errors),
        )
        return OracleHealth(
            status="unknown",
            oracle_fresh=False,
            oracle_oldest_age_s=None,
            oracle_probed_count=probed_count,
            oracle_universe_count=universe_count,
            reason=f"oracle_health chain_read_failed: {'; '.join(errors)}",
        )

    oldest_age = max(ages)
    fresh_count = sum(1 for f in all_fresh_flags if f)
    all_fresh = fresh_count == probed_count and not errors

    if all_fresh:
        status = "fresh"
        reason = f"{fresh_count}/{probed_count} probed oracle(s) fresh (of {universe_count} in the universe)"
    else:
        status = "stale"
        reason = (
            f"{fresh_count}/{probed_count} probed oracle(s) fresh, oldest age {oldest_age}s "
            f"(of {universe_count} in the universe)"
        )
        if errors:
            reason += f"; {len(errors)} read error(s): {'; '.join(errors)}"

    return OracleHealth(
        status=status,
        oracle_fresh=all_fresh,
        oracle_oldest_age_s=oldest_age,
        oracle_probed_count=probed_count,
        oracle_universe_count=universe_count,
        reason=reason,
    )
