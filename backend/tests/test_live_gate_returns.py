"""#788/#818 — fusion/debate candidates persist REAL returns so the live rigor
gate reads pass/fail, not "pending".

Real tmp-sqlite integration (per the deep review): the earlier mock-only tests
mocked away exactly the two broken behaviors. These drive `_persist_real_returns`
against a real sqlite DB and prove:
  1. the write COMMITS — the row survives → `get_daily_returns` reads it (catches the
     flush-only rollback that kept the gate at "pending");
  2. a SYNTHETIC-sourced backtest is NEVER persisted (grading random-walk noise as a
     real pass/fail is a #1-rule claims-integrity violation) — it stays honestly
     "pending".
"""

from __future__ import annotations

import numpy as np
import pytest
from archimedes.agents.generation_pipeline import _CandidateResult, _persist_real_returns
from archimedes.db import get_session


@pytest.fixture(autouse=True)
def _use_tmp_db(tmp_path, monkeypatch):
    """Point the DB at a FRESH temp sqlite so the persist path runs for real.

    db.engine/SessionLocal are created once at import, so setenv alone doesn't
    re-point them; rebind both to a per-test engine (else tests share one stale DB).
    """
    import archimedes.db as db
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    url = f"sqlite:///{tmp_path / 'live_gate.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    eng = create_engine(url, connect_args={"check_same_thread": False})
    monkeypatch.setattr(db, "engine", eng)
    monkeypatch.setattr(db, "SessionLocal", sessionmaker(bind=eng, autocommit=False, autoflush=False))
    db.init_db()  # uses the rebound engine + registers ALL tables (passport/kg side-effect imports)

    # Run the persist inline (not on a worker thread) so the write + read share one
    # connection context — deterministic under sqlite.
    async def _inline(fn, *a, **k):
        return fn(*a, **k)

    monkeypatch.setattr("archimedes.agents.generation_pipeline.asyncio.to_thread", _inline)
    yield


class _FakeEmit:
    def __init__(self):
        self.events = []

    async def emit(self, name, **kw):
        self.events.append((name, kw))


def _rigor(data_source="live"):
    return {
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
        "data_source": data_source,
        "admissible": data_source != "synthetic",
    }


def _candidate(*, return_series, has_real_rigor=True, data_source="live") -> _CandidateResult:
    return _CandidateResult(
        candidate_id="c1",
        strategy_name="Fusion X",
        thesis="Fuse momentum + vol.",
        asset_universe=["SPY"],
        source_papers=[{"arxiv_id": "2401.00001", "title": "P"}],
        weights={},
        reasoning="r",
        rigor_verdict=_rigor(data_source),
        passes_rigor=True,
        generation_method="fusion",
        source_arxiv_ids=["2401.00001", "2402.00001"],
        has_real_rigor=has_real_rigor,
        return_series=return_series,
    )


# A realistic daily series with genuine variance (real DSL backtests produce hundreds
# of bars). A deterministic-but-degenerate/repeating series makes the DSR/OOS math
# raise → the gate fails closed to "pending"; a real return distribution grades cleanly.
_REAL_RETURNS = np.random.default_rng(7).normal(0.0006, 0.011, 250).tolist()


async def test_real_returns_persist_and_survive_commit():
    from archimedes.services.backtest_repository import get_daily_returns

    emit = _FakeEmit()
    await _persist_real_returns(_candidate(return_series=_REAL_RETURNS), "strat_live", emit, num_trials=24)

    with get_session() as session:
        persisted = get_daily_returns(session, "strat_live")
    # Row SURVIVED the session close → commit fired (flush-only would give []).
    assert len(persisted) == len(_REAL_RETURNS)
    assert any(name == "backtest_done" for name, _ in emit.events)
    assert not any(name == "backtest_failed" for name, _ in emit.events)


async def test_synthetic_returns_are_never_persisted():
    from archimedes.services.backtest_repository import get_daily_returns

    # Same returns, but data_source="synthetic" → must NOT be persisted (claims-integrity).
    await _persist_real_returns(
        _candidate(return_series=_REAL_RETURNS, data_source="synthetic"), "strat_synth", _FakeEmit(), num_trials=24
    )
    with get_session() as session:
        assert get_daily_returns(session, "strat_synth") == []  # honestly "pending"


async def test_live_verdict_readable_from_persisted_strategy():
    # After persistence, the strategy's live gate + passport read the REAL returns —
    # not "pending" — so the single-strategy read path + deploy gate see the verdict.
    from archimedes.services.live_rigor_gate import verdict_from_returns

    await _persist_real_returns(_candidate(return_series=_REAL_RETURNS), "strat_read", _FakeEmit(), num_trials=24)
    with get_session() as session:
        from archimedes.services.backtest_repository import get_daily_returns

        returns = get_daily_returns(session, "strat_read")
    verdict = verdict_from_returns("strat_read", returns, num_trials=24)
    assert verdict.status in ("pass", "fail")  # NOT "pending"


async def test_persist_skips_when_no_real_backtest():
    from archimedes.services.backtest_repository import get_daily_returns

    await _persist_real_returns(_candidate(return_series=None), "s1", _FakeEmit(), 5)
    await _persist_real_returns(_candidate(return_series=_REAL_RETURNS, has_real_rigor=False), "s2", _FakeEmit(), 5)
    await _persist_real_returns(_candidate(return_series=[0.01, 0.02]), "s3", _FakeEmit(), 5)
    with get_session() as session:
        assert get_daily_returns(session, "s1") == []
        assert get_daily_returns(session, "s2") == []
        assert get_daily_returns(session, "s3") == []
