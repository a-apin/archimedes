"""#774: library-PBO computed from the DB store must equal library-PBO
computed from the pre-#774 JSON-snapshot shape, exactly.

The JSON files this issue removes from the working tree are gone by the time
this test runs, so "the JSON snapshot" here is the same
``{stem: {"dates": [...], "daily_returns": [...]}}`` dict
``load_daily_returns_store`` always produced from them — built directly as a
synthetic fixture, then round-tripped through the DB via the new
``StrategyDailyReturn`` model and ``load_daily_returns_store``. Parity is
exact: same float, no tolerance loosening — compute_pbo/compute_library_pbo
themselves are untouched by #774, so any drift here would be a bug in the
loader, not in the math.

Hermetic: tmp SQLite via a fresh, isolated engine (not the module-level
``archimedes.db`` singleton) passed directly into ``load_daily_returns_store``
— no real DB, no real files, no network.
"""

from __future__ import annotations

import numpy as np
import pytest
from archimedes.services.rigor_evaluator import align_returns_store, compute_library_pbo, load_daily_returns_store
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


def _synthetic_json_snapshot(n_strategies: int = 6, n_days: int = 300, seed: int = 11) -> dict[str, dict[str, list]]:
    """The shape every ``analytics-engine/strategies/daily_returns/<stem>.json``
    record projected onto — what ``load_daily_returns_store`` returned pre-#774."""
    rng = np.random.default_rng(seed)
    dates = [f"2024-{1 + (i // 28):02d}-{1 + (i % 28):02d}" for i in range(n_days)]
    return {
        f"strat_{i}": {
            "dates": dates,
            "daily_returns": rng.normal(0.0005, 0.01, n_days).tolist(),
        }
        for i in range(n_strategies)
    }


@pytest.fixture()
def tmp_db_session(tmp_path):
    """An isolated SQLite engine/session with strategy_daily_returns created —
    independent of the app's module-level engine, so no monkeypatching of
    shared globals is needed."""
    from archimedes.db import Base

    engine = create_engine(f"sqlite:///{tmp_path / 'parity.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def _insert_snapshot(session, snapshot: dict[str, dict[str, list]], data_vintage: str) -> None:
    from datetime import date as date_cls

    from archimedes.models.daily_returns_store import StrategyDailyReturn

    for stem, rec in snapshot.items():
        for d, r in zip(rec["dates"], rec["daily_returns"], strict=True):
            session.add(
                StrategyDailyReturn(
                    stem=stem,
                    date=date_cls.fromisoformat(d),
                    daily_return=r,
                    data_vintage=data_vintage,
                )
            )
    session.commit()


class TestDailyReturnsStoreParity:
    def test_library_pbo_from_db_matches_json_snapshot_exactly(self, tmp_db_session) -> None:
        snapshot = _synthetic_json_snapshot()
        json_pbo = compute_library_pbo(snapshot)
        assert json_pbo is not None  # sanity: the fixture must be computable

        _insert_snapshot(tmp_db_session, snapshot, data_vintage="2026-07-03")
        db_store, db_vintage = load_daily_returns_store(session=tmp_db_session)
        db_pbo = compute_library_pbo(db_store)

        assert db_pbo == json_pbo  # exact — same float, no tolerance
        assert db_vintage == "2026-07-03"

    def test_loaded_shape_matches_json_snapshot_exactly(self, tmp_db_session) -> None:
        """Not just the PBO output — the intermediate {stem: {dates, daily_returns}}
        shape must round-trip byte-identically (string dates, float returns)."""
        snapshot = _synthetic_json_snapshot(n_strategies=3, n_days=50)
        _insert_snapshot(tmp_db_session, snapshot, data_vintage="2026-07-01")

        db_store, _ = load_daily_returns_store(session=tmp_db_session)

        assert set(db_store.keys()) == set(snapshot.keys())
        for stem, rec in snapshot.items():
            assert db_store[stem]["dates"] == rec["dates"]
            assert db_store[stem]["daily_returns"] == pytest.approx(rec["daily_returns"], abs=0.0)

    def test_aligned_selection_set_size_matches(self, tmp_db_session) -> None:
        """compute_library_pbo's actual selection-set size (post date-alignment)
        is identical whether sourced from the raw snapshot or the DB round-trip."""
        snapshot = _synthetic_json_snapshot(n_strategies=5, n_days=200)
        _insert_snapshot(tmp_db_session, snapshot, data_vintage="2026-07-02")

        db_store, _ = load_daily_returns_store(session=tmp_db_session)

        assert len(align_returns_store(snapshot)) == len(align_returns_store(db_store)) == 5

    def test_empty_store_fails_closed_same_as_pre_774(self, tmp_db_session) -> None:
        store, vintage = load_daily_returns_store(session=tmp_db_session)
        assert store == {}
        assert vintage is None
        assert compute_library_pbo(store) is None
