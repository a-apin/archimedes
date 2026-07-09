#!/usr/bin/env python3
"""Agent journey harness — drive the Archimedes user journey programmatically (#788).

Archimedes has exactly one interface today: a passkey/wallet React SPA. That means
AI agents — the "new citizens" of the Agora thesis — literally cannot use the
product. This script is the first slice of the **agent-facing path**: it exercises
the SAME journey a human does (land → read library → generate → read the rigor
verdict) over the public ``/api/*`` surface, with **zero browser or wallet**.

Two payoffs, immediately:
  1. **Dogfooding / testing force-multiplier** — run this against prod and every
     bug or rough edge it surfaces is one a human would also hit. File them.
  2. **Proves + measures the agent-user segment** — this client sends an explicit
     agent User-Agent, so the telemetry classifier counts it as an external agent
     and the conversion funnel (#787) attributes its ``generation_started``.

Slice 2 adds **agent-auth**: a programmatic EOA signs the SIWE (EIP-4361)
challenge from ``GET /api/auth/nonce`` and posts it to ``POST /api/auth/verify``,
establishing the same session cookie the browser gets. With
``REQUIRE_SIWE_FOR_GENERATION`` now defaulting ON server-side, the GENERATE path
(and the per-job stream/candidates reads, which are owner-scoped under the gate)
requires this session; READ stays public.

This revision completes slice 2 with **DEPLOY + MONITOR**: once the generated
winner reads live-gate ``deployable``, ``step_deploy`` calls the wallet-gated
``POST /api/vaults/create`` (the backend's own signer pays gas; the SIWE wallet
only becomes the vault's ``Ownable`` owner) and ``step_monitor`` reads the vault
back via ``GET /api/vaults/{address}/health``. **``--deploy`` is a dry run by
default** — see its help text for why (the contract suite was T3.2-redeployed
2026-07-09, #588 is still open, and no vault had yet been created against the
new deployment by anyone, agent or human, as of this revision).

Key handling: the private key comes ONLY from the ``AGENT_WALLET_KEY`` env var
(never a CLI arg, never printed/logged), or ``--ephemeral`` mints a throwaway
in-memory key via ``eth_account.Account.create()``. Use a fresh TESTNET dev key —
never a funded/mainnet key. (Deploying via ``POST /api/vaults/create`` needs no
funding on the agent wallet itself — see ``step_deploy``'s docstring.)

Usage:
    python scripts/agent_journey.py --base https://archimedes-arc.com
    AGENT_WALLET_KEY=0x... python scripts/agent_journey.py            # auth auto-on
    python scripts/agent_journey.py --ephemeral                       # throwaway wallet
    python scripts/agent_journey.py --no-auth --read-only             # anon read smoke
    python scripts/agent_journey.py --base http://localhost:8000 --intent "low-vol momentum"
    python scripts/agent_journey.py --ephemeral --deploy              # full journey incl. real deploy
    python scripts/agent_journey.py --no-auth --read-only --monitor-address 0x...  # health check only

Exit code is nonzero if a hard step fails, so this doubles as a journey smoke test.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import UTC, datetime
from urllib.parse import urlsplit

import httpx

# Identify as an agent so telemetry classifies us as an external agent (not a
# browser) — this is the point: we ARE the agent-user segment we want to capture.
AGENT_UA = "archimedes-agent-journey/0.1 (+https://github.com/a-apin/archimedes/issues/788)"

# Terminal SSE event names from api/generate_schemas.py::EventName.
_TERMINAL_EVENTS = {"done", "error"}

# SIWE constants — must match the backend bindings (api/auth_siwe.py): the
# verifier REQUIRES domain + chain-id + fresh Issued At, and rejects mismatches.
ARC_CHAIN_ID = 5042002  # Arc testnet (0x4cef52)
_SESSION_COOKIE = "archimedes_session"
_KEY_ENV = "AGENT_WALLET_KEY"  # the ONLY source for a persistent key — never a CLI arg


def build_siwe_message(
    domain: str,
    wallet: str,
    nonce: str,
    issued_at: str,
    chain_id: int = ARC_CHAIN_ID,
    statement: str = "Sign in to Archimedes.",
) -> str:
    """Build an EIP-4361 (SIWE) message the backend verifier accepts.

    Mirrors backend/tests/test_auth_siwe.py::test_verify_with_valid_signature —
    the reference programmatic recipe. The backend binds on: the domain in the
    first line (must equal its PUBLIC_DOMAIN), ``Chain ID:`` (must be Arc's
    5042002), ``Nonce:`` (live, single-use), and ``Issued At:`` (fresh within
    5 min). The wallet is the first 42-char 0x line.
    """
    return (
        f"{domain} wants you to sign in with your Ethereum account:\n"
        f"{wallet}\n\n"
        f"{statement}\n\n"
        f"URI: https://{domain}\n"
        f"Version: 1\n"
        f"Chain ID: {chain_id}\n"
        f"Nonce: {nonce}\n"
        f"Issued At: {issued_at}"
    )


def _domain_from_base(base: str) -> str:
    """Bare authority from a --base URL (scheme/path stripped) — SIWE domain shape."""
    parts = urlsplit(base if "://" in base else f"https://{base}")
    return (parts.netloc or parts.path).rstrip("/")


def step_auth(client: httpx.Client, base: str, *, ephemeral: bool) -> str | None:
    """SIWE sign-in with a programmatic EOA. Returns the wallet address, or None.

    nonce + issued_at come from ``GET /api/auth/nonce``; the domain is the one
    the server advertises in that same response (its PUBLIC_DOMAIN — the value
    the verifier binds on), falling back to the --base host if absent. Using the
    server-advertised domain keeps the harness working against localhost dev,
    where PUBLIC_DOMAIN and the --base host differ; the server still enforces
    every binding, so nothing is weakened client-side.
    """
    from eth_account import Account
    from eth_account.messages import encode_defunct

    _hr("AUTH — SIWE (EIP-4361) with a programmatic EOA")

    if ephemeral:
        acct = Account.create()
        print("  · ephemeral wallet (throwaway key, in-memory only)")
    else:
        key = os.environ.get(_KEY_ENV, "").strip()
        if not key:
            print(f"  ✗ {_KEY_ENV} not set (and --ephemeral not given) — cannot authenticate")
            return None
        try:
            acct = Account.from_key(key)
        except (ValueError, TypeError):
            # Never echo the key material — not even in the error path.
            print(f"  ✗ {_KEY_ENV} is not a valid private key")
            return None
    wallet = acct.address
    print(f"  · wallet address: {wallet}")

    nonce_data = _get(client, "/api/auth/nonce")
    if not isinstance(nonce_data, dict) or "nonce" not in nonce_data:
        print("  ✗ /api/auth/nonce — unusable response; cannot authenticate")
        return None
    domain = nonce_data.get("domain") or _domain_from_base(base)
    issued_at = datetime.fromtimestamp(int(nonce_data.get("issued_at", time.time())), tz=UTC).isoformat()

    message = build_siwe_message(domain=domain, wallet=wallet.lower(), nonce=nonce_data["nonce"], issued_at=issued_at)
    signed = acct.sign_message(encode_defunct(text=message))

    try:
        r = client.post("/api/auth/verify", json={"message": message, "signature": signed.signature.hex()})
    except httpx.HTTPError as exc:
        print(f"  ✗ POST /api/auth/verify — transport error: {exc}")
        return None
    if r.status_code != 200:
        print(f"  ✗ POST /api/auth/verify — HTTP {r.status_code}: {r.text[:200]}")
        return None

    # The session cookie is flagged Secure; over a plain-http --base (local dev)
    # the stdlib cookie policy would store but never SEND it. Re-set it as a
    # plain client cookie so the authenticated journey works on http too. The
    # server is untouched — this is purely harness-side cookie plumbing.
    if urlsplit(base).scheme == "http":
        host = (urlsplit(base).hostname or "").lower()
        if host not in {"localhost", "127.0.0.1", "::1"}:
            # Downgrading a Secure cookie over non-local plain HTTP would send
            # the session token in cleartext across a real network — refuse.
            print(f"  ✗ refusing to send the session cookie over http to non-local host {host!r}; use https")
            return None
        token = r.cookies.get(_SESSION_COOKIE)
        if token:
            client.cookies.set(_SESSION_COOKIE, token)

    session = _get(client, "/api/auth/session")
    authenticated = isinstance(session, dict) and session.get("authenticated") is True
    print(f"  ✓ authenticated as {r.json().get('wallet')}  (session check: {authenticated})")
    if not authenticated:
        print("  ✗ session cookie did not round-trip — continuing unauthenticated")
        return None
    return wallet


def _hr(title: str) -> None:
    print(f"\n{'─' * 4} {title} {'─' * (60 - len(title))}")


def _get(client: httpx.Client, path: str, *, required: bool = False) -> object | None:
    """GET a JSON path; print a one-line result. Returns parsed JSON or None."""
    try:
        r = client.get(path)
    except httpx.HTTPError as exc:
        print(f"  ✗ GET {path} — transport error: {exc}")
        if required:
            raise SystemExit(2) from exc
        return None
    if r.status_code != 200:
        print(f"  ✗ GET {path} — HTTP {r.status_code}")
        if required:
            raise SystemExit(2)
        return None
    try:
        return r.json()
    except ValueError:
        # A 200 with a non-JSON body (e.g. an nginx/proxy HTML page) is a hard
        # failure for a required surface like /health — don't let it slip through
        # as a soft None and continue the journey on misleading output. (Copilot #790)
        print(f"  ✗ GET {path} — 200 but non-JSON body")
        if required:
            raise SystemExit(2) from None
        return None


def step_read(client: httpx.Client) -> None:
    """Read-only surfaces: health, traction, funnel, the strategy library."""
    _hr("READ — public surfaces (no auth)")

    health = _get(client, "/health", required=True)
    if isinstance(health, dict):
        print(f"  ✓ /health — status={health.get('status')!r}")

    metrics = _get(client, "/api/metrics")
    if isinstance(metrics, dict):
        print(f"  ✓ /api/metrics — humans={metrics.get('human_count')} agents={metrics.get('agent_count')}")

    funnel = _get(client, "/api/metrics/funnel")
    if isinstance(funnel, dict):
        stages = funnel.get("stages", [])
        summary = ", ".join(f"{s['stage']}={s['distinct_visitors']}" for s in stages)
        print(f"  ✓ /api/metrics/funnel [{funnel.get('window')}] — {summary}")
    else:
        print("  · /api/metrics/funnel — unavailable; skipping")

    strategies = _get(client, "/api/strategies/")
    if isinstance(strategies, list):
        print(f"  ✓ /api/strategies/ — {len(strategies)} strategies in the library")
    elif isinstance(strategies, dict):
        items = strategies.get("strategies") or strategies.get("items") or []
        print(f"  ✓ /api/strategies/ — {len(items)} strategies in the library")


def step_generate(client: httpx.Client, intent: str, risk: str, timeout_s: float) -> str | None:
    """Start a generation, tail the SSE stream, return the job_id (or None on failure).

    Timeout tradeoff (reference harness): the SSE read timeout is disabled (``read=None``)
    so a slow generation that writes infrequently isn't killed mid-stream; the ``deadline``
    is enforced per-line inside the loop. The known gap: if the backend accepts the
    connection but never writes a *single* byte, ``iter_lines()`` blocks and the per-line
    deadline check never runs, so the harness can hang until the process is killed. Acceptable
    for a dogfooding harness; a production client should add a wall-clock watchdog / cancel.
    """
    _hr("GENERATE — start + stream (session cookie if authenticated)")

    body = {
        "brief": {"intent": intent, "risk_appetite": risk, "max_papers": 5},
        "n_candidates": 1,
    }
    try:
        r = client.post("/api/generate/start", json=body)
    except httpx.HTTPError as exc:
        print(f"  ✗ POST /api/generate/start — transport error: {exc}")
        return None
    if r.status_code not in (200, 202):
        print(f"  ✗ POST /api/generate/start — HTTP {r.status_code}: {r.text[:200]}")
        return None

    # Don't assume a well-formed JSON body with the expected keys — a non-JSON
    # body or an error shape behind a 200/202 should produce a clean failure +
    # message, not a traceback (the whole point of the harness). (Copilot #790)
    try:
        start = r.json()
        job_id = start["job_id"]
        stream_url = start["stream_url"]
    except (ValueError, KeyError, TypeError) as exc:
        print(f"  ✗ POST /api/generate/start — unexpected response shape ({exc}): {r.text[:200]}")
        return None
    print(f"  ✓ job_id={job_id}  stream={stream_url}")

    _hr("STREAM — live reasoning events")
    deadline = time.monotonic() + timeout_s
    seen = 0
    terminal: str | None = None
    # Disable the httpx READ timeout for the SSE stream: the backend only writes
    # when a new event arrives, so a long quiet gap (a slow LLM/tool call) would
    # trip a ReadTimeout even though the overall journey isn't over. We bound the
    # stream with the explicit `deadline` check inside the loop instead. Keep a
    # SHORT, finite connect/write/pool timeout so a dead endpoint still fails fast
    # (read=None alone would otherwise inherit the full generation timeout on the
    # connect phase too — the opposite of fail-fast). (Copilot, #790)
    stream_timeout = httpx.Timeout(read=None, connect=10.0, write=10.0, pool=10.0)
    try:
        with client.stream("GET", stream_url, timeout=stream_timeout) as resp:
            if resp.status_code != 200:
                print(f"  ✗ stream — HTTP {resp.status_code}")
                return job_id
            event_name = None
            for line in resp.iter_lines():
                if time.monotonic() > deadline:
                    print("  · stream timeout reached; moving on")
                    break
                if not line:
                    continue
                if line.startswith("event:"):
                    event_name = line.split(":", 1)[1].strip()
                elif line.startswith("data:"):
                    seen += 1
                    payload = line.split(":", 1)[1].strip()
                    label = event_name or "?"
                    snippet = payload[:110] + ("…" if len(payload) > 110 else "")
                    print(f"  • {label:<20} {snippet}")
                    if event_name in _TERMINAL_EVENTS:
                        terminal = event_name
                        break
    except httpx.HTTPError as exc:
        print(f"  ✗ stream — transport error: {exc}")
        return job_id

    print(f"  → {seen} events; terminal={terminal!r}")
    return job_id


def step_readback(client: httpx.Client, job_id: str) -> dict:
    """Read the externalized rigor verdict + considered alternatives for the job.

    Returns ``{"read_ok": bool, "winner": {...} | None}``. ``winner`` (when the
    selected candidate resolved to a persisted strategy) carries the fields
    ``step_deploy`` needs: ``strategy_id``, ``strategy_name``,
    ``rigor_gate_status``, ``deployable``.
    """
    _hr("RIGOR — externalized verdict + considered alternatives")
    data = _get(client, f"/api/generate/jobs/{job_id}/candidates")
    if not isinstance(data, dict):
        print("  ✗ could not read candidates")
        return {"read_ok": False, "winner": None}
    candidates = data.get("candidates", [])
    best_id = data.get("best_candidate_id")
    if not candidates:
        print("  · no candidates yet (generation may still be running)")
        return {"read_ok": False, "winner": None}
    for c in candidates:
        marker = "🏆 WINNER" if c.get("selected") else "  rejected"
        passes = "PASS" if c.get("passes_rigor") else "FAIL"
        verdict = c.get("rigor_verdict") or {}
        keys = ", ".join(sorted(verdict.keys())[:6]) if isinstance(verdict, dict) else ""
        print(f"  {marker}  {c.get('strategy_name')!r}  rigor={passes}  [{keys}]")
    print(f"  → best_candidate_id={best_id!r}")

    # ── LIVE rigor gate — the SAME tri-state verdict a human sees on the passport ──
    # (#833 live_rigor_gate, compute-on-read from real persisted returns). After the
    # #788 returns-persistence fix, a fusion/debate winner with a real backtest reads
    # "pass"/"fail" + deployable, not the misleading "pending".
    _hr("LIVE GATE — deployability (read the persisted strategy passport)")
    saw_non_pending = False
    winner: dict | None = None
    for c in candidates:
        sid = c.get("strategy_id")
        if not sid:
            continue
        strat = _get(client, f"/api/strategies/{sid}")
        if not isinstance(strat, dict):
            print(f"  · /api/strategies/{sid} — unavailable")
            continue
        status = strat.get("rigor_gate_status")
        deployable = bool(strat.get("passes_rigor_gate"))
        if status and status != "pending":
            saw_non_pending = True
        print(
            f"  {'🏆' if c.get('selected') else '·'}  {c.get('strategy_name')!r}  "
            f"live_gate={status!r}  deployable={deployable}  sharpe={strat.get('sharpe_ratio')}"
        )
        if c.get("selected"):
            winner = {
                "strategy_id": sid,
                "strategy_name": c.get("strategy_name"),
                "rigor_gate_status": status,
                "deployable": deployable,
            }
    if not saw_non_pending:
        print(
            "  ⚠ all strategies read live_gate='pending' — generated candidates have no real "
            "persisted returns (the #788/#818 deployability gap)."
        )
    return {"read_ok": True, "winner": winner}


# ── DEPLOY + MONITOR (slice 2 continued) ───────────────────────────────────


def build_vault_create_payload(
    *,
    strategy_id: str,
    name: str,
    symbol: str,
    management_fee_bps: int = 0,
    performance_fee_bps: int = 0,
    agent_assisted: bool = True,
    strictness_level: int = 1,
) -> dict:
    """The exact JSON body ``POST /api/vaults/create`` (``VaultCreateRequest``)
    expects. Factored out of ``step_deploy`` so a hermetic test can prove this
    shape is accepted end-to-end by the real route (SIWE + server-side rigor
    gate + Pydantic binding) without needing an async ASGI context inside the
    (sync) reference client itself — see
    ``backend/tests/scripts/test_agent_journey_deploy.py``.
    """
    return {
        "name": name,
        "symbol": symbol,
        "management_fee_bps": management_fee_bps,
        "performance_fee_bps": performance_fee_bps,
        "agent_assisted": agent_assisted,
        "strategy_ids": [strategy_id],
        "strictness_level": strictness_level,
    }


def step_deploy(
    client: httpx.Client,
    winner: dict | None,
    *,
    execute: bool,
    vault_name: str,
    vault_symbol: str,
    management_fee_bps: int = 0,
    performance_fee_bps: int = 0,
    strictness_level: int = 1,
) -> str | None:
    """Deploy a vault from the winning generated strategy via ``POST /api/vaults/create``.

    Reuses the SAME authenticated ``client`` ``step_auth`` built — the endpoint is
    wallet-gated (``require_verified_wallet``), but note WHO pays gas: the
    backend's OWN signer creates the vault, then transfers ``Ownable`` ownership
    to the caller's SIWE wallet (see the route's docstring in
    ``backend/archimedes/api/vaults_routes.py::create_vault``). So the agent
    wallet itself needs no funding for this path — ``scripts/fund_agent_wallet.py``
    is for a DIFFERENT, not-yet-built path (an agent signing ``createVault``
    itself, the way the browser UI's ``CreateVaultModal`` does client-side).

    Refuses outright (never calls the endpoint) when the winning candidate isn't
    LIVE-GATE deployable — the harness must never spend gas on a call guaranteed
    to 422, and must never make it look like a pending/failing strategy can be
    forced through (#788 anti-goal: never weaken or bypass the rigor gate; the
    server enforces this independently via ``_assert_strategies_pass_rigor``
    regardless of what this client does).

    ``execute=False`` (the default wired from ``--deploy`` being absent) prints
    the exact payload and returns without sending anything — mirrors
    ``fund_agent_wallet.py``'s ``--execute`` convention for anything that spends
    real (even if testnet) value. Default OFF on purpose: as of this revision
    the contract suite was T3.2-redeployed 2026-07-09 (#1079), issue #588 (the
    redeploy keystone ``docs/agent-api.md`` gates contract-touching agent work
    on) is still open, and ``backend/tests/chain/test_create_vault_abi_consistency.py``
    itself documents that whether the repo's ABI matches the LIVE deployed
    bytecode is unverified pending #588. No vault — agent or human — had been
    created against the new deployment yet at the time this was written. Flip
    ``--deploy`` on once that's confirmed settled.
    """
    _hr("DEPLOY — create a vault from the generated strategy")

    if not winner:
        print("  · no winning candidate available — skipping deploy")
        return None
    if not winner.get("deployable"):
        print(
            f"  ✗ {winner.get('strategy_name')!r} is not deployable "
            f"(live_gate={winner.get('rigor_gate_status')!r}) — refusing to deploy. "
            "This harness never bypasses the rigor gate."
        )
        return None

    payload = build_vault_create_payload(
        strategy_id=winner["strategy_id"],
        name=vault_name,
        symbol=vault_symbol,
        management_fee_bps=management_fee_bps,
        performance_fee_bps=performance_fee_bps,
        strictness_level=strictness_level,
    )
    print(f"  · POST /api/vaults/create  {payload}")

    if not execute:
        print(
            "  · DRY RUN — nothing sent (default). The backend signer would spend real\n"
            "    testnet gas on this call. Re-run with --deploy to actually create it —\n"
            "    see step_deploy's docstring / docs/agent-api.md for why this defaults off."
        )
        return None

    try:
        r = client.post("/api/vaults/create", json=payload)
    except httpx.HTTPError as exc:
        print(f"  ✗ POST /api/vaults/create — transport error: {exc}")
        return None
    if r.status_code != 200:
        print(f"  ✗ POST /api/vaults/create — HTTP {r.status_code}: {r.text[:300]}")
        return None
    try:
        data = r.json()
        vault_address = data["vault_address"]
    except (ValueError, KeyError, TypeError) as exc:
        print(f"  ✗ POST /api/vaults/create — unexpected response shape ({exc}): {r.text[:200]}")
        return None
    print(f"  ✓ vault deployed at {vault_address}")
    return vault_address


def step_monitor(client: httpx.Client, vault_address: str) -> bool:
    """Read vault health back via ``GET /api/vaults/{address}/health``.

    Public — no auth required (matches the live route). Safe to call for ANY
    address, including one with no deployed contract behind it yet: the
    monitor reads off Redis-backed snapshot/heartbeat state keyed by address
    string, not a chain existence check, so an unknown address returns an
    (empty-ish) health snapshot rather than erroring — verified against prod.
    """
    _hr("MONITOR — vault health")
    health = _get(client, f"/api/vaults/{vault_address}/health")
    if not isinstance(health, dict):
        print("  ✗ could not read vault health")
        return False
    print(
        f"  ✓ agent_alive={health.get('agent_alive')}  "
        f"last_rebalance={health.get('last_rebalance')!r}  "
        f"aum_trend_pct={health.get('aum_trend_pct')}  "
        f"snapshots={health.get('snapshot_count')}"
    )
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description="Drive the Archimedes journey as an agent (#788).")
    ap.add_argument("--base", default="https://archimedes-arc.com", help="API base URL")
    ap.add_argument("--intent", default="diversified low-volatility strategy for idle USDC")
    ap.add_argument(
        "--risk",
        default="moderate",
        choices=["fixed_income", "conservative", "moderate", "aggressive", "hyper_risky"],
    )
    ap.add_argument("--read-only", action="store_true", help="skip generation (cheap smoke test)")
    ap.add_argument("--timeout", type=float, default=120.0, help="generation stream timeout (s)")
    ap.add_argument(
        "--auth",
        action=argparse.BooleanOptionalAction,
        default=None,
        help=f"SIWE sign-in with the EOA key from ${_KEY_ENV} (env only — never a CLI arg). "
        f"Default: on when ${_KEY_ENV} is set; --no-auth forces anonymous.",
    )
    ap.add_argument(
        "--ephemeral",
        action="store_true",
        help="mint a throwaway in-memory wallet (Account.create()) and SIWE-auth with it; implies --auth",
    )
    ap.add_argument(
        "--deploy",
        action="store_true",
        help="actually call POST /api/vaults/create for the generated winner (backend signer spends real "
        "testnet gas). Default is a DRY RUN that prints the exact payload and sends nothing — see "
        "step_deploy's docstring for why (contract suite T3.2-redeployed 2026-07-09, #588 still open, "
        "no vault yet created against it by anyone as of this revision).",
    )
    ap.add_argument(
        "--vault-name", default=None, help="vault name for --deploy (default: derived, 'Agent Journey <UTC timestamp>')"
    )
    ap.add_argument("--vault-symbol", default="AGTJRN", help="vault symbol for --deploy (<=16 chars, default AGTJRN)")
    ap.add_argument("--management-fee-bps", type=int, default=0, help="vault management fee, basis points (default 0)")
    ap.add_argument(
        "--performance-fee-bps", type=int, default=0, help="vault performance fee, basis points (default 0)"
    )
    ap.add_argument(
        "--strictness-level",
        type=int,
        default=1,
        choices=[1, 2, 3, 4, 5],
        help="rigor strictness for deploy (1=strictest/safest, matches the server default; never bypasses "
        "the always-on correctness floors)",
    )
    ap.add_argument(
        "--monitor-address",
        default=None,
        help="GET /api/vaults/{address}/health for an existing vault, independent of deploying one this run "
        "(no auth needed; safe read-only check)",
    )
    args = ap.parse_args()

    # Auth resolution: --ephemeral implies auth; otherwise --auth/--no-auth wins;
    # otherwise auto — on exactly when the key env var is present.
    want_auth = args.ephemeral or (args.auth if args.auth is not None else bool(os.environ.get(_KEY_ENV)))

    print(f"Archimedes agent journey → {args.base}")
    client = httpx.Client(base_url=args.base, headers={"User-Agent": AGENT_UA}, timeout=30.0, follow_redirects=True)
    wallet: str | None = None
    try:
        if want_auth:
            wallet = step_auth(client, args.base, ephemeral=args.ephemeral)
            if wallet is None and not args.read_only:
                # With the server's secure-by-default generation gate, an unauthenticated
                # generate would just 401 — fail loudly here instead.
                print("\nRESULT: SIWE auth failed and generation requires it — aborting.")
                return 2
        print(f"\n  auth status: {'authenticated as ' + wallet if wallet else 'anonymous'}")
        step_read(client)
        if args.read_only:
            if args.monitor_address:
                step_monitor(client, args.monitor_address)
            print("\n(read-only — skipping generation)")
            return 0
        job_id = step_generate(client, args.intent, args.risk, args.timeout)
        if not job_id:
            print("\nRESULT: generation failed to start — see above.")
            return 1
        readback = step_readback(client, job_id)
        ok = readback["read_ok"]
        print(f"\nRESULT: journey {'completed' if ok else 'partial (no verdict read)'} — job_id={job_id}")

        vault_address = step_deploy(
            client,
            readback.get("winner"),
            execute=args.deploy,
            vault_name=args.vault_name or f"Agent Journey {datetime.now(UTC):%Y-%m-%d %H:%M}",
            vault_symbol=args.vault_symbol,
            management_fee_bps=args.management_fee_bps,
            performance_fee_bps=args.performance_fee_bps,
            strictness_level=args.strictness_level,
        )
        monitor_target = vault_address or args.monitor_address
        if monitor_target:
            step_monitor(client, monitor_target)
        if vault_address:
            print(f"RESULT: vault deployed — {vault_address}")

        return 0 if ok else 1
    finally:
        client.close()


if __name__ == "__main__":
    sys.exit(main())
