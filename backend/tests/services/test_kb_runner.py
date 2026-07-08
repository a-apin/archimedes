"""Unit coverage for the KB-pipeline scheduling helpers.

Targets `_load_manifest`, `_count_new_papers_since`, and `needs_rerun`.
The blocking `main()` loop is intentionally not exercised — it's an
infinite `time.sleep` loop and is excluded by the `if __name__` guard.

Added 2026-05-24 as part of the #147 coverage-gate lift. Extended 2026-07-07
(#1043) with the sync exactly-once lease path (`_SyncLeaseClient`,
`_run_pipeline_with_lease`) — see backend/tests/test_runner_lease.py for the
async (oracle_runner/agent_runner) counterpart. Further extended 2026-07-07
(PR #1046 review) with the `lease_lost` mid-run manifest-write guard in
`archimedes.scripts.run_kb_pipeline.run_pipeline`.
"""

from __future__ import annotations

import json
import threading
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import fakeredis
import pytest
from archimedes.scripts.run_kb_pipeline import run_pipeline
from archimedes.services import kb_runner


@pytest.fixture
def tmp_artifact_dir(tmp_path, monkeypatch):
    """Point kb_runner.ARTIFACT_DIR at a fresh tmp directory for the test."""
    monkeypatch.setattr(kb_runner, "ARTIFACT_DIR", tmp_path)
    return tmp_path


class TestLoadManifest:
    def test_missing_manifest_returns_none(self, tmp_artifact_dir):
        assert kb_runner._load_manifest() is None

    def test_unreadable_manifest_returns_none(self, tmp_artifact_dir):
        (tmp_artifact_dir / "manifest.json").write_text("not-json")
        assert kb_runner._load_manifest() is None

    def test_valid_manifest_returns_parsed_dict(self, tmp_artifact_dir):
        payload = {"run_ts": "2026-05-20T12:00:00+00:00", "paper_count": 4242}
        (tmp_artifact_dir / "manifest.json").write_text(json.dumps(payload))
        assert kb_runner._load_manifest() == payload


class TestCountNewPapersSince:
    def test_none_timestamp_returns_zero(self):
        # Short-circuits before any DB import — safe to call without mocks
        assert kb_runner._count_new_papers_since(None) == 0

    def test_db_failure_returns_zero(self):
        # No DB available in unit test env → the broad except returns 0
        assert kb_runner._count_new_papers_since("2026-05-01T00:00:00+00:00") == 0


class TestNeedsRerun:
    def test_no_prior_run_triggers_rerun(self, tmp_artifact_dir):
        # No manifest at all → eligible to run
        assert kb_runner.needs_rerun() is True

    def test_new_paper_threshold_triggers_rerun(self, tmp_artifact_dir):
        (tmp_artifact_dir / "manifest.json").write_text(json.dumps({"run_ts": datetime.now(UTC).isoformat()}))
        with patch.object(kb_runner, "_count_new_papers_since", return_value=kb_runner.NEW_PAPER_THRESHOLD + 1):
            assert kb_runner.needs_rerun() is True

    def test_stale_run_triggers_rerun(self, tmp_artifact_dir):
        old_ts = (datetime.now(UTC) - timedelta(days=kb_runner.MAX_DAYS_SINCE_LAST + 1)).isoformat()
        (tmp_artifact_dir / "manifest.json").write_text(json.dumps({"run_ts": old_ts}))
        with patch.object(kb_runner, "_count_new_papers_since", return_value=0):
            assert kb_runner.needs_rerun() is True

    def test_recent_run_no_threshold_skips(self, tmp_artifact_dir):
        recent = datetime.now(UTC).isoformat()
        (tmp_artifact_dir / "manifest.json").write_text(json.dumps({"run_ts": recent}))
        with patch.object(kb_runner, "_count_new_papers_since", return_value=0):
            assert kb_runner.needs_rerun() is False

    def test_malformed_timestamp_triggers_rerun(self, tmp_artifact_dir):
        (tmp_artifact_dir / "manifest.json").write_text(json.dumps({"run_ts": "not-iso"}))
        with patch.object(kb_runner, "_count_new_papers_since", return_value=0):
            # ValueError path → treats as eligible-to-run
            assert kb_runner.needs_rerun() is True


