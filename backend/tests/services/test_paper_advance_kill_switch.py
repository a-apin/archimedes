"""The ``PAPER_ADVANCE_ENABLED`` operator kill switch (#1632).

Why this switch exists, and why a test file rather than a line in a runbook:
the paper-advance tick is the one scheduled job that can take the *web tier*
down rather than merely failing. The faulthandler traceback on #1632 shows a
backend container dying with ``Fatal Python error: Aborted`` inside psycopg2
``do_executemany``, on the OHLCV cache-write commit in
``market_data_provider``, reached from ``replay_spec_with_decisions`` via
``fetch_real_panel``. A C-level abort cannot be caught by the loop's fail-soft
``except`` arm, so the task dies at the first tick — ``+PAPER_ADVANCE_STARTUP_DELAY_S``
after boot — and its replacement dies the same way: a cold-fleet spiral.

The only lever that works against an uncatchable abort is not running the code.
That makes this switch load-bearing. #1725 defaulted it ON so unset was a
no-op; task-def :211 proved that hole (cloned last-good, name absent, tick
started, /health 502 at 240s). Unset is now OFF. Every test here is paired
with the mutation it would catch.

Hermetic: no DB, no network, no app import beyond the service module. The
loop's ``asyncio.sleep`` is stubbed to break out after exactly one tick.
"""

from __future__ import annotations

import asyncio
import logging

import pytest
from archimedes.services import paper_trading


class _StopLoop(Exception):
    """Breaks ``paper_advance_loop`` out after one tick.

    Raised from the stubbed ``asyncio.sleep``. Both sleeps the loop performs
    sit OUTSIDE its ``try``, so this propagates instead of being swallowed by
    the fail-soft arm — which is also, incidentally, a check that the arm has
    not been widened to cover the whole body.
    """


# ─── advance_enabled(): the value contract ─────────────────────────────


class TestAdvanceEnabledValueContract:
    def test_unset_is_off_so_a_cloned_task_def_cannot_tick(self, monkeypatch):
        """Task-def :211 died because unset meant ON. Unset now means OFF."""
        monkeypatch.delenv("PAPER_ADVANCE_ENABLED", raising=False)
        assert paper_trading.advance_enabled() is False

    def test_the_getenv_default_literal_is_false(self):
        """Flip-back of the default cannot be a docstring-only change."""
        from pathlib import Path

        src = Path(paper_trading.__file__).read_text(encoding="utf-8")
        assert 'os.getenv("PAPER_ADVANCE_ENABLED", "false")' in src
        assert 'os.getenv("PAPER_ADVANCE_ENABLED", "true")' not in src

    @pytest.mark.parametrize("value", ["false", "FALSE", "False", "0", "no", "off", "  off  ", "OFF"])
    def test_falsy_literals_disable_case_and_whitespace_insensitively(self, monkeypatch, value):
        monkeypatch.setenv("PAPER_ADVANCE_ENABLED", value)
        assert paper_trading.advance_enabled() is False

    @pytest.mark.parametrize("value", ["true", "TRUE", "1", "yes", "on", ""])
    def test_explicit_non_falsy_values_still_enable(self, monkeypatch, value):
        """An explicit value that is not a recognised falsy literal still enables.

        Unset is OFF (see above). A present-but-typoed value is a different
        mistake — it is not the :211 cloned-task-def hole.
        """
        monkeypatch.setenv("PAPER_ADVANCE_ENABLED", value)
        assert paper_trading.advance_enabled() is True

    def test_a_typo_does_not_silently_disable(self, monkeypatch):
        monkeypatch.setenv("PAPER_ADVANCE_ENABLED", "flase")
        assert paper_trading.advance_enabled() is True


# ─── The loop actually consults it ─────────────────────────────────────


def _drive_one_tick(monkeypatch, *, enabled_env: str | None):
    """Run exactly one iteration of ``paper_advance_loop`` and report whether
    the real work ran.

    Returns ``(advance_all_call_count, sleep_durations)``. The sleep list is
    the load-bearing half: it proves the disabled branch still reaches the
    interval sleep at the bottom of the loop.
    """
    if enabled_env is None:
        monkeypatch.delenv("PAPER_ADVANCE_ENABLED", raising=False)
    else:
        monkeypatch.setenv("PAPER_ADVANCE_ENABLED", enabled_env)
    monkeypatch.setenv("PAPER_ADVANCE_STARTUP_DELAY_S", "0")
    monkeypatch.setenv("PAPER_ADVANCE_INTERVAL_HOURS", "24")

    calls = {"advance_all": 0}
    sleeps: list[float] = []

    real_sleep = asyncio.sleep

    async def fake_sleep(seconds, *args, **kwargs):
        sleeps.append(seconds)
        # 1st = startup delay, 2nd = the interval sleep after one tick.
        if len(sleeps) >= 2:
            raise _StopLoop
        return await real_sleep(0)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    # Boundary doubles for the DB seam the loop imports from archimedes.db.
    # Stubbed at the module the loop imports FROM, since it imports inside the
    # function body.
    import archimedes.db as db

    class _FakeSession:
        def commit(self):
            return None

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(db, "init_db", lambda *a, **k: None)
    monkeypatch.setattr(db, "get_session", lambda *a, **k: _FakeSession())

    def fake_advance_all(session):
        calls["advance_all"] += 1
        return {"advanced": 0}

    monkeypatch.setattr(paper_trading, "advance_all", fake_advance_all)

    with pytest.raises(_StopLoop):
        asyncio.run(paper_trading.paper_advance_loop())

    return calls["advance_all"], sleeps


