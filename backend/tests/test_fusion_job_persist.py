"""Fusion job persistence failure handling (#948).

A fusion job that produces an actionable strategy but fails to PERSIST it must
not be reported as a successful "done" job with a null strategy_id (which leaves
the UI's "view" link dead). It must be marked "failed" instead.

Hermetic: every boundary (job store, fusion runner, DB session, upsert) is
mocked — no Redis, no DB, no LLM.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from archimedes.api.strategies_routes import _run_fusion_job


def _actionable_result() -> SimpleNamespace:
    """A minimal actionable fusion result with strategy_spec=None (skips eval)."""
    return SimpleNamespace(
        is_actionable=True,
        strategy_spec=None,  # skip the backtest/rigor evaluator branch
        status="done",
        strategy_name="Fused Strat",
        thesis="thesis",
        source_arxiv_ids=[],
        fusion_reasoning="because",
        novelty_rationale="novel",
        risk_notes="risky",
        model="nova-micro",
        requested_model="nova-micro",
    )


def _mock_store() -> MagicMock:
    store = MagicMock()
    store.update_status = AsyncMock()
    store.get = AsyncMock(return_value={"payload": {"risk_appetite": "moderate", "asset_classes": ["equities"]}})
    store.close = AsyncMock()
    return store


@pytest.mark.asyncio
async def test_persist_failure_marks_job_failed_not_done():
    """upsert_strategy raising → job is marked 'failed', never 'done' (#948)."""
    store = _mock_store()
    fusion = MagicMock()
    fusion.propose = MagicMock(return_value=_actionable_result())

    with (
        patch("archimedes.services.job_queue.JobStore", return_value=store),
        patch("archimedes.agents.strategy_fusion.default_fusion", return_value=fusion),
        patch("archimedes.db.get_session"),
        patch(
            "archimedes.models.strategy_store.upsert_strategy",
            side_effect=RuntimeError("db down"),
        ),
    ):
        await _run_fusion_job("job-123")

    # Collect every (positional) status update the job received.
    statuses = [call.args[1] for call in store.update_status.await_args_list if len(call.args) >= 2]
    assert "done" not in statuses, f"job must NOT be marked done on persist failure; got {statuses}"
    # The final status must be 'failed' with an explanatory error.
    failed_calls = [c for c in store.update_status.await_args_list if len(c.args) >= 2 and c.args[1] == "failed"]
    assert failed_calls, f"expected a 'failed' status update; got {statuses}"
    assert "could not be saved" in (failed_calls[-1].kwargs.get("error") or "")


@pytest.mark.asyncio
async def test_persist_success_marks_job_done():
    """When persistence succeeds, the job is marked 'done' with the strategy_id."""
    store = _mock_store()
    fusion = MagicMock()
    fusion.propose = MagicMock(return_value=_actionable_result())

    session_cm = MagicMock()
    session_cm.__enter__ = MagicMock(return_value=MagicMock())
    session_cm.__exit__ = MagicMock(return_value=False)

    with (
        patch("archimedes.services.job_queue.JobStore", return_value=store),
        patch("archimedes.agents.strategy_fusion.default_fusion", return_value=fusion),
        patch("archimedes.db.get_session", return_value=session_cm),
        patch(
            "archimedes.models.strategy_store.upsert_strategy",
            return_value=SimpleNamespace(id="strat-42"),
        ),
    ):
        await _run_fusion_job("job-456")

    done_calls = [c for c in store.update_status.await_args_list if len(c.args) >= 2 and c.args[1] == "done"]
    assert done_calls, "expected a 'done' status update on successful persist"
    assert done_calls[-1].kwargs["result"]["strategy_id"] == "strat-42"
