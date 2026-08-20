"""The ``archimedes`` entry point.

0.0.1 was a name-reservation release: the command tree and flags were fixed, but no
subcommand did any work. 0.1.0 fills in three of them against the hosted API —
``login`` (Better Auth email + password, ``POST /api/auth/sign-in/email``), ``meter``
(``GET /api/account/usage`` — today's generation usage and the live price quote), and
``verify`` (``POST /api/rigor/verify`` — the rigor gate over a returns series).
``backtest`` and ``verify --local`` still exit ``NOT_IMPLEMENTED``: both need the local
execution engine, which is not published yet.

``--json`` on every command, including every error path, and a stable exit code for a
failing gate are what make the tool usable from a CI job — see ``exits.py``.
"""

from __future__ import annotations

import csv
import io
import json
import os
import sys

import click
import httpx

from . import __version__, exits
from .session import SESSION_COOKIE_NAME, load_session, save_session

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


# Session-aware variant for commands that RUN AGAINST an existing session
# (meter/verify): when --api-url/ARCHIMEDES_API_URL is absent, the URL the
# session was logged into wins over the global default — otherwise a session
# cached against a non-default server would silently send its cookie to the
# wrong host and surface as a confusing 401.
_api_url_session_option = click.option(
    "--api-url",
    envvar="ARCHIMEDES_API_URL",
    default=None,
    metavar="URL",
    help=f"Base URL of the Archimedes API. Defaults to the cached session's URL, else {DEFAULT_API_URL}.",
)


def _resolve_api_url(explicit: str | None, session: dict | None) -> str:
    return explicit or (session or {}).get("api_url") or DEFAULT_API_URL


def _http_client(api_url: str, *, cookies: dict[str, str] | None = None) -> httpx.Client:
    """The one place an ``httpx.Client`` gets constructed.

    Keeping construction behind a single function is what lets tests mock HTTP at the
    boundary: they monkeypatch this factory to return a client wired to an
    ``httpx.MockTransport`` instead of a real socket, rather than patching internals of
    each command.
    """
    # follow_redirects=False, deliberately: the API never redirects, and a
    # compromised/misconfigured endpoint must not be able to bounce a request
    # carrying credentials (login body, session cookie) to another host.
    return httpx.Client(base_url=api_url, cookies=cookies, timeout=10.0, follow_redirects=False)


def _unavailable(command: str, *, as_json: bool, lands_in: str = "unscheduled") -> None:
    """Report that ``command`` has no implementation yet, then exit.

    ``lands_in`` defaults to ``"unscheduled"`` rather than guessing a version number —
    ``backtest`` and ``verify --local`` both need the not-yet-published local execution
    engine, and no target release for that exists yet. Fabricating one (e.g. hardcoding
    the CURRENT version, which 0.0.1 did — a bug once 0.1.0 became the running version
    and made this same message claim a feature "lands in" the release it's running in)
    would be exactly the kind of false claim this repo's conventions forbid.

    Structured output is honoured even here. A caller that asked for ``--json``
    gets JSON on every code path, so a script never has to parse prose to find
    out what happened.
    """
    message = f"'archimedes {command}' is not implemented in {__version__}."
    if lands_in != "unscheduled":
        message += f" It lands in {lands_in}."
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
    sys.exit(exits.NOT_IMPLEMENTED)


def _fail(command: str, *, as_json: bool, exit_code: int, error: str, message: str) -> None:
    """Report a command-produced failure (bad input, no session, a rejected request)
    and exit. Same ``--json``-on-every-path contract as :func:`_unavailable`."""
    if as_json:
        payload = {"ok": False, "command": command, "error": error, "message": message}
        click.echo(json.dumps(payload))
    else:
        click.echo(message, err=True)
    sys.exit(exit_code)


def _response_detail(response: httpx.Response) -> str:
    """Best-effort human string for a non-2xx API response body."""
    try:
        body = response.json()
    except ValueError:
        return response.text[:200] if response.text else f"HTTP {response.status_code}"
    if isinstance(body, dict) and "detail" in body:
        return str(body["detail"])
    return str(body)


