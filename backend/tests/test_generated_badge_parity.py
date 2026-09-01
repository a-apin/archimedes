"""GET /api/strategies/generated may never claim a rigor pass the gate did not give (#1747).

The defect. ``list_generated_strategies`` returned ``StrategyRecord.to_dict()``
and nothing else, so the Library's Generated tab took BOTH halves of its green
badge from the same generation-time blob: ``status`` ("live") and
``rigor_verdict["passing"]`` are written together by
``models/strategy_store.py`` from the FUSION verdict and neither is ever
re-derived after a backtest. The pill's own demotion arm — ``status === 'live'``
AND the gate failed — was therefore structurally unreachable on that tab, and
21 strategies whose own passport says "Reference only — gate failed" rendered
"Live ✓".

What is pinned here:

  1. A row seeded exactly as prod holds it — ``StrategyRecord(status='live',
     rigor_verdict={'passing': True})`` plus a passport row plus a healthy
     persisted return series the LIVE gate fails — is served ``fail`` / False,
     and agrees with ``GET /api/strategies/{id}`` for the same id.
  2. A row with no ``strategy_passports`` row is ``pending`` / ``None`` — never
     True, never False (nothing graded it, so "failed" would be a fresh lie).
  3. **The stored column is not the source.** A row whose stored
     ``strategy_passports.passes_rigor_gate`` is True, but whose live gate
     fails, is served False. Overlaying the stored boolean instead of running
     the gate passes tests 1 and 2 and fails this one — that is the whole point
     of it. The column is mixed-vintage: its FIRST writer is the generation-time
     fusion verdict (``generation_pipeline._persist_candidate``), and the live
     re-grade replaces it only when the post-backtest refresh reaches its write.
  4. A zero-variance persisted series is ``degenerate``, not ``fail`` and not
     ``pending`` — it WAS evaluated, and there was nothing in it to grade.
  5. A genuinely passing series is still served ``pass`` / True: the fix must
     not have "fixed" the badge by never being green.
  6. The rigor NUMBERS come from the same gate run as the badge, and are absent
     unless that run reached a pass/fail verdict. The display Sharpe comes from
     the same ``backtest_results`` row that run read its returns from — not from
     ``strategy_passports.sharpe_ratio``, a denormalised snapshot with no
     foreign key to ``backtest_results`` that routinely describes an older run.
  7. Page cost is constant in the number of rows — the passport read and the
     persisted-context reads are batched, never per row.

Hermetic (tmp-sqlite, no .env / network / Redis); DB fixture copies the
``_use_tmp_db`` pattern from test_generated_citation_truth.py.

Gate:
  env -i HOME=$HOME PATH=$PATH PYTHONPATH=backend python -m pytest \\
      backend/tests/test_generated_badge_parity.py -q -p no:cacheprovider
"""

from __future__ import annotations

import json
import time

import archimedes.db as db
import numpy as np
import pytest
from archimedes.api.auth_siwe import _COOKIE_NAME, _sign_session
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event

_W_OWNER = "0xAbC0000000000000000000000000000000000001"


@pytest.fixture(autouse=True)
def _use_tmp_db(tmp_path, monkeypatch):
    """Point the DB at a FRESH temp sqlite (rebinds db.engine + db.SessionLocal).

    db.engine/SessionLocal are built once at import, so setenv alone re-points
    nothing — both have to be rebound to a per-test engine before init_db().
    """
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    url = f"sqlite:///{tmp_path / 'badge.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    eng = create_engine(url, connect_args={"check_same_thread": False})
    monkeypatch.setattr(db, "engine", eng)
    monkeypatch.setattr(db, "SessionLocal", sessionmaker(bind=eng, autocommit=False, autoflush=False))
    db.init_db()
    yield


def _siwe_cookies(wallet: str) -> dict[str, str]:
    return {_COOKIE_NAME: _sign_session(wallet, time.time())}


