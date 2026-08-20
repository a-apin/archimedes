"""Durable per-generation cost (#1326) — the measurement has to outlive the job.

#1314 built the meter; its snapshot lived only on the Redis job record, whose
``JOB_TTL`` is 3600s. An hour after a generation finished, the only record of
what it consumed was gone and no surface ever showed it. This file covers the
persistence and the read surfaces that close that gap, and each guard is
exercised with the input that SHOULD fail it:

* **G1 — unknown is never zero.** A strategy nothing measured reads ``None``, a
  record whose measurement will not decode reads ``None``, and neither ever
  becomes an empty measurement or a zero-token record. The other half of the
  same rule is checked too: a genuinely measured zero survives as zero.
* **G2 — the measurement and the price stay in separate columns.** The obvious
  future shortcut is to merge the quote into the snapshot; that raises
  :class:`PricingLeakError` at the write boundary rather than shipping a priced
  ``cost_v1`` record. Demonstrated with the exact merged payload.
* **G3 — the persistence write is deliberately untallied**, because
  ``record_write("generation_costs")`` is itself a pricing-shaped label and the
  meter refuses it. Demonstrated, so the omission reads as the design it is.
* **G4 — instrumentation cannot fail a generation.** A DB that refuses the write
  produces a log line, not a failed run.

Hermetic: tmp-file SQLite via ``redirect_to_tmp_sqlite`` (the ``#1243``
precedent), ``fakeredis`` for the job store (the ``test_generation_cost_meter``
precedent), the fixture generation path (``GENERATION_PIPELINE_FIXTURE=1``). No
Postgres, no Redis, no LLM, no network.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import fakeredis
import pytest
from archimedes.db import get_session
from archimedes.models.generation_cost import (
    GenerationCostRecord,
    generation_cost_for_strategy,
    generation_costs_for_strategies,
    record_generation_cost,
)
from archimedes.services.cost_meter import (
    DATA_KEYED_PATHS,
    CostMeter,
    PricingLeakError,
    assert_measurement_only,
)
from archimedes.services.job_queue import KEY_PREFIX, JobStore

from tests.db_isolation import redirect_to_tmp_sqlite

_SNAPSHOT = {
    "schema": "cost_v1",
    "job_id": "job-1326",
    "wall_seconds": 47.9312,
    "cpu_seconds": 31.4407,
    "cpu_attribution": "process_wide_delta",
    "peak_rss_bytes": 812939264,
    "rss_attribution": "process_high_water",
    "llm": {
        "calls": 17,
        "calls_missing_usage": 0,
        "usage_complete": True,
        "input_tokens": 41234,
        "output_tokens": 5120,
        "total_tokens": 46354,
        "by_model": {"amazon.nova-micro-v1:0": {"calls": 17, "input_tokens": 41234, "output_tokens": 5120}},
    },
    "stages": {
        "candidate_generation": {"wall_seconds": 43.1, "cpu_seconds": 24.9, "runs": 1},
        "debate_backtest": {"wall_seconds": 21.55, "cpu_seconds": 20.9, "runs": 1},
    },
    "writes": {"strategy_store": 1, "strategy_passports": 2},
    "meta": {"pipeline": "debate", "outcome": "done", "candidates_passing_rigor": 0},
}

_QUOTE = {
    "payment_required": True,
    "pricing_model": "flat_v1",
    "price": "$0.150000",
    "asset": "USDC",
    "chain": "eip155:5042002",
    "recipient": "0xRecipient",
    "dry_run": True,
    "how": "POST /api/generate/start ...",
}


@pytest.fixture(autouse=True)
def _tmp_db(tmp_path):
    yield from redirect_to_tmp_sqlite(tmp_path)


# ── G2: the measurement and the price never share a namespace ─────────────


class TestMeasurementOnlyGuard:
    """``assert_measurement_only`` is the persistence-boundary form of the
    meter's write-time label screen: by the time a snapshot reaches storage the
    labels that built it are gone, and the document is the only thing left to
    inspect."""

    def test_a_real_snapshot_passes(self):
        assert_measurement_only(_SNAPSHOT, where="test")
        assert_measurement_only(CostMeter("job-x").snapshot(), where="test")

    @pytest.mark.parametrize(
        ("payload", "why"),
        [
            ({"cost_usd": 0.14}, "top-level pricing key"),
            ({"llm": {"price_per_1k": 0.0002}}, "nested pricing key"),
            ({"stages": {"debate": {"bedrock_spend": 1}}}, "pricing key two levels down"),
            ({"meta": [{"invoice_id": "x"}]}, "pricing key inside a list"),
            ({"writes": {"settlement_fee": 1}}, "pricing-shaped write counter name"),
        ],
    )
    def test_a_pricing_shaped_key_at_any_depth_is_refused(self, payload, why):
        with pytest.raises(PricingLeakError):
            assert_measurement_only(payload, where="test")

    def test_merging_the_quote_into_the_measurement_is_refused_at_the_write(self):
        """The input that SHOULD fail the guard: the obvious future shortcut of
        folding the recorded quote into the snapshot instead of keeping it in its
        own column. It carries ``pricing_model`` and ``price``, so it raises."""
        merged = {**_SNAPSHOT, "quote": _QUOTE}
        with pytest.raises(PricingLeakError):
            assert_measurement_only(merged, where="generation_costs.measurement_json")

        with get_session() as session:
            with pytest.raises(PricingLeakError):
                record_generation_cost(session, job_id="j", strategy_id="s", measurement=merged)
            # Nothing landed — the refusal is at the boundary, not after it.
            assert session.query(GenerationCostRecord).count() == 0

    def test_the_quote_itself_is_NOT_screened_it_is_a_price_by_definition(self):
        """The quote is the recorded price; screening it would make the column
        unwritable. It is kept apart, not sanitized."""
        with get_session() as session:
            record_generation_cost(session, job_id="j-q", strategy_id="s-q", measurement=_SNAPSHOT, quote=_QUOTE)
            session.commit()
            payload = generation_cost_for_strategy(session, "s-q")
        assert payload["quote"]["price"] == "$0.150000"
        assert payload["quote"]["pricing_model"] == "flat_v1"
        # …and the measurement half is still money-free.
        assert_measurement_only(payload["measurement"], where="test")


# ── G2b: model ids are DATA, and screening data drops measurements ────────


class TestModelIdsAreNotScreened:
    """``llm.by_model``'s keys are provider identifiers copied off a response,
    not labels this codebase chose. Screening them would let a vendor's naming
    decide whether a generation's measurement survives at all — and it would
    fail SILENTLY, because the persist runs inside a swallowing ``finally``."""

    def test_the_exemption_is_exactly_one_path_and_it_is_by_model(self):
        assert DATA_KEYED_PATHS == frozenset({"llm.by_model"})

    @pytest.mark.parametrize(
        "model_id",
        [
            "price-aware-model-x",  # marketed on price
            "llama-3-feedback-tuned",  # "fee" inside "feedback" — the ordinary case
            "vendor/cost-optimized-v2",
            "acme-spend-lite",
        ],
    )
    def test_a_model_id_carrying_pricing_words_still_persists(self, model_id):
        """The input that SHOULD NOT fail the guard. Against the unexempted walk
        every one of these raises ``PricingLeakError`` and the durable row is
        dropped, which is the loss this instrumentation exists to prevent."""
        snapshot = {
            **_SNAPSHOT,
            "llm": {
                **_SNAPSHOT["llm"],
                "by_model": {model_id: {"calls": 3, "input_tokens": 10, "output_tokens": 2}},
            },
        }
        assert_measurement_only(snapshot, where="test")

        with get_session() as session:
            record_generation_cost(session, job_id="j-model", strategy_id="s-model", measurement=snapshot)
            session.commit()
            payload = generation_cost_for_strategy(session, "s-model")

        assert payload is not None, "a vendor's model name must never cost us the measurement"
        assert payload["measurement"]["llm"]["by_model"][model_id]["calls"] == 3

    def test_the_exemption_is_one_level_deep_counters_under_a_model_id_are_still_screened(self):
        """The model id is data; the counters hanging off it are ours, and a
        pricing-shaped one there still raises."""
        snapshot = {
            **_SNAPSHOT,
            "llm": {**_SNAPSHOT["llm"], "by_model": {"price-aware-model-x": {"calls": 1, "cost_usd": 0.14}}},
        }
        with pytest.raises(PricingLeakError):
            assert_measurement_only(snapshot, where="test")

    @pytest.mark.parametrize(
        ("payload", "why"),
        [
            ({"stages": {"usd_conversion": {"wall_seconds": 1.0}}}, "stage name"),
            ({"writes": {"settlement_fee": 1}}, "write-counter name"),
            ({"meta": {"unit_price": 1}}, "meta key"),
            ({"llm": {"by_model_pricing": {"m": {}}}}, "a near-miss key that is NOT the exempt path"),
            ({"by_model": {"price-aware-model-x": {}}}, "by_model at the WRONG path is not exempt"),
        ],
    )
    def test_authored_labels_are_still_rejected(self, payload, why):
        """The exemption must not have opened a hole: every caller-authored label
        is screened exactly as before, including a key that merely resembles the
        exempt path and a ``by_model`` that is not under ``llm``."""
        with pytest.raises(PricingLeakError):
            assert_measurement_only(payload, where="test")


# ── G3: why the persistence write is not tallied ──────────────────────────


def test_record_write_generation_costs_is_refused_by_the_meter():
    """The pipeline does NOT tally its own ``generation_costs`` write, and this
    is why: "cost" is pricing vocabulary inside a measurement record, so the
    meter refuses the label. (The snapshot is also already sealed by then — a
    tally cannot appear inside the document it counts.)"""
    meter = CostMeter("job-g3")
    with pytest.raises(PricingLeakError):
        meter.record_write("generation_costs")
    assert meter.snapshot()["writes"] == {}


def test_the_pipeline_does_not_tally_the_durable_write():
    """Source-invariant companion to the test above: if someone "fixes" the
    missing tally, the meter raises inside a ``finally`` on every generation.

    AST, not substring: the docstring beside the write *explains* the omission
    by quoting the forbidden call, and a grep cannot tell prose from code —
    exactly the confusion the house slowapi-AST checks exist to avoid. Only a
    real ``record_write("generation_costs")`` **call** fails this.
    """
    import ast
    from pathlib import Path

    tree = ast.parse(Path("backend/archimedes/agents/generation_pipeline.py").read_text())
    offenders = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and getattr(node.func, "attr", getattr(node.func, "id", None)) == "record_write"
        and node.args
        and isinstance(node.args[0], ast.Constant)
        and node.args[0].value == "generation_costs"
    ]
    assert offenders == [], f"the durable write must not be tallied through the meter (lines {offenders})"


# ── The model: upsert, refusals, round-trip ───────────────────────────────


class TestRecordGenerationCost:
    def test_round_trips_the_measurement_and_the_quote(self):
        with get_session() as session:
            record_generation_cost(
                session, job_id="job-1326", strategy_id="strat-a", measurement=_SNAPSHOT, quote=_QUOTE
            )
            session.commit()
            payload = generation_cost_for_strategy(session, "strat-a")

        assert payload["schema"] == "cost_v1"
        assert payload["job_id"] == "job-1326"
        assert payload["recorded_at"]
        assert payload["measurement"]["llm"]["total_tokens"] == 46354
        assert payload["measurement"]["stages"]["debate_backtest"]["wall_seconds"] == 21.55
        assert payload["quote"]["price"] == "$0.150000"

    def test_is_idempotent_on_the_job_strategy_pair(self):
        with get_session() as session:
            record_generation_cost(session, job_id="j", strategy_id="s", measurement=_SNAPSHOT)
            record_generation_cost(
                session,
                job_id="j",
                strategy_id="s",
                measurement={**_SNAPSHOT, "wall_seconds": 99.0},
                quote=_QUOTE,
            )
            session.commit()
            assert session.query(GenerationCostRecord).count() == 1
            payload = generation_cost_for_strategy(session, "s")
        assert payload["measurement"]["wall_seconds"] == 99.0
        assert payload["quote"] is not None

    def test_a_value_json_cannot_encode_is_stringified_like_the_job_record_does(self):
        """The two stores must not disagree about whether a snapshot is
        writable. ``JobStore`` encodes with ``default=str``; if this one did not,
        a stray non-JSON meta value would land on the expiring copy and be lost
        from the durable one — the failure mode hardest to notice, because it
        looks fine for an hour."""
        from datetime import datetime as _dt

        exotic = {**_SNAPSHOT, "meta": {"outcome": "done", "stamped_at": _dt(2026, 8, 20, 9, 16)}}
        with get_session() as session:
            record_generation_cost(session, job_id="j", strategy_id="s", measurement=exotic)
            session.commit()
            payload = generation_cost_for_strategy(session, "s")
        assert payload["measurement"]["meta"]["stamped_at"] == "2026-08-20 09:16:00"

    def test_a_missing_key_is_refused_rather_than_written_unreadable(self):
        with get_session() as session:
            with pytest.raises(ValueError, match="job_id"):
                record_generation_cost(session, job_id="", strategy_id="s", measurement=_SNAPSHOT)
            with pytest.raises(ValueError, match="strategy_id"):
                record_generation_cost(session, job_id="j", strategy_id="", measurement=_SNAPSHOT)
            with pytest.raises(ValueError, match="measurement mapping"):
                record_generation_cost(session, job_id="j", strategy_id="s", measurement=None)
            assert session.query(GenerationCostRecord).count() == 0

    def test_a_schemaless_snapshot_is_labelled_unknown_not_assumed_to_be_cost_v1(self):
        with get_session() as session:
            record_generation_cost(session, job_id="j", strategy_id="s", measurement={"wall_seconds": 1.0})
            session.commit()
            payload = generation_cost_for_strategy(session, "s")
        assert payload["schema"] == "unknown"

    def test_a_missing_quote_is_null_not_a_zero_price(self):
        with get_session() as session:
            record_generation_cost(session, job_id="j", strategy_id="s", measurement=_SNAPSHOT, quote=None)
            session.commit()
            payload = generation_cost_for_strategy(session, "s")
        assert payload["quote"] is None
        assert "price" not in json.dumps(payload["measurement"])


# ── G1: unknown is never zero at the read boundary ────────────────────────


class TestUnknownIsNeverZero:
    def test_a_strategy_with_no_record_reads_none(self):
        with get_session() as session:
            assert generation_cost_for_strategy(session, "never-generated") is None
            assert generation_cost_for_strategy(session, "") is None
            assert generation_costs_for_strategies(session, ["never-generated"]) == {}
            assert generation_costs_for_strategies(session, []) == {}

    def test_a_corrupt_measurement_reads_as_absent_not_as_an_empty_measurement(self):
        """The input that SHOULD fail the guard: a row whose measurement column
        is not JSON. Returning ``{}`` would render a cost card full of zeros for
        a run we cannot actually read."""
        with get_session() as session:
            record_generation_cost(session, job_id="j", strategy_id="s", measurement=_SNAPSHOT)
            session.query(GenerationCostRecord).filter_by(strategy_id="s").first().measurement_json = "{not json"
            session.commit()

            assert generation_cost_for_strategy(session, "s") is None
            assert generation_costs_for_strategies(session, ["s"]) == {}

    def test_a_corrupt_quote_degrades_alone_the_measurement_still_stands(self):
        with get_session() as session:
            record_generation_cost(session, job_id="j", strategy_id="s", measurement=_SNAPSHOT, quote=_QUOTE)
            session.query(GenerationCostRecord).filter_by(strategy_id="s").first().quote_json = "{not json"
            session.commit()
            payload = generation_cost_for_strategy(session, "s")

        assert payload is not None
        assert payload["quote"] is None
        assert payload["measurement"]["llm"]["total_tokens"] == 46354

    def test_a_measured_zero_survives_as_zero(self):
        """The other half of the same rule. The fixture path makes no LLM calls
        and honestly reports zeros; those must round-trip as zeros, or "unknown"
        and "measured zero" have collapsed into one another."""
        zeroed = {
            **_SNAPSHOT,
            "llm": {
                "calls": 0,
                "calls_missing_usage": 0,
                "usage_complete": True,
                "input_tokens": 0,
                "output_tokens": 0,
                "total_tokens": 0,
                "by_model": {},
            },
        }
        with get_session() as session:
            record_generation_cost(session, job_id="j0", strategy_id="s0", measurement=zeroed)
            session.commit()
            payload = generation_cost_for_strategy(session, "s0")
        assert payload["measurement"]["llm"]["total_tokens"] == 0
        assert payload["measurement"]["llm"]["usage_complete"] is True

    def test_the_batch_reader_does_not_fall_back_to_an_older_row_when_the_NEWEST_is_corrupt(self):
        """The input that SHOULD fail the guard: two rows for one strategy where
        only the NEWEST will not decode.

        The ASC + overwrite form silently kept the older row, so the batch reader
        served a superseded measurement while the single-strategy reader served
        ``None`` for the same strategy — two surfaces disagreeing about what was
        measured, with the stale one looking perfectly healthy. Both readers must
        answer "the newest is unreadable, so we have nothing"."""
        with get_session() as session:
            record_generation_cost(
                session, job_id="job-old", strategy_id="s", measurement={**_SNAPSHOT, "wall_seconds": 1.0}
            )
            record_generation_cost(
                session, job_id="job-new", strategy_id="s", measurement={**_SNAPSHOT, "wall_seconds": 2.0}
            )
            session.commit()

            newest = session.query(GenerationCostRecord).filter_by(strategy_id="s", job_id="job-new").first()
            newest.measurement_json = "{not json"
            session.commit()

            batch = generation_costs_for_strategies(session, ["s"])
            single = generation_cost_for_strategy(session, "s")

        assert single is None, "the single reader already answers honestly"
        assert "s" not in batch, "the batch reader must not substitute the older, superseded row"
        assert batch == {}

    def test_the_batch_reader_returns_the_newest_row_per_strategy(self):
        with get_session() as session:
            record_generation_cost(session, job_id="job-old", strategy_id="s", measurement=_SNAPSHOT)
            record_generation_cost(
                session, job_id="job-new", strategy_id="s", measurement={**_SNAPSHOT, "wall_seconds": 5.0}
            )
            session.commit()
            batch = generation_costs_for_strategies(session, ["s", "missing"])

        assert set(batch) == {"s"}
        assert batch["s"]["job_id"] == "job-new"
        assert batch["s"]["measurement"]["wall_seconds"] == 5.0


# ── The pipeline writes it, on the real production path ───────────────────


class _FakeStore:
    """Mirrors ``tests/services/test_generation_pipeline._FakeStore``."""

    def __init__(self) -> None:
        self.events: list[dict] = []
        self.status: list[tuple] = []

    async def push_event(self, job_id, payload):
        self.events.append(payload)
        return len(self.events)

    async def update_status(self, job_id, status, *, result=None, error=""):
        self.status.append((status, result, error))

    async def merge_result(self, job_id, patch):
        return True


@pytest.fixture
def _fixture_path(monkeypatch):
    monkeypatch.setenv("GENERATION_PIPELINE_FIXTURE", "1")


@pytest.mark.usefixtures("_fixture_path")
class TestPipelinePersistsDurably:
    async def test_a_completed_generation_writes_the_durable_row(self):
        from archimedes.agents.generation_pipeline import GenerateBrief, run_generation

        with patch(
            "archimedes.agents.generation_pipeline._persist_candidate",
            new=AsyncMock(return_value=("strat_cost_001", "0xabc")),
        ):
            await run_generation(
                job_id="job_cost_e2e",
                brief=GenerateBrief(intent="balanced macro", risk_appetite="moderate"),
                n_candidates=1,
                store=_FakeStore(),
            )

        with get_session() as session:
            payload = generation_cost_for_strategy(session, "strat_cost_001")

        assert payload is not None, "a completed generation must leave a durable measurement"
        assert payload["job_id"] == "job_cost_e2e"
        assert payload["schema"] == "cost_v1"
        assert payload["measurement"]["meta"]["outcome"] == "done"
        assert "persist_winner" in payload["measurement"]["stages"]
        # This run's candidates FAILED the gate, and it still carries a full
        # measurement — the issue's "rigor-failed terminal path where a strategy
        # row exists", which is also the common case and costs the same compute.
        assert payload["measurement"]["meta"]["candidates_passing_rigor"] == 0
        # The fixture runner makes no LLM calls, so zero tokens here is a real
        # measurement, not a missing one — and it reports itself complete.
        assert payload["measurement"]["llm"]["total_tokens"] == 0
        assert payload["measurement"]["llm"]["usage_complete"] is True
        # The quote in force at generation time is recorded beside it.
        assert payload["quote"] is not None
        assert payload["quote"]["pricing_model"] == "flat_v1"

    async def test_the_record_survives_the_job_records_expiry(self):
        """The acceptance criterion, exercised: delete the Redis job hash — the
        TTL's end state — and the measurement is still readable."""
        from archimedes.agents.generation_pipeline import GenerateBrief, run_generation

        store = JobStore(url="redis://unused")
        store._redis = fakeredis.FakeAsyncRedis(decode_responses=True)
        job_id = await store.enqueue(job_type="generate", payload={"brief": {"intent": "x"}})

        with patch(
            "archimedes.agents.generation_pipeline._persist_candidate",
            new=AsyncMock(return_value=("strat_ttl_001", "0xabc")),
        ):
            await run_generation(
                job_id=job_id,
                brief=GenerateBrief(intent="balanced macro", risk_appetite="moderate"),
                n_candidates=1,
                store=store,
            )

        # Before: both copies exist.
        job = await store.get(job_id)
        assert job["result"]["cost"]["schema"] == "cost_v1"

        # Simulate JOB_TTL elapsing.
        await store._redis.delete(f"{KEY_PREFIX}{job_id}")
        assert await store.get(job_id) is None

        with get_session() as session:
            payload = generation_cost_for_strategy(session, "strat_ttl_001")
        assert payload is not None, "the durable record must not depend on the job hash"
        assert payload["measurement"]["llm"]["calls"] == job["result"]["cost"]["llm"]["calls"], (
            "the durable row and the job record must carry the SAME snapshot, not two readings"
        )

    async def test_a_run_that_produced_no_strategy_writes_no_durable_row(self):
        """The issue's own boundary: persist "where a strategy row exists". An
        invalid brief bails before any persist, so there is nothing to key a
        record to — and inventing a strategy id to hang it on would be worse
        than the gap."""
        from archimedes.agents import generation_pipeline

        with (
            patch.object(generation_pipeline, "_llm_available", return_value=True),
            patch.object(
                generation_pipeline,
                "_validate_brief",
                new=AsyncMock(return_value={"is_valid": False, "reason": "too vague", "hint": "name an asset"}),
            ),
        ):
            await generation_pipeline.run_generation(
                job_id="job_no_strategy",
                brief=generation_pipeline.GenerateBrief(intent="uh"),
                store=_FakeStore(),
            )

        with get_session() as session:
            assert session.query(GenerationCostRecord).count() == 0

    async def test_a_persist_failure_does_not_fail_the_generation(self):
        """G4. The input that SHOULD break a naive implementation: a write that
        raises inside the ``finally``. Instrumentation is not allowed to change
        the outcome of a generation."""
        from archimedes.agents.generation_pipeline import GenerateBrief, run_generation

        store = _FakeStore()
        with (
            patch(
                "archimedes.agents.generation_pipeline._persist_candidate",
                new=AsyncMock(return_value=("strat_boom", "0xabc")),
            ),
            patch(
                "archimedes.models.generation_cost.record_generation_cost",
                side_effect=RuntimeError("database is on fire"),
            ),
        ):
            await run_generation(
                job_id="job_boom",
                brief=GenerateBrief(intent="balanced macro", risk_appetite="moderate"),
                n_candidates=1,
                store=store,
            )

        assert [s[0] for s in store.status] == ["running", "done"], (
            "a failed cost persist must not change the job's terminal state"
        )
        with get_session() as session:
            assert session.query(GenerationCostRecord).count() == 0


