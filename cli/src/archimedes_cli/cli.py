"""The ``archimedes`` entry point.

0.0.1 is a name-reservation release. The command tree below is the shape the tool
will have and the flags are the ones it will accept, but no subcommand does any
work yet: each one explains itself and exits ``NOT_IMPLEMENTED``. Nothing here
opens a socket or reads a credential.

The tree is committed now rather than grown later because the flags are a
contract. ``--json`` on every command and a stable exit code for a failing gate
are what make the tool usable from a CI job, and both are much cheaper to get
right before anyone has scripted against them.
"""

from __future__ import annotations

import json
import sys

import click

from . import __version__
from .exits import NOT_IMPLEMENTED

DEFAULT_API_URL = "https://archimedes-arc.com"

_json_option = click.option(
    "--json",
    "as_json",
    is_flag=True,
    help="Emit a single JSON object on stdout instead of human-readable text.",
)

_api_url_option = click.option(
    "--api-url",
    envvar="ARCHIMEDES_API_URL",
    default=DEFAULT_API_URL,
    show_default=True,
    metavar="URL",
    help="Base URL of the Archimedes API.",
)


def _unavailable(command: str, *, as_json: bool, lands_in: str = "0.1.0") -> None:
    """Report that ``command`` has no implementation yet, then exit.

    Structured output is honoured even here. A caller that asked for ``--json``
    gets JSON on every code path, so a script never has to parse prose to find
    out what happened.
    """
    message = f"'archimedes {command}' is not implemented in {__version__}. It lands in {lands_in}."
    if as_json:
        payload = {
            "ok": False,
            "command": command,
            "error": "not_implemented",
            "version": __version__,
            "lands_in": lands_in,
            "message": message,
        }
        click.echo(json.dumps(payload))
    else:
        click.echo(message, err=True)
        click.echo("See https://github.com/a-apin/archimedes for progress.", err=True)
    sys.exit(NOT_IMPLEMENTED)


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.version_option(__version__, "-V", "--version", prog_name="archimedes")
def main() -> None:
    """Run the Archimedes rigor gate over your own strategies and returns.

    The gate is four checks: a deflated Sharpe ratio that prices in how many
    variants were tried, a probability of backtest overfitting, a walk-forward
    out-of-sample pass, and a look-ahead audit. A strategy that clears all four
    is worth deploying capital behind; one that clears none of them is a curve
    fit that happens to have a pretty equity chart.

    Your strategy code is never uploaded or executed on an Archimedes server.
    See the README for exactly where that boundary sits.
    """


@main.command()
@_api_url_option
@_json_option
def login(api_url: str, as_json: bool) -> None:
    """Authenticate with a wallet signature and cache the session.

    Signs a Sign-In With Ethereum message locally and exchanges it for a session
    cookie. The private key is read once, used to sign, and never sent anywhere.
    """
    del api_url
    _unavailable("login", as_json=as_json)


@main.command()
@_api_url_option
@_json_option
def meter(api_url: str, as_json: bool) -> None:
    """Show what you have used and what it cost.

    Prints the current billing period's call count and USDC spend, so the number
    the CLI reports and the number the invoice reports come from one place.
    """
    del api_url
    _unavailable("meter", as_json=as_json)


@main.command()
@click.argument(
    "returns_csv",
    type=click.Path(exists=True, dir_okay=False, allow_dash=True),
    metavar="RETURNS_CSV",
)
@click.option(
    "--local",
    "run_local",
    is_flag=True,
    help="Run the gate on this machine. No network, no account, no charge.",
)
@_api_url_option
@_json_option
def verify(returns_csv: str, run_local: bool, api_url: str, as_json: bool) -> None:
    """Run the rigor gate over a returns series.

    RETURNS_CSV is a two-column CSV of date and daily return, or ``-`` to read
    the series from stdin.

    Exits 0 when the gate passes and 1 when it fails, which is what makes
    ``archimedes verify returns.csv`` usable as a CI check before a deploy.
    """
    del returns_csv, run_local, api_url
    _unavailable("verify", as_json=as_json)


@main.command()
@click.option(
    "--strategy-path",
    required=True,
    type=click.Path(exists=True, dir_okay=False),
    metavar="PATH",
    help="Python file holding the strategy class.",
)
@click.option(
    "--strategy-class",
    required=True,
    metavar="NAME",
    help="Name of the strategy class inside that file.",
)
@_json_option
def backtest(strategy_path: str, strategy_class: str, as_json: bool) -> None:
    """Backtest a strategy file on this machine and print its returns.

    Runs entirely locally, which is the only way it can work: producing returns
    means importing and executing your Python, and that is not something an
    Archimedes server will ever do with code it received over the wire.

    Pipe the output straight into the gate:

        archimedes backtest --strategy-path mine.py --strategy-class Mine | archimedes verify -
    """
    del strategy_path, strategy_class
    _unavailable("backtest", as_json=as_json)


if __name__ == "__main__":  # pragma: no cover
    main()
