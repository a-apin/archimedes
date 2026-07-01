"""Tests for selection_bias_routes — HTTP endpoints and schema validation.

Covers:
  - GET /api/selection-bias/gate  (library-level rigor gate)
  - GET /api/selection-bias/gate/{strategy_id}  (single-strategy gate)
  - POST /api/selection-bias/pbo  (pure-math PBO computation)
  - Pydantic schema construction and defaults
  - Pure helper functions (_load_strategy_code)

The GET /gate and GET /gate/{id} endpoints call init_db() internally; an
autouse fixture redirects DATABASE_URL to a per-test temporary SQLite file
so no persistent on-disk state is created and no external DB is required.
Tests pass with:
  env -i HOME=$HOME PATH=$PATH PYTHONPATH=backend python -m pytest \\
      backend/tests/test_selection_bias_routes.py -q
"""

from __future__ import annotations

import numpy as np
import pytest
from archimedes.api.selection_bias_routes import (
    LibraryPbo,
    PBORequest,
    PBOResponse,
    RigorGateDetail,
    RigorGateResponse,
    StrategyRigorResult,
    _load_strategy_code,
)
from archimedes.services.rigor_evaluator import run_rigor_gate
from httpx import ASGITransport, AsyncClient

# ── Hermetic DB fixture ────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _use_tmp_db(tmp_path, monkeypatch):
    """Redirect DATABASE_URL to a per-test temp SQLite file.

    The evaluate_rigor_gate endpoint calls init_db() before querying
    persisted backtest data.  Without this fixture, init_db() falls back
    to sqlite:///./archimedes_chat.db (CWD-relative), which accumulates
    state across test runs and across parallel workers.  The fixture
    isolates every test and satisfies the hermetic-test mandate.
    """
    from archimedes.db import init_db

    db_path = tmp_path / "test_selection_bias.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    init_db()
    yield


# ── CPCV honest not-run reporting (#771) ───────────────────────────────


class TestCpcvHonestNotRun:
    """#771: the selection-bias surface must not advertise CPCV as a method while
    emitting a bare "MISSING". The live route calls run_rigor_gate WITHOUT a
    cv_returns_matrix (the analytics-engine doesn't emit one yet), so CPCV must be
    reported as an explicit NOT_RUN status with its reason — never a bare placeholder
    and never a fabricated value.
    """

    @staticmethod
    def _series():
        rng = np.random.default_rng(7)
        return list(rng.normal(0.001, 0.01, 400))

    def test_cpcv_reports_explicit_not_run_label(self):
        # Exactly how selection_bias_routes.evaluate_rigor_gate calls it: no matrix.
        result = run_rigor_gate("s1", self._series(), num_trials=6)
        cpcv = result.gate_details["cpcv"]
        assert cpcv.startswith("NOT_RUN"), cpcv
        assert "combinatorial" in cpcv.lower(), "the not-run reason must be surfaced"
        assert cpcv != "MISSING", "no bare placeholder that implies a silent method"

    def test_cpcv_label_is_not_a_fabricated_value(self):
        # Anti-goal: the not-run label must not look like a computed CPCV verdict.
        cpcv = run_rigor_gate("s1", self._series(), num_trials=6).gate_details["cpcv"]
        assert not cpcv.startswith(("PASS", "FAIL")), "must not mimic a computed verdict"

    def test_cpcv_not_run_stays_non_gating(self):
        # CPCV is not enforced when absent: no positive_fraction is computed, so the
        # NOT_RUN status cannot, by itself, flip pass/fail for the other criteria.
        result = run_rigor_gate("s1", self._series(), num_trials=6)
        assert result.cpcv_positive_fraction is None
        assert isinstance(result.passes_all, bool)


# ── Schema unit tests ──────────────────────────────────────────────────


