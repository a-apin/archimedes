"""Tests for services.rigor_cache — the honest, data-version-keyed TTL cache that
fixes the slow Library page load (``GET /api/strategies/`` ~6s, ``GET
/api/selection-bias/gate`` ~8-10s, both from recomputing the live rigor gate on
every request).

Three properties must hold, proven below:
  (a) Two consecutive calls with UNCHANGED persisted data return IDENTICAL results
      and ``run_rigor_gate`` runs only ONCE per strategy (the second call is a
      cache hit) — the cache never re-does work it doesn't need to.
  (b) Changing a strategy's persisted returns changes the cache key, so
      ``run_rigor_gate`` runs again and the served numbers reflect the new data —
      the cache never serves a stale number for changed data.
  (c) A cache-layer error (the cache itself, not the live computation) falls back
      to live compute — the cache can only make a request slow, never wrong.

Hermetic gate:
  env -i HOME=$HOME PATH=$PATH PYTHONPATH=backend python -m pytest \\
      backend/tests/test_rigor_cache.py -q
"""

from __future__ import annotations

import numpy as np
import pytest
from archimedes.services import rigor_cache
from archimedes.services.rigor_evaluator import (
    compute_average_pairwise_correlation,
    compute_pbo,
    run_rigor_gate,
)

# ── Hermetic DB fixture (same pattern as test_strategies_routes.py) ────────


@pytest.fixture(autouse=True)
def _use_tmp_db(tmp_path, monkeypatch):
    """Redirect DATABASE_URL to a per-test temp SQLite file.

    ``_live_rigor_results_for_strategies`` calls ``init_db()`` before the (here
    monkeypatched) DB read, so a real, isolated DB backs every test.
    """
    from archimedes.db import init_db

    db_path = tmp_path / "test_rigor_cache.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    init_db()
    yield


@pytest.fixture(autouse=True)
def _isolated_rigor_cache():
    """Every test gets a clean process-level cache — this module's cache is a
    module-global dict, so without this fixture, state (and the raise-on-access
    monkeypatches used in the fail-open tests) would leak across tests."""
    rigor_cache.clear()
    yield
    rigor_cache.clear()


# A clean strategy snippet that passes the AST look-ahead audit (no future/peek
# access) — same fixture literal test_strategies_routes.py uses.
_CLEAN_CODE = "def init(self):\n    self.sma = 0\n"


def _series(seed: int, n: int = 300) -> list[float]:
    return np.random.default_rng(seed).normal(0.0015, 0.004, n).tolist()


class _FakeStrategy:
    """Minimal duck-typed stand-in for ``models.strategy.Strategy`` — only the
    attributes ``_live_rigor_results_for_strategies`` / ``_load_strategy_code_safe_local``
    actually touch."""

    def __init__(self, id_: str, paper_claimed_sharpe: float | None = None):
        self.id = id_
        self.paper_claimed_sharpe = paper_claimed_sharpe
        self.strategy_code_path = None  # None -> _load_strategy_code_safe_local short-circuits, no file I/O


def _spy_run_rigor_gate(monkeypatch, calls: list):
    """Patch archimedes.services.rigor_evaluator.run_rigor_gate with a counting
    wrapper that still delegates to the real implementation — same technique
    test_strategies_routes.py::TestDefaultNumTrials uses. The batch compute
    closure in strategies_routes imports run_rigor_gate locally (function-local
    import), so patching the source module's attribute is what makes this work:
    the local import re-reads the module namespace on every call.
    """

    def _spy(*args, **kwargs):
        calls.append((args, kwargs))
        return run_rigor_gate(*args, **kwargs)

    monkeypatch.setattr("archimedes.services.rigor_evaluator.run_rigor_gate", _spy)


def _assert_close(actual, expected) -> None:
    """``pytest.approx`` chokes on ``None`` — both metrics can legitimately be
    ``None`` (e.g. an OOS Sharpe the gate could not compute), so compare those
    cases with plain equality and everything else with a NaN-tolerant approx."""
    if expected is None or actual is None:
        assert actual == expected
    else:
        assert actual == pytest.approx(expected, nan_ok=True)


