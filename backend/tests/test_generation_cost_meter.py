"""Per-generation cost instrumentation (#1217) — the measurement must be honest.

The point of the meter is to replace an assumption ("near-zero marginal cost")
with numbers. A measurement layer that reports a plausible-looking zero when it
failed to measure is worse than no measurement at all, so the guards below are
the substance of this file, not a formality. Each one is exercised with the
input that SHOULD fail it:

* **G1** — a provider response with no ``usage`` block must not bank as a 0-token
  call. ``usage_complete`` flips to ``False``.
* **G2** — an implausible count (negative, non-finite, stringly-typed, absurd) is
  refused, not accumulated; the call is recorded as missing instead.
* **G3** — a pricing-shaped label (``cost_usd``, ``price_per_call``, …) is
  rejected with :class:`PricingLeakError`. No pricing math lives server-side and
  the quote seam stays ``flat_v1``.
* **G4** — ``JobStore.merge_result`` on a job that does not exist must not create
  a phantom, TTL-less job hash.

Hermetic: fakeredis for the job store (``test_runner_lease`` precedent), the
provider SDK clients mocked at the boundary (``test_api_routes`` precedent), and
real signed SIWE cookies for the endpoint scoping (``test_user_routes``
precedent). No live LLM, no Postgres, no network.
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import fakeredis
import pytest
from archimedes.api.auth_siwe import _COOKIE_NAME, _sign_session
from archimedes.services import cost_meter
from archimedes.services.cost_meter import CostMeter, PricingLeakError, extract_token_usage
from archimedes.services.job_queue import JOB_TTL, JobStore
from fastapi.testclient import TestClient


# ── Token extraction, per provider response shape ─────────────────────────


class TestTokenExtraction:
    """Every provider spells the same two numbers differently."""

    def test_bedrock_converse_shape(self):
        # boto3 bedrock-runtime `converse` — the live provider (bedrock_converse).
        resp = {"usage": {"inputTokens": 812, "outputTokens": 143, "totalTokens": 955}}
        assert extract_token_usage(resp) == (812, 143)

    def test_anthropic_sdk_shape(self):
        resp = MagicMock()
        resp.usage.input_tokens = 400
        resp.usage.output_tokens = 91
        assert extract_token_usage(resp) == (400, 91)

    def test_openai_compatible_shape(self):
        resp = {"usage": {"prompt_tokens": 33, "completion_tokens": 7}}
        assert extract_token_usage(resp) == (33, 7)

    def test_ollama_top_level_shape(self):
        # Ollama reports counts at the top level with no usage block at all.
        assert extract_token_usage({"prompt_eval_count": 12, "eval_count": 4}) == (12, 4)

    def test_no_usage_anywhere_is_none_not_zero(self):
        assert extract_token_usage({"output": {"message": {}}}) == (None, None)
        assert extract_token_usage(None) == (None, None)


# ── G1: a missing measurement is never a zero ─────────────────────────────


class TestMissingUsageIsNotZero:
    """The input that SHOULD fail the guard: a response with no usage block.

    A meter that banked it as 0/0 would report ``usage_complete: true`` with
    plausible-looking totals, which is exactly the fail-soft substitution
    CLAUDE.md forbids — the run would look free.
    """

    def test_response_without_usage_marks_the_snapshot_incomplete(self):
        meter = CostMeter("job-g1")
        meter.record_response(model="amazon.nova-micro-v1:0", response={"output": {"message": {}}})
        llm = meter.snapshot()["llm"]

        assert llm["calls"] == 1, "the call happened and must be counted"
        assert llm["calls_missing_usage"] == 1
        assert llm["usage_complete"] is False, "a snapshot with an unmeasured call must not claim completeness"
        assert llm["input_tokens"] == 0
        assert llm["output_tokens"] == 0

    def test_a_measured_call_alongside_an_unmeasured_one_still_flags_incomplete(self):
        meter = CostMeter("job-g1b")
        meter.record_response(model="m", response={"usage": {"inputTokens": 100, "outputTokens": 10}})
        meter.record_response(model="m", response={"no": "usage"})
        llm = meter.snapshot()["llm"]

        assert llm["input_tokens"] == 100, "the measured call is still banked"
        assert llm["calls"] == 2
        assert llm["calls_missing_usage"] == 1
        assert llm["usage_complete"] is False
        assert llm["by_model"]["m"]["calls_missing_usage"] == 1

    def test_half_readable_usage_banks_neither_half(self):
        # Input side readable, output side absent. Banking 812/0 would understate
        # the run while reporting itself complete.
        meter = CostMeter("job-g1c")
        meter.record_response(model="m", response={"usage": {"inputTokens": 812}})
        llm = meter.snapshot()["llm"]

        assert llm["input_tokens"] == 0
        assert llm["usage_complete"] is False
        assert llm["calls_missing_usage"] == 1

    def test_all_measured_reports_complete(self):
        meter = CostMeter("job-g1d")
        meter.record_response(model="m", response={"usage": {"inputTokens": 5, "outputTokens": 6}})
        llm = meter.snapshot()["llm"]
        assert llm["usage_complete"] is True
        assert llm["total_tokens"] == 11


# ── G2: implausible counts are refused, not accumulated ───────────────────


class TestImplausibleCountsRefused:
    """The inputs that SHOULD fail the guard, each demonstrated to fail it."""

    @pytest.mark.parametrize(
        ("bad_usage", "why"),
        [
            ({"inputTokens": -5, "outputTokens": 12}, "negative token count"),
            ({"inputTokens": float("nan"), "outputTokens": 12}, "NaN token count"),
            ({"inputTokens": float("inf"), "outputTokens": 12}, "infinite token count"),
            ({"inputTokens": "812", "outputTokens": 12}, "stringly-typed count"),
            ({"inputTokens": 10**12, "outputTokens": 12}, "absurd count (> MAX_PLAUSIBLE_TOKENS)"),
            ({"inputTokens": True, "outputTokens": 12}, "bool masquerading as an int"),
            ({"inputTokens": 1.5, "outputTokens": 12}, "non-integral count"),
        ],
    )
    def test_bad_count_is_recorded_as_missing_not_added(self, bad_usage, why):
        meter = CostMeter("job-g2")
        meter.record_response(model="m", response={"usage": bad_usage})
        llm = meter.snapshot()["llm"]

        assert llm["input_tokens"] == 0, f"{why} must not be accumulated"
        assert llm["output_tokens"] == 0, f"{why} must not bank its partner half either"
        assert llm["calls_missing_usage"] == 1, f"{why} must be recorded as an unmeasured call"
        assert llm["usage_complete"] is False

    def test_zero_is_a_legitimate_measurement(self):
        # 0 is only wrong when it stands in for "unknown". A provider that
        # genuinely reports 0 output tokens is measured, not missing.
        meter = CostMeter("job-g2b")
        meter.record_response(model="m", response={"usage": {"inputTokens": 40, "outputTokens": 0}})
        llm = meter.snapshot()["llm"]
        assert llm["usage_complete"] is True
        assert llm["calls_missing_usage"] == 0
        assert llm["total_tokens"] == 40

    def test_implausible_write_count_is_ignored(self):
        meter = CostMeter("job-g2c")
        meter.record_write("backtest_results", -3)
        assert meter.snapshot()["writes"] == {}


# ── G3: no pricing math in the measurement layer ──────────────────────────


class TestNoPricingInMeasurementLayer:
    """The inputs that SHOULD fail the guard: pricing-shaped labels.

    #1217 is explicit that the server records raw counts and nothing more; the
    quote seam stays ``flat_v1`` until a human converts these numbers into a
    price off-server.
    """

    @pytest.mark.parametrize(
        "bad_label",
        ["cost_usd", "price_per_call", "bedrock_spend", "usd_total", "token_pricing", "settlement_fee"],
    )
    def test_pricing_shaped_write_counter_is_rejected(self, bad_label):
        meter = CostMeter("job-g3")
        with pytest.raises(PricingLeakError):
            meter.record_write(bad_label)
        assert meter.snapshot()["writes"] == {}

    @pytest.mark.parametrize("bad_label", ["cost_usd", "price_per_call", "invoice_build"])
    def test_pricing_shaped_stage_name_is_rejected(self, bad_label):
        meter = CostMeter("job-g3b")
        with pytest.raises(PricingLeakError):
            with meter.stage(bad_label):
                pass
        assert meter.snapshot()["stages"] == {}

    @pytest.mark.parametrize("bad_label", ["dollars_burned", "revenue_attributed", "unit_cost"])
    def test_pricing_shaped_meta_key_is_rejected(self, bad_label):
        meter = CostMeter("job-g3c")
        with pytest.raises(PricingLeakError):
            meter.set_meta(bad_label, 1)
        assert meter.snapshot()["meta"] == {}

    def test_module_level_recorders_reject_even_with_no_meter_bound(self):
        # Unbound is the common case in tests/scripts; the guard must not be
        # bypassable by simply not having started a job.
        assert cost_meter.current_meter() is None
        with pytest.raises(PricingLeakError):
            cost_meter.record_write("cost_usd")
        with pytest.raises(PricingLeakError):
            cost_meter.set_meta("price_hint", 1)
        with pytest.raises(PricingLeakError):
            with cost_meter.stage("usd_conversion"):
                pass

    def test_legitimate_labels_are_accepted(self):
        meter = CostMeter("job-g3d")
        meter.record_write("backtest_results")
        with meter.stage("debate_backtest"):
            pass
        meter.set_meta("outcome", "done")
        snap = meter.snapshot()
        assert snap["writes"] == {"backtest_results": 1}
        assert snap["stages"]["debate_backtest"]["runs"] == 1
        assert snap["meta"]["outcome"] == "done"

    def test_snapshot_carries_no_pricing_shaped_key(self):
        meter = CostMeter("job-g3e")
        meter.record_response(model="m", response={"usage": {"inputTokens": 1, "outputTokens": 2}})
        with meter.stage("rigor_gate"):
            pass
        meter.record_write("strategy_store")

        def _keys(node, prefix=""):
            if isinstance(node, dict):
                for k, v in node.items():
                    yield f"{prefix}{k}"
                    yield from _keys(v, f"{prefix}{k}.")

        banned = ("usd", "dollar", "price", "cost", "fee", "revenue")
        offenders = [k for k in _keys(meter.snapshot()) if any(b in k.lower() for b in banned)]
        assert offenders == [], f"measurement snapshot must carry no pricing fields: {offenders}"

    def test_quote_seam_is_untouched_and_still_flat(self):
        from archimedes.services.generation_payment import quote

        assert quote()["pricing_model"] == "flat_v1"


# ── The LLM boundary actually records (production code path) ──────────────


class TestLLMBackendBoundaryRecords:
    """``complete()`` is where every provider response passes; mock the SDK
    client, not the meter, so the wiring under test is the real one."""

    def test_bedrock_converse_complete_banks_usage(self):
        from archimedes.services.llm_backend import BedrockConverseBackend

        fake_client = MagicMock()
        fake_client.converse.return_value = {
            "output": {"message": {"content": [{"text": "hello"}]}},
            "usage": {"inputTokens": 1200, "outputTokens": 260, "totalTokens": 1460},
        }
        with patch("boto3.Session") as session, patch("boto3.client", return_value=fake_client):
            session.return_value.get_credentials.return_value = object()
            backend = BedrockConverseBackend(model="amazon.nova-micro-v1:0")
            with cost_meter.measure("job-live") as meter:
                assert backend.complete("sys", "user") == "hello"

        llm = meter.snapshot()["llm"]
        assert llm["calls"] == 1
        assert llm["usage_complete"] is True
        assert llm["input_tokens"] == 1200
        assert llm["output_tokens"] == 260
        assert llm["by_model"]["amazon.nova-micro-v1:0"]["calls"] == 1

    def test_bedrock_converse_without_usage_is_flagged_not_zeroed(self):
        from archimedes.services.llm_backend import BedrockConverseBackend

        fake_client = MagicMock()
        fake_client.converse.return_value = {"output": {"message": {"content": [{"text": "hi"}]}}}
        with patch("boto3.Session") as session, patch("boto3.client", return_value=fake_client):
            session.return_value.get_credentials.return_value = object()
            backend = BedrockConverseBackend(model="amazon.nova-micro-v1:0")
            with cost_meter.measure("job-live-2") as meter:
                backend.complete("sys", "user")

        llm = meter.snapshot()["llm"]
        assert llm["calls"] == 1
        assert llm["usage_complete"] is False

    def test_anthropic_backend_complete_banks_usage(self):
        import anthropic

        from archimedes.services.llm_backend import AnthropicBackend

        block = MagicMock()
        block.text = "answer"
        resp = MagicMock()
        resp.content = [block]
        resp.model = "claude-haiku-4-5"
        resp.usage.input_tokens = 77
        resp.usage.output_tokens = 21

        client = MagicMock()
        client.messages.create.return_value = resp
        with patch.object(anthropic, "Anthropic", return_value=client):
            backend = AnthropicBackend(model="claude-haiku-4-5", api_key="test-key")
            with cost_meter.measure("job-live-3") as meter:
                assert backend.complete("sys", "user") == "answer"

        llm = meter.snapshot()["llm"]
        assert llm["input_tokens"] == 77
        assert llm["output_tokens"] == 21
        assert llm["by_model"]["claude-haiku-4-5"]["calls"] == 1

    def test_recording_is_a_noop_with_no_meter_bound(self):
        # An LLM call outside a measured job must not raise; the shared agent
        # singleton is used by chat and the KB runner too.
        assert cost_meter.current_meter() is None
        cost_meter.record_llm_call(model="m", response={"usage": {"inputTokens": 1, "outputTokens": 1}})


# ── Context propagation into worker threads ───────────────────────────────


class TestContextPropagation:
    """The society proposes and backtests in ``asyncio.to_thread`` workers, so
    usage recorded there has to reach the job's meter."""

    def test_usage_recorded_in_a_worker_thread_reaches_the_job_meter(self):
        async def _run() -> dict:
            with cost_meter.measure("job-threads") as meter:

                def _call(n: int) -> None:
                    cost_meter.record_llm_call(
                        model="amazon.nova-micro-v1:0",
                        response={"usage": {"inputTokens": n, "outputTokens": 1}},
                    )

                await asyncio.gather(*(asyncio.to_thread(_call, i) for i in range(1, 6)))
                return meter.snapshot()

        llm = asyncio.run(_run())["llm"]
        assert llm["calls"] == 5
        assert llm["input_tokens"] == 1 + 2 + 3 + 4 + 5
        assert llm["usage_complete"] is True

    def test_stages_accumulate_across_repeat_entries(self):
        meter = CostMeter("job-stages")
        for _ in range(3):
            with meter.stage("candidate_generation"):
                time.sleep(0.001)
        stage = meter.snapshot()["stages"]["candidate_generation"]
        assert stage["runs"] == 3
        assert stage["wall_seconds"] > 0