class TestRigorGateDetail:
    def test_default_values_are_missing(self):
        detail = RigorGateDetail()
        assert detail.dsr == "MISSING"
        assert detail.pbo == "MISSING"
        assert detail.oos_sharpe == "MISSING"
        assert detail.look_ahead == "MISSING"

    def test_custom_values_stored(self):
        detail = RigorGateDetail(
            dsr="PASS (p=0.98)",
            pbo="FAIL (PBO=0.6)",
            oos_sharpe="SET (OOS=1.2)",
            look_ahead="PASS",
        )
        assert detail.dsr == "PASS (p=0.98)"
        assert detail.pbo == "FAIL (PBO=0.6)"
        assert detail.oos_sharpe == "SET (OOS=1.2)"
        assert detail.look_ahead == "PASS"


class TestStrategyRigorResult:
    def test_required_fields_present(self):
        result = StrategyRigorResult(
            strategy_id="abc123",
            strategy_name="Test Strategy",
            passes_all=False,
            gate_details=RigorGateDetail(),
        )
        assert result.strategy_id == "abc123"
        assert result.strategy_name == "Test Strategy"
        assert result.passes_all is False

    def test_optional_fields_default_none(self):
        result = StrategyRigorResult(
            strategy_id="abc123",
            strategy_name="Test Strategy",
            passes_all=True,
            gate_details=RigorGateDetail(),
        )
        assert result.deflated_sharpe is None
        assert result.dsr_p_value is None
        assert result.pbo_score is None
        assert result.oos_sharpe is None
        assert result.in_sample_sharpe is None

    def test_optional_fields_accept_floats(self):
        result = StrategyRigorResult(
            strategy_id="abc123",
            strategy_name="Test Strategy",
            passes_all=False,
            gate_details=RigorGateDetail(),
            deflated_sharpe=1.23,
            dsr_p_value=0.97,
            pbo_score=0.2,
            oos_sharpe=0.8,
            in_sample_sharpe=1.5,
        )
        assert result.deflated_sharpe == pytest.approx(1.23)
        assert result.dsr_p_value == pytest.approx(0.97)
        assert result.pbo_score == pytest.approx(0.2)


class TestRigorGateResponse:
    def test_empty_library_response(self):
        resp = RigorGateResponse(strategies=[], total=0, passing=0, failing=0)
        assert resp.total == 0
        assert resp.passing == 0
        assert resp.failing == 0
        assert resp.strategies == []

    def test_counts_consistency(self):
        detail = RigorGateDetail()
        strats = [
            StrategyRigorResult(strategy_id=f"s{i}", strategy_name=f"S{i}", passes_all=(i == 0), gate_details=detail)
            for i in range(3)
        ]
        resp = RigorGateResponse(strategies=strats, total=3, passing=1, failing=2)
        assert resp.total == 3
        assert resp.passing == 1
        assert resp.failing == 2


class TestPBORequest:
    def test_default_s_partitions(self):
        req = PBORequest(returns_matrix={"s1": [0.001] * 50})
        assert req.s_partitions == 16

    def test_custom_s_partitions(self):
        req = PBORequest(returns_matrix={"s1": [0.001] * 50}, s_partitions=4)
        assert req.s_partitions == 4

    def test_matrix_stored(self):
        returns = [0.001, -0.002, 0.003]
        req = PBORequest(returns_matrix={"s1": returns})
        assert req.returns_matrix["s1"] == returns


class TestPBOResponse:
    def test_fields_stored(self):
        resp = PBOResponse(pbo_scores={"s1": 0.3, "s2": 0.3}, interpretation="PASSED rigor gate.")
        assert resp.pbo_scores["s1"] == pytest.approx(0.3)
        assert "PASSED" in resp.interpretation


# ── Pure helper function tests ──────────────────────────────────────────


class TestLoadStrategyCode:
    def test_nonexistent_path_returns_none(self):
        result = _load_strategy_code("/nonexistent/path/strategy.py")
        assert result is None

    def test_empty_string_returns_none(self):
        result = _load_strategy_code("")
        assert result is None

    def test_none_returns_none(self):
        result = _load_strategy_code(None)
        assert result is None

    def test_path_traversal_blocked(self):
        result = _load_strategy_code("../../../../etc/passwd")
        assert result is None


