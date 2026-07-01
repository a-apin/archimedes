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

The READ + GENERATE paths need NO signing (``REQUIRE_SIWE_FOR_GENERATION`` is off),
so this slice runs today. DEPLOY + MONITOR (which need a programmatic signer, since
agents can't do browser passkeys) are slice 2 — see issue #788.

Usage:
    python scripts/agent_journey.py --base https://archimedes-arc.com
    python scripts/agent_journey.py --base http://localhost:8000 --intent "low-vol momentum"
    python scripts/agent_journey.py --read-only          # skip generation (cheap smoke)

Exit code is nonzero if a hard step fails, so this doubles as a journey smoke test.
"""

from __future__ import annotations

import argparse
import sys
import time

import httpx

# Identify as an agent so telemetry classifies us as an external agent (not a
# browser) — this is the point: we ARE the agent-user segment we want to capture.
AGENT_UA = "archimedes-agent-journey/0.1 (+https://github.com/a-apin/archimedes/issues/788)"

# Terminal SSE event names from api/generate_schemas.py::EventName.
_TERMINAL_EVENTS = {"done", "error"}


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
    _hr("GENERATE — start + stream (no wallet)")

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


def step_readback(client: httpx.Client, job_id: str) -> bool:
    """Read the externalized rigor verdict + considered alternatives for the job."""
    _hr("RIGOR — externalized verdict + considered alternatives")
    data = _get(client, f"/api/generate/jobs/{job_id}/candidates")
    if not isinstance(data, dict):
        print("  ✗ could not read candidates")
        return False
    candidates = data.get("candidates", [])
    best_id = data.get("best_candidate_id")
    if not candidates:
        print("  · no candidates yet (generation may still be running)")
        return False
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
    if not saw_non_pending:
        print(
            "  ⚠ all strategies read live_gate='pending' — generated candidates have no real "
            "persisted returns (the #788/#818 deployability gap)."
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
    args = ap.parse_args()

    print(f"Archimedes agent journey → {args.base}")
    client = httpx.Client(base_url=args.base, headers={"User-Agent": AGENT_UA}, timeout=30.0, follow_redirects=True)
    try:
        step_read(client)
        if args.read_only:
            print("\n(read-only — skipping generation)")
            return 0
        job_id = step_generate(client, args.intent, args.risk, args.timeout)
        if not job_id:
            print("\nRESULT: generation failed to start — see above.")
            return 1
        ok = step_readback(client, job_id)
        print(f"\nRESULT: journey {'completed' if ok else 'partial (no verdict read)'} — job_id={job_id}")
        return 0 if ok else 1
    finally:
        client.close()


if __name__ == "__main__":
    sys.exit(main())
