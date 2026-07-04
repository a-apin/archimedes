"""Backtest-fixtures DB store must round-trip byte-identically to the
pre-migration JSON shape.

The committed ``backtest_fixtures.json`` this migration removes from the
working tree is gone by the time this test runs, so "the JSON snapshot" here
is the same ``{stem: {28 metric fields}}`` dict
``strategy_provider._load_fixtures`` always produced from it — built directly
as a synthetic fixture, then round-tripped through the DB via the new
``StrategyBacktestFixture`` model and ``_load_fixtures_from_db``. Parity is
exact: same values, no tolerance loosening — nothing about how a strategy's
real metrics are computed changes here, only where they're read from.

Hermetic: tmp SQLite via a fresh, isolated engine (not the module-level
``archimedes.db`` singleton) passed directly into ``_load_fixtures_from_db``
— no real DB, no real files, no network.
"""

from __future__ import annotations

import numpy as np
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


def _synthetic_json_snapshot(n_strategies: int = 5, seed: int = 7) -> dict[str, dict]:
    """The shape ``backtest_fixtures.json`` projected onto — what
    ``strategy_provider._load_fixtures`` returned pre-migration, one entry
    per strategy stem with the full 28-field metric set."""
    rng = np.random.default_rng(seed)
    snapshot = {}
    for i in range(n_strategies):
        snapshot[f"strat_{i}"] = {
            "n_obs_daily": int(rng.integers(2000, 6000)),
            "sharpe_ratio": float(rng.normal(0.5, 0.3)),
            "sortino_ratio": float(rng.normal(0.6, 0.3)),
            "max_drawdown": float(rng.uniform(0.05, 0.5)),
            "cagr": float(rng.normal(0.08, 0.05)),
            "calmar_ratio": float(rng.uniform(0.1, 1.0)),
            "win_rate": float(rng.uniform(0.3, 0.6)),
            "profit_factor": float(rng.uniform(0.8, 3.0)),
            "total_trades": int(rng.integers(10, 200)),
            "avg_holding_period_days": float(rng.uniform(5, 90)),
            "correlation_to_spy": float(rng.uniform(-0.2, 0.9)),
            "correlation_to_btc": None,
            "out_of_sample_sharpe": float(rng.normal(0.4, 0.3)),
            "look_ahead_audit_passed": i % 2 == 0,
            "backtest_engine": "backtrader",
            "transaction_cost_bps": 10,
            "backtest_start": "2004-01-02T00:00:00",
            "backtest_end": "2026-04-30T00:00:00",
            "paper_claimed_sharpe": float(rng.normal(0.7, 0.2)),
            "paper_claimed_cagr": float(rng.normal(0.1, 0.05)),
            "paper_claimed_max_dd": float(rng.uniform(0.05, 0.3)),
            "deflated_sharpe_ratio": float(rng.normal(0.3, 0.2)),
            "dsr_p_value": float(rng.uniform(0, 1)),
            "dsr_convention": "raw",
            "num_trials_in_selection": n_strategies,
            "pbo_score": float(rng.uniform(0, 0.6)),
            "passes_rigor_gate": i == 0,
            "kelly_fraction": float(rng.uniform(0.1, 0.9)),
        }
    return snapshot


@pytest.fixture()
def tmp_db_session(tmp_path):
    """An isolated SQLite engine/session with strategy_backtest_fixtures
    created — independent of the app's module-level engine, so no
    monkeypatching of shared globals is needed."""
    from archimedes.db import Base

    engine = create_engine(f"sqlite:///{tmp_path / 'parity.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def _insert_snapshot(session, snapshot: dict[str, dict]) -> None:
    from archimedes.models.backtest_fixtures_store import StrategyBacktestFixture

    for stem, rec in snapshot.items():
        session.add(StrategyBacktestFixture(stem=stem, **rec))
    session.commit()


class TestBacktestFixturesStoreParity:
    def test_loaded_shape_matches_json_snapshot_exactly(self, tmp_db_session) -> None:
        from archimedes.services.strategy_provider import _load_fixtures_from_db

        snapshot = _synthetic_json_snapshot()
        _insert_snapshot(tmp_db_session, snapshot)

        db_store = _load_fixtures_from_db(session=tmp_db_session)

        assert set(db_store.keys()) == set(snapshot.keys())
        for stem, rec in snapshot.items():
            assert db_store[stem] == rec  # exact — every field, no tolerance

    def test_field_types_round_trip_natively(self, tmp_db_session) -> None:
        """float/int/bool/str/None survive the DB round-trip as the same
        native Python types ``_to_strategy``'s ``fx.get(...)`` calls expect."""
        from archimedes.services.strategy_provider import _load_fixtures_from_db

        snapshot = _synthetic_json_snapshot(n_strategies=1)
        _insert_snapshot(tmp_db_session, snapshot)

        rec = _load_fixtures_from_db(session=tmp_db_session)["strat_0"]
        assert isinstance(rec["sharpe_ratio"], float)
        assert isinstance(rec["total_trades"], int)
        assert isinstance(rec["passes_rigor_gate"], bool)
        assert isinstance(rec["backtest_engine"], str)
        assert rec["correlation_to_btc"] is None

    def test_empty_store_degrades_to_empty_dict(self, tmp_db_session) -> None:
        from archimedes.services.strategy_provider import _load_fixtures_from_db

        assert _load_fixtures_from_db(session=tmp_db_session) == {}

    def test_load_fixtures_orchestrator_matches_direct_db_load(self, tmp_db_session, monkeypatch) -> None:
        """``_load_fixtures()`` with no dynamic source configured is identical
        to calling ``_load_fixtures_from_db()`` directly."""
        from archimedes.services.strategy_provider import _load_fixtures, _load_fixtures_from_db

        monkeypatch.delenv("ARCHIMEDES_FIXTURES_PATH", raising=False)
        monkeypatch.delenv("ARCHIMEDES_FIXTURES_URL", raising=False)
        snapshot = _synthetic_json_snapshot(n_strategies=3, seed=42)
        _insert_snapshot(tmp_db_session, snapshot)

        assert _load_fixtures(session=tmp_db_session) == _load_fixtures_from_db(session=tmp_db_session)
