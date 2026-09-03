"""Deploy at will, with the verdict of record beside the forward record (#1764).

Owner decision (Dan, 2026-09-01): any strategy the caller can see may be
paper-deployed — gate ``pass``, ``fail``, ``pending`` or ``degenerate`` — because
a failed strategy that performs poorly forward is what validates the gate, and a
passed one that tracks its backtest validates it too. That freedom is only
honest if the verdict travels with the numbers, so what this file pins is:

  1. THE PAYLOAD CARRIES THE VERDICT. ``deployment_summary`` — and therefore
     every ``/api/paper/deployments`` read — carries ``rigor_gate_status``,
     ``graded_at``, ``passes_rigor_gate`` and ``gate_version``. Mutation: delete
     any one of those keys from the payload and the first test here goes red.
  2. IT IS A READ, NEVER A RECOMPUTE. The stored verdict is served even when it
     CONTRADICTS the metrics sitting beside it on the same row — a read-time
     re-grade is the #1746/#1747 split arriving on a new surface
     (docs/adr/rigor-verdict-of-record.md).
  3. ``passes_rigor_gate`` IS DERIVED FROM THE FOUR-STATE, not copied from the
     stored boolean, so a legacy row whose two columns were written apart cannot
     be served apart.
  4. IT FAILS CLOSED. No passport row, or a broken read, degrades to "pending" —
     never to a fabricated pass.
  5. DEPLOY IS AT WILL. A gate-FAILED strategy deploys through the real route
     with a 201, and its payload says ``fail`` on the way back out.
  6. THE TWO READ SIDES AGREE. The paper surface's ungraded shape and the
     Library page's (``strategies_routes._UNGRADED_VERDICT_FIELDS``) describe an
     ungraded row identically, and the UI's ``legacy-derived`` literal is the
     backend's.

Hermetic: in-memory SQLite for the service laws, tmp SQLite + a stubbed session
for the route laws — the same shapes ``tests/services/test_paper_trading.py``
and ``tests/test_paper_marks_routes.py`` already use.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from archimedes.api import account_auth, paper_routes
from archimedes.models.chat import Base
from archimedes.models.strategy_passport_record import StrategyPassportRecord
from archimedes.services.paper_trading import create_deployment, deployment_summary
from archimedes.services.passport_loader import UNGRADED_RIGOR_VERDICT, stored_rigor_verdict
from archimedes.services.rigor_gate_version import LEGACY_DERIVED
from fastapi import FastAPI
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from tests.db_isolation import redirect_to_tmp_sqlite

_SPEC = {
    "name": "verdict probe",
    "asset_universe": ["SPY"],
    "rebalance_frequency": "daily",
    "entry": {"gt": ["momentum_20", 0]},
    "exit": {"lt": ["momentum_20", -0.99]},
    "position_sizing": {"type": "full_invested_when_in_market"},
    "source_arxiv_ids": ["0706.1497"],
    "look_ahead_safe": True,
}

_DEPLOY = date(2026, 8, 1)
_GRADED_AT = datetime(2026, 8, 30, 11, 22, 33)


def _session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return Session(engine)


def _passport(session, strategy_id: str, **kwargs) -> StrategyPassportRecord:
    """A minimal passport row. Every rigor column the caller does not name is
    left at its column default, so a test only states what it is testing."""
    row = StrategyPassportRecord(id=strategy_id, methodology_summary="probe", **kwargs)
    session.add(row)
    session.flush()
    return row


# ── 1. The payload carries the verdict ───────────────────────────────────────


def test_the_deployment_payload_carries_the_stored_verdict_and_its_provenance():
    """The whole issue in one assertion: a paper deployment of a gate-FAILED
    strategy reports the failure on the same payload as the return figure.

    Mutation-verified: dropping ``**stored_rigor_verdict(...)`` from
    ``deployment_summary``'s returned dict fails this on the first key."""
    with _session() as s:
        _passport(
            s,
            "s-fail",
            rigor_gate_status="fail",
            passes_rigor_gate=False,
            graded_at=_GRADED_AT,
            gate_version="gate-v1-deadbeefdeadbeef",
        )
        dep = create_deployment(s, strategy_id="s-fail", spec_dict=_SPEC, owner_wallet=None, deployed_at=_DEPLOY)

        summary = deployment_summary(s, dep)

        assert summary["rigor_gate_status"] == "fail"
        assert summary["passes_rigor_gate"] is False
        assert summary["graded_at"] == "2026-08-30T11:22:33"
        assert summary["gate_version"] == "gate-v1-deadbeefdeadbeef"
        # And beside them, unchanged: the forward record the verdict qualifies.
        assert summary["total_return"] == 0.0
        assert summary["days"] == 0


def test_a_passing_strategys_payload_says_pass_with_the_same_four_keys():
    with _session() as s:
        _passport(
            s,
            "s-pass",
            rigor_gate_status="pass",
            passes_rigor_gate=True,
            graded_at=_GRADED_AT,
            gate_version="gate-v1-0123456789abcdef",
        )
        dep = create_deployment(s, strategy_id="s-pass", spec_dict=_SPEC, owner_wallet=None, deployed_at=_DEPLOY)

        summary = deployment_summary(s, dep)

        assert summary["rigor_gate_status"] == "pass"
        assert summary["passes_rigor_gate"] is True
        assert summary["graded_at"] == "2026-08-30T11:22:33"


