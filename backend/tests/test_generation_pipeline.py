"""num_trials multiple-testing correction for the N-candidate society (#770).

Hermetic: pure math over seeded return series — no LLM, no chain, no DB.

The agentic society generates N candidates, backtests + rigor-gates all, and keeps the
best. Selection-from-N is itself a multiple-testing search, so the Deflated Sharpe Ratio
must deflate for N + library_size, not library alone (which under-deflated and inflated
the survivor's DSR). These tests pin the chosen formula (approach A) and prove the
correction rejects a best-of-N survivor that the library-only count would have admitted.

Also covers approach B (#822): feeding the REAL candidate-pool pairwise correlation ρ̄
into the same DSR call, instead of the ρ̄=0 (independent-trials) assumption approach A
ships with. The N society candidates share a brief and overlapping universes, so they
are typically correlated — ρ̄=0 over-deflates and over-states the gate's strictness.
"""

from __future__ import annotations

import numpy as np
from archimedes.agents.generation_pipeline import (
    _CandidateResult,
    _patch_dsr_with_pool_correlation,
    _rigor_verdict_for,
    _society_num_trials,
)


class TestSocietyNumTrialsFormula:
    def test_is_n_plus_library(self):
        # Approach A: N candidates + library context.
        assert _society_num_trials(library_size=4, selection_pool_size=20) == 24
        assert _society_num_trials(library_size=10, selection_pool_size=10) == 20

    def test_single_candidate_adds_one(self):
        # N=1 (one generated candidate) is still a real trial on top of the library.
        assert _society_num_trials(library_size=4, selection_pool_size=1) == 5

    def test_floored_at_one(self):
        assert _society_num_trials(library_size=0, selection_pool_size=0) == 1


class TestNumTrialsCorrection:
    # A seeded series with a real positive edge whose DSR p-value brackets the 0.95 gate
    # between library-only (4) and society (library+N = 4+20 = 24). OOS is identical at
    # both counts, so the ONLY thing that flips the verdict is the num_trials deflation.
    _MU, _SD, _N, _SEED = 0.0009, 0.009, 300, 4

    def _series(self):
        return list(np.random.default_rng(self._SEED).normal(self._MU, self._SD, self._N))

    def test_num_trials_overfit_survivor_fails_society_path(self):
        """A best-of-N survivor that passes the library-only count FAILS once the society
        correction (N + library_size) deflates it — the exact selection bias #770 closes."""
        series = self._series()
        library_only = _rigor_verdict_for(series, num_trials=4, lookahead_passed=True)
        society = _rigor_verdict_for(series, num_trials=_society_num_trials(4, 20), lookahead_passed=True)

        assert library_only["passing"] is True, "fixture must pass under the (wrong) library-only count"
        assert society["passing"] is False, "society N+library correction must reject the inflated survivor"
        # The flip is the DSR deflation, not OOS: OOS Sharpe is identical at both counts.
        assert library_only["oos_sharpe"] == society["oos_sharpe"]
        assert society["dsr_p_value"] < library_only["dsr_p_value"]

    def test_dsr_pvalue_monotonic_stricter_in_trials(self):
        """More trials can only deflate harder — the property the additive count relies on."""
        series = self._series()
        p_small = _rigor_verdict_for(series, num_trials=4, lookahead_passed=True)["dsr_p_value"]
        p_large = _rigor_verdict_for(series, num_trials=24, lookahead_passed=True)["dsr_p_value"]
        assert p_large <= p_small


def _candidate(candidate_id: str, return_series: list[float], num_trials: int) -> _CandidateResult:
    """Build a buy-and-hold society candidate with a real DSR verdict already computed
    at ρ̄=0 (approach A) — the state each candidate is in right after ``_run_live_candidate``,
    before the post-loop pool-correlation patch runs."""
    verdict = _rigor_verdict_for(return_series, num_trials=num_trials, lookahead_passed=True)
    verdict["pbo"] = 0.0  # mirrors _patch_pbo's N<2 default; patch under test runs after PBO
    return _CandidateResult(
        candidate_id=candidate_id,
        strategy_name=f"Strategy {candidate_id}",
        thesis="t",
        asset_universe=["sSPY"],
        source_papers=[],
        weights={"sSPY": 1.0},
        reasoning="r",
        rigor_verdict=verdict,
        passes_rigor=verdict.get("passing", False),
        return_series=return_series,
        dsr_num_trials=num_trials,
    )


