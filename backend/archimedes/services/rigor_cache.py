"""Process-level TTL cache for the (expensive but always-honest) live rigor gate.

**Why this exists (perf):** ``GET /api/strategies/`` and ``GET /api/selection-bias/gate``
each recompute the full rigor gate LIVE on every request — cohort PBO, average
pairwise correlation, the look-ahead code audit, and one ``run_rigor_gate`` call per
strategy. Measured cost: ~6s and ~8-10s respectively, and the Library page's
``Promise.all`` blocks on the slower one. That is unacceptable page-load latency for a
computation whose inputs (persisted daily returns) change only when a new backtest is
written — i.e. rarely, compared to how often the page is loaded.

**Why this is safe (correctness):** this module caches the REAL computed result, not a
fake or precomputed one. The cache key (``cohort_key``) is a data-version token derived
from the exact returns data the computation would read — the moment any strategy's
persisted returns change, the key changes, and the next call recomputes live. Nothing
here weakens a threshold, skips a check, or serves a stale number for changed data. It
is pure memoization of an idempotent, deterministic function of its inputs.

**Fail-open, always.** Any exception raised by the cache layer itself (lookup or store)
is caught and the call falls back to ``compute_fn()`` — a live, correct result. A cache
bug can only ever cost a slow request, never a wrong or crashed one.

``cachetools`` is NOT currently a dependency (checked ``backend/requirements.txt`` and
``environment.yml`` on 2026-07-06) — rather than add one for a single call site, this
is a small hand-rolled dict-with-timestamps cache. If ``cachetools`` becomes a
dependency for an unrelated reason later, ``_Store`` below can be swapped for
``cachetools.TTLCache`` without changing the public API (``cohort_key`` /
``get_or_compute`` / ``clear``).
"""

from __future__ import annotations

import hashlib
import logging
import struct
import threading
import time
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)

# Safety cap only. The cohort_key is the real invalidation mechanism — it changes
# the instant underlying returns data changes, so in practice a cache entry is
# almost always invalidated by a key change long before this TTL would matter. The
# TTL exists purely as a backstop against any invalidation-hook gap (e.g. a writer
# path that doesn't call `clear()`) so a cached entry can never live forever.
_TTL_SECONDS = 600.0

_lock = threading.Lock()
_store: dict[str, tuple[float, Any]] = {}


def _fingerprint(series: list[float]) -> bytes:
    """Cheap, order-and-value-sensitive fingerprint of one strategy's return series.

    Packed IEEE-754 doubles change on ANY element changing (not just length/sum),
    so this is stronger than the ``(len, first, last, round(sum, 6))`` shorthand
    the docstring elsewhere describes as a minimum bar — this does that one better
    for about the same cost (struct.pack over a few hundred floats is microseconds,
    negligible next to the rigor-gate computation it's guarding).
    """
    if not series:
        return b"\x00empty"
    try:
        return struct.pack(f"{len(series)}d", *series)
    except (struct.error, TypeError):
        # Non-float-coercible input (shouldn't happen for persisted daily returns,
        # but never let a fingerprint failure crash the caller) — falls back to a
        # coarser summary that still changes whenever length/edges do.
        return repr((len(series), series[0], series[-1])).encode("utf-8", errors="replace")


def cohort_key(strategy_ids: list[str], returns_by_strategy: dict[str, list[float]]) -> str:
    """Stable data-version token for a rigor-gate cohort.

    Hashes ``sorted(strategy_ids)`` together with a fingerprint of each strategy's
    persisted returns series, so the key is:
      - independent of dict/list ordering (ids are sorted before hashing), and
      - guaranteed to change the moment ANY strategy's returns change, are added,
        or are removed — the property that makes caching the downstream
        computation safe without an explicit invalidation call.

    Strategies present in ``strategy_ids`` but absent from ``returns_by_strategy``
    (no persisted returns yet) still participate in the key via the empty-series
    fingerprint, so a strategy gaining its first persisted returns also changes
    the key.
    """
    hasher = hashlib.sha256()
    for sid in sorted(strategy_ids):
        hasher.update(sid.encode("utf-8"))
        hasher.update(b"\x00")
        hasher.update(_fingerprint(returns_by_strategy.get(sid) or []))
        hasher.update(b"\x01")
    return hasher.hexdigest()


def get_or_compute(key: str, compute_fn: Callable[[], Any]) -> Any:
    """Return the cached value for ``key`` if present and still fresh; else compute
    it via ``compute_fn()``, cache it, and return it.

    FAIL-OPEN: any exception raised while reading or writing the cache store itself
    is logged and swallowed — the function still calls ``compute_fn()`` and returns
    its (real, correct) result. This function never suppresses an exception raised
    BY ``compute_fn()`` itself — that is the caller's live computation failing, and
    must propagate exactly as it would without a cache in front of it.
    """
    try:
        now = time.monotonic()
        with _lock:
            entry = _store.get(key)
        if entry is not None:
            stored_at, value = entry
            if now - stored_at < _TTL_SECONDS:
                return value
    except Exception as exc:  # pragma: no cover - defensive; cache lookup must never break the request
        logger.warning("rigor_cache: lookup failed, falling back to live compute: %s", exc)
        return compute_fn()

    value = compute_fn()

    try:
        with _lock:
            _store[key] = (time.monotonic(), value)
    except Exception as exc:  # pragma: no cover - defensive; cache store must never break the request
        logger.warning("rigor_cache: store failed, serving live result anyway: %s", exc)

    return value


def clear() -> None:
    """Invalidate every cached rigor-gate result.

    Call this wherever persisted daily-returns data is (re)written (e.g.
    ``backtest_repository.insert_backtest_if_missing`` on a real insert) so a
    request immediately after a new backtest is written never has to wait out the
    TTL to see it. Not strictly required for correctness — ``cohort_key`` already
    changes the moment the underlying returns change, which is what makes an
    un-cleared cache still safe — this only tightens the window between "data
    written" and "next read recomputes" from up to ``_TTL_SECONDS`` to ~0.
    """
    with _lock:
        _store.clear()
