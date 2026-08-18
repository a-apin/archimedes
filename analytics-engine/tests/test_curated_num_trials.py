"""Regression guard for V1 (num_trials-provenance audit, 2026-08-03).

``regen_fixtures.py`` and its sibling ``regen_buy_hold_fixture.py`` generate
fixture entries for CURATED strategies — hand-implemented published papers,
never the output of a search of ours. Per Dan's decision (2026-07-27): "For a
hand-curated implementation of one published paper there is no search of
ours, so num_trials = 1." A curated strategy's DSR multiple-testing
correction must therefore be self-contained (num_trials=1), never deflated by
how many OTHER strategies happen to sit in the library.

BEFORE the fix, both scripts computed ``num_trials`` as a function of the
current library size (``len(fixtures)`` [+ pending new entries]) and stamped
that library-size count into every new entry's ``num_trials_in_selection`` —
exactly the cross-strategy coupling the decision forbids outside
Leaderboard/Marketplace, and the confirmed shape of the anomalous
``num_trials`` values this audit traced in production.

These tests call the extracted, directly-testable ``curated_num_trials()``
helpers with library sizes that are DELIBERATELY LARGE (25, 34) and NONZERO
pending-entry counts — i.e. exactly the inputs that would have produced a
large, wrong ``num_trials`` under the old library-size formula. Asserting the
result is always ``1`` regardless of those inputs is what makes this a
regression guard, not a same-literal-both-sides tautology: the old formula
and the new one are given the SAME inputs and diverge sharply (25/34/59 vs 1).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from regen_buy_hold_fixture import curated_num_trials as buy_hold_num_trials  # noqa: E402
from regen_fixtures import curated_num_trials as fixtures_num_trials  # noqa: E402


def _old_buggy_fixtures_formula(existing_fixture_count: int, new_entry_count: int) -> int:
    """The PRE-fix formula regen_fixtures.py used to compute num_trials —
    kept here only so the tests below can demonstrate they fail against it."""
    return existing_fixture_count + new_entry_count


def _old_buggy_buy_hold_formula(existing_fixture_count: int) -> int:
    """The PRE-fix formula regen_buy_hold_fixture.py used to compute
    num_trials — kept here only so the tests below can demonstrate they fail
    against it."""
    return existing_fixture_count


def test_regen_fixtures_curated_num_trials_is_always_one():
    """A curated fixture entry's num_trials is 1 no matter how large the
    library is or how many new entries are being added in this run."""
    # Library already has 25 entries (matches the production N=25 bucket this
    # audit traced), and this run is adding 9 more (matches NEW_SINGLE_SPECS'
    # actual length) — the exact shape of a real regen_fixtures.py --write run.
    assert fixtures_num_trials(existing_fixture_count=25, new_entry_count=9) == 1
    # A from-scratch library (0 existing) adding a handful of new entries.
    assert fixtures_num_trials(existing_fixture_count=0, new_entry_count=6) == 1
    # A much larger, more mature library — the result must still be 1.
    assert fixtures_num_trials(existing_fixture_count=34, new_entry_count=1) == 1


def test_regen_fixtures_diverges_from_the_old_buggy_formula():
    """Adversarial: the SAME inputs that produced the wrong answer under the
    old code must produce a DIFFERENT (correct) answer under the fix — proof
    this isn't a test that would pass against unfixed code too."""
    existing, new = 25, 9
    old_wrong = _old_buggy_fixtures_formula(existing, new)
    fixed = fixtures_num_trials(existing, new)
    assert old_wrong == 34  # the old formula really did produce library-size N
    assert fixed == 1
    assert fixed != old_wrong


def test_buy_hold_curated_num_trials_is_always_one():
    assert buy_hold_num_trials(existing_fixture_count=25) == 1
    assert buy_hold_num_trials(existing_fixture_count=0) == 1
    assert buy_hold_num_trials(existing_fixture_count=34) == 1


def test_buy_hold_diverges_from_the_old_buggy_formula():
    existing = 25
    old_wrong = _old_buggy_buy_hold_formula(existing)
    fixed = buy_hold_num_trials(existing)
    assert old_wrong == 25  # the old formula really did produce the library size
    assert fixed == 1
    assert fixed != old_wrong


def test_every_entry_this_run_would_produce_gets_num_trials_one():
    """Simulates the loop shape in regen_fixtures.py: several new entries
    from ONE run must all receive num_trials=1, not the (larger) post-add
    library size that a per-entry misuse of `len(fixtures)` after each write
    could otherwise produce."""
    existing = 25
    pending_stems = ["a", "b", "c"]  # 3 new entries added in this run
    for _ in pending_stems:
        n = fixtures_num_trials(existing, len(pending_stems))
        assert n == 1


def test_main_wires_through_curated_num_trials_not_an_inline_formula():
    """Wiring guard: the unit tests above only prove ``curated_num_trials()``
    itself is correct — they would NOT catch a future refactor that reverts
    ``main()`` to an inline library-size formula while leaving the (now
    unused) helper untouched. Assert ``main()``'s source actually calls the
    helper, so that regression is caught too."""
    import inspect

    import regen_fixtures as rf

    src = inspect.getsource(rf.main)
    assert "curated_num_trials(" in src


def test_buy_hold_main_wires_through_curated_num_trials_not_an_inline_formula():
    import inspect

    import regen_buy_hold_fixture as rbh

    src = inspect.getsource(rbh.main)
    assert "curated_num_trials(" in src
