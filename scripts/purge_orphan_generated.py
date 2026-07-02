#!/usr/bin/env python3
"""Purge ownerless generated strategies (owner_wallet IS NULL, is_example=False).

Before per-user ownership landed (dbrowneup/strategy-ownership), every generated
strategy was persisted with no owner. Those legacy rows are now invisible to
everyone under private-until-published (not published, no owner to match), so
they are dead weight — e.g. the ~10 orphan rejected fusion strategies in prod.

What gets deleted per orphan strategy id:
  * the ``strategy_store`` row itself
  * its ``strategy_passports`` mirror row (same id — the generation pipeline
    reuses the StrategyRecord id for the passport), deleted via the ORM so the
    ``passport_paper_refs`` children cascade (relationship cascade
    "all, delete-orphan") on BOTH sqlite and postgres
  * its ``backtest_results`` rows (``strategy_id`` column — indexed, no FK)

NOT deleted — investigated, no strategy-id relationship exists:
  * ``strategy_proposals`` (episodic memory, T-PE.8) carries generation_id /
    proposal_id / its own content_hash; it has NO strategy_id column and its
    content-hash space differs from strategy_store's, so rows cannot be safely
    matched to a strategy id. Proposals are intentionally kept as episodic
    history of every generation attempt.

DEFAULT is a dry run that prints exactly what would be deleted. Pass
``--execute`` to perform the deletion in one transaction.

Usage (from the repo root, archimedes conda env; DATABASE_URL selects the DB):

    python scripts/purge_orphan_generated.py             # dry run (default)
    python scripts/purge_orphan_generated.py --execute   # delete for real
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))


def find_orphans(session):
    """Generated (non-example) strategies with no owner wallet."""
    from archimedes.models.strategy_store import StrategyRecord

    return (
        session.query(StrategyRecord)
        .filter(
            StrategyRecord.is_example.is_(False),
            StrategyRecord.owner_wallet.is_(None),
        )
        .order_by(StrategyRecord.created_at.asc())
        .all()
    )


def purge_orphans(*, execute: bool = False) -> dict:
    """Find (and with ``execute=True`` delete) orphan generated strategies.

    Returns a summary dict:
    ``{"strategies": int, "passports": int, "backtests": int, "executed": bool}``
    """
    from archimedes.db import get_session, init_db
    from archimedes.models.backtest_store import BacktestResultRecord
    from archimedes.models.strategy_passport_record import StrategyPassportRecord

    init_db()  # ensure the ownership columns exist before querying them

    with get_session() as session:
        orphans = find_orphans(session)
        ids = [r.id for r in orphans]

        passports = (
            session.query(StrategyPassportRecord).filter(StrategyPassportRecord.id.in_(ids)).all() if ids else []
        )
        backtests = (
            session.query(BacktestResultRecord).filter(BacktestResultRecord.strategy_id.in_(ids)).all() if ids else []
        )
        backtests_by_sid: dict[str, int] = {}
        for b in backtests:
            backtests_by_sid[b.strategy_id] = backtests_by_sid.get(b.strategy_id, 0) + 1
        passport_ids = {p.id for p in passports}

        mode = "EXECUTE" if execute else "DRY RUN"
        print(f"[{mode}] orphan generated strategies (is_example=False, owner_wallet IS NULL): {len(orphans)}")
        for r in orphans:
            created = r.created_at.isoformat() if r.created_at else "?"
            print(
                f"  - {r.id}  name={r.strategy_name!r}  method={r.generation_method}  "
                f"status={r.status}  created={created}  "
                f"passport={'yes' if r.id in passport_ids else 'no'}  "
                f"backtest_rows={backtests_by_sid.get(r.id, 0)}"
            )
        print(
            f"[{mode}] totals: {len(orphans)} strategy_store row(s), "
            f"{len(passports)} strategy_passports row(s) (+ their passport_paper_refs), "
            f"{len(backtests)} backtest_results row(s). "
            "strategy_proposals: kept (no strategy-id relationship — see module docstring)."
        )

        summary = {
            "strategies": len(orphans),
            "passports": len(passports),
            "backtests": len(backtests),
            "executed": execute,
        }

        if not execute:
            print("Dry run — nothing deleted. Re-run with --execute to delete.")
            session.rollback()
            return summary

        # One transaction: ORM deletes so passport_paper_refs cascade in Python
        # (works on sqlite without PRAGMA foreign_keys and on postgres alike).
        for p in passports:
            session.delete(p)
        if ids:
            session.query(BacktestResultRecord).filter(BacktestResultRecord.strategy_id.in_(ids)).delete(
                synchronize_session=False
            )
        for r in orphans:
            session.delete(r)
        session.commit()
        print(f"Deleted {len(orphans)} strategies, {len(passports)} passports, {len(backtests)} backtest rows.")
        return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=True,
        help="Print what would be deleted without deleting (the default).",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually delete the orphan rows (one transaction).",
    )
    args = parser.parse_args()

    purge_orphans(execute=args.execute)
    return 0


if __name__ == "__main__":
    sys.exit(main())
