"""Smoke tests for the 0.0.1 command tree.

There is no behaviour to test yet, so these cover the things that are already a
contract with users: the exit codes, the shape of ``--json`` output, the version
the binary reports, and the claim that no command touches the network.
"""

from __future__ import annotations

import json
import re
import socket
from pathlib import Path
from unittest import mock

import pytest
from archimedes_cli import __version__
from archimedes_cli.cli import main
from archimedes_cli.exits import GATE_FAILED, NOT_IMPLEMENTED, OK, USAGE
from click.testing import CliRunner

SUBCOMMANDS = ["login", "meter", "verify", "backtest"]

# Argument lists that satisfy each command's required options, so the command
# reaches its body instead of being rejected by click.
INVOCATIONS = {
    "login": ["login"],
    "meter": ["meter"],
    "verify": ["verify", "returns.csv"],
    "backtest": ["backtest", "--strategy-path", "s.py", "--strategy-class", "S"],
}


@pytest.fixture
def runner(tmp_path, monkeypatch):
    """A CliRunner in a tmp dir holding the files the commands expect to exist."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "returns.csv").write_text("date,ret\n2026-01-02,0.001\n")
    (tmp_path / "s.py").write_text("class S:\n    pass\n")
    return CliRunner()


class TestExitCodesAreAContract:
    """These numbers ship in CI jobs. Changing one silently breaks a user's pipeline."""

    def test_codes_have_their_documented_values(self):
        assert (OK, GATE_FAILED, USAGE, NOT_IMPLEMENTED) == (0, 1, 2, 3)

    @pytest.mark.parametrize("command", SUBCOMMANDS)
    def test_every_subcommand_is_not_implemented(self, runner, command):
        result = runner.invoke(main, INVOCATIONS[command])
        assert result.exit_code == NOT_IMPLEMENTED

    def test_a_missing_file_is_usage_not_not_implemented(self, runner):
        """Click validates before the body runs, so a bad path is 2 and not 3.

        This is the distinction the README asks people to branch on: 3 means the
        command exists but does nothing yet, 2 means they typed something wrong.
        """
        result = runner.invoke(main, ["verify", "no-such-file.csv"])
        assert result.exit_code == USAGE

    def test_stdin_is_an_accepted_returns_source(self, runner):
        """`archimedes backtest ... | archimedes verify -` is the headline usage,
        so `-` has to get past argument validation even in the stub."""
        result = runner.invoke(main, ["verify", "-"])
        assert result.exit_code == NOT_IMPLEMENTED


class TestJsonOutput:
    @pytest.mark.parametrize("command", SUBCOMMANDS)
    def test_json_flag_produces_one_parseable_object(self, runner, command):
        result = runner.invoke(main, [*INVOCATIONS[command], "--json"])
        payload = json.loads(result.stdout)
        assert payload["ok"] is False
        assert payload["error"] == "not_implemented"
        assert payload["command"] == command
        assert payload["version"] == __version__

    @pytest.mark.parametrize("command", SUBCOMMANDS)
    def test_without_json_nothing_lands_on_stdout(self, runner, command):
        """The human path writes to stderr, so piping stdout stays clean."""
        result = runner.invoke(main, INVOCATIONS[command])
        assert result.stdout == ""


class TestVersionReporting:
    def test_version_flag_exits_zero_and_prints_the_version(self, runner):
        result = runner.invoke(main, ["--version"])
        assert result.exit_code == OK
        assert __version__ in result.stdout

    def test_package_version_matches_pyproject(self):
        """A release that reports one number while PyPI shows another is a support
        problem that takes an hour to diagnose and one line to prevent."""
        pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
        match = re.search(r'^version = "([^"]+)"', pyproject.read_text(), re.MULTILINE)
        assert match is not None, "no version line in pyproject.toml"
        assert match.group(1) == __version__


class TestHelp:
    def test_help_lists_every_subcommand(self, runner):
        result = runner.invoke(main, ["--help"])
        assert result.exit_code == OK
        for command in SUBCOMMANDS:
            assert command in result.stdout


class TestNothingTouchesTheNetwork:
    """The README says 0.0.1 opens no sockets. This is what backs that sentence."""

    @pytest.mark.parametrize("command", SUBCOMMANDS)
    def test_no_socket_is_opened(self, runner, command):
        with mock.patch.object(socket, "socket", side_effect=AssertionError("opened a socket")):
            result = runner.invoke(main, INVOCATIONS[command], catch_exceptions=False)
        assert result.exit_code == NOT_IMPLEMENTED
