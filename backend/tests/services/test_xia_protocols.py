"""Tests for Xia et al. 2026 named protocols — Issues #156.

Tests for:
  1. Outcome Embargo (embargo_filter.py)
  2. Time-Aware Retrieval (time_aware_retrieval.py)
  3. Source Tracking (source_tracker.py)
  4. V_check (chain/v_check.py)
  5. Integration: trace model includes consulted_paper_hashes
"""

from __future__ import annotations

from datetime import date

# ── 1. Outcome Embargo ───────────────────────────────────────────────────


class TestOutcomeEmbargo:
    """Outcome Embargo — Xia § 4.2."""

    def test_paper_old_enough_passes(self):
        """Paper published before the embargo window is included."""
        from archimedes.services.embargo_filter import apply_outcome_embargo

        papers = [{"arxiv_id": "2510.02209", "published": "2025-10-03"}]
        # Published Oct 3, checking Nov 2 → 30 days old → passes
        result = apply_outcome_embargo(papers, at=date(2025, 11, 2), embargo_days=30)
        assert len(result) == 1
        assert result[0]["arxiv_id"] == "2510.02209"

    def test_paper_too_recent_filtered(self):
        """Paper published within embargo window is excluded."""
        from archimedes.services.embargo_filter import apply_outcome_embargo

        papers = [{"arxiv_id": "2510.02209", "published": "2025-10-03"}]
        result = apply_outcome_embargo(papers, at=date(2025, 10, 15), embargo_days=30)
        assert len(result) == 0

    def test_paper_exactly_at_boundary_included(self):
        """Paper exactly embargo_days old is included."""
        from archimedes.services.embargo_filter import apply_outcome_embargo

        papers = [{"arxiv_id": "2510.02209", "published": "2025-10-01"}]
        result = apply_outcome_embargo(papers, at=date(2025, 10, 31), embargo_days=30)
        assert len(result) == 1

    def test_missing_published_kept(self):
        """Paper with no published date is kept (conservative)."""
        from archimedes.services.embargo_filter import apply_outcome_embargo

        papers = [{"arxiv_id": "no.date", "published": ""}]
        result = apply_outcome_embargo(papers, embargo_days=30)
        assert len(result) == 1

    def test_default_embargo_30_days(self):
        """Default embargo is 30 days."""
        from archimedes.services.embargo_filter import DEFAULT_EMBARGO_DAYS

        assert DEFAULT_EMBARGO_DAYS == 30

    def test_empty_list(self):
        """Empty input returns empty output."""
        from archimedes.services.embargo_filter import apply_outcome_embargo

        assert apply_outcome_embargo([]) == []

    def test_custom_embargo_days(self):
        """Custom embargo_days is respected."""
        from archimedes.services.embargo_filter import apply_outcome_embargo

        papers = [{"arxiv_id": "2510.02209", "published": "2025-10-01"}]
        # 7-day embargo → paper 15 days old → passes
        result = apply_outcome_embargo(papers, at=date(2025, 10, 16), embargo_days=7)
        assert len(result) == 1
        # 30-day embargo → same paper → filtered
        result = apply_outcome_embargo(papers, at=date(2025, 10, 16), embargo_days=30)
        assert len(result) == 0

    def test_iso_full_datetime_parsed(self):
        """Full ISO datetime string is parsed correctly."""
        from archimedes.services.embargo_filter import apply_outcome_embargo

        papers = [{"arxiv_id": "2510.02209", "published": "2025-10-03T14:30:00Z"}]
        # Published Oct 3, checking Nov 2 → 30 days old → passes
        result = apply_outcome_embargo(papers, at=date(2025, 11, 2), embargo_days=30)
        assert len(result) == 1


# ── 2. Time-Aware Retrieval ──────────────────────────────────────────────