class TestPoolCorrelationDSR:
    """Approach B (#822): feed the real candidate-pool ρ̄ into the society DSR, replacing
    the ρ̄=0 (independent-trials) assumption approach A ships with for the N generated
    candidates' own DSR call. ``_patch_dsr_with_pool_correlation`` is the post-loop patch
    ``run_generation`` calls after ``_patch_pbo``, once every candidate's return series
    is known — mirrors the existing PBO patch's two-pass shape."""

    _NUM_TRIALS = 24  # library_size=4 + selection_pool_size=20, same as TestNumTrialsCorrection

    def _correlated_pool(self, n: int, rho_target: float, seed: int = 11) -> list[list[float]]:
        """N series sharing a common factor + idiosyncratic noise, engineered so the
        realized average pairwise correlation is close to ``rho_target``. Same brief,
        overlapping universe — the scenario #822 describes."""
        rng = np.random.default_rng(seed)
        t = 300
        factor = rng.normal(0.0009, 0.009, t)
        idio_scale = 0.009 * np.sqrt(max(1e-9, (1.0 - rho_target) / max(rho_target, 1e-9)))
        series = []
        for _ in range(n):
            idio = rng.normal(0.0, idio_scale, t)
            series.append((np.sqrt(rho_target) * factor + np.sqrt(1.0 - rho_target) * idio).tolist())
        return series

    def _independent_pool(self, n: int, seed: int = 23) -> list[list[float]]:
        rng = np.random.default_rng(seed)
        return [rng.normal(0.0009, 0.009, 300).tolist() for _ in range(n)]

    def test_correlated_pool_relaxes_dsr_pvalue_never_tightens(self):
        """Monotonicity pin (acceptance criterion #1): a correlated pool's DSR p-value
        is >= the ρ̄=0 (approach A) value for every candidate — the correction only
        relaxes over-deflation, never tightens the gate beyond approach A."""
        series_list = self._correlated_pool(n=5, rho_target=0.75)
        candidates = [_candidate(f"cand_{i}", s, self._NUM_TRIALS) for i, s in enumerate(series_list)]
        baseline_p = [c.rigor_verdict["dsr_p_value"] for c in candidates]  # ρ̄=0, computed in _candidate()

        _patch_dsr_with_pool_correlation(candidates)

        patched_p = [c.rigor_verdict["dsr_p_value"] for c in candidates]
        for base, patched in zip(baseline_p, patched_p, strict=True):
            assert patched >= base, "approach B must relax (never tighten) the ρ̄=0 p-value"
        # A materially correlated pool should show a REAL relaxation somewhere, not a no-op.
        assert any(p > b for b, p in zip(baseline_p, patched_p, strict=True))

    def test_correlated_pool_uses_n_eff_below_nominal_count(self):
        """Acceptance criterion #1: the society DSR uses N_eff < N + library_size for a
        correlated pool. Cross-check via compute_dsr directly at N_eff vs. the nominal
        count, on the same series the patch would have used."""
        from archimedes.services.rigor_evaluator import compute_average_pairwise_correlation, compute_dsr

        series_list = self._correlated_pool(n=5, rho_target=0.75)
        rho_bar = compute_average_pairwise_correlation({str(i): s for i, s in enumerate(series_list)})
        assert 0.0 < rho_bar < 1.0, "the engineered pool must realize a real, non-trivial correlation"

        _, p_nominal = compute_dsr(series_list[0], num_trials=self._NUM_TRIALS, average_correlation=0.0)
        _, p_neff = compute_dsr(series_list[0], num_trials=self._NUM_TRIALS, average_correlation=rho_bar)
        assert p_neff >= p_nominal

    def test_fewer_than_two_series_falls_back_to_rho_zero(self):
        """Acceptance criterion #2: with < 2 candidate return series, stay on approach A
        (ρ̄=0) unchanged — no correlation is estimable from a single series."""
        series_list = self._correlated_pool(n=1, rho_target=0.75)
        candidates = [_candidate("cand_0", series_list[0], self._NUM_TRIALS)]
        before = dict(candidates[0].rigor_verdict)

        _patch_dsr_with_pool_correlation(candidates)

        assert candidates[0].rigor_verdict["dsr_p_value"] == before["dsr_p_value"]
        assert candidates[0].rigor_verdict["dsr"] == before["dsr"]

    def test_no_return_series_at_all_falls_back_to_rho_zero(self):
        """Zero eligible series (e.g. every candidate too-short / fixture path) is also
        the < 2 fallback — must not raise."""
        candidates = [_candidate("cand_0", [], self._NUM_TRIALS)]
        before = dict(candidates[0].rigor_verdict)

        _patch_dsr_with_pool_correlation(candidates)

        assert candidates[0].rigor_verdict == before

    def test_independent_pool_is_a_near_noop(self):
        """A genuinely independent pool (ρ̄ ≈ 0) should leave the DSR verdict close to the
        approach-A baseline — the correction only bites when candidates are actually
        correlated, consistent with N_eff → N as ρ̄ → 0."""
        series_list = self._independent_pool(n=5)
        candidates = [_candidate(f"cand_{i}", s, self._NUM_TRIALS) for i, s in enumerate(series_list)]
        baseline_p = [c.rigor_verdict["dsr_p_value"] for c in candidates]

        _patch_dsr_with_pool_correlation(candidates)

        patched_p = [c.rigor_verdict["dsr_p_value"] for c in candidates]
        for base, patched in zip(baseline_p, patched_p, strict=True):
            assert patched >= base  # monotonicity still holds even near ρ̄=0
            assert abs(patched - base) < 0.02, "near-independent pool should barely move the p-value"

    def test_fusion_candidates_excluded_from_pool_and_untouched(self):
        """has_real_rigor candidates (fusion/debate) carry their own CSCV-derived DSR and
        must never be folded into the buy-and-hold pool correlation, or patched — mirrors
        _patch_pbo's existing scope boundary."""
        series_list = self._correlated_pool(n=3, rho_target=0.8)
        buy_and_hold = [_candidate(f"cand_{i}", s, self._NUM_TRIALS) for i, s in enumerate(series_list)]
        fusion_verdict = {
            "dsr": 1.23,
            "dsr_p_value": 0.5,
            "pbo": 0.1,
            "oos_sharpe": 0.4,
            "in_sample_sharpe": 0.5,
            "lookahead_audit_passed": True,
            "passing": True,
        }
        fusion_candidate = _CandidateResult(
            candidate_id="cand_fusion",
            strategy_name="Fusion X",
            thesis="t",
            asset_universe=["sSPY"],
            source_papers=[],
            weights={},
            reasoning="r",
            rigor_verdict=dict(fusion_verdict),
            passes_rigor=True,
            has_real_rigor=True,
            return_series=series_list[0],  # even if it "looks like" pool data, must be ignored
            generation_method="fusion",
        )
        candidates = [*buy_and_hold, fusion_candidate]

        _patch_dsr_with_pool_correlation(candidates)

        assert fusion_candidate.rigor_verdict == fusion_verdict, "fusion candidate's real DSR must be untouched"