# ── Return series with KNOWN live-gate verdicts ─────────────────────────────
#
# Deliberately not "a series that looks plausible": each is chosen because the
# real ``run_rigor_gate`` gives it the verdict this file names, so a test that
# claims "the live gate fails this" is checkable rather than asserted.


def _failing_returns(n: int = 300) -> list[float]:
    """Zero-drift noise: real variance, Sharpe ~0 → the live gate FAILS on DSR."""
    return np.random.default_rng(7).normal(0.0, 0.01, n).tolist()


def _passing_returns(n: int = 300) -> list[float]:
    """Strong, non-degenerate → the live gate PASSES at the strictest level."""
    return np.random.default_rng(42).normal(0.0018, 0.006, n).tolist()


def _flat_returns(n: int = 300) -> list[float]:
    """Zero variance → DEGENERATE: evaluated, and unevaluable."""
    return [0.0] * n


def _mk_strategy(
    sid: str,
    *,
    status: str = "live",
    rigor_verdict: dict | None = None,
    owner: str = _W_OWNER,
):
    """The generation-time record, seeded the way the pipeline writes it."""
    from archimedes.models.strategy_store import StrategyRecord

    with db.get_session() as session:
        session.add(
            StrategyRecord(
                id=sid,
                content_hash=("0x" + sid).ljust(66, "0"),
                generation_method="fusion",
                source_papers="[]",
                strategy_name=f"Strategy {sid}",
                thesis="test thesis",
                asset_universe="[]",
                risk_profile="moderate",
                status=status,
                rigor_verdict=json.dumps(rigor_verdict) if rigor_verdict is not None else None,
                is_example=False,
                owner_wallet=owner.lower(),
                is_published=False,
            )
        )
        session.commit()


def _mk_passport(sid: str, *, passes_rigor_gate: bool = False, sharpe_ratio: float | None = 0.4):
    from archimedes.models.strategy_passport_record import StrategyPassportRecord

    with db.get_session() as session:
        session.add(
            StrategyPassportRecord(
                id=sid,
                content_hash=("0y" + sid).ljust(66, "0"),
                generation_method="fusion",
                methodology_summary="test",
                asset_universe="[]",
                status="live",
                passes_rigor_gate=passes_rigor_gate,
                sharpe_ratio=sharpe_ratio,
                # The generation-time fusion numbers. Nothing may serve these
                # beside a non-pass badge (#1187 / #868).
                deflated_sharpe_ratio=0.87,
                dsr_p_value=0.99,
                pbo_score=0.02,
                out_of_sample_sharpe=1.9,
            )
        )
        session.commit()


def _mk_backtest(
    sid: str,
    *,
    returns: list[float] | None,
    pbo: float | None = 0.05,
    sharpe_ratio: float = 0.031,
    content_hash: str | None = None,
):
    """Persist the row the live gate reads its returns and context from.

    ``sharpe_ratio`` defaults to a value DISTINCT from every
    ``strategy_passports.sharpe_ratio`` this file seeds (0.4 / 0.9 / 1.4 / 1.8),
    so an assertion about the served display Sharpe can tell the two columns
    apart. They are not the same number in prod either: the passport column is a
    denormalised snapshot with no FK to ``backtest_results``.
    """
    from archimedes.models.backtest_store import BacktestResultRecord

    with db.get_session() as session:
        session.add(
            BacktestResultRecord(
                strategy_id=sid,
                content_hash=content_hash or f"bt_{sid}",
                sharpe_ratio=sharpe_ratio,
                artifact_json=(
                    json.dumps({"results": [{"metrics": {"daily_returns": returns}}]}) if returns is not None else None
                ),
                num_trials_in_selection=1,
                pbo_score=pbo,
                look_ahead_audit_passed=True,
                look_ahead_audit_source=None,
                source_pipeline="test",
                backtest_engine=None,
            )
        )
        session.commit()


