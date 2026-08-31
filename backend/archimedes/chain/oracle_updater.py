"""Oracle updater — fetches real-world prices and pushes them on-chain.

Implements IOracleUpdater from archimedes/interfaces/chain.py.
Equity/ETF prices (and the #775 cross-check's secondary reading) come from
the market-data provider seam (``archimedes.services.market_data_provider``,
#1218) — default provider yfinance, unchanged behavior; vendor-swappable via
``MARKET_DATA_PROVIDER``. Crypto still comes straight from CoinGecko
(unaffected by that seam). Pushes prices via Circle Developer Controlled
Wallets API (the oracle owner wallet is a Circle-managed wallet — no raw
private key available).
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import uuid
from datetime import UTC, datetime

import aiohttp

from archimedes.models.asset import AssetPrice, MarketSnapshot

logger = logging.getLogger(__name__)

# Symbol → yfinance ticker mapping.
#
# Only synths that are actually on the live on-chain universe belong here — the
# fetch loop (fetch_prices) and the secondary cross-check (_cross_check_secondary)
# both key off this map, so a stale entry means a wasted download and a misleading
# "fetched N prices" log every cycle. Retired single-stock synths (sTSLA/sNVDA,
# dropped from the SSOT in #725 pending securities-compliance review) and the
# futures/index synths retired in #842 (sGOLD → sGLD/sXAU, sOIL/sNKY dropped) are
# no longer in universe.ON_CHAIN_SYNTHS, so they are removed here too. sSPY is the
# only live synth with a yfinance ticker; the ^-prefixed entries are index tickers
# (not synths) used for regime signals — the S&P 500 moving averages
# (_fetch_sp500_moving_averages) and VIX (fetch_market_snapshot) — and are excluded
# from the synth fetch loop by the leading-"s" filter, so they stay.
YFINANCE_MAP = {
    "sSPY": "SPY",
    "^GSPC": "^GSPC",  # S&P 500 index (regime signal, not a synth)
    "^VIX": "^VIX",  # VIX index (regime signal, not a synth)
}

# Symbol → CoinGecko ID
CRYPTO_MAP = {
    "sBTC": "bitcoin",
}

CIRCLE_API_BASE = "https://api.circle.com/v1/w3s"
CIRCLE_BLOCKCHAIN = "ARC-TESTNET"

# ─── Push-confirmation polling (#905) ───
# A 201 from Circle's contractExecution endpoint means "submission accepted",
# NOT "landed on-chain" — the tx can still FAIL/revert later. Every push is
# polled to a terminal state before the price is treated as pushed; mirrors
# circle_signer._poll_transaction's states/cadence.
#
# Split into success/failure subsets (#1525) so a submission response can be
# graded correctly: "COMPLETE"/"CONFIRMED" are terminal successes (Circle can
# return either depending on how far the tx got before the API call returned
# — e.g. a fast testnet tx that already landed by the time the create-call's
# response is built), "FAILED"/"DENIED"/"CANCELLED" are terminal failures.
# Before #1525, the create-response handler only trusted HTTP 201 and logged
# EVERY OTHER response — including a 200 carrying a terminal-success state —
# as "Circle API error", which buried genuine failures under bogus ones.
_TX_SUCCESS_STATES = {"COMPLETE", "CONFIRMED"}
_TX_FAILURE_STATES = {"FAILED", "DENIED", "CANCELLED"}
_TX_TERMINAL_STATES = _TX_SUCCESS_STATES | _TX_FAILURE_STATES
_TX_POLL_INTERVAL_S = 2.0
_TX_MAX_POLLS = 60  # 2 minutes max per tx

# ─── Wedged-tx abandonment (#1525) ───
# _TX_MAX_POLLS/_TX_POLL_INTERVAL_S bound how long ONE push cycle will poll a
# tx before giving up (2 minutes). They do NOT protect against a tx that is
# wedged on Circle's side across MANY CYCLES: the per-symbol idempotency key
# is deterministic on (wallet, contract, price), so an unchanged price (e.g.
# an equity synth over a weekend) reproduces the SAME key every cycle, Circle
# dedups to the SAME never-resolving tx id, and this runner re-polls that one
# id forever with no path back to a fresh submission. After this many
# consecutive cycles seeing the identical unresolved id for a symbol, abandon
# it (WARN) and force a new Circle tx next cycle. Override via
# ORACLE_TX_MAX_REPOLLS.
DEFAULT_TX_MAX_REPOLLS = 10

# ─── Sanity bounds for on-chain pushes (audit #13 / issue #508) ───
# Max allowed move vs the last known good price, in basis points.
# Default mirrors PriceOracle.sol's maxDeviationBps (2000 bps = 20%) so the
# backend rejects a corrupted upstream price before spending a tx the
# contract would revert anyway. Override via ORACLE_MAX_DEVIATION_BPS.
DEFAULT_MAX_DEVIATION_BPS = 2000
# Max age of the upstream observation before we refuse to push it on-chain.
# Override via ORACLE_MAX_UPSTREAM_STALENESS_SECONDS.
DEFAULT_MAX_UPSTREAM_STALENESS_SECONDS = 900  # 15 minutes
# Secondary-source cross-check band (#775): the max divergence (bps) between a
# NON-yfinance primary (Pyth/Stork via the PRICE_SOURCE cascade) and an
# independent yfinance reading before we fail closed. Generous default — both
# sources lag between updates; Önder owns tuning. 0 disables the cross-check.
DEFAULT_CROSSCHECK_BAND_BPS = 5000  # 50%
# Secondary-source staleness window (#775 Phase 2): max age (seconds) of the
# yfinance secondary's bar timestamp before it's treated as unusable for the
# cross-check (fail-open, same as a missing secondary). yfinance's
# interval="1m" intraday bar goes stale over weekends/holidays for equity-like
# instruments — a Friday-close bar can be up to ~65 hours old by Monday
# morning before market open, so the threshold needs headroom past a normal
# 2-day weekend to avoid spurious staleness flags every Monday; 4 days covers
# a 3-day holiday weekend too.
DEFAULT_CROSSCHECK_MAX_STALENESS_SECONDS = 345_600  # 4 days


def _int_env(name: str, default: int) -> int:
    """Parse an integer env var, failing SAFE to ``default`` rather than raising at
    oracle-runner startup.

    A missing or blank value silently uses ``default`` — an unset optional var is
    normal and must not log on every startup. A value that is PRESENT but non-numeric
    (a typo) is a real misconfiguration, so it logs a warning before falling back. A
    bare ``int(os.getenv(...))`` would instead crash the runner on either.
    """
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default  # unset/blank optional var → default, no warning (not a misconfig)
    try:
        return int(raw.strip())
    except ValueError:
        logger.warning("invalid %s=%r (not an integer) — falling back to %d", name, raw, default)
        return default


def _encrypt_entity_secret(entity_secret_hex: str, public_key_pem: str) -> str:
    """Encrypt entity secret with Circle's RSA public key (OAEP/SHA-256).

    Circle requires a fresh ciphertext per request to prevent replay attacks.
    """
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding

    public_key = serialization.load_pem_public_key(public_key_pem.encode())
    plaintext = bytes.fromhex(entity_secret_hex)
    ciphertext = public_key.encrypt(
        plaintext,
        padding.OAEP(
            mgf=padding.MGF1(algorithm=hashes.SHA256()),
            algorithm=hashes.SHA256(),
            label=None,
        ),
    )
    return base64.b64encode(ciphertext).decode()


class OracleUpdater:
    """Fetches market prices and pushes them to on-chain PriceOracle contracts."""

    def __init__(self) -> None:
        self._price_cache: dict[str, AssetPrice] = {}
        self._circle_public_key: str | None = None  # cached per instance lifetime

        # Circle credentials from env
        self._api_key: str = os.getenv("CIRCLE_API_KEY", "")
        self._entity_secret: str = os.getenv("CIRCLE_ENTITY_SECRET", "")
        self._wallet_id: str = os.getenv("WALLET_ID", "")

        # Sanity bounds (audit #13 / issue #508). Parsed fail-safe: a non-numeric
        # env var degrades to the default instead of crashing the runner at startup.
        self._max_deviation_bps: int = _int_env("ORACLE_MAX_DEVIATION_BPS", DEFAULT_MAX_DEVIATION_BPS)
        self._max_upstream_staleness_s: int = _int_env(
            "ORACLE_MAX_UPSTREAM_STALENESS_SECONDS", DEFAULT_MAX_UPSTREAM_STALENESS_SECONDS
        )
        # Last price (6-dec int) we successfully submitted, per symbol —
        # fallback deviation reference when the on-chain read fails.
        self._last_pushed_price_int: dict[str, int] = {}

        # Secondary-source cross-check band (#775).
        self._crosscheck_band_bps: int = _int_env("PRICE_CROSSCHECK_BAND_BPS", DEFAULT_CROSSCHECK_BAND_BPS)
        # Secondary-source staleness window (#775 Phase 2).
        self._crosscheck_max_staleness_s: int = _int_env(
            "PRICE_CROSSCHECK_MAX_STALENESS_SECONDS", DEFAULT_CROSSCHECK_MAX_STALENESS_SECONDS
        )

        # Wedged-tx abandonment (#1525).
        self._tx_max_repolls: int = _int_env("ORACLE_TX_MAX_REPOLLS", DEFAULT_TX_MAX_REPOLLS)
        # symbol -> (tx id, consecutive cycles seen unresolved). Cleared as soon
        # as that symbol's tx reaches any terminal state, or a different tx id
        # shows up (a legitimate new submission, not a wedge).
        self._wedge_tracking: dict[str, tuple[str, int]] = {}
        # symbol -> salt bumped on abandonment so the next idempotency key is
        # guaranteed to differ from the wedged one even if the price hasn't
        # moved (the whole reason the old key kept deduping to the dead tx).
        self._tx_retry_salt: dict[str, int] = {}

    # ─── Public API ──────────────────────────────────────────────

    async def fetch_prices(self) -> list[AssetPrice]:
        """Fetch current prices for all synthetic assets.

        The source is governed by the ``PRICE_SOURCE`` env (North Star §10.5):
          • ``yfinance`` (default) — legacy behavior, unchanged; a deploy of this
            change is a no-op until the flag is flipped.
          • ``cascade`` — Pyth Hermes for the symbols it covers, yfinance for the
            rest, CoinGecko for crypto Pyth misses.
          • ``pyth_hermes`` — Pyth only (no yfinance/CoinGecko fallback).
        Admin overrides (``ADMIN_PRICES_JSON``) apply on top in every mode.

        The cascade is the safety net: any Pyth miss/error falls back
        transparently, and every price keeps its true ``source`` + upstream
        timestamp so the on-chain push staleness/deviation gates stay meaningful.
        Zero contract change — only *where* the price comes from differs.
        """
        # Lazy import: keep the `archimedes.services` package (and its import-time
        # cycle) off oracle_updater's module-load path so the standalone oracle
        # runner imports cleanly.
        from archimedes.services.price_source import load_admin_prices, price_source_mode

        now = datetime.now(UTC)
        mode = price_source_mode()
        equity_symbols = {k: v for k, v in YFINANCE_MAP.items() if k.startswith("s")}

        if mode in ("cascade", "pyth_hermes"):
            prices = await self._fetch_cascade(equity_symbols, now, strict_pyth=(mode == "pyth_hermes"))
        else:
            prices = list(await asyncio.to_thread(self._fetch_yfinance, equity_symbols, now))
            prices.extend(await self._fetch_crypto(now))

        # Manual admin overrides (pin/demo) win over the fetched price, in any mode.
        admin = load_admin_prices()
        if admin:
            prices = [admin.get(p.symbol, p) for p in prices]
            have = {p.symbol for p in prices}
            prices.extend(ap for sym, ap in admin.items() if sym not in have)

        for p in prices:
            self._price_cache[p.symbol] = p

        logger.info("Fetched %d prices (source=%s)", len(prices), mode)
        return prices

    async def _fetch_cascade(
        self, equity_symbols: dict[str, str], now: datetime, *, strict_pyth: bool
    ) -> list[AssetPrice]:
        """Pyth-first cascade: Pyth for covered symbols, yfinance/CoinGecko for the
        rest (unless ``strict_pyth``). Pyth failures degrade gracefully to fallback."""
        from archimedes.services.price_source import PYTH_FEED_IDS, fetch_pyth_prices

        pyth_targets = [s for s in (*equity_symbols, *CRYPTO_MAP) if s in PYTH_FEED_IDS]
        pyth = await fetch_pyth_prices(pyth_targets)

        if strict_pyth:
            # Strict Pyth: no fallback by contract. Keep every observation — stale
            # ones are rejected downstream by _validate_for_push and no source fills
            # the gap (that's the strict-mode promise).
            return list(pyth.values())

        # Cascade: a STALE Pyth observation must NOT count as "covered". Otherwise the
        # symbol is dropped downstream by the staleness gate (_validate_for_push) with
        # no yfinance/CoinGecko fallback to fill it — the exact gap the cascade exists
        # to close (e.g. off-hours equity feeds). Only FRESH Pyth prices count as
        # covered; stale ones fall through to the fallback sources below.
        cap = self._max_upstream_staleness_s

        def _is_fresh(p: AssetPrice) -> bool:
            observed = p.timestamp if p.timestamp.tzinfo else p.timestamp.replace(tzinfo=UTC)
            return (now - observed).total_seconds() <= cap

        fresh_pyth = {sym: p for sym, p in pyth.items() if _is_fresh(p)}
        covered = set(fresh_pyth)
        prices: list[AssetPrice] = list(fresh_pyth.values())

        remaining_equity = {k: v for k, v in equity_symbols.items() if k not in covered}
        if remaining_equity:
            prices.extend(await asyncio.to_thread(self._fetch_yfinance, remaining_equity, now))

        # Only fill crypto symbols Pyth didn't freshly cover. _fetch_crypto fetches the
        # whole CRYPTO_MAP, so filter its result to the uncovered set — otherwise a
        # symbol Pyth already covered would be pushed twice.
        uncovered_crypto = {s for s in CRYPTO_MAP if s not in covered}
        if uncovered_crypto:
            crypto = await self._fetch_crypto(now)
            prices.extend(p for p in crypto if p.symbol in uncovered_crypto)

        return prices

    async def push_prices_on_chain(self, prices: list[AssetPrice]) -> str | None:
        """Call PriceOracle.setPrice() for each asset via Circle Wallets API.

        Two phases (#905): submit every price, then poll each Circle tx to a
        terminal state. ``_last_pushed_price_int`` — the deviation guard's
        fallback reference — is updated ONLY for txs that reach ``COMPLETE``.
        Recording it on HTTP 201 (submission accepted) treated a later on-chain
        revert as success: the next tick's deviation guard then compared against
        a price that was never written, silently defeating the guard and masking
        a stuck oracle.
        """
        if not self._api_key or not self._entity_secret or not self._wallet_id:
            logger.warning(
                "Circle credentials not configured "
                "(CIRCLE_API_KEY / CIRCLE_ENTITY_SECRET / WALLET_ID) — "
                "skipping on-chain price push"
            )
            return None

        from archimedes.chain.client import chain_client

        oracle_addresses = chain_client.settings.oracle_addresses
        # (symbol, human price, 6-dec int, Circle tx id) per accepted submission,
        # pending on-chain confirmation.
        submitted: list[tuple[str, float, int, str]] = []
        confirmed_tx_ids: list[str] = []

        async with aiohttp.ClientSession() as session:
            public_key = await self._get_circle_public_key(session)
            if not public_key:
                logger.error("Failed to fetch Circle public key — aborting price push")
                return None

            for price in prices:
                oracle_addr = oracle_addresses.get(price.symbol)
                if not oracle_addr:
                    logger.debug(f"No oracle address for {price.symbol} — skipping")
                    continue

                price_int = int(price.price_usd * 1e6)  # 6 decimals, matches PriceOracle.sol

                # Sanity gate (audit #13 / issue #508): refuse to push prices
                # that are non-positive, stale upstream, or deviate too far
                # from the last known good price.
                rejection = await self._validate_for_push(price, price_int)
                # Secondary-source cross-check (#775): only when the sanity gate
                # already passed (avoid the extra yfinance fetch on a rejected price).
                if rejection is None:
                    rejection = await self._cross_check_secondary(price)
                if rejection is not None:
                    logger.warning(f"Refusing to push {price.symbol} price {price.price_usd}: {rejection}")
                    continue

                # Wedged-tx abandonment (#1525): if the LAST cycle's tx for this
                # symbol has now been seen unresolved for `_tx_max_repolls`
                # consecutive cycles, it is never going to resolve on its own —
                # bump the retry salt so the idempotency key below is
                # guaranteed fresh (Circle would otherwise dedup right back to
                # the same dead tx) and drop the old tracking entry.
                wedged_id, wedged_count = self._wedge_tracking.get(price.symbol, (None, 0))
                if wedged_count >= self._tx_max_repolls:
                    logger.warning(
                        "Circle tx %s for %s re-polled %d consecutive cycles with no "
                        "terminal state — abandoning it and submitting fresh",
                        wedged_id,
                        price.symbol,
                        wedged_count,
                    )
                    self._tx_retry_salt[price.symbol] = self._tx_retry_salt.get(price.symbol, 0) + 1
                    self._wedge_tracking.pop(price.symbol, None)

                try:
                    ciphertext = _encrypt_entity_secret(self._entity_secret, public_key)

                    # Deterministic idempotency key (#F5): derived from the
                    # stable identifying content of this exact call (wallet +
                    # oracle contract + function + price), not a fresh random
                    # UUID per call. A retry of THIS SAME push (same symbol,
                    # same price) now produces the same key, so Circle's
                    # dedup can actually recognize it as a retry, while a
                    # genuinely different push (different oracle or price)
                    # still produces a different key. uuid5 is used so the
                    # result is a validly-formatted UUID per Circle's
                    # IdempotencyKey schema. `retrySalt` defaults to 0 (no
                    # behavior change) and only moves once wedge-abandonment
                    # above has fired for this symbol (#1525).
                    idempotency_source = json.dumps(
                        {
                            "walletId": self._wallet_id,
                            "contractAddress": oracle_addr,
                            "abiFunctionSignature": "setPrice(uint256)",
                            "abiParameters": [str(price_int)],
                            "retrySalt": self._tx_retry_salt.get(price.symbol, 0),
                        },
                        sort_keys=True,
                    )
                    idempotency_key = str(uuid.uuid5(uuid.NAMESPACE_URL, idempotency_source))

                    payload = {
                        "idempotencyKey": idempotency_key,
                        "walletId": self._wallet_id,
                        "contractAddress": oracle_addr,
                        "abiFunctionSignature": "setPrice(uint256)",
                        "abiParameters": [str(price_int)],
                        "feeLevel": "MEDIUM",
                        "blockchain": CIRCLE_BLOCKCHAIN,
                        "entitySecretCiphertext": ciphertext,
                    }

                    async with session.post(
                        f"{CIRCLE_API_BASE}/developer/transactions/contractExecution",
                        json=payload,
                        headers={
                            "Authorization": f"Bearer {self._api_key}",
                            "Content-Type": "application/json",
                        },
                    ) as resp:
                        body = await resp.json()
                        # Circle's create-transaction call can validly return
                        # either 201 or 200 with a transaction id — including a
                        # terminal-success state already (a fast testnet tx, or
                        # an idempotency-key dedup hitting an already-resolved
                        # tx). Grading solely on `resp.status == 201` treated
                        # every one of those legitimate 200s as an error (#1525:
                        # "Circle API error for sBTC (200): {'state': 'COMPLETE'}"
                        # logged every cycle for a push that had already
                        # succeeded). Grade on the payload instead: a usable tx
                        # id whose state isn't a genuine failure is a success;
                        # only a terminal failure state or a response with no
                        # usable id at all is a real error, named explicitly.
                        data = body.get("data") if isinstance(body, dict) else None
                        tx_id = data.get("id") if isinstance(data, dict) else None
                        state = data.get("state") if isinstance(data, dict) else None
                        if tx_id and state not in _TX_FAILURE_STATES:
                            submitted.append((price.symbol, price.price_usd, price_int, tx_id))
                            logger.info(
                                f"Submitted {price.symbol} price {price.price_usd:.2f} → Circle tx {tx_id}"
                                + (f" ({state})" if state else "")
                            )
                        else:
                            logger.error(
                                "Circle API error for %s (%s): %s",
                                price.symbol,
                                resp.status,
                                f"tx {tx_id} state={state}" if tx_id else body,
                            )
                except Exception:
                    logger.exception(f"Failed to push price for {price.symbol}")

            # ── Confirmation phase (#905): poll each submission to a terminal
            # state. Only a terminal-success tx (COMPLETE/CONFIRMED, #1525)
            # counts as pushed; anything else leaves the cached deviation
            # reference untouched and is logged loudly so a stuck oracle is
            # visible to operators, never masked.
            for symbol, price_usd, price_int, tx_id in submitted:
                state = await self._poll_circle_tx(session, tx_id)
                if state in _TX_SUCCESS_STATES:
                    self._last_pushed_price_int[symbol] = price_int
                    confirmed_tx_ids.append(tx_id)
                    self._wedge_tracking.pop(symbol, None)
                    logger.info(f"Pushed {symbol} price {price_usd:.2f} on-chain (Circle tx {tx_id} {state})")
                else:
                    logger.error(
                        "Oracle push for %s (price %.2f, Circle tx %s) ended %s — "
                        "on-chain price NOT updated; deviation-guard reference unchanged",
                        symbol,
                        price_usd,
                        tx_id,
                        state,
                    )
                    # Wedged-tx tracking (#1525): only a TIMEOUT — this exact
                    # tx never reaching ANY terminal state within the poll
                    # budget — is evidence of a wedge. A genuine terminal
                    # failure (FAILED/DENIED/CANCELLED) is a resolved outcome,
                    # not a wedge: clear any prior tracking so a legitimately
                    # failed-then-freshly-retried tx doesn't inherit a stale
                    # count.
                    if state == "TIMEOUT":
                        wedged_id, wedged_count = self._wedge_tracking.get(symbol, (None, 0))
                        self._wedge_tracking[symbol] = (
                            tx_id,
                            wedged_count + 1 if wedged_id == tx_id else 1,
                        )
                    else:
                        self._wedge_tracking.pop(symbol, None)

        return confirmed_tx_ids[0] if confirmed_tx_ids else None

    async def _poll_circle_tx(self, session: aiohttp.ClientSession, circle_tx_id: str) -> str:
        """Poll a Circle tx to a terminal state (#905).

        Same states/cadence as ``circle_signer._poll_transaction``, but returns
        the terminal state string (``"TIMEOUT"`` after ``_TX_MAX_POLLS``) instead
        of raising, so one failed price push cannot abort the confirmation of
        the other symbols in the same batch. Poll errors are treated as
        still-processing — only an explicit terminal state or the timeout ends
        the loop, and only ``COMPLETE`` is ever treated as success.
        """
        for _ in range(_TX_MAX_POLLS):
            try:
                async with session.get(
                    f"{CIRCLE_API_BASE}/transactions?pageSize=50",
                    headers={"Authorization": f"Bearer {self._api_key}"},
                ) as resp:
                    if resp.status == 200:
                        body = await resp.json()
                        for tx in body.get("data", {}).get("transactions", []):
                            if tx.get("id") == circle_tx_id:
                                state = tx.get("state", "UNKNOWN")
                                if state in _TX_TERMINAL_STATES:
                                    return state
                                break  # found but still processing
            except Exception:
                logger.warning("Circle tx %s poll attempt failed — retrying", circle_tx_id, exc_info=True)
            await asyncio.sleep(_TX_POLL_INTERVAL_S)
        return "TIMEOUT"

    async def fetch_market_snapshot(self) -> MarketSnapshot:
        """Fetch a full market snapshot with prices + regime signals."""
        prices = await self.fetch_prices()
        price_map = {p.symbol: p.price_usd for p in prices}
        now = datetime.now(UTC)

        vix_result = await self._fetch_yfinance_single("^VIX")
        vix = vix_result[0] if vix_result is not None else None
        sp500_data = await asyncio.to_thread(self._fetch_sp500_moving_averages)

        snapshot = MarketSnapshot(
            timestamp=now,
            prices=price_map,
            vix=vix,
            sp500_ma50=sp500_data.get("ma50"),
            sp500_ma200=sp500_data.get("ma200"),
        )
        await self._write_redis_price_snapshots(price_map)
        return snapshot

    async def _write_redis_price_snapshots(self, price_map: dict[str, float]) -> None:
        """Publish oracle price snapshot to Redis (#1103)."""
        try:
            import redis.asyncio as _aioredis

            redis_url = (
                os.getenv("REDIS_URL")
                or f"redis://{os.getenv('REDIS_HOST', 'localhost')}:{os.getenv('REDIS_PORT', '6379')}/0"
            )
            r = _aioredis.from_url(redis_url, decode_responses=True)
            await r.set("oracle:prices:latest", json.dumps(price_map))
            await r.aclose()
        except Exception:
            logger.debug("Redis price snapshot write failed", exc_info=True)

    def get_cached_price(self, symbol: str) -> AssetPrice | None:
        return self._price_cache.get(symbol)

    # ─── Private helpers ──────────────────────────────────────────

    async def _validate_for_push(self, price: AssetPrice, price_int: int) -> str | None:
        """Sanity-check a price before pushing on-chain (audit #13 / issue #508).

        Returns a human-readable rejection reason, or None when the price is
        safe to push. Checks, in order:

        1. Zero/non-positive guard — the 6-decimal int must be > 0 (the
           contract rejects zero; sub-microdollar floats truncate to 0).
        2. Upstream staleness — the observation must be younger than
           ``ORACLE_MAX_UPSTREAM_STALENESS_SECONDS`` (default 15 min).
        3. Deviation cap — the move vs the last known good price (on-chain
           read, falling back to the last successfully pushed value) must be
           within ``ORACLE_MAX_DEVIATION_BPS`` (default 2000 = 20%, mirroring
           PriceOracle.sol's maxDeviationBps). A *confirmed-absent* reference
           (genuine first push) is allowed; an *unobtainable* reference (the
           on-chain read failed and there is no cached fallback) fails CLOSED —
           we refuse the push rather than send it unchecked (issue #587).

        Single-source design (deferred multi-source to v2 per issue #508):
        Each symbol currently has exactly one upstream source (yfinance for
        equities/futures, CoinGecko for sBTC). Multi-source corroboration
        would require N independent feeds; that is a v2 enhancement documented
        in docs/design.md § Price Oracle. Today, we validate upstream
        freshness and deviation bounds against the last known good on-chain
        price, but do not require cross-source agreement. This is an
        intentional tradeoff: simplicity for v1 MVP, and the protocol is
        extensible should a second feed be added.
        """
        if price_int <= 0:
            return f"non-positive on-chain price ({price.price_usd} → {price_int})"

        observed = price.timestamp if price.timestamp.tzinfo else price.timestamp.replace(tzinfo=UTC)
        age_s = (datetime.now(UTC) - observed).total_seconds()
        if age_s > self._max_upstream_staleness_s:
            return f"stale upstream data ({age_s:.0f}s old > {self._max_upstream_staleness_s}s cap)"

        reference_int, reference_known = await self._get_reference_price_int(price.symbol)
        if reference_int is None:
            if not reference_known:
                return (
                    "reference price unobtainable (on-chain read failed, no cached "
                    "fallback) — failing closed to avoid unchecked push"
                )
            # reference confirmed absent (genuine first push) → allow
        else:
            deviation_bps = abs(price_int - reference_int) * 10_000 / reference_int
            if deviation_bps > self._max_deviation_bps:
                return (
                    f"deviation {deviation_bps:.0f} bps vs last known price "
                    f"{reference_int / 1e6:.2f} exceeds {self._max_deviation_bps} bps cap"
                )

        return None

    async def _cross_check_secondary(self, price: AssetPrice) -> str | None:
        """Secondary-source cross-check (#775): compare a primary that is NOT
        the active market-data provider (Pyth/Stork via the PRICE_SOURCE
        cascade) against an independent reading FROM the active provider
        (``MARKET_DATA_PROVIDER``, #1218 seam — default yfinance, today's
        behavior) and fail closed when they diverge beyond the band. Returns
        a rejection reason, or None to proceed.

        **The #775/#1218 seam tie-in.** The secondary reading is fetched via
        ``_fetch_yfinance_single`` → ``market_data_provider.get_provider()``,
        and the "same source, skip" guard below compares against
        ``provider_name()`` rather than a hardcoded ``"yfinance"``. Swap
        ``MARKET_DATA_PROVIDER`` to a new vendor and this guardrail's
        secondary source swaps with it automatically — no separate change
        needed here. (The variable/log names below keep saying "yfinance"
        because that is still the only implemented provider; the mechanism is
        vendor-generic.)

        **Asymmetric, by design.** A stale / missing / flaky secondary NEVER
        blocks a healthy primary — otherwise a secondary-provider outage
        (yfinance is known-flaky, #772) would become a trading halt and the
        guardrail itself the failure. Secondary problems only proceed-and-log;
        only a *confident* divergence between two healthy sources fails closed.

        Honest claim this earns: *"primary cross-checked against an independent
        market-data-provider reading, fail-closed on relative-magnitude divergence
        beyond a band, with the secondary's own bar timestamp checked for staleness
        first."* NOT "decentralized 2-of-3" (yfinance — the default/only provider
        today — is centralized + off-chain).

        **Staleness gate (#775 Phase 2).** Before comparing magnitudes, the
        secondary's bar timestamp is checked against
        ``PRICE_CROSSCHECK_MAX_STALENESS_SECONDS``. A stale secondary is treated the
        same as a missing one (fail-open, proceed on primary) — a stale reading was
        never validly comparable, so no divergence verdict is computed or reported,
        only a staleness verdict. This closes the previous magnitude-only gap where a
        stale-but-numerically-close secondary value could pass silently, or a
        stale-and-wildly-different one could in principle trip a false divergence.

        The check is a no-op when the primary already IS the active provider
        (same source, not an independent second opinion), an admin pin (operator
        last-resort override — must not be second-guessed), or when the symbol
        has no ticker mapped for the secondary read.
        """
        if self._crosscheck_band_bps <= 0:
            return None  # disabled
        from archimedes.services.market_data_provider import provider_name

        if price.source in (provider_name(), "admin"):
            # active provider: same source, not an independent second opinion.
            # admin: a last-resort operator pin (ADMIN_PRICES_JSON) — the whole point
            # is to override upstream, so the secondary guardrail must not block it.
            return None
        yf_ticker = YFINANCE_MAP.get(price.symbol)
        if not yf_ticker:
            return None  # no ticker mapped for this symbol → can't cross-check → proceed
        try:
            result = await self._fetch_yfinance_single(yf_ticker)
        except Exception as exc:
            logger.warning(
                "cross-check: yfinance fetch failed for %s (%s) — proceeding on primary (asymmetric)",
                price.symbol,
                exc,
            )
            return None
        if result is None:
            logger.info(
                "cross-check: no usable yfinance secondary for %s — proceeding on primary (asymmetric)",
                price.symbol,
            )
            return None
        secondary, bar_ts = result
        if secondary <= 0:
            logger.info(
                "cross-check: no usable yfinance secondary for %s — proceeding on primary (asymmetric)",
                price.symbol,
            )
            return None

        age_s = (datetime.now(UTC) - bar_ts).total_seconds()
        if age_s > self._crosscheck_max_staleness_s:
            logger.info(
                "cross-check: stale yfinance secondary for %s (bar age %.0fs > %ds cap) — "
                "proceeding on primary (asymmetric)",
                price.symbol,
                age_s,
                self._crosscheck_max_staleness_s,
            )
            return None

        deviation_bps = abs(price.price_usd - secondary) / secondary * 10_000
        if deviation_bps > self._crosscheck_band_bps:
            return (
                f"cross-check FAIL: primary({price.source}) {price.price_usd:.4f} diverges "
                f"{deviation_bps:.0f} bps from independent yfinance {secondary:.4f} "
                f"(band {self._crosscheck_band_bps} bps) — failing closed"
            )
        logger.debug(
            "cross-check OK for %s: primary(%s) %.4f vs yfinance %.4f (%.0f bps)",
            price.symbol,
            price.source,
            price.price_usd,
            secondary,
            deviation_bps,
        )
        return None

    async def _get_reference_price_int(self, symbol: str) -> tuple[int | None, bool]:
        """Resolve the deviation reference price (6-dec int) for a symbol.

        Returns a ``(reference_int, reference_known)`` tuple so the caller can
        distinguish a *confirmed-absent* reference (genuine first push, safe to
        allow) from an *unobtainable* one (on-chain read failed and no cached
        fallback — must fail closed to avoid an unchecked push). The two cases
        both used to surface as a bare ``None``, which let an RPC outage bypass
        the only deviation protection (issue #587, part 2).

        Cases:

        - on-chain read succeeds, price > 0 → ``(price_int, True)``
        - on-chain read succeeds but 0/empty, last-pushed cached → ``(last_pushed, True)``
        - on-chain read succeeds but 0/empty, no cache → ``(None, True)``
          (reference *confirmed absent* — genuine first push)
        - on-chain read throws, last-pushed cached → ``(last_pushed, True)``
          (RPC down, but we still hold a fallback reference)
        - on-chain read throws, no cache → ``(None, False)``
          (reference *unobtainable* — caller must fail closed)
        """
        try:
            from archimedes.chain.contracts import get_contract_loader

            onchain = await get_contract_loader().oracle_for(symbol).functions.price().call()
            if onchain and int(onchain) > 0:
                return int(onchain), True
            # On-chain read succeeded but reports no price yet.
            cached = self._last_pushed_price_int.get(symbol)
            if cached is not None:
                return cached, True
            # Confirmed absent: the read worked and there is genuinely no price.
            return None, True
        except Exception as e:
            logger.warning(f"Could not read on-chain reference price for {symbol}: {e}")
            cached = self._last_pushed_price_int.get(symbol)
            if cached is not None:
                return cached, True
            # Unobtainable: read threw and we have no cached fallback.
            return None, False

    async def _get_circle_public_key(self, session: aiohttp.ClientSession) -> str | None:
        """Fetch Circle's RSA public key (cached per instance)."""
        if self._circle_public_key:
            return self._circle_public_key
        try:
            async with session.get(
                f"{CIRCLE_API_BASE}/config/entity/publicKey",
                headers={"Authorization": f"Bearer {self._api_key}"},
            ) as resp:
                if resp.status == 200:
                    body = await resp.json()
                    self._circle_public_key = body["data"]["publicKey"]
                    return self._circle_public_key
                logger.error(f"Failed to fetch Circle public key: {resp.status}")
        except Exception:
            logger.exception("Error fetching Circle public key")
        return None

    def _fetch_yfinance(self, symbols: dict[str, str], timestamp: datetime) -> list[AssetPrice]:
        """Fetch equity prices via the market-data provider seam (#1218; sync —
        call via to_thread). Default provider is yfinance. ``source`` on the
        returned prices is the ACTIVE provider name, not a hardcoded
        ``"yfinance"``, so a vendor swap is visible on every downstream
        consumer of these prices (including the #775 cross-check).

        **This leg still stamps the POLL time, deliberately — on-chain
        behavior is unchanged here.** ``get_intraday_quotes_batch`` now
        returns ``(price, bar_ts)`` (widened for the paper-marks loop, which
        must store an upstream observation time), so the tuple is unpacked —
        but ``bar_ts`` is discarded on this path and ``timestamp`` (``now``,
        from ``fetch_prices``) is what lands on the ``AssetPrice``.

        That is a known, tracked honesty gap and NOT a silent one:
        ``_validate_for_push`` computes ``age_s`` from this same field, so on
        the yfinance leg the staleness gate compares now against now and
        cannot reject a stale bar (the Pyth cascade does carry a real
        observation time; this leg does not). Stamping ``bar_ts`` here would
        close it — and would ALSO stop every off-hours equity push, because a
        Friday-close bar is hours older than
        ``ORACLE_MAX_UPSTREAM_STALENESS_SECONDS``. That is a live-chain
        behavior change with weekend-freshness consequences (#1528), so it
        does not ride along in a paper-trading change: it is split out to
        ``dbrowneup/oracle-bar-time-stamp`` for its own review.
        ``test_stamps_the_poll_time_unchanged_by_the_widened_batch_seam``
        pins that this branch did not quietly flip it.
        """
        from archimedes.services.market_data_provider import get_provider, provider_name

        quotes = get_provider().get_intraday_quotes_batch(symbols)
        source = provider_name()
        return [
            AssetPrice(symbol=synth_symbol, price_usd=price, timestamp=timestamp, source=source)
            for synth_symbol, (price, _bar_ts) in quotes.items()
        ]

    async def _fetch_crypto(self, timestamp: datetime) -> list[AssetPrice]:
        """Fetch crypto prices from CoinGecko API."""
        results: list[AssetPrice] = []
        try:
            async with aiohttp.ClientSession() as session:
                for symbol, cg_id in CRYPTO_MAP.items():
                    try:
                        url = f"https://api.coingecko.com/api/v3/simple/price?ids={cg_id}&vs_currencies=usd"
                        async with session.get(url) as resp:
                            if resp.status == 200:
                                data = await resp.json()
                                price = data[cg_id]["usd"]
                                results.append(
                                    AssetPrice(
                                        symbol=symbol,
                                        price_usd=price,
                                        timestamp=timestamp,
                                        source="coingecko",
                                    )
                                )
                    except Exception as e:
                        logger.warning(f"Failed to fetch {symbol} from CoinGecko: {e}")
        except Exception as e:
            logger.warning(f"Crypto fetch error: {e}")
        return results

    async def _fetch_yfinance_single(self, symbol: str) -> tuple[float, datetime] | None:
        """Fetch a single provider price + its bar timestamp (e.g. VIX, or the
        secondary-source cross-check's independent reading, #775) via the
        market-data provider seam (#1218). Default provider is yfinance —
        identical behavior to before this seam existed (the fetch logic
        itself moved to ``YFinanceProvider.get_intraday_quote``, unchanged;
        this method now just runs it in a thread).

        Returns ``(price, bar_ts)`` on success with ``bar_ts`` normalized to a
        tz-aware UTC ``datetime``, or ``None`` on any failure (empty data, missing
        column, exception).
        """
        from archimedes.services.market_data_provider import get_provider

        return await asyncio.to_thread(get_provider().get_intraday_quote, symbol)

    def _fetch_sp500_moving_averages(self) -> dict[str, float]:
        """Fetch S&P 500 50-day and 200-day moving averages via the
        market-data provider seam (#1218) — daily bars, so this also benefits
        from the provider's ``asset_daily_bars`` Postgres cache."""
        try:
            from archimedes.services.market_data_provider import get_provider

            close = get_provider().get_daily_close_batch({"^GSPC": "^GSPC"}, period="1y").get("^GSPC")
            if close is None or close.empty:
                return {}

            return {
                "ma50": float(close.rolling(50).mean().iloc[-1]),
                "ma200": float(close.rolling(200).mean().iloc[-1]),
            }
        except Exception as e:
            logger.warning(f"Failed to fetch S&P MA data: {e}")
            return {}