class TestQuoteInForce:
    def test_reads_the_quote_seam_verbatim(self):
        from archimedes.agents.generation_pipeline import _quote_in_force
        from archimedes.services.generation_payment import quote

        assert _quote_in_force() == quote()

    def test_an_unreadable_quote_seam_records_unknown_not_a_free_generation(self):
        from archimedes.agents import generation_pipeline
        from archimedes.services import generation_payment

        with patch.object(generation_payment, "quote", side_effect=RuntimeError("seam down")):
            assert generation_pipeline._quote_in_force() is None

    def test_the_quote_seam_is_still_flat_v1_and_untouched(self):
        from archimedes.services.generation_payment import quote

        assert quote()["pricing_model"] == "flat_v1"


# ── The read surfaces ─────────────────────────────────────────────────────


def _make_passport(session, strategy_id: str):
    from archimedes.models.strategy_passport_record import PassportPaperRef, StrategyPassportRecord

    session.add(
        StrategyPassportRecord(
            id=strategy_id,
            generation_method="debate",
            methodology_summary="For brief 'momentum': a test methodology",
            asset_universe="[]",
            position_sizing="equal_weight",
            rebalance_frequency="weekly",
            status="candidate",
            regime_tag="regime_neutral",
            passes_rigor_gate=False,
        )
    )
    session.add(PassportPaperRef(passport_id=strategy_id, arxiv_id="2301.00001", title="A Paper", authors="[]"))
    session.flush()


