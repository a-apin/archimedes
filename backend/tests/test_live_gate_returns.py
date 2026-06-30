"""#788/#818 — fusion/debate candidates persist REAL returns so the live rigor
gate reads pass/fail, not "pending".

Hermetic: the DB/persist boundary (`get_session`, `insert_backtest_if_missing`) is
mocked, so this proves `_persist_real_returns` builds the correct backtest row +
artifact (the live gate then re-grades the real returns) without a DB. The full
pass/fail end-to-end is verified by dogfooding `scripts/agent_journey.py` on deploy.
"""

from __future__ import annotations

import contextlib
import json

from archimedes.agents.generation_pipeline import _CandidateResult, _persist_real_returns


class _FakeEmit:
    def __init__(self):
        self.events = []

    async def emit(self, name, **kw):
        self.events.append((name, kw))


_RIGOR = {
    "dsr": 1.6,
    "dsr_p_value": 0.99,
    "pbo": 0.2,
    "oos_sharpe": 1.1,
    "sharpe_ratio": 1.3,
    "sortino_ratio": 1.5,
    "max_drawdown": 0.12,
    "cagr": 0.15,
    "calmar_ratio": 1.25,
    "win_rate": 0.55,
    "total_trades": 64,
    "lookahead_audit_passed": True,
    "passing": True,
}


def _candidate(*, return_series, has_real_rigor=True, gen="fusion") -> _CandidateResult:
    return _CandidateResult(
        candidate_id="c1",
        strategy_name="Fusion X",
        thesis="t",
        asset_universe=["SPY"],
        source_papers=[{"arxiv_id": "2401.00001"}],
        weights={},
        reasoning="r",
        rigor_verdict=dict(_RIGOR),
        passes_rigor=True,
        generation_method=gen,
        source_arxiv_ids=["2401.00001", "2402.00001"],
        has_real_rigor=has_real_rigor,
        return_series=return_series,
    )


def _patch_persist(monkeypatch, captured):
    def _fake_insert(session, *, strategy_id, content_hash, result, operation=None, artifact_json=None, **kw):
        captured.update(
            strategy_id=strategy_id,
            content_hash=content_hash,
            result=result,
            operation=operation,
            artifact=artifact_json,
        )
        return (None, True)

    @contextlib.contextmanager
    def _fake_session():
        yield object()

    monkeypatch.setattr("archimedes.db.get_session", _fake_session)
    monkeypatch.setattr("archimedes.services.backtest_repository.insert_backtest_if_missing", _fake_insert)


async def test_persist_real_returns_builds_correct_backtest_row(monkeypatch):
    captured: dict = {}
    _patch_persist(monkeypatch, captured)

    returns = [0.01, -0.004, 0.006, -0.002, 0.008, 0.003, -0.001, 0.005, 0.002, -0.003, 0.004, 0.001]
    c = _candidate(return_series=returns)
    emit = _FakeEmit()
    await _persist_real_returns(c, "strat1", emit, num_trials=24)

    assert captured["strategy_id"] == "strat1"
    r = captured["result"]
    # DSR multiple-testing count threaded through (library+pool, #770/#820).
    assert r.num_trials_in_selection == 24
    # Equity curve rebuilt from the daily returns (base 1.0).
    assert r.equity_curve[0] == 1.0
    assert len(r.equity_curve) == len(returns) + 1
    # Rigor metrics mapped from the verdict.
    assert r.deflated_sharpe_ratio == _RIGOR["dsr"]
    assert r.out_of_sample_sharpe == _RIGOR["oos_sharpe"]
    # The exact daily returns ride in the artifact so live_rigor_gate re-grades them.
    art = json.loads(captured["artifact"])
    assert art["results"][0]["metrics"]["daily_returns"] == returns
    # An SSE backtest_done event is emitted for the agent/UI.
    assert any(name == "backtest_done" for name, _ in emit.events)


async def test_persist_real_returns_skips_when_no_real_backtest(monkeypatch):
    called: list = []
    monkeypatch.setattr(
        "archimedes.services.backtest_repository.insert_backtest_if_missing",
        lambda *a, **k: called.append(1) or (None, True),
    )
    # No return series → nothing to persist (live gate stays "pending" honestly).
    await _persist_real_returns(_candidate(return_series=None), "s", _FakeEmit(), 5)
    # has_real_rigor=False (e.g. text-only fusion / agent path) → skip.
    await _persist_real_returns(_candidate(return_series=[0.01] * 12, has_real_rigor=False), "s", _FakeEmit(), 5)
    # Too few observations to grade → skip.
    await _persist_real_returns(_candidate(return_series=[0.01, 0.02]), "s", _FakeEmit(), 5)
    assert called == []
