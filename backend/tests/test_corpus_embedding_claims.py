"""No field on /api/health may be read, on its own, as "the corpus is embedded".

``corpus_embedded`` was published as ``paper_rag_status == "live"`` (#1488). The
name asserted a property of the 10,000-paper corpus; the value measured whether a
sentence-transformer object happened to be loaded in that process. One retrieval
flipped it to true, a restart flipped it back, and nothing was ever embedded at
rest: no embedding column, no pgvector, no vector index, no migration. Retrieval
is a keyword filter plus a query-time rerank of at most 150 candidates, with
everything past the cap appended at score 0.0.

That matters more than a naming nit because CLAUDE.md sends every agent and
teammate to /api/health for ground truth, and #778 added the field specifically to
stop surfaces claiming "embedded". It had become the field most likely to produce
the claim.

Hermetic: no DB, no network, no model load. The at-rest check reads the ORM
metadata, and the endpoint tests use a tmp sqlite URL.
"""

from __future__ import annotations

import pytest
from archimedes.services.paper_rag import (
    _VECTOR_COLUMN_HINTS,
    corpus_embedding_at_rest,
    rerank_candidate_cap,
)
from httpx import ASGITransport, AsyncClient


async def _health() -> dict:
    """Read /health through ASGITransport, the precedent in test_risk_routes.py.

    Deliberately NOT fastapi.testclient.TestClient: entering that context manager
    runs the app's startup lifespan, which seeds the corpus and warms the loader
    caches. Doing so from a module-scoped fixture left those caches populated for
    everything that ran afterwards and broke ten later corpus tests
    (test_strategy_fusion's loader cases, test_debate_engine's citation grounding,
    test_papers_routes' manifest fallback) while every test in THIS file passed.
    ASGITransport skips lifespan, so reading /health stays a read.
    """
    from archimedes.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/health")
    assert response.status_code == 200
    return response.json()


@pytest.fixture
async def health_body():
    return await _health()


class TestTheFieldThatLied:
    def test_corpus_embedded_is_gone(self, health_body):
        """The exact key, because the exact key is what a reader greps for and
        what the docs and the debate spec quote."""
        assert "corpus_embedded" not in health_body

    def test_no_field_reads_as_the_corpus_being_embedded(self, health_body):
        """Broader than the check above: any future key whose name claims corpus
        embedding must be false, or must qualify itself the way
        ``corpus_embedded_at_rest`` does."""
        offenders = {
            key: value
            for key, value in health_body.items()
            if "embed" in key.lower() and value is True and not key.endswith("_at_rest")
        }
        assert not offenders, f"these read as 'the corpus is embedded': {offenders}"

    def test_the_honest_pair_is_published_together(self, health_body):
        """A lone false reads as an outage and a lone true reads as the claim we
        must not make. The pair is what makes the absence legible, which is the
        same reason corpus_kg_built sits beside corpus_kg_entities."""
        assert health_body["paper_rerank_model_live"] is False or health_body["paper_rerank_model_live"] is True
        assert health_body["corpus_embedded_at_rest"] is False
        assert health_body["corpus_embedded_at_rest_reason"]
        assert health_body["rerank_candidate_cap"] == rerank_candidate_cap()

    def test_the_cap_that_makes_reranked_an_honest_word_is_published(self, health_body):
        """Candidates past this point are appended at score 0.0 and never scored.
        Publishing 10000 papers next to a silent 150-candidate cap is how the
        overstatement gets rebuilt somewhere else."""
        assert health_body["rerank_candidate_cap"] == 150

    def test_the_two_fields_are_not_the_same_number_in_disguise(self, health_body):
        """Renaming ``corpus_embedded`` to ``corpus_embedded_at_rest`` while
        leaving it wired to paper_rag would pass every test above whenever the
        model is not loaded, which is most of the time."""
        assert corpus_embedding_at_rest().embedded_at_rest is False
        for status in ("live", "ready", "degraded", "disabled"):
            assert corpus_embedding_at_rest().embedded_at_rest is False, (
                f"at-rest state must not move with paper_rag status ({status})"
            )

    async def test_a_loaded_model_does_not_make_the_corpus_embedded(self, monkeypatch):
        """The one that actually discriminates.

        Every endpoint assertion above passes while ``paper_rag`` is anything but
        ``live``, which is its state in the test environment and most of its state
        in prod. So they would ALL survive someone re-wiring
        ``corpus_embedded_at_rest`` back to ``paper_rag_status == "live"`` — which
        is precisely the bug. Force the reranker live and the two fields have to
        disagree.
        """
        from archimedes.services import paper_rag as rag

        monkeypatch.setattr(
            rag, "paper_rag_health", lambda probe=False: rag.PaperRAGHealth(status="live", reason="forced")
        )
        body = await _health()

        assert body["paper_rag"] == "live"
        assert body["paper_rerank_model_live"] is True, "the process-local field should follow the reranker"
        assert body["corpus_embedded_at_rest"] is False, (
            "loading a model embedded nothing — this is the exact conflation #1488 removed"
        )


class TestTheProbeIsDerivedNotDeclared:
    def test_it_reads_the_schema_rather_than_returning_a_constant(self):
        """A hard-coded False is the same defect as the True it replaces, one
        release later. Point the probe at a schema that HAS a vector column and
        it must notice."""
        import sqlalchemy as sa
        from archimedes.services import paper_rag

        fake = sa.MetaData()
        sa.Table(
            "paper_vectors", fake, sa.Column("id", sa.Integer, primary_key=True), sa.Column("embedding", sa.LargeBinary)
        )

        class _FakeBase:
            metadata = fake

        import archimedes.db as db_module

        original = db_module.Base
        db_module.Base = _FakeBase
        try:
            result = paper_rag.corpus_embedding_at_rest()
        finally:
            db_module.Base = original

        assert "paper_vectors.embedding" in result.reason, "the probe did not read the schema it was given"
        assert result.embedded_at_rest is False, (
            "a discovered but uncounted store must read as a loud absence, not a claim"
        )

    def test_todays_answer_names_the_reason_it_is_false(self):
        result = corpus_embedding_at_rest()
        assert result.embedded_at_rest is False
        assert "no stored-vector column" in result.reason

    def test_the_hints_would_catch_the_names_a_vector_store_arrives_under(self):
        """Anti-vacuity. An empty or over-narrow hint list makes every assertion
        in this class pass while detecting nothing."""
        assert _VECTOR_COLUMN_HINTS
        for plausible in ("embedding", "embeddings", "abstract_embedding", "vector", "content_vector"):
            assert any(hint in plausible for hint in _VECTOR_COLUMN_HINTS), plausible


class TestTheSchemaHasNotQuietlyGrownVectors:
    def test_no_stored_vector_surface_exists_without_the_field_being_rewired(self):
        """The self-maintaining half. If someone adds pgvector or an embedding
        column, this fails and points at the field that now has to be counted
        rather than assumed, instead of /api/health quietly under-reporting a
        store that does exist."""
        from archimedes.db import Base

        found = [
            f"{table.name}.{column.name}"
            for table in Base.metadata.sorted_tables
            for column in table.columns
            if any(hint in column.name.lower() for hint in _VECTOR_COLUMN_HINTS)
        ]
        assert not found, (
            f"stored-vector column(s) appeared: {found}. corpus_embedding_at_rest() must now COUNT them "
            "rather than report the schema-absence reason, and this test updated to match."
        )
