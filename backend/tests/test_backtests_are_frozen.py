"""A backtest is frozen evidence: no clock, no boot hook, anywhere (#1760).

``services/backtest_scheduler.py`` armed a long-lived task from the FastAPI
lifespan that, after a 180 s settle delay, re-ran ``run_backtests()`` for the
whole curated library **in the serving process**. Its backoff was a
process-global set, so every new task started with it empty and re-ran the
storm exactly once. On the 2026-09-01 deploy (task-def 215) that landed at
+180 s on a 1-vCPU Fargate task while a visitor's cold ``GET /api/strategies/``
was in flight: ``CpuUtilized`` 972 of 1024, ``/health`` past the 5 s container
health-check timeout, three failures, task killed — and ECS replaced it with a
fresh task that booted into the same storm.

The owner's call (Dan, 2026-09-01) was not to make the refresh cheaper but to
retire it: generation, backtesting and grading are one-time events, a backtest
is an artifact of evidence with a stated data window, and it is never revisited
on a clock. Policy: ``docs/adr/backtests-are-frozen-evidence.md``. The one
remaining way to produce a curated backtest is the manual CLI
(``python -m archimedes.scripts.run_backtests``), run out-of-band —
``docs/runbooks/curated-backtests.md``.

**Why a source-text guard.** The property being guarded is *"this code path
does not exist"*. Booting the whole app and waiting three minutes to observe
the absence of a refresh is both slower and weaker than asserting the module is
gone and the lifespan does not name it — the same reasoning as
``test_lifespan_no_rigor_backfill.py``, whose shape this test follows.

Hermetic: an import and ``inspect.getsource``. No DB, no network, no yfinance,
no app boot.
"""

from __future__ import annotations

import importlib
import inspect
from pathlib import Path

import archimedes.main as main_module
import pytest


def _lifespan_source() -> str:
    # __wrapped__: lifespan is decorated with @asynccontextmanager, so the
    # decorated object is not the function whose body we want to inspect.
    fn = getattr(main_module.lifespan, "__wrapped__", main_module.lifespan)
    return inspect.getsource(fn)


def _code_lines(source: str) -> list[str]:
    """Source lines with ``#`` comments stripped.

    Comments are stripped on purpose: the replacement comment in the lifespan
    explains *why* there is no backtest refresh, and naming the retired thing
    is how that explanation stops it being re-added. A guard that fails on its
    own tombstone would force the explanation out.
    """
    out = []
    for raw in source.splitlines():
        line = raw.split("#", 1)[0]
        if line.strip():
            out.append(line)
    return out


def test_backtest_scheduler_module_is_gone() -> None:
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("archimedes.services.backtest_scheduler")


def test_backtest_scheduler_file_is_gone() -> None:
    services = Path(inspect.getfile(main_module)).resolve().parent / "services"
    assert not (services / "backtest_scheduler.py").exists(), (
        "services/backtest_scheduler.py is back. #1760: its boot-time + age-driven "
        "refresh re-ran the curated library in the web process and got ECS tasks "
        "killed by their own container health check. A curated backtest is produced "
        "out-of-band (docs/runbooks/curated-backtests.md), never on a clock."
    )


def test_lifespan_does_not_arm_a_backtest_refresh() -> None:
    code = "\n".join(_code_lines(_lifespan_source()))
    assert "backtest_refresh" not in code, (
        "the FastAPI lifespan arms a backtest refresh again (#1760). Every new "
        "ECS task boots into that loop, it runs run_backtests() for the whole "
        "curated library on the 1-vCPU serving task, and /health misses its 5 s "
        "container health-check budget. Backtests are frozen evidence — "
        "docs/adr/backtests-are-frozen-evidence.md."
    )
    assert "backtest_scheduler" not in code, (
        "the FastAPI lifespan imports backtest_scheduler again (#1760). The module "
        "was deleted; see docs/adr/backtests-are-frozen-evidence.md."
    )


def test_no_module_schedules_a_backtest_refresh() -> None:
    """No source under ``archimedes/`` may schedule a backtest refresh.

    Deleting one module is not the property; *nothing anywhere puts backtests
    on a clock* is. This scans committed sources rather than importing them, so
    a new scheduler under a different name still trips as soon as it spells
    ``backtest_refresh`` or imports the retired module.
    """
    package_root = Path(inspect.getfile(main_module)).resolve().parent
    offenders: list[str] = []
    for path in sorted(package_root.rglob("*.py")):
        text = path.read_text(encoding="utf-8", errors="replace")
        code = "\n".join(_code_lines(text))
        if "backtest_refresh" in code or "backtest_scheduler" in code:
            offenders.append(str(path.relative_to(package_root)))
    assert not offenders, (
        "these modules reference a backtest refresh loop again: "
        f"{offenders}. #1760 retired periodic and boot-time backtest refresh "
        "everywhere — a curated backtest is produced by an explicit operator run "
        "(docs/runbooks/curated-backtests.md), a generated one exactly once at "
        "generation. Policy: docs/adr/backtests-are-frozen-evidence.md."
    )