async def _generated_rows() -> list[dict]:
    from archimedes.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/strategies/generated", cookies=_siwe_cookies(_W_OWNER))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["degraded"] is False, body["degraded_reason"]
    return body["strategies"]


async def _row(sid: str) -> dict:
    rows = {r["id"]: r for r in await _generated_rows()}
    assert sid in rows, f"{sid} missing from the generated list: {sorted(rows)}"
    return rows[sid]


async def _detail(sid: str) -> dict:
    from archimedes.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/api/strategies/{sid}", cookies=_siwe_cookies(_W_OWNER))
    assert resp.status_code == 200, resp.text
    return resp.json()


# ── 1. The prod shape: synthesis said pass, the live gate says fail ─────────


async def test_generation_time_pass_does_not_survive_a_failing_live_gate():
    """The exact 21-row prod state, and the parity claim #1747 is about.

    ``status='live'`` + ``rigor_verdict={'passing': True}`` is what the fusion
    gate wrote; the persisted series is what the rigor gate actually has to
    grade. The Library must report the second, and must not disagree with the
    strategy's own detail page about it.
    """
    sid = "gen1747000000fail"
    _mk_strategy(sid, status="live", rigor_verdict={"passing": True, "dsr": 0.8, "pbo": 0.02})
    _mk_passport(sid, passes_rigor_gate=False, sharpe_ratio=0.4)
    _mk_backtest(sid, returns=_failing_returns())

    row = await _row(sid)
    assert row["rigor_gate_status"] == "fail"
    assert row["passes_rigor_gate"] is False

    # The generation-time blob is still ON THE WIRE unchanged — the fix is a
    # read-side overlay, not a rewrite of what the synthesis gate thought.
    assert row["status"] == "live"
    assert row["rigor_verdict"]["passing"] is True

    # ...and the badge the Library renders now agrees with the badge the
    # strategy's own passport page renders. Before the fix these were "Live ✓"
    # and "Reference only — gate failed" for the same id.
    detail = await _detail(sid)
    assert row["passes_rigor_gate"] == detail["passes_rigor_gate"]
    assert row["rigor_gate_status"] == detail["rigor_gate_status"]


# ── 2. No passport row → nothing graded it ─────────────────────────────────


async def test_row_without_a_passport_is_pending_not_a_boolean():
    sid = "gen1747000pending"
    _mk_strategy(sid, status="live", rigor_verdict={"passing": True})

    row = await _row(sid)
    assert row["rigor_gate_status"] == "pending"
    assert row["passes_rigor_gate"] is None, (
        "an ungraded row must carry no boolean at all — False would accuse it of failing a gate that never ran"
    )


async def test_passport_row_with_no_persisted_returns_is_pending():
    """A passport exists but the backtest has not produced a series yet."""
    sid = "gen1747000norets"
    _mk_strategy(sid, status="live", rigor_verdict={"passing": True})
    _mk_passport(sid, passes_rigor_gate=True, sharpe_ratio=1.4)

    row = await _row(sid)
    assert row["rigor_gate_status"] == "pending"
    assert row["passes_rigor_gate"] is None


# ── 3. The mutation-kill: the stored column is NOT the source ──────────────


async def test_stored_passport_true_does_not_survive_a_failing_live_gate():
    """Seeded so that overlaying ``strategy_passports.passes_rigor_gate``
    instead of running the gate would serve True.

    This is the assertion that separates "the badge is a gate result" from "the
    badge is a stored boolean of unknown vintage". That column's first writer is
    the generation-time fusion verdict, and there is no provenance column to
    tell a fresh live verdict from a stale synthesis one — so reading it is
    reading a claim, whichever value it happens to hold.
    """
    sid = "gen1747storedtru"
    _mk_strategy(sid, status="live", rigor_verdict={"passing": True})
    _mk_passport(sid, passes_rigor_gate=True, sharpe_ratio=1.4)
    _mk_backtest(sid, returns=_failing_returns())

    row = await _row(sid)
    assert row["passes_rigor_gate"] is False, "the stored True must not reach the badge"
    assert row["rigor_gate_status"] == "fail"

    # And the DB column is untouched: this is a read-side overlay, not a
    # write-back that would destroy the record of what the pipeline wrote.
    from archimedes.services.passport_loader import get_passport

    with db.get_session() as session:
        assert get_passport(session, sid).passes_rigor_gate is True


