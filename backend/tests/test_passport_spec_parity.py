"""The passport card's executable fields come from the validated DSL spec (#1769).

Owner dogfood, 2026-09-01, strategy ``8411f2d044aeacc6`` read back through
``GET /api/strategies/{id}``:

    field               | methodology card | validated strategy_spec
    rebalance_frequency | weekly           | monthly
    position_sizing     | equal_weight     | full_invested_when_in_market

Two descriptions of two different strategies on one passport. The backtest ran
the DSL; the card did not come from it and was never checked against it.

The guard has to close BOTH halves, because the bug had two independent causes
and fixing either one alone leaves the observed card wrong:

* the WRITE half — the generation pipeline's passport writers never passed a
  cadence or a sizing rule at all, so the row took its column defaults
  (``weekly`` / ``equal_weight``), and the post-backtest rebuild had no writer
  for those two columns either, so a corrected value would have been reverted;
* the READ half — every row written before the write fix is still in an
  append-only table, so a read path that trusts the stored columns keeps serving
  the same contradiction to the same user.

Each test below states the mutation that turns it red.
"""

from __future__ import annotations

import json
import logging

import pytest

# The prose/spec disagreement the issue reported, verbatim in shape.
PROSE_REBALANCE = "weekly"
PROSE_SIZING = "equal_weight"
PROSE_UNIVERSE = ["QQQ", "SHV", "TLT"]

SPEC_UNIVERSE = ["QQQ", "SHV"]
DISAGREEING_SPEC = {
    "name": "Volatility-Relief Swing",
    "asset_universe": SPEC_UNIVERSE,
    "rebalance_frequency": "monthly",
    "entry": {"gt": ["close", "sma_200"]},
    "exit": {"lt": ["close", "sma_200"]},
    "position_sizing": {"type": "full_invested_when_in_market"},
    "source_arxiv_ids": ["2401.00001", "2401.00002"],
}


@pytest.fixture
def _tmp_db(tmp_path):
    """Genuinely-isolated per-test SQLite (see tests/db_isolation.py)."""
    from tests.db_isolation import redirect_to_tmp_sqlite

    yield from redirect_to_tmp_sqlite(tmp_path)


def _seed_disagreeing_row(sid: str) -> None:
    """A row exactly as the pipeline wrote it BEFORE this fix.

    The passport carries the prose values; the strategy_store row carries the
    validated spec that actually ran. This is the shape of every generated row
    already in production, and it is what the read-time reconciliation exists
    for — the table is append-only, so these rows are not going away.
    """
    from archimedes.db import get_session
    from archimedes.models.strategy_passport_record import StrategyPassportRecord
    from archimedes.models.strategy_store import StrategyRecord

    with get_session() as session:
        session.add(
            StrategyRecord(
                id=sid,
                content_hash=("0x" + sid).ljust(66, "0"),
                generation_method="debate",
                source_papers="[]",
                strategy_name="Volatility-Relief Swing",
                thesis="prose thesis",
                asset_universe=json.dumps(PROSE_UNIVERSE),
                risk_profile="moderate",
                status="candidate",
                is_example=False,
                is_published=True,
                strategy_spec=json.dumps(DISAGREEING_SPEC),
            )
        )
        session.add(
            StrategyPassportRecord(
                id=sid,
                generation_method="debate",
                methodology_summary="Prose methodology",
                asset_universe=json.dumps(PROSE_UNIVERSE),
                position_sizing=PROSE_SIZING,
                rebalance_frequency=PROSE_REBALANCE,
                status="candidate",
                regime_tag="regime_neutral",
                passes_rigor_gate=False,
            )
        )
        session.commit()


# ── READ: an existing row whose stored card disagrees with its spec ──────────


@pytest.mark.asyncio
async def test_served_detail_response_shows_the_spec_not_the_prose(_tmp_db):
    """THE guard the issue asks for, on the surface the owner actually read.

    MUTATION: serve the prose — in ``_passport_to_strategy_response``, put
    ``record.rebalance_frequency`` / ``record.position_sizing`` /
    ``json.loads(record.asset_universe)`` back on the response instead of the
    reconciled ``_card`` values. This test then reads ``weekly`` /
    ``equal_weight`` / the 3-name universe off a strategy whose spec says
    ``monthly`` / ``full_invested_when_in_market`` / 2 names, and fails on all
    three.
    """
    from archimedes.main import app
    from httpx import ASGITransport, AsyncClient

    sid = "parity-read-detail"
    _seed_disagreeing_row(sid)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/api/strategies/{sid}")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["rebalance_frequency"] == "monthly"
    assert body["position_sizing"] == "full_invested_when_in_market"
    assert body["asset_universe"] == SPEC_UNIVERSE
    # The prose values must be GONE, not merely outvoted somewhere else on the
    # payload — a card that shows both is the defect restated.
    assert PROSE_REBALANCE not in (body["rebalance_frequency"], body["position_sizing"])
    assert "TLT" not in body["asset_universe"]


