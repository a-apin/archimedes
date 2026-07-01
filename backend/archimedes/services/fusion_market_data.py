"""Real market data for fusion/DSL backtests — the #788/#818 deployability unlock.

``evaluate_fusion_spec`` historically ran on synthetic random-walk prices unless
a caller passed ``data_feed`` — and no caller ever did, so every fusion/debate
candidate carried ``data_source="synthetic"``, was inadmissible by design, and
the live rigor gate honestly read "pending" forever. This module is the missing
supplier:

- resolves a spec's ``asset_universe`` to yfinance tickers via the
  Chainlink-only universe SSOT (``GLOBAL_ASSETS``, PR #842),
- fetches real daily OHLCV through the analytics-engine's ``fetch_ohlcv``
  (the single owner of the fetch+normalize contract — same reuse rationale as
  ``portfolio_backtester``),
- strictly inner-joins the panel across assets (missing data is a lookahead /
  survivorship vector — same rule as ``portfolio_backtester._fetch_price_panel``),
- hands back per-asset **feed factories**: a backtrader feed object is consumed
  by a single ``cerebro.run()``, so the variant grid needs a *fresh* feed per
  run — sharing one concrete feed silently corrupts every run after the first.

Fail-closed: any resolution/fetch problem returns ``None`` and the caller falls
back to the synthetic path, which stays honestly inadmissible ("pending") —
never a fake pass.
"""

from __future__ import annotations

