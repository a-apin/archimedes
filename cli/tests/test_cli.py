"""Tests for the 0.1.0 command tree.

0.0.1 shipped a stub with nothing to test but shape (exit codes, `--json`, no sockets).
0.1.0 makes `login`, `meter`, and `verify` real against the hosted API, so this file adds:
real HTTP interactions mocked at the transport boundary (`httpx.MockTransport`, patched in
via the CLI's own `_http_client` factory — never internals), exit-code coverage for the new
`AUTH` family, the `not_evaluable` rendering the honesty contract requires, and the
session-file's 600 permission. `backtest` and `verify --local` are unchanged stubs and keep
their original no-network guarantee.
"""

from __future__ import annotations

import json
import re
import socket
import stat
from pathlib import Path
from unittest import mock

import archimedes_cli.cli as cli_module
import httpx
import pytest
from archimedes_cli import __version__
from archimedes_cli.cli import main
from archimedes_cli.exits import AUTH, GATE_FAILED, INCOMPLETE, NOT_IMPLEMENTED, OK, USAGE
from archimedes_cli.session import SECURE_SESSION_COOKIE_NAME, SESSION_COOKIE_NAME, save_session, session_path
from click.testing import CliRunner

# ── Fixtures & test-only helpers ────────────────────────────────────────


@pytest.fixture
def runner(tmp_path, monkeypatch):
    """A CliRunner in a tmp dir, with an isolated $HOME (so the session cache never
    touches a real `~/.config/archimedes`) and no ambient CI credentials leaking in
    from the real environment."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("ARCHIMEDES_EMAIL", raising=False)
    monkeypatch.delenv("ARCHIMEDES_PASSWORD", raising=False)
    monkeypatch.delenv("ARCHIMEDES_API_URL", raising=False)
    (tmp_path / "returns.csv").write_text(
        "date,daily_return\n2026-01-02,0.0021\n2026-01-03,-0.0009\n2026-01-04,0.0014\n"
    )
    (tmp_path / "s.py").write_text("class S:\n    pass\n")
    return CliRunner()


def _route(routes: dict[tuple[str, str], object]):
    """Build an ``httpx.MockTransport`` handler from a ``{(method, path): response}``
    map. ``response`` may be an ``httpx.Response`` or a callable taking the request and
    returning one (for handlers that need to inspect the request body/headers)."""

    def handler(request: httpx.Request) -> httpx.Response:
        key = (request.method, request.url.path)
        target = routes.get(key)
        if target is None:
            raise AssertionError(f"unexpected request: {request.method} {request.url.path}")
        return target(request) if callable(target) else target

    return handler


def _install_transport(monkeypatch, handler):
    """Mock HTTP at the boundary: patch the CLI's one client-construction function so
    every client it builds talks to ``handler`` via ``httpx.MockTransport`` instead of a
    real socket. Nothing inside `cli.py`'s command bodies is touched."""

    def factory(api_url, *, cookies=None):
        return httpx.Client(base_url=api_url, cookies=cookies, transport=httpx.MockTransport(handler))

    monkeypatch.setattr(cli_module, "_http_client", factory)


def _forbid_network(monkeypatch):
    """Install a transport factory that blows up if the CLI ever tries to build an HTTP
    client — proves a client-side guard short-circuited BEFORE any request, not merely
    that the eventual response happened to look like a failure."""

    def factory(api_url, *, cookies=None):  # noqa: ARG001 — signature must match `_http_client`'s
        raise AssertionError("a guard should have stopped this before any HTTP client was built")

    monkeypatch.setattr(cli_module, "_http_client", factory)


def _seed_session(cookie_value: str = "opaque-token", email: str = "dan@example.com") -> Path:
    return save_session(
        api_url="https://archimedes-arc.com",
        cookies={SESSION_COOKIE_NAME: cookie_value},
        email=email,
    )


_USAGE_BODY = {
    "date": "2026-08-19",
    "user_id": "user-abc",
    "user": {"used": 3, "cap": 10, "unlimited": False, "remaining": 7, "error": None},
    "ip": {"used": 0, "cap": 20, "unlimited": False, "remaining": 20, "error": None},
    "quote": {
        "payment_required": True,
        "pricing_model": "flat_v1",
        "price": "$0.05",
        "asset": "USDC",
        "chain": "arc-testnet",
        "recipient": "0xabc",
        "dry_run": True,
        "how": "POST /api/generate/start without a Payment-Signature header returns 402 ...",
    },
}

_USAGE_BODY_QUOTA_DOWN = {
    "date": "2026-08-19",
    "user_id": "user-abc",
    "user": {"used": None, "cap": 10, "unlimited": False, "remaining": None, "error": "quota_backend_unavailable"},
    "ip": {"used": None, "cap": 20, "unlimited": False, "remaining": None, "error": "quota_backend_unavailable"},
    "quote": {"price": "$0.05", "asset": "USDC", "dry_run": True, "pricing_model": "flat_v1"},
}

