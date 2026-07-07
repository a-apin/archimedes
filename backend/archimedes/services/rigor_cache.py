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
import threading
import time
from array import array
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)

# Safety cap only. The cohort_key is the real invalidation mechanism — it changes
# the instant underlying returns data changes, so in practice a cache entry is
# almost always invalidated by a key change long before this TTL would matter. The
# TTL exists purely as a backstop against any invalidation-hook gap (e.g. a writer
# path that doesn't call `clear()`) so a cached entry can never live forever.
_TTL_SECONDS = 600.0

# Hard backstop on memory growth (Copilot review, PR #1040). The TTL alone only
# stops STALE entries from being SERVED — it does nothing to reclaim the memory
# an expired/obsolete entry occupies, since nothing ever revisits a key once it
# stops being requested. A frequently-changing key (e.g. a returns-rewrite path,
# or a growing/rotating strategy set) can otherwise accumulate keys in `_store`
# forever. Two backstops, both applied opportunistically on every cache WRITE
# (never on the read/lookup path, so a cache hit stays O(1)):
#   1. Prune every entry older than `_TTL_SECONDS` — cheap, and removes the
#      overwhelming majority of garbage in the common case.
#   2. Cap `_store` at `_MAX_STORE_SIZE` entries, evicting the OLDEST (by
#      `stored_at`) first — a hard ceiling independent of the TTL, so even a
#      pathological key-churn rate can't grow `_store` without bound.
_MAX_STORE_SIZE = 256

_lock = threading.Lock()
_store: dict[str, tuple[float, Any]] = {}


def _prune_expired_locked(now: float) -> None:
    """Remove every entry older than ``_TTL_SECONDS``. Caller must hold ``_lock``."""
    expired = [k for k, (stored_at, _value) in _store.items() if now - stored_at >= _TTL_SECONDS]
    for k in expired:
        del _store[k]


def _evict_oldest_locked(max_size: int) -> None:
    """Hard backstop: cap ``_store`` at ``max_size`` entries, evicting the
    oldest (smallest ``stored_at``) first. Caller must hold ``_lock``."""
    while len(_store) > max_size:
        oldest_key = min(_store, key=lambda k: _store[k][0])
        del _store[oldest_key]


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
        # array(...).tobytes() serializes the whole series without expanding it
        # into positional args (struct.pack(fmt, *series) would materialize one
        # arg per element — costly for large cohorts). Same order-and-value
        # sensitivity, C-contiguous double bytes.
        return array("d", series).tobytes()
    except (TypeError, ValueError, OverflowError):
        # Non-float-coercible input (shouldn't happen for persisted daily returns,
        # but never let a fingerprint failure crash the caller) — falls back to a
        # coarser summary that still changes whenever length/edges do.
        return repr((len(series), series[0], series[-1])).encode("utf-8", errors="replace")


def cohort_key(
    strategy_ids: list[str],
    returns_by_strategy: dict[str, list[float]],
    code_versions: dict[str, str | None] | None = None,
) -> str:
    """Stable data-version token for a rigor-gate cohort.

    Hashes ``sorted(strategy_ids)`` together with a fingerprint of each strategy's
    persisted returns series (and, when supplied, a code-version token per
    strategy — see below), so the key is:
      - independent of dict/list ordering (ids are sorted before hashing), and
      - guaranteed to change the moment ANY strategy's returns change, are added,
        or are removed — the property that makes caching the downstream
        computation safe without an explicit invalidation call.

    Strategies present in ``strategy_ids`` but absent from ``returns_by_strategy``
    (no persisted returns yet) still participate in the key via the empty-series
    fingerprint, so a strategy gaining its first persisted returns also changes
    the key.

    ``code_versions`` (optional) maps each strategy id to a cheap code-version
    token — the caller's ``Strategy.strategy_code_hash`` (a SHA-256 of the
    strategy file contents, already computed and held in memory — no extra I/O
    on the request path). This closes a real staleness gap (Copilot review, PR
    #1040): the cached computation this key guards doesn't just read persisted
    returns, it also runs the look-ahead code audit inside ``run_rigor_gate``
    (``strategy_code=...``), so a key built from returns alone would keep
    serving a stale look-ahead verdict / ``passes_all`` for up to the TTL after
    a strategy's code changed, even though its returns didn't. Folding the code
    hash in means a code edit changes the key exactly like a returns change
    does. Omitted or ``None`` for an id is treated as ``""`` — i.e. an id with
    no known code-version token doesn't perturb the key beyond that fixed
    placeholder, matching the prior (returns-only) behavior for callers that
    don't pass ``code_versions`` at all.
    """
    hasher = hashlib.sha256()
    code_versions = code_versions or {}
    for sid in sorted(strategy_ids):
        hasher.update(sid.encode("utf-8"))
        hasher.update(b"\x00")
        hasher.update(_fingerprint(returns_by_strategy.get(sid) or []))
        hasher.update(b"\x01")
        hasher.update((code_versions.get(sid) or "").encode("utf-8"))
        hasher.update(b"\x02")
    return hasher.hexdigest()


def get_or_compute(
    key: str,
    compute_fn: Callable[[], Any],
    cache_if: Callable[[Any], bool] = lambda _value: True,
) -> Any:
    """Return the cached value for ``key`` if present and still fresh; else compute
    it via ``compute_fn()``, cache it (subject to ``cache_if``), and return it.

    ``cache_if`` (optional, defaults to "always cache") is evaluated against the
    freshly computed value before it's written to the store. Callers whose
    ``compute_fn`` can legitimately return an empty/failure sentinel on a
    transient error (e.g. ``{}`` on a DB or cohort-compute failure) should pass
    ``cache_if=lambda v: bool(v)`` so that sentinel is never cached (Copilot
    review, PR #1040) — caching it would make a transient failure "sticky" for
    the full TTL, serving every caller a stale fallback long after the failure
    that caused it has passed. The live, correct value is still returned either
    way; ``cache_if`` only controls whether it's memoized for the NEXT caller.
    A ``cache_if`` that itself raises is treated as "don't cache" (never as
    "crash the request") — consistent with this module's fail-open contract.

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
        should_cache = cache_if(value)
    except Exception as exc:
        logger.warning("rigor_cache: cache_if predicate failed, not caching this result: %s", exc)
        should_cache = False

    if should_cache:
        try:
            with _lock:
                store_time = time.monotonic()
                # Opportunistic bound-keeping on every write (never on the read
                # path, so a cache hit stays O(1)): first reclaim anything
                # that's aged out, then enforce the hard size cap. See the
                # `_MAX_STORE_SIZE` docstring above for why both are needed.
                _prune_expired_locked(store_time)
                _store[key] = (store_time, value)
                _evict_oldest_locked(_MAX_STORE_SIZE)
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
