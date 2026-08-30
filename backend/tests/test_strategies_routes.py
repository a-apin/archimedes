"""Tests for strategies_routes — the library-list rigor badge (#821).

The user-facing ``passes_rigor_gate`` badge (and the CANDIDATE → VALIDATED 🏆
promotion) on ``GET /api/strategies/`` must come from a LIVE ``run_rigor_gate``
verdict computed on the strategy's persisted real returns — the SAME machinery
the ``/api/selection-bias/gate`` route uses — NOT from the stored fixture boolean
in ``analytics-engine/strategies/backtest_fixtures.json``. A strategy with no real
returns surfaces an explicit ``pending`` badge, never a fixture ``True``/``False``.

These tests mock at the DB boundary (``get_all_daily_returns``) — the persisted
real-returns source — and assert the served badge equals an independently-computed
``run_rigor_gate`` verdict on the same returns.

Hermetic gate:
  env -i HOME=$HOME PATH=$PATH PYTHONPATH=backend python -m pytest \\
      backend/tests/test_strategies_routes.py -q
"""

from __future__ import annotations

from unittest.mock import MagicMock

import numpy as np
import pytest
from archimedes.services.live_rigor_gate import (
    DEGENERATE,
    FAIL,
    PASS,
    PENDING,
    RigorGateVerdict,
    verdict_from_returns,
)
from archimedes.services.rigor_evaluator import (
    compute_average_pairwise_correlation,
    compute_pbo,
    run_rigor_gate,
)
from httpx import ASGITransport, AsyncClient

# ── Hermetic DB fixture ────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _use_tmp_db(tmp_path, monkeypatch):
    """Redirect DATABASE_URL to a per-test temp SQLite file.

    The list endpoint calls init_db() (via verdicts_for_strategies) before
    querying persisted backtest data. Isolating the DB per test satisfies the
    hermetic-test mandate and prevents cross-run state.
    """
    from archimedes.db import init_db

    db_path = tmp_path / "test_strategies_routes.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    init_db()
    yield


# A clean strategy snippet that passes the AST look-ahead audit (no future/peek
# access). Real curated strategy files also pass; this keeps the test independent
# of any particular file on disk.
_CLEAN_CODE = "def init(self):\n    self.sma = 0\n"


def _passing_series(seed: int = 0, n: int = 500) -> list[float]:
    """A return series engineered to clear the live gate when paired with a
    cohort (≥2 strategies for PBO) + clean code (look-ahead pass)."""
    return np.random.default_rng(seed).normal(0.0015, 0.004, n).tolist()


def _failing_series(seed: int = 99, n: int = 500) -> list[float]:
    """Pure-noise series — negative/zero drift, high vol — that the gate fails."""
    return np.random.default_rng(seed).normal(0.0, 0.02, n).tolist()


# ── live_rigor_gate unit tests (the single source of truth) ─────────────


class TestVerdictFromReturns:
    def test_no_returns_is_pending(self):
        v = verdict_from_returns("s", [])
        assert v.status == PENDING
        assert v.passes is False
        assert v.source == "pending"

    def test_too_few_returns_is_pending(self):
        # Below the 10-obs floor the gate cannot run → pending, NOT a boolean.
        v = verdict_from_returns("s", [0.01] * 9)
        assert v.status == PENDING
        assert v.passes is False

    def test_pending_source_is_never_fixture(self):
        # The whole point of #821: pending must never be a stored boolean.
        v = verdict_from_returns("s", [])
        assert v.source != "fixture"

    def test_real_returns_yield_live_verdict_not_boolean(self):
        # With real returns the verdict comes from run_rigor_gate, not a constant.
        a = _passing_series(0)
        b = _passing_series(1)
        pbo = compute_pbo({"a": a, "b": b})
        v = verdict_from_returns("a", a, num_trials=2, pbo_scores=pbo, strategy_code=_CLEAN_CODE)
        expected = run_rigor_gate("a", a, num_trials=2, pbo_scores=pbo, strategy_code=_CLEAN_CODE)
        assert v.passes == expected.passes_all
        assert v.status == (PASS if expected.passes_all else FAIL)
        assert v.source == "live_gate"

    def test_noise_series_fails_live_gate(self):
        v = verdict_from_returns("noise", _failing_series(), num_trials=4)
        assert v.status == FAIL
        assert v.passes is False

    def test_gate_exception_fails_closed_to_pending(self, monkeypatch):
        # If run_rigor_gate raises, the badge must NOT claim a pass.
        def _boom(*a, **k):
            raise RuntimeError("gate exploded")

        monkeypatch.setattr("archimedes.services.rigor_evaluator.run_rigor_gate", _boom)
        # verdict_from_returns is already imported at module top; patching the source
        # module's run_rigor_gate (which it imports locally) is what makes this work.
        v = verdict_from_returns("s", _passing_series(), num_trials=2)
        assert v.status == PENDING
        assert v.passes is False


class TestDefaultNumTrials:
    """Decouple #2: an unspecified num_trials defaults to a self-contained 1 —
    never derived from the curated library's count."""

    def test_verdict_from_returns_defers_to_default_num_trials(self, monkeypatch):
        # verdict_from_returns still delegates to _default_num_trials() when the
        # caller passes nothing — this pins the indirection, independent of what
        # _default_num_trials() itself resolves to.
        from archimedes.services import live_rigor_gate

        captured = {}

        def _spy_gate(*args, **kwargs):
            captured.update(kwargs)
            return run_rigor_gate(*args, **kwargs)

        monkeypatch.setattr("archimedes.services.rigor_evaluator.run_rigor_gate", _spy_gate)
        monkeypatch.setattr(live_rigor_gate, "_default_num_trials", lambda: 7)

        verdict_from_returns("a", _passing_series(0), strategy_code=_CLEAN_CODE)
        assert captured["num_trials"] == 7

    def test_default_num_trials_is_self_contained_one(self, monkeypatch):
        # A strategy's rigor depends ONLY on itself — _default_num_trials() must
        # always resolve to 1, regardless of the curated library's size or
        # whether the strategy provider is even reachable (decouple #2 removed
        # the library-size lookup entirely, so this must not touch it at all).
        from archimedes.services import live_rigor_gate

        def _boom():
            raise AssertionError("_default_num_trials must not consult the strategy provider")

        monkeypatch.setattr("archimedes.services.strategy_provider.default_provider", _boom)
        assert live_rigor_gate._default_num_trials() == 1

    def test_explicit_num_trials_still_wins(self, monkeypatch):
        from archimedes.services import live_rigor_gate

        captured = {}

        def _spy_gate(*args, **kwargs):
            captured.update(kwargs)
            return run_rigor_gate(*args, **kwargs)

        monkeypatch.setattr("archimedes.services.rigor_evaluator.run_rigor_gate", _spy_gate)
        monkeypatch.setattr(
            live_rigor_gate, "_default_num_trials", lambda: (_ for _ in ()).throw(AssertionError("not called"))
        )

        verdict_from_returns("a", _passing_series(0), num_trials=3, strategy_code=_CLEAN_CODE)
        assert captured["num_trials"] == 3


