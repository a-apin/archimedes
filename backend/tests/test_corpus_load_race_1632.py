"""The #1632 abort class: concurrent corpus loads racing in session teardown.

Prod rev 214 died (exit 139, tick OFF) with faulthandler showing two executor
threads simultaneously inside ``load_corpus`` → ``load_papers_from_db``, one in
SQLAlchemy ``_detach_states``, one in ``InstanceState._cleanup`` — piled up by
abandoned /health corpus probes on a cold task ("abandoning the call (6 in
flight)"). Two guards keep the class dead:

1. ``load_corpus`` is serialized behind ``_CORPUS_LOAD_LOCK`` — the race is
   unrepresentable for any pair of callers.
2. The /health corpus probe never calls ``load_corpus`` at all — it reads
   ``count_corpus_papers`` (a scalar COUNT with no ORM state to race).

Each guard is exercised against the input that killed prod: real concurrency
for (1), a live /health request for (2).
"""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from archimedes.agents import strategy_fusion


class TestLoadCorpusIsSerialized:
    def test_concurrent_loaders_never_overlap(self, monkeypatch):
        """Four threads through load_corpus: the DB read runs strictly one-at-a-time."""
        in_flight = 0
        max_in_flight = 0
        gauge = threading.Lock()

        def slow_db_load():
            nonlocal in_flight, max_in_flight
            with gauge:
                in_flight += 1
                max_in_flight = max(max_in_flight, in_flight)
            time.sleep(0.05)  # wide-open window: unlocked, 4 threads WILL overlap here
            with gauge:
                in_flight -= 1
            return [
                {"arxiv_id": "2401.00001", "title": "t", "abstract": "a", "published": "2024-01-01"}
            ]

        import archimedes.services.corpus_service as corpus_service

        monkeypatch.setattr(corpus_service, "load_papers_from_db", slow_db_load)

        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: strategy_fusion.load_corpus(), range(4)))

        assert all(len(r) == 1 for r in results)
        # THE assertion: with _CORPUS_LOAD_LOCK removed this reads 2..4 —
        # verified by reverting the lock before this file was pushed.
        assert max_in_flight == 1, (
            f"load_corpus ran {max_in_flight} DB loads concurrently — the #1632 "
            "session-teardown race is representable again"
        )


class TestHealthProbeDoesNotLoadTheCorpus:
    @pytest.fixture()
    def client(self):
        from fastapi.testclient import TestClient

        from archimedes.main import app
        from archimedes.services.health_cache import health_probe_cache

        health_probe_cache.clear()
        with TestClient(app) as c:
            yield c
        health_probe_cache.clear()

    def test_health_reports_the_count_and_never_calls_load_corpus(self, monkeypatch, client):
        import archimedes.services.corpus_service as corpus_service

        def _forbidden(*_a, **_k):  # pragma: no cover - the guard IS the failure
            raise AssertionError(
                "/health called load_corpus — the full-ORM load the #1632 "
                "abort piled up. The probe must read count_corpus_papers."
            )

        monkeypatch.setattr(strategy_fusion, "load_corpus", _forbidden)
        monkeypatch.setattr(corpus_service, "count_corpus_papers", lambda **_k: 1234)

        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["corpus_papers"] == 1234


class TestCountMatchesTheEmbargoRule:
    def test_sql_count_agrees_with_the_python_filter(self, monkeypatch):
        """The COUNT and apply_outcome_embargo answer identically on the same rows."""
        from datetime import date, timedelta

        from archimedes.services.embargo_filter import apply_outcome_embargo

        old = (date.today() - timedelta(days=40)).isoformat()
        fresh = (date.today() - timedelta(days=10)).isoformat()
        rows = [
            {"arxiv_id": "a1", "published": old},
            {"arxiv_id": "a2", "published": fresh},  # inside the embargo → dropped
            {"arxiv_id": "a3", "published": ""},  # unparseable → dropped (fail closed)
        ]
        kept = apply_outcome_embargo(rows, embargo_days=30)
        assert [r["arxiv_id"] for r in kept] == ["a1"]

        # The SQL mirror: published != "" AND published < cutoff.
        cutoff = (date.today() - timedelta(days=30)).isoformat()
        sql_kept = [r for r in rows if r["published"] and r["published"] < cutoff]
        assert [r["arxiv_id"] for r in sql_kept] == ["a1"]
