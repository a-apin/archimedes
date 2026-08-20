"""The CLI's machine-readable contract — ``archimedes manifest``.

Agentic connectivity is the interface (Ricardo's skill-store brief, applied):
an agent can only invoke what it can understand, and it should understand this
tool through a declarative contract, not by parsing ``--help`` prose. Every
command declares its inputs, output contract, exit codes, cost class, and
whether it is implemented at all.

This dict is hand-written but it is NOT allowed to drift: a test walks the
real click command tree and asserts every command and every flag here matches
it (``cli/tests/test_cli.py``, the manifest-sync tests) — the promise is
checked against the truth by CI, per house convention.
"""

from __future__ import annotations

from . import __version__

EXIT_CODES = {
    "0": "OK — command succeeded (for `verify`: the gate PASSED)",
    "1": "GATE_FAILED — `verify` produced a verdict and it fails the gate",
    "2": "USAGE/AUTH — bad invocation, or no/expired session (run `archimedes login`)",
    "3": "NOT_IMPLEMENTED — the subcommand has no implementation in this version",
}

MANIFEST: dict = {
    "tool": "archimedes",
    "version": __version__,
    "json_contract": (
        "--json makes every code path, including errors, emit exactly one JSON "
        "object on stdout; a script never parses prose."
    ),
    "exit_codes": EXIT_CODES,
    "commands": {
        "login": {
            "implemented": True,
            "description": "Authenticate with an Archimedes account and cache the session cookie.",
            "inputs": {
                "--api-url": {"env": "ARCHIMEDES_API_URL", "default": "https://archimedes-arc.com"},
                "--json": {"flag": True},
                "email": {"env": "ARCHIMEDES_EMAIL", "prompted_if_absent": True},
                "password": {"env": "ARCHIMEDES_PASSWORD", "prompted_if_absent": True, "hidden": True},
            },
            "output": {"ok": "bool", "email": "str"},
            "cost_class": "network: 2 HTTP calls (sign-in + session round-trip); no funds, no chain",
            "side_effects": "writes ~/.config/archimedes/session.json (mode 600)",
        },
        "meter": {
            "implemented": True,
            "description": "Today's generation usage vs both daily caps, plus the live price quote.",
            "inputs": {
                "--api-url": {
                    "env": "ARCHIMEDES_API_URL",
                    "default": "the cached session's URL, else https://archimedes-arc.com",
                },
                "--json": {"flag": True},
            },
            "output": {
                "user": "{used: int|null, cap: int} — null used means the quota backend was unavailable, never a fabricated 0",
                "ip": "{used: int|null, cap: int}",
                "quote": "the literal GET /api/generate/quote payload",
            },
            "cost_class": "network: 1 HTTP call; requires session; no funds, no chain",
            "side_effects": "none",
        },
        "verify": {
            "implemented": True,
            "description": "Run the rigor gate's evaluable checks over a returns CSV (or '-' for stdin).",
            "inputs": {
                "RETURNS_CSV": {"positional": True, "format": "two columns: date, daily_return; '-' reads stdin"},
                "--trials": {"default": 1, "min": 1, "meaning": "self-attested trial count deflating the DSR"},
                "--local": {"flag": True, "implemented": False},
                "--api-url": {
                    "env": "ARCHIMEDES_API_URL",
                    "default": "the cached session's URL, else https://archimedes-arc.com",
                },
                "--json": {"flag": True},
            },
            "output": {
                "passes": "bool — no evaluable check failed AND at least one was evaluable",
                "dsr": "{status: pass|fail|not_evaluable, deflated_sharpe, dsr_p_value, reason}",
                "oos_consistency": "{status, oos_sharpe, in_sample_sharpe, reason}",
                "pbo": "{status: always not_evaluable for a bare series — needs a trial matrix, reason names the decisive gap}",
                "look_ahead": "{status: always not_evaluable — needs strategy source; never uploaded}",
            },
            "cost_class": "network: 1 HTTP call; requires session; rate-limited 5/minute; free",
            "side_effects": "none",
            "honesty": (
                "checks the endpoint cannot honestly compute are reported not_evaluable "
                "with the decisive reason — never silently passed, failed, or defaulted"
            ),
        },
        "backtest": {
            "implemented": False,
            "lands_in": "unscheduled",
            "description": "Local-only backtest of a strategy file (never uploaded).",
        },
        "manifest": {
            "implemented": True,
            "description": "This contract. Always JSON, always exit 0.",
            "inputs": {},
            "output": "this document",
            "cost_class": "local, no network",
            "side_effects": "none",
        },
    },
}