def _handle_api_error(command: str, *, as_json: bool, response: httpx.Response) -> None:
    """Turn a non-2xx API response into a :func:`_fail` call. Never returns."""
    if response.status_code == 401:
        _fail(
            command,
            as_json=as_json,
            exit_code=exits.AUTH,
            error="session_expired",
            message="session expired or was revoked. Run `archimedes login` again.",
        )
    if response.status_code == 429:
        _fail(
            command,
            as_json=as_json,
            exit_code=exits.USAGE,
            error="rate_limited",
            message="rate limited by the API. Wait a moment and try again.",
        )
    if response.status_code == 422:
        _fail(
            command,
            as_json=as_json,
            exit_code=exits.USAGE,
            error="invalid_request",
            message=f"the API rejected the request: {_response_detail(response)}",
        )
    _fail(
        command,
        as_json=as_json,
        exit_code=exits.USAGE,
        error="http_error",
        message=f"{command} failed: HTTP {response.status_code}: {_response_detail(response)}",
    )


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.version_option(__version__, "-V", "--version", prog_name="archimedes")
def main() -> None:
    """Run the Archimedes rigor gate over your own strategies and returns.

    The gate is four checks: a deflated Sharpe ratio that prices in how many
    variants were tried, a probability of backtest overfitting, a walk-forward
    out-of-sample pass, and a look-ahead audit. A strategy that clears all four
    is worth deploying capital behind; one that clears none of them is a curve
    fit that happens to have a pretty equity chart.

    A bare returns series (what ``verify`` takes) cannot support all four: PBO
    needs a trial matrix and the look-ahead audit needs strategy source, so those
    two always render ``not_evaluable`` rather than being silently passed.

    Your strategy code is never uploaded or executed on an Archimedes server.
    See the README for exactly where that boundary sits.
    """


@main.command()
@_api_url_option
@_json_option
def login(api_url: str, as_json: bool) -> None:
    """Sign in and cache the session.

    Archimedes accounts are Better Auth (email + password) — there is no wallet
    signature involved in signing in. Reads ``ARCHIMEDES_EMAIL`` / ``ARCHIMEDES_PASSWORD``
    from the environment when both are set (the CI path, so a pipeline never has to
    answer an interactive prompt); otherwise prompts for them, with the password hidden.

    The password is sent once, over this one request (``POST /api/auth/sign-in/email``),
    and is never stored. What gets cached at ``~/.config/archimedes/session.json``
    (mode 600) is the session cookie Better Auth issues back, after confirming it round
    trips against ``GET /api/auth/get-session``. Linking a crypto wallet is a separate,
    optional, later step (for on-chain vault actions) — it does not authenticate you and
    is not part of this command.
    """
    email = os.environ.get("ARCHIMEDES_EMAIL", "").strip()
    password = os.environ.get("ARCHIMEDES_PASSWORD", "")
    if not email:
        email = click.prompt("Email")
    if not password:
        password = click.prompt("Password", hide_input=True)

    try:
        with _http_client(api_url) as client:
            response = client.post("/api/auth/sign-in/email", json={"email": email, "password": password})
    except httpx.HTTPError as exc:
        _fail(
            "login",
            as_json=as_json,
            exit_code=exits.AUTH,
            error="network_error",
            message=f"could not reach {api_url}: {exc}",
        )

    if not response.is_success:
        _fail(
            "login",
            as_json=as_json,
            exit_code=exits.AUTH,
            error="invalid_credentials",
            message=f"sign-in failed: HTTP {response.status_code}: {_response_detail(response)}",
        )

    token = response.cookies.get(SESSION_COOKIE_NAME)
    if not token:
        _fail(
            "login",
            as_json=as_json,
            exit_code=exits.AUTH,
            error="no_session_cookie",
            message="sign-in succeeded but the server did not return a session cookie",
        )

    # Confirm the cookie actually round-trips, and read back the canonical account email,
    # before trusting it — mirrors scripts/agent_journey.py's step_auth.
    try:
        with _http_client(api_url, cookies={SESSION_COOKIE_NAME: token}) as client:
            session_response = client.get("/api/auth/get-session")
        session_payload = session_response.json() if session_response.is_success else None
    except (httpx.HTTPError, ValueError):
        # ValueError covers json.JSONDecodeError from a 200 with a non-JSON or empty
        # body — treated the same as "the round trip didn't confirm a session", not a
        # crash.
        session_payload = None

    user = session_payload.get("user") if isinstance(session_payload, dict) else None
    confirmed_email = user.get("email") if isinstance(user, dict) else None
    if not confirmed_email:
        _fail(
            "login",
            as_json=as_json,
            exit_code=exits.AUTH,
            error="session_not_confirmed",
            message="signed in, but the session cookie did not round-trip on GET /api/auth/get-session",
        )

    path = save_session(api_url=api_url, cookies={SESSION_COOKIE_NAME: token}, email=confirmed_email)

    if as_json:
        click.echo(json.dumps({"ok": True, "email": confirmed_email}))
    else:
        click.echo(f"Logged in as {confirmed_email}. Session cached at {path}.")
    sys.exit(exits.OK)