@pytest.fixture
def fake_sync_redis():
    """Patch the sync `redis.Redis.from_url` used by `_SyncLeaseClient` to fakeredis.

    Hermetic: no live Redis. `_SyncLeaseClient` imports `redis` (sync) and
    calls `.from_url(...)` lazily inside `__init__`, so patching the class
    method is enough to redirect every `_SyncLeaseClient()` construction in
    the test to the same in-memory fake instance.
    """
    fake = fakeredis.FakeRedis(decode_responses=True)
    with patch("redis.Redis.from_url", return_value=fake):
        yield fake


class TestSyncLeaseClient:
    """#1043 — the sync counterpart to AgentStateStore's lease API."""

    def test_acquire_returns_a_token(self, fake_sync_redis):
        client = kb_runner._SyncLeaseClient()
        token = client.acquire("kb_runner", ttl_ms=5000)
        assert token is not None

    def test_uses_the_same_key_namespace_as_the_async_runners(self, fake_sync_redis):
        from archimedes.services.redis_state import KEY_LEASE_PREFIX

        client = kb_runner._SyncLeaseClient()
        token = client.acquire("kb_runner", ttl_ms=5000)
        assert fake_sync_redis.get(f"{KEY_LEASE_PREFIX}kb_runner") == token

    def test_second_acquire_while_held_returns_none(self, fake_sync_redis):
        client = kb_runner._SyncLeaseClient()
        first = client.acquire("kb_runner", ttl_ms=5000)
        assert first is not None
        second = client.acquire("kb_runner", ttl_ms=5000)
        assert second is None

    def test_renew_fails_for_a_non_holder_token(self, fake_sync_redis):
        client = kb_runner._SyncLeaseClient()
        client.acquire("kb_runner", ttl_ms=5000)
        assert client.renew("kb_runner", "not-my-token:0", ttl_ms=9000) is False

    def test_renew_succeeds_for_the_holder(self, fake_sync_redis):
        client = kb_runner._SyncLeaseClient()
        token = client.acquire("kb_runner", ttl_ms=5000)
        assert client.renew("kb_runner", token, ttl_ms=9000) is True

    def test_release_with_wrong_token_is_a_noop(self, fake_sync_redis):
        client = kb_runner._SyncLeaseClient()
        token = client.acquire("kb_runner", ttl_ms=5000)
        client.release("kb_runner", "wrong-token:1")
        # Still held — a fresh acquire attempt fails.
        assert client.acquire("kb_runner", ttl_ms=5000) is None
        client.release("kb_runner", token)
        assert client.acquire("kb_runner", ttl_ms=5000) is not None


class TestRunPipelineWithLease:
    """#1043 — the fail-closed acquire-hold-release wrapper around run_pipeline()."""

    def test_pipeline_runs_and_lease_is_released_after(self, fake_sync_redis):
        with patch("archimedes.scripts.run_kb_pipeline.run_pipeline", MagicMock()) as mock_pipeline:
            kb_runner._run_pipeline_with_lease()
        mock_pipeline.assert_called_once()
        # Released — the key no longer exists.
        assert fake_sync_redis.get("archimedes:leader:kb_runner") is None

    def test_pipeline_not_run_when_lease_already_held(self, fake_sync_redis):
        fake_sync_redis.set("archimedes:leader:kb_runner", "someone-else:1", nx=True, px=60_000)
        with patch("archimedes.scripts.run_kb_pipeline.run_pipeline", MagicMock()) as mock_pipeline:
            kb_runner._run_pipeline_with_lease()
        mock_pipeline.assert_not_called()

    def test_pipeline_not_run_when_redis_unreachable(self):
        # Force the Redis client construction itself to fail — deterministic
        # stand-in for "Redis is down", independent of whether a real Redis
        # happens to be listening on the test host. Fail-closed: the
        # pipeline must never run in that state.
        with (
            patch("redis.Redis.from_url", side_effect=ConnectionError("no redis here")),
            patch("archimedes.scripts.run_kb_pipeline.run_pipeline", MagicMock()) as mock_pipeline,
        ):
            kb_runner._run_pipeline_with_lease()
        mock_pipeline.assert_not_called()

    def test_pipeline_exception_still_releases_the_lease(self, fake_sync_redis):
        with (
            patch("archimedes.scripts.run_kb_pipeline.run_pipeline", MagicMock(side_effect=RuntimeError("boom"))),
            pytest.raises(RuntimeError),
        ):
            kb_runner._run_pipeline_with_lease()
        assert fake_sync_redis.get("archimedes:leader:kb_runner") is None


