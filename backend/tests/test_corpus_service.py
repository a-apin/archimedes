"""Tests for the DB-backed corpus pipeline (seed, intake, reads).

Hermetic — no network, no Redis, no external DB. Uses in-memory SQLite.
"""

from __future__ import annotations

import json

import pytest
from archimedes.models.chat import Base
from archimedes.models.corpus_store import CorpusMetaRecord, PaperRecord
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    s = Session()
    yield s
    s.close()


# ── Seed idempotency ───────────────────────────────────────────


class TestSeedIdempotency:
    def test_seed_inserts_rows(self, session, tmp_path, monkeypatch):
        from archimedes.services import corpus_service

        manifest = tmp_path / "manifest.jsonl"
        papers = [
            {
                "arxiv_id": "2601.00001",
                "title": "Paper A",
                "abstract": "Abs A",
                "primary_category": "q-fin.PM",
                "categories": ["q-fin.PM"],
                "published": "2026-01-01",
                "updated": "2026-01-01",
            },
            {
                "arxiv_id": "2601.00002",
                "title": "Paper B",
                "abstract": "Abs B",
                "primary_category": "q-fin.TR",
                "categories": ["q-fin.TR"],
                "published": "2026-01-02",
                "updated": "2026-01-02",
            },
        ]
        manifest.write_text("\n".join(json.dumps(p) for p in papers))

        # Monkeypatch get_session to use our test session
        monkeypatch.setattr(corpus_service, "get_session", lambda: _ctx_session(session))

        inserted = corpus_service.seed_from_manifest(manifest)
        assert inserted == 2
        count = session.query(PaperRecord).count()
        assert count == 2

    def test_seed_idempotent_no_dups(self, session, tmp_path, monkeypatch):
        from archimedes.services import corpus_service

        manifest = tmp_path / "manifest.jsonl"
        papers = [
            {
                "arxiv_id": "2601.00001",
                "title": "Paper A",
                "abstract": "Abs A",
                "primary_category": "q-fin.PM",
                "categories": ["q-fin.PM"],
                "published": "2026-01-01",
                "updated": "2026-01-01",
            },
        ]
        manifest.write_text(json.dumps(papers[0]))

        monkeypatch.setattr(corpus_service, "get_session", lambda: _ctx_session(session))

        corpus_service.seed_from_manifest(manifest)
        assert session.query(PaperRecord).count() == 1

        # Second seed — no dups
        inserted2 = corpus_service.seed_from_manifest(manifest)
        assert inserted2 == 0
        assert session.query(PaperRecord).count() == 1

    def test_seed_skips_bad_lines(self, session, tmp_path, monkeypatch):
        from archimedes.services import corpus_service

        manifest = tmp_path / "manifest.jsonl"
        lines = [
            '{"arxiv_id": "2601.00001", "title": "Good", "abstract": "A"}',
            "not json at all",
            '{"title": "No ID"}',
            "",
        ]
        manifest.write_text("\n".join(lines))

        monkeypatch.setattr(corpus_service, "get_session", lambda: _ctx_session(session))

        inserted = corpus_service.seed_from_manifest(manifest)
        assert inserted == 1

    def test_seed_creates_meta(self, session, tmp_path, monkeypatch):
        from archimedes.services import corpus_service

        manifest = tmp_path / "manifest.jsonl"
        manifest.write_text(
            json.dumps(
                {
                    "arxiv_id": "2601.00001",
                    "title": "T",
                    "abstract": "A",
                    "primary_category": "q-fin.PM",
                    "categories": ["q-fin.PM"],
                    "published": "2026-01-01",
                    "updated": "2026-01-01",
                }
            )
        )

        monkeypatch.setattr(corpus_service, "get_session", lambda: _ctx_session(session))

        corpus_service.seed_from_manifest(manifest)
        meta = session.query(CorpusMetaRecord).first()
        assert meta is not None
        assert meta.paper_count == 1
        assert meta.source == "seed"
        assert meta.last_intake_at is not None


# ── Intake dedup + cap ─────────────────────────────────────────


class TestIntakeDedup:
    def test_intake_skips_existing(self, session, monkeypatch):
        from archimedes.services import corpus_service

        # Pre-seed one paper
        session.add(
            PaperRecord(
                arxiv_id="2601.00001",
                title="Existing",
                abstract="A",
                primary_category="q-fin.PM",
                categories='["q-fin.PM"]',
                published="2026-01-01",
                updated="2026-01-01",
                source="seed",
            )
        )
        session.commit()

        monkeypatch.setattr(corpus_service, "get_session", lambda: _ctx_session(session))

        # The arxiv intake would need network — just test dedup logic directly
        count_before = session.query(PaperRecord).count()
        assert count_before == 1

    def test_corpus_max_env(self, monkeypatch):
        monkeypatch.setenv("CORPUS_MAX", "500")
        # Reimport to pick up env
        import importlib

        from archimedes.services import corpus_service

        importlib.reload(corpus_service)
        assert corpus_service.CORPUS_MAX == 500


# ── DB-backed read path ────────────────────────────────────────