class TestTimeAwareRetrieval:
    """Time-Aware Retrieval — Xia § 4.2."""

    def test_decayed_score_less_than_raw(self):
        """Decayed score is less than raw similarity for non-zero age."""
        from archimedes.services.time_aware_retrieval import decayed_score

        raw = 0.9
        result = decayed_score(raw, age_days=365, lam=0.002)
        assert result < raw
        assert result > 0

    def test_zero_age_no_decay(self):
        """Zero age returns raw similarity."""
        from archimedes.services.time_aware_retrieval import decayed_score

        assert decayed_score(0.9, age_days=0) == 0.9

    def test_negative_age_no_decay(self):
        """Negative age (future) returns raw similarity."""
        from archimedes.services.time_aware_retrieval import decayed_score

        assert decayed_score(0.9, age_days=-10) == 0.9

    def test_older_paper_lower_score(self):
        """Older paper gets lower score than newer paper with same similarity."""
        from archimedes.services.time_aware_retrieval import decayed_score

        new_score = decayed_score(0.9, age_days=30, lam=0.002)
        old_score = decayed_score(0.9, age_days=365, lam=0.002)
        assert new_score > old_score

    def test_apply_time_aware_adds_score(self):
        """apply_time_aware_retrieval adds time_aware_score to each paper."""
        from archimedes.services.time_aware_retrieval import apply_time_aware_retrieval

        papers = [
            {"arxiv_id": "A", "published": "2025-01-01", "similarity": 0.9},
            {"arxiv_id": "B", "published": "2024-01-01", "similarity": 0.8},
        ]
        result = apply_time_aware_retrieval(papers, now=date(2025, 6, 1))
        assert all("time_aware_score" in p for p in result)

    def test_apply_time_aware_sorted_descending(self):
        """Papers are sorted by time_aware_score descending."""
        from archimedes.services.time_aware_retrieval import apply_time_aware_retrieval

        papers = [
            {"arxiv_id": "old", "published": "2020-01-01", "similarity": 0.95},
            {"arxiv_id": "new", "published": "2025-01-01", "similarity": 0.80},
        ]
        result = apply_time_aware_retrieval(papers, now=date(2025, 6, 1))
        # Newer paper with lower raw similarity may still rank higher due to decay
        scores = [p["time_aware_score"] for p in result]
        assert scores == sorted(scores, reverse=True)

    def test_regime_lambda_risk_off_higher(self):
        """risk_off regime has higher λ than risk_on."""
        from archimedes.services.time_aware_retrieval import regime_lambda

        assert regime_lambda(regime="risk_off") > regime_lambda(regime="risk_on")

    def test_regime_lambda_transition_moderate(self):
        """transition regime has moderate λ."""
        from archimedes.services.time_aware_retrieval import regime_lambda

        lam_transition = regime_lambda(regime="transition")
        lam_risk_on = regime_lambda(regime="risk_on")
        lam_risk_off = regime_lambda(regime="risk_off")
        assert lam_risk_on < lam_transition < lam_risk_off


# ── 3. Source Tracking ────────────────────────────────────────────────────


class TestSourceTracking:
    """Source Tracking — Xia § 4.3."""

    def test_build_consulted_hashes(self):
        """Build sorted arxiv_id:hash list from papers."""
        from archimedes.services.source_tracker import build_consulted_hashes

        papers = [
            {"arxiv_id": "2510.02209", "content_hash": "abc123"},
            {"arxiv_id": "2605.19337", "content_hash": "def456"},
        ]
        hashes = build_consulted_hashes(papers)
        assert hashes == ["2510.02209:abc123", "2605.19337:def456"]

    def test_build_consulted_hashes_sorted(self):
        """Output is sorted by arxiv_id."""
        from archimedes.services.source_tracker import build_consulted_hashes

        papers = [
            {"arxiv_id": "z-paper", "content_hash": "zzz"},
            {"arxiv_id": "a-paper", "content_hash": "aaa"},
        ]
        hashes = build_consulted_hashes(papers)
        assert hashes[0].startswith("a-paper")

    def test_build_consulted_hashes_fallback_sha256(self):
        """Falls back to pdf_sha256 if content_hash is missing."""
        from archimedes.services.source_tracker import build_consulted_hashes

        papers = [{"arxiv_id": "test", "pdf_sha256": "sha256val"}]
        hashes = build_consulted_hashes(papers)
        assert hashes == ["test:sha256val"]

    def test_verify_all_present(self):
        """Verification passes when all papers exist in corpus."""
        from archimedes.services.source_tracker import verify_source_papers

        consulted = ["2510.02209:abc123", "2605.19337:def456"]
        corpus = [
            {"arxiv_id": "2510.02209", "content_hash": "abc123"},
            {"arxiv_id": "2605.19337", "content_hash": "def456"},
        ]
        result = verify_source_papers(consulted, corpus)
        assert result["verified"] is True
        assert result["missing"] == []
        assert result["hash_mismatch"] == []

    def test_verify_missing_paper(self):
        """Verification fails when a paper is missing from corpus."""
        from archimedes.services.source_tracker import verify_source_papers

        consulted = ["2510.02209:abc123", "missing.0001:xyz"]
        corpus = [{"arxiv_id": "2510.02209", "content_hash": "abc123"}]
        result = verify_source_papers(consulted, corpus)
        assert result["verified"] is False
        assert "missing.0001" in result["missing"]

    def test_verify_hash_mismatch(self):
        """Verification fails when content hash differs."""
        from archimedes.services.source_tracker import verify_source_papers

        consulted = ["2510.02209:abc123"]
        corpus = [{"arxiv_id": "2510.02209", "content_hash": "different"}]
        result = verify_source_papers(consulted, corpus)
        assert result["verified"] is False
        assert "2510.02209" in result["hash_mismatch"]

    def test_empty_consulted_verifies(self):
        """Empty consulted list trivially verifies."""
        from archimedes.services.source_tracker import verify_source_papers

        result = verify_source_papers([], [{"arxiv_id": "any", "content_hash": "x"}])
        assert result["verified"] is True


# ── 4. V_check ────────────────────────────────────────────────────────────


