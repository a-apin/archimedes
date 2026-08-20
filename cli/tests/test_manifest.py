"""The manifest is a promise; these tests check it against the truth.

House pattern (compare CRUMB_MAP⊂PAGE_TO_PATH, the slowapi AST invariant):
the hand-written contract in ``manifest.py`` must track the real click
command tree, so an agent reading ``archimedes manifest`` can trust it.
"""

from __future__ import annotations

import json

import click
from click.testing import CliRunner

from archimedes_cli import exits
from archimedes_cli.cli import main
from archimedes_cli.manifest import MANIFEST


def test_every_click_command_appears_in_the_manifest():
    assert set(MANIFEST["commands"].keys()) == set(main.commands.keys())


def test_implemented_flags_match_reality():
    """A command marked implemented must not be the NOT_IMPLEMENTED stub, and
    vice versa — checked by actually invoking the two unimplemented paths."""
    runner = CliRunner()
    # backtest is declared unimplemented → must exit NOT_IMPLEMENTED.
    assert MANIFEST["commands"]["backtest"]["implemented"] is False
    result = runner.invoke(main, ["backtest", "--strategy-path", __file__, "--strategy-class", "X"])
    assert result.exit_code == exits.NOT_IMPLEMENTED
    # manifest is declared implemented → exits OK with parseable JSON.
    assert MANIFEST["commands"]["manifest"]["implemented"] is True
    result = runner.invoke(main, ["manifest"])
    assert result.exit_code == exits.OK
    payload = json.loads(result.stdout)
    assert payload["tool"] == "archimedes"


def test_manifest_flags_match_the_real_commands_both_directions():
    """Flag parity is BIDIRECTIONAL (the drift-proofing promise): every --flag
    the manifest declares must exist on the command, AND every real click
    --option must appear in the manifest — a new flag added to a command
    without updating manifest.py must fail this test (review finding: the
    original one-directional check let that drift through silently)."""
    for name, spec in MANIFEST["commands"].items():
        inputs = spec.get("inputs") or {}
        declared_flags = {k for k in inputs if k.startswith("--")}
        cmd = main.commands[name]
        real_flags = {opt for p in cmd.params if isinstance(p, click.Option) for opt in p.opts if opt.startswith("--")}
        assert declared_flags <= real_flags, (
            f"{name}: manifest declares {declared_flags - real_flags} but the command lacks them"
        )
        assert real_flags <= declared_flags, (
            f"{name}: command has {real_flags - declared_flags} undeclared in the manifest"
        )


def test_manifest_exit_codes_match_exits_module():
    assert MANIFEST["exit_codes"].keys() == {"0", "1", "2", "3"}
    assert exits.OK == 0 and exits.GATE_FAILED == 1 and exits.AUTH == 2 and exits.NOT_IMPLEMENTED == 3


def test_manifest_version_matches_package():
    from archimedes_cli import __version__

    assert MANIFEST["version"] == __version__