# ── HTTP endpoint tests ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_gate_endpoint_returns_200():
    """GET /api/selection-bias/gate returns 200."""
    from archimedes.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/selection-bias/gate")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_gate_endpoint_has_required_top_level_keys():
    """Response body contains strategies, total, passing, failing."""
    from archimedes.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/selection-bias/gate")
    data = resp.json()
    for key in ("strategies", "total", "passing", "failing"):
        assert key in data, f"missing key '{key}' in response: {list(data.keys())}"


@pytest.mark.asyncio
async def test_gate_endpoint_counts_are_consistent():
    """total == passing + failing and strategies list length matches total."""
    from archimedes.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/selection-bias/gate")
    data = resp.json()
    assert data["total"] == data["passing"] + data["failing"]
    assert len(data["strategies"]) == data["total"]


@pytest.mark.asyncio
async def test_gate_endpoint_counts_non_negative():
    """total, passing, failing are all non-negative integers."""
    from archimedes.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/selection-bias/gate")
    data = resp.json()
    assert data["total"] >= 0
    assert data["passing"] >= 0
    assert data["failing"] >= 0


@pytest.mark.asyncio
async def test_gate_endpoint_strategy_types():
    """strategy_id and strategy_name are strings; passes_all is bool."""
    from archimedes.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/selection-bias/gate")
    data = resp.json()
    for strat in data["strategies"]:
        assert isinstance(strat["strategy_id"], str)
        assert isinstance(strat["strategy_name"], str)
        assert isinstance(strat["passes_all"], bool)


@pytest.mark.asyncio
async def test_gate_single_strategy_404_for_nonexistent():
    """GET /api/selection-bias/gate/{strategy_id} with unknown ID returns 404."""
    from archimedes.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/selection-bias/gate/nonexistent-id-that-doesnt-exist-xyz")
    assert resp.status_code == 404
    assert "detail" in resp.json()


@pytest.mark.asyncio
async def test_pbo_endpoint_two_strategies_returns_200():
    """POST /api/selection-bias/pbo with valid two-strategy matrix returns 200."""
    from archimedes.main import app

    payload = {"returns_matrix": {"s1": [0.001] * 100, "s2": [-0.0005] * 100}}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/api/selection-bias/pbo", json=payload)
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_pbo_endpoint_has_required_keys():
    """POST /api/selection-bias/pbo response has pbo_scores and interpretation."""
    from archimedes.main import app

    payload = {"returns_matrix": {"s1": [0.001] * 100, "s2": [-0.0005] * 100}}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/api/selection-bias/pbo", json=payload)
    data = resp.json()
    assert "pbo_scores" in data, f"missing 'pbo_scores' in {list(data.keys())}"
    assert "interpretation" in data, f"missing 'interpretation' in {list(data.keys())}"


@pytest.mark.asyncio
async def test_pbo_endpoint_scores_keyed_by_strategy_id():
    """pbo_scores dict contains entries for each submitted strategy."""
    from archimedes.main import app

    payload = {"returns_matrix": {"alpha": [0.001] * 100, "beta": [-0.0005] * 100}}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/api/selection-bias/pbo", json=payload)
    scores = resp.json()["pbo_scores"]
    assert "alpha" in scores
    assert "beta" in scores


@pytest.mark.asyncio
async def test_pbo_endpoint_scores_are_floats():
    """pbo_scores values are numeric and within [0, 1]."""
    from archimedes.main import app

    payload = {"returns_matrix": {"s1": [0.001] * 100, "s2": [-0.001] * 100}}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/api/selection-bias/pbo", json=payload)
    scores = resp.json()["pbo_scores"]
    for sid, score in scores.items():
        assert isinstance(score, (int, float)), f"score for {sid} is not numeric: {score}"
        assert 0.0 <= score <= 1.0, f"score for {sid} out of [0,1]: {score}"


@pytest.mark.asyncio
async def test_pbo_endpoint_interpretation_string():
    """interpretation field is a non-empty string."""
    from archimedes.main import app

    payload = {"returns_matrix": {"s1": [0.001] * 100, "s2": [-0.001] * 100}}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/api/selection-bias/pbo", json=payload)
    interpretation = resp.json()["interpretation"]
    assert isinstance(interpretation, str)
    assert len(interpretation) > 0


