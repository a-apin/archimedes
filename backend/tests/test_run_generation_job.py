"""The out-of-process generation entrypoint (#1411 spike).

The guard under test is not hypothetical. The spike's first real Lambda
invocation failed with

    redis.exceptions.ConnectionError: … Connect call failed ('127.0.0.1', 6379)

because ``archimedes/services/__init__.py`` re-exports ``generation_pipeline``,
so importing ``secrets_service`` to *fetch* the secrets already imported
``job_queue`` and froze ``REDIS_URL`` at its localhost default. The dangerous
version of that bug is not the crash — it is the near miss where a localhost
Redis happens to answer and a paid job's events go somewhere nobody reads.
"""

from __future__ import annotations

import pytest
from archimedes.scripts import run_generation_job as entry


class _FakeStore:
    def __init__(self, url: str) -> None:
        self._url = url


class TestJobStoreBinding:
    def test_localhost_store_is_refused_in_production(self, monkeypatch):
        """The input that SHOULD fail: production signal on, store on loopback."""
        monkeypatch.setenv("PUBLIC_DOMAIN", "https://archimedes-arc.com")
        with pytest.raises(RuntimeError, match="refusing to start"):
            entry._require_configured_store(_FakeStore("redis://localhost:6379/0"))

    def test_localhost_store_is_fine_outside_production(self, monkeypatch):
        """Local dev genuinely runs against a loopback Redis — no false alarm."""
        monkeypatch.delenv("PUBLIC_DOMAIN", raising=False)
        store = _FakeStore("redis://localhost:6379/0")
        assert entry._require_configured_store(store) is store

    def test_a_configured_store_passes_in_production(self, monkeypatch):
        monkeypatch.setenv("PUBLIC_DOMAIN", "https://archimedes-arc.com")
        store = _FakeStore("rediss://archimedes.cache.amazonaws.com:6379/0")
        assert entry._require_configured_store(store) is store

    def test_store_is_bound_to_the_env_not_the_import_time_constant(self, monkeypatch):
        """The fix itself: REDIS_URL set AFTER job_queue was imported still wins.

        ``job_queue`` is already in ``sys.modules`` by the time this test runs
        (the test suite imports the pipeline), so its module-level ``REDIS_URL``
        is frozen — exactly the production-worker situation. The store must
        still come out bound to the value in the environment now.
        """
        monkeypatch.setenv("PUBLIC_DOMAIN", "https://archimedes-arc.com")
        monkeypatch.setenv("REDIS_URL", "rediss://set-after-import:6379/0")

        store = entry._bind_job_store()

        assert store._url == "rediss://set-after-import:6379/0"

    def test_an_explicit_store_is_passed_through_untouched(self):
        sentinel = _FakeStore("redis://localhost:6379/0")
        assert entry._bind_job_store(sentinel) is sentinel


class TestEventParsing:
    def test_brief_is_validated_by_the_production_model(self):
        brief = entry._coerce_brief({"intent": "momentum on ETFs", "max_papers": 5})
        assert brief.intent == "momentum on ETFs"
        assert brief.risk_appetite == "moderate"

    def test_brief_accepts_a_json_string(self):
        brief = entry._coerce_brief('{"intent": "carry trade"}')
        assert brief.intent == "carry trade"

    def test_out_of_range_field_is_rejected_here_too(self):
        """An offloaded worker must not accept a brief the HTTP route would refuse."""
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            entry._coerce_brief({"intent": "x", "max_papers": 9999})

    def test_non_object_brief_is_a_type_error(self):
        with pytest.raises(TypeError):
            entry._coerce_brief(42)

    async def test_missing_job_id_is_refused_before_any_work(self):
        with pytest.raises(ValueError, match="job_id"):
            await entry.run_job({"brief": {"intent": "x"}})


class TestBootstrap:
    def test_ssm_is_not_read_without_the_production_signal(self, monkeypatch):
        """Mirrors main.py's #1044 gate: no PUBLIC_DOMAIN, no SSM fetch."""
        monkeypatch.setattr(entry, "_BOOTSTRAPPED", False)
        monkeypatch.delenv("PUBLIC_DOMAIN", raising=False)
        monkeypatch.setenv("AWS_SSM_PATH_PREFIX", "/archimedes/prod/")

        called = []
        import archimedes.services.secrets_service as secrets

        monkeypatch.setattr(secrets, "load_ssm_secrets", lambda *a, **k: called.append(1) or 1)

        assert entry.bootstrap_environment() == 0
        assert called == []

    def test_bootstrap_is_idempotent(self, monkeypatch):
        monkeypatch.setattr(entry, "_BOOTSTRAPPED", True)
        assert entry.bootstrap_environment() == 0
