"""One DSR bar, one definition (#1794).

Before this, the bar was written down in three places with two values. The badge
path — every surface that decides "Archimedes Verified" — read
``rigor_profiles.get_profile(STRICTEST_LEVEL).dsr_p_min`` (0.90 since PR #901),
while ``generation_pipeline`` carried two hardcoded ``0.95`` comparisons
(``_rigor_verdict_for`` and ``_patch_dsr_with_pool_correlation``) and every public
rigor page said 0.95 too.

Traced rather than assumed: ``_rigor_verdict_for`` has no production caller today,
and ``_patch_dsr_with_pool_correlation`` — which ``run_generation`` does call —
skips every ``has_real_rigor=True`` candidate, which is all of them on the Phase-3
debate-only pipeline. So the shipped inconsistency was gate-vs-docs, not
gate-vs-generator; the literals were the split waiting to happen the moment a
buy-and-hold path returned. A strategy in [0.90, 0.95) still earned the badge
while the page describing the badge said it should not.

The owner's call (2026-09-03) was 0.95 everywhere and the 0.90 path retired. That
is only durable if the number cannot be written down twice, so this module holds
two guards:

* ``TestOneLiteral`` — a source scan over the modules that compute or serve the
  gate. Outside ``DSR_P_BADGE_MIN``'s own definition line, no ``0.9x`` literal may
  sit within two lines of DSR code — in a comparison, a default, OR a comment. A
  stale comment quoting a retired bar is how #1794's docs drift started, so prose
  is in scope, not exempt from it.

* ``TestGateBehaviourAtTheBar`` — the bar is actually 0.95 at every call site that
  used to hold its own literal: a candidate at DSR p = 0.92 fails and one at 0.96
  passes, on the badge gate, on ``_rigor_verdict_for``, and on the pool-correlation
  re-patch that re-derives ``passing`` after re-deflating.

Hermetic: reads committed source off disk and imports pure Python. No DB, no net.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from archimedes.services.rigor_evaluator import RigorGateResult
from archimedes.services.rigor_profiles import (
    DSR_P_BADGE_MIN,
    STRICTEST_LEVEL,
    get_profile,
)

REPO_ROOT = Path(__file__).resolve().parents[2]

# Every module that computes, patches, or documents the admission decision. If a
# new one starts grading DSR, add it here — the guard is only as wide as this list.
GUARDED_MODULES = (
    "backend/archimedes/services/rigor_profiles.py",
    "backend/archimedes/services/rigor_evaluator.py",
    "backend/archimedes/services/_rigor_helpers.py",
    "backend/archimedes/services/live_rigor_gate.py",
    "backend/archimedes/services/fusion_evaluator.py",
    "backend/archimedes/agents/generation_pipeline.py",
    "backend/archimedes/api/selection_bias_routes.py",
    "backend/archimedes/api/strategies_routes.py",
    "backend/archimedes/models/backtest.py",
)

# 0.9, 0.90, 0.95, 0.9xx — the shape a DSR bar takes. Not preceded by a digit or a
# dot, so "28.9%" and "1.0.95" are not matches.
_BAR_LITERAL = re.compile(r"(?<![\d.])0\.9\d*")
# "DSR", "deflated", "deflation", "deflates" — the tokens that make a nearby
# literal a threshold claim rather than an unrelated number.
_DSR_TOKEN = re.compile(r"dsr|deflat", re.IGNORECASE)
# The ONE line allowed to write the number down. Matched with ``startswith``, not
# ``in``: an ``in`` test would let `x = 0.90  # DSR_P_BADGE_MIN = the real one`
# name its way out of the scan.
_DEFINITION = "DSR_P_BADGE_MIN = "
# Narrow, documented exemption: the pool-correlation prose quotes ρ ranges like
# "ρ ∈ [0.3, 0.9]" near DSR code. It applies ONLY to a line that mentions ρ or
# correlation and does NOT itself name DSR — so `dsr_p >= 0.95  # correlation`
# is still caught.
_RHO_PROSE = re.compile(r"ρ|correlation", re.IGNORECASE)
_WINDOW = 2


def _offenders(path: str) -> list[str]:
    lines = (REPO_ROOT / path).read_text(encoding="utf-8").splitlines()
    out: list[str] = []
    for i, line in enumerate(lines):
        if not _BAR_LITERAL.search(line):
            continue
        if line.startswith(_DEFINITION):
            continue  # the one definition
        window = lines[max(0, i - _WINDOW) : i + _WINDOW + 1]
        if not any(_DSR_TOKEN.search(w) for w in window):
            continue  # a number that has nothing to do with the DSR bar
        if _RHO_PROSE.search(line) and not _DSR_TOKEN.search(line):
            continue  # the ρ̄ pool-correlation prose
        out.append(f"{path}:{i + 1}: {line.strip()}")
    return out


class TestOneLiteral:
    """The bar is written down exactly once, in ``rigor_profiles``."""

    def test_every_guarded_module_exists(self):
        """A renamed/deleted module must not silently shrink the guard's scope."""
        missing = [m for m in GUARDED_MODULES if not (REPO_ROOT / m).is_file()]
        assert not missing, (
            "GUARDED_MODULES names files that no longer exist, so the #1794 "
            f"single-bar scan silently stopped covering them: {missing}"
        )

    def test_no_dsr_bar_literal_outside_its_definition(self):
        found = [o for m in GUARDED_MODULES for o in _offenders(m)]
        assert not found, (
            "A DSR-bar literal (0.9 / 0.90 / 0.95) appears next to DSR code outside "
            "rigor_profiles.DSR_P_BADGE_MIN. That is the #1794 defect: two bars in "
            "the tree, and which one graded you depended on which code path you "
            "reached. Import DSR_P_BADGE_MIN (or read profile.dsr_p_min); in a "
            "comment, name the constant instead of quoting its value:\n  " + "\n  ".join(found)
        )

    def test_the_definition_is_singular_and_is_the_badge_bar(self):
        """One assignment in the tree, and the ladder's badge rung IS it."""
        assignments = [
            f"{m}:{i + 1}"
            for m in GUARDED_MODULES
            for i, line in enumerate((REPO_ROOT / m).read_text(encoding="utf-8").splitlines())
            if line.startswith(_DEFINITION)
        ]
        assert len(assignments) == 1 and assignments[0].startswith("backend/archimedes/services/rigor_profiles.py:"), (
            f"DSR_P_BADGE_MIN must be assigned exactly once, in rigor_profiles. Found: {assignments}"
        )
        # The number itself, pinned: 0.95 is the owner's call on #1794. Changing it
        # is a product decision, so it fails here rather than sliding through.
        assert DSR_P_BADGE_MIN == 0.95
        # ...and the ladder's badge rung IS the constant, not a copy of its value.
        assert get_profile(STRICTEST_LEVEL).dsr_p_min is DSR_P_BADGE_MIN


