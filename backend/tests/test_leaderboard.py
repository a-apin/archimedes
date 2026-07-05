"""Hermetic tests for the leaderboard scoring engine (services/leaderboard.py).

No DB, no network — build ``StrategyResponse`` objects directly and assert the
ranking, the transparent score, None-handling, filters, and the honest
forward-axis/pending behaviour. The leaderboard's #1 rule is "never fabricate a
number", so the tests pin: placeholders sink (score 0), missing sort values land
last, and the scoring-engine metadata is always intact.
"""

from __future__ import annotations

from archimedes.api.schemas import StrategyResponse
from archimedes.services.leaderboard import (
    OOS_TARGET,
    WEIGHTS,
    build_leaderboard,
    compute_conviction,
)


def _strat(
    sid: str,
    *,
    passes_gate: bool = False,
    dsr_p: float | None = None,
    oos: float | None = None,
    pbo: float | None = None,
    sharpe: float | None = None,
    placeholder: bool = False,
    regime: str = "regime_neutral",
    status: str = "validated",
    title: str = "",
    curator: str | None = None,
) -> StrategyResponse:
    return StrategyResponse(
        id=sid,
        methodology_summary=f"methodology for {sid}",
        asset_universe=["SPY", "GLD"],
        position_sizing="equal_weight",
        rebalance_frequency="monthly",
        status=status,
        paper_title=title,
        passes_rigor_gate=passes_gate,
        dsr_p_value=dsr_p,
        out_of_sample_sharpe=oos,
        pbo_score=pbo,
        sharpe_ratio=sharpe,
        is_backtest_placeholder=placeholder,
        regime_tag=regime,
        curator_wallet=curator,
    )


# ── compute_conviction ────────────────────────────────────────────────────


def test_weights_sum_to_one():
    assert abs(sum(WEIGHTS.values()) - 1.0) < 1e-9


def test_fully_validated_strategy_scores_high():
    s = _strat("A", passes_gate=True, dsr_p=0.99, oos=1.2, pbo=0.1)
    score, comp = compute_conviction(s)
    # 100*(0.35*1 + 0.25*0.99 + 0.25*1.0(clamped) + 0.15*0.9) = 98.25
    assert score == 98.2 or score == 98.3 or abs(score - 98.25) < 0.2
    assert comp.gate == 1.0
    assert comp.oos_performance == 1.0  # 1.2/1.0 clamps to 1.0
    assert comp.overfitting_resistance == 0.9
    assert comp.data_completeness == 1.0


def test_placeholder_scores_zero_and_incomplete():
    s = _strat("P", placeholder=True)  # all rigor None, gate not real
    score, comp = compute_conviction(s)
    assert score == 0.0
    assert comp.data_completeness == 0.0
    assert comp.gate == 0.0


def test_gate_not_counted_when_placeholder():
    # passes_rigor_gate True but placeholder → gate must NOT credit (no real backtest).
    s = _strat("X", passes_gate=True, placeholder=True)
    score, comp = compute_conviction(s)
    assert comp.gate == 0.0
    assert score == 0.0


def test_placeholder_with_borrowed_rigor_still_sinks():
    # Regression: a placeholder that ALSO carries DSR/OOS/PBO values (e.g. seeded
    # numbers on a placeholder record) must NOT ride them to a non-zero score and
    # outrank a real-but-partially-missing strategy. Every component zeros.
    ph = _strat("ph", passes_gate=True, dsr_p=0.99, oos=1.5, pbo=0.05, placeholder=True)
    score, comp = compute_conviction(ph)
    assert score == 0.0
    assert comp.gate == 0.0
    assert comp.dsr_confidence == 0.0
    assert comp.oos_performance == 0.0
    assert comp.overfitting_resistance == 0.0
    assert comp.data_completeness == 0.0

    # ... and it sorts strictly below a real strategy with only partial data.
    real_partial = _strat("real", passes_gate=True, dsr_p=0.6)  # gate + dsr only
    real_score, _ = compute_conviction(real_partial)
    assert real_score > score


def test_negative_oos_clamps_to_zero():
    s = _strat("N", passes_gate=True, oos=-0.5)
    _, comp = compute_conviction(s)
    assert comp.oos_performance == 0.0