# ── G4: merge_result must never conjure a job ─────────────────────────────


def _fake_store() -> JobStore:
    store = JobStore(url="redis://unused")
    store._redis = fakeredis.FakeAsyncRedis(decode_responses=True)
    return store


class TestJobStoreMergeResult:
    async def test_merges_into_an_existing_job_result(self):
        store = _fake_store()
        job_id = await store.enqueue(job_type="generate", payload={"brief": {"intent": "x"}})
        await store.update_status(job_id, "done", result={"best_strategy_id": "s1"})

        assert await store.merge_result(job_id, {"cost": {"schema": "cost_v1"}}) is True
        job = await store.get(job_id)
        assert job["result"]["best_strategy_id"] == "s1", "the existing result must survive the merge"
        assert job["result"]["cost"] == {"schema": "cost_v1"}

    async def test_merges_onto_an_errored_job_that_never_wrote_a_result(self):
        # The rejected path is the common case (#1217 anti-goal 2) and it spends
        # the same compute, so it must still carry a measurement.
        store = _fake_store()
        job_id = await store.enqueue(job_type="generate", payload={})
        await store.update_status(job_id, "error", error="no candidates generated")

        assert await store.merge_result(job_id, {"cost": {"schema": "cost_v1"}}) is True
        job = await store.get(job_id)
        assert job["status"] == "error"
        assert job["result"]["cost"]["schema"] == "cost_v1"

    async def test_unknown_job_is_not_conjured_into_existence(self):
        """The input that SHOULD fail the guard: a merge for a job id that does
        not exist. A bare HSET would materialise a TTL-less hash that then shows
        up in ``list_recent_jobs`` as a statusless phantom job."""
        store = _fake_store()
        redis = store._redis

        assert await store.merge_result("does-not-exist", {"cost": {"schema": "cost_v1"}}) is False
        assert await redis.exists("archimedes:job:does-not-exist") == 0
        assert await store.get("does-not-exist") is None
        assert await store.list_recent_jobs() == []

    async def test_merge_preserves_the_job_ttl(self):
        store = _fake_store()
        job_id = await store.enqueue(job_type="generate", payload={})
        await store.merge_result(job_id, {"cost": {"schema": "cost_v1"}})
        ttl = await store._redis.ttl(f"archimedes:job:{job_id}")
        assert 0 < ttl <= JOB_TTL, "merging a measurement must not clear or extend the job's expiry"

    async def test_non_dict_result_is_left_intact(self):
        store = _fake_store()
        job_id = await store.enqueue(job_type="generate", payload={})
        await store._redis.hset(f"archimedes:job:{job_id}", "result", '["legacy", "list"]')

        assert await store.merge_result(job_id, {"cost": {"schema": "cost_v1"}}) is False
        job = await store.get(job_id)
        assert job["result"] == ["legacy", "list"]


