"""Composes on-chain oracle + history data into the /api/explore/assets response.

Primary price source: on-chain ``PriceOracle.getPrice(token)`` via ``chain_client``.
Fall back to yfinance histories for change windows / vol and as a price
fallback when the oracle is stale or unavailable.  30-second TTL cache
(per the Phase 3a spec — page must load <1s without synchronous on-chain
reads on cache hit). The plain-English explanations live in this module so
the route handler stays a thin facade.
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
from datetime import UTC, datetime
from typing import Any

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
_ORACLE_READ_TIMEOUT = 5  # seconds per individual chain read


# ── Plain-English explanations ────────────────────────────────────────────


_EXPLANATIONS_TEMPLATES = {
    "current_price": "Latest price the on-chain oracle quoted. Settlement on Arc uses this.",
    "change_24h_pct": (
        "Percentage move in the last trading day. Positive = up. "
        "Daily moves bigger than {vol_daily_pct:.1f}% are unusual for this asset."
    ),
    "change_7d_pct": "Percentage move over the past week (5 trading days).",
    "change_30d_pct": "Percentage move over the past month (≈21 trading days).",
    "realized_vol_30d": (
        "How much the price wobbles. Higher = bigger swings. {vol:.2f} annualized "
        "means daily moves of ~{vol_daily_pct:.1f}% are typical."
    ),
}


def _explanations_for(item: dict[str, Any]) -> dict[str, str]:
    vol = item.get("realized_vol_30d") or 0.0
    vol_daily_pct = (vol / math.sqrt(252)) * 100.0 if vol else 0.0
    fields = {}
    for key, template in _EXPLANATIONS_TEMPLATES.items():
        if item.get(key) is None:
            continue
        try:
            fields[key] = template.format(vol=vol, vol_daily_pct=vol_daily_pct)
        except (KeyError, IndexError):
            fields[key] = template
    return fields


# ── Stat math ─────────────────────────────────────────────────────────────


def _pct_change(prices: list[float], n: int) -> float | None:
    """Pct change between prices[-1] and prices[-1-n]. None if not enough data."""
    if not prices or len(prices) < n + 1:
        return None
    end, start = prices[-1], prices[-1 - n]
    if not start:
        return None
    return (end - start) / start * 100.0


def _realized_vol_annual(prices: list[float], window: int = 30) -> float | None:
    """Annualized realized vol over the most recent ``window`` trading days."""
    if not prices or len(prices) < window + 1:
        return None
    tail = prices[-(window + 1) :]
    rets = []
    for i in range(1, len(tail)):
        prev = tail[i - 1]
        if not prev:
            continue
        rets.append((tail[i] - prev) / prev)
    if len(rets) < 2:
        return None
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    return math.sqrt(var) * math.sqrt(252)


# ── Service ───────────────────────────────────────────────────────────────


class AssetMarketService:
    """Composes per-synth market stats from on-chain oracle + histories. 30s TTL cache."""

    def __init__(self) -> None:
        self._cache: ExploreAssetsResponse | None = None
        self._cache_ts: float = 0.0
        self._cache_history: dict[str, ExploreHistoryResponse] = {}

    # ── On-chain oracle reads ────────────────────────────────────────────

    async def _read_oracle_prices(
        self,
        synth_symbols: list[str],
    ) -> dict[str, dict[str, Any]]:
        """Read current prices from on-chain PriceOracle for each synth.

        Returns ``{symbol: {price: float, updated_at: int, stale: bool}}``.
        Symbols missing from oracle config or failing chain reads are omitted.
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

        for symbol in synth_symbols:
            oracle_addr = oracle_addrs.get(symbol)
            synth_addr = synth_addrs.get(symbol)
            # getPrice takes the synth token address, called on the oracle contract
            if not oracle_addr or not synth_addr:
                continue
            try:
                contract = chain_client.w3.eth.contract(
                    address=chain_client.to_checksum(oracle_addr),
                    abi=oracle_abi,
                )
                price_raw, updated_at = await asyncio.wait_for(
                    contract.functions.getPrice(chain_client.to_checksum(synth_addr)).call(),
                    timeout=_ORACLE_READ_TIMEOUT,
                )
                price_usd = float(price_raw) / 1e6  # 6 decimals per PriceOracle.sol
                stale = (now_ts - updated_at) > _STALE_WINDOW_SECONDS
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

    # ── Main list ─────────────────────────────────────────────────────────

    async def list_assets(self) -> ExploreAssetsResponse:
        now = time.time()
        if self._cache and (now - self._cache_ts) < _CACHE_TTL_SECONDS:
            return self._cache

        try:
            from archimedes.services.strategy_signal_evaluator import (
                DEFAULT_SCAN_UNIVERSE,
                GLOBAL_ASSETS,
                _fetch_price_histories,
            )
        except Exception as exc:
            logger.warning("explore: import failed: %s", exc)
            DEFAULT_SCAN_UNIVERSE, GLOBAL_ASSETS, _fetch_price_histories = [], {}, None

        # 1. Read on-chain oracle prices (primary source)
        oracle_data = await self._read_oracle_prices(DEFAULT_SCAN_UNIVERSE)

        # 2. Fetch yfinance histories for change windows / vol (fallback)
        histories: dict[str, Any] = {}
        try:
            if _fetch_price_histories is not None:
                histories = await asyncio.wait_for(
                    asyncio.to_thread(_fetch_price_histories, DEFAULT_SCAN_UNIVERSE, _HISTORY_LOOKBACK),
                    timeout=20.0,
                )
        except Exception as exc:
            logger.warning("explore: history fetch failed: %s", exc)

        # Build items: merge oracle price + yfinance change/vol
        items: list[AssetExploreItem] = []
        nowstamp = datetime.now(UTC).isoformat()

        # Use the union of oracle symbols and history symbols so nothing is lost
        all_symbols = list(dict.fromkeys(list(oracle_data.keys()) + list(histories.keys())))

        for synth in all_symbols:
            oracle = oracle_data.get(synth, {})
            hist = histories.get(synth, {}) if isinstance(histories.get(synth), dict) else {}
            hist_prices = hist.get("close") or []

            # Current price: oracle primary, yfinance fallback
            current_price: float | None = oracle.get("price")
            if current_price is None and hist_prices:
                current_price = hist_prices[-1]

            # Staleness + last_updated from oracle
            oracle_stale = oracle.get("stale", True)
            oracle_updated_at = oracle.get("updated_at")
            if oracle_updated_at:
                last_updated = datetime.fromtimestamp(oracle_updated_at, tz=UTC).isoformat()
            elif hist.get("last_ts"):
                last_updated = str(hist["last_ts"])
            else:
                last_updated = nowstamp

            # If no oracle data at all, mark stale
            is_stale = oracle_stale if oracle else True

            # Change/vol from yfinance history
            stat_dict: dict[str, Any] = {
                "current_price": current_price,
                "change_24h_pct": _pct_change(hist_prices, 1) if hist_prices else None,
                "change_7d_pct": _pct_change(hist_prices, 5) if hist_prices else None,
                "change_30d_pct": _pct_change(hist_prices, 21) if hist_prices else None,
                "realized_vol_30d": _realized_vol_annual(hist_prices, 30) if hist_prices else None,
            }

            entry = GLOBAL_ASSETS.get(synth)
            asset_class = entry[2] if entry else "unknown"
            real_ticker = entry[0] if entry else synth.lstrip("s")

            items.append(
                AssetExploreItem(
                    symbol=synth,
                    name=f"Synthetic {real_ticker}",
                    asset_class=asset_class,
                    oracle_address=oracle.get("oracle_address"),
                    last_updated=last_updated,
                    is_stale=is_stale,
                    explanations=_explanations_for(stat_dict),
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
        )
        self._cache_ts = now
        return self._cache

    async def get_history(self, symbol: str) -> ExploreHistoryResponse:
        if symbol in self._cache_history:
            return self._cache_history[symbol]
        histories: dict[str, Any] = {}
        try:
            from archimedes.services.strategy_signal_evaluator import (
                _fetch_price_histories,
            )

            histories = await asyncio.to_thread(_fetch_price_histories, [symbol], _HISTORY_LOOKBACK)
        except Exception as exc:
            logger.warning("explore: history for %s failed: %s", symbol, exc)
        hist = histories.get(symbol) or {}
        prices = hist.get("close") or []
        dates = hist.get("dates") or []
        points = [
            ExploreHistoryPoint(ts=str(dates[i]) if i < len(dates) else "", price=prices[i]) for i in range(len(prices))
        ]
        resp = ExploreHistoryResponse(symbol=symbol, points=points)
        self._cache_history[symbol] = resp
        return resp


asset_market_service = AssetMarketService()