@pytest.mark.asyncio
async def test_pbo_endpoint_high_pbo_interpretation_says_failed():
    """PBO=1.0 is deterministic for s1-positive vs s2-negative constant returns.

    Constant series cause std=0 in every CSCV block, so _sharpe_per_col returns
    0.0 for both strategies via the safe_sigma=inf guard.  argsort([0,0]) is
    stable, assigning s1 (index 0) rank 1 (worst) out of 2.  omega=0.5/2=0.5,
    lambda=log(0.5/0.5)=0.0, which satisfies lam<=0, so every split votes for
    overfitting and PBO=1.0.
    """
    from archimedes.main import app

    payload = {"returns_matrix": {"s1": [0.001] * 100, "s2": [-0.001] * 100}}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/api/selection-bias/pbo", json=payload)
    data = resp.json()
    score = next(iter(data["pbo_scores"].values()))
    assert score == pytest.approx(1.0), f"expected PBO=1.0 for this payload, got {score}"
    assert "FAILED" in data["interpretation"]


@pytest.mark.asyncio
async def test_pbo_endpoint_low_pbo_interpretation_says_passed():
    """PBO=0.0 is deterministic when one strategy clearly dominates.

    With rng(42), 'good' (mean=+0.002, vol=0.003) dominates 'poor'
    (mean=-0.002, vol=0.01) on every IS/OOS split, so compute_pbo returns 0.0.
    """
    from archimedes.main import app

    rng = np.random.default_rng(42)
    good = rng.normal(0.002, 0.003, 300).tolist()
    poor = rng.normal(-0.002, 0.01, 300).tolist()
    payload = {"returns_matrix": {"good": good, "poor": poor}}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/api/selection-bias/pbo", json=payload)
    data = resp.json()
    score = next(iter(data["pbo_scores"].values()))
    assert score == pytest.approx(0.0), f"expected PBO=0.0 for dominant strategy, got {score}"
    assert "PASSED" in data["interpretation"]


@pytest.mark.asyncio
async def test_pbo_endpoint_single_strategy_returns_zero():
    """Single strategy -> compute_pbo returns 0.0 (no comparison possible)."""
    from archimedes.main import app

    payload = {"returns_matrix": {"s1": [0.001] * 100}}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/api/selection-bias/pbo", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert "pbo_scores" in data
    assert data["pbo_scores"].get("s1") == pytest.approx(0.0)


@pytest.mark.asyncio
async def test_pbo_endpoint_short_series_edge_case():
    """Very short series (10 bars) with s_partitions=4 returns a valid response."""
    from archimedes.main import app

    payload = {
        "returns_matrix": {"s1": [0.001] * 10, "s2": [-0.001] * 10},
        "s_partitions": 4,
    }
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/api/selection-bias/pbo", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert "pbo_scores" in data
    assert "interpretation" in data


@pytest.mark.asyncio
async def test_pbo_endpoint_three_strategies():
    """Three strategies are all present in pbo_scores output."""
    from archimedes.main import app

    payload = {
        "returns_matrix": {
            "strategy_a": [0.001] * 80,
            "strategy_b": [-0.0005] * 80,
            "strategy_c": [0.0002] * 80,
        }
    }
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/api/selection-bias/pbo", json=payload)
    assert resp.status_code == 200
    scores = resp.json()["pbo_scores"]
    assert set(scores.keys()) == {"strategy_a", "strategy_b", "strategy_c"}


@pytest.mark.asyncio
async def test_pbo_endpoint_custom_s_partitions():
    """s_partitions parameter is accepted and response remains valid."""
    from archimedes.main import app

    payload = {
        "returns_matrix": {"s1": [0.001] * 200, "s2": [-0.001] * 200},
        "s_partitions": 8,
    }
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/api/selection-bias/pbo", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert "pbo_scores" in data
    assert isinstance(data["interpretation"], str)