# ── Behaviour: the bar really is 0.95 at every call site that held a literal ──

# Straddles the bar, and also straddles the retired 0.90 one from below/above so a
# regression to it is visible rather than silently equivalent.
BELOW = 0.92
ABOVE = 0.96

# A return series whose OOS Sharpe is positive with no in-/out-of-sample cliff, so
# every non-DSR admission leg is clean and only the bar under test decides.
_CLEAN_SERIES = [0.01, -0.005, 0.008, 0.003] * 20


def _result(dsr_p: float) -> RigorGateResult:
    """A candidate that clears every leg except (possibly) the DSR bar."""
    return RigorGateResult(
        "cand",
        dsr_p_value=dsr_p,
        pbo_score=0.10,
        oos_sharpe=1.00,
        in_sample_sharpe=1.00,
        look_ahead_passed=True,
    )


class TestGateBehaviourAtTheBar:
    def test_badge_gate_fails_below_and_passes_above(self):
        assert _result(BELOW).passes_all is False, (
            f"DSR p={BELOW} cleared the badge gate — the bar is not {DSR_P_BADGE_MIN}."
        )
        assert _result(ABOVE).passes_all is True

    def test_badge_gate_detail_quotes_the_one_bar(self):
        detail = _result(BELOW).gate_details["dsr"]
        assert detail == f"FAIL (p={BELOW:.4f}, need ≥ {DSR_P_BADGE_MIN:.2f})", detail

    @pytest.mark.parametrize(("dsr_p", "expected"), [(BELOW, False), (ABOVE, True)])
    def test_generation_verdict_uses_the_same_bar(self, monkeypatch, dsr_p, expected):
        """``_rigor_verdict_for`` — the buy-and-hold verdict helper (one former literal)."""
        from archimedes.agents import generation_pipeline
        from archimedes.services import rigor_evaluator

        monkeypatch.setattr(rigor_evaluator, "compute_dsr", lambda *a, **k: (1.0, dsr_p))
        # 80 bars: a positive OOS Sharpe with no IS/OOS cliff, so the DSR leg is
        # the only thing left that can decide `passing`.
        verdict = generation_pipeline._rigor_verdict_for(_CLEAN_SERIES, num_trials=4, lookahead_passed=True)
        assert verdict["dsr_p_value"] == pytest.approx(dsr_p)
        assert verdict["passing"] is expected

    @pytest.mark.parametrize(("dsr_p", "expected"), [(BELOW, False), (ABOVE, True)])
    def test_pool_correlation_repatch_uses_the_same_bar(self, monkeypatch, dsr_p, expected):
        """``_patch_dsr_with_pool_correlation`` re-derives ``passing`` from scratch."""
        from archimedes.agents import generation_pipeline
        from archimedes.services import rigor_evaluator

        monkeypatch.setattr(rigor_evaluator, "compute_dsr", lambda *a, **k: (1.0, dsr_p))
        monkeypatch.setattr(rigor_evaluator, "compute_average_pairwise_correlation", lambda *a, **k: 0.5)

        def _cand(cid: str) -> generation_pipeline._CandidateResult:
            c = object.__new__(generation_pipeline._CandidateResult)
            c.candidate_id = cid
            c.has_real_rigor = False
            c.return_series = list(_CLEAN_SERIES)
            c.dsr_num_trials = 2
            c.rigor_verdict = {
                "dsr": 1.0,
                "dsr_p_value": 0.5,
                "pbo": 0.1,
                "oos_sharpe": 1.0,
                "in_sample_sharpe": 1.0,
                "lookahead_audit_passed": True,
                "passing": False,
            }
            return c

        cands = [_cand("a"), _cand("b")]
        generation_pipeline._patch_dsr_with_pool_correlation(cands)
        assert cands[0].rigor_verdict["dsr_p_value"] == pytest.approx(dsr_p)
        assert cands[0].rigor_verdict["passing"] is expected
