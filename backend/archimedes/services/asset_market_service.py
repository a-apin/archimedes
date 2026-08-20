"""Composes on-chain oracle + history data into the /api/explore/assets response.

Primary price source: on-chain ``PriceOracle.getPrice(token)`` via ``chain_client``.
Fall back to yfinance histories for change windows / vol and as a price
fallback when the oracle is stale or unavailable.  30-second TTL cache
(per the Phase 3a spec — page must load <1s without synchronous on-chain
reads on cache hit). The plain-English explanations live in this module so
the route handler stays a thin facade.

Universe (aligned 2026-07 — #759 follow-up to PR #842): the listing iterates
the **deploy-eligible SSOT universe** (``archimedes.universe.ON_CHAIN_SYNTHS``,
~280 synths) — the same set the Generate picker is generated from
(``scripts/gen_ui_asset_universe.py``) — NOT the legacy ~74-name
``DEFAULT_SCAN_UNIVERSE`` scan subset. Every universe symbol gets a card; a
symbol whose data hasn't been fetched yet is served honestly with
``price_source="none"`` rather than being dropped. ``universe_size`` /
``priced_count`` on the response disclose how complete the data is. Because a
cold fetch of ~280 yfinance histories can exceed the request budget, histories
are fetched in chunks under a total time budget and partial results are served;
the evaluator's module-level ``_PRICE_CACHE`` warms across requests, so
follow-up requests converge to full coverage.

Staleness semantics (rebuilt 2026-05-25 — see Explore page rebuild):
The on-chain ``PriceOracle`` is only actively pushed for a small subset of
synths (those in ``OracleUpdater.YFINANCE_MAP`` / ``CRYPTO_MAP`` — currently
~9 symbols). The rest of the universe has an
oracle slot allocated but no one calls ``setPrice()`` for it. Flagging those
assets as "STALE" when in fact the displayed price comes from yfinance is
misleading — the *displayed* price isn't stale; an unused oracle slot is. So
``is_stale`` now reflects the actual displayed price source, not the oracle
slot. ``price_source`` discloses where the displayed price came from.
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
from datetime import UTC, datetime
from typing import Any, Literal

from archimedes.api.explore_schemas import (
    AssetExploreItem,
    ExploreAssetsResponse,
    ExploreHistoryPoint,
    ExploreHistoryResponse,
)

logger = logging.getLogger(__name__)


_CACHE_TTL_SECONDS = 30
_HISTORY_LOOKBACK = "3mo"  # enough for 30d realized vol + change windows
_STALE_WINDOW_SECONDS = 5 * 60  # >5 min since oracle push → "stale" (per issue #168)
_YF_STALE_WINDOW_SECONDS = 4 * 24 * 60 * 60  # yfinance daily-close → stale if >4 days old
_ORACLE_READ_TIMEOUT = 5  # seconds per individual chain read
# Total budget across ALL sequential oracle reads. With the ~280-synth SSOT
# universe, an unresponsive RPC at 5s/read would otherwise stall the request
# for minutes; symbols past the deadline are skipped (their card falls back
# to yfinance / "none").
_ORACLE_TOTAL_BUDGET_SECONDS = 12.0
# yfinance history fetch: chunked batch calls under one total budget. Chunks
# keep each yf.download() call bounded so a slow upstream loses at most one
# chunk of coverage instead of the whole universe; whatever fetched before the
# budget ran out is served as an honest partial result. A timed-out chunk's
# worker thread still completes in the background and populates the
# evaluator's _PRICE_CACHE (600s TTL), so subsequent requests fill the gaps
# from cache instead of re-paying the cold fetch.
_HISTORY_CHUNK_SIZE = 60
_HISTORY_FETCH_BUDGET_SECONDS = 45.0

# Plausibility bound for computed pct changes (#1322). A bad tick / decimal-
# placement error in the upstream feed (a tiny prior close) can turn into an
# explosive ratio that passes every other check — e.g. sJUP's prior close of
# 0.0003166165 produced a reported "+1483.08% 24h" move. 100%/day is already
# an extremely generous bound (no major asset has ever moved >100% in a
# single day without a corporate-action-style event). Multi-day windows
# (n > 1) do NOT compound this into a single endpoint-ratio bound — sqrt(n)
# scaling is mathematically wrong for a point-to-point cumulative simple
# return (it's a volatility-scaling argument), but compounding the bound
# itself (`(1+bound)**n - 1`) is vacuous, not just generous: ±12,700% at
# n=7, ±107,374,182,300% at n=30 — wide enough to let the sJUP-shaped bad
# tick straight through once it ages into the 7d/30d lookback (#1322
# review, PR #1343 finding 5). Instead, multi-day windows are scanned
# bar-to-bar against this same per-day bound, admitting a real 10x month
# (many individually-plausible ~8%/day steps) while still rejecting a
# single implausible bar anywhere in the window. See `_pct_change_with_reason`
# for the bar-scan.
_MAX_PLAUSIBLE_DAILY_PCT = 100.0

# Trading-day conventions differ by asset class (#1322). Equities/ETFs/FX
# only print a yfinance daily bar on trading days (weekends + holidays are
# gaps), so ~5 bars ≈ 1 calendar week and ~21 bars ≈ 1 calendar month, and
# annualizing realized vol uses the standard 252 trading days/year. Crypto
# trades every calendar day with no gaps, so 1 bar == 1 calendar day: 7
# bars == 1 week, 30 bars == 1 month, and annualizing must use 365.
_EQUITY_TRADING_DAYS_PER_YEAR = 252
_CRYPTO_TRADING_DAYS_PER_YEAR = 365

# Range param → (yfinance period, yfinance interval). Daily intervals work
# for week / month / year ranges; 1D uses 5-minute intraday data; the longer
# ranges (5Y / 10Y / MAX) downsample to weekly / monthly bars to keep the
# point count and the chart legible. "max" pulls the asset's full history.
_HISTORY_RANGE_MAP: dict[str, tuple[str, str]] = {
    "1D": ("2d", "5m"),
    "1W": ("1mo", "1d"),
    "1M": ("3mo", "1d"),
    "1Y": ("1y", "1d"),
    "5Y": ("5y", "1wk"),
    "10Y": ("10y", "1wk"),
    "MAX": ("max", "1mo"),
}


# ── Plain-English explanations ────────────────────────────────────────────


_EXPLANATIONS_TEMPLATES = {
    "current_price": "Latest price the on-chain oracle quoted. Settlement on Arc uses this.",
    "change_24h_pct": (
        "Percentage move in the last trading day. Positive = up. "
        "Daily moves bigger than {vol_daily_pct:.1f}% are unusual for this asset."
    ),
    "change_7d_pct": "Percentage move over the past week ({week_label}).",
    "change_30d_pct": "Percentage move over the past month ({month_label}).",
    "realized_vol_30d": (
        "How much the price wobbles. Higher = bigger swings. {vol:.2f} annualized "
        "means daily moves of ~{vol_daily_pct:.1f}% are typical."
    ),
}

# change_24h_pct copy for when realized_vol_30d was suppressed (#1322
# review finding 3) — the vol-guard makes this systematically more common
# (one bad bar suppresses vol for the whole 30-session window it sits in),
# and the vol-based "unusual" clause can't be honestly asserted from a
# number the computation explicitly refused to produce.
_CHANGE_24H_PCT_NO_VOL_TEMPLATE = "Percentage move in the last trading day. Positive = up."


def _explanations_for(item: dict[str, Any], is_247: bool = False) -> dict[str, str]:
    """Plain-English copy for each metric. ``is_247`` (#1322) selects the
    asset-class-correct trading-day convention so the copy never asserts a
    convention the computation didn't actually use — crypto is 7/30 calendar
    days annualized with sqrt(365); everything else is 5/21 trading days
    annualized with sqrt(252)."""
    vol = item.get("realized_vol_30d")
    trading_days_per_year = _CRYPTO_TRADING_DAYS_PER_YEAR if is_247 else _EQUITY_TRADING_DAYS_PER_YEAR
    vol_daily_pct = (vol / math.sqrt(trading_days_per_year)) * 100.0 if vol else 0.0
    week_label = "7 calendar days" if is_247 else "5 trading days"
    month_label = "30 calendar days" if is_247 else "≈21 trading days"
    fields = {}
    for key, template in _EXPLANATIONS_TEMPLATES.items():
        if item.get(key) is None:
            continue
        if key == "change_24h_pct" and vol is None:
            # realized_vol_30d was suppressed as implausible/insufficient —
            # don't default it to a fabricated 0.0% just to fill the
            # template; drop the vol clause instead.
            fields[key] = _CHANGE_24H_PCT_NO_VOL_TEMPLATE
            continue
        try:
            fields[key] = template.format(
                vol=vol,
                vol_daily_pct=vol_daily_pct,
                week_label=week_label,
                month_label=month_label,
            )
        except (KeyError, IndexError):
            fields[key] = template
    return fields


# ── Stat math ─────────────────────────────────────────────────────────────


def _pct_change_with_reason(prices: list[float], n: int) -> tuple[float | None, bool]:
    """Pct change between prices[-1] and prices[-1-n].

    Returns ``(value, was_rejected)``. ``value`` is None if not enough data,
    the prior close is zero, or the result exceeds a plausible bound for a
    window this size (#1322): a bad tick / decimal-placement error in the
    upstream feed can turn a tiny prior close into an explosive ratio that
    passes every other check. An honest absence beats shipping an
    arithmetically-impossible number — see docs/architectural-principles.md
    § fail-soft. ``was_rejected`` is True only for the plausibility-bound
    case (not for insufficient data / zero start), so callers can
    distinguish "not enough history yet" from "a value was actively
    suppressed as implausible" — see ``AssetExploreItem.rejected_fields``.

    For n > 1, compounding the single-day bound onto the *endpoint* ratio
    (e.g. `(1 + bound)**n - 1`) turns out to be vacuous rather than merely
    generous: at n=7 it allows ±12,700%, at n=30 it allows
    ±107,374,182,300% — wide enough to let the issue's own bad tick
    (sJUP's +1479.20%, aged into the 7d/30d lookback) straight through
    (#1322 review, PR #1343 finding 5). An endpoint-only check can never
    tell "one bad tick" apart from "many small legitimate daily moves" —
    both can produce the same huge endpoint ratio — so for n > 1 this scans
    the window bar-to-bar instead, the same shape
    `_realized_vol_annual_with_reason` uses: reject only if *some single
    bar* in the window implies a move past the per-day bound. A real
    multi-day move made of individually-plausible daily steps (e.g. a
    genuine 10x over a month, ~8.1%/day compounded) is kept no matter how
    large the compounded total is; a single implausible bar anywhere in the
    window — including one that's since aged out of the 1-day window but
    still sits inside the 7d/30d one — is not. n=1 keeps the simple
    endpoint bound (identical to a 1-bar scan).
    """
    if not prices or len(prices) < n + 1:
        return None, False
    end, start = prices[-1], prices[-1 - n]
    if not start:
        return None, False
    pct = (end - start) / start * 100.0
    if n <= 1:
        if abs(pct) > _MAX_PLAUSIBLE_DAILY_PCT:
            logger.warning(
                "explore: rejecting implausible %.1f%% change over %d bar(s) (bound ±%.1f%%) — "
                "treating as a bad tick, not a real move",
                pct,
                n,
                _MAX_PLAUSIBLE_DAILY_PCT,
            )
            return None, True
        return pct, False
    window = prices[-(n + 1) :]
    for i in range(1, len(window)):
        prev, curr = window[i - 1], window[i]
        if prev <= 0 or curr <= 0:
            # A non-positive price inside the window is itself a data hole
            # (#1322 review finding 4's fix, applied consistently here too)
            # — not a computable return, and not something a legitimate
            # multi-day move can produce.
            logger.warning(
                "explore: rejecting implausible %.1f%% change over %d bar(s) — bar %d/%d has a "
                "non-positive price (prev=%.6g, curr=%.6g) — treating as a bad tick, not a real move",
                pct,
                n,
                i,
                len(window) - 1,
                prev,
                curr,
            )
            return None, True
        bar_pct = (curr - prev) / prev * 100.0
        if abs(bar_pct) > _MAX_PLAUSIBLE_DAILY_PCT:
            logger.warning(
                "explore: rejecting implausible %.1f%% change over %d bar(s) — bar %d/%d implied a "
                "%.1f%% single-bar move (bound ±%.1f%%) — treating as a bad tick, not a real move",
                pct,
                n,
                i,
                len(window) - 1,
                bar_pct,
                _MAX_PLAUSIBLE_DAILY_PCT,
            )
            return None, True
    return pct, False


def _pct_change(prices: list[float], n: int) -> float | None:
    """Pct change between prices[-1] and prices[-1-n]; see
    ``_pct_change_with_reason`` for the full contract. Thin wrapper kept for
    callers (and the existing unit-test surface) that only need the value."""
    return _pct_change_with_reason(prices, n)[0]


def _realized_vol_annual_with_reason(
    prices: list[float],
    window: int = 30,
    trading_days_per_year: int = _EQUITY_TRADING_DAYS_PER_YEAR,
) -> tuple[float | None, bool]:
    """Annualized realized vol over the most recent ``window`` bars.

    Returns ``(value, was_rejected)`` — see ``_pct_change_with_reason`` for
    why callers need both.

    ``trading_days_per_year`` is the annualization factor (#1322): 252 for
    equities/ETFs/FX (yfinance bars only on trading days), 365 for crypto
    (yfinance bars every calendar day, no weekend/holiday gaps) — applying
    the equity constant to a 24/7 asset understates its vol by
    sqrt(365/252) ≈ 1.20, about 17%.

    Plausibility guard (#1322): a bad tick / decimal-placement error inside
    the window inflates one bar-to-bar return past what's physically
    plausible even when the *endpoint-to-endpoint* ratio `_pct_change`
    checks would pass — vol is built from every bar-to-bar step, not just
    the two ends, so a single corrupt bar corrupts the whole estimate.
    Dropping the bad bar and computing vol on what's left would still ship
    a clean-looking but fabricated number (the issue's anti-goal forbids
    exactly that shape of "fix"); the honest response is None for the
    whole window when any single bar exceeds the same per-day plausibility
    bound `_pct_change` uses (bar-to-bar, so no compounding — n=1 always).

    A non-positive bar inside the window (#1322 review finding 4) is
    rejected the same way, not silently skipped: skipping only the step
    *out of* a zero close (the old ``if not prev: continue``) still let the
    step *into* it compute an exact -100% return that slipped past a
    strict ``>`` bound comparison (-100% is not > 100%) and got folded into
    the vol estimate as real data. A zero/negative close is a data hole,
    not a return — same treatment `_pct_change_with_reason` gives a zero
    start.
    """
    if not prices or len(prices) < window + 1:
        return None, False
    tail = prices[-(window + 1) :]
    rets = []
    for i in range(1, len(tail)):
        prev, curr = tail[i - 1], tail[i]
        if prev <= 0 or curr <= 0:
            logger.warning(
                "explore: rejecting realized-vol window — bar %d/%d has a non-positive "
                "price (prev=%.6g, curr=%.6g) — treating as a bad tick, not real data",
                i,
                len(tail) - 1,
                prev,
                curr,
            )
            return None, True
        ret = (curr - prev) / prev
        if abs(ret) * 100.0 > _MAX_PLAUSIBLE_DAILY_PCT:
            logger.warning(
                "explore: rejecting realized-vol window — bar %d/%d implied a %.1f%% "
                "single-bar move (bound ±%.1f%%) — treating as a bad tick, not a real move",
                i,
                len(tail) - 1,
                ret * 100.0,
                _MAX_PLAUSIBLE_DAILY_PCT,
            )
            return None, True
        rets.append(ret)
    if len(rets) < 2:
        return None, False
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    return math.sqrt(var) * math.sqrt(trading_days_per_year), False


def _realized_vol_annual(
    prices: list[float],
    window: int = 30,
    trading_days_per_year: int = _EQUITY_TRADING_DAYS_PER_YEAR,
) -> float | None:
    """Annualized realized vol over the most recent ``window`` bars; see
    ``_realized_vol_annual_with_reason`` for the full contract. Thin wrapper
    kept for callers (and the existing unit-test surface) that only need the
    value."""
    return _realized_vol_annual_with_reason(prices, window, trading_days_per_year)[0]


# ── Direct yfinance fetch (used by /assets/{symbol}/history) ─────────────


def _fetch_yfinance_series(symbol: str, period: str, interval: str) -> list[ExploreHistoryPoint]:
    """Fetch a single-asset time series at the requested period+interval, via
    the market-data provider seam (#1218 — default provider yfinance,
    unchanged behavior; vendor-swappable via ``MARKET_DATA_PROVIDER``).

    Returns an empty list when the symbol is unknown, the provider is
    unavailable, or the upstream feed returned no data. The caller (route
    handler) turns an empty list into a 404 so the frontend can render an
    honest empty state instead of a faked flat line. Uncached (a per-request,
    single-symbol drill-down — not the #1218 volume driver; see
    ``market_data_provider``'s module docstring for the caching scope).
    """
    try:
        from archimedes.services.strategy_signal_evaluator import GLOBAL_ASSETS

        entry = GLOBAL_ASSETS.get(symbol)
        if not entry:
            return []
        yf_ticker = entry[0]
    except Exception as exc:
        logger.warning("explore: history symbol resolve for %s failed: %s", symbol, exc)
        return []

    try:
        from archimedes.services.market_data_provider import get_provider

        close = get_provider().get_series(yf_ticker, period, interval)
    except Exception as exc:
        logger.warning("explore: market-data history fetch failed for %s (%s/%s): %s", symbol, period, interval, exc)
        return []

    if close is None or close.empty:
        return []

    points: list[ExploreHistoryPoint] = []
    for ts, price in close.items():
        try:
            # pandas Timestamps render as ISO when stringified.
            points.append(ExploreHistoryPoint(ts=str(ts), price=float(price)))
        except Exception:
            continue
    return points


# ── Explore universe (SSOT) ───────────────────────────────────────────────


def _explore_universe() -> list[str]:
    """The asset universe the Explore page lists.

    Source: ``archimedes.universe.ON_CHAIN_SYNTHS`` — the deploy-eligible SSOT
    universe (backend/archimedes/data/synthetic_universe.json, regenerated by
    PR #842's Chainlink-only pass). This is exactly the set the Generate
    picker is generated from (``scripts/gen_ui_asset_universe.py`` renders
    ``ON_CHAIN_SYNTHS`` into ``ui/src/data/assetUniverse.js``), so Explore and
    Generate show the same universe. It is deliberately NOT ``GLOBAL_ASSETS``
    (which additionally carries ~59 compliance-flagged backtest-only single
    stocks) and NOT the legacy ~74-name ``DEFAULT_SCAN_UNIVERSE`` scan subset.
    Parity is CI-enforced in ``backend/tests/test_universe_parity.py``.
    """
    try:
        from archimedes.universe import ON_CHAIN_SYNTHS

        return list(ON_CHAIN_SYNTHS)
    except Exception as exc:  # pragma: no cover — SSOT loader logs its own errors
        logger.warning("explore: universe SSOT import failed: %s", exc)
        return []


def _is_24_7_asset_class(asset_class: str) -> bool:
    """True for asset classes that trade every calendar day, no gaps (#1322).

    Crypto is the only such class in the current universe: a yfinance daily
    bar for e.g. BTC-USD prints every calendar day (weekends included), so
    1 bar == 1 calendar day. Equities/ETFs/FX/bonds/commodities only trade
    (and only get a bar) on trading days — the same string-membership test
    the existing group-sort already uses (see ``items.sort`` below).
    """
    return "crypto" in (asset_class or "")


def _ssot_display_name(synth: str, fallback: str) -> str:
    """SSOT display name for a synth card, falling back to the ticker."""
    try:
        from archimedes.universe import SYNTHETIC_UNIVERSE

        spec = SYNTHETIC_UNIVERSE.get(synth)
        if spec is not None and spec.name:
            return spec.name
    except Exception as exc:  # pragma: no cover — defensive; loader logs errors
        logger.debug("explore: SSOT name lookup failed for %s: %s", synth, exc)
    return fallback


# ── Service ───────────────────────────────────────────────────────────────


class AssetMarketService:
    """Composes per-synth market stats from on-chain oracle + histories. 30s TTL cache."""

    def __init__(self) -> None:
        self._cache: ExploreAssetsResponse | None = None
        self._cache_ts: float = 0.0
        # Keyed by (symbol, range) — different ranges have different lookbacks.
        self._cache_history: dict[tuple[str, str], tuple[float, ExploreHistoryResponse]] = {}
        self._history_cache_ttl = 60.0  # seconds
        # Guards the background refresh (see list_assets): at most one
        # in-flight refresh at a time, so N requests landing in the same
        # stale window don't each spawn their own oracle+yfinance rebuild.
        self._refresh_task: asyncio.Task | None = None

    # ── On-chain oracle reads ────────────────────────────────────────────

    async def _read_oracle_prices(
        self,
        synth_symbols: list[str],
    ) -> dict[str, dict[str, Any]]:
        """Read current prices from on-chain PriceOracle for each synth.

        Returns ``{symbol: {price: float, updated_at: int, stale: bool}}``.
        Symbols missing from oracle config or failing chain reads are omitted.
        Reads run sequentially under ``_ORACLE_TOTAL_BUDGET_SECONDS``; symbols
        past the deadline are skipped (yfinance covers their card instead).
        """
        try:
            import json
            from pathlib import Path

            from archimedes.chain.client import chain_client

            oracle_addrs = chain_client.settings.oracle_addresses or {}
            synth_addrs = chain_client.settings.synth_addresses or {}

            # Resolve ABI path — try multiple locations (repo root, relative)
            abi_candidates = [
                Path(chain_client.settings.abi_dir) / "IPriceOracle.json",
                Path(__file__).resolve().parents[3] / "contracts" / "abis" / "IPriceOracle.json",
            ]
            oracle_abi = []
            for p in abi_candidates:
                if p.exists():
                    oracle_abi = json.loads(p.read_text())
                    break
        except Exception as exc:
            logger.warning("explore: oracle setup failed: %s", exc)
            return {}

        if not oracle_abi:
            logger.warning("explore: IPriceOracle ABI not found")
            return {}

        results: dict[str, dict[str, Any]] = {}
        now_ts = time.time()
        deadline = time.monotonic() + _ORACLE_TOTAL_BUDGET_SECONDS

        for i, symbol in enumerate(synth_symbols):
            oracle_addr = oracle_addrs.get(symbol)
            synth_addr = synth_addrs.get(symbol)
            # getPrice takes the synth token address, called on the oracle contract
            if not oracle_addr or not synth_addr:
                continue
            if time.monotonic() > deadline:
                logger.warning(
                    "explore: oracle read budget exhausted after %d/%d symbols (%d priced)",
                    i,
                    len(synth_symbols),
                    len(results),
                )
                break
            try:
                contract = chain_client.w3.eth.contract(
                    address=chain_client.to_checksum(oracle_addr),
                    abi=oracle_abi,
                )
                # Bound each read by BOTH the per-read cap and the remaining
                # total budget — otherwise a read started near the deadline can
                # overshoot the endpoint budget by up to _ORACLE_READ_TIMEOUT.
                remaining = deadline - time.monotonic()
                price_raw, updated_at = await asyncio.wait_for(
                    contract.functions.getPrice(chain_client.to_checksum(synth_addr)).call(),
                    timeout=max(0.1, min(_ORACLE_READ_TIMEOUT, remaining)),
                )
                price_usd = float(price_raw) / 1e6  # 6 decimals per PriceOracle.sol
                # A future updated_at (block time ahead of the host clock) is
                # anomalous: normal block timestamps are ≤ wall clock, and a
                # timestamp in the future would leave the age permanently
                # negative — masking genuine staleness forever. Treat any
                # future timestamp as stale rather than "fresh" (#934).
                age = now_ts - updated_at
                stale = age < 0 or age > _STALE_WINDOW_SECONDS
                results[symbol] = {
                    "price": price_usd,
                    "updated_at": updated_at,
                    "stale": stale,
                    "oracle_address": oracle_addr,
                }
            except TimeoutError:
                logger.debug("explore: oracle read timeout for %s", symbol)
            except Exception as exc:
                logger.debug("explore: oracle read failed for %s: %s", symbol, exc)

        return results

    # ── yfinance histories (chunked, budgeted) ──────────────────────────

    async def _fetch_histories_budgeted(self, symbols: list[str]) -> dict[str, Any]:
        """Fetch yfinance close histories for ``symbols`` in chunks under a total budget.

        Delegates each chunk to the evaluator's ``_fetch_price_histories``, which
        serves from / populates its module-level ``_PRICE_CACHE`` (600s TTL) — so
        warm requests are near-free and a cold ~280-symbol universe fills up over
        a couple of requests instead of timing the endpoint out. Whatever fetched
        before the budget ran out is returned as an honest partial result.
        """
        try:
            from archimedes.services.strategy_signal_evaluator import _fetch_price_histories
        except Exception as exc:
            logger.warning("explore: evaluator import failed: %s", exc)
            return {}

        histories: dict[str, Any] = {}
        deadline = time.monotonic() + _HISTORY_FETCH_BUDGET_SECONDS
        for start in range(0, len(symbols), _HISTORY_CHUNK_SIZE):
            chunk = symbols[start : start + _HISTORY_CHUNK_SIZE]
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                logger.warning(
                    "explore: history budget exhausted — %d/%d symbols fetched; serving partial result",
                    len(histories),
                    len(symbols),
                )
                break
            try:
                chunk_histories = await asyncio.wait_for(
                    asyncio.to_thread(_fetch_price_histories, chunk, _HISTORY_LOOKBACK),
                    timeout=remaining,
                )
                histories.update(chunk_histories)
            except TimeoutError:
                # The worker thread keeps running and populates _PRICE_CACHE when
                # it completes; the next request picks those symbols up from cache.
                logger.warning(
                    "explore: history chunk timed out — %d/%d symbols fetched; serving partial result",
                    len(histories),
                    len(symbols),
                )
                break
            except Exception as exc:
                logger.warning("explore: history chunk fetch failed (%d symbols): %s", len(chunk), exc)
                # Keep going: later chunks may still succeed. (The wall-clock
                # budget is an absolute deadline, so time burned in a failing
                # chunk IS spent — this only skips the failed chunk's data.)
        return histories

    # ── Main list ─────────────────────────────────────────────────────────

    async def list_assets(self) -> ExploreAssetsResponse:
        """Serve the asset list, stale-while-revalidate (#explore-latency).

        The old shape awaited the full oracle+yfinance rebuild inline on
        every cache miss: whichever request landed just after the 30s TTL
        expired paid that cost synchronously. Measured in prod (2026-08-02):
        12.7s-42.9s per rebuild. The oracle read alone accounts for up to
        ``_ORACLE_TOTAL_BUDGET_SECONDS`` of that, and it is spent whether or
        not any price comes back: ``_read_oracle_prices`` runs its reads
        sequentially under that budget and simply OMITS symbols that fail or
        run past the deadline, so a fully unresponsive oracle costs the entire
        budget and returns nothing.
        That is the whole "Explore takes a long time to load" symptom for
        an unlucky visitor.

        Fix: once a cache exists at all, NEVER block a request behind a
        rebuild. A stale cache is served immediately and a rebuild is
        kicked off in the background (deduplicated via ``_refresh_task`` so
        concurrent requests in the same stale window don't each start their
        own rebuild); the NEXT request picks up the fresh result. Only the
        very first request after cold start (no cache yet at all) pays the
        synchronous cost — unavoidable, and no worse than today.
        """
        now = time.time()
        if self._cache is not None:
            if (now - self._cache_ts) < _CACHE_TTL_SECONDS:
                return self._cache
            self._kick_background_refresh()
            return self._cache

        # Cold start — no cache yet. This request pays the one-time cost;
        # every request after it hits the branches above.
        return await self._refresh()

    def _kick_background_refresh(self) -> None:
        """Start a background rebuild if one isn't already in flight."""
        if self._refresh_task is not None and not self._refresh_task.done():
            return
        self._refresh_task = asyncio.create_task(self._refresh(), name="explore-assets-refresh")

        def _log_if_failed(task: asyncio.Task) -> None:
            if task.cancelled():
                return
            exc = task.exception()
            if exc is not None:
                # exc_info so the traceback survives. This path swallows a
                # failure by design -- a stale cache is still served -- which
                # makes the log the ONLY evidence the refresh is broken. A
                # bare "%s" would reduce an rpc/yfinance/oracle stack trace to
                # one uninformative line and leave the failure looking like a
                # transient blip rather than a broken dependency.
                logger.warning("explore: background refresh failed: %s", exc, exc_info=exc)

        self._refresh_task.add_done_callback(_log_if_failed)

    async def _refresh(self) -> ExploreAssetsResponse:
        """Rebuild the asset list from oracle + yfinance and populate the cache.

        This is the full-cost path (was the entire body of ``list_assets``
        before the stale-while-revalidate wrapper above). Callers should go
        through ``list_assets()``; this is called directly only for the
        cold-start (no cache yet) case and from the background refresh task.
        """
        now = time.time()

        # The deploy-eligible SSOT universe — same set as the Generate picker.
        # See _explore_universe() for the source rationale.
        universe = _explore_universe()

        try:
            from archimedes.services.strategy_signal_evaluator import GLOBAL_ASSETS
        except Exception as exc:
            logger.warning("explore: import failed: %s", exc)
            GLOBAL_ASSETS = {}

        # 1. Read on-chain oracle prices (primary source)
        oracle_data = await self._read_oracle_prices(universe)

        # 2. Fetch yfinance histories for change windows / vol (fallback),
        #    chunked under a total budget — partial coverage beats a timeout.
        histories = await self._fetch_histories_budgeted(universe)

        # Build items: merge oracle price + yfinance change/vol
        items: list[AssetExploreItem] = []
        nowstamp = datetime.now(UTC).isoformat()

        # Every universe symbol gets a card — symbols with no data yet are
        # served honestly as price_source="none" instead of being dropped, so
        # the page always shows the full deploy-eligible universe.
        for synth in universe:
            oracle = oracle_data.get(synth, {})
            # _fetch_price_histories returns {symbol: pd.Series} (close prices)
            # Convert Series to a plain list for downstream math.
            raw_hist = histories.get(synth)
            if raw_hist is not None and hasattr(raw_hist, "tolist"):
                # An object-dtype series (a feed gap, a mixed column) can hold
                # None or other non-numbers. math.isnan() raises TypeError on a
                # non-float, which previously aborted the entire /explore/assets
                # response over one bad element — guard the type and drop those
                # instead of dropping the whole universe (#928).
                hist_prices = [float(v) for v in raw_hist.tolist() if isinstance(v, (int, float)) and not math.isnan(v)]
            elif isinstance(raw_hist, dict):
                hist_prices = raw_hist.get("close") or []
            else:
                hist_prices = []

            # Pick the displayed price source. Oracle wins iff its last push
            # is within the oracle freshness window. Otherwise fall back to
            # the most recent yfinance daily close. Track which one we used
            # so the UI can label it ("Source: on-chain oracle" vs. yfinance).
            oracle_price = oracle.get("price")
            oracle_updated_at = oracle.get("updated_at")
            oracle_fresh = (
                oracle_price is not None
                and oracle_updated_at
                and oracle_updated_at > 0
                and (now - oracle_updated_at) <= _STALE_WINDOW_SECONDS
            )

            current_price: float | None
            price_source: Literal["oracle", "yfinance", "none"]
            last_updated: str | None
            displayed_is_stale: bool

            if oracle_fresh:
                current_price = oracle_price
                price_source = "oracle"
                last_updated = datetime.fromtimestamp(oracle_updated_at, tz=UTC).isoformat()
                displayed_is_stale = False
            elif hist_prices:
                current_price = hist_prices[-1]
                price_source = "yfinance"
                if raw_hist is not None and hasattr(raw_hist, "index") and len(raw_hist) > 0:
                    last_bar = raw_hist.index[-1]
                    last_updated = str(last_bar)
                    try:
                        # pandas Timestamp supports .timestamp(); fall back to
                        # parse if last_bar is already a str.
                        bar_ts = float(last_bar.timestamp()) if hasattr(last_bar, "timestamp") else 0.0
                    except Exception:
                        bar_ts = 0.0
                    # yfinance daily close: stale if last bar is more than a few
                    # trading days old (weekends + bank holidays count as
                    # legitimate gaps, but more than ~4 days means the feed is
                    # broken for this name).
                    displayed_is_stale = bool(bar_ts > 0 and (now - bar_ts) > _YF_STALE_WINDOW_SECONDS)
                else:
                    last_updated = nowstamp
                    displayed_is_stale = False
            else:
                # No source at all — honestly stale + null price.
                current_price = None
                price_source = "none"
                last_updated = None
                displayed_is_stale = True

            # 24h high / low — only computable for intraday data, which we
            # don't fetch in the listing endpoint. The detail-modal endpoint
            # (get_history with range="1D") returns intraday bars and the UI
            # can compute these client-side from that series. Leave them
            # ``None`` here so the card / modal show "—" honestly.
            high_24h = None
            low_24h = None

            entry = GLOBAL_ASSETS.get(synth)
            asset_class = entry[2] if entry else "unknown"
            real_ticker = entry[0] if entry else synth.lstrip("s")
            # Trading-day convention is asset-class aware (#1322): crypto
            # bars are 1-per-calendar-day (24/7 markets), everything else is
            # 1-per-trading-day (weekends/holidays are gaps in the feed).
            is_247 = _is_24_7_asset_class(asset_class)
            week_n = 7 if is_247 else 5
            month_n = 30 if is_247 else 21
            trading_days_per_year = _CRYPTO_TRADING_DAYS_PER_YEAR if is_247 else _EQUITY_TRADING_DAYS_PER_YEAR

            # Change / vol from yfinance daily history (independent of where
            # the spot came from — both source paths benefit from these).
            # Each `*_with_reason` call also reports whether the None came
            # from an active plausibility rejection (#1322) vs. simply not
            # having enough history yet; rejected_fields discloses the
            # former on the served item rather than leaving a suppressed
            # bad tick indistinguishable from "not fetched yet" (issue
            # Precedent: route rejected values down an honest-absence path).
            rejected_fields: list[str] = []
            change_24h_pct, rej_24h = _pct_change_with_reason(hist_prices, 1) if hist_prices else (None, False)
            change_7d_pct, rej_7d = _pct_change_with_reason(hist_prices, week_n) if hist_prices else (None, False)
            change_30d_pct, rej_30d = _pct_change_with_reason(hist_prices, month_n) if hist_prices else (None, False)
            realized_vol_30d, rej_vol = (
                _realized_vol_annual_with_reason(hist_prices, 30, trading_days_per_year)
                if hist_prices
                else (None, False)
            )
            if rej_24h:
                rejected_fields.append("change_24h_pct")
            if rej_7d:
                rejected_fields.append("change_7d_pct")
            if rej_30d:
                rejected_fields.append("change_30d_pct")
            if rej_vol:
                rejected_fields.append("realized_vol_30d")

            stat_dict: dict[str, Any] = {
                "current_price": current_price,
                "change_24h_pct": change_24h_pct,
                "change_7d_pct": change_7d_pct,
                "change_30d_pct": change_30d_pct,
                "high_24h": high_24h,
                "low_24h": low_24h,
                "realized_vol_30d": realized_vol_30d,
            }

            # oracle_address is a capability marker — populated from settings
            # regardless of whether the oracle read succeeded (Issue #346).
            try:
                from archimedes.chain.client import chain_client as _cc

                _oracle_addr = oracle.get("oracle_address") or (_cc.settings.oracle_addresses or {}).get(synth)
            except Exception:
                _oracle_addr = oracle.get("oracle_address")

            items.append(
                AssetExploreItem(
                    symbol=synth,
                    # SSOT display name (synthetic_universe.json), falling back
                    # to the ticker for anything the SSOT doesn't carry.
                    name=_ssot_display_name(synth, real_ticker),
                    asset_class=asset_class,
                    oracle_address=_oracle_addr,
                    last_updated=last_updated,
                    is_stale=displayed_is_stale,
                    price_source=price_source,
                    explanations=_explanations_for(stat_dict, is_247=is_247),
                    rejected_fields=rejected_fields,
                    **stat_dict,
                )
            )

        # Stable ordering — equities first, then crypto, then everything else.
        items.sort(
            key=lambda a: (
                0 if "equity" in a.asset_class else 1 if "crypto" in a.asset_class else 2,
                a.symbol,
            )
        )

        self._cache = ExploreAssetsResponse(
            assets=items,
            cache_ttl_seconds=_CACHE_TTL_SECONDS,
            generated_at=nowstamp,
            universe_size=len(universe),
            priced_count=sum(1 for a in items if a.current_price is not None),
        )
        self._cache_ts = now
        return self._cache

    async def get_history(self, symbol: str, range_: str = "1M") -> ExploreHistoryResponse:
        """Return time-series points for ``symbol`` over ``range_``.

        Ranges: 1D (intraday 5m bars), 1W / 1M / 1Y (daily close).
        """
        if range_ not in _HISTORY_RANGE_MAP:
            range_ = "1M"

        cache_key = (symbol, range_)
        now = time.time()
        cached = self._cache_history.get(cache_key)
        if cached is not None and (now - cached[0]) < self._history_cache_ttl:
            return cached[1]

        period, interval = _HISTORY_RANGE_MAP[range_]
        points = await asyncio.to_thread(_fetch_yfinance_series, symbol, period, interval)

        resp = ExploreHistoryResponse(
            symbol=symbol,
            range=range_,  # type: ignore[arg-type]
            interval=interval,  # type: ignore[arg-type]
            points=points,
        )
        self._cache_history[cache_key] = (now, resp)
        return resp


asset_market_service = AssetMarketService()