@pytest.mark.asyncio
async def test_gate_endpoint_empty_provider(monkeypatch):
    """When provider returns no strategies, gate returns empty-list response."""
    from archimedes.api import selection_bias_routes
    from archimedes.main import app

    monkeypatch.setattr(selection_bias_routes._provider, "list_strategies", list)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/selection-bias/gate")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 0
    assert data["passing"] == 0
    assert data["failing"] == 0
    assert data["strategies"] == []


@pytest.mark.asyncio
async def test_gate_endpoint_empty_provider_404_for_strategy(monkeypatch):
    """When provider returns no strategies, any strategy_id returns 404."""
    from archimedes.api import selection_bias_routes
    from archimedes.main import app

    monkeypatch.setattr(selection_bias_routes._provider, "get_strategy", lambda sid: None)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/selection-bias/gate/any-id-at-all")
    assert resp.status_code == 404


# ── OOS/IS cliff denominator (audit finding #7) ─────────────────────────


class TestOosCliffDenominator:
    """The /gate route must pass in_sample_sharpe=None so run_rigor_gate derives
    the IS denominator from the first 70% of the series — NOT the full-sample
    backtest Sharpe (the previous `bt_map[s.id].sharpe_ratio` override), which
    blends IS+OOS and makes the OOS/IS cliff trivially passable.

    These deterministic series (no RNG → version-independent) demonstrate the
    denominator choice flipping the verdict on a strong-IS / weak-positive-OOS
    strategy: the honest first-70% denominator fails the cliff; the inflated
    full-sample denominator passes it.
    """

    @staticmethod
    def _strong_is_weak_oos() -> list[float]:
        # IS (first 700 bars): drift 0.003 ± 0.01 → high Sharpe.
        # OOS (last 300 bars): drift 0.0015 ± 0.01 → ~half the IS edge.
        amp = 0.01
        is_part = [0.003 + (amp if i % 2 == 0 else -amp) for i in range(700)]
        oos_part = [0.0015 + (amp if i % 2 == 0 else -amp) for i in range(300)]
        return is_part + oos_part

    def test_first70_denominator_fails_cliff(self):
        from archimedes.services.rigor_evaluator import run_rigor_gate

        series = self._strong_is_weak_oos()
        gate = run_rigor_gate(
            "s",
            series,
            num_trials=1,
            pbo_scores=None,
            strategy_code=None,
            in_sample_sharpe=None,
            average_correlation=0.0,
        )
        assert "FAIL" in gate.gate_details["oos_sharpe"]

    def test_fullsample_denominator_would_inflate_and_pass(self):
        import math

        from archimedes.services.rigor_evaluator import run_rigor_gate

        series = self._strong_is_weak_oos()
        arr = np.asarray(series, dtype=float)
        full_sample_sharpe = (arr.mean() / arr.std(ddof=1)) * math.sqrt(252)
        gate = run_rigor_gate(
            "s",
            series,
            num_trials=1,
            pbo_scores=None,
            strategy_code=None,
            in_sample_sharpe=full_sample_sharpe,
            average_correlation=0.0,
        )
        # Same series, only the denominator differs → the bug let it pass.
        assert "PASS" in gate.gate_details["oos_sharpe"]


# ── Library PBO: schema + cached helper (#546, display-only) ────────────


class TestLibraryPboSchema:
    def test_defaults_are_unavailable_shape(self):
        lp = LibraryPbo()
        assert lp.value is None
        assert lp.data_vintage is None
        assert lp.selection_set_size == 0
        assert lp.source == "library_cscv"

    def test_custom_values_stored(self):
        lp = LibraryPbo(value=0.31, data_vintage="2026-06-11", selection_set_size=22, source="library_cscv")
        assert lp.value == pytest.approx(0.31)
        assert lp.data_vintage == "2026-06-11"
        assert lp.selection_set_size == 22

    def test_present_on_response_models_by_default(self):
        resp = RigorGateResponse(strategies=[], total=0, passing=0, failing=0)
        assert isinstance(resp.library_pbo, LibraryPbo)
        result = StrategyRigorResult(
            strategy_id="x",
            strategy_name="X",
            passes_all=False,
            gate_details=RigorGateDetail(),
        )
        assert isinstance(result.library_pbo, LibraryPbo)


