"""num_trials multiple-testing correction for the N-candidate society (#770).

Hermetic: pure math over seeded return series — no LLM, no chain, no DB.

The agentic society generates N candidates, backtests + rigor-gates all, and keeps the
best. Selection-from-N is itself a multiple-testing search, so the Deflated Sharpe Ratio
must deflate for N + library_size, not library alone (which under-deflated and inflated
the survivor's DSR). These tests pin the chosen formula (approach A) and prove the
correction rejects a best-of-N survivor that the library-only count would have admitted.
"""

from __future__ import annotations

import numpy as np
from archimedes.agents.generation_pipeline import _rigor_verdict_for, _society_num_trials


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