class TestVCheck:
    """V_check — Reasoning I/O contract (Xia § 5)."""

    def test_valid_weights_pass(self):
        """Weights summing to 10000 BPS passes all checks."""
        from archimedes.chain.v_check import VCheck

        vc = VCheck(weights_bps={"A": 5000, "B": 5000})
        result = vc.run()
        assert result.passed is True
        assert result.passed is True  # __bool__
        assert len(result.failures) == 0

    def test_weights_not_summing_fail(self):
        """Weights NOT summing to 10000 BPS fails."""
        from archimedes.chain.v_check import VCheck

        vc = VCheck(weights_bps={"A": 5000, "B": 4500})
        result = vc.run()
        assert result.passed is False
        assert "weights_sum_bps" in result.checks
        assert result.checks["weights_sum_bps"] is False

    def test_max_concentration_exceeded(self):
        """Single weight exceeding max_concentration fails."""
        from archimedes.chain.v_check import VCheck

        vc = VCheck(weights_bps={"A": 7000, "B": 3000}, max_concentration_bps=6000)
        result = vc.run()
        assert result.passed is False
        assert result.checks["max_concentration"] is False

    def test_cost_benefit_below_minimum(self):
        """Cost benefit below minimum fails."""
        from archimedes.chain.v_check import VCheck

        vc = VCheck(
            weights_bps={"A": 5000, "B": 5000},
            cost_benefit_bps=2,
            min_cost_benefit_bps=10,
        )
        result = vc.run()
        assert result.passed is False
        assert result.checks["min_cost_benefit_bps"] is False

    def test_cost_benefit_not_provided_skips_check(self):
        """When cost_benefit_bps is None, the check is skipped."""
        from archimedes.chain.v_check import VCheck

        vc = VCheck(weights_bps={"A": 5000, "B": 5000})
        result = vc.run()
        assert "min_cost_benefit_bps" not in result.checks
        assert result.passed is True

    def test_from_weights_dict(self):
        """from_weights_dict converts floats to BPS correctly."""
        from archimedes.chain.v_check import VCheck

        vc = VCheck.from_weights_dict({"sSPY": 0.50, "sQQQ": 0.50})
        result = vc.run()
        assert result.passed is True

    def test_empty_weights_fail(self):
        """Empty weights fail (sum = 0 != 10000)."""
        from archimedes.chain.v_check import VCheck

        vc = VCheck(weights_bps={})
        result = vc.run()
        assert result.passed is False

    def test_multiple_failures(self):
        """Multiple failures are all captured."""
        from archimedes.chain.v_check import VCheck

        vc = VCheck(
            weights_bps={"A": 7000},
            max_concentration_bps=6000,
            cost_benefit_bps=1,
            min_cost_benefit_bps=10,
        )
        result = vc.run()
        assert result.passed is False
        assert len(result.failures) >= 2  # sum + concentration + cost_benefit

    def test_result_bool(self):
        """VCheckResult is truthy when passed, falsy when failed."""
        from archimedes.chain.v_check import VCheck

        passed = VCheck(weights_bps={"A": 5000, "B": 5000}).run()
        failed = VCheck(weights_bps={"A": 5000}).run()
        assert bool(passed) is True
        assert bool(failed) is False


# ── 5. Trace model integration ────────────────────────────────────────────


class TestTraceIntegration:
    """consulted_paper_hashes in ReasoningTrace hash fields."""

    def test_trace_has_consulted_paper_hashes_field(self):
        """ReasoningTrace has consulted_paper_hashes field."""
        from archimedes.models.trace import DecisionType, ReasoningTrace

        trace = ReasoningTrace(
            id="test",
            vault_address="0x0000",
            decision_type=DecisionType.SKIP,
            trigger="test",
        )
        assert hasattr(trace, "consulted_paper_hashes")
        assert isinstance(trace.consulted_paper_hashes, list)

    def test_consulted_paper_hashes_in_hash_fields(self):
        """consulted_paper_hashes is in _HASH_FIELDS."""
        from archimedes.models.trace import ReasoningTrace

        assert "consulted_paper_hashes" in ReasoningTrace._HASH_FIELDS

    def test_hash_includes_consulted_hashes(self):
        """Trace hash changes when consulted_paper_hashes changes."""
        from archimedes.models.trace import DecisionType, ReasoningTrace

        trace1 = ReasoningTrace(
            id="test1",
            vault_address="0x0000",
            decision_type=DecisionType.SKIP,
            trigger="test",
            consulted_paper_hashes=["paper1:hash1"],
        )
        trace2 = ReasoningTrace(
            id="test1",
            vault_address="0x0000",
            decision_type=DecisionType.SKIP,
            trigger="test",
            consulted_paper_hashes=["paper2:hash2"],
        )
        hash1 = trace1.compute_hash()
        hash2 = trace2.compute_hash()
        assert hash1 != hash2

    def test_canonical_json_includes_hashes(self):
        """canonical_json includes consulted_paper_hashes."""
        from archimedes.models.trace import DecisionType, ReasoningTrace

        trace = ReasoningTrace(
            id="test",
            vault_address="0x0000",
            decision_type=DecisionType.SKIP,
            trigger="test",
            consulted_paper_hashes=["paper1:hash1"],
        )
        cj = trace.canonical_json()
        assert "consulted_paper_hashes" in cj
        assert "paper1:hash1" in cj
