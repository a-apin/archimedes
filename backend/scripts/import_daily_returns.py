"""Import per-strategy daily-return JSON records into strategy_daily_returns (#774).

Consumes the exact record shape ``analytics-engine/scripts/gen_daily_returns_store.py``
writes (``{"stem", "dates", "daily_returns", "data_vintage", ...}``) and upserts
them into the DB table ``rigor_evaluator.load_daily_returns_store`` /
``compute_library_pbo`` now read from, replacing the committed JSON files this
repo used to carry.

Why a separate backend-side script rather than teaching gen_daily_returns_store.py
to write straight to Postgres: analytics-engine is a standalone, dependency-light
package (backtrader/pandas/yfinance only, no DB driver) — adding one is a new
top-level dependency, which this repo's convention flags for team sign-off before
adding, not something to decide unilaterally inside this migration. This script
lives in backend/, which already has the full SQLAlchemy/DB stack, and reads
whatever JSON gen_daily_returns_store.py produced (uncommitted, local-only from
here on) rather than requiring analytics-engine to talk to a database itself.

A stem's rows are replaced wholesale on import (delete existing rows for that
stem, insert the fresh set) — same "add-only per stem, explicit re-measurement
replaces" law gen_daily_returns_store.py documents for the JSON files it used to
produce. Running this script twice on unchanged input is a no-op in effect
(the replace is idempotent).

Usage:
    cd analytics-engine && uv run python scripts/gen_daily_returns_store.py --write
    cd .. && conda run -n archimedes python backend/scripts/import_daily_returns.py \\
        analytics-engine/strategies/daily_returns
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from archimedes.db import get_session, init_db  # noqa: E402
from archimedes.models.daily_returns_store import StrategyDailyReturn  # noqa: E402


def _load_records(store_dir: Path) -> list[dict]:
    records = []
    for path in sorted(store_dir.glob("*.json")):
        rec = json.loads(path.read_text(encoding="utf-8"))
        dates = rec.get("dates")
        daily_returns = rec.get("daily_returns")
        if not isinstance(dates, list) or not isinstance(daily_returns, list) or len(dates) != len(daily_returns):
            print(f"skip {path.name}: missing/mismatched dates or daily_returns")
            continue
        records.append(rec)
    return records


def import_records(store_dir: Path) -> int:
    """Replace each stem's rows with the store's current version. Returns the
    number of stems written."""
    records = _load_records(store_dir)
    if not records:
        print(f"no usable records in {store_dir}")
        return 0

    init_db()
    written = 0
    with get_session() as session:
        for rec in records:
            stem = rec.get("stem") or "unknown"
            vintage = rec.get("data_vintage")
            session.query(StrategyDailyReturn).filter(StrategyDailyReturn.stem == stem).delete()
            for d, r in zip(rec["dates"], rec["daily_returns"], strict=True):
                session.add(
                    StrategyDailyReturn(
                        stem=stem,
                        date=date.fromisoformat(d),
                        daily_return=float(r),
                        data_vintage=vintage,
                    )
                )
            written += 1
            print(f"{stem}: {len(rec['dates'])} rows (vintage={vintage})")
        session.commit()
    return written


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("analytics-engine/strategies/daily_returns")
    if not target.is_dir():
        raise SystemExit(f"{target} is not a directory")
    n = import_records(target)
    print(f"done: {n} stem(s) written to strategy_daily_returns")