# ── The pipeline persists a snapshot on the FAILED path too ───────────────


class TestPipelinePersistsSnapshot:
    """A generation that fails still consumed the compute. #1217's anti-goal 2 is
    explicit that the rejected path is the common case and is not cheaper, so the
    measurement has to survive the error paths, not just ``done``."""

    async def _run_brief_invalid(self) -> MagicMock:
        from archimedes.agents import generation_pipeline

        store = MagicMock()
        store.update_status = AsyncMock()
        store.push_event = AsyncMock(return_value=1)
        store.merge_result = AsyncMock(return_value=True)

        with (
            patch.object(generation_pipeline, "_llm_available", return_value=True),
            patch.object(
                generation_pipeline,
                "_validate_brief",
                new=AsyncMock(return_value={"is_valid": False, "reason": "too vague", "hint": "name an asset class"}),
            ),
        ):
            await generation_pipeline.run_generation(
                job_id="job-fail",
                brief=generation_pipeline.GenerateBrief(intent="uh"),
                store=store,
            )
        return store

    async def test_error_path_still_persists_a_measurement(self):
        store = await self._run_brief_invalid()

        store.merge_result.assert_awaited_once()
        job_id, patch_payload = store.merge_result.await_args.args
        assert job_id == "job-fail"
        snapshot = patch_payload["cost"]
        assert snapshot["schema"] == "cost_v1"
        assert snapshot["job_id"] == "job-fail"
        assert snapshot["meta"]["outcome"] == "brief_invalid"
        assert "brief_validation" in snapshot["stages"], "the stage that ran must appear in the breakdown"
        assert snapshot["wall_seconds"] >= 0
        assert snapshot["cpu_attribution"] == "process_wide_delta"

    async def test_meter_is_unbound_after_the_job_finishes(self):
        # A leaked binding would attribute the NEXT job's tokens to this one.
        await self._run_brief_invalid()
        assert cost_meter.current_meter() is None


