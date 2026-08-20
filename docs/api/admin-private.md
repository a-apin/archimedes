# Admin Metrics API

> **status:** current
> **owner:** Dan Browne
> **updated:** 2026-08-20

`/api/metrics/private/*` — the internal cost/ops dashboard: Bedrock/infra spend (currently
draft placeholders) and the per-wallet identity roster that the public, PII-free `GET
/api/metrics` deliberately does not expose. Landed in #1373 (closing #1366) after a
full-tree audit found `GET /api/metrics/wallets` and `GET /api/metrics/wallets/connections`
serving the complete per-wallet address list to **anonymous** callers in production — a
per-identity roster is not aggregate traction data. Those two routes were removed from the
public router entirely (the old paths now `404`, they do not redirect) and re-mounted here
behind an admin gate.

**Auth model — the platform-admin gate.** Every route in this router shares one dependency
chain, applied at the router level
(`metrics_private_router = APIRouter(..., dependencies=[Depends(require_platform_admin)])`),
so the same check order runs before any handler code executes:

1. **Is there a session at all?** `require_platform_admin` depends on `require_linked_wallet`,
   which first checks for a live Better Auth session. No session → **`401`**
   "Authentication required".
2. **Does that account have a verified linked wallet?** A session with no linked wallet →
   **`403`** "A verified linked wallet is required" (raised inside `require_linked_wallet`
   itself, `wallet_routes.py`).
3. **Is that wallet a platform admin?** `require_platform_admin` then checks the linked
   wallet (lowercased) against `PLATFORM_ADMIN_WALLETS` — a comma/whitespace-separated env
   allowlist, parsed case-insensitively, empty by default. Not listed → **`403`** "Admin
   access required."

So in practice: **anonymous → 401; any signed-in-but-unlinked or signed-in-and-linked-but-
non-admin caller → 403; only a session whose linked wallet is on the allowlist → 200.**
Both 403 branches share the status code but not the message — the exact detail string
tells you which of the two you hit. `PLATFORM_ADMIN_WALLETS` grants **no fund / custody /
treasury authority** — it is a read gate on this dashboard (plus one narrow publish
exception for example strategies, `wallet_can_publish` in `models/strategy_generators.py`),
nothing more; a listed wallet still has to sign in and link normally like any other user
(`.env.example`).

The wallet-roster routes were moved here, not merely re-gated, specifically because a
per-wallet address list is individually identity-bearing and permanently linkable to
on-chain activity — the module docstring is explicit that "any linked wallet is not an
appropriate bar for it," which is why the gate is `PLATFORM_ADMIN_WALLETS` membership and
not merely "has a linked wallet."

---

### GET /api/metrics/private/cost
Account + admin cost/ops dashboard. | **Auth**: platform-admin | **Flags**:
`PLATFORM_ADMIN_WALLETS` env allowlist

Request: none.
Response: `{source: "draft", real_users: int, bedrock_monthly_usd: null,
bedrock_daily_usd: null, infra_monthly_usd: null, cost_per_user_usd: null,
cost_per_generation_usd: null, note: str, authenticated_wallet: str, timestamp: str}`.
Errors: `401` — no session. `403` — session without a linked wallet, or a linked wallet
not on the admin allowlist (see the two-flavor 403 note above).

Every cost field is an explicit `null` placeholder — **not live-metered spend**. The live
AWS Cost Explorer + Bedrock token-metering wiring is roadmap work, not yet built; only
`real_users` (canonical Better Auth account count) is read live, so any per-user math a
consumer does downstream is anchored to an honest denominator rather than the cumulative
request tallies on the public `GET /api/metrics` (issue #830).

```bash
curl -sS -b /tmp/session.jar http://localhost:8080/api/metrics/private/cost
```

### GET /api/metrics/private/wallets
Enumerate legacy verified human wallets. | **Auth**: platform-admin | **Flags**:
`PLATFORM_ADMIN_WALLETS` env allowlist

Request: none.
Response: `{real_users: int, wallets: [{wallet_address, actor_class, first_seen_at,
last_auth_at}], timestamp: str}` — `real_users` here means the wallet count on *this*
endpoint (kept for issue #1028 AC1 response-shape compatibility), not the canonical
account count; don't conflate it with `/private/cost`'s `real_users`.
Errors: `401` / `403` — same admin-gate semantics as `/cost`, above.

Fail-safe: an empty list / zero on a DB read error, never a `500`.

```bash
curl -sS -b /tmp/session.jar http://localhost:8080/api/metrics/private/wallets
```

### GET /api/metrics/private/wallets/connections
"Which wallets connected, and when." | **Auth**: platform-admin | **Flags**:
`PLATFORM_ADMIN_WALLETS` env allowlist

Request: none.
Response: `{count: int, connections: [{wallet: str, connected_at: str}], timestamp: str}`
— one row per wallet, `connected_at = min(occurred_at)` over that wallet's
`identity_events` rows where `event_type='auth_verified'`, earliest first.
Errors: `401` / `403` — same admin-gate semantics as `/cost`, above.

This query was impossible before the identity ledger existed: the pre-Better-Auth
SIWE-verify path discarded the wallet into a stateless cookie with no durable write
(issue #1028 AC2). Fail-safe: an empty list on a DB read error.

```bash
curl -sS -b /tmp/session.jar http://localhost:8080/api/metrics/private/wallets/connections
```

---

**No admin UI today.** `ui/src/components/Insights.jsx` explicitly does *not* render this
dashboard — its own header comment names `GET /api/metrics/private/cost` as "a SEPARATE,
account + admin-linked-wallet-gated surface ... intentionally NOT rendered anywhere in this
component." A repo-wide search finds no other frontend consumer of `/api/metrics/private/*`
either. That is a UI gap, not an access gap: the API is fully live and enforced today, a
caller just has to reach it directly (curl / an API client with a session cookie) rather
than through app navigation.

See also: `.env.example` (`PLATFORM_ADMIN_WALLETS` documentation block) and
`docs/security/auth-model.md`.