def test_oos_target_constant_is_one():
    assert OOS_TARGET == 1.0


# ── build_leaderboard: ranking + medals ───────────────────────────────────


def test_ranks_by_conviction_desc_with_medals():
    weak = _strat("weak", passes_gate=False, dsr_p=0.2, oos=0.1, pbo=0.8)
    strong = _strat("strong", passes_gate=True, dsr_p=0.99, oos=1.5, pbo=0.05)
    mid = _strat("mid", passes_gate=True, dsr_p=0.7, oos=0.6, pbo=0.4)
    board = build_leaderboard([weak, strong, mid])
    ids = [e.id for e in board.entries]
    assert ids == ["strong", "mid", "weak"]
    assert board.entries[0].rank == 1 and board.entries[0].medal == "gold"
    assert board.entries[1].medal == "silver"
    assert board.entries[2].medal == "bronze"


def test_placeholders_sink_to_bottom():
    real = _strat("real", passes_gate=True, dsr_p=0.9, oos=1.0, pbo=0.1)
    ph = _strat("ph", placeholder=True)
    board = build_leaderboard([ph, real])
    assert board.entries[0].id == "real"
    assert board.entries[-1].id == "ph"


def test_missing_sort_value_pushed_to_bottom_regardless_of_order():
    has = _strat("has", sharpe=1.5)
    none1 = _strat("none1", sharpe=None)
    # desc
    board = build_leaderboard([none1, has], sort_by="sharpe_ratio", order="desc")
    assert board.entries[0].id == "has"
    assert board.entries[-1].id == "none1"
    # asc — None still last, not first
    board2 = build_leaderboard([none1, has], sort_by="sharpe_ratio", order="asc")
    assert board2.entries[-1].id == "none1"


def test_sort_by_pbo_respects_order():
    low = _strat("low", passes_gate=True, pbo=0.1, dsr_p=0.9, oos=1.0)
    high = _strat("high", passes_gate=True, pbo=0.6, dsr_p=0.9, oos=1.0)
    board = build_leaderboard([high, low], sort_by="pbo_score", order="asc")
    assert [e.id for e in board.entries] == ["low", "high"]


# ── filters ───────────────────────────────────────────────────────────────


def test_min_rigor_filters_out_failing_and_placeholders():
    passing = _strat("pass", passes_gate=True, dsr_p=0.9, oos=1.0, pbo=0.1)
    failing = _strat("fail", passes_gate=False, dsr_p=0.3)
    ph = _strat("ph", passes_gate=True, placeholder=True)
    board = build_leaderboard([passing, failing, ph], min_rigor=True)
    assert [e.id for e in board.entries] == ["pass"]
    assert board.total == 1


def test_regime_filter():
    bull = _strat("bull", regime="bull", passes_gate=True, dsr_p=0.9, oos=1.0, pbo=0.1)
    bear = _strat("bear", regime="bear", passes_gate=True, dsr_p=0.9, oos=1.0, pbo=0.1)
    board = build_leaderboard([bull, bear], regime_tag="bull")
    assert [e.id for e in board.entries] == ["bull"]


def test_limit_caps_entries_but_total_is_full_count():
    strats = [_strat(f"s{i}", passes_gate=True, dsr_p=0.5 + i * 0.01, oos=0.5, pbo=0.3) for i in range(10)]
    board = build_leaderboard(strats, limit=3)
    assert len(board.entries) == 3
    assert board.total == 10
    # ranks are global (1,2,3), not re-based after the cap
    assert [e.rank for e in board.entries] == [1, 2, 3]


# ── honesty / metadata ────────────────────────────────────────────────────


def test_forward_axis_is_honest_pending():
    board = build_leaderboard([_strat("a", passes_gate=True, dsr_p=0.9, oos=1.0, pbo=0.1)])
    fwd = board.entries[0].forward
    assert fwd.stockbench_status == "pending"
    assert fwd.stockbench_sortino is None
    assert fwd.live_pnl_status == "pending"
    assert fwd.live_pnl_pct is None


def test_scoring_engine_metadata_intact_even_when_empty():
    board = build_leaderboard([])
    assert board.entries == []
    assert board.total == 0
    eng = board.scoring_engine
    assert abs(sum(eng.weights.values()) - 1.0) < 1e-9
    assert eng.validation_axis == "live"
    assert eng.forward_axis == "pending"
    assert eng.stockbench_global.rank == "15/15"
    assert "fabricat" in eng.disclaimer.lower()  # the no-fabrication promise is loud