class TestPassportReadSurface:
    def test_the_passport_response_carries_the_durable_record(self):
        from archimedes.api.strategies_routes import _passport_to_strategy_response
        from archimedes.services.passport_loader import get_passport

        with get_session() as session:
            _make_passport(session, "pp-with-cost")
            record_generation_cost(
                session, job_id="job-pp", strategy_id="pp-with-cost", measurement=_SNAPSHOT, quote=_QUOTE
            )
            session.commit()
            resp = _passport_to_strategy_response(get_passport(session, "pp-with-cost"), session=session)

        assert resp.generation_cost["job_id"] == "job-pp"
        assert resp.generation_cost["measurement"]["llm"]["total_tokens"] == 46354
        assert resp.generation_cost["quote"]["price"] == "$0.150000"

    def test_a_strategy_nothing_measured_reports_none_not_a_zeroed_record(self):
        from archimedes.api.strategies_routes import _passport_to_strategy_response
        from archimedes.services.passport_loader import get_passport

        with get_session() as session:
            _make_passport(session, "pp-no-cost")
            session.commit()
            resp = _passport_to_strategy_response(get_passport(session, "pp-no-cost"), session=session)

        assert resp.generation_cost is None

    def test_a_curated_strategy_reports_none(self):
        """Curated strategies were never generated by a metered run, so there is
        nothing to measure and nothing is claimed."""
        from archimedes.api.schemas import StrategyResponse

        assert (
            StrategyResponse(
                id="x",
                methodology_summary="",
                asset_universe=[],
                position_sizing="equal_weight",
                rebalance_frequency="weekly",
                status="live",
            ).generation_cost
            is None
        )