import logging
import os
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Full-history start mirrors the curated backtests (Faber et al. run 2004→now,
# ~5,600 daily bars) so fusion strategies are graded on the same evidence depth.
_DEFAULT_START = "2004-01-02"

# Rigor needs enough bars for the walk-forward OOS split + indicator warmup
# (e.g. sma_200 consumes 200 bars before the first signal). 252 = one trading
# year, well above live_rigor_gate's floor; mixed universes with young assets
# (crypto listed ~2017+) shrink the inner-join and can legitimately fail this.
_MIN_BARS = 252

# Cap the per-spec asset fan-out: each asset is one yfinance fetch + one
# backtest per variant combo. Deeper universes get truncated LOUDLY (logged +
# visible in the provenance label), never silently.
_MAX_ASSETS = 6

_CACHE_TTL_S = 6 * 3600.0
_panel_cache: dict[tuple, tuple[float, dict[str, Any]]] = {}
_cache_lock = threading.Lock()


def real_data_enabled() -> bool:
    """Whether fusion backtests should fetch real market data.

    Off under pytest (``TESTING`` — hermetic tests must never hit the network)
    and via the ``ARCHIMEDES_FUSION_REAL_DATA=0`` kill switch; on otherwise.
    """
    if os.getenv("TESTING"):
        return False
    return os.getenv("ARCHIMEDES_FUSION_REAL_DATA", "1").strip().lower() not in {"0", "false", "no", "off"}


def _ensure_analytics_import() -> None:
    """Place ``analytics-engine/src`` on sys.path so its ``data`` module imports.

    Mirrors ``portfolio_backtester._ensure_analytics_import`` — one function
    (``fetch_ohlcv``) owns the yfinance fetch+normalize contract repo-wide.
    """
    analytics_src = Path(__file__).resolve().parents[3] / "analytics-engine" / "src"
    if str(analytics_src) not in sys.path:
        sys.path.insert(0, str(analytics_src))


def _fetch_one(yf_ticker: str, start: str, end: str) -> Any:
    """Fetch one symbol's normalized OHLCV DataFrame. Test seam — monkeypatch me."""
    _ensure_analytics_import()
    from archimedes_analytics_engine.data import fetch_ohlcv

    return fetch_ohlcv(yf_ticker, start, end)


def resolve_universe(asset_universe: list[str]) -> dict[str, str]:
    """Map spec asset symbols → yfinance tickers via the universe SSOT.

    Accepts any of the shapes LLM output actually produces: the synth key
    (``sSPY``), the display name (``SPY``, ``AAVE``), or the yfinance ticker
    itself (``AAVE-USD``). Unmappable symbols are skipped with a warning —
    the universe SSOT is the boundary; we never backtest arbitrary tickers.
    """
    from archimedes.services.strategy_signal_evaluator import GLOBAL_ASSETS

    display_index = {display.upper(): yf for yf, display, _cls, _exch in GLOBAL_ASSETS.values()}
    yf_index = {yf.upper(): yf for yf, _d, _c, _e in GLOBAL_ASSETS.values()}

    resolved: dict[str, str] = {}
    for raw in asset_universe:
        sym = str(raw).strip()
        if not sym:
            continue
        entry = GLOBAL_ASSETS.get(sym) or GLOBAL_ASSETS.get(f"s{sym}")
        if entry is not None:
            resolved[sym] = entry[0]
            continue
        yf_ticker = display_index.get(sym.upper()) or yf_index.get(sym.upper())
        if yf_ticker is not None:
            resolved[sym] = yf_ticker
            continue
        logger.warning("fusion real-data: %r is outside the universe SSOT — skipping", sym)

    if len(resolved) > _MAX_ASSETS:
        dropped = list(resolved)[_MAX_ASSETS:]
        logger.warning(
            "fusion real-data: universe capped at %d assets — dropped %s",
            _MAX_ASSETS,
            dropped,
        )
        resolved = dict(list(resolved.items())[:_MAX_ASSETS])
    return resolved


@dataclass(frozen=True)
class RealPanel:
    """Inner-joined real OHLCV frames for a spec's (mappable) asset universe."""

    frames: dict[str, Any]  # spec symbol -> pd.DataFrame (aligned on a common date index)
    label: str  # provenance, e.g. "yfinance:SPY,AAVE-USD"
    start: date
    end: date
    n_bars: int


def fetch_real_panel(
    asset_universe: list[str],
    *,
    start: str = _DEFAULT_START,
    end: str | None = None,
    min_bars: int = _MIN_BARS,
) -> RealPanel | None:
    """Fetch + inner-join real daily OHLCV for the mappable subset of a universe.

    Returns ``None`` (caller falls back to synthetic → honest "pending") when
    nothing maps, a fetch fails, or the joined history is too short. Results
    are cached per ticker-set for ``_CACHE_TTL_S`` so a debate pool / variant
    sweep doesn't re-hit yfinance.
    """
    resolved = resolve_universe(list(asset_universe or []))
    if not resolved:
        logger.warning("fusion real-data: no asset in %s maps to the universe SSOT", asset_universe)
        return None

    end = end or date.today().isoformat()
    cache_key = (tuple(sorted(resolved.values())), start, end)
    fetched: dict[str, Any] | None = None
    with _cache_lock:
        hit = _panel_cache.get(cache_key)
        if hit is not None and (time.monotonic() - hit[0]) < _CACHE_TTL_S:
            fetched = hit[1]

    if fetched is None:
        fetched = {}
        for sym, yf_ticker in resolved.items():
            try:
                fetched[yf_ticker] = _fetch_one(yf_ticker, start, end)
            except Exception as exc:
                # One dead ticker shrinks the panel rather than killing it; if
                # everything dies we return None below.
                logger.warning("fusion real-data: fetch failed for %s (%s): %s", sym, yf_ticker, exc)
        if not fetched:
            return None
        with _cache_lock:
            _panel_cache[cache_key] = (time.monotonic(), fetched)

    frames = {sym: fetched[yf] for sym, yf in resolved.items() if yf in fetched}
    panel = _panel_from_frames(frames, resolved)
    if panel is None:
        return None
    if panel.n_bars < min_bars:
        logger.warning(
            "fusion real-data: only %d overlapping bars across %s (min %d) — refusing",
            panel.n_bars,
            list(frames),
            min_bars,
        )
        return None
    return panel


def _panel_from_frames(frames: dict[str, Any], resolved: dict[str, str]) -> RealPanel | None:
    """Strictly inner-join frames on their date index (anti-lookahead join)."""
    if not frames:
        return None
    common = None
    for df in frames.values():
        common = df.index if common is None else common.intersection(df.index)
    if common is None or len(common) == 0:
        return None
    aligned = {sym: df.loc[common] for sym, df in frames.items()}
    label = "yfinance:" + ",".join(resolved[sym] for sym in aligned)
    return RealPanel(
        frames=aligned,
        label=label,
        start=common.min().date(),
        end=common.max().date(),
        n_bars=len(common),
    )


def feed_factory(df: Any) -> Callable[[], Any]:
    """Return a zero-arg factory producing a FRESH backtrader feed per call.

    A ``bt.feeds.PandasData`` instance is stateful and consumed by a single
    ``cerebro.run()``; reusing one across the base run + every variant run
    silently yields empty/corrupt backtests. The factory closes over an
    immutable lowercase-column copy and builds a new feed each call.
    """
    prepared = df.rename(columns=str.lower)

    def _make() -> Any:
        import backtrader as bt

        return bt.feeds.PandasData(dataname=prepared, openinterest=None)

    return _make
