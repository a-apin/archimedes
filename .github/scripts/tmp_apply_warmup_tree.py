#!/usr/bin/env python3
"""Apply the remaining #1713 file patches onto copies taken from origin/main."""
from __future__ import annotations

import subprocess
from pathlib import Path

NEW_TESTS = r'''
def test_get_all_daily_returns_cache_hit_issues_no_query() -> None:
    """#1713: the second Library read must not re-decode artifact blobs.

    MUTATION: delete the cache lookup in ``get_all_daily_returns``. This then
    sees a SELECT on the second call and fails. The first-call one-query
    guard above still requires a live read — the cache is a memo, not a fixture.
    """
    session, _SessionLocal, ids = _seeded_session_with_artifacts(n_strategies=10, n_cycles=3)
    try:
        first = get_all_daily_returns(session, ids)
        session.expunge_all()
        statements, detach = _capture_sql(session.get_bind())
        try:
            second = get_all_daily_returns(session, ids)
        finally:
            detach()

        selects = _selects_from_backtest_results(statements)
        assert selects == [], (
            "cohort-returns cache miss on the second call — the Library page "
            f"would still pay the blob decode. statements={selects!r}"
        )
        assert second == first
        statements, detach = _capture_sql(session.get_bind())
        try:
            reversed_order = get_all_daily_returns(session, list(reversed(ids)))
        finally:
            detach()
        assert _selects_from_backtest_results(statements) == []
        assert set(reversed_order) == set(first)
    finally:
        session.close()
'''

def show(path: str) -> str:
    return subprocess.check_output(["git", "show", f"origin/main:{path}"], text=True)

def main() -> None:
    print("helper loaded")

if __name__ == "__main__":
    main()