def _expected_result(sid: str, returns_by_strategy: dict[str, list[float]]):
    """Independently reproduce what the (uncached) live gate should compute for
    ``sid`` given ``returns_by_strategy`` — the same cohort-context derivation
    ``_live_rigor_results_for_strategies`` performs, used to assert the cache
    never changes the served numbers."""
    valid = {k: v for k, v in returns_by_strategy.items() if len(v) >= 10 and float(np.ptp(np.asarray(v))) > 0.0}
    pbo_scores = compute_pbo(valid) if len(valid) >= 2 else {}
    num_trials = max(len(valid), 1)
    avg_corr = compute_average_pairwise_correlation(valid) if len(valid) >= 2 else 0.0
    return run_rigor_gate(
        strategy_id=sid,
        daily_returns=returns_by_strategy[sid],
        num_trials=num_trials,
        pbo_scores=pbo_scores,
        strategy_code=_CLEAN_CODE,
        in_sample_sharpe=None,
        average_correlation=avg_corr,
    )


# ── (a) unchanged data -> cache hit, identical results, one live call each ──


def test_second_call_with_unchanged_data_is_a_cache_hit(monkeypatch):
    from archimedes.api import strategies_routes as sr

    returns = {"s0": _series(0), "s1": _series(1)}
    monkeypatch.setattr(
        "archimedes.services.backtest_repository.get_all_daily_returns",
        lambda session, ids: dict(returns),
    )
    monkeypatch.setattr(sr, "_load_strategy_code_safe_local", lambda s: _CLEAN_CODE)

    calls: list = []
    _spy_run_rigor_gate(monkeypatch, calls)

    strategies = [_FakeStrategy("s0"), _FakeStrategy("s1")]

    first = sr._live_rigor_results_for_strategies(strategies)
    assert len(calls) == 2, "first call must run the live gate once per strategy"

    second = sr._live_rigor_results_for_strategies(strategies)
    assert len(calls) == 2, "second call with unchanged data must be a pure cache hit — no new run_rigor_gate calls"

    assert set(first) == set(second) == {"s0", "s1"}
    for sid in first:
        assert second[sid] is first[sid], f"cache hit for {sid} should return the SAME cached object"
        assert second[sid].deflated_sharpe == first[sid].deflated_sharpe
        assert second[sid].pbo_score == first[sid].pbo_score
        assert second[sid].oos_sharpe == first[sid].oos_sharpe
        assert second[sid].passes_all == first[sid].passes_all


# ── (b) changed returns -> different key -> live gate runs again ───────────


def test_changed_returns_invalidate_the_cache_and_recompute(monkeypatch):
    from archimedes.api import strategies_routes as sr

    returns = {"s0": _series(0), "s1": _series(1)}
    get_all = {"value": dict(returns)}
    monkeypatch.setattr(
        "archimedes.services.backtest_repository.get_all_daily_returns",
        lambda session, ids: dict(get_all["value"]),
    )
    monkeypatch.setattr(sr, "_load_strategy_code_safe_local", lambda s: _CLEAN_CODE)

    calls: list = []
    _spy_run_rigor_gate(monkeypatch, calls)

    strategies = [_FakeStrategy("s0"), _FakeStrategy("s1")]

    first = sr._live_rigor_results_for_strategies(strategies)
    assert len(calls) == 2

    # s0's persisted returns change (e.g. a fresh backtest was written) — a
    # DIFFERENT series than the one the first call was keyed on.
    new_returns = {"s0": _series(123), "s1": _series(1)}
    get_all["value"] = new_returns

    second = sr._live_rigor_results_for_strategies(strategies)
    assert len(calls) == 4, "changed cohort data must bust the cache key -> the live gate reruns for both strategies"

    expected_s0 = _expected_result("s0", new_returns)
    _assert_close(second["s0"].deflated_sharpe, expected_s0.deflated_sharpe)
    _assert_close(second["s0"].oos_sharpe, expected_s0.oos_sharpe)
    assert second["s0"].passes_all == expected_s0.passes_all

    # s1's series is unchanged, but it still shares a cohort with s0 (its cache
    # key includes both strategies' fingerprints), so it recomputes too — and
    # must still reflect the SAME live numbers as before, since ITS data didn't
    # change (only the pbo/correlation cohort context could shift with s0's new
    # series, so we recompute the "expected" for s1 with the new cohort too).
    expected_s1 = _expected_result("s1", new_returns)
    _assert_close(second["s1"].deflated_sharpe, expected_s1.deflated_sharpe)

    # And critically: results actually differ from the first call's stale cache
    # entry (proving this isn't accidentally still serving the old value).
    assert (
        first["s0"].deflated_sharpe != second["s0"].deflated_sharpe
        or first["s0"].oos_sharpe != second["s0"].oos_sharpe
        or first["s0"].pbo_score != second["s0"].pbo_score
    ), "second call must reflect the new data, not the first call's cached result"


