"""The curated grade is produced by an operator-run job, never by a request.

Issue #1746, PR-B. ``GET /api/strategies/{id}`` used to run the rigor gate over
the whole curated library on every call and serve THAT verdict, while
``GET /api/strategies/passports/{id}`` served the stored row. One strategy id,
two answers — and the recomputed one moved between reads 37 s apart, because the
provider memoises its backtest map per process and prod runs two tasks.

The owner's call (Dan, 2026-09-01, ``docs/adr/rigor-verdict-of-record.md``) is
that grading is a one-time event: the verdict is produced ONCE, by the real gate,
at backtest time, stored with its provenance, and every surface reads it. So the
property this file guards is not "the read path is fast" but **"the read path
cannot grade"** — a recompute on read is the defect, whatever it is called and
however cheap it becomes.

**Why a static-source guard.** The property is *"this code path does not
exist"*. Asserting that no module outside the two operator scripts can reach the
grading job is stronger than any timing or call-count observation of one route,
and it survives a rename — the same reasoning as
``test_backtests_are_frozen.py``, whose shape this follows.

Hermetic: ``ast.parse`` over committed sources. No DB, no network, no app boot.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import archimedes.main as main_module

# The two operator entry points. Both are ``python -m`` scripts a person runs;
# neither is importable from a serving path.
_GRADING_OWNERS = frozenset(
    {
        "scripts/run_backtests.py",  # new evidence → new grade, one job
        "scripts/grade_curated.py",  # the standalone re-grade / backfill
    }
)
_GRADING_MODULE = "archimedes.services.curated_grading"
_GRADING_ENTRY = "grade_curated_library"


def _reaches_the_grading_job(tree: ast.Module) -> list[tuple[int, str]]:
    """Sites in one parsed module that import, call, or dynamically name the job.

    AST rather than a text scan so prose and tombstone comments do not trip it —
    naming the retired behaviour is how an explanation stops it being re-added.
    A docstring carrying the dotted module path IS treated as a call site,
    because that is what a dynamic import looks like from here.

    ``grade_cohort`` is deliberately NOT guarded: it is a pure computation over a
    list of strategies and writes nothing. ``grade_curated_library`` is the one
    that writes verdicts, and writing a verdict is the event with a policy.
    """
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == _GRADING_MODULE:
                    found.append((node.lineno, f"import {alias.name}"))
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if any(alias.name == _GRADING_ENTRY for alias in node.names):
                found.append((node.lineno, f"from {module or '.'} import {_GRADING_ENTRY}"))
        elif isinstance(node, ast.Call):
            func = node.func
            called = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else None
            if called == _GRADING_ENTRY:
                found.append((node.lineno, f"{_GRADING_ENTRY}(...)"))
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) and _GRADING_MODULE in node.value:
            found.append((node.lineno, f"{_GRADING_MODULE!r} in a string literal"))
    return found


def test_only_the_operator_scripts_reach_the_grading_job() -> None:
    """Nothing under ``archimedes/`` but the two scripts may grade.

    A route, a lifespan hook, a background task, a "cheap" cached wrapper — all
    of them are a recompute on read wearing a different name, and all of them
    reintroduce the disagreement #1746 is about.
    """
    package_root = Path(inspect.getfile(main_module)).resolve().parent
    offenders: list[str] = []
    for path in sorted(package_root.rglob("*.py")):
        rel = path.relative_to(package_root).as_posix()
        if rel in _GRADING_OWNERS or rel == "services/curated_grading.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"), filename=str(path))
        offenders.extend(f"{rel}:{lineno} ({what})" for lineno, what in _reaches_the_grading_job(tree))
    assert not offenders, (
        "these sites import or call the curated grading job outside the operator "
        f"scripts: {offenders}. The rigor verdict is graded once, at backtest time, "
        "and every surface READS it — docs/adr/rigor-verdict-of-record.md. Grading "
        "from a request is the #1746 defect: two endpoints, one strategy id, two "
        "answers. Run it with `python -m archimedes.scripts.grade_curated` "
        "(docs/runbooks/curated-backtests.md § 5)."
    )


def test_the_lifespan_does_not_arm_a_grading_run() -> None:
    """No boot hook, on any tier — the #1760 lesson, applied to grading.

    Grading the library costs one ``run_rigor_gate`` call per strategy plus a
    cohort PBO compute (~6 s measured on a healthy task). Arming that from the
    FastAPI lifespan is the shape that got ECS tasks killed by their own
    container health check.
    """
    fn = getattr(main_module.lifespan, "__wrapped__", main_module.lifespan)
    source = inspect.getsource(fn)
    code = "\n".join(line.split("#", 1)[0] for line in source.splitlines()).lower()
    assert "curated_grading" not in code, (
        "the FastAPI lifespan reaches the curated grading job. Grading is an "
        "operator-run event, not a boot hook — docs/adr/rigor-verdict-of-record.md "
        "and docs/adr/backtests-are-frozen-evidence.md."
    )
    assert _GRADING_ENTRY not in code, (
        f"the FastAPI lifespan calls {_GRADING_ENTRY}. See docs/adr/rigor-verdict-of-record.md."
    )


def test_the_grading_job_is_reachable_from_both_operator_scripts() -> None:
    """The other half: a guard that bans every caller is satisfied by a job
    nothing runs. Both entry points must actually reach it.
    """
    package_root = Path(inspect.getfile(main_module)).resolve().parent
    for owner in sorted(_GRADING_OWNERS):
        tree = ast.parse((package_root / owner).read_text(encoding="utf-8"), filename=owner)
        assert _reaches_the_grading_job(tree), (
            f"{owner} no longer reaches {_GRADING_ENTRY}. With no caller, curated "
            "passports never get a real verdict and every one of them reads "
            "`pending` forever."
        )
