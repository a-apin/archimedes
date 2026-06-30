"""Oracle updater — fetches real-world prices and pushes them on-chain.

Implements IOracleUpdater from archimedes/interfaces/chain.py.
Uses yfinance for equity/ETF prices and CoinGecko for crypto.
Pushes prices via Circle Developer Controlled Wallets API (the oracle owner
wallet is a Circle-managed wallet — no raw private key available).
"""

from __future__ import annotations

import asyncio
import base64
import logging
import os
import uuid
from datetime import UTC, datetime

import aiohttp

from archimedes.models.asset import AssetPrice, MarketSnapshot

logger = logging.getLogger(__name__)

# Symbol → yfinance ticker mapping
YFINANCE_MAP = {
    "sTSLA": "TSLA",
    "sNVDA": "NVDA",
    "sSPY": "SPY",
    "sGOLD": "GC=F",  # Gold futures
    "sOIL": "CL=F",  # WTI crude oil futures
    "sNKY": "^N225",  # Nikkei 225
    "^GSPC": "^GSPC",  # S&P 500 index
    "^VIX": "^VIX",  # VIX index
}

# Symbol → CoinGecko ID
CRYPTO_MAP = {
    "sBTC": "bitcoin",
}

CIRCLE_API_BASE = "https://api.circle.com/v1/w3s"
CIRCLE_BLOCKCHAIN = "ARC-TESTNET"

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

        # Sanity bounds (audit #13 / issue #508)
        self._max_deviation_bps: int = int(os.getenv("ORACLE_MAX_DEVIATION_BPS", str(DEFAULT_MAX_DEVIATION_BPS)))
        self._max_upstream_staleness_s: int = int(
            os.getenv("ORACLE_MAX_UPSTREAM_STALENESS_SECONDS", str(DEFAULT_MAX_UPSTREAM_STALENESS_SECONDS))
        )
        # Last price (6-dec int) we successfully submitted, per symbol —
        # fallback deviation reference when the on-chain read fails.
        self._last_pushed_price_int: dict[str, int] = {}

        # Secondary-source cross-check band (#775).
        self._crosscheck_band_bps: int = int(os.getenv("PRICE_CROSSCHECK_BAND_BPS", str(DEFAULT_CROSSCHECK_BAND_BPS)))

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
        covered = set(pyth)
        prices: list[AssetPrice] = list(pyth.values())

        if not strict_pyth:
            remaining_equity = {k: v for k, v in equity_symbols.items() if k not in covered}
            if remaining_equity:
                prices.extend(await asyncio.to_thread(self._fetch_yfinance, remaining_equity, now))
            if any(s not in covered for s in CRYPTO_MAP):
                prices.extend(await self._fetch_crypto(now))

        return prices

    async def push_prices_on_chain(self, prices: list[AssetPrice]) -> str | None:
        """Call PriceOracle.setPrice() for each asset via Circle Wallets API."""
        if not self._api_key or not self._entity_secret or not self._wallet_id:
            logger.warning(
                "Circle credentials not configured "
                "(CIRCLE_API_KEY / CIRCLE_ENTITY_SECRET / WALLET_ID) — "
                "skipping on-chain price push"
            )
            return None

        from archimedes.chain.client import chain_client

        oracle_addresses = chain_client.settings.oracle_addresses
        tx_ids: list[str] = []

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

                try:
                    ciphertext = _encrypt_entity_secret(self._entity_secret, public_key)

                    payload = {
                        "idempotencyKey": str(uuid.uuid4()),
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
                        if resp.status == 201:
                            tx_id = body["data"]["id"]
                            tx_ids.append(tx_id)
                            self._last_pushed_price_int[price.symbol] = price_int
                            logger.info(f"Pushed {price.symbol} price {price.price_usd:.2f} → Circle tx {tx_id}")
                        else:
                            logger.error(f"Circle API error for {price.symbol} ({resp.status}): {body}")
                except Exception:
                    logger.exception(f"Failed to push price for {price.symbol}")

        return tx_ids[0] if tx_ids else None

    async def fetch_market_snapshot(self) -> MarketSnapshot:
        """Fetch a full market snapshot with prices + regime signals."""
        prices = await self.fetch_prices()
        price_map = {p.symbol: p.price_usd for p in prices}
        now = datetime.now(UTC)

        vix = await self._fetch_yfinance_single("^VIX")
        sp500_data = await asyncio.to_thread(self._fetch_sp500_moving_averages)

        return MarketSnapshot(
            timestamp=now,
            prices=price_map,
            vix=vix,
            sp500_ma50=sp500_data.get("ma50"),
            sp500_ma200=sp500_data.get("ma200"),
        )

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
        """Secondary-source cross-check (#775): compare a NON-yfinance primary
        (Pyth/Stork via the PRICE_SOURCE cascade) against an independent yfinance
        reading and fail closed when they diverge beyond the band. Returns a
        rejection reason, or None to proceed.

        **Asymmetric, by design.** A stale / missing / flaky yfinance NEVER blocks
        a healthy primary — otherwise a yfinance outage (it's known-flaky, #772)
        would become a trading halt and the guardrail itself the failure. yfinance
        problems only proceed-and-log; only a *confident* divergence between two
        healthy sources fails closed.

        Honest claim this earns: *"primary cross-checked against an independent
        yfinance market source, fail-closed on divergence beyond a band."* NOT
        "decentralized 2-of-3" (yfinance is centralized + off-chain). The check is
        a no-op when the primary IS yfinance (same source — not independent) or
        when no yfinance ticker exists for the symbol. Band/staleness thresholds
        are Önder's lane to tune (collaborate per #775).
        """
        if self._crosscheck_band_bps <= 0:
            return None  # disabled
        if price.source == "yfinance":
            return None  # primary IS yfinance — not an independent second source
        yf_ticker = YFINANCE_MAP.get(price.symbol)
        if not yf_ticker:
            return None  # no independent yfinance ticker for this symbol → can't cross-check → proceed
        try:
            secondary = await self._fetch_yfinance_single(yf_ticker)
        except Exception as exc:
            logger.warning(
                "cross-check: yfinance fetch failed for %s (%s) — proceeding on primary (asymmetric)",
                price.symbol,
                exc,
            )
            return None
        if secondary is None or secondary <= 0:
            logger.info(
                "cross-check: no usable yfinance secondary for %s — proceeding on primary (asymmetric)",
                price.symbol,
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
        """Fetch prices from yfinance (sync — call via to_thread)."""
        try:
            import yfinance as yf

            tickers_str = " ".join(symbols.values())
            data = yf.download(tickers_str, period="1d", interval="1m", progress=False)

            results: list[AssetPrice] = []
            for synth_symbol, yf_ticker in symbols.items():
                try:
                    if data.empty:
                        continue
                    if len(symbols) == 1:
                        close = data["Close"]
                        # yfinance >=1.0 returns a 1-column DataFrame (MultiIndex
                        # columns) for single-ticker downloads; squeeze to a Series
                        # before taking the last value.
                        if hasattr(close, "columns"):
                            close = close.iloc[:, 0]
                        price = float(close.iloc[-1])
                    else:
                        close = data["Close"]
                        if yf_ticker in close.columns:
                            price = float(close[yf_ticker].dropna().iloc[-1])
                        else:
                            continue

                    results.append(
                        AssetPrice(
                            symbol=synth_symbol,
                            price_usd=price,
                            timestamp=timestamp,
                            source="yfinance",
                        )
                    )
                except Exception as e:
                    logger.warning(f"Failed to fetch {synth_symbol}: {e}")

            return results
        except ImportError:
            logger.warning("yfinance not installed — returning empty prices")
            return []

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

    async def _fetch_yfinance_single(self, symbol: str) -> float | None:
        """Fetch a single yfinance price (e.g. VIX)."""
        try:
            import yfinance as yf

            data = await asyncio.to_thread(yf.download, symbol, period="1d", interval="1m", progress=False)
            if not data.empty:
                close = data["Close"]
                # yfinance >=1.0 returns a 1-column DataFrame for single-ticker
                # downloads; squeeze to a Series before taking the last value.
                if hasattr(close, "columns"):
                    close = close.iloc[:, 0]
                return float(close.iloc[-1])
        except Exception as e:
            logger.warning(f"Failed to fetch {symbol}: {e}")
        return None

    def _fetch_sp500_moving_averages(self) -> dict[str, float]:
        """Fetch S&P 500 50-day and 200-day moving averages."""
        try:
            import yfinance as yf

            spy = yf.Ticker("^GSPC")
            hist = spy.history(period="1y")
            if hist.empty:
                return {}

            return {
                "ma50": float(hist["Close"].rolling(50).mean().iloc[-1]),
                "ma200": float(hist["Close"].rolling(200).mean().iloc[-1]),
            }
        except Exception as e:
            logger.warning(f"Failed to fetch S&P MA data: {e}")
            return {}