class TestCachedLibraryPbo:
    """The module-level cached helper computes a value for a valid tmp store and
    fails closed gracefully for an absent one — without re-running CSCV per call."""

    def _write_store(self, directory, n_series: int = 4, n_obs: int = 256, vintage: str = "2026-06-11"):
        import json

        import numpy as np

        rng = np.random.default_rng(7)
        dates = [f"2020-{1 + i // 28:02d}-{1 + i % 28:02d}" for i in range(n_obs)]
        for k in range(n_series):
            rec = {
                "stem": f"strat_{k}",
                "data_vintage": vintage,
                "n_obs": n_obs,
                "dates": dates,
                "daily_returns": rng.normal(0.0005, 0.01, n_obs).tolist(),
            }
            (directory / f"strat_{k}.json").write_text(json.dumps(rec), encoding="utf-8")

    def test_returns_value_for_valid_store(self, tmp_path, monkeypatch):
        from archimedes.api import selection_bias_routes as routes

        self._write_store(tmp_path)
        # Point the resolver at the tmp store and clear any cached value.
        monkeypatch.setattr(routes, "_LIBRARY_PBO_CACHE", {})
        monkeypatch.setattr(
            "archimedes.services.rigor_evaluator._resolve_daily_returns_store_dir",
            lambda: tmp_path,
        )
        value, vintage, size = routes._cached_library_pbo()
        assert value is not None
        assert 0.0 <= value <= 1.0
        assert vintage == "2026-06-11"
        assert size == 4

    def test_absent_store_fails_closed(self, monkeypatch):
        from archimedes.api import selection_bias_routes as routes

        monkeypatch.setattr(routes, "_LIBRARY_PBO_CACHE", {})
        monkeypatch.setattr(
            "archimedes.services.rigor_evaluator._resolve_daily_returns_store_dir",
            lambda: None,
        )
        value, vintage, size = routes._cached_library_pbo()
        assert value is None
        assert vintage is None
        assert size == 0

    def test_payload_unavailable_when_value_none(self, monkeypatch):
        from archimedes.api import selection_bias_routes as routes

        monkeypatch.setattr(routes, "_cached_library_pbo", lambda: (None, None, 0))
        payload = routes._library_pbo_payload()
        assert payload.value is None
        assert payload.source == "unavailable"

    def test_cache_avoids_recompute_on_unchanged_store(self, tmp_path, monkeypatch):
        """Second call with an unchanged store does NOT re-run compute_library_pbo."""
        from archimedes.api import selection_bias_routes as routes

        self._write_store(tmp_path)
        monkeypatch.setattr(routes, "_LIBRARY_PBO_CACHE", {})
        monkeypatch.setattr(
            "archimedes.services.rigor_evaluator._resolve_daily_returns_store_dir",
            lambda: tmp_path,
        )

        calls = {"n": 0}
        real = routes.compute_library_pbo

        def _counting(*a, **k):
            calls["n"] += 1
            return real(*a, **k)

        monkeypatch.setattr(routes, "compute_library_pbo", _counting)

        routes._cached_library_pbo()
        routes._cached_library_pbo()
        assert calls["n"] == 1  # cached on the unchanged file signature


# ── Library PBO: endpoint wiring + additivity guarantee (#546) ──────────


@pytest.mark.asyncio
async def test_gate_response_includes_library_pbo_object():
    """GET /api/selection-bias/gate carries a library_pbo object on the response
    AND on every per-strategy result (selection-set property)."""
    from archimedes.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/selection-bias/gate")
    data = resp.json()
    assert "library_pbo" in data
    lp = data["library_pbo"]
    for key in ("value", "data_vintage", "selection_set_size", "source"):
        assert key in lp, f"missing '{key}' in library_pbo: {list(lp.keys())}"
    for strat in data["strategies"]:
        assert "library_pbo" in strat


