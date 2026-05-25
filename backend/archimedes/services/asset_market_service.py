"""Composes on-chain oracle + history data into the /api/explore/assets response.

Primary price source: on-chain ``PriceOracle.getPrice(token)`` via ``chain_client``.
Fall back to yfinance histories for change windows / vol and as a price
fallback when the oracle is stale or unavailable.  30-second TTL cache
(per the Phase 3a spec — page must load <1s without synchronous on-chain
reads on cache hit). The plain-English explanations live in this module so
the route handler stays a thin facade.

Staleness semantics (rebuilt 2026-05-25 — see Explore page rebuild):
The on-chain ``PriceOracle`` is only actively pushed for a small subset of
synths (those in ``OracleUpdater.YFINANCE_MAP`` / ``CRYPTO_MAP`` — currently
~9 symbols). The rest of the ``DEFAULT_SCAN_UNIVERSE`` (~70 names) has an
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


# ── Direct yfinance fetch (used by /assets/{symbol}/history) ─────────────


def _fetch_yfinance_series(symbol: str, period: str, interval: str) -> list[ExploreHistoryPoint]:
    """Fetch a single-asset time series at the requested period+interval.

    Returns an empty list when the symbol is unknown, yfinance is unavailable,
    or the upstream feed returned no data. The caller (route handler) turns
    an empty list into a 404 so the frontend can render an honest empty state
    instead of a faked flat line.
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
        import yfinance as yf

        data = yf.download(
            yf_ticker,
            period=period,
            interval=interval,
            progress=False,
            auto_adjust=True,
            threads=False,
        )
    except Exception as exc:
        logger.warning("explore: yfinance history fetch failed for %s (%s/%s): %s", symbol, period, interval, exc)
        return []

    if data is None or len(data) == 0:
        return []

    try:
        close = data["Close"]
        # When yfinance is called with a single ticker it sometimes still returns
        # a DataFrame (one column). Squeeze to a Series for uniform handling.
        if hasattr(close, "columns"):
            close = close.iloc[:, 0]
        close = close.dropna()
    except Exception as exc:
        logger.warning("explore: yfinance close extract failed for %s: %s", symbol, exc)
        return []

    points: list[ExploreHistoryPoint] = []
    for ts, price in close.items():
        try:
            # pandas Timestamps render as ISO when stringified.
            points.append(ExploreHistoryPoint(ts=str(ts), price=float(price)))
        except Exception:
            continue
    return points


# ── Service ───────────────────────────────────────────────────────────────


class AssetMarketService:
    """Composes per-synth market stats from on-chain oracle + histories. 30s TTL cache."""

    def __init__(self) -> None:
        self._cache: ExploreAssetsResponse | None = None
        self._cache_ts: float = 0.0
        # Keyed by (symbol, range) — different ranges have different lookbacks.
        self._cache_history: dict[tuple[str, str], tuple[float, ExploreHistoryResponse]] = {}
        self._history_cache_ttl = 60.0  # seconds

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
                    timeout=45.0,  # 84 symbols can take 10-30s via yfinance
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
            # _fetch_price_histories returns {symbol: pd.Series} (close prices)
            # Convert Series to a plain list for downstream math.
            raw_hist = histories.get(synth)
            if raw_hist is not None and hasattr(raw_hist, "tolist"):
                hist_prices = [float(v) for v in raw_hist.tolist() if v == v]  # v==v filters NaN
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

            # Change / vol from yfinance daily history (independent of where
            # the spot came from — both source paths benefit from these).
            stat_dict: dict[str, Any] = {
                "current_price": current_price,
                "change_24h_pct": _pct_change(hist_prices, 1) if hist_prices else None,
                "change_7d_pct": _pct_change(hist_prices, 5) if hist_prices else None,
                "change_30d_pct": _pct_change(hist_prices, 21) if hist_prices else None,
                "high_24h": high_24h,
                "low_24h": low_24h,
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
                    is_stale=displayed_is_stale,
                    price_source=price_source,
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