@pytest.mark.asyncio
async def test_the_disagreement_is_logged_naming_the_strategy_id(_tmp_db, caplog):
    """A card that has been wrong for months is a fact about the data.

    Serving the right value silently would leave no way to find the rows it
    happened to, so the reconciliation logs at WARNING, naming the id and both
    sides of every field that differed.

    MUTATION: drop the ``logger.warning`` in ``reconcile_card_fields`` (or
    demote it to ``debug``) — the id and the ``stored ... != spec ...`` pairs
    stop appearing and this fails.
    """
    from archimedes.main import app
    from httpx import ASGITransport, AsyncClient

    sid = "parity-read-logged"
    _seed_disagreeing_row(sid)

    with caplog.at_level(logging.WARNING, logger="archimedes.services.passport_spec_parity"):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get(f"/api/strategies/{sid}")

    assert resp.status_code == 200, resp.text
    warnings = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    named = [m for m in warnings if sid in m and "disagreed with its validated DSL spec" in m]
    assert named, f"no warning named {sid}: {warnings}"
    assert "rebalance_frequency" in named[0]
    assert "position_sizing" in named[0]
    assert "asset_universe" in named[0]


@pytest.mark.asyncio
async def test_a_row_whose_spec_agrees_logs_nothing(_tmp_db, caplog):
    """The warning must mean something. If it fired for rows that agree, it
    would fire for every generated row and be worth nothing.

    MUTATION: log unconditionally in ``reconcile_card_fields`` instead of only
    when ``disagreements`` is non-empty.
    """
    from archimedes.db import get_session
    from archimedes.main import app
    from archimedes.models.strategy_passport_record import StrategyPassportRecord
    from httpx import ASGITransport, AsyncClient

    sid = "parity-read-agrees"
    _seed_disagreeing_row(sid)
    with get_session() as session:
        row = session.query(StrategyPassportRecord).filter_by(id=sid).first()
        row.rebalance_frequency = "monthly"
        row.position_sizing = "full_invested_when_in_market"
        row.asset_universe = json.dumps(SPEC_UNIVERSE)
        session.commit()

    with caplog.at_level(logging.WARNING, logger="archimedes.services.passport_spec_parity"):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get(f"/api/strategies/{sid}")

    assert resp.status_code == 200, resp.text
    assert not [r for r in caplog.records if "disagreed" in r.getMessage()]


# ── WRITE: the pipeline never writes a card its own spec contradicts ─────────


def _disagreeing_candidate():
    """A candidate carrying the prose universe AND the spec that disagrees.

    ``asset_universe`` is deliberately set to the 3-name prose list rather than
    the spec's 2 — on the debate path those already agree (debate_engine reads
    the universe straight off the spec), so seeding them equal would let a
    universe regression pass unnoticed.
    """
    from archimedes.agents.generation_pipeline import _CandidateResult

    return _CandidateResult(
        candidate_id="cand_parity",
        strategy_name="Volatility-Relief Swing",
        thesis="prose thesis",
        asset_universe=list(PROSE_UNIVERSE),
        source_papers=[{"arxiv_id": "2401.00001", "title": "A paper"}],
        weights={},
        reasoning="",
        rigor_verdict={"passing": False},
        passes_rigor=False,
        generation_method="debate",
        strategy_spec=dict(DISAGREEING_SPEC),
    )


@pytest.mark.asyncio
async def test_persist_writes_the_specs_card_fields_not_the_candidates_prose(_tmp_db):
    """The write half: the row the pipeline creates already agrees with its spec.

    MUTATION: revert ``_persist_candidate``'s passport block to
    ``asset_universe=c.asset_universe or []`` with no cadence and no sizing —
    the row takes the ``weekly`` / ``equal_weight`` column defaults and the
    3-name prose universe, and all three assertions fail.
    """
    from archimedes.agents.generation_pipeline import _persist_candidate
    from archimedes.api.generate_schemas import GenerateBrief
    from archimedes.db import get_session
    from archimedes.models.strategy_passport_record import StrategyPassportRecord

    strategy_id, _ = await _persist_candidate(
        _disagreeing_candidate(),
        GenerateBrief(intent="relieve volatility"),
    )

    with get_session() as session:
        row = session.query(StrategyPassportRecord).filter_by(id=strategy_id).first()
        assert row is not None
        assert row.rebalance_frequency == "monthly"
        assert row.position_sizing == "full_invested_when_in_market"
        assert json.loads(row.asset_universe) == SPEC_UNIVERSE