# ── 4. Degenerate is its own answer ────────────────────────────────────────


async def test_zero_variance_series_is_degenerate_not_fail_or_pending():
    sid = "gen1747000degen0"
    _mk_strategy(sid, status="live", rigor_verdict={"passing": True})
    _mk_passport(sid, passes_rigor_gate=True, sharpe_ratio=0.9)
    _mk_backtest(sid, returns=_flat_returns())

    row = await _row(sid)
    assert row["rigor_gate_status"] == "degenerate", (
        "a flat persisted series was evaluated and found unevaluable — that is neither 'pending' nor 'fail'"
    )
    assert row["passes_rigor_gate"] is False


# ── 5. The badge can still be green when a gate says so ────────────────────


async def test_live_gate_pass_is_served_as_a_pass():
    """Inverse control. A fix that made everything non-green would satisfy every
    assertion above and still be a lie."""
    sid = "gen1747000000pas"
    # status "rejected" on purpose: the SYNTHESIS gate rejected this one. The
    # live gate is the authority, in both directions.
    _mk_strategy(sid, status="rejected", rigor_verdict={"passing": False})
    _mk_passport(sid, passes_rigor_gate=False, sharpe_ratio=1.8)
    _mk_backtest(sid, returns=_passing_returns())

    row = await _row(sid)
    assert row["rigor_gate_status"] == "pass"
    assert row["passes_rigor_gate"] is True


# ── 6. The numbers come from the same run as the badge ─────────────────────


async def test_rigor_numbers_come_from_the_live_run_not_the_stored_passport():
    """Both directions of the #1187 rule on this route.

    A graded row's DSR/PBO/OOS/dsr_p are the LIVE run's, so they cannot be the
    generation-time fusion numbers seeded on the passport; an ungraded row's are
    absent, so no row ever prints a confident statistic beside a non-verdict.
    """
    graded = "gen1747numsgrade"
    _mk_strategy(graded, status="live", rigor_verdict={"passing": True})
    _mk_passport(graded, passes_rigor_gate=True, sharpe_ratio=0.4)
    _mk_backtest(graded, returns=_failing_returns())

    ungraded = "gen1747numspend0"
    _mk_strategy(ungraded, status="live", rigor_verdict={"passing": True, "dsr": 0.8})
    _mk_passport(ungraded, passes_rigor_gate=True, sharpe_ratio=1.4)

    rows = {r["id"]: r for r in await _generated_rows()}

    g = rows[graded]
    assert g["rigor_gate_status"] == "fail"
    assert g["deflated_sharpe_ratio"] is not None
    # The seeded passport/fusion values, which must NOT be what is served.
    assert g["deflated_sharpe_ratio"] != 0.87
    assert g["dsr_p_value"] != 0.99
    assert g["pbo_score"] == 0.05, "criterion-4 PBO is the persisted per-row value the deploy gate uses"
    assert g["sharpe_ratio"] == 0.031, (
        "display Sharpe is the GRADED backtest row's own number, not the passport snapshot (0.4)"
    )

    u = rows[ungraded]
    assert u["rigor_gate_status"] == "pending"
    for field in (
        "deflated_sharpe_ratio",
        "dsr_p_value",
        "pbo_score",
        "out_of_sample_sharpe",
        "sharpe_ratio",
    ):
        assert u[field] is None, f"{field} must be absent on a row no gate graded"


# ── 6b. The display Sharpe describes the run the gate graded ───────────────


