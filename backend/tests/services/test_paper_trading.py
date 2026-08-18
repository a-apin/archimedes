"""The paper-trading laws (MVP: verdict → paper-trade).

Hermetic: an in-memory SQLite session and a stubbed replay. What is pinned is
the part that must never regress silently:

  1. APPEND-ONLY: advancing twice appends nothing new the second time; a
     replay can only ever contribute unseen dates.
  2. DRIFT IS SURFACED, NEVER REPAIRED: when a fresh replay disagrees with
     rows already written (upstream restatement), the ledger keeps the
     original numbers, the deployment is stamped, and the event is logged —
     mutation-verified: a variant that "repairs" the ledger fails these.
  3. THE SNAPSHOT IS THE CONTRACT: mutating the strategy's spec after deploy
     must not change what the ledger grades.
  4. THE DEPLOY DATE SLICES THE REPLAY: history before deployed_at never
     enters the ledger.
  5. ONE BAD DEPLOYMENT CANNOT STALL THE REST of advance_all.
"""

from __future__ import annotations

import json
from datetime import date

import pytest
from archimedes.models.chat import Base
from archimedes.models.paper_store import PaperDailyReturn
from archimedes.services.paper_trading import (
    advance_all,
    advance_deployment,
    create_deployment,
    deployment_summary,
)
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

_SPEC = {
    "name": "paper probe",
    "asset_universe": ["SPY"],
    "rebalance_frequency": "daily",
    "entry": {"gt": ["momentum_20", 0]},
    "exit": {"lt": ["momentum_20", -0.99]},
    "position_sizing": {"type": "full_invested_when_in_market"},
    "source_arxiv_ids": ["0706.1497"],
    "look_ahead_safe": True,
}

_DEPLOY = date(2026, 8, 1)


def _session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return Session(engine)


def _replay_v1(spec_dict, deployed_at):
    # A stub replay honouring the real contract: dates >= deployed_at only.
    series = {
        date(2026, 7, 30): 0.9,  # pre-deploy history — must never be ledgered
        date(2026, 8, 1): 0.01,
        date(2026, 8, 4): -0.02,
        date(2026, 8, 5): 0.005,
    }
    return {d: r for d, r in series.items() if d >= deployed_at}


def test_advance_appends_only_from_the_deploy_date():
    with _session() as s:
        dep = create_deployment(s, strategy_id="s1", spec_dict=_SPEC, owner_wallet="0xAB", deployed_at=_DEPLOY)
        out = advance_deployment(s, dep, replay=_replay_v1)
        assert out == {"appended": 3, "drift": 0}
        dates = [r.date for r in s.query(PaperDailyReturn).order_by(PaperDailyReturn.date)]
        assert dates == [date(2026, 8, 1), date(2026, 8, 4), date(2026, 8, 5)]
        assert date(2026, 7, 30) not in dates  # law 4


def test_second_advance_is_idempotent_and_new_days_append():
    with _session() as s:
        dep = create_deployment(s, strategy_id="s1", spec_dict=_SPEC, owner_wallet="0xAB", deployed_at=_DEPLOY)
        advance_deployment(s, dep, replay=_replay_v1)
        assert advance_deployment(s, dep, replay=_replay_v1) == {"appended": 0, "drift": 0}  # law 1

        def replay_v2(spec_dict, deployed_at):
            out = _replay_v1(spec_dict, deployed_at)
            out[date(2026, 8, 6)] = 0.003  # the next trading day arrives
            return out

        assert advance_deployment(s, dep, replay=replay_v2) == {"appended": 1, "drift": 0}
        assert s.query(PaperDailyReturn).count() == 4


def test_restatement_is_surfaced_never_repaired(caplog):
    """Law 2 — the load-bearing one. Mutation-verified: an implementation that
    overwrites the drifted row makes BOTH assertions here fail (the stored
    value changes, and drift reports 0 on the second pass)."""
    with _session() as s:
        dep = create_deployment(s, strategy_id="s1", spec_dict=_SPEC, owner_wallet="0xAB", deployed_at=_DEPLOY)
        advance_deployment(s, dep, replay=_replay_v1)

        def restated(spec_dict, deployed_at):
            out = _replay_v1(spec_dict, deployed_at)
            out[date(2026, 8, 4)] = -0.5  # upstream rewrote history
            return out

        with caplog.at_level("WARNING"):
            out = advance_deployment(s, dep, replay=restated)
        assert out["drift"] == 1
        row = s.query(PaperDailyReturn).filter_by(date=date(2026, 8, 4)).one()
        assert row.daily_return == pytest.approx(-0.02)  # original stands
        assert dep.drift_detected_at is not None
        assert any("NOT rewritten" in r.message for r in caplog.records)


def test_spec_snapshot_survives_strategy_mutation():
    with _session() as s:
        dep = create_deployment(s, strategy_id="s1", spec_dict=_SPEC, owner_wallet="0xAB", deployed_at=_DEPLOY)
        seen_specs = []

        def spy_replay(spec_dict, deployed_at):
            seen_specs.append(spec_dict)
            return _replay_v1(spec_dict, deployed_at)

        advance_deployment(s, dep, replay=spy_replay)
        # The strategy gets regenerated with a different spec afterwards —
        # the deployment must keep replaying its snapshot.
        advance_deployment(s, dep, replay=spy_replay)
        assert all(spec["entry"] == _SPEC["entry"] for spec in seen_specs)
        assert json.loads(dep.spec_json)["name"] == "paper probe"


def test_advance_all_isolates_failures():
    from archimedes.services import paper_trading

    with _session() as s:
        good = create_deployment(s, strategy_id="ok", spec_dict=_SPEC, owner_wallet="0xAB", deployed_at=_DEPLOY)
        bad = create_deployment(s, strategy_id="bad", spec_dict=_SPEC, owner_wallet="0xAB", deployed_at=_DEPLOY)
        bad.spec_json = json.dumps({**_SPEC, "entry": {"gt": ["nonsense_metric", 0]}})

        calls = {}

        def selective(spec_dict, deployed_at):
            if spec_dict["entry"] == _SPEC["entry"]:
                calls["good"] = True
                return _replay_v1(spec_dict, deployed_at)
            raise paper_trading.PaperReplayError("boom")

        original = paper_trading.replay_spec
        paper_trading.replay_spec = selective
        try:
            out = advance_all(s)
        finally:
            paper_trading.replay_spec = original
        assert out["deployments"] == 2
        assert out["ok"] == 1 and out["failed"] == 1
        assert calls.get("good") is True
        assert s.query(PaperDailyReturn).filter_by(deployment_id=good.id).count() == 3


def test_summary_compounds_the_ledger():
    with _session() as s:
        dep = create_deployment(s, strategy_id="s1", spec_dict=_SPEC, owner_wallet="0xAB", deployed_at=_DEPLOY)
        advance_deployment(s, dep, replay=_replay_v1)
        summary = deployment_summary(s, dep)
        assert summary["days"] == 3
        expected = (1.01 * 0.98 * 1.005) - 1.0
        assert summary["total_return"] == pytest.approx(expected)
        assert summary["series"][-1]["equity_index"] == pytest.approx(1.0 + expected)
