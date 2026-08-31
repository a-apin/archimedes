"""``assoc/v1`` — the paper-association contract, its hash, and its projections.

Issue #1637, the contract + trace-binding half. The passport's *visual* half is
#1646's; nothing here asserts layout.

Hermetic by construction: in-memory SQLite for every DB test, the corpus lookup
and the Redis trace store mocked at their boundaries. No network, no `.env`.

Each test names the defect it pins. Where a test is a **guard**, the same test
also feeds it the input that must be rejected — a guard nobody has watched
reject something is not known to guard anything (CLAUDE.md § "A guard must be
shown to reject something").
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from archimedes.models.chat import Base
from archimedes.models.paper_assoc import (
    ASSOC_KEYS,
    ASSOC_SCHEMA,
    ROLE_CITED,
    ROLE_CONSIDERED,
    assert_assoc,
    assoc_handle,
    assoc_identity,
    assoc_to_paper_ref,
    is_assoc,
    make_assoc,
    normalize_assoc,
    normalize_assocs,
    paper_ref_to_assoc,
)
from archimedes.models.paper_ref import PaperRef
from archimedes.models.strategy_passport_record import PassportPaperRef
from archimedes.models.strategy_store import (
    StrategyRecord,
    _compute_content_hash,
    upsert_strategy,
)
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


# ── The three historical shapes, verbatim ────────────────────────────────────
#
# Reproduced from the writers as they stood before this issue. They are the
# fixture, not a paraphrase: the whole defect was that these were *different*.
LEGACY_MAIN_PY = [{"arxiv_id": "2301.00001", "title": "Momentum", "authors": ["Ada"]}]
LEGACY_DEBATE = [{"arxiv_id": "2301.00001", "title": ""}]
LEGACY_FUSION_JOB = [{"arxiv_id": "2301.00001", "sha256": ""}]
LEGACY_SHAPES = (LEGACY_MAIN_PY, LEGACY_DEBATE, LEGACY_FUSION_JOB)


# ═══════════════════════════════════════════════════════════════════════════
# 1. One shape
# ═══════════════════════════════════════════════════════════════════════════


class TestAssocSchemaIsSingleShape:
    """Acceptance 1. Before: three key sets. After: one."""

    def test_the_legacy_shapes_really_did_disagree(self):
        """The premise, pinned — so this file cannot quietly test nothing."""
        key_sets = [frozenset(shape[0]) for shape in LEGACY_SHAPES]
        assert len({*key_sets}) == 3, "fixture no longer reproduces the three-shape split"

    def test_every_writer_shape_normalizes_to_one_key_set(self):
        normalized = [normalize_assocs(shape)[0] for shape in LEGACY_SHAPES]
        for a in normalized:
            assert set(a) == ASSOC_KEYS
            assert a["schema"] == ASSOC_SCHEMA
        assert len({frozenset(a) for a in normalized}) == 1

    def test_stored_shape_is_identical_whichever_writer_wrote_it(self, session):
        """The end-to-end version: three writers, one stored key set."""
        stored = []
        for i, shape in enumerate(LEGACY_SHAPES):
            record = upsert_strategy(
                session,
                generation_method="fusion",
                strategy_name=f"s{i}",
                thesis="t",
                source_papers=shape,
                asset_universe=["SPY"],
            )
            stored.append(json.loads(record.source_papers)[0])
        assert len({frozenset(a) for a in stored}) == 1
        for a in stored:
            assert_assoc(a)

    def test_blank_strings_normalize_to_null_not_to_empty(self):
        """``""`` is not a value. ``None`` is the honest absence."""
        a = normalize_assoc({"arxiv_id": "2301.1", "title": "", "sha256": "", "doi": "  "})
        assert a["title"] is None
        assert a["content_hash"] is None
        assert a["doi"] is None

    def test_an_association_naming_nothing_at_all_is_dropped(self):
        """The ``PaperRef(title=strategy_name)`` placeholder is not a paper."""
        assert normalize_assocs([{}]) == []
        assert normalize_assocs([{"arxiv_id": "", "doi": "", "title": "  "}]) == []

    def test_a_curated_reference_with_no_arxiv_id_is_KEPT(self):
        """Every curated strategy in this repo declares ``PAPER_ARXIV_ID = None``.

        An arXiv-only id space would have silently dropped all 34 of them from
        the passport projection. ``doi`` and the case-folded ``title`` are
        documented fallbacks, not optional niceties — see ``assoc_handle``.
        """
        by_doi = normalize_assocs([{"doi": "10.3905/jwm.2007.674809", "title": "Tactical Allocation"}])
        assert len(by_doi) == 1
        assert assoc_handle(by_doi[0]) == "doi:10.3905/jwm.2007.674809"

        by_title = normalize_assocs([{"title": "A Quantitative Approach to Tactical Asset Allocation"}])
        assert len(by_title) == 1
        assert assoc_handle(by_title[0]) == "title:a quantitative approach to tactical asset allocation"

    def test_the_handle_precedence_is_arxiv_then_doi_then_title(self):
        assert assoc_handle(make_assoc("2301.1", doi="10.1/x", title="T")) == "2301.1"
        assert assoc_handle(make_assoc(None, doi="10.1/x", title="T")) == "doi:10.1/x"
        assert assoc_handle(make_assoc(None, title="T")) == "title:t"
        assert assoc_handle(make_assoc(None)) is None

    def test_the_store_and_the_passport_share_ONE_identity_definition(self):
        """A second definition is how the two drift into disagreeing."""
        from archimedes.services.passport_loader import _ref_key

        for arxiv_id, doi, title in (
            ("2301.1", "10.1/x", "T"),
            (None, "10.1/x", "T"),
            (None, None, "Tactical Allocation"),
        ):
            assert _ref_key(arxiv_id, doi, title) == assoc_handle({"arxiv_id": arxiv_id, "doi": doi, "title": title})

    def test_role_is_closed_and_rejects_anything_else(self):
        """GUARD + its adversarial companion, in one test."""
        assert make_assoc("2301.1", role=ROLE_CONSIDERED)["role"] == ROLE_CONSIDERED
        with pytest.raises(ValueError, match="role"):
            make_assoc("2301.1", role="probably")
        with pytest.raises(ValueError, match="role"):
            normalize_assoc({"arxiv_id": "2301.1", "role": "maybe-cited"})

    def test_assert_assoc_rejects_every_legacy_shape(self):
        """GUARD: the validator must not accept what it exists to replace."""
        for shape in LEGACY_SHAPES:
            assert not is_assoc(shape[0])
            with pytest.raises(ValueError, match="key mismatch"):
                assert_assoc(shape[0])
        # …and a record that is assoc/v1 except for a tampered schema tag.
        tampered = make_assoc("2301.1") | {"schema": "assoc/v2"}
        with pytest.raises(ValueError, match="schema tag"):
            assert_assoc(tampered)

    def test_no_legacy_paper_shape_survives_at_the_four_writers(self):
        """GUARD over the writer sources, plus the input it must reject.

        A shape guard that only ran over the current tree would pass forever
        without anyone knowing whether it can fail, so the same predicate is
        run against a literal reintroduction of each legacy shape below.
        """

        def legacy_hits(text: str) -> list[str]:
            # Comment lines are stripped first: several of these files QUOTE the
            # shape they replaced, in the comment explaining why. A guard that
            # counted the explanation as the offence would force the fix to be
            # undocumented to stay green.
            code = "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("#"))
            needles = (
                '"sha256": ""',
                '"arxiv_id": aid, "sha256"',
                '{"arxiv_id": a, "title": ""}',
                'p.get("arxiv_id"), title=p.get("title", "")',
            )
            return [n for n in needles if n in code]

        writers = [
            "backend/archimedes/main.py",
            "backend/archimedes/agents/debate_engine.py",
            "backend/archimedes/agents/generation_pipeline.py",
            "backend/archimedes/api/strategies_routes.py",
        ]
        for rel in writers:
            text = (REPO_ROOT / rel).read_text(encoding="utf-8")
            assert legacy_hits(text) == [], f"{rel} reintroduced a legacy association shape"

        # Adversarial companion: the predicate DOES fire on a reintroduction.
        assert legacy_hits('source_papers = [{"arxiv_id": aid, "sha256": ""} for aid in ids]')
        assert legacy_hits('[{"arxiv_id": a, "title": ""} for a in ids]')


# ═══════════════════════════════════════════════════════════════════════════
# 2. Identity, not shape, is what the hash sees
# ═══════════════════════════════════════════════════════════════════════════


class TestContentHashIgnoresEnrichment:
    """Acceptance 2. Backfilling a title must not fork a strategy."""

    def _h(self, papers):
        return _compute_content_hash("fusion", "Name", "Thesis", papers, ["SPY"])

    def test_enrichment_does_not_change_the_hash(self):
        bare = self._h([{"arxiv_id": "2301.1"}])
        enriched = self._h(
            [
                make_assoc(
                    "2301.1",
                    title="A Paper",
                    authors=["Ada", "Grace"],
                    year=2023,
                    venue="JF",
                    doi="10.1/x",
                    contribution="supplies the timing rule",
                    selection_rank=3,
                    semantic_score=0.81,
                    content_hash="deadbeef",
                )
            ]
        )
        assert bare == enriched

    def test_all_three_legacy_shapes_hash_to_one_value(self):
        assert len({self._h(shape) for shape in LEGACY_SHAPES}) == 1

    def test_order_and_duplicates_do_not_change_identity(self):
        a = self._h([{"arxiv_id": "2301.1"}, {"arxiv_id": "2301.2"}])
        b = self._h([{"arxiv_id": "2301.2"}, {"arxiv_id": "2301.1"}, {"arxiv_id": "2301.1"}])
        assert a == b

    def test_a_doi_identified_paper_keeps_its_identity_under_enrichment(self):
        """The curated case: no arXiv id, so the DOI is the handle — and adding
        authors/year around it must still not move the hash."""
        bare = self._h([{"doi": "10.3905/jwm.2007.674809"}])
        enriched = self._h(
            [make_assoc(None, doi="10.3905/jwm.2007.674809", authors=["Faber"], year=2007, title="Tactical")]
        )
        assert bare == enriched

    def test_the_hash_still_distinguishes_a_different_paper_set(self):
        """The complement — an identity-only hash must not collapse everything."""
        assert self._h([{"arxiv_id": "2301.1"}]) != self._h([{"arxiv_id": "2301.9"}])

    def test_role_is_part_of_identity(self):
        """A considered paper is not a cited one; the two are different strategies."""
        assert self._h([make_assoc("2301.1", role=ROLE_CITED)]) != self._h([make_assoc("2301.1", role=ROLE_CONSIDERED)])

    def test_assoc_identity_emits_only_id_and_role(self):
        rows = assoc_identity([make_assoc("2301.1", title="T", authors=["Ada"])])
        assert rows == [["2301.1", "cited"]]

    def test_two_writers_now_dedup_to_a_single_row(self, session):
        """The user-visible payoff: one strategy, not two."""
        for shape in (LEGACY_DEBATE, LEGACY_FUSION_JOB):
            upsert_strategy(
                session,
                generation_method="fusion",
                strategy_name="Same Strategy",
                thesis="Same thesis",
                source_papers=shape,
                asset_universe=["SPY"],
            )
        assert session.query(StrategyRecord).count() == 1


# ═══════════════════════════════════════════════════════════════════════════
# 3. The pin — what already works and must keep working
# ═══════════════════════════════════════════════════════════════════════════


class TestFullCitedSetSurvivesToPassport:
    """Acceptance 4. This one PASSES on unmodified ``main`` — it is a pin, not
    a regression test. It exists so the write-path surgery above cannot quietly
    drop a paper on its way to the passport."""

    IDS = ["2301.00001", "2301.00002", "2301.00003", "2301.00004"]

    def test_four_ids_reach_store_passport_refs_and_papers_identically(self, session):
        from archimedes.services.passport_loader import ingest_passport

        record = upsert_strategy(
            session,
            generation_method="fusion",
            strategy_name="Four-paper fusion",
            thesis="A synthesis of four mechanisms.",
            source_papers=[make_assoc(i) for i in self.IDS],
            asset_universe=["SPY"],
        )

        in_store = {p["arxiv_id"] for p in json.loads(record.source_papers)}

        passport = record.to_strategy_passport()
        in_papers = {p.arxiv_id for p in passport.papers}

        ingest_passport(session, passport, generation_method="fusion")
        session.flush()
        in_refs = {r.arxiv_id for r in session.query(PassportPaperRef).filter_by(passport_id=record.id).all()}

        assert in_store == set(self.IDS)
        assert in_papers == set(self.IDS)
        assert in_refs == set(self.IDS)

    def test_considered_papers_never_reach_the_passport(self, session):
        """The one addition to the pin: role gates the projection."""
        record = upsert_strategy(
            session,
            generation_method="fusion",
            strategy_name="Two cited, one considered",
            thesis="t",
            source_papers=[
                make_assoc("2301.00001"),
                make_assoc("2301.00002"),
                make_assoc("2301.00009", role=ROLE_CONSIDERED),
            ],
            asset_universe=["SPY"],
        )
        assert len(json.loads(record.source_papers)) == 3
        assert {p.arxiv_id for p in record.to_strategy_passport().papers} == {"2301.00001", "2301.00002"}


# ═══════════════════════════════════════════════════════════════════════════
# 4. Trace binding
# ═══════════════════════════════════════════════════════════════════════════


class TestTraceRecordsEveryCitedPaper:
    """Acceptance 5. Was: one entry, suffixed with the STRATEGY id."""

    def test_every_cited_paper_gets_an_entry(self):
        from archimedes.services.source_tracker import build_consulted_hashes

        assocs = [make_assoc(f"2301.0000{i}") for i in range(1, 5)]
        entries = build_consulted_hashes(assocs)
        assert len(entries) == 4
        assert entries == sorted(entries)

    def test_suffix_is_the_content_hash_or_empty_never_something_else(self):
        from archimedes.services.source_tracker import build_consulted_hashes, split_consulted_entry

        assocs = [make_assoc("2301.1", content_hash="abc"), make_assoc("2301.2")]
        for entry in build_consulted_hashes(assocs):
            arxiv_id, claimed = split_consulted_entry(entry)
            expected = {"2301.1": "abc", "2301.2": ""}[arxiv_id]
            assert claimed == expected

    def test_considered_papers_are_not_bound_to_the_decision(self):
        from archimedes.services.source_tracker import build_consulted_hashes

        entries = build_consulted_hashes([make_assoc("2301.1"), make_assoc("2301.9", role=ROLE_CONSIDERED)])
        assert entries == ["2301.1:"]

    def test_entries_are_deduplicated(self):
        from archimedes.services.source_tracker import build_consulted_hashes

        assert build_consulted_hashes([make_assoc("2301.1"), make_assoc("2301.1")]) == ["2301.1:"]


class TestConstructionTraceCitesPapersInThePaperField:
    """Acceptance 7. Papers out of ``strategies_referenced``, into
    ``consulted_paper_hashes`` — which is what lets ``construction`` join the
    strategy-reference filter without the filter starting to lie."""

    def _trace(self, strategy_id="abcdef0123456789"):
        from archimedes.services.source_tracker import build_consulted_hashes

        assocs = [make_assoc("2301.00001"), make_assoc("2301.00002")]
        return {
            "decision_type": "construction",
            "strategies_referenced": [strategy_id] if strategy_id else [],
            "consulted_paper_hashes": build_consulted_hashes(assocs),
        }

    def test_construction_is_in_the_strategy_reference_scope(self):
        from archimedes.services.redis_state import STRATEGY_REFERENCE_DECISION_TYPES

        assert "construction" in STRATEGY_REFERENCE_DECISION_TYPES

    def test_a_construction_trace_now_joins_to_its_own_strategy(self):
        from archimedes.services.redis_state import trace_references_strategy

        trace = self._trace()
        assert trace["consulted_paper_hashes"]
        assert trace["strategies_referenced"] == ["abcdef0123456789"]
        assert trace_references_strategy(trace, "abcdef0123456789") is True

    def test_an_arxiv_id_in_the_strategy_field_still_matches_nothing(self):
        """GUARD: admitting ``construction`` must not make arXiv ids match.

        This is the adversarial input for the scope widening — a trace written
        the OLD way, fed to the matcher that now sees construction traces.
        """
        from archimedes.services.redis_state import trace_references_strategy

        old_style = {
            "decision_type": "construction",
            "strategies_referenced": ["2301.00001", "2301.00002"],
        }
        assert trace_references_strategy(old_style, "2301.00001") is True  # exact id, honestly matched
        assert trace_references_strategy(old_style, "abcdef0123456789") is False

    def test_a_failed_persist_names_no_strategy_rather_than_inventing_one(self):
        from archimedes.services.redis_state import trace_references_strategy

        trace = self._trace(strategy_id=None)
        assert trace["strategies_referenced"] == []
        assert trace["consulted_paper_hashes"], "papers stay recorded even when the DB write failed"
        assert trace_references_strategy(trace, "abcdef0123456789") is False


# ═══════════════════════════════════════════════════════════════════════════
# 5. Enrichment survives re-ingest
# ═══════════════════════════════════════════════════════════════════════════


def _passport(papers, sid="strat-001"):
    from archimedes.models.strategy import (
        PositionSizing,
        RebalanceFrequency,
        StrategyPassport,
        StrategyStatus,
    )

    return StrategyPassport(
        id=sid,
        papers=papers,
        methodology_summary="Trend following.",
        asset_universe=["SPY"],
        position_sizing=PositionSizing.EQUAL_WEIGHT,
        rebalance_frequency=RebalanceFrequency.DAILY,
        status=StrategyStatus.CANDIDATE,
    )


class TestReingestPreservesEnrichment:
    """Acceptance 8. ``ingest_passport(force_update=True)`` ran on every
    real-returns refresh and DELETEd the refs first, so enrichment could not
    survive by construction."""

    ENRICHED = PaperRef(
        arxiv_id="2301.00001",
        title="Momentum Everywhere",
        authors=["Ada", "Grace"],
        doi="10.1/x",
        venue="JF",
        year=2013,
        citation_count=99,
        contribution="supplies the cross-sectional ranking rule",
        selection_rank=2,
        semantic_score=0.77,
        content_hash="c0ffee",
    )
    #: What the refresh path actually has in hand: id + title, nothing else.
    THIN = PaperRef(arxiv_id="2301.00001", title="Momentum Everywhere")

    def test_backfilled_fields_survive_an_id_and_title_only_refresh(self, session):
        from archimedes.services.passport_loader import ingest_passport

        ingest_passport(session, _passport([self.ENRICHED]), generation_method="fusion")
        session.flush()

        ingest_passport(session, _passport([self.THIN]), generation_method="fusion", force_update=True)
        session.flush()

        refs = session.query(PassportPaperRef).filter_by(passport_id="strat-001").all()
        assert len(refs) == 1, "the merge must not duplicate the row it matched"
        ref = refs[0]
        assert ref.authors == json.dumps(["Ada", "Grace"])
        assert ref.year == 2013
        assert ref.venue == "JF"
        assert ref.doi == "10.1/x"
        assert ref.citation_count == 99
        assert ref.contribution == "supplies the cross-sectional ranking rule"
        assert ref.selection_rank == 2
        assert ref.semantic_score == pytest.approx(0.77)
        assert ref.content_hash == "c0ffee"

    def test_a_real_new_value_still_overwrites(self, session):
        """Merge, not freeze: a caller that HAS a better value must win."""
        from archimedes.services.passport_loader import ingest_passport

        ingest_passport(session, _passport([self.ENRICHED]), generation_method="fusion")
        session.flush()
        corrected = PaperRef(arxiv_id="2301.00001", title="Momentum Everywhere", year=2012)
        ingest_passport(session, _passport([corrected]), generation_method="fusion", force_update=True)
        session.flush()

        ref = session.query(PassportPaperRef).filter_by(passport_id="strat-001").one()
        assert ref.year == 2012
        assert ref.authors == json.dumps(["Ada", "Grace"]), "an unrelated column must not be collateral"

    def test_an_empty_paper_list_is_ignorance_not_a_deletion(self, session):
        from archimedes.services.passport_loader import ingest_passport

        ingest_passport(session, _passport([self.ENRICHED]), generation_method="fusion")
        session.flush()
        ingest_passport(session, _passport([]), generation_method="fusion", force_update=True)
        session.flush()

        assert session.query(PassportPaperRef).filter_by(passport_id="strat-001").count() == 1

    def test_a_genuinely_changed_cited_set_does_drop_the_old_paper(self, session):
        """The complement — merge must not become an append-only pile."""
        from archimedes.services.passport_loader import ingest_passport

        ingest_passport(session, _passport([self.ENRICHED]), generation_method="fusion")
        session.flush()
        replacement = PaperRef(arxiv_id="2409.99999", title="Something Else")
        ingest_passport(session, _passport([replacement]), generation_method="fusion", force_update=True)
        session.flush()

        ids = {r.arxiv_id for r in session.query(PassportPaperRef).filter_by(passport_id="strat-001").all()}
        assert ids == {"2409.99999"}


class TestContributionRoundTrips:
    """Acceptance 9. ``contribution`` was written to the column and then
    silently omitted from ``to_dict()`` — the passport rendered a column the
    projection could never fill."""

    def test_contribution_reaches_the_column_the_dict_and_the_response(self, session):
        from archimedes.api.schemas import PaperRefResponse
        from archimedes.services.passport_loader import ingest_passport

        ref_in = PaperRef(
            arxiv_id="2301.00001",
            title="Momentum Everywhere",
            contribution="supplies the cross-sectional ranking rule",
        )
        ingest_passport(session, _passport([ref_in]), generation_method="fusion")
        session.flush()

        row = session.query(PassportPaperRef).filter_by(passport_id="strat-001").one()
        assert row.contribution == "supplies the cross-sectional ranking rule"

        as_dict = row.to_dict()
        assert "contribution" in as_dict
        assert as_dict["contribution"] == "supplies the cross-sectional ranking rule"

        response = PaperRefResponse(**{k: v for k, v in as_dict.items() if k in PaperRefResponse.model_fields})
        assert response.contribution == "supplies the cross-sectional ranking rule"

    def test_it_survives_the_assoc_round_trip(self):
        a = paper_ref_to_assoc(PaperRef(arxiv_id="2301.1", contribution="the vol overlay"))
        assert a["contribution"] == "the vol overlay"
        assert assoc_to_paper_ref(a).contribution == "the vol overlay"

    def test_to_dict_never_prints_an_empty_title_as_a_title(self):
        row = PassportPaperRef(passport_id="p", arxiv_id="2301.1", title="")
        assert row.to_dict()["title"] is None


# ═══════════════════════════════════════════════════════════════════════════
# 6. Honest rendering of the degenerate cases (backend half of acceptance 12)
# ═══════════════════════════════════════════════════════════════════════════


class TestSingleAndZeroPaperPassportsAreHonest:
    """Acceptance 12, backend half. The ``length > 1`` render condition and the
    bare-quotes markup at ``StrategyPassport.jsx:604`` belong to #1646; what is
    tested here is the payload those renderers receive.

    Both cases go through the REAL response builder
    (``_passport_to_strategy_response``), not through a re-derivation of what it
    ought to return — a test that computes ``x or None`` itself would pass
    identically against the unfixed code.
    """

    @staticmethod
    def _response(session, sid, papers):
        from archimedes.api.strategies_routes import _passport_to_strategy_response
        from archimedes.services.passport_loader import get_passport, ingest_passport

        ingest_passport(session, _passport(papers, sid=sid), generation_method="fusion")
        session.flush()
        return _passport_to_strategy_response(get_passport(session, sid), session=session)

    def test_one_paper_row_projects_exactly_one_paper(self, session):
        resp = self._response(session, "one", [PaperRef(arxiv_id="2301.1", title="Only Paper")])
        assert len(resp.papers) == 1
        assert resp.papers[0].arxiv_id == "2301.1"
        assert resp.paper_title == "Only Paper"

    def test_zero_paper_row_reports_a_null_title_not_empty_quotes(self, session):
        resp = self._response(session, "none", [])
        assert resp.papers == []
        assert resp.paper_title is None
        assert resp.paper_arxiv_id is None


# ═══════════════════════════════════════════════════════════════════════════
# 7. No semantic claims on provenance surfaces
# ═══════════════════════════════════════════════════════════════════════════


class TestNoSemanticClaimOnProvenanceSurfaces:
    """Acceptance 13 / precedent #778. Retrieval is LEXICAL: the corpus has no
    embedding column, ``corpus_meta`` is 0 and the KG is 0/0. A provenance
    payload that says "semantic" or "knowledge graph" is making a claim the
    system cannot back."""

    #: The builders that assemble what a passport or trace SAYS about itself.
    PAYLOAD_BUILDERS = (
        "backend/archimedes/models/paper_assoc.py",
        "backend/archimedes/models/strategy_passport_record.py",
        "backend/archimedes/models/paper_ref.py",
        "backend/archimedes/services/source_tracker.py",
    )

    #: Not in the list on purpose: ``semantic_score`` is a FIELD NAME for the
    #: reranker's own output, which is a real measured number when a rerank
    #: ran and ``None`` when it did not. The banned thing is prose asserting
    #: semantic/embedding/knowledge-graph retrieval to a reader.
    BANNED = ("embedding", "knowledge graph", "knowledge-graph")

    @staticmethod
    def _claims(text: str, banned) -> list[str]:
        lowered = text.lower()
        return [w for w in banned if w in lowered]

    def test_provenance_payload_builders_make_no_semantic_claim(self):
        for rel in self.PAYLOAD_BUILDERS:
            text = (REPO_ROOT / rel).read_text(encoding="utf-8")
            assert self._claims(text, self.BANNED) == [], f"{rel} asserts retrieval it cannot back"

    def test_the_guard_fires_on_a_claim(self):
        """GUARD's adversarial companion — the predicate must be able to fail."""
        assert self._claims("papers found by embedding similarity", self.BANNED) == ["embedding"]
        assert self._claims("sourced from the Knowledge Graph", self.BANNED) == ["knowledge graph"]

    def test_the_honest_vocabulary_is_available_instead(self):
        """``semantic_score`` is nullable — absence is how "no rerank" is said."""
        a = make_assoc("2301.1")
        assert a["semantic_score"] is None
        assert a["selection_rank"] is None