async def test_display_sharpe_is_the_graded_backtest_rows_own_number():
    """The headline Sharpe must come from the SAME ``backtest_results`` row the
    gate read its returns from — not ``strategy_passports.sharpe_ratio``.

    That column is a denormalised snapshot with no foreign key to
    ``backtest_results`` (``scripts/archive_backtest_results.py``: ~155 backtest
    rows per strategy), so serving it prints a Sharpe from one run beside a DSR
    computed from another. Seeded here as two backtest rows for one strategy: an
    older PASSING series at Sharpe 9.9 and a newer FAILING one at 0.031, with a
    passport snapshot of 1.4. Exactly one of those three numbers describes the
    series the gate graded.
    """
    sid = "gen1747sharpevin"
    _mk_strategy(sid, status="live", rigor_verdict={"passing": True})
    _mk_passport(sid, passes_rigor_gate=True, sharpe_ratio=1.4)
    # Older row first: same window (`created_at DESC, id DESC`) in
    # get_all_daily_returns and latest_backtests_by_strategy, so the row
    # inserted SECOND is the one both readers resolve to.
    _mk_backtest(sid, returns=_passing_returns(), sharpe_ratio=9.9, content_hash=f"bt_{sid}_old")
    _mk_backtest(sid, returns=_failing_returns(), sharpe_ratio=0.031, content_hash=f"bt_{sid}_new")

    row = await _row(sid)
    # The gate graded the NEWER series, so the badge is the newer verdict...
    assert row["rigor_gate_status"] == "fail"
    # ...and the Sharpe beside it is that same row's, not the passport's 1.4 and
    # not the superseded run's 9.9.
    assert row["sharpe_ratio"] == 0.031, (
        f"served sharpe_ratio={row['sharpe_ratio']!r} — the display Sharpe and the rigor "
        "numbers are describing two different backtest runs"
    )


# ── 7. Page cost is constant in the number of rows ─────────────────────────


def _capture_sql(engine) -> tuple[list[str], object]:
    """Attach a before_cursor_execute listener; return (statements, detach).

    Same engine-boundary counter as test_publishable_strategy_ids.py — measuring
    at the engine is what makes "batched" checkable rather than asserted.
    """
    statements: list[str] = []

    def _on_exec(_conn, _cursor, statement, _params, _context, _executemany) -> None:
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", _on_exec)

    def _detach() -> None:
        event.remove(engine, "before_cursor_execute", _on_exec)

    return statements, _detach


def _selects_touching(statements: list[str], table: str) -> list[str]:
    return [s for s in statements if s.lstrip().upper().startswith("SELECT") and table in s]


async def _sql_for_page(n_rows: int, *, tag: str) -> list[str]:
    for i in range(n_rows):
        sid = f"gen1747{tag}{i:05d}"
        _mk_strategy(sid, status="live", rigor_verdict={"passing": True})
        _mk_passport(sid, passes_rigor_gate=False, sharpe_ratio=0.4)
        _mk_backtest(sid, returns=_failing_returns())

    statements, detach = _capture_sql(db.engine)
    try:
        rows = await _generated_rows()
    finally:
        detach()
    assert len(rows) >= n_rows
    return statements


async def test_badge_overlay_is_batched_not_per_row():
    """The passport read and the persisted-context reads must not scale with the
    page. Compared at two page sizes rather than pinned to a magic number, so
    the assertion survives an unrelated query being added elsewhere on the route
    but cannot survive an N+1 in the overlay."""
    small = await _sql_for_page(1, tag="cst")
    small_passports = len(_selects_touching(small, "strategy_passports"))
    small_backtests = len(_selects_touching(small, "backtest_results"))

    big = await _sql_for_page(5, tag="cbg")  # 6 rows on the page now, not 1
    assert len(_selects_touching(big, "strategy_passports")) == small_passports
    assert len(_selects_touching(big, "backtest_results")) == small_backtests
    # And the counter can actually see an N+1: it saw more than zero of each.
    assert small_passports >= 1
    assert small_backtests >= 1