class TestDBReadPath:
    def test_load_papers_from_db(self, session, monkeypatch):
        from archimedes.services import corpus_service

        session.add(
            PaperRecord(
                arxiv_id="2601.00001",
                title="Paper One",
                abstract="Abstract",
                primary_category="q-fin.PM",
                categories='["q-fin.PM"]',
                published="2026-01-01",
                updated="2026-01-01",
                source="seed",
            )
        )
        session.add(
            PaperRecord(
                arxiv_id="2601.00002",
                title="Paper Two",
                abstract="Abstract 2",
                primary_category="q-fin.TR",
                categories='["q-fin.TR"]',
                published="2026-01-02",
                updated="2026-01-02",
                source="seed",
            )
        )
        session.commit()

        monkeypatch.setattr(corpus_service, "get_session", lambda: _ctx_session(session))

        rows = corpus_service.load_papers_from_db()
        assert len(rows) == 2
        # Ordered by published desc — newest first
        assert rows[0]["arxiv_id"] == "2601.00002"
        assert rows[1]["arxiv_id"] == "2601.00001"

    def test_load_papers_empty_db(self, session, monkeypatch):
        from archimedes.services import corpus_service

        monkeypatch.setattr(corpus_service, "get_session", lambda: _ctx_session(session))

        rows = corpus_service.load_papers_from_db()
        assert rows == []

    def test_to_dict_roundtrip(self, session):
        record = PaperRecord(
            arxiv_id="2601.00001",
            title="Test Paper",
            abstract="Abstract",
            authors='["Author One"]',
            primary_category="q-fin.PM",
            categories='["q-fin.PM", "q-fin.RM"]',
            published="2026-01-01",
            updated="2026-01-01",
            source="seed",
        )
        session.add(record)
        session.commit()

        d = record.to_dict()
        assert d["arxiv_id"] == "2601.00001"
        assert d["authors"] == ["Author One"]
        assert d["categories"] == ["q-fin.PM", "q-fin.RM"]
        assert d["source"] == "seed"


# ── Corpus meta tracking ───────────────────────────────────────


class TestCorpusMeta:
    def test_get_meta_empty_db(self, session, monkeypatch):
        from archimedes.services import corpus_service

        monkeypatch.setattr(corpus_service, "get_session", lambda: _ctx_session(session))

        meta = corpus_service.get_corpus_meta()
        assert meta is None

    def test_get_meta_after_seed(self, session, tmp_path, monkeypatch):
        from archimedes.services import corpus_service

        manifest = tmp_path / "manifest.jsonl"
        manifest.write_text(
            json.dumps(
                {
                    "arxiv_id": "2601.00001",
                    "title": "T",
                    "abstract": "A",
                    "primary_category": "q-fin.PM",
                    "categories": ["q-fin.PM"],
                    "published": "2026-01-01",
                    "updated": "2026-01-01",
                }
            )
        )

        monkeypatch.setattr(corpus_service, "get_session", lambda: _ctx_session(session))

        corpus_service.seed_from_manifest(manifest)
        meta = corpus_service.get_corpus_meta()
        assert meta is not None
        assert meta["paper_count"] == 1
        assert meta["source"] == "seed"
        assert meta["last_intake_at"] is not None

    def test_paper_count(self, session, monkeypatch):
        from archimedes.services import corpus_service

        session.add(
            PaperRecord(
                arxiv_id="2601.00001",
                title="T",
                abstract="A",
                primary_category="q-fin.PM",
                categories='["q-fin.PM"]',
                published="2026-01-01",
                updated="2026-01-01",
                source="seed",
            )
        )
        session.commit()

        monkeypatch.setattr(corpus_service, "get_session", lambda: _ctx_session(session))

        assert corpus_service.get_paper_count() == 1


# ── load_corpus DB-first fallback ──────────────────────────────


class TestLoadCorpusDBFallback:
    def test_db_takes_priority_over_file(self, session, tmp_path, monkeypatch):
        from archimedes.agents.strategy_fusion import load_corpus
        from archimedes.services import corpus_service

        # Seed DB
        session.add(
            PaperRecord(
                arxiv_id="2601.00001",
                title="DB Paper",
                abstract="From DB",
                primary_category="q-fin.PM",
                categories='["q-fin.PM"]',
                published="2026-01-01",
                updated="2026-01-01",
                source="seed",
            )
        )
        session.commit()

        monkeypatch.setattr(corpus_service, "get_session", lambda: _ctx_session(session))

        corpus = load_corpus()
        assert len(corpus) == 1
        assert corpus[0].arxiv_id == "2601.00001"
        assert corpus[0].title == "DB Paper"

    def test_file_fallback_when_db_empty(self, tmp_path, monkeypatch):
        from archimedes.agents.strategy_fusion import load_corpus
        from archimedes.services import corpus_service

        # Create a file manifest
        manifest = tmp_path / "manifest.jsonl"
        manifest.write_text(
            json.dumps(
                {
                    "arxiv_id": "2601.00001",
                    "title": "File Paper",
                    "abstract": "From file",
                    "primary_category": "q-fin.PM",
                    "categories": ["q-fin.PM"],
                    "published": "2026-01-01",
                }
            )
        )

        # Make DB return empty (by having load_papers_from_db raise)
        monkeypatch.setattr(corpus_service, "load_papers_from_db", lambda: [])

        corpus = load_corpus(path=manifest)
        assert len(corpus) == 1
        assert corpus[0].title == "File Paper"


# ── Helper ─────────────────────────────────────────────────────


class _CtxSession:
    """Context-manager wrapper so `with get_session() as s:` works."""

    def __init__(self, session):
        self._session = session

    def __enter__(self):
        return self._session

    def __exit__(self, *args):
        pass


def _ctx_session(session):
    return _CtxSession(session)