_VERIFY_PASS_BODY = {
    "passes": True,
    "trials": 1,
    "self_attested": True,
    "n_bars": 300,
    # #1481: the server qualifies `passes` with the quorum it was computed over.
    "legs_evaluated": 2,
    "legs_runnable": 2,
    "legs_total": 4,
    "legs_not_run": ["pbo", "look_ahead"],
    "verdict_capped": True,
    "dsr": {
        "status": "pass",
        "reason": "self-attested trials=1: DSR p-value 0.9997 >= floor 0.50 (Newey-West HAC standard error)",
        "deflated_sharpe": 1.9,
        "dsr_p_value": 0.9997,
    },
    "pbo": {
        "status": "not_evaluable",
        "reason": "PBO requires a trial matrix of multiple candidate strategies' returns.",
    },
    "oos_consistency": {
        "status": "pass",
        "reason": "walk-forward OOS Sharpe 2.6300 > floor 0.00 (chronological 70/30 holdout)",
        "oos_sharpe": 2.63,
        "in_sample_sharpe": 2.1,
    },
    "look_ahead": {
        "status": "not_evaluable",
        "reason": "The look-ahead audit is AST-based static analysis of strategy source code.",
    },
    "rf_convention": "excess_tbill_series",
}

_VERIFY_FAIL_BODY = {
    "passes": False,
    "trials": 1,
    "self_attested": True,
    "n_bars": 300,
    "legs_evaluated": 2,
    "legs_runnable": 2,
    "legs_total": 4,
    "legs_not_run": ["pbo", "look_ahead"],
    "verdict_capped": True,
    "dsr": {
        "status": "fail",
        "reason": "self-attested trials=1: DSR p-value 0.0002 < floor 0.50 (Newey-West HAC standard error)",
        "deflated_sharpe": -1.2,
        "dsr_p_value": 0.000165,
    },
    "pbo": {"status": "not_evaluable", "reason": "PBO requires a trial matrix."},
    "oos_consistency": {
        "status": "fail",
        "reason": "walk-forward OOS Sharpe -3.6200 <= floor 0.00 — strategy loses money out-of-sample",
        "oos_sharpe": -3.62,
        "in_sample_sharpe": -0.4,
    },
    "look_ahead": {"status": "not_evaluable", "reason": "The look-ahead audit needs strategy source."},
    "rf_convention": "excess_flat_fallback",
}

_VERIFY_NOT_EVALUABLE_BODY = {
    "passes": False,
    "trials": 1,
    "self_attested": True,
    "n_bars": 3,
    "legs_evaluated": 0,
    "legs_runnable": 2,
    "legs_total": 4,
    "legs_not_run": ["dsr", "pbo", "oos_consistency", "look_ahead"],
    "verdict_capped": True,
    "dsr": {"status": "not_evaluable", "reason": "return series too short or degenerate for DSR (need >= 4 bars)"},
    "pbo": {"status": "not_evaluable", "reason": "PBO requires a trial matrix."},
    "oos_consistency": {"status": "not_evaluable", "reason": "insufficient data for a walk-forward OOS split"},
    "look_ahead": {"status": "not_evaluable", "reason": "The look-ahead audit needs strategy source."},
    "rf_convention": "excess_flat_fallback",
}


def _login_ok_route(*, cookie: str = "tok123", email: str = "dan@example.com", cookie_name: str = SESSION_COOKIE_NAME):
    return {
        ("POST", "/api/auth/sign-in/email"): httpx.Response(
            200,
            json={"redirect": False},
            headers=[("set-cookie", f"{cookie_name}={cookie}; Path=/; HttpOnly")],
        ),
        ("GET", "/api/auth/get-session"): httpx.Response(
            200,
            json={"user": {"id": "u1", "email": email, "name": "Dan"}, "session": {"id": "s1"}},
        ),
    }


# ── Exit codes, version, help (kept from 0.0.1; extended for AUTH) ─────


class TestExitCodesAreAContract:
    """These numbers ship in CI jobs. Changing one silently breaks a user's pipeline."""

    def test_codes_have_their_documented_values(self):
        assert (OK, GATE_FAILED, USAGE, AUTH, NOT_IMPLEMENTED) == (0, 1, 2, 2, 3)

    def test_a_missing_file_is_usage_not_not_implemented(self, runner):
        """Click validates before the body runs, so a bad path is 2 and not 3 — still
        true in 0.1.0: `verify`'s RETURNS_CSV argument keeps `exists=True`."""
        result = runner.invoke(main, ["verify", "no-such-file.csv"])
        assert result.exit_code == USAGE


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
        for command in ("login", "meter", "verify", "backtest"):
            assert command in result.stdout


# ── backtest + verify --local: still stubs, still no network ───────────