class TestSingleStrategyCohortContext:
    """#902: the single-strategy fetch must grade with the library cohort, so the
    detail view can never disagree with the (deflated) list badge."""

    def test_live_verdict_for_one_uses_full_library_cohort(self, monkeypatch):
        from archimedes.api import strategies_routes

        lib = [MagicMock(id=f"s{i}") for i in range(3)]
        target = lib[1]
        provider = MagicMock()
        provider.list_strategies.return_value = lib
        monkeypatch.setattr(strategies_routes, "strategy_provider", lambda: provider)

        captured = {}

        def _fake_batch(strategies):
            captured["cohort_ids"] = [s.id for s in strategies]
            return {target.id: RigorGateVerdict.failed()}

        monkeypatch.setattr(strategies_routes, "verdicts_for_strategies", _fake_batch)

        v = strategies_routes._live_verdict_for_one(target)
        assert captured["cohort_ids"] == ["s0", "s1", "s2"]
        assert v.status == FAIL

    def test_live_verdict_for_one_appends_unlisted_strategy(self, monkeypatch):
        # A just-generated strategy missing from the provider list still grades.
        from archimedes.api import strategies_routes

        provider = MagicMock()
        provider.list_strategies.return_value = [MagicMock(id="s0")]
        monkeypatch.setattr(strategies_routes, "strategy_provider", lambda: provider)

        captured = {}

        def _fake_batch(strategies):
            captured["cohort_ids"] = [s.id for s in strategies]
            return {}

        monkeypatch.setattr(strategies_routes, "verdicts_for_strategies", _fake_batch)

        fresh = MagicMock(id="fresh")
        v = strategies_routes._live_verdict_for_one(fresh)
        assert "fresh" in captured["cohort_ids"]
        # Batch returned nothing for it → fail-closed pending, never a fixture.
        assert v.status == PENDING

    def test_live_rigor_result_for_one_uses_full_library_cohort(self, monkeypatch):
        from archimedes.api import strategies_routes

        lib = [MagicMock(id="s0"), MagicMock(id="s1")]
        provider = MagicMock()
        provider.list_strategies.return_value = lib
        monkeypatch.setattr(strategies_routes, "strategy_provider", lambda: provider)

        sentinel = object()
        captured = {}

        def _fake_batch(strategies):
            captured["cohort_ids"] = [s.id for s in strategies]
            return {"s1": sentinel}

        monkeypatch.setattr(strategies_routes, "_live_rigor_results_for_strategies", _fake_batch)

        assert strategies_routes._live_rigor_result_for_one(lib[1]) is sentinel
        assert captured["cohort_ids"] == ["s0", "s1"]

    @pytest.mark.asyncio
    async def test_list_route_grades_full_library_not_the_page(self, monkeypatch):
        """REGRESSION (#1173): the LIST badge must be graded over the whole library,
        never the paginated window.

        The detail route grades via ``_library_cohort_including`` (the test above
        pins that), so grading the list over ``strats[offset:offset+limit]`` made
        the same strategy's badge depend on which page it landed on — a short window
        can fall under ``MIN_LIBRARY_N_FOR_PBO_GATING`` (criterion 4 skipped) and the
        cohort-scoped PBO/CSCV value itself shifts with membership. Verified against
        production before the fix: strategy ``d90b357a…4bbd`` graded False in a
        5-item window but True in the full-library view and True on its own
        detail/passport route — the exact list-vs-detail contradiction dfa8fc1 was
        written to prevent.
        """
        from archimedes.api import strategies_routes
        from archimedes.main import app

        lib = [MagicMock(id=f"s{i}", status=None) for i in range(30)]
        provider = MagicMock()
        provider.list_strategies.return_value = lib
        monkeypatch.setattr(strategies_routes, "strategy_provider", lambda: provider)

        captured = {}

        def _fake_batch(strategies):
            captured["cohort_ids"] = [s.id for s in strategies]
            return {}

        monkeypatch.setattr(strategies_routes, "_live_rigor_results_for_strategies", _fake_batch)

        # The cohort is handed to the gate BEFORE any response serialization, so we
        # assert on it regardless of whether serializing these MagicMock strategies
        # succeeds. raise_app_exceptions=False keeps a serialization 500 from
        # masking the assertion we actually care about.
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            await client.get("/api/strategies/?limit=5&offset=25")

        assert captured.get("cohort_ids") == [s.id for s in lib], (
            "list route must grade the FULL library cohort; got a "
            f"{len(captured.get('cohort_ids', []))}-item cohort for a 5-item page"
        )


class TestRigorGateVerdict:
    def test_passes_only_truthy_for_pass(self):
        assert RigorGateVerdict.passed().passes is True
        assert RigorGateVerdict.failed().passes is False
        assert RigorGateVerdict.pending().passes is False
        assert RigorGateVerdict.degenerate().passes is False

    def test_status_labels(self):
        assert RigorGateVerdict.passed().status == PASS
        assert RigorGateVerdict.failed().status == FAIL
        assert RigorGateVerdict.pending().status == PENDING
        assert RigorGateVerdict.degenerate().status == DEGENERATE

    def test_degenerate_never_deployable_at_any_level(self):
        """#1184: broken/zero-trade data blocks every strictness level, same as
        any other always-on correctness floor — never just the strictest bar."""
        v = RigorGateVerdict.degenerate()
        assert v.blocked_by_floor is True
        assert v.min_passing_level is None


class TestVerdictFromResultDegenerate:
    """#1184: the LIST/DETAIL route badge (``_verdict_from_result``) must report
    a zero-variance persisted series as ``degenerate`` — the same category
    ``live_rigor_gate.verdict_from_returns`` reports for the identical input —
    not silently diverge into a plain ``fail`` just because this route reduces
    an already-computed ``RigorGateResult`` instead of calling the gate itself.
    """

    def test_degenerate_result_maps_to_degenerate_verdict(self):
        from archimedes.api.strategies_routes import _verdict_from_result

        result = run_rigor_gate(strategy_id="lib-degenerate", daily_returns=[0.0] * 5659, num_trials=1)
        assert result.is_degenerate is True  # sanity: the input really is degenerate

        v = _verdict_from_result(result)
        assert v.status == DEGENERATE
        assert v.status != FAIL
        assert v.status != PENDING
        assert v.passes is False

    def test_none_result_still_maps_to_pending(self):
        """No live gate result at all (insufficient/no persisted returns) stays
        the pre-existing PENDING — the new category must not swallow this case."""
        from archimedes.api.strategies_routes import _verdict_from_result

        assert _verdict_from_result(None).status == PENDING

    def test_non_degenerate_fail_result_still_maps_to_fail(self):
        rng = np.random.default_rng(9)
        losing = rng.normal(-0.002, 0.01, size=300).tolist()
        result = run_rigor_gate(strategy_id="lib-real-loser", daily_returns=losing, num_trials=1)
        assert result.is_degenerate is False

        from archimedes.api.strategies_routes import _verdict_from_result

        v = _verdict_from_result(result)
        assert v.status == FAIL