def _render_usage(usage: dict) -> None:
    click.echo(f"Usage for {usage.get('date', '?')} (user {usage.get('user_id', '?')})")
    for label, bucket in (("Per-user", usage.get("user") or {}), ("Per-IP", usage.get("ip") or {})):
        error = bucket.get("error")
        used = bucket.get("used")
        if error:
            click.echo(f"  {label}: unavailable ({error})")
        elif bucket.get("unlimited"):
            click.echo(f"  {label}: {used if used is not None else '?'} used (no cap)")
        else:
            click.echo(f"  {label}: {used}/{bucket.get('cap')} used, {bucket.get('remaining')} remaining")
    quote = usage.get("quote") or {}
    price = quote.get("price")
    if price is not None:
        suffix = " (dry run)" if quote.get("dry_run") else ""
        click.echo(f"  Price per generation: {price} {quote.get('asset', '')}{suffix}".rstrip())


@main.command()
@_api_url_session_option
@_json_option
def meter(api_url: str | None, as_json: bool) -> None:
    """Show what you have used today and what it costs.

    ``GET /api/account/usage`` — today's generation count against both the per-user and
    per-IP daily caps, plus the live price quote (the same ``generation_payment.quote()``
    call the paywall itself reads, so this number and the invoice can never drift apart).
    Requires a cached session; run ``archimedes login`` first.
    """
    session = load_session()
    if session is None:
        _fail(
            "meter",
            as_json=as_json,
            exit_code=exits.AUTH,
            error="no_session",
            message="not logged in. Run `archimedes login` first.",
        )
    api_url = _resolve_api_url(api_url, session)

    try:
        with _http_client(api_url, cookies=session["cookies"]) as client:
            response = client.get("/api/account/usage")
    except httpx.HTTPError as exc:
        _fail(
            "meter",
            as_json=as_json,
            exit_code=exits.USAGE,
            error="network_error",
            message=f"could not reach {api_url}: {exc}",
        )

    if not response.is_success:
        _handle_api_error("meter", as_json=as_json, response=response)

    usage = response.json()
    if as_json:
        click.echo(json.dumps({"ok": True, **usage}))
    else:
        _render_usage(usage)
    sys.exit(exits.OK)


def _parse_returns_csv(source: str) -> list[dict]:
    """Parse ``RETURNS_CSV`` into ``[{"date": ..., "daily_return": ...}, ...]``.

    Two columns: date, then daily return. A header row (or any row whose second column
    does not parse as a float — a blank line, a comment) is skipped rather than rejected,
    so both a ``date,daily_return`` header and a bare headerless file work.
    """
    if source == "-":
        text = click.get_text_stream("stdin").read()
    else:
        with open(source, newline="", encoding="utf-8") as handle:
            text = handle.read()

    rows: list[dict] = []
    for row in csv.reader(io.StringIO(text)):
        if len(row) < 2:
            continue
        date_str = row[0].strip()
        if not date_str:
            continue
        try:
            value = float(row[1].strip())
        except ValueError:
            continue
        rows.append({"date": date_str, "daily_return": value})
    return rows