# ── The measurement is readable, and only by its owner ────────────────────

_OWNER = "0xAb5801a7D398351b8bE11C439e05C5B3259aeC9B"
_ATTACKER = "0x9999999999999999999999999999999999999999"
_JOB_ID = "job-cost-1"
_SNAPSHOT = {
    "schema": "cost_v1",
    "job_id": _JOB_ID,
    "llm": {"calls": 9, "input_tokens": 41234, "output_tokens": 5120, "usage_complete": True},
    "stages": {"debate_backtest": {"wall_seconds": 31.2, "cpu_seconds": 28.9, "runs": 1}},
    "writes": {"strategy_store": 1},
}


def _cookies(wallet: str) -> dict[str, str]:
    return {_COOKIE_NAME: _sign_session(wallet, time.time())}


def _cost_job(owner_wallet: str | None, *, cost: dict | None) -> dict:
    return {
        "id": _JOB_ID,
        "type": "generate",
        "status": "done",
        "created_at": "2026-08-01T00:00:00+00:00",
        "updated_at": "2026-08-01T00:00:30+00:00",
        "payload": {"owner_wallet": owner_wallet, "brief": {"intent": "x"}, "n_candidates": 1},
        "result": {"best_strategy_id": "s1", "candidates": [], **({"cost": cost} if cost is not None else {})},
    }