# ── Acceptance #1: served badge == live run_rigor_gate verdict ──────────


@pytest.mark.asyncio
async def test_library_badge_equals_live_gate_verdict_on_persisted_returns(monkeypatch):
    """ACCEPTANCE #1: the library-list ``passes_rigor_gate`` for a real strategy
    equals the live ``run_rigor_gate`` verdict computed on its persisted returns.

    We inject known persisted returns at the DB boundary (get_all_daily_returns),
    serve the real ``GET /api/strategies/`` endpoint, then independently recompute
    the gate verdict over the SAME returns (same num_trials / cohort PBO /
    avg-correlation the route derives) and assert every served badge matches.
    """
    from archimedes.api import strategies_routes as sr
    from archimedes.main import app

    strategies = sr.strategy_provider().list_strategies()
    assert len(strategies) >= 2, "need ≥2 curated strategies for a PBO cohort"

    # Give the first two real strategies persisted returns: one engineered to
    # pass, one engineered to fail. The rest get no returns (→ pending).
    s_pass, s_fail = strategies[0], strategies[1]
    returns = {s_pass.id: _passing_series(0), s_fail.id: _failing_series()}

    # Patch the DB boundary used by verdicts_for_strategies (imported inside the
    # function body, so patch the definition module).
    monkeypatch.setattr(
        "archimedes.services.backtest_repository.get_all_daily_returns",
        lambda session, ids: dict(returns),
    )
    # Make the look-ahead audit deterministic (clean code → pass) for both. The
    # served badge on GET /api/strategies/ comes from _live_rigor_results_for_
    # strategies (#868), which reads its OWN local loader — patch both loaders
    # so this test and test_leaderboard_numeric_fields_equal_live_gate_on_
    # persisted_returns (below) see identical code. Only patching the
    # live_rigor_gate loader left the served badge reading REAL (uncontrolled)
    # strategy source, a gap that stayed masked while num_trials=len(cohort)
    # deflation dominated passes_all; it surfaces now that num_trials=1
    # (decouple #2) makes DSR pass easily and the look-ahead leg decide.
    monkeypatch.setattr(
        "archimedes.services.live_rigor_gate._load_strategy_code_safe",
        lambda strategy: _CLEAN_CODE,
    )
    monkeypatch.setattr(
        "archimedes.api.strategies_routes._load_strategy_code_safe_local",
        lambda strategy: _CLEAN_CODE,
    )

    # Independently reproduce the route's cohort context. num_trials is
    # self-contained (1, decouple #2) — it does NOT come from this cohort;
    # only PBO/avg_correlation are cohort-derived.
    valid = {k: v for k, v in returns.items() if len(v) >= 10}
    pbo_scores = compute_pbo(valid) if len(valid) >= 2 else {}
    num_trials = 1
    avg_corr = compute_average_pairwise_correlation(valid) if len(valid) >= 2 else 0.0

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/strategies/?limit=100")
    assert resp.status_code == 200
    by_id = {s["id"]: s for s in resp.json()["strategies"]}

    for sid, series in returns.items():
        expected = run_rigor_gate(
            strategy_id=sid,
            daily_returns=series,
            num_trials=num_trials,
            pbo_scores=pbo_scores,
            strategy_code=_CLEAN_CODE,
            in_sample_sharpe=None,
            average_correlation=avg_corr,
        )
        served = by_id[sid]
        assert served["passes_rigor_gate"] == expected.passes_all, (
            f"served badge for {sid} ({served['passes_rigor_gate']}) != live gate verdict ({expected.passes_all})"
        )
        # Four-state comparison (#1184): degenerate is neither PASS nor FAIL, so
        # comparing only against passes_all would misclassify a degenerate series
        # as FAIL. Use the same tri_state_status the route's badge is built from.
        assert served["rigor_gate_status"] == expected.tri_state_status

    # The engineered pass strategy passes; the noise strategy fails — on the LIVE path.
    assert by_id[s_pass.id]["passes_rigor_gate"] is True
    assert by_id[s_fail.id]["passes_rigor_gate"] is False


