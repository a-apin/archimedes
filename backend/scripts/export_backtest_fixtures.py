"""Export strategy_backtest_fixtures back into the legacy JSON shape.

The analytics-engine fixture generators (``regen_fixtures.py``,
``regen_buy_hold_fixture.py``) are add-only: they read the existing fixture
file to skip stems already present, so a fresh backtest run never silently
overwrites a stem whose current-data re-run would drift from its
originally-curated metrics (yfinance data drift — see ``regen_fixtures.py``'s
SCOPE note). Now that the committed ``backtest_fixtures.json`` is gone, this
script re-creates that "what's already published" snapshot locally
(uncommitted) from the DB, so the add-only check in those two scripts still
has something real to compare against.

Usage:
    conda run -n archimedes python backend/scripts/export_backtest_fixtures.py \\
        analytics-engine/strategies/backtest_fixtures.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from archimedes.db import get_session, init_db
from archimedes.models.backtest_fixtures_store import FIXTURE_FIELDS, StrategyBacktestFixture


def export_records(fixture_path: Path) -> int:
    """Write every strategy_backtest_fixtures row to fixture_path in the
    legacy ``{stem: {...}}`` shape. Returns the number of stems written."""
    init_db()
    with get_session() as session:
        rows = session.query(StrategyBacktestFixture).order_by(StrategyBacktestFixture.stem).all()
        data = {row.stem: {field: getattr(row, field) for field in FIXTURE_FIELDS} for row in rows}

    fixture_path.parent.mkdir(parents=True, exist_ok=True)
    fixture_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return len(data)


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("analytics-engine/strategies/backtest_fixtures.json")
    n = export_records(target)
    print(f"done: {n} stem(s) exported to {target}")
