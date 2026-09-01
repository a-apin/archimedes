"""``paper_advance_loop`` must not be able to kill the web process.

The #1632 faulthandler traceback is a C-level abort inside psycopg2
``do_executemany``, reached from the paper-advance replay. ``except Exception``
cannot catch it. Isolation is therefore a process boundary, not a try arm.

These tests pin three properties:

1. The FastAPI lifespan never schedules ``paper_advance_loop()`` in-process.
2. ``arm_paper_advance_for_web_tier`` refuses to spawn when the kill switch
   is off (including unset — the :211 hole), and when it is on it spawns a
   child rather than calling ``advance_all`` here.
3. A C-level death in that child (SIGSEGV, the ECS 139) leaves the parent
   alive. That is the proof ``/health`` survives the paper-advance window.

Hermetic: no DB, no network, no ``archimedes.main`` import. Lifespan wiring
is a source inspection of ``main.py``. The SIGSEGV case is a real subprocess
with a ``python -c`` child that never imports archimedes.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import os
import signal
import sys
from pathlib import Path

from archimedes.services import paper_trading

MAIN_PY = Path(__file__).resolve().parents[2] / "archimedes" / "main.py"


class _FakeProc:
    def __init__(self, argv):
        self.argv = argv
        self.returncode = None

    def wait(self, timeout=None):
        self.returncode = 0
        return 0

    def poll(self):
        return self.returncode

    def terminate(self):
        self.returncode = -15

    def kill(self):
        self.returncode = -9


def _main_code() -> str:
    """``main.py`` with comments stripped, so a warning in a comment cannot
    satisfy (or fail) a call-site assertion."""
    lines = []
    for raw in MAIN_PY.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0]
        if line.strip():
            lines.append(line)
    return "\n".join(lines)


class TestLifespanRefusesTheInProcessLoop:
    def test_lifespan_does_not_call_paper_advance_loop(self):
        code = _main_code()
        assert "paper_advance_loop(" not in code, (
            "main.py schedules paper_advance_loop in-process again. A C abort "
            "on that tick kills /health. Arm arm_paper_advance_for_web_tier instead."
        )
        assert "arm_paper_advance_for_web_tier" in code

    def test_module_main_runs_the_loop_not_the_supervisor(self):
        """Child entry must not recurse into another spawn."""
        src = inspect.getsource(paper_trading._module_main)
        # Skip the docstring — it names the supervisor as the thing not to call.
        first = src.find('"""')
        second = src.find('"""', first + 3)
        body = src[second + 3 :]
        assert "asyncio.run(paper_advance_loop())" in body
        assert "arm_paper_advance_for_web_tier(" not in body
        assert "paper_advance_supervisor(" not in body


class TestWebTierArming:
    def test_unset_does_not_spawn_and_does_not_advance(self, monkeypatch, caplog):
        """The :211 hole at the process boundary: name absent, must not spawn."""
        monkeypatch.delenv("PAPER_ADVANCE_ENABLED", raising=False)

        def boom(*_a, **_k):
            raise AssertionError("spawned a child with PAPER_ADVANCE_ENABLED unset")

        def advance_boom(*_a, **_k):
            raise AssertionError("advance_all ran in the web process")

        monkeypatch.setattr(paper_trading, "advance_all", advance_boom)
        with caplog.at_level(logging.WARNING, logger=paper_trading.__name__):
            result = asyncio.run(paper_trading.arm_paper_advance_for_web_tier(popen=boom))

        assert result is None
        assert any("PAPER_ADVANCE_ENABLED is off" in r.message for r in caplog.records)

    def test_kill_switch_off_does_not_spawn_and_does_not_advance(self, monkeypatch, caplog):
        monkeypatch.setenv("PAPER_ADVANCE_ENABLED", "false")

        def boom(*_a, **_k):
            raise AssertionError("spawned a child with the kill switch pulled")

        def advance_boom(*_a, **_k):
            raise AssertionError("advance_all ran in the web process")

        monkeypatch.setattr(paper_trading, "advance_all", advance_boom)
        with caplog.at_level(logging.WARNING, logger=paper_trading.__name__):
            result = asyncio.run(paper_trading.arm_paper_advance_for_web_tier(popen=boom))

        assert result is None
        assert any("PAPER_ADVANCE_ENABLED is off" in r.message for r in caplog.records)
        assert any("#1632" in r.message for r in caplog.records)

    def test_kill_switch_on_spawns_a_child_and_does_not_advance_here(self, monkeypatch):
        monkeypatch.setenv("PAPER_ADVANCE_ENABLED", "true")
        spawned: list[list[str]] = []

        def fake_popen(argv, **_kwargs):
            spawned.append(list(argv))
            return _FakeProc(argv)

        def advance_boom(*_a, **_k):
            raise AssertionError("advance_all ran in the web process")

        monkeypatch.setattr(paper_trading, "advance_all", advance_boom)
        asyncio.run(paper_trading.arm_paper_advance_for_web_tier(popen=fake_popen))

        assert spawned, "enabled arming did not spawn a child"
        assert spawned[0][-2:] == ["-m", "archimedes.services.paper_trading"]


class TestChildAbortDoesNotKillParent:
    def test_sigsegv_in_the_child_leaves_this_process_alive(self):
        """The load-bearing isolation proof.

        ECS recorded essential-container exit 139 (SIGSEGV) on the web task.
        A child that raises SIGSEGV must not take this interpreter with it.
        Getting past ``asyncio.run`` *is* the assertion; the returncode shows
        the death happened in the child.
        """
        child = "import os, signal; os.kill(os.getpid(), signal.SIGSEGV)"
        rc = asyncio.run(
            paper_trading.paper_advance_supervisor(argv=[sys.executable, "-c", child]),
        )
        assert os.getpid() > 0  # parent still here
        assert rc in (-signal.SIGSEGV, 128 + signal.SIGSEGV), (
            f"child did not die of SIGSEGV; returncode={rc!r} — isolation unproven"
        )