def test_creator_defaults_to_archimedes_for_curated():
    board = build_leaderboard([_strat("c", curator=None)])
    assert board.entries[0].creator == "Archimedes"
    board2 = build_leaderboard([_strat("c2", curator="0xabc")])
    assert board2.entries[0].creator == "0xabc"


def test_invalid_sort_by_falls_back_to_conviction():
    a = _strat("a", passes_gate=True, dsr_p=0.99, oos=1.5, pbo=0.05)
    b = _strat("b", passes_gate=False, dsr_p=0.1, oos=0.1, pbo=0.9)
    board = build_leaderboard([b, a], sort_by="nonsense_field")
    assert board.sort_by == "conviction_score"
    assert board.entries[0].id == "a"


def test_name_falls_back_to_methodology_when_no_paper_title():
    board = build_leaderboard([_strat("nm", title="")])
    assert board.entries[0].name.startswith("methodology for nm")


# ── HTTP route (full app boot → route → service) ──────────────────────────
# Mirrors test_risk_routes: import the app and exercise the real wiring. The
# route is fail-safe, so it returns 200 with intact scoring-engine metadata even
# if the strategy provider has no data.


async def test_leaderboard_endpoint_returns_200_with_scoring_engine():
    from archimedes.main import app
    from httpx import ASGITransport, AsyncClient

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/leaderboard")
    assert resp.status_code == 200
    data = resp.json()
    assert "entries" in data and isinstance(data["entries"], list)
    assert "scoring_engine" in data
    eng = data["scoring_engine"]
    assert abs(sum(eng["weights"].values()) - 1.0) < 1e-9
    assert eng["stockbench_global"]["rank"] == "15/15"
    assert "fabricat" in eng["disclaimer"].lower()
    # Every entry (if any) must carry the honest forward axis.
    for e in data["entries"]:
        assert e["forward"]["stockbench_status"] == "pending"
        assert e["forward"]["live_pnl_status"] == "pending"


async def test_leaderboard_endpoint_respects_query_params():
    from archimedes.main import app
    from httpx import ASGITransport, AsyncClient

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/leaderboard?sort_by=sharpe_ratio&order=asc&min_rigor=true&limit=5")
    assert resp.status_code == 200
    data = resp.json()
    assert data["sort_by"] == "sharpe_ratio"
    assert data["order"] == "asc"
    assert len(data["entries"]) <= 5
    # min_rigor → every returned entry passes the gate and is not a placeholder.
    for e in data["entries"]:
        assert e["passes_rigor_gate"] is True
        assert e["is_backtest_placeholder"] is False


async def test_leaderboard_rejects_bad_sort_param():
    from archimedes.main import app
    from httpx import ASGITransport, AsyncClient

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/leaderboard?sort_by=DROP_TABLE")
    # Query pattern guard → 422, never a 500.
    assert resp.status_code == 422


async def test_leaderboard_batches_rigor_gate_once(monkeypatch):
    """Regression for #912: the public leaderboard must run the rigor gate ONCE
    over the whole cohort (batch), not recompute it per strategy. The per-
    strategy path (_live_verdict_for_one + _live_rigor_result_for_one, 2 DB reads
    + 2 gate runs each) on an unauth, unratelimited endpoint is a trivial DoS."""
    from unittest.mock import MagicMock

    import archimedes.api.strategies_routes as sr
    from archimedes.main import app
    from httpx import ASGITransport, AsyncClient

    batch = MagicMock(return_value={})
    per_verdict = MagicMock()
    per_result = MagicMock()
    monkeypatch.setattr(sr, "_live_rigor_results_for_strategies", batch)
    monkeypatch.setattr(sr, "_live_verdict_for_one", per_verdict)
    monkeypatch.setattr(sr, "_live_rigor_result_for_one", per_result)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/leaderboard")

    assert resp.status_code == 200
    # Exactly one batched gate run for the whole board ...
    assert batch.call_count == 1
    # ... and never the per-strategy recompute.
    assert per_verdict.call_count == 0
    assert per_result.call_count == 0
