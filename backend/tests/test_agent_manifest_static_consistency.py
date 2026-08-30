"""The static discovery document and the served manifest must agree (#1448).

Two surfaces describe the same API to an agent:

- ``ui/public/.well-known/agent.json`` — the static discovery document, fetched
  straight off the CDN at a well-known path;
- ``GET /api/agent/manifest`` — the served manifest, built in
  ``api/agent_manifest_routes.py``.

They drifted. PR #1447 corrected the served manifest's ``deploy`` /
``marketplace`` / ``monitor`` groups from "landing with the T3.2 redeploy
(#588)" to ``live``, because the redeploy landed 2026-07-09 and #588 closed
2026-07-14. The static file kept asserting the stale state in seven places, so
an agent that discovered us the well-known way was told three live capabilities
were unavailable — for over a month.

Nothing connected the two files, so nothing noticed. This test is that
connection: the drift is the defect, not either value on its own.

Statuses only. Route KEYS deliberately differ between the surfaces — the static
document is camelCase (``createVault``) and the served one is snake_case
(``create_vault``) — and forcing those to match would be a different, larger
change than keeping the two honest about what works.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

REPO_ROOT = Path(__file__).resolve().parents[2]
STATIC_MANIFEST = REPO_ROOT / "ui" / "public" / ".well-known" / "agent.json"


def _static_statuses() -> dict[str, str]:
    doc = json.loads(STATIC_MANIFEST.read_text(encoding="utf-8"))
    return {
        name: group["status"]
        for name, group in doc["endpoints"].items()
        if isinstance(group, dict) and "status" in group
    }


async def _served_statuses() -> dict[str, str]:
    from archimedes.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/agent/manifest")
    assert resp.status_code == 200, resp.text
    return {
        name: group["status"]
        for name, group in resp.json()["endpoints"].items()
        if isinstance(group, dict) and "status" in group
    }


async def test_both_surfaces_describe_the_same_capability_groups():
    """A group on one surface and not the other is drift too, not just a status gap."""
    static, served = _static_statuses(), await _served_statuses()
    assert set(static) == set(served), (
        f"only in the static document: {sorted(set(static) - set(served))}; "
        f"only in the served manifest: {sorted(set(served) - set(static))}"
    )


async def test_the_two_manifests_agree_on_every_group_status():
    static, served = _static_statuses(), await _served_statuses()
    disagreements = {
        name: (static[name], served[name]) for name in set(static) & set(served) if static[name] != served[name]
    }
    assert not disagreements, (
        "the static discovery document and the served manifest disagree about what works:\n"
        + "\n".join(
            f"  {name}: .well-known/agent.json says {s!r}, /api/agent/manifest says {v!r}"
            for name, (s, v) in sorted(disagreements.items())
        )
        + "\nAn agent's answer would depend on which surface it happened to read."
    )


async def test_the_comparison_actually_covers_the_groups_that_drifted():
    """Guard on the guard: an empty or tiny comparison would pass vacuously."""
    static = _static_statuses()
    assert len(static) >= 8, f"only {len(static)} static groups parsed — the reader is broken"
    for group in ("deploy", "marketplace", "monitor", "paper"):
        assert group in static, f"{group} not compared — it is one of the groups that drifted"


@pytest.mark.parametrize("group", ["deploy", "marketplace", "monitor"])
def test_the_groups_that_were_stale_are_not_still_advertised_as_pending(group: str):
    """The specific false claim, pinned by value.

    Distinct from the agreement test above on purpose: that one would also pass
    if BOTH surfaces regressed to the stale string together.
    """
    status = _static_statuses()[group]
    assert "588" not in status, f"{group} still cites #588, which closed 2026-07-14"
    assert status == "live", f"{group} advertises {status!r}"