class TestLoopHonoursTheSwitch:
    def test_off_skips_the_work_entirely(self, monkeypatch, caplog):
        """The whole point: no replay runs, so nothing can reach the aborting
        cache write."""
        with caplog.at_level(logging.WARNING, logger=paper_trading.__name__):
            advanced, _sleeps = _drive_one_tick(monkeypatch, enabled_env="false")

        assert advanced == 0, "advance_all ran with the kill switch pulled"
        assert any("PAPER_ADVANCE_ENABLED is off" in r.message for r in caplog.records)
        assert any("#1632" in r.message for r in caplog.records)

    def test_the_skip_log_is_a_warning_not_an_info(self, monkeypatch, caplog):
        """Off means paper ledgers stop advancing — a product claim suspended.
        That is not INFO-level news, and an operator scanning WARNING+ during
        an incident must see it."""
        with caplog.at_level(logging.DEBUG, logger=paper_trading.__name__):
            _drive_one_tick(monkeypatch, enabled_env="off")

        skips = [r for r in caplog.records if "PAPER_ADVANCE_ENABLED is off" in r.message]
        assert skips, "no skip line was logged at all"
        assert all(r.levelno >= logging.WARNING for r in skips)

    def test_disabled_tick_still_sleeps_the_full_interval(self, monkeypatch):
        """The adversarial one, and the reason the skip is an ``else`` rather
        than a ``continue``.

        A ``continue`` would jump past the interval sleep at the bottom of the
        loop and spin the event loop hot forever — turning a mitigation into a
        CPU incident on a fleet that is already cycling. Two sleeps observed
        (startup + interval) is the proof it fell through normally.
        """
        _, sleeps = _drive_one_tick(monkeypatch, enabled_env="false")

        assert len(sleeps) == 2, f"expected startup + interval sleep, saw {sleeps}"
        assert sleeps[0] == 0.0  # startup delay, as set
        assert sleeps[1] == 24 * 3600.0  # the full interval — not a hot spin

    def test_unset_skips_the_work(self, monkeypatch):
        """The :211 hole: cloned task-def, name absent, must not advance."""
        advanced, _ = _drive_one_tick(monkeypatch, enabled_env=None)

        assert advanced == 0, "unset PAPER_ADVANCE_ENABLED must not start the tick"

    def test_explicit_true_runs_the_work(self, monkeypatch):
        """MUTATION CHECK for every skip assertion above.

        Without this, a gate that disabled the tick *unconditionally* — or a
        loop body accidentally deleted — would pass the whole disabled-path
        suite. This is the test that fails if the flag stops being a flag.
        """
        advanced, _ = _drive_one_tick(monkeypatch, enabled_env="true")

        assert advanced == 1


class TestSwitchIsPulledInTheDeployedConfig:
    """The mitigation is only real if the deploy path actually sets it.

    A code-level kill switch that nobody pulled is the #1632 outage still
    running. ``infra/ecs.tf`` is the terraform pin; ``deploy.yml`` is the
    path that actually ships (it clones the live task-def and does not
    apply terraform). Both must stay false. These tests are written to
    FAIL on flip-back, so the tick cannot come back without deleting the
    tests and the comments together.
    """

    def test_ecs_task_definition_pins_it_false_with_the_incident_named(self):
        from pathlib import Path

        ecs_tf = Path(__file__).resolve().parents[3] / "infra" / "ecs.tf"
        source = ecs_tf.read_text()

        assert '{ name = "PAPER_ADVANCE_ENABLED", value = "false" }' in source, (
            "the #1632 mitigation is not pinned in the deployed task definition"
        )
        # The comment is load-bearing: an unexplained "false" is how a
        # temporary mitigation becomes permanent by forgetting.
        assert "#1632" in source
        assert "TEMPORARY" in source
        assert "deploy.yml" in source, (
            "ecs.tf no longer names deploy.yml as the path that actually ships — "
            "a terraform-only pin is how last-good 011b6bfc kept flapping"
        )

    def test_deploy_yml_rewrite_is_the_shipping_pin(self):
        from pathlib import Path

        deploy = Path(__file__).resolve().parents[3] / ".github" / "workflows" / "deploy.yml"
        assert "ecs_rewrite_task_def.py" in deploy.read_text(), (
            "deploy.yml no longer invokes the rewrite that pins PAPER_ADVANCE_ENABLED=false"
        )