@pytest.mark.parametrize("status", ["pass", "fail", "pending", "degenerate"])
def test_every_one_of_the_four_states_survives_the_payload(status):
    """All four, not just the two interesting ones: the UI renders a distinct,
    non-green answer for ``pending`` and ``degenerate``, and it can only do that
    if the backend stops collapsing them into a boolean on the way out."""
    with _session() as s:
        _passport(s, f"s-{status}", rigor_gate_status=status, passes_rigor_gate=(status == "pass"))
        dep = create_deployment(s, strategy_id=f"s-{status}", spec_dict=_SPEC, owner_wallet=None, deployed_at=_DEPLOY)

        assert deployment_summary(s, dep)["rigor_gate_status"] == status


# ── 2. A read, never a recompute ─────────────────────────────────────────────


def test_the_stored_verdict_wins_over_the_metrics_sitting_beside_it():
    """The verdict of record is what was decided at backtest time, not what a
    reader would conclude from the numbers today.

    This row is deliberately self-contradictory: a stored ``fail`` beside a DSR
    p-value and an OOS Sharpe that any live re-grade would wave through. Serving
    ``pass`` here would mean the paper card and the strategy's own passport can
    show two different verdicts for the same strategy — exactly the split
    #1746/#1747 closed, arriving on a new surface."""
    with _session() as s:
        _passport(
            s,
            "s-contradictory",
            rigor_gate_status="fail",
            passes_rigor_gate=False,
            graded_at=_GRADED_AT,
            deflated_sharpe_ratio=2.4,
            dsr_p_value=0.0001,
            pbo_score=0.01,
            out_of_sample_sharpe=1.9,
        )
        dep = create_deployment(
            s, strategy_id="s-contradictory", spec_dict=_SPEC, owner_wallet=None, deployed_at=_DEPLOY
        )

        summary = deployment_summary(s, dep)

        assert summary["rigor_gate_status"] == "fail"
        assert summary["passes_rigor_gate"] is False


# ── 3. The boolean is derived, not copied ────────────────────────────────────


def test_passes_rigor_gate_is_derived_from_the_four_state_not_copied():
    """A pre-coupling row can carry ``passes_rigor_gate=True`` beside a
    ``fail`` four-state (the two columns had different writers before #1792).
    The read side must not serve them apart — it re-derives the boolean, so the
    fail-closed answer wins on a row that disagrees with itself."""
    with _session() as s:
        _passport(s, "s-split", rigor_gate_status="fail", passes_rigor_gate=True)
        dep = create_deployment(s, strategy_id="s-split", spec_dict=_SPEC, owner_wallet=None, deployed_at=_DEPLOY)

        summary = deployment_summary(s, dep)

        assert summary["rigor_gate_status"] == "fail"
        assert summary["passes_rigor_gate"] is False, "the stored boolean must never override the four-state"


# ── 4. Fail closed ───────────────────────────────────────────────────────────


def test_a_strategy_with_no_passport_row_reads_as_ungraded_never_as_a_pass():
    """The honest degraded state. ``passes_rigor_gate`` is None rather than
    False because False is a VERDICT ("the gate ran and this lost") and no gate
    ran — the same distinction ``pending`` makes in words."""
    with _session() as s:
        dep = create_deployment(s, strategy_id="s-unknown", spec_dict=_SPEC, owner_wallet=None, deployed_at=_DEPLOY)

        summary = deployment_summary(s, dep)

        assert summary["rigor_gate_status"] == "pending"
        assert summary["passes_rigor_gate"] is None
        assert summary["graded_at"] is None
        assert summary["gate_version"] is None


def test_a_broken_verdict_read_degrades_to_pending_and_never_raises():
    """The ledger is the user's track record; a passport read that blows up must
    not take a correct ledger down with it. What is never produced on this path
    is a ``pass``."""

    class _Boom:
        def query(self, *_a, **_k):
            raise RuntimeError("passport table is unreadable")

        def rollback(self):
            return None

    verdict = stored_rigor_verdict(_Boom(), "s-any")

    assert verdict == UNGRADED_RIGOR_VERDICT
    assert verdict["passes_rigor_gate"] is not True


def test_a_row_whose_status_column_is_empty_reads_as_pending(monkeypatch):
    """The defensive arm, mirroring ``strategies_routes._passport_verdicts_for``.

    ``strategy_passports.rigor_gate_status`` is NOT NULL with a ``pending``
    server default, so this shape is unreachable through the ORM today — the row
    is stubbed rather than written, and the test is honest about why. What it
    pins is the direction of the fallback: an absent four-state must read as "no
    gate has answered", never as a failure and never as a pass."""

    class _NullStatusRow:
        rigor_gate_status = None
        graded_at = None
        gate_version = None

    monkeypatch.setattr("archimedes.services.passport_loader.get_passport", lambda *_a, **_k: _NullStatusRow())

    verdict = stored_rigor_verdict(object(), "s-null")

    assert verdict["rigor_gate_status"] == "pending"
    assert verdict["passes_rigor_gate"] is False