_CHECK_LABELS = (
    ("DSR", "dsr"),
    ("PBO", "pbo"),
    ("OOS consistency", "oos_consistency"),
    ("Look-ahead", "look_ahead"),
)
_STATUS_TAG = {"pass": "PASS", "fail": "FAIL", "not_evaluable": "N/A"}


def _render_verify(body: dict) -> None:
    click.echo(f"n_bars={body.get('n_bars')}  trials={body.get('trials')} (self-attested)")
    for name, key in _CHECK_LABELS:
        check = body.get(key) or {}
        status = check.get("status", "?")
        tag = _STATUS_TAG.get(status, status.upper())
        line = f"  [{tag}] {name}"
        reason = check.get("reason")
        if reason:
            line += f" — {reason}"
        click.echo(line)
    click.echo("PASSES" if body.get("passes") else "FAILS")


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
@click.option(
    "--trials",
    type=int,
    default=1,
    show_default=True,
    metavar="N",
    help="Self-attested trial/variant count the DSR deflation is computed against (>= 1).",
)
@_api_url_session_option
@_json_option
def verify(returns_csv: str, run_local: bool, trials: int, api_url: str | None, as_json: bool) -> None:
    """Run the rigor gate over a returns series.

    RETURNS_CSV is a two-column CSV of date and daily return (a header row, if present,
    is skipped automatically), or ``-`` to read the series from stdin.

    Runs against the hosted API: ``POST /api/rigor/verify`` computes the deflated Sharpe
    ratio (DSR, deflated by ``--trials``) and a walk-forward out-of-sample consistency
    check against your real returns — the exact same functions and thresholds the
    strategy-passport verdict uses. A bare returns series cannot support the other two
    gate checks: PBO needs a trial matrix of candidate strategies and the look-ahead audit
    needs strategy source, so both always render ``not_evaluable`` with a reason rather
    than being silently scored as a pass. ``--local`` (this machine, no network, no
    account) is not implemented yet.

    Requires a cached session; run ``archimedes login`` first. Exits 0 when the gate
    passes and 1 when it fails, which is what makes ``archimedes verify returns.csv``
    usable as a CI check before a deploy.
    """
    if run_local:
        _unavailable("verify", as_json=as_json)

    if trials < 1:
        _fail(
            "verify",
            as_json=as_json,
            exit_code=exits.USAGE,
            error="invalid_trials",
            message="--trials must be >= 1",
        )

    returns = _parse_returns_csv(returns_csv)
    if not returns:
        _fail(
            "verify",
            as_json=as_json,
            exit_code=exits.USAGE,
            error="empty_returns",
            message=f"no (date, daily_return) rows parsed from {returns_csv!r}",
        )

    session = load_session()
    if session is None:
        _fail(
            "verify",
            as_json=as_json,
            exit_code=exits.AUTH,
            error="no_session",
            message="not logged in. Run `archimedes login` first.",
        )

    api_url = _resolve_api_url(api_url, session)
    try:
        with _http_client(api_url, cookies=session["cookies"]) as client:
            response = client.post("/api/rigor/verify", json={"returns": returns, "trials": trials})
    except httpx.HTTPError as exc:
        _fail(
            "verify",
            as_json=as_json,
            exit_code=exits.USAGE,
            error="network_error",
            message=f"could not reach {api_url}: {exc}",
        )

    if not response.is_success:
        _handle_api_error("verify", as_json=as_json, response=response)

    body = response.json()
    if as_json:
        click.echo(json.dumps({"ok": True, **body}))
    else:
        _render_verify(body)
    sys.exit(exits.OK if body.get("passes") else exits.GATE_FAILED)


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


@main.command()
def manifest() -> None:
    """Emit this CLI's machine-readable contract and exit 0.

    Agentic connectivity is the interface: an agent should discover what this
    tool accepts, returns, costs, and exits with from a declarative contract —
    not by parsing --help prose. Always JSON, no network, no session. A test
    walks the real click command tree and asserts this contract matches it,
    so the promise cannot silently drift from the truth.
    """
    from .manifest import MANIFEST

    click.echo(json.dumps(MANIFEST, indent=2))
    sys.exit(exits.OK)


if __name__ == "__main__":  # pragma: no cover
    main()