class TestRunPipelineLeaseLostGuard:
    """PR #1046 review comment 2 — a lease lost mid-run must not let this
    instance clobber the authoritative instance's manifest.json.

    Direct, deterministic coverage of run_kb_pipeline.run_pipeline()'s
    ``lease_lost`` guard: whether the Event was set before or during the
    call, run_pipeline() must see it and refuse to write manifest.json.
    """

    def test_lease_lost_prevents_manifest_write(self, tmp_path, monkeypatch):
        monkeypatch.delenv("KB_PIPELINE_ENABLED", raising=False)
        lease_lost = threading.Event()
        lease_lost.set()

        manifest = run_pipeline(artifact_dir=tmp_path, lease_lost=lease_lost)

        assert not (tmp_path / "manifest.json").exists()
        # Still returns a manifest dict to the caller — only the WRITE is guarded.
        assert manifest["status"].startswith("skipped")

    def test_lease_not_lost_writes_manifest_normally(self, tmp_path, monkeypatch):
        monkeypatch.delenv("KB_PIPELINE_ENABLED", raising=False)
        lease_lost = threading.Event()  # never set

        manifest = run_pipeline(artifact_dir=tmp_path, lease_lost=lease_lost)

        written = tmp_path / "manifest.json"
        assert written.exists()
        assert json.loads(written.read_text()) == manifest

    def test_no_lease_lost_event_passed_still_writes_manifest(self, tmp_path, monkeypatch):
        """Backward-compatible default: callers that don't pass ``lease_lost``
        (e.g. the CLI entry point in ``main()``) keep the old unguarded behavior."""
        monkeypatch.delenv("KB_PIPELINE_ENABLED", raising=False)

        run_pipeline(artifact_dir=tmp_path)

        assert (tmp_path / "manifest.json").exists()

    def test_lease_lost_before_the_enabled_pipeline_stage_aborts_early(self, tmp_path, monkeypatch):
        """With KB_PIPELINE_ENABLED set, a lease already lost must abort BEFORE
        the (multi-GB-model) real pipeline stage even starts — a cooperative
        checkpoint, not just a final guard-the-write."""
        monkeypatch.setenv("KB_PIPELINE_ENABLED", "1")
        lease_lost = threading.Event()
        lease_lost.set()

        manifest = run_pipeline(artifact_dir=tmp_path, lease_lost=lease_lost)

        assert not (tmp_path / "manifest.json").exists()
        assert "aborted" in manifest["status"]


class TestLeaseLostMidRunIntegration:
    """PR #1046 review comment 2 — full integration of the renewal thread's
    failure signal reaching run_pipeline()'s guard.

    Event-synchronized (no sleep-based polling): the mocked "pipeline" body
    blocks until the renewal thread has actually recorded a CAS failure,
    then calls through to the real run_pipeline() so the manifest-write
    guard is exercised against a genuinely mid-run lease loss.
    """

    def test_renew_failure_mid_run_prevents_manifest_write(self, fake_sync_redis, tmp_path, monkeypatch):
        monkeypatch.setattr(kb_runner, "ARTIFACT_DIR", tmp_path)
        # Fire the renewal loop's first tick almost immediately instead of
        # waiting the production ~10 minutes.
        monkeypatch.setattr(kb_runner, "LEASE_RENEW_INTERVAL_S", 0.01)
        monkeypatch.delenv("KB_PIPELINE_ENABLED", raising=False)

        def _failing_renew(self, runner_name, token, ttl_ms):
            return False  # simulate a lost CAS — someone else now holds the lease

        monkeypatch.setattr(kb_runner._SyncLeaseClient, "renew", _failing_renew)

        def _pipeline_body(*, lease_lost=None, **_):
            # Block "mid-run" until the renewal thread has genuinely set
            # lease_lost itself — waiting on the Event directly (rather than
            # a side-channel flag set from inside the mocked renew()) avoids
            # a race against _renew_loop's own post-renew bookkeeping, which
            # runs a moment AFTER renew() returns.
            assert lease_lost is not None
            assert lease_lost.wait(timeout=2), "lease_lost was never set"
            return run_pipeline(artifact_dir=tmp_path, lease_lost=lease_lost)

        with patch("archimedes.scripts.run_kb_pipeline.run_pipeline", side_effect=_pipeline_body):
            kb_runner._run_pipeline_with_lease()

        assert not (tmp_path / "manifest.json").exists()