def _client_with(job: dict | None) -> tuple[TestClient, object]:
    from archimedes.main import app

    store = MagicMock()
    store.get = AsyncMock(return_value=job)
    store.list_recent_jobs = AsyncMock(return_value=[job] if job else [])
    return TestClient(app), patch("archimedes.api.generate_routes.get_job_store", return_value=store)


class TestCostEndpoint:
    def test_owner_reads_the_measurement(self):
        client, patched = _client_with(_cost_job(_OWNER.lower(), cost=_SNAPSHOT))
        with patched:
            resp = client.get(f"/api/generate/jobs/{_JOB_ID}/cost", cookies=_cookies(_OWNER))
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["state"] == "done"
        assert body["cost"]["llm"]["input_tokens"] == 41234
        assert body["cost"]["stages"]["debate_backtest"]["cpu_seconds"] == 28.9

    def test_missing_measurement_is_null_not_zero(self):
        """A job that predates the meter (or has not finished) reports ``null`` —
        an honest absence, never a fabricated zero-token record."""
        client, patched = _client_with(_cost_job(_OWNER.lower(), cost=None))
        with patched:
            resp = client.get(f"/api/generate/jobs/{_JOB_ID}/cost", cookies=_cookies(_OWNER))
        assert resp.status_code == 200, resp.text
        assert resp.json()["cost"] is None

    def test_another_users_job_404s(self):
        client, patched = _client_with(_cost_job(_OWNER.lower(), cost=_SNAPSHOT))
        with patched:
            resp = client.get(f"/api/generate/jobs/{_JOB_ID}/cost", cookies=_cookies(_ATTACKER))
        assert resp.status_code == 404, resp.text

    def test_anonymous_401(self):
        client, patched = _client_with(_cost_job(_OWNER.lower(), cost=_SNAPSHOT))
        with patched:
            resp = client.get(f"/api/generate/jobs/{_JOB_ID}/cost")
        assert resp.status_code == 401, resp.text

    def test_jobs_listing_carries_the_measurement(self):
        client, patched = _client_with(_cost_job(_OWNER.lower(), cost=_SNAPSHOT))
        with patched:
            resp = client.get("/api/generate/jobs", cookies=_cookies(_OWNER))
        assert resp.status_code == 200, resp.text
        assert resp.json()["jobs"][0]["cost"]["llm"]["calls"] == 9