class TestStubsStillNotImplemented:
    """`backtest` and `verify --local` are explicitly out of scope for 0.1.0 (need the
    unpublished local execution engine) and must keep 0.0.1's guarantees exactly."""

    @pytest.mark.parametrize(
        ("invocation", "command"),
        [
            (["backtest", "--strategy-path", "s.py", "--strategy-class", "S"], "backtest"),
            (["verify", "--local", "returns.csv"], "verify"),
        ],
    )
    def test_not_implemented_json_shape(self, runner, invocation, command):
        result = runner.invoke(main, [*invocation, "--json"])
        assert result.exit_code == NOT_IMPLEMENTED
        payload = json.loads(result.stdout)
        assert payload == {
            "ok": False,
            "command": command,
            "error": "not_implemented",
            "version": __version__,
            "lands_in": "unscheduled",
            "message": payload["message"],
        }
        # The self-contradictory claim this guards against: 0.0.1 hardcoded
        # `lands_in="0.1.0"`, which became false the moment 0.1.0 itself became the
        # running version (a feature can't "land in" the release it's already in).
        assert __version__ not in payload["lands_in"]

    @pytest.mark.parametrize(
        "invocation",
        [
            ["backtest", "--strategy-path", "s.py", "--strategy-class", "S"],
            ["verify", "--local", "returns.csv"],
        ],
    )
    def test_no_socket_is_opened(self, runner, invocation):
        with mock.patch.object(socket, "socket", side_effect=AssertionError("opened a socket")):
            result = runner.invoke(main, invocation, catch_exceptions=False)
        assert result.exit_code == NOT_IMPLEMENTED


# ── login ────────────────────────────────────────────────────────────


