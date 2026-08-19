"""Agent-discoverability manifest — GET /api/agent/manifest.

A small, unauthenticated, machine-readable summary of the agent-facing API contract
documented in full at ``docs/agent-api.md`` (and curated for low-token consumption at
``/llms.txt``). Intent: give an autonomous AI agent landing on the API surface with no
prior context a single cheap GET that says what this product is, how to authenticate,
and which endpoints matter.

Deliberately a SEPARATE module from ``agent_routes.py`` (``/api/agent/status`` etc.):
that file's "agent" is the autonomous on-chain trading/rebalancing agent (internal
system health). This file's "agent" is an external AI agent consuming the product as a
user. Same URL prefix (``/api/agent``), different concept — kept in separate modules so
neither docstring has to disambiguate the other's meaning inline.

Honesty note (mirrors docs/agent-api.md): READ / AUTH / GENERATE — including the rigor
readback surfaced under the generate group (there is no separate ``rigor`` group) — are live today.
DEPLOY, and the marketplace PUBLISH/SUBSCRIBE + MONITOR endpoints that depend on a
deployed vault, are real routes but not yet wired for agent-driven end-to-end use --
they land with the T3.2 contract redeploy (issue #588). Never advertise these as
complete; the ``status`` field on each group says so explicitly.
"""

from __future__ import annotations

from fastapi import APIRouter

agent_manifest_router = APIRouter(prefix="/api/agent", tags=["agent"])

_PENDING_T32 = "landing with the T3.2 contract redeploy (issue #588) — endpoint exists but agent-driven use isn't wired end-to-end yet"


@agent_manifest_router.get("/manifest")
async def get_agent_manifest():
    """Small JSON contract for programmatic discovery by AI agents.

    Unauthenticated, rate-limited like sibling public GETs (no decorator -> the
    limiter's default_limits apply, matching e.g. /api/config/contracts).
    """
    return {
        "name": "Archimedes",
        "blurb": (
            "Agentic trading, grounded in research — settled on Arc. Turns a "
            "natural-language investment intent into a research-grounded, "
            "rigor-gated portfolio strategy, executed in a non-custodial USDC "
            "vault on the Arc testnet (chain ID 5042002)."
        ),
        "docs": {
            "llms_txt": "/llms.txt",
            "agent_api": "https://github.com/a-apin/archimedes/blob/main/docs/agent-api.md",
            "agent_card": "/.well-known/agent.json",
        },
        "auth": {
            "scheme": "Better Auth session",
            "methods": ["emailPassword", "google", "github"],
            "description": (
                "Create or sign in to canonical account at /api/auth/* and retain session cookie. "
                "Provider availability is returned by GET /api/auth/providers. Wallet proof is a "
                "separate optional link under /api/wallets/* and never creates a session."
            ),
            "wallet_link_spec": "EIP-4361",
            "chain_id": 5042002,
        },
        "endpoints": {
            "read": {
                "status": "live",
                "auth_required": False,
                "routes": {
                    "strategies": "GET /api/strategies/",
                    "assets": "GET /api/explore/assets",
                    "health": "GET /api/health",
                },
            },
            "auth": {
                "status": "live",
                "auth_required": False,
                "routes": {
                    "providers": "GET /api/auth/providers",
                    "sign_up": "POST /api/auth/sign-up/email",
                    "sign_in": "POST /api/auth/sign-in/email",
                    "session": "GET /api/auth/get-session",
                    "sign_out": "POST /api/auth/sign-out",
                    "wallet_challenge": "POST /api/wallets/challenge",
                    "wallet_verify": "POST /api/wallets/verify",
                },
            },
            "generate": {
                "status": "live",
                "auth_required": True,
                "routes": {
                    "start": "POST /api/generate/start",
                    "stream": "GET /api/generate/stream/{job_id}",
                    "candidates": "GET /api/generate/jobs/{job_id}/candidates",
                },
            },
            "deploy": {
                "status": _PENDING_T32,
                "auth_required": True,
                "routes": {
                    "create_vault": "POST /api/vaults/create",
                },
            },
            "marketplace": {
                "status": _PENDING_T32,
                "auth_required": True,
                "routes": {
                    "publish": "POST /api/marketplace/publish",
                    "subscribe": "POST /api/marketplace/subscribe",
                },
            },
            "monitor": {
                "status": _PENDING_T32,
                "auth_required": False,
                "routes": {
                    "vault_health": "GET /api/vaults/{address}/health",
                },
            },
        },
        "faucet": {
            "url": "https://faucet.circle.com/",
            "description": "Free Arc testnet USDC (also used as gas on Arc). No real funds at risk.",
        },
    }