def test_the_post_backtest_rebuild_repairs_a_stale_card(_tmp_db):
    """The rebuild half, on the row shape that actually exists in production.

    ``_refresh_passport_real_metrics`` re-declares the whole passport and ingests
    it with ``force_update=True``, which routes to ``_update_record`` — and
    ``_update_record`` had no writer for ``position_sizing`` or
    ``rebalance_frequency`` at all. So the update path could neither write a
    correct value nor repair an incorrect one: an already-stored ``weekly`` /
    ``equal_weight`` survived every re-backtest untouched.

    Seeded as a PRE-FIX row on purpose. Persisting fresh and refreshing would
    pass with the writers deleted (the initial insert already lands ``monthly``
    and an update that writes nothing cannot disturb it) — the scenario has to
    start from the wrong value for the repair to be observable.

    MUTATION: delete the ``record.position_sizing`` /
    ``record.rebalance_frequency`` writes from
    ``passport_loader._update_record`` — the row stays ``weekly`` /
    ``equal_weight`` after a refresh that rewrote every metric beside them.
    """
    from archimedes.agents.generation_pipeline import _refresh_passport_real_metrics
    from archimedes.db import get_session
    from archimedes.models.strategy_passport_record import StrategyPassportRecord

    sid = "parity-rebuild-repairs"
    _seed_disagreeing_row(sid)
    c = _disagreeing_candidate()

    class _Result:
        sharpe_ratio = 0.70
        sortino_ratio = 0.9
        cagr = 0.08
        max_drawdown = 0.12
        calmar_ratio = 0.6
        correlation_to_spy = 0.4
        total_trades = 20
        backtest_start = None
        backtest_end = None
        deflated_sharpe_ratio = 0.08
        dsr_p_value = 0.42
        num_trials_in_selection = 3
        pbo_score = 0.3
        out_of_sample_sharpe = 0.2

    with get_session() as session:
        _refresh_passport_real_metrics(session, c, sid, _Result(), passes_rigor_gate=False, n_obs=500)
        session.commit()

    with get_session() as session:
        row = session.query(StrategyPassportRecord).filter_by(id=sid).first()
        assert row.sharpe_ratio == 0.70, "the refresh must actually have run"
        assert row.rebalance_frequency == "monthly"
        assert row.position_sizing == "full_invested_when_in_market"
        assert json.loads(row.asset_universe) == SPEC_UNIVERSE


# ── The coupling this all rests on ──────────────────────────────────────────


def test_position_sizing_enum_covers_every_dsl_sizing_type():
    """``PositionSizing`` must stay a SUPERSET of the DSL's closed vocabulary.

    ``StrategyPassportRecord.to_strategy_passport`` coerces the stored column
    back through this enum, so a DSL sizing type with no member is not
    "unsupported" — it is a ``ValueError`` on read for every row that uses it.
    Before #1769 the enum was missing ``full_invested_when_in_market`` and
    ``volatility_target``, which is why nothing had ever written a spec-derived
    sizing rule to the column.

    MUTATION: delete ``FULL_INVESTED_WHEN_IN_MARKET`` from
    ``models/strategy.py`` — this fails naming it.
    """
    from archimedes.models.strategy import PositionSizing
    from archimedes.services.strategy_dsl import POSITION_SIZING_TYPES

    missing = sorted(POSITION_SIZING_TYPES - {m.value for m in PositionSizing})
    assert not missing, f"DSL sizing types with no PositionSizing member: {missing}"


def test_an_unvalidatable_spec_never_overrides_the_stored_card():
    """The claim is "the card shows the spec the backtest RAN".

    A blob the DSL validator rejects was never run by anything, so promoting its
    fields would swap one unverified string for another and call the result
    derived. This is the adversarial input for the whole module: a spec that
    LOOKS like it disagrees but cannot be trusted to.

    MUTATION: make ``card_fields_from_spec`` read the three keys off the dict
    instead of going through ``validate_strategy_spec`` — the junk cadence below
    reaches the card and this fails.
    """
    from archimedes.services.passport_spec_parity import card_fields_from_spec, reconcile_card_fields

    junk = {
        "name": "Not A Spec",
        "asset_universe": ["QQQ"],
        "rebalance_frequency": "hourly",  # not in REBALANCE_FREQUENCIES
        "entry": {"gt": ["close", "sma_200"]},
        "exit": {"lt": ["close", "sma_200"]},
        "position_sizing": {"type": "full_invested_when_in_market"},
        "source_arxiv_ids": ["2401.00001"],
    }
    assert card_fields_from_spec(junk, strategy_id="junk") is None
    assert reconcile_card_fields(
        "junk",
        junk,
        asset_universe=["SPY"],
        rebalance_frequency="weekly",
        position_sizing="equal_weight",
    ) == {
        "asset_universe": ["SPY"],
        "rebalance_frequency": "weekly",
        "position_sizing": "equal_weight",
    }


def test_no_spec_leaves_the_stored_card_untouched():
    """The fixture / buy-and-hold path stores no spec, and curated rows have
    none either. Inventing a cadence for them would be the same defect with the
    sign flipped.

    MUTATION: have ``reconcile_card_fields`` fall back to the DSL defaults
    instead of returning the stored values when ``derived is None``.
    """
    from archimedes.services.passport_spec_parity import reconcile_card_fields

    for spec in (None, {}, [], "not a dict", 42):
        assert reconcile_card_fields(
            "no-spec",
            spec,
            asset_universe=["SPY", "GLD"],
            rebalance_frequency="daily",
            position_sizing="risk_parity",
        ) == {
            "asset_universe": ["SPY", "GLD"],
            "rebalance_frequency": "daily",
            "position_sizing": "risk_parity",
        }