class TestLogin:
    def test_success_via_env_credentials_reports_ok_and_caches_session(self, runner, monkeypatch):
        monkeypatch.setenv("ARCHIMEDES_EMAIL", "dan@example.com")
        monkeypatch.setenv("ARCHIMEDES_PASSWORD", "correct horse battery staple")
        _install_transport(monkeypatch, _route(_login_ok_route(cookie="tok123", email="dan@example.com")))

        result = runner.invoke(main, ["login", "--json"])

        assert result.exit_code == OK
        assert json.loads(result.stdout) == {"ok": True, "email": "dan@example.com"}

        cached = json.loads(session_path().read_text())
        assert cached["email"] == "dan@example.com"
        assert cached["cookies"][SESSION_COOKIE_NAME] == "tok123"

    def test_login_accepts_the_secure_prefixed_cookie_production_actually_sets(self, runner, monkeypatch):
        """The P0 this fix closes: production sets `__Secure-better-auth.session_token`
        (Better Auth's `useSecureCookies: production`, `auth/auth.js`), never the bare
        name. A client that only recognized the bare name would report
        `no_session_cookie` on every real login against archimedes-arc.com."""
        monkeypatch.setenv("ARCHIMEDES_EMAIL", "dan@example.com")
        monkeypatch.setenv("ARCHIMEDES_PASSWORD", "correct horse battery staple")
        _install_transport(
            monkeypatch,
            _route(_login_ok_route(cookie="tok-secure", cookie_name=SECURE_SESSION_COOKIE_NAME)),
        )

        result = runner.invoke(main, ["login", "--json"])

        assert result.exit_code == OK
        assert json.loads(result.stdout) == {"ok": True, "email": "dan@example.com"}

        cached = json.loads(session_path().read_text())
        assert cached["cookies"] == {SECURE_SESSION_COOKIE_NAME: "tok-secure"}
        assert SESSION_COOKIE_NAME not in cached["cookies"]

    def test_login_round_trips_the_secure_cookie_under_its_own_name_not_the_bare_one(self, runner, monkeypatch):
        """Adversarial: proves the round-trip GET /api/auth/get-session is sent the cookie
        under the NAME IT WAS ISSUED AS, not a hardcoded bare name. A build that hardcoded
        `SESSION_COOKIE_NAME` for the round trip would send no recognizable cookie here,
        get-session would report nobody signed in, and login would wrongly fail
        `session_not_confirmed` even though sign-in issued a good `__Secure-` cookie."""
        monkeypatch.setenv("ARCHIMEDES_EMAIL", "dan@example.com")
        monkeypatch.setenv("ARCHIMEDES_PASSWORD", "whatever")

        def get_session(request: httpx.Request) -> httpx.Response:
            jar = dict(
                pair.strip().split("=", 1) for pair in request.headers.get("cookie", "").split(";") if "=" in pair
            )
            if jar.get(SECURE_SESSION_COOKIE_NAME) != "tok-secure":
                return httpx.Response(200, json={"user": None, "session": None})
            return httpx.Response(
                200, json={"user": {"id": "u1", "email": "dan@example.com", "name": "Dan"}, "session": {"id": "s1"}}
            )

        _install_transport(
            monkeypatch,
            _route(
                {
                    ("POST", "/api/auth/sign-in/email"): httpx.Response(
                        200,
                        json={"redirect": False},
                        headers=[("set-cookie", f"{SECURE_SESSION_COOKIE_NAME}=tok-secure; Path=/; Secure; HttpOnly")],
                    ),
                    ("GET", "/api/auth/get-session"): get_session,
                }
            ),
        )

        result = runner.invoke(main, ["login", "--json"])
        assert result.exit_code == OK
        assert json.loads(result.stdout) == {"ok": True, "email": "dan@example.com"}
        assert json.loads(session_path().read_text())["cookies"] == {SECURE_SESSION_COOKIE_NAME: "tok-secure"}

    def test_adversarial_an_unrelated_cookie_is_not_mistaken_for_the_session(self, runner, monkeypatch):
        """A Set-Cookie for something that is neither recognized name (a CSRF token, a
        load-balancer affinity cookie) must not be picked up as the session — this is
        the input that should fail `pick_session_cookie` and it must fail closed."""
        monkeypatch.setenv("ARCHIMEDES_EMAIL", "dan@example.com")
        monkeypatch.setenv("ARCHIMEDES_PASSWORD", "whatever")
        _install_transport(
            monkeypatch,
            _route(
                {
                    ("POST", "/api/auth/sign-in/email"): httpx.Response(
                        200,
                        json={"redirect": False},
                        headers=[("set-cookie", "csrf_token=irrelevant; Path=/")],
                    ),
                }
            ),
        )
        result = runner.invoke(main, ["login", "--json"])
        assert result.exit_code == AUTH
        assert json.loads(result.stdout)["error"] == "no_session_cookie"
        assert not session_path().exists()

    def test_session_file_is_written_mode_600(self, runner, monkeypatch):
        monkeypatch.setenv("ARCHIMEDES_EMAIL", "dan@example.com")
        monkeypatch.setenv("ARCHIMEDES_PASSWORD", "hunter2-but-longer")
        _install_transport(monkeypatch, _route(_login_ok_route()))

        result = runner.invoke(main, ["login", "--json"])

        assert result.exit_code == OK
        mode = stat.S_IMODE(session_path().stat().st_mode)
        assert mode == 0o600, f"session.json is {oct(mode)}, must be 0o600"

    def test_prompts_for_credentials_when_env_vars_absent(self, runner, monkeypatch):
        _install_transport(monkeypatch, _route(_login_ok_route(email="prompted@example.com")))
        result = runner.invoke(main, ["login"], input="prompted@example.com\ns3cret\n")
        assert result.exit_code == OK
        assert "Logged in as prompted@example.com" in result.stdout

    def test_invalid_credentials_exits_auth(self, runner, monkeypatch):
        monkeypatch.setenv("ARCHIMEDES_EMAIL", "dan@example.com")
        monkeypatch.setenv("ARCHIMEDES_PASSWORD", "wrong")
        _install_transport(
            monkeypatch,
            _route({("POST", "/api/auth/sign-in/email"): httpx.Response(401, json={"error": "invalid credentials"})}),
        )
        result = runner.invoke(main, ["login", "--json"])
        assert result.exit_code == AUTH
        payload = json.loads(result.stdout)
        assert payload["ok"] is False
        assert payload["error"] == "invalid_credentials"
        assert not session_path().exists()

    def test_adversarial_missing_session_cookie_is_not_trusted(self, runner, monkeypatch):
        """A 200 with no Set-Cookie header must not be treated as a successful login —
        this is the input that should fail the 'a session actually exists' guard."""
        monkeypatch.setenv("ARCHIMEDES_EMAIL", "dan@example.com")
        monkeypatch.setenv("ARCHIMEDES_PASSWORD", "whatever")
        _install_transport(
            monkeypatch,
            _route({("POST", "/api/auth/sign-in/email"): httpx.Response(200, json={"redirect": False})}),
        )
        result = runner.invoke(main, ["login", "--json"])
        assert result.exit_code == AUTH
        assert json.loads(result.stdout)["error"] == "no_session_cookie"
        assert not session_path().exists()

    def test_adversarial_cookie_that_does_not_round_trip_is_not_trusted(self, runner, monkeypatch):
        """The other adversarial case: sign-in returns a cookie, but GET
        /api/auth/get-session with that exact cookie says nobody's signed in (Better
        Auth's actual empty-session shape — see `_user_from_payload` in
        `backend/archimedes/api/account_auth.py`). A naive implementation would cache
        the cookie anyway because sign-in "succeeded"."""
        monkeypatch.setenv("ARCHIMEDES_EMAIL", "dan@example.com")
        monkeypatch.setenv("ARCHIMEDES_PASSWORD", "whatever")
        _install_transport(
            monkeypatch,
            _route(
                {
                    ("POST", "/api/auth/sign-in/email"): httpx.Response(
                        200,
                        json={"redirect": False},
                        headers=[("set-cookie", f"{SESSION_COOKIE_NAME}=stale; Path=/")],
                    ),
                    ("GET", "/api/auth/get-session"): httpx.Response(200, json={"user": None, "session": None}),
                }
            ),
        )
        result = runner.invoke(main, ["login", "--json"])
        assert result.exit_code == AUTH
        assert json.loads(result.stdout)["error"] == "session_not_confirmed"
        assert not session_path().exists()

    def test_adversarial_non_json_round_trip_response_does_not_crash(self, runner, monkeypatch):
        """A 200 with a non-JSON body from get-session (proxy hiccup, HTML error page)
        must fail the same clean way as a confirmed-empty session, not raise an
        uncaught exception out of the command."""
        monkeypatch.setenv("ARCHIMEDES_EMAIL", "dan@example.com")
        monkeypatch.setenv("ARCHIMEDES_PASSWORD", "whatever")
        _install_transport(
            monkeypatch,
            _route(
                {
                    ("POST", "/api/auth/sign-in/email"): httpx.Response(
                        200,
                        json={"redirect": False},
                        headers=[("set-cookie", f"{SESSION_COOKIE_NAME}=stale; Path=/")],
                    ),
                    ("GET", "/api/auth/get-session"): httpx.Response(200, content=b"<html>not json</html>"),
                }
            ),
        )
        result = runner.invoke(main, ["login", "--json"], catch_exceptions=False)
        assert result.exit_code == AUTH
        assert json.loads(result.stdout)["error"] == "session_not_confirmed"

    def test_network_error_is_reported_not_raised(self, runner, monkeypatch):
        def factory(api_url, *, cookies=None):
            def blow_up(_request):
                raise httpx.ConnectError("connection refused")

            return httpx.Client(base_url=api_url, cookies=cookies, transport=httpx.MockTransport(blow_up))

        monkeypatch.setattr(cli_module, "_http_client", factory)
        monkeypatch.setenv("ARCHIMEDES_EMAIL", "dan@example.com")
        monkeypatch.setenv("ARCHIMEDES_PASSWORD", "whatever")

        result = runner.invoke(main, ["login", "--json"], catch_exceptions=False)
        assert result.exit_code == AUTH
        assert json.loads(result.stdout)["error"] == "network_error"


