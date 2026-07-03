"""Import per-strategy backtest-fixture JSON records into strategy_backtest_fixtures.

Consumes the exact record shape ``analytics-engine/strategies/backtest_fixtures.json``
(and its generators, ``regen_fixtures.py`` / ``regen_buy_hold_fixture.py``) produce
(``{stem: {28 metric fields}}``) and upserts them into the DB table
``strategy_provider._load_fixtures_from_db`` now reads from, replacing the
committed JSON file this repo used to carry.

Why a separate backend-side script rather than teaching the analytics-engine
generators to write straight to Postgres: analytics-engine is a standalone,
dependency-light package (backtrader/pandas/yfinance only, no DB driver) —
adding one is a new top-level dependency, which this repo's convention flags
for team sign-off before adding, not something to decide unilaterally inside
this migration. This script lives in backend/, which already has the full
SQLAlchemy/DB stack, and reads whatever JSON a generator (or
``export_backtest_fixtures.py``) produced locally (uncommitted from here on)
rather than requiring analytics-engine to talk to a database itself.

Each stem is an upsert by primary key, so running this script twice on
unchanged input is a no-op in effect.

Usage:
    conda run -n archimedes python backend/scripts/import_backtest_fixtures.py \\
        analytics-engine/strategies/backtest_fixtures.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from archimedes.db import get_session, init_db
from archimedes.models.backtest_fixtures_store import FIXTURE_FIELDS, StrategyBacktestFixture


def _load_records(fixture_path: Path) -> dict[str, dict]:
    data = json.loads(fixture_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise SystemExit(f"{fixture_path}: expected a JSON object, got {type(data).__name__}")
    records = {}
    for stem, rec in data.items():
        missing = [f for f in FIXTURE_FIELDS if f not in rec]
        if missing:
            print(f"skip {stem}: missing field(s) {missing}")
            continue
        records[stem] = rec
    return records


def import_records(fixture_path: Path) -> int:
    """Upsert each stem's row from the fixture file. Returns the number of
    stems written."""
    records = _load_records(fixture_path)
    if not records:
        print(f"no usable records in {fixture_path}")
        return 0

    init_db()
    written = 0
    with get_session() as session:
        for stem, rec in records.items():
            session.merge(StrategyBacktestFixture(stem=stem, **{field: rec[field] for field in FIXTURE_FIELDS}))
            written += 1
            print(f"{stem}: sharpe={rec['sharpe_ratio']:.4f} passes_rigor_gate={rec['passes_rigor_gate']}")
        session.commit()
    return written


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("analytics-engine/strategies/backtest_fixtures.json")
    if not target.is_file():
        raise SystemExit(f"{target} is not a file")
    n = import_records(target)
    print(f"done: {n} stem(s) written to strategy_backtest_fixtures")
