"""The seven conflicts #1598 reconciled in ``docs/quant/`` must not come back.

PR #1597's OpenWiki pass found seven internal contradictions in the quant slice.
Six were documents disagreeing with each other; the seventh was a rule the slice
states three times and breaks twice. Prose has no compiler, so every one of them
regenerates the moment a partially-applied edit lands — which is exactly how
conflict 4 arose (a Faber sentence stranded under a different strategy) and how
conflict 2 arose (a promotion flow left describing a convention reversed on
2026-07-09).

This module is the compiler for the parts that are mechanically checkable.

Two kinds of check live here:

* **Negative guards** — a forbidden string class must be absent from
  ``docs/quant/``. These are deliberately *literal and exemption-free*. An
  exemption ("unless the line also says 'corrected'") is a hole an edit can walk
  through, and it means a doc can still ship the forbidden number verbatim where
  a grep — or an LLM reading the tree — will find it. The consequence is a
  standing convention: **a correction annotation in these docs describes the old
  claim, it does not reproduce it.** Every annotation added by #1598 is written
  that way, and these guards are what keeps it that way.

* **A positive binding** — the level-1 threshold numbers printed in
  ``admission-criteria.md`` are asserted equal to the live values in
  ``rigor_profiles``. That was conflict 1: the doc presented the numbers as "the
  literal gate" while the gate reads a profile row. Transcription drifts;
  an assertion does not.

What this file deliberately does NOT do: judge whether a *prose* description of a
threshold is current. `test_no_library_sized_num_trials` matches the code-shaped
form only. A doc can still describe the reversed convention in words, which is
correct — the findings notes need to, to explain their own vintage.

Scope is ``docs/quant/`` only, matching the issue. ``openwiki/`` is excluded on
purpose: its pages are generated artifacts, and
``openwiki/rigor/documented-conflicts.md`` is the evidence record of what the run
found — it *must* keep quoting the strings this module forbids.

Hermetic: reads committed files off disk and imports one pure-dataclass module.
No DB, no network, no .env, no model load.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from archimedes.services import rigor_profiles


def _repo_root() -> Path:
    import archimedes  # backend/archimedes/__init__.py → parents: archimedes, backend, <repo>

    return Path(archimedes.__file__).resolve().parents[2]


def _quant_dir() -> Path:
    return _repo_root() / "docs" / "quant"


def _quant_docs() -> list[Path]:
    docs = sorted(_quant_dir().glob("*.md"))
    assert docs, f"no markdown found under {_quant_dir()} — the guard would pass vacuously"
    return docs


def _hits(pattern: re.Pattern[str]) -> list[str]:
    """Every ``path:lineno: line`` in the slice matching ``pattern``."""
    found: list[str] = []
    for doc in _quant_docs():
        for n, line in enumerate(doc.read_text(encoding="utf-8").splitlines(), start=1):
            if pattern.search(line):
                found.append(f"{doc.relative_to(_repo_root())}:{n}: {line.strip()}")
    return found


# ── Conflict 7 — the CLAUDE.md hard rule ────────────────────────────────────
# "Don't quote a curated-library strategy pass count — anywhere. [...] Say
# 'unestablished', not a number." Three strategies reported passing were later
# found to be grading equity-like series through a data-feed fallback, so the
# corrected count is not established. Two findings notes stated one anyway.
_PASS_COUNT = re.compile(
    r"""(?ix)
    \b(?:one|two|three|four|five|six|seven|eight|nine|ten|\d+)
    [\s\-]+ (?:gate[\s\-]*)? pass(?:es|ers?|ing)\b
    |
    \b(?:one|two|three|four|five|six|seven|eight|nine|ten|\d+)
    \s+ (?:\w+\s+){0,3}? pass(?:es)? \s+ (?:all\s+)? (?:the\s+)? (?:four\s+)? gates?\b
    """
)

# ── Conflict 2 — the reversed `num_trials` convention, in code form ─────────
_LIBRARY_SIZED_NUM_TRIALS = re.compile(r"num_trials\s*=\s*len\s*\(", re.IGNORECASE)

# ── Conflict 3 — the pre-#901 bar stated as a gate condition ────────────────
_STALE_095_BAR = re.compile(r"p\s*(?:≥|>=)\s*0\.95")

# ── Conflict 5 — 0.612 is a DSR p-value; it is not any kind of Sharpe ───────
_SHARPE_0612 = re.compile(r"Sharpe[^\n]{0,40}0\.612", re.IGNORECASE)

# ── Conflict 4 — a heading is not a verdict ────────────────────────────────
_VERDICT_IN_HEADING = re.compile(r"✅|❌|passes the gate|fails the gate", re.IGNORECASE)


class TestForbiddenClaims:
    def test_no_curated_library_pass_count(self):
        """CLAUDE.md's hard rule, enforced over the whole slice.

        Caught before this ran: the retraction notes #1598 first wrote quoted the
        old phrasings verbatim ("the two gate-passers", "the two-passers story").
        The guard flagged them, and it was right to — a retraction that reprints
        the count still puts the count in the document. They were rewritten to
        describe the old claim instead.
        """
        hits = _hits(_PASS_COUNT)
        assert not hits, (
            "A curated-library pass count is quoted in docs/quant/. The corrected "
            "count is UNESTABLISHED (CLAUDE.md) — say so, do not give a number:\n  " + "\n  ".join(hits)
        )

    def test_no_library_sized_num_trials(self):
        """`num_trials = len(...)` was reversed on 2026-07-09 and is now ADR-forbidden.

        The ADR (`num-trials-self-containment.md`, Accepted) is explicit: a
        library-sized trial count makes a strategy's p-value a function of
        unrelated strategies, so a passport stops being reproducible from its own
        artifacts. The curated path hard-codes 1; the generated path uses the
        debate's own pool size.
        """
        hits = _hits(_LIBRARY_SIZED_NUM_TRIALS)
        assert not hits, (
            "docs/quant/ shows a library-sized num_trials. Curated = 1, generated = "
            "the strategy's own debate pool size — see docs/adr/"
            "num-trials-self-containment.md:\n  " + "\n  ".join(hits)
        )

    def test_no_stale_095_gate_bar(self):
        """The DSR bar has been 0.90 since PR #901; `p ≥ 0.95` states a gate condition.

        Historical *narration* of the old bar is fine and necessary — the findings
        notes need it to explain their own vintage — which is why this matches the
        threshold-expression form and not the digits.
        """
        hits = _hits(_STALE_095_BAR)
        assert not hits, (
            "A `p ≥ 0.95` gate condition survives in docs/quant/. The bar is 0.90 "
            "(PR #901); narrate the old bar, do not state it as a condition:\n  " + "\n  ".join(hits)
        )

    def test_0612_is_never_called_a_sharpe(self):
        """0.612 is Faber's DSR p-value. Its OOS Sharpe on the same pull is 0.930.

        Two passages explained a failure as an OOS Sharpe of 0.612 "under the 0.90
        gate" — comparing a Sharpe ratio to a probability. The OOS Sharpe's own
        thresholds are an always-on floor of > 0 and a cliff of OOS/IS >= 0.5, and
        0.930 clears both; the failure is on criterion 1.
        """
        hits = _hits(_SHARPE_0612)
        assert not hits, (
            "docs/quant/ attributes 0.612 to a Sharpe ratio. It is a DSR p-value "
            "(docs/analysis/faber-dsr-finding.md); 0.90 is a probability bar, not "
            "an OOS-Sharpe bar:\n  " + "\n  ".join(hits)
        )

    def test_strategy_library_headings_carry_no_verdict(self):
        """`strategy-library.md` states twice that pass/fail is not recorded there.

        Three headings broke that rule and all three disagreed with the status line
        directly beneath them — including a ✅ *passes the gate* sitting on top of
        "CANDIDATE — fails admission". A reader skimming headings got the opposite
        answer from a reader reading status lines.
        """
        doc = _quant_dir() / "strategy-library.md"
        offenders = [
            f"{n}: {line.strip()}"
            for n, line in enumerate(doc.read_text(encoding="utf-8").splitlines(), start=1)
            if line.startswith("#") and _VERDICT_IN_HEADING.search(line)
        ]
        assert not offenders, (
            "A heading in strategy-library.md carries a pass/fail verdict. The live "
            "rigor gate is the only authority on pass/fail:\n  " + "\n  ".join(offenders)
        )


class TestThresholdsMatchLiveCode:
    """Conflict 1: the doc printed the ladder's level-1 row as "the literal gate".

    `passes_all` reads a `rigor_profiles` row and never compares against a
    hard-coded number. The numbers in the doc are still right — they are level 1,
    and level 1 *is* the Tier-1 badge bar — but nothing bound them to the source.
    These assertions are that binding: change `_PROFILES` and this fails until the
    doc is updated with it.
    """

    @pytest.fixture
    def admission_text(self) -> str:
        return (_quant_dir() / "admission-criteria.md").read_text(encoding="utf-8")

    def test_level_1_is_the_strictest_and_the_badge_bar(self):
        """The premise of the whole page: level 1 is what the badge is graded at."""
        assert rigor_profiles.STRICTEST_LEVEL == 1
        assert rigor_profiles.DEFAULT_LEVEL == rigor_profiles.STRICTEST_LEVEL
        assert rigor_profiles.get_profile(1).label == "Conservative"

    def test_adjustable_ladder_endpoints_are_transcribed_correctly(self, admission_text):
        strictest = rigor_profiles.get_profile(rigor_profiles.STRICTEST_LEVEL)
        loosest = rigor_profiles.get_profile(rigor_profiles.LOOSEST_LEVEL)
        for name, lo, hi in (
            ("dsr_p_min", strictest.dsr_p_min, loosest.dsr_p_min),
            ("pbo_max", strictest.pbo_max, loosest.pbo_max),
            ("oos_is_ratio_min", strictest.oos_is_ratio_min, loosest.oos_is_ratio_min),
        ):
            expected = f"`{lo:.2f} → {hi:.2f}`"
            assert expected in admission_text, (
                f"admission-criteria.md's ladder row for {name} does not show "
                f"{expected}, which is what rigor_profiles._PROFILES holds today."
            )

    def test_always_on_floors_are_transcribed_correctly(self, admission_text):
        for expected in (
            f"`OOS_ABS_FLOOR = {rigor_profiles.OOS_ABS_FLOOR:.1f}`",
            f"`DSR_P_FLOOR = {rigor_profiles.DSR_P_FLOOR:.2f}`",
            f"`CPCV_MIN_POSITIVE_FRACTION = {rigor_profiles.CPCV_MIN_POSITIVE_FRACTION}`",
        ):
            assert expected in admission_text, (
                f"admission-criteria.md does not carry {expected}. The always-on "
                "floors are what make 'you can never fully bypass the rigor gate' "
                "true; a stale copy of them is a false claim."
            )

    def test_the_level_1_dsr_bar_in_the_control_table_is_live(self, admission_text):
        """The single most-quoted number on the page."""
        bar = rigor_profiles.get_profile(rigor_profiles.STRICTEST_LEVEL).dsr_p_min
        assert f"`dsr_p_value ≥ {bar:.2f}`" in admission_text