# ── meter ────────────────────────────────────────────────────────────


class TestMeter:
    def test_no_session_exits_auth_without_touching_the_network(self, runner, monkeypatch):
        _forbid_network(monkeypatch)
        result = runner.invoke(main, ["meter", "--json"])
        assert result.exit_code == AUTH
        assert isinstance(result.exception, SystemExit)  # not the network-guard AssertionError
        payload = json.loads(result.stdout)
        assert payload == {"ok": False, "command": "meter", "error": "no_session", "message": payload["message"]}

    def test_json_passthrough_on_success(self, runner, monkeypatch):
        _seed_session(cookie_value="tok-xyz")
        _install_transport(monkeypatch, _route({("GET", "/api/account/usage"): httpx.Response(200, json=_USAGE_BODY)}))
        result = runner.invoke(main, ["meter", "--json"])
        assert result.exit_code == OK
        assert json.loads(result.stdout) == {"ok": True, **_USAGE_BODY}

    def test_sends_the_cached_session_cookie(self, runner, monkeypatch):
        _seed_session(cookie_value="tok-xyz")
        seen = {}

        def handler(request):
            seen["cookie"] = request.headers.get("cookie", "")
            return httpx.Response(200, json=_USAGE_BODY)

        _install_transport(monkeypatch, handler)
        result = runner.invoke(main, ["meter", "--json"])
        assert result.exit_code == OK
        assert f"{SESSION_COOKIE_NAME}=tok-xyz" in seen["cookie"]

    def test_sends_a_secure_prefixed_cached_cookie_under_its_own_name(self, runner, monkeypatch):
        """A session cached from a production login (`__Secure-`-prefixed) must reach the
        wire under that same name — nothing downstream of `load_session` may special-case
        or rewrite the bare name, because rewriting it is exactly what would make prod
        reject the request."""
        save_session(
            api_url="https://archimedes-arc.com",
            cookies={SECURE_SESSION_COOKIE_NAME: "tok-secure-xyz"},
            email="dan@example.com",
        )
        seen = {}

        def handler(request):
            seen["cookie"] = request.headers.get("cookie", "")
            return httpx.Response(200, json=_USAGE_BODY)

        _install_transport(monkeypatch, handler)
        result = runner.invoke(main, ["meter", "--json"])
        assert result.exit_code == OK
        assert f"{SECURE_SESSION_COOKIE_NAME}=tok-secure-xyz" in seen["cookie"]

    def test_human_table_renders_caps_and_price(self, runner, monkeypatch):
        _seed_session()
        _install_transport(monkeypatch, _route({("GET", "/api/account/usage"): httpx.Response(200, json=_USAGE_BODY)}))
        result = runner.invoke(main, ["meter"])
        assert result.exit_code == OK
        assert "3/10 used" in result.stdout
        assert "0/20 used" in result.stdout
        assert "$0.05" in result.stdout

    def test_quota_backend_unavailable_renders_honestly_not_as_zero(self, runner, monkeypatch):
        """The repo's fail-soft rule, at the CLI layer: an outage must read as
        "unavailable", never as a plausible-looking 0/cap."""
        _seed_session()
        _install_transport(
            monkeypatch, _route({("GET", "/api/account/usage"): httpx.Response(200, json=_USAGE_BODY_QUOTA_DOWN)})
        )
        result = runner.invoke(main, ["meter"])
        assert result.exit_code == OK
        assert "unavailable (quota_backend_unavailable)" in result.stdout
        assert "None/10" not in result.stdout
        assert "0/10 used" not in result.stdout  # would misreport the outage as "idle"

    def test_expired_session_exits_auth(self, runner, monkeypatch):
        _seed_session()
        _install_transport(
            monkeypatch,
            _route({("GET", "/api/account/usage"): httpx.Response(401, json={"detail": "Authentication required"})}),
        )
        result = runner.invoke(main, ["meter", "--json"])
        assert result.exit_code == AUTH
        assert json.loads(result.stdout)["error"] == "session_expired"