# ═══════════════════════════════════════════════════════════════════════════
# 8. The verifier has a caller
# ═══════════════════════════════════════════════════════════════════════════


class TestVerifySourcePapersIsWired:
    """Acceptance 6. ``verify_source_papers`` had zero production callers;
    ``/verify`` re-hashed the body and never checked the papers."""

    @pytest.fixture
    def anyio_backend(self):
        return "asyncio"

    @staticmethod
    def _off_chain(consulted):
        return {
            "id": "trace-1",
            "vault_address": "",
            "trace_hash": "0xaabbccdd",
            "arc_tx_hash": None,
            "consulted_paper_hashes": consulted,
        }

    async def _verify(self, off_chain, corpus_hashes=None, corpus_error=None):
        from httpx import ASGITransport, AsyncClient

        from archimedes.main import app
        from archimedes.services.redis_state import AgentStateStore

        target = "archimedes.services.source_tracker.corpus_content_hashes"
        kwargs = {"side_effect": corpus_error} if corpus_error else {"return_value": corpus_hashes or {}}
        with (
            patch.object(AgentStateStore, "get_trace", AsyncMock(return_value=off_chain)),
            patch.object(AgentStateStore, "close", AsyncMock()),
            patch(target, **kwargs),
            patch("archimedes.api.traces_routes._assert_can_read", AsyncMock(return_value=None)),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                return await client.get("/api/traces/1/verify")

    @pytest.mark.anyio
    async def test_absent_paper_reports_papers_verified_false_and_names_it(self):
        resp = await self._verify(
            self._off_chain(["2301.00001:", "2999.99999:"]),
            corpus_hashes={"2301.00001": ""},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["papers_verified"] is False
        assert body["source_paper_verification"]["missing"] == ["2999.99999"]
        assert body["source_paper_verification"]["mode"] == "checked"

    @pytest.mark.anyio
    async def test_present_papers_report_verified_true(self):
        resp = await self._verify(
            self._off_chain(["2301.00001:", "2301.00002:"]),
            corpus_hashes={"2301.00001": "", "2301.00002": ""},
        )
        body = resp.json()
        assert body["papers_verified"] is True
        assert body["source_paper_verification"]["checked"] == 2
        assert body["source_paper_verification"]["missing"] == []

    @pytest.mark.anyio
    async def test_a_claimed_hash_that_disagrees_is_a_mismatch(self):
        resp = await self._verify(
            self._off_chain(["2301.00001:deadbeef"]),
            corpus_hashes={"2301.00001": "c0ffee"},
        )
        body = resp.json()
        assert body["papers_verified"] is False
        assert body["source_paper_verification"]["hash_mismatch"] == ["2301.00001"]

    @pytest.mark.anyio
    async def test_no_claimed_papers_is_not_checked_rather_than_passed(self):
        resp = await self._verify(self._off_chain([]))
        body = resp.json()
        assert body["papers_verified"] is None
        assert body["source_paper_verification"]["mode"] == "no_papers_claimed"

    @pytest.mark.anyio
    async def test_a_corpus_outage_is_not_a_provenance_failure(self):
        """GUARD, adversarial: an outage must not read as either verdict."""
        from archimedes.services.source_tracker import CorpusUnavailable

        resp = await self._verify(
            self._off_chain(["2301.00001:"]),
            corpus_error=CorpusUnavailable("down"),
        )
        body = resp.json()
        assert body["papers_verified"] is None
        assert body["source_paper_verification"]["mode"] == "corpus_unavailable"
        assert body["source_paper_verification"]["missing"] == []

    def test_empty_claimed_hash_asserts_nothing_and_is_not_a_mismatch(self):
        """#1091: the corpus columns are NULL, so existence is all we can check."""
        from archimedes.services.source_tracker import verify_source_papers

        result = verify_source_papers(["2301.1:"], [{"arxiv_id": "2301.1", "content_hash": ""}])
        assert result == {"verified": True, "missing": [], "hash_mismatch": []}