@pytest.mark.asyncio
async def test_degenerate_persisted_series_serves_degenerate_badge_via_route(monkeypatch):
    """ACCEPTANCE #4 (#1184): a zero-variance persisted return series is served
    as the DEGENERATE badge by the REAL ``GET /api/strategies/`` route.

    The unit tests in ``TestDegenerateSeriesCategory`` (test_rigor_evaluator.py)
    and ``TestVerdictFromResultDegenerate`` above already cover
    ``run_rigor_gate`` / ``_verdict_from_result`` in isolation; this exercises
    the same category end-to-end through the ASGI transport, the way
    Acceptance #1 exercises the non-degenerate case above — otherwise the enum
    assertions elsewhere in this file (e.g.
    ``test_list_endpoint_returns_200_and_status_field``) could silently start
    rejecting a real degenerate strategy without any route-level test catching
    it.
    """
    from archimedes.api import strategies_routes as sr
    from archimedes.main import app

    strategies = sr.strategy_provider().list_strategies()
    assert strategies, "need at least 1 curated strategy"
    target = strategies[0]

    # A constant (all-zero) 5,659-observation series — the exact shape #1184
    # names (mathematically constant, most plausibly all-zero).
    degenerate_series = [0.0] * 5659
    monkeypatch.setattr(
        "archimedes.services.backtest_repository.get_all_daily_returns",
        lambda session, ids: {target.id: degenerate_series},
    )
    # The route's badge reads its OWN local code loader (#868) — patch both so
    # this test doesn't depend on the real strategy source files (mirrors the
    # pattern in test_library_badge_equals_live_gate_verdict_on_persisted_returns
    # above). Not load-bearing for the DEGENERATE verdict itself — is_degenerate
    # short-circuits tri_state_status before the look-ahead leg is consulted —
    # but keeps this test deterministic and independent of on-disk source.
    monkeypatch.setattr(
        "archimedes.services.live_rigor_gate._load_strategy_code_safe",
        lambda strategy: _CLEAN_CODE,
    )
    monkeypatch.setattr(
        "archimedes.api.strategies_routes._load_strategy_code_safe_local",
        lambda strategy: _CLEAN_CODE,
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/strategies/?limit=100")
    assert resp.status_code == 200
    by_id = {s["id"]: s for s in resp.json()["strategies"]}

    served = by_id[target.id]
    assert served["rigor_gate_status"] == DEGENERATE
    assert served["passes_rigor_gate"] is False


# ── Acceptance #2: no real returns → pending, not a fixture boolean ─────


@pytest.mark.asyncio
async def test_strategy_without_real_returns_is_pending(monkeypatch):
    """ACCEPTANCE #2: a strategy with NO persisted returns surfaces a ``pending``
    badge — never a fixture True/False. We force the DB to return nothing for
    every strategy, so the live gate cannot run for any of them."""
    from archimedes.main import app

    monkeypatch.setattr(
        "archimedes.services.backtest_repository.get_all_daily_returns",
        lambda session, ids: {},
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/strategies/?limit=100")
    assert resp.status_code == 200
    served = resp.json()["strategies"]
    assert served, "expected curated strategies in the library"

    # EVERY strategy is pending with passes_rigor_gate=False — including the two
    # strategies whose FIXTURE value is True (moreira_muir, moskowitz_ooi_pedersen).
    # If the fixture boolean were still the source, those two would read True.
    for s in served:
        assert s["rigor_gate_status"] == PENDING, f"{s['id']} not pending: {s['rigor_gate_status']}"
        assert s["passes_rigor_gate"] is False, f"{s['id']} leaked a non-live pass badge"
        # #1358 A4: the live gate never ran for this strategy, so there is no
        # provenance to report — never a silently-assumed self-contained 1.
        assert s["num_trials_in_selection"] is None, f"{s['id']} claimed a num_trials with no live gate run"
        assert s["num_trials_scope"] == "unspecified", f"{s['id']} scope: {s['num_trials_scope']}"


@pytest.mark.asyncio
async def test_fixture_true_strategies_do_not_read_true_without_live_returns(monkeypatch):
    """The two fixture-True strategies (moreira_muir, moskowitz_ooi_pedersen) must
    NOT show passes_rigor_gate=True purely from the fixture: with no live returns
    they are ``pending``. This is the direct anti-regression for #821."""
    from archimedes.api import strategies_routes as sr
    from archimedes.main import app

    monkeypatch.setattr(
        "archimedes.services.backtest_repository.get_all_daily_returns",
        lambda session, ids: {},
    )

    # Resolve the deterministic strategy ids for the two fixture-True stems.
    by_path = {}
    for s in sr.strategy_provider().list_strategies():
        by_path[s.strategy_code_path or ""] = s
    fixture_true_stems = ("moreira_muir_2017_volatility_managed", "moskowitz_ooi_pedersen_2012_tsmom")
    targets = [s for path, s in by_path.items() if any(stem in path for stem in fixture_true_stems)]
    assert targets, "could not resolve the fixture-True strategies"

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/strategies/?limit=100")
    by_id = {s["id"]: s for s in resp.json()["strategies"]}

    for t in targets:
        served = by_id[t.id]
        assert served["passes_rigor_gate"] is False, f"{t.id} read fixture True on the live path"
        assert served["rigor_gate_status"] == PENDING


# ── CANDIDATE → VALIDATED promotion is live, not fixture-driven ─────────


@pytest.mark.asyncio
async def test_validated_promotion_only_when_live_gate_passes(monkeypatch):
    """A CANDIDATE is promoted to VALIDATED only when the LIVE gate passes on real
    returns — not because a fixture said so. With no live returns, every CANDIDATE
    stays CANDIDATE (no fixture-driven 🏆)."""
    from archimedes.main import app

    monkeypatch.setattr(
        "archimedes.services.backtest_repository.get_all_daily_returns",
        lambda session, ids: {},
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/strategies/?limit=100")
    served = resp.json()["strategies"]

    # No live returns → no strategy may be served as "validated" purely from a fixture.
    for s in served:
        if s["rigor_gate_status"] == PENDING:
            assert s["status"] != "validated", f"{s['id']} promoted to validated without a live pass"


@pytest.mark.asyncio
async def test_validated_promotion_fires_on_live_pass(monkeypatch):
    """When the live gate passes on persisted returns for a CANDIDATE strategy, the
    served status is promoted to VALIDATED."""
    from archimedes.api import strategies_routes as sr
    from archimedes.main import app
    from archimedes.models.strategy import StrategyStatus

    strategies = sr.strategy_provider().list_strategies()
    candidates = [s for s in strategies if s.status == StrategyStatus.CANDIDATE]
    assert len(candidates) >= 2, "need ≥2 CANDIDATE strategies"
    s_pass = candidates[0]
    cohort = candidates[1]
    returns = {s_pass.id: _passing_series(0), cohort.id: _passing_series(1)}

    monkeypatch.setattr(
        "archimedes.services.backtest_repository.get_all_daily_returns",
        lambda session, ids: dict(returns),
    )
    monkeypatch.setattr(
        "archimedes.services.live_rigor_gate._load_strategy_code_safe",
        lambda strategy: _CLEAN_CODE,
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/strategies/?limit=100")
    by_id = {s["id"]: s for s in resp.json()["strategies"]}

    if by_id[s_pass.id]["passes_rigor_gate"]:
        assert by_id[s_pass.id]["status"] == "validated"


# ── Endpoint smoke + schema shape ───────────────────────────────────────


@pytest.mark.asyncio
async def test_list_endpoint_returns_200_and_status_field():
    from archimedes.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/strategies/?limit=5")
    assert resp.status_code == 200
    data = resp.json()
    assert "strategies" in data and "total" in data
    for s in data["strategies"]:
        assert s["rigor_gate_status"] in (PASS, FAIL, PENDING, DEGENERATE)
        assert isinstance(s["passes_rigor_gate"], bool)


# ── Acceptance #3 (#868): leaderboard numeric fields == live gate values ────
#
# GET /api/strategies/ must serve dsr_p_value / pbo_score / out_of_sample_sharpe /
# deflated_sharpe_ratio computed by the SAME live run_rigor_gate call that backs
# GET /api/selection-bias/gate for the same strategy id — previously only the
# passes_rigor_gate BOOLEAN read the live verdict (#821) while these numeric
# fields still read stale s.<field>/bt.<field> fixture values, so the two
# surfaces could disagree on the numbers for one strategy even when they agreed
# on pass/fail.


@pytest.mark.asyncio
async def test_leaderboard_numeric_fields_equal_live_gate_on_persisted_returns(monkeypatch):
    """ACCEPTANCE #3: for a strategy with real persisted returns, the served
    dsr_p_value/pbo_score/out_of_sample_sharpe/deflated_sharpe_ratio on
    GET /api/strategies/ equal an independently-computed run_rigor_gate result
    over the SAME returns + SAME cohort context the route derives — mirroring
    test_library_badge_equals_live_gate_verdict_on_persisted_returns's
    established pattern for the boolean badge, extended to the numeric fields."""
    from archimedes.api import strategies_routes as sr
    from archimedes.main import app

    strategies = sr.strategy_provider().list_strategies()
    assert len(strategies) >= 2, "need ≥2 curated strategies for a PBO cohort"

    s_pass, s_fail = strategies[0], strategies[1]
    returns = {s_pass.id: _passing_series(0), s_fail.id: _failing_series()}

    monkeypatch.setattr(
        "archimedes.services.backtest_repository.get_all_daily_returns",
        lambda session, ids: dict(returns),
    )
    # verdicts_for_strategies (the boolean path) reads live_rigor_gate's loader;
    # _live_rigor_results_for_strategies (the new numeric path) reads its own
    # local loader — patch BOTH so the two paths see identical code and the
    # look-ahead leg is deterministic across both.
    monkeypatch.setattr(
        "archimedes.services.live_rigor_gate._load_strategy_code_safe",
        lambda strategy: _CLEAN_CODE,
    )
    monkeypatch.setattr(
        "archimedes.api.strategies_routes._load_strategy_code_safe_local",
        lambda strategy: _CLEAN_CODE,
    )

    # Independently reproduce the route's cohort context (same recipe as
    # test_library_badge_equals_live_gate_verdict_on_persisted_returns).
    # num_trials is self-contained (1, decouple #2), not cohort-derived.
    valid = {k: v for k, v in returns.items() if len(v) >= 10}
    pbo_scores = compute_pbo(valid) if len(valid) >= 2 else {}
    num_trials = 1
    avg_corr = compute_average_pairwise_correlation(valid) if len(valid) >= 2 else 0.0

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/strategies/?limit=100")
    assert resp.status_code == 200
    by_id = {s["id"]: s for s in resp.json()["strategies"]}

    for sid, series in returns.items():
        expected = run_rigor_gate(
            strategy_id=sid,
            daily_returns=series,
            num_trials=num_trials,
            pbo_scores=pbo_scores,
            strategy_code=_CLEAN_CODE,
            in_sample_sharpe=None,
            average_correlation=avg_corr,
        )
        served = by_id[sid]
        assert served["dsr_p_value"] == pytest.approx(expected.dsr_p_value, rel=1e-9), (
            f"dsr_p_value for {sid}: served={served['dsr_p_value']} vs live gate={expected.dsr_p_value}"
        )
        assert served["pbo_score"] == pytest.approx(expected.pbo_score, rel=1e-9), (
            f"pbo_score for {sid}: served={served['pbo_score']} vs live gate={expected.pbo_score}"
        )
        assert served["out_of_sample_sharpe"] == pytest.approx(expected.oos_sharpe, rel=1e-9), (
            f"out_of_sample_sharpe for {sid}: served={served['out_of_sample_sharpe']} vs live gate={expected.oos_sharpe}"
        )
        assert served["deflated_sharpe_ratio"] == pytest.approx(expected.deflated_sharpe, rel=1e-9), (
            f"deflated_sharpe_ratio for {sid}: served={served['deflated_sharpe_ratio']} vs "
            f"live gate={expected.deflated_sharpe}"
        )


@pytest.mark.asyncio
async def test_leaderboard_never_disagrees_with_selection_bias_gate_route(monkeypatch):
    """ACCEPTANCE #3 (framed exactly as the issue states it): for a given
    strategy id, GET /api/strategies/ 's numeric rigor fields equal what
    GET /api/selection-bias/gate computes for that SAME id right now — the two
    live surfaces are queried back-to-back against the same injected DB state
    and must not disagree, rather than each being checked against an
    independently-reproduced expectation (belt-and-suspenders vs. the test
    above, which pins the exact machinery instead)."""
    from archimedes.api import strategies_routes as sr
    from archimedes.main import app

    strategies = sr.strategy_provider().list_strategies()
    assert len(strategies) >= 3, "need ≥3 curated strategies for a shared cohort"

    ids = [s.id for s in strategies[:3]]
    returns = {
        ids[0]: _passing_series(2),
        ids[1]: _passing_series(3),
        ids[2]: _failing_series(seed=101),
    }

    monkeypatch.setattr(
        "archimedes.services.backtest_repository.get_all_daily_returns",
        lambda session, ids: dict(returns),
    )
    monkeypatch.setattr(
        "archimedes.services.live_rigor_gate._load_strategy_code_safe",
        lambda strategy: _CLEAN_CODE,
    )
    monkeypatch.setattr(
        "archimedes.api.strategies_routes._load_strategy_code_safe_local",
        lambda strategy: _CLEAN_CODE,
    )
    monkeypatch.setattr(
        "archimedes.api.selection_bias_routes._load_strategy_code",
        lambda path: _CLEAN_CODE,
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        leaderboard_resp = await client.get("/api/strategies/?limit=100")
        gate_resp = await client.get("/api/selection-bias/gate")
    assert leaderboard_resp.status_code == 200
    assert gate_resp.status_code == 200

    leaderboard_by_id = {s["id"]: s for s in leaderboard_resp.json()["strategies"]}
    gate_by_id = {s["strategy_id"]: s for s in gate_resp.json()["strategies"]}

    checked = 0
    for sid in ids:
        lb = leaderboard_by_id.get(sid)
        gate = gate_by_id.get(sid)
        if lb is None or gate is None:
            continue
        # Both routes recompute num_trials/cohort context from the FULL injected
        # returns dict (3 series here), so — for THIS shared fixture — the two
        # independently-computed live gates should agree on every number.
        checked += 1
        assert lb["dsr_p_value"] == pytest.approx(gate["dsr_p_value"], rel=1e-6, abs=1e-9), (
            f"dsr_p_value disagreement for {sid}: leaderboard={lb['dsr_p_value']} vs gate={gate['dsr_p_value']}"
        )
        assert lb["pbo_score"] == pytest.approx(gate["pbo_score"], rel=1e-6, abs=1e-9), (
            f"pbo_score disagreement for {sid}: leaderboard={lb['pbo_score']} vs gate={gate['pbo_score']}"
        )
        assert lb["out_of_sample_sharpe"] == pytest.approx(gate["oos_sharpe"], rel=1e-6, abs=1e-9), (
            f"out_of_sample_sharpe disagreement for {sid}: "
            f"leaderboard={lb['out_of_sample_sharpe']} vs gate={gate['oos_sharpe']}"
        )
        assert lb["deflated_sharpe_ratio"] == pytest.approx(gate["deflated_sharpe"], rel=1e-6, abs=1e-9), (
            f"deflated_sharpe_ratio disagreement for {sid}: "
            f"leaderboard={lb['deflated_sharpe_ratio']} vs gate={gate['deflated_sharpe']}"
        )
    assert checked >= 1, "no shared ids resolved between the two routes — fixture setup is broken"


def test_to_strategy_response_serves_null_not_fixture_without_live_rigor_result():
    """#1187 (claim-integrity): when the live rigor gate could not run
    (``rigor_result is None`` — insufficient/no persisted returns, or a batch
    failure), the four numeric rigor fields must serve ``None`` — the honest
    "not run" — NEVER the strategy's stored ``s.<field>`` values. Those columns
    trace back to a migrated test-fixture snapshot
    (``backend/tests/fixtures/backtest_fixtures_snapshot.json``, PR #863) that
    predates the current DSR convention and gate threshold (#901) and cannot be
    reproduced by any single code version — presenting one as measured next to
    a ``pending`` badge is exactly the defect #1187 tracks.

    Direct unit call (no HTTP, no DB seeding needed): construct a ``Strategy``
    carrying non-None values for all four fields — mirroring exactly what the
    real migrated fixture rows look like — and confirm none of them leak
    through when the live gate result is unavailable. Adversarial check: with
    the pre-#1187 fallback chain restored (``s.<field> if s.<field> is not None
    else (bt.<field> if bt else None)``), this test fails — it would observe
    0.283312 / 0.611531 / 0.373116 / 0.930283 instead of ``None``.
    """
    from archimedes.api.strategies_routes import _to_strategy_response
    from archimedes.models.strategy import Strategy
    from archimedes.services.live_rigor_gate import RigorGateVerdict

    # Values lifted from the real migrated fixture (faber_2007_sma200_timing in
    # backend/tests/fixtures/backtest_fixtures_snapshot.json) so this test fails
    # exactly the way the live #1187 bug did, not against an arbitrary sentinel.
    s = Strategy(
        id="test-1187-fixture-stub",
        deflated_sharpe_ratio=0.283312,
        dsr_p_value=0.611531,
        pbo_score=0.373116,
        out_of_sample_sharpe=0.930283,
    )

    resp = _to_strategy_response(s, verdict=RigorGateVerdict.pending(), rigor_result=None)

    assert resp.rigor_gate_status == "pending"
    assert resp.deflated_sharpe_ratio is None, (
        f"deflated_sharpe_ratio must be None, not the fixture value; got {resp.deflated_sharpe_ratio}"
    )
    assert resp.dsr_p_value is None, f"dsr_p_value must be None, not the fixture value; got {resp.dsr_p_value}"
    assert resp.pbo_score is None, f"pbo_score must be None, not the fixture value; got {resp.pbo_score}"
    assert resp.out_of_sample_sharpe is None, (
        f"out_of_sample_sharpe must be None, not the fixture value; got {resp.out_of_sample_sharpe}"
    )


def test_to_strategy_response_serves_null_not_bt_fixture_without_live_rigor_result(monkeypatch):
    """#1187 adversarial gap (found in re-review): the sibling test above only
    guards the ``s.<field>`` half of the removed fallback chain — ``bt`` is
    always ``None`` there (a bare ``Strategy`` against an empty tmp DB), so a
    partial revert that restored ONLY ``(bt.<field> if bt else None)`` would
    pass every existing guard. This test persists the ``bt`` half: a
    ``BacktestResult`` carrying non-None rigor numbers is what
    ``strategy_provider().get_backtest_result`` returns for this strategy id,
    while ``s.<field>`` stays ``None`` and ``rigor_result`` stays ``None``
    (live gate could not run). Adversarial check: reintroducing
    ``(bt.deflated_sharpe_ratio if bt else None)`` (etc.) on any of the four
    keys makes this fail — it would observe 0.283312 / 0.611531 / 0.373116 /
    0.930283 (the same real fixture values as the sibling test) instead of
    ``None``.
    """
    from archimedes.api.strategies_routes import _to_strategy_response, strategy_provider
    from archimedes.models.backtest import BacktestResult
    from archimedes.models.strategy import Strategy
    from archimedes.services.live_rigor_gate import RigorGateVerdict

    s = Strategy(id="test-1187-bt-stub")  # s.<field> defaults None — bt half only
    bt = BacktestResult(
        strategy_id=s.id,
        sharpe_ratio=1.0,
        sortino_ratio=1.0,
        max_drawdown=0.1,
        cagr=0.1,
        calmar_ratio=1.0,
        win_rate=0.5,
        profit_factor=1.5,
        total_trades=10,
        avg_holding_period_days=5.0,
        correlation_to_spy=0.5,
        correlation_to_btc=0.0,
        deflated_sharpe_ratio=0.283312,
        dsr_p_value=0.611531,
        pbo_score=0.373116,
        out_of_sample_sharpe=0.930283,
    )
    monkeypatch.setattr(strategy_provider(), "get_backtest_result", lambda strategy_id: bt)

    resp = _to_strategy_response(s, verdict=RigorGateVerdict.pending(), rigor_result=None)

    assert resp.rigor_gate_status == "pending"
    assert resp.deflated_sharpe_ratio is None, (
        f"deflated_sharpe_ratio must be None, not the bt fixture value; got {resp.deflated_sharpe_ratio}"
    )
    assert resp.dsr_p_value is None, f"dsr_p_value must be None, not the bt fixture value; got {resp.dsr_p_value}"
    assert resp.pbo_score is None, f"pbo_score must be None, not the bt fixture value; got {resp.pbo_score}"
    assert resp.out_of_sample_sharpe is None, (
        f"out_of_sample_sharpe must be None, not the bt fixture value; got {resp.out_of_sample_sharpe}"
    )


def test_to_strategy_response_surfaces_backtest_provenance(monkeypatch):
    """Left-behind batch close (docs/sprint/a6-rerun.md / sprint README row 5):
    ``backtest_engine`` and ``cost_model_id`` have lived on ``BacktestResultRecord``
    since the cost SSOT / 2026-08-03 provenance audit and were already declared on
    ``StrategyResponse``, but no construction site in strategies_routes.py ever
    populated them from ``bt`` — the values stopped at the DB. Same
    monkeypatch-the-provider pattern as the #1187 sibling tests above: a
    ``BacktestResult`` carrying real engine/cost-model values is what
    ``strategy_provider().get_backtest_result`` returns, and both must reach the
    served response verbatim.

    Adversarial check: with the two ``backtest_engine=``/``cost_model_id=`` kwargs
    removed from ``_to_strategy_response``'s ``StrategyResponse(...)`` call, this
    test fails — it would observe ``None`` for both instead of the real values.
    """
    from archimedes.api.strategies_routes import _to_strategy_response, strategy_provider
    from archimedes.models.backtest import BacktestResult
    from archimedes.models.strategy import Strategy
    from archimedes.services.live_rigor_gate import RigorGateVerdict

    s = Strategy(id="test-provenance-bt-stub")
    bt = BacktestResult(
        strategy_id=s.id,
        sharpe_ratio=1.0,
        sortino_ratio=1.0,
        max_drawdown=0.1,
        cagr=0.1,
        calmar_ratio=1.0,
        win_rate=0.5,
        profit_factor=1.5,
        total_trades=10,
        avg_holding_period_days=5.0,
        correlation_to_spy=0.5,
        correlation_to_btc=0.0,
        backtest_engine="backtrader",
        cost_model_id="cm1:d10:s5",
    )
    monkeypatch.setattr(strategy_provider(), "get_backtest_result", lambda strategy_id: bt)

    resp = _to_strategy_response(s, verdict=RigorGateVerdict.pending(), rigor_result=None)

    assert resp.backtest_engine == "backtrader"
    assert resp.cost_model_id == "cm1:d10:s5"


def test_to_strategy_response_backtest_provenance_is_none_without_persisted_backtest():
    """No BacktestResultRecord row (``bt is None``) must serve None for both
    provenance fields — never a fabricated engine name or cost-model id."""
    from archimedes.api.strategies_routes import _to_strategy_response
    from archimedes.models.strategy import Strategy
    from archimedes.services.live_rigor_gate import RigorGateVerdict

    s = Strategy(id="test-provenance-no-bt")

    resp = _to_strategy_response(s, verdict=RigorGateVerdict.pending(), rigor_result=None)

    assert resp.backtest_engine is None
    assert resp.cost_model_id is None


def test_to_strategy_response_provenance_from_real_persisted_backtest_row():
    """The same claim as the monkeypatched test above, but end-to-end through the
    REAL stack: a row written by ``insert_backtest_if_missing`` into the actual
    ``backtest_results`` table, read back by the real
    ``LocalStrategyProvider.get_backtest_result``.

    The monkeypatched sibling stubs ``get_backtest_result`` to hand back a
    hand-built ``BacktestResult``, so it only proves ``_to_strategy_response``
    copies two attributes off whatever object it is given — it cannot see a break
    anywhere in the chain that actually carries the values:
    ``BacktestResultRecord.from_backtest_result`` (write) →
    ``backtest_results.backtest_engine`` / ``.cost_model_id`` (columns) →
    ``latest_backtests_by_strategy`` (read) →
    ``BacktestResultRecord.to_backtest_result`` (hydrate). Drop either column
    from either mapper and the stubbed test still passes; this one fails.
    """
    from archimedes.api._route_helpers import strategy_provider
    from archimedes.api.strategies_routes import _to_strategy_response
    from archimedes.db import get_session
    from archimedes.models.backtest import BacktestResult
    from archimedes.models.strategy import Strategy
    from archimedes.services.backtest_repository import insert_backtest_if_missing
    from archimedes.services.live_rigor_gate import RigorGateVerdict

    strategy_id = "test-provenance-real-db-row"
    result = BacktestResult(
        strategy_id=strategy_id,
        sharpe_ratio=1.0,
        sortino_ratio=1.0,
        max_drawdown=0.1,
        cagr=0.1,
        calmar_ratio=1.0,
        win_rate=0.5,
        profit_factor=1.5,
        total_trades=10,
        avg_holding_period_days=5.0,
        correlation_to_spy=0.5,
        correlation_to_btc=0.0,
        backtest_engine="vectorbt",
        cost_model_id="cm2:d20:s7",
    )
    with get_session() as session:
        insert_backtest_if_missing(
            session,
            strategy_id=strategy_id,
            content_hash="provenance-real-db-h1",
            result=result,
            source_pipeline="test",
        )
        session.commit()

    # Fresh provider: LocalStrategyProvider memoises ``_backtests`` per instance,
    # and the lru_cached singleton may already have been built (and its cache
    # populated) by an earlier test in this module.
    strategy_provider.cache_clear()

    resp = _to_strategy_response(Strategy(id=strategy_id), verdict=RigorGateVerdict.pending(), rigor_result=None)

    assert resp.backtest_engine == "vectorbt"
    assert resp.cost_model_id == "cm2:d20:s7"


@pytest.mark.asyncio
async def test_leaderboard_serves_null_not_fixture_without_real_returns(monkeypatch):
    """End-to-end companion to the unit test above, over the REAL curated
    library with the REAL migrated fixture data seeded into the DB (#863's
    ``strategy_backtest_fixtures`` table — the exact source the issue names) —
    not a synthetic Strategy. With NO persisted daily returns for anyone,
    ``GET /api/strategies/`` must still serve every strategy's numeric rigor
    fields as ``None`` and its badge as ``pending``, never the seeded fixture
    number. Seeding the fixture table (mirroring ``test_api_routes.py``'s
    ``_use_tmp_db``) is what makes this a real regression guard rather than
    a vacuous one: against pre-#1187 code every strategy here has a non-None
    ``s.deflated_sharpe_ratio`` fixture value available to leak through."""
    import json
    from pathlib import Path

    from archimedes.api._route_helpers import strategy_provider
    from archimedes.db import get_session
    from archimedes.main import app
    from archimedes.models.backtest_fixtures_store import FIXTURE_FIELDS, StrategyBacktestFixture

    snapshot_path = Path(__file__).parent / "fixtures" / "backtest_fixtures_snapshot.json"
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    with get_session() as session:
        for stem, rec in snapshot.items():
            session.merge(StrategyBacktestFixture(stem=stem, **{field: rec[field] for field in FIXTURE_FIELDS}))
        session.commit()
    strategy_provider.cache_clear()

    monkeypatch.setattr(
        "archimedes.services.backtest_repository.get_all_daily_returns",
        lambda session, ids: {},
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/strategies/?limit=100")
    assert resp.status_code == 200
    served = resp.json()["strategies"]
    assert served
    # Sanity: the seeded fixture data actually landed on the in-memory Strategy
    # objects (else this test would vacuously pass regardless of the fix, the
    # exact trap CLAUDE.md's guard-adversarial-pass rule warns about).
    strategies = strategy_provider().list_strategies()
    assert any(s.deflated_sharpe_ratio is not None for s in strategies), (
        "fixture seed did not reach strategy_provider() — this test would pass vacuously regardless of the #1187 fix"
    )
    for s in served:
        assert s["rigor_gate_status"] == "pending", (
            f"{s['id']}: expected pending badge with no persisted returns, got {s['rigor_gate_status']}"
        )
        assert s["deflated_sharpe_ratio"] is None, (
            f"{s['id']}: deflated_sharpe_ratio must be None (not the seeded fixture number) when the "
            f"live gate could not run; got {s['deflated_sharpe_ratio']}"
        )
        assert s["dsr_p_value"] is None, f"{s['id']}: dsr_p_value must be None; got {s['dsr_p_value']}"
        assert s["pbo_score"] is None, f"{s['id']}: pbo_score must be None; got {s['pbo_score']}"
        assert s["out_of_sample_sharpe"] is None, (
            f"{s['id']}: out_of_sample_sharpe must be None; got {s['out_of_sample_sharpe']}"
        )


@pytest.mark.asyncio
async def test_single_strategy_endpoint_numeric_fields_equal_live_gate(monkeypatch):
    """The single-strategy fetch path (GET /api/strategies/{id}) also serves
    live-gate-sourced numeric fields, not just the batch list path — mirrors
    _live_verdict_for_one's existing on-demand-computation precedent
    (verdict is None → compute here), extended to _live_rigor_result_for_one."""
    from archimedes.api import strategies_routes as sr
    from archimedes.main import app

    strategies = sr.strategy_provider().list_strategies()
    assert strategies, "need at least one curated strategy"
    target = strategies[0]
    series = _passing_series(5)

    monkeypatch.setattr(
        "archimedes.services.backtest_repository.get_daily_returns",
        lambda session, sid: series if sid == target.id else [],
    )
    monkeypatch.setattr(
        "archimedes.api.selection_bias_routes._load_strategy_code",
        lambda path: _CLEAN_CODE,
    )

    expected = run_rigor_gate(
        strategy_id=target.id,
        daily_returns=series,
        num_trials=1,
        strategy_code=_CLEAN_CODE,
        in_sample_sharpe=None,
        paper_claimed_sharpe=target.paper_claimed_sharpe,
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/api/strategies/{target.id}")
    assert resp.status_code == 200
    served = resp.json()

    assert served["dsr_p_value"] == pytest.approx(expected.dsr_p_value, rel=1e-9)
    assert served["pbo_score"] == pytest.approx(expected.pbo_score, rel=1e-9)
    assert served["out_of_sample_sharpe"] == pytest.approx(expected.oos_sharpe, rel=1e-9)
    assert served["deflated_sharpe_ratio"] == pytest.approx(expected.deflated_sharpe, rel=1e-9)
    assert served["passes_rigor_gate"] == expected.passes_all
    # #1358 A4: a curated strategy the live gate actually graded carries its
    # provenance — self-contained N=1 (decouple #2), never a bare/absent number.
    assert served["num_trials_in_selection"] == 1
    assert served["num_trials_scope"] == "curated_self_contained"


# ── Cohort invariance under the ?status= filter (#1172 review follow-up) ────
#
# Fixing the PAGINATION dependence alone left a second way for the same badge to
# change with how it was requested: the route graded
# `list_strategies(status=...)`, a SUBSET, while the detail/passport path grades
# `_library_cohort_including()` — which calls `list_strategies()` with NO filter.
# So `?status=candidate` and `?status=validated` could disagree with each other
# and with the passport for one strategy. The cohort must be the full library;
# `status` is a display concern only.


@pytest.mark.asyncio
async def test_list_route_grades_full_library_regardless_of_status_filter(monkeypatch):
    """The graded cohort is byte-identical with and without ?status=, and equals
    the unfiltered library — the same cohort _library_cohort_including() builds."""
    import archimedes.api.strategies_routes as sr
    from archimedes.main import app

    seen_cohorts: list[list[str]] = []

    def _spy(strategies):
        seen_cohorts.append([s.id for s in strategies])
        return {}

    monkeypatch.setattr(sr, "_live_rigor_results_for_strategies", _spy)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r_all = await client.get("/api/strategies/?limit=3")
        r_candidate = await client.get("/api/strategies/?limit=3&status=candidate")
        r_validated = await client.get("/api/strategies/?limit=3&status=validated")

    assert r_all.status_code == 200
    assert r_candidate.status_code == 200
    assert r_validated.status_code == 200

    full_library = [s.id for s in sr.strategy_provider().list_strategies()]
    assert len(seen_cohorts) == 3, "each request must grade exactly one cohort"
    for cohort in seen_cohorts:
        assert cohort == full_library

    # And the filter still actually filters the RESPONSE — the fix must not have
    # turned ?status= into a no-op.
    assert r_candidate.json()["total"] <= r_all.json()["total"]


@pytest.mark.asyncio
async def test_list_route_grades_full_library_regardless_of_pagination(monkeypatch):
    """Companion to the above: the cohort is also independent of offset/limit,
    which is the original #1173 defect. Pinned so neither can regress alone."""
    import archimedes.api.strategies_routes as sr
    from archimedes.main import app

    seen_cohorts: list[list[str]] = []
    monkeypatch.setattr(
        sr,
        "_live_rigor_results_for_strategies",
        lambda strategies: (seen_cohorts.append([s.id for s in strategies]), {})[1],
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.get("/api/strategies/?limit=2&offset=0")
        await client.get("/api/strategies/?limit=2&offset=4")
        await client.get("/api/strategies/?limit=100&offset=0")

    full_library = [s.id for s in sr.strategy_provider().list_strategies()]
    assert len(seen_cohorts) == 3
    for cohort in seen_cohorts:
        assert cohort == full_library


# ── GET /api/strategies/{id} must not 500 for a strategy with no linked
# paper (#1342) ──────────────────────────────────────────────────────────
#
# Curated strategies always have papers[0], so the legacy scalar fields
# (paper_arxiv_id / paper_title) never went through the None branch for
# them. GENERATED strategies (fusion/architect, ingested straight into
# strategy_passports with no paper_refs) do: _passport_to_strategy_response
# passes ``first.arxiv_id if first else None`` into a field declared
# ``str = ""``, which pydantic 2.x rejects — turning a genuine "no paper"
# into an HTTP 500 instead of a null field.


@pytest.mark.asyncio
async def test_single_strategy_endpoint_200s_with_no_linked_paper():
    """A generated (passport) strategy with zero linked papers must serve
    200 with paper_arxiv_id/paper_title == None, not 500."""
    from archimedes.db import get_session
    from archimedes.main import app
    from archimedes.models.strategy import StrategyPassport
    from archimedes.services.passport_loader import ingest_passport

    strategy_id = "test-1342-no-linked-paper"
    passport = StrategyPassport(
        id=strategy_id,
        papers=[],
        methodology_summary="A strategy with no linked paper (e.g. a fresh generation).",
        asset_universe=["SPY"],
    )
    with get_session() as session:
        ingest_passport(session, passport, generation_method="fusion")
        session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/api/strategies/{strategy_id}")

    assert resp.status_code == 200, resp.text
    served = resp.json()
    assert served["paper_arxiv_id"] is None
    assert served["paper_title"] is None
    assert served["papers"] == []