# ── verify ───────────────────────────────────────────────────────────


class TestVerify:
    def test_passing_series_exits_0_and_renders_all_four_checks(self, runner, monkeypatch):
        _seed_session()
        _install_transport(
            monkeypatch, _route({("POST", "/api/rigor/verify"): httpx.Response(200, json=_VERIFY_PASS_BODY)})
        )
        result = runner.invoke(main, ["verify", "returns.csv"])
        assert result.exit_code == OK
        assert "[PASS] DSR" in result.stdout
        assert "[PASS] OOS consistency" in result.stdout
        assert "[N/A] PBO" in result.stdout
        assert "[N/A] Look-ahead" in result.stdout
        # #1409 round-4 review fix: the human-readable output must disclose
        # WHICH risk-free-rate convention produced the DSR/OOS numbers above —
        # not just the raw `--json` body, which always carried it silently.
        assert "rf_convention=excess_tbill_series" in result.stdout
        # Never an unqualified "PASSES" (#1481): two of the four gate legs cannot
        # run on a bare returns series, so the verdict is reported as capped.
        assert "PASSES (capped" in result.stdout
        assert "legs evaluated: 2/2 runnable here of 4 in the full gate" in result.stdout

    def test_json_on_a_lookahead_free_strong_oos_series_exits_0(self, runner, monkeypatch):
        """The issue's literal acceptance check, second half: `--json` on a series with
        look-ahead-free strong OOS exits 0."""
        _seed_session()
        _install_transport(
            monkeypatch, _route({("POST", "/api/rigor/verify"): httpx.Response(200, json=_VERIFY_PASS_BODY)})
        )
        result = runner.invoke(main, ["verify", "returns.csv", "--json"])
        assert result.exit_code == OK
        payload = json.loads(result.stdout)
        assert payload["passes"] is True
        assert payload["oos_consistency"]["status"] == "pass"
        assert payload["oos_consistency"]["oos_sharpe"] > 0.0

    def test_failing_series_exits_1(self, runner, monkeypatch):
        _seed_session()
        _install_transport(
            monkeypatch, _route({("POST", "/api/rigor/verify"): httpx.Response(200, json=_VERIFY_FAIL_BODY)})
        )
        result = runner.invoke(main, ["verify", "returns.csv", "--json"])
        assert result.exit_code == GATE_FAILED
        payload = json.loads(result.stdout)
        assert payload["ok"] is True  # the REQUEST succeeded; the verdict is what failed
        assert payload["passes"] is False
        assert payload["pbo"]["status"] == "not_evaluable"  # issue's literal acceptance check

    def test_not_evaluable_rendering_when_nothing_could_run(self, runner, monkeypatch):
        """Every check not_evaluable must render as such (never a silent pass).

        Exit code changed deliberately in #1481: this used to exit GATE_FAILED(1).
        But `exits.py` reserves 1 for "the gate ran and the answer was no" — a real
        verdict about the strategy — and a 3-bar series produced no verdict at all.
        Collapsing it into 1 reported "too few bars" as "strategy rejected", which
        is the exact confusion that module's docstring warns against. It now exits
        INCOMPLETE(4)."""
        _seed_session()
        _install_transport(
            monkeypatch, _route({("POST", "/api/rigor/verify"): httpx.Response(200, json=_VERIFY_NOT_EVALUABLE_BODY)})
        )
        result = runner.invoke(main, ["verify", "returns.csv"])
        assert result.exit_code == INCOMPLETE
        assert result.stdout.count("[N/A]") == 4
        assert "[PASS]" not in result.stdout
        assert "[FAIL]" not in result.stdout
        # #1409: the rf convention is disclosed on every verdict path, including
        # the ones that produce no verdict at all.
        assert "rf_convention=excess_flat_fallback" in result.stdout
        assert "INCOMPLETE" in result.stdout
        assert "PASSES" not in result.stdout

    def test_partially_evaluated_series_exits_incomplete_not_ok(self, runner, monkeypatch):
        """#1481 REGRESSION, CLI half. A 4-bar series where DSR passed but the OOS
        split could not run is one leg of four — `archimedes verify && deploy` used
        to succeed on it. It must not exit OK, and must not print PASSES."""
        _seed_session()
        body = {
            **_VERIFY_PASS_BODY,
            "passes": False,
            "n_bars": 4,
            "legs_evaluated": 1,
            "legs_runnable": 2,
            "legs_not_run": ["pbo", "oos_consistency", "look_ahead"],
            "oos_consistency": {
                "status": "not_evaluable",
                "reason": "insufficient data for a walk-forward OOS split",
            },
        }
        _install_transport(monkeypatch, _route({("POST", "/api/rigor/verify"): httpx.Response(200, json=body)}))
        result = runner.invoke(main, ["verify", "returns.csv"])
        assert result.exit_code == INCOMPLETE
        assert "PASSES" not in result.stdout
        assert "INCOMPLETE" in result.stdout

    def test_server_without_quorum_fields_fails_closed(self, runner, monkeypatch):
        """Defence in depth: `--api-url` can point at a host predating the #1481 fix,
        which returns `passes: true` on a partial evaluation and carries no `legs_*`
        fields at all. An unreported quorum is an unproven one, so the CLI must not
        exit OK on it — it re-derives the quorum from the leg statuses and fails
        closed."""
        _seed_session()
        legacy = {k: v for k, v in _VERIFY_PASS_BODY.items() if not k.startswith("legs_")}
        legacy.pop("verdict_capped", None)
        legacy["passes"] = True
        legacy["n_bars"] = 4
        legacy["oos_consistency"] = {"status": "not_evaluable", "reason": "too short"}
        _install_transport(monkeypatch, _route({("POST", "/api/rigor/verify"): httpx.Response(200, json=legacy)}))
        result = runner.invoke(main, ["verify", "returns.csv"])
        assert result.exit_code == INCOMPLETE
        assert "PASSES" not in result.stdout

    def test_trials_option_is_sent_to_the_api(self, runner, monkeypatch):
        _seed_session()
        seen = {}

        def handler(request):
            seen["body"] = json.loads(request.content)
            return httpx.Response(200, json=_VERIFY_PASS_BODY)

        _install_transport(monkeypatch, handler)
        result = runner.invoke(main, ["verify", "returns.csv", "--trials", "50"])
        assert result.exit_code == OK
        assert seen["body"]["trials"] == 50
        assert len(seen["body"]["returns"]) == 3  # the 3 data rows in the runner fixture's CSV

    def test_header_row_is_skipped_not_sent_as_a_data_point(self, runner, monkeypatch):
        seen = {}

        def handler(request):
            seen["body"] = json.loads(request.content)
            return httpx.Response(200, json=_VERIFY_PASS_BODY)

        _seed_session()
        _install_transport(monkeypatch, handler)
        result = runner.invoke(main, ["verify", "returns.csv"])
        assert result.exit_code == OK
        dates = [row["date"] for row in seen["body"]["returns"]]
        assert "date" not in dates
        assert dates == ["2026-01-02", "2026-01-03", "2026-01-04"]

    def test_stdin_source_is_parsed_and_sent(self, runner, monkeypatch):
        seen = {}

        def handler(request):
            seen["body"] = json.loads(request.content)
            return httpx.Response(200, json=_VERIFY_PASS_BODY)

        _seed_session()
        _install_transport(monkeypatch, handler)
        result = runner.invoke(main, ["verify", "-"], input="2026-02-01,0.01\n2026-02-02,-0.004\n")
        assert result.exit_code == OK
        assert len(seen["body"]["returns"]) == 2

    def test_adversarial_trials_zero_rejected_before_any_network_call(self, runner, monkeypatch):
        """The guard: --trials must be >= 1 (a self-attested trial count of 0 makes no
        sense for a deflation). Demonstrated failing, and failing WITHOUT spending a
        rate-limited API call."""
        _seed_session()
        _forbid_network(monkeypatch)
        result = runner.invoke(main, ["verify", "returns.csv", "--trials", "0", "--json"])
        assert result.exit_code == USAGE
        assert isinstance(result.exception, SystemExit)
        payload = json.loads(result.stdout)
        assert payload["error"] == "invalid_trials"

    def test_adversarial_empty_returns_rejected_before_any_network_call(self, runner, monkeypatch, tmp_path):
        """A CSV with a header and nothing else parses to zero rows — must be caught
        client-side (the API also 422s on this, but the guard should fire first)."""
        (tmp_path / "header_only.csv").write_text("date,daily_return\n")
        _seed_session()
        _forbid_network(monkeypatch)
        result = runner.invoke(main, ["verify", "header_only.csv", "--json"])
        assert result.exit_code == USAGE
        assert isinstance(result.exception, SystemExit)
        assert json.loads(result.stdout)["error"] == "empty_returns"

    def test_adversarial_trials_over_the_cap_rejected_before_any_network_call(self, runner, monkeypatch):
        """#1803: `trials` had no upper bound, and `trials=10**18` drove the DSR
        deflation to -inf — a caller-controlled way to turn a FAIL into
        `not_evaluable`. Shown failing, and failing without spending a call."""
        _seed_session()
        _forbid_network(monkeypatch)
        result = runner.invoke(main, ["verify", "returns.csv", "--trials", str(10**18), "--json"])
        assert result.exit_code == USAGE
        payload = json.loads(result.stdout)
        assert payload["error"] == "invalid_trials"
        # The SERVER's code for the identical refusal rides along, so a caller
        # branches on one string whichever side caught it.
        assert payload["reason"] == "trials_out_of_range"

    @pytest.mark.parametrize(
        "reason",
        [
            "invalid_date",
            "duplicate_date",
            "unsorted_dates",
            "non_finite",
            "out_of_range",
            "too_short",
            "too_many_rows",
            "trials_out_of_range",
        ],
    )
    def test_every_input_rejection_code_is_surfaced_verbatim(self, runner, monkeypatch, reason):
        """#1803: a 422 from `/api/rigor/verify` carries a machine-readable
        `reason`. It must reach `--json` as `reason` (a CI job branches on it)
        and reach a human as the SERVER's own sentence — never flattened into
        the generic "invalid_request" the CLI used to print for every 422."""
        _seed_session()
        body = {
            "detail": {
                "error": "input_rejected",
                "reason": reason,
                "reasons": [reason],
                "message": f"server sentence for {reason}",
                "loc": ["body", "returns"],
            }
        }
        _install_transport(monkeypatch, _route({("POST", "/api/rigor/verify"): httpx.Response(422, json=body)}))
        result = runner.invoke(main, ["verify", "returns.csv", "--json"])
        assert result.exit_code == USAGE
        payload = json.loads(result.stdout)
        assert payload["error"] == "input_rejected"
        assert payload["reason"] == reason
        assert f"server sentence for {reason}" in payload["message"]

    def test_input_rejection_prints_the_server_sentence_and_a_remedy(self, runner, monkeypatch):
        """The human path: the server says what was wrong, the CLI adds what to
        change. The shuffle refusal is the one that most needs explaining."""
        _seed_session()
        body = {
            "detail": {
                "error": "input_rejected",
                "reason": "unsorted_dates",
                "reasons": ["unsorted_dates"],
                "message": "returns must be in ascending date order; row 11 (2024-01-11) precedes row 10 ...",
                "loc": ["body", "returns"],
            }
        }
        _install_transport(monkeypatch, _route({("POST", "/api/rigor/verify"): httpx.Response(422, json=body)}))
        result = runner.invoke(main, ["verify", "returns.csv"])
        assert result.exit_code == USAGE
        assert "unsorted_dates" in result.stderr
        assert "ascending date order" in result.stderr
        assert "Sort the CSV by date" in result.stderr

    def test_an_uncoded_422_still_takes_the_generic_path(self, runner, monkeypatch):
        """Only the endpoint's OWN codes get the new envelope. Anything else —
        a shape error, an older server — must keep rendering as it did."""
        _seed_session()
        _install_transport(
            monkeypatch,
            _route(
                {
                    ("POST", "/api/rigor/verify"): httpx.Response(
                        422, json={"detail": [{"type": "missing", "loc": ["body", "returns"], "msg": "Field required"}]}
                    )
                }
            ),
        )
        result = runner.invoke(main, ["verify", "returns.csv", "--json"])
        assert result.exit_code == USAGE
        payload = json.loads(result.stdout)
        assert payload["error"] == "invalid_request"
        assert "reason" not in payload

    def test_no_session_exits_auth_without_touching_the_network(self, runner, monkeypatch):
        _forbid_network(monkeypatch)
        result = runner.invoke(main, ["verify", "returns.csv", "--json"])
        assert result.exit_code == AUTH
        assert isinstance(result.exception, SystemExit)
        assert json.loads(result.stdout)["error"] == "no_session"

    def test_rate_limited_exits_usage_not_gate_failed(self, runner, monkeypatch):
        """A 429 is not a verdict about the strategy — it must not be conflated with
        exit 1 (GATE_FAILED)."""
        _seed_session()
        _install_transport(
            monkeypatch,
            _route({("POST", "/api/rigor/verify"): httpx.Response(429, json={"detail": "rate limited"})}),
        )
        result = runner.invoke(main, ["verify", "returns.csv", "--json"])
        assert result.exit_code == USAGE
        assert result.exit_code != GATE_FAILED
        assert json.loads(result.stdout)["error"] == "rate_limited"

    def test_expired_session_exits_auth(self, runner, monkeypatch):
        _seed_session()
        _install_transport(
            monkeypatch,
            _route({("POST", "/api/rigor/verify"): httpx.Response(401, json={"detail": "Authentication required"})}),
        )
        result = runner.invoke(main, ["verify", "returns.csv", "--json"])
        assert result.exit_code == AUTH
        assert json.loads(result.stdout)["error"] == "session_expired"

    def test_local_flag_is_still_not_implemented_even_with_a_session(self, runner, monkeypatch):
        _seed_session()
        _forbid_network(monkeypatch)
        result = runner.invoke(main, ["verify", "--local", "returns.csv", "--json"])
        assert result.exit_code == NOT_IMPLEMENTED
        assert json.loads(result.stdout)["error"] == "not_implemented"