# ── (c) cache-layer error -> fail open to live compute ──────────────────────


class _BoomOnGet(dict):
    """A dict-like whose ``.get`` raises — simulates a cache-layer bug (e.g. a
    corrupted store, a serialization error) independent of the live computation."""

    def get(self, *args, **kwargs):
        raise RuntimeError("simulated cache-layer failure")


def test_get_or_compute_fails_open_on_lookup_error(monkeypatch):
    """Pure unit-level proof: if reading the cache store raises, get_or_compute
    still calls compute_fn and returns its (real, correct) result — never raises,
    never serves a stale/fabricated value."""
    monkeypatch.setattr(rigor_cache, "_store", _BoomOnGet())

    sentinel = object()
    calls = {"n": 0}

    def _compute():
        calls["n"] += 1
        return sentinel

    result = rigor_cache.get_or_compute("some-key", _compute)
    assert result is sentinel
    assert calls["n"] == 1


def test_get_or_compute_fails_open_on_store_error(monkeypatch):
    """Same fail-open guarantee when WRITING to the cache raises (e.g. the
    store rejects the value) — compute_fn's result must still be returned."""

    class _BoomOnSet(dict):
        def __setitem__(self, key, value):
            raise RuntimeError("simulated cache write failure")

    monkeypatch.setattr(rigor_cache, "_store", _BoomOnSet())

    sentinel = object()
    result = rigor_cache.get_or_compute("some-key", lambda: sentinel)
    assert result is sentinel


def test_live_rigor_results_survive_a_broken_cache(monkeypatch):
    """Route-level proof of the same guarantee: with the cache store patched to
    raise on lookup, ``_live_rigor_results_for_strategies`` must still return the
    exact same numbers a healthy cache would have — the cache is fully
    bypassable without breaking or staling the served rigor numbers."""
    from archimedes.api import strategies_routes as sr

    returns = {"s0": _series(0), "s1": _series(1)}
    monkeypatch.setattr(
        "archimedes.services.backtest_repository.get_all_daily_returns",
        lambda session, ids: dict(returns),
    )
    monkeypatch.setattr(sr, "_load_strategy_code_safe_local", lambda s: _CLEAN_CODE)
    monkeypatch.setattr(rigor_cache, "_store", _BoomOnGet())

    strategies = [_FakeStrategy("s0"), _FakeStrategy("s1")]
    result = sr._live_rigor_results_for_strategies(strategies)

    expected_s0 = _expected_result("s0", returns)
    expected_s1 = _expected_result("s1", returns)
    _assert_close(result["s0"].deflated_sharpe, expected_s0.deflated_sharpe)
    assert result["s0"].passes_all == expected_s0.passes_all
    _assert_close(result["s1"].deflated_sharpe, expected_s1.deflated_sharpe)
    assert result["s1"].passes_all == expected_s1.passes_all


# ── cohort_key unit properties ──────────────────────────────────────────────


def test_cohort_key_stable_for_identical_input():
    returns = {"a": _series(1), "b": _series(2)}
    k1 = rigor_cache.cohort_key(["a", "b"], returns)
    k2 = rigor_cache.cohort_key(["b", "a"], dict(returns))  # order-independent
    assert k1 == k2


def test_cohort_key_changes_when_any_series_changes():
    returns = {"a": _series(1), "b": _series(2)}
    k1 = rigor_cache.cohort_key(["a", "b"], returns)

    mutated = dict(returns)
    mutated["a"] = _series(1)[:-1] + [0.999]  # tweak one strategy's series
    k2 = rigor_cache.cohort_key(["a", "b"], mutated)
    assert k1 != k2


def test_cohort_key_changes_when_a_strategy_is_added_or_removed():
    returns = {"a": _series(1)}
    k1 = rigor_cache.cohort_key(["a"], returns)
    k2 = rigor_cache.cohort_key(["a", "b"], {**returns, "b": _series(2)})
    assert k1 != k2


def test_clear_removes_all_entries():
    rigor_cache.get_or_compute("k1", lambda: 1)
    rigor_cache.get_or_compute("k2", lambda: 2)
    assert rigor_cache._store  # sanity: something is cached
    rigor_cache.clear()
    assert rigor_cache._store == {}