# ── 6. The two read sides agree ──────────────────────────────────────────────


def test_the_paper_and_library_surfaces_describe_an_ungraded_row_identically():
    """Anti-drift. ``strategies_routes._UNGRADED_VERDICT_FIELDS`` (Library) and
    ``passport_loader.UNGRADED_RIGOR_VERDICT`` (paper) are two constants for one
    idea; a change to either that made them disagree would put two different
    answers to "has a gate graded this?" on two pages of the same app."""
    from archimedes.api.strategies_routes import _UNGRADED_VERDICT_FIELDS

    shared = set(UNGRADED_RIGOR_VERDICT) & set(_UNGRADED_VERDICT_FIELDS)
    assert shared == {"passes_rigor_gate", "rigor_gate_status", "graded_at"}
    for key in shared:
        assert UNGRADED_RIGOR_VERDICT[key] == _UNGRADED_VERDICT_FIELDS[key], key


def test_the_ui_legacy_derived_literal_is_the_backends():
    """``ui/src/paperCopy.js`` appends a "no gate run produced this" note to any
    verdict whose ``gate_version`` is the migration marker. It can only do that
    by matching a string literal, so the two literals are pinned together — a
    rename on one side would otherwise make the UI silently present a
    legacy-derived verdict as a real grade."""
    source = (Path(__file__).resolve().parents[2] / "ui/src/paperCopy.js").read_text(encoding="utf-8")

    assert f"export const LEGACY_DERIVED_GATE_VERSION = '{LEGACY_DERIVED}'" in source


# ── 5. Deploy is at will, through the real route ─────────────────────────────


@pytest.fixture(autouse=True)
def _tmp_db(tmp_path):
    yield from redirect_to_tmp_sqlite(tmp_path)


@pytest.fixture()
def app(monkeypatch):
    application = FastAPI()
    application.middleware("http")(account_auth.better_auth_session_middleware)
    application.include_router(paper_routes.paper_router)
    # The ownership/visibility gate and the replay are exercised in their own
    # suites; what this file needs from the route is the PAYLOAD.
    monkeypatch.setattr(paper_routes, "_spec_for_strategy", lambda *_a, **_k: dict(_SPEC))
    monkeypatch.setattr(paper_routes, "advance_deployment", lambda *_a, **_k: {"appended": 0, "drift": 0})

    async def _session_stub(_request):
        return {
            "user": {"id": "u1", "name": "u1", "email": "u1@example.com", "emailVerified": True},
            "session": {"id": "s-u1", "expiresAt": (datetime.now(UTC) + timedelta(hours=1)).isoformat()},
        }

    monkeypatch.setattr(account_auth, "_fetch_session", _session_stub)
    monkeypatch.setattr(paper_routes, "get_linked_wallet_address", lambda _r: None)
    return application


def _client(app):
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers={"cookie": "better-auth.session_token=opaque", "host": "archimedes-arc.com"},
    )


def _seed_failed_passport(strategy_id: str) -> None:
    from archimedes.db import get_session

    with get_session() as session:
        session.add(
            StrategyPassportRecord(
                id=strategy_id,
                methodology_summary="probe",
                rigor_gate_status="fail",
                passes_rigor_gate=False,
                graded_at=_GRADED_AT,
                gate_version=LEGACY_DERIVED,
            )
        )
        session.commit()


@pytest.mark.asyncio
async def test_a_gate_failed_strategy_deploys_and_says_so_on_the_way_back_out(app):
    """Deploy at will: the route has no rigor precondition (it checks ownership
    and that the stored spec validates, and nothing else — docs/claims-ledger.md
    depends on that being true), and the 201 body carries the failure."""
    _seed_failed_passport("s-fail")
    async with _client(app) as client:
        created = await client.post("/api/paper/deployments", json={"strategy_id": "s-fail"})

        assert created.status_code == 201
        body = created.json()
        assert body["rigor_gate_status"] == "fail"
        assert body["passes_rigor_gate"] is False
        assert body["graded_at"] == "2026-08-30T11:22:33"
        assert body["gate_version"] == LEGACY_DERIVED


@pytest.mark.asyncio
async def test_every_read_route_carries_the_verdict_not_just_the_create(app):
    """The list route is what /app/paper renders from, and the detail route is
    what a deep link renders from. A verdict on only one of them would leave a
    performance number standing alone on the other."""
    _seed_failed_passport("s-fail")
    async with _client(app) as client:
        dep_id = (await client.post("/api/paper/deployments", json={"strategy_id": "s-fail"})).json()["deployment_id"]

        listed = (await client.get("/api/paper/deployments")).json()["deployments"]
        detail = (await client.get(f"/api/paper/deployments/{dep_id}")).json()

        for payload in (listed[0], detail):
            assert payload["rigor_gate_status"] == "fail"
            assert payload["passes_rigor_gate"] is False
            assert payload["graded_at"] == "2026-08-30T11:22:33"
            assert "gate_version" in payload