@pytest.mark.asyncio
async def test_single_strategy_result_includes_library_pbo():
    """GET /gate/{id} renders the selection-set library_pbo on the passport result."""
    from archimedes.api import selection_bias_routes
    from archimedes.main import app

    # Pick a real strategy id from the provider so the route resolves it.
    strategies = selection_bias_routes._provider.list_strategies()
    if not strategies:
        pytest.skip("no strategies in provider")
    sid = strategies[0].id

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/api/selection-bias/gate/{sid}")
    assert resp.status_code == 200
    assert "library_pbo" in resp.json()


@pytest.mark.asyncio
async def test_library_pbo_does_not_change_verdict_or_pbo_score(monkeypatch):
    """ADDITIVITY GUARANTEE: injecting vs removing the library PBO must NOT change
    any strategy's passes_all or pbo_score. We run the gate twice — once with the
    library PBO forced to a concrete value, once forced unavailable — and assert
    the per-strategy verdict + cohort pbo_score are byte-for-byte identical."""
    from archimedes.api import selection_bias_routes as routes
    from archimedes.main import app

    async def _run_once():
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            return (await client.get("/api/selection-bias/gate")).json()

    # Run A: library PBO present with a concrete value.
    monkeypatch.setattr(
        routes,
        "_library_pbo_payload",
        lambda: routes.LibraryPbo(value=0.42, data_vintage="2026-06-11", selection_set_size=22),
    )
    data_with = await _run_once()

    # Run B: library PBO unavailable (None).
    monkeypatch.setattr(
        routes,
        "_library_pbo_payload",
        lambda: routes.LibraryPbo(value=None, source="unavailable"),
    )
    data_without = await _run_once()

    by_id_with = {s["strategy_id"]: s for s in data_with["strategies"]}
    by_id_without = {s["strategy_id"]: s for s in data_without["strategies"]}
    assert by_id_with.keys() == by_id_without.keys()
    for sid, a in by_id_with.items():
        b = by_id_without[sid]
        assert a["passes_all"] == b["passes_all"], f"passes_all changed for {sid}"
        assert a["pbo_score"] == b["pbo_score"], f"pbo_score changed for {sid}"
    # Sanity: the only thing that differs is the additive library_pbo field.
    assert data_with["passing"] == data_without["passing"]
    assert data_with["library_pbo"]["value"] == pytest.approx(0.42)
    assert data_without["library_pbo"]["value"] is None


@pytest.mark.asyncio
async def test_run_rigor_gate_called_without_library_pbo_kwarg(monkeypatch):
    """The gate verdict path must be provably unchanged: run_rigor_gate is called
    WITHOUT a library_pbo= argument (passing it would alter criterion 4 = option 3,
    which is out of scope). Patch run_rigor_gate, force the DB to yield a usable
    return series so the full branch executes, and assert library_pbo is absent
    from every call's kwargs."""
    from archimedes.api import selection_bias_routes as routes
    from archimedes.main import app
    from archimedes.services.rigor_evaluator import run_rigor_gate as real_run_rigor_gate

    strategies = routes._provider.list_strategies()
    if not strategies:
        pytest.skip("no strategies in provider")

    # Force the DB read to return a usable series for at least one strategy so the
    # full (non-MISSING) branch — the one that calls run_rigor_gate — executes.
    import numpy as np

    rng = np.random.default_rng(3)
    series = rng.normal(0.001, 0.01, 400).tolist()
    target_id = strategies[0].id
    # The route imports get_all_daily_returns inside the function body
    # (`from archimedes.services.backtest_repository import get_all_daily_returns`),
    # so the effective patch point is the definition module, not the route module.
    monkeypatch.setattr(
        "archimedes.services.backtest_repository.get_all_daily_returns",
        lambda session, ids: {target_id: series},
    )

    captured_kwargs = []

    def _spy(*args, **kwargs):
        captured_kwargs.append(kwargs)
        return real_run_rigor_gate(*args, **kwargs)

    monkeypatch.setattr(routes, "run_rigor_gate", _spy)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/selection-bias/gate")
    assert resp.status_code == 200
    assert captured_kwargs, "run_rigor_gate was never called — the full branch did not execute"
    for kwargs in captured_kwargs:
        assert "library_pbo" not in kwargs, "run_rigor_gate must NOT receive library_pbo (option-3 guard)"
