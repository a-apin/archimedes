# Admin Metrics API

> **status:** current
> **owner:** Dan Browne
> **updated:** 2026-08-20

`/api/metrics/private/*` — the internal cost/ops dashboard: Bedrock/infra spend (currently
draft placeholders), the current-schema engagement/adoption dashboard-v2 tiles, the
admin-gate probe the frontend uses, and the per-wallet identity roster that the public,
PII-free `GET /api/metrics` deliberately does not expose. Landed in #1373 (closing #1366)
after a full-tree audit found `GET /api/metrics/wallets` and `GET
/api/metrics/wallets/connections` serving the complete per-wallet address list to
**anonymous** callers in production — a per-identity roster is not aggregate traction data.
Those two routes were removed from the public router entirely (the old paths now `404`,
they do not redirect) and re-mounted here behind an admin gate.

**Owner directive (2026-08-20, supersedes issue #1028 D8 "public Insights page"):**
`/app/insights` moved from the public app surface to ADMIN-ONLY
(`PLATFORM_ADMIN_WALLETS` holders). `GET /whoami` below is the server-truth gate the
frontend probes on entry; `GET /engagement` is the new dashboard-v2 content. See
`ui/src/adminProbe.js`, `ui/src/App.jsx`, and `ui/src/components/Insights.jsx`.

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

### GET /api/metrics/private/whoami
Admin-gate probe. | **Auth**: platform-admin | **Flags**: `PLATFORM_ADMIN_WALLETS` env
allowlist

Request: none.
Response: `{admin: true, wallet: str}` — always `admin: true` on `200`; there is no
`admin: false` 200 response, non-admin is always a `401`/`403` (see below).
Errors: `401` — no session. `403` — session without a linked wallet, or a linked wallet not
on the admin allowlist (same two-flavor 403 as `/cost`, above).

The frontend calls this on entry to `/app/insights`, before rendering anything
Insights-shaped, and to decide whether the Ops nav item renders at all
(`ui/src/adminProbe.js`, cached ~30s and shared across both call sites). A denied probe
renders the identical "page not found" treatment an unknown route gets
(`ui/src/components/NotFound.jsx`) — the page does not exist for a non-admin/anonymous
visitor, not merely "access denied", so it is never advertised.

```bash
curl -sS -b /tmp/session.jar http://localhost:8080/api/metrics/private/whoami
```

### GET /api/metrics/private/engagement
Dashboard-v2 engagement/adoption tiles. | **Auth**: platform-admin | **Flags**:
`PLATFORM_ADMIN_WALLETS` env allowlist

Request: none.
Response:
```
{
  accounts: {total: int, new_7d: int, new_30d: int},
  linked_wallets: {total: int},
  strategies: {total: int, new_7d: int, daily_new: [{date: "YYYY-MM-DD", count: int}, ...7]},
  generation_costs: {measured_count: int, total_input_tokens: int, total_output_tokens: int,
                      total_tokens: int},
  paper_deployments: {active: int, stopped: int},
  repeat_generation_users: {generating_users: int, repeat_users: int, note: str},
  payments: {dry_run: bool, settled_volume_usd: null, note: str},
  authenticated_wallet: str,
  timestamp: str,
}
```
Errors: `401` / `403` — same admin-gate semantics as `/cost`, above.

Every field is a real query against an existing table (`services/engagement_metrics.py`) —
no sampling, no estimation. `repeat_generation_users` is scoped to `strategy_store` rows
with a non-NULL `owner_user_id` (the real FK to `auth_users.id`); pre-account, wallet-only
generations are excluded from both `generating_users` and `repeat_users` rather than
silently rounded into either bucket. `payments.settled_volume_usd` is always `null` today —
`PAYMENTS_DRY_RUN` gates every settlement path, so there is no durable settled-volume
record for this endpoint to read; it is the real field name settlement wiring will
eventually populate, not a placeholder `0`. Fail-safe per sub-metric: a DB error on any one
tile degrades that tile to its own zero shape without blanking the rest of the response.

```bash
curl -sS -b /tmp/session.jar http://localhost:8080/api/metrics/private/engagement
```

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

**Admin UI (2026-08-20).** `ui/src/components/Insights.jsx` is now the admin-only
`/app/insights` page's content — but the gate itself lives one level up, in
`ui/src/App.jsx`: it probes `GET /whoami` before Insights.jsx ever mounts, and only
renders the component once that probe resolves `admin === true`. Insights.jsx itself never
imports the probe; once mounted it renders `GET /engagement`'s tiles.
`GET /api/metrics/private/cost` is still deliberately NOT rendered anywhere in that
component — its own header comment explains why: every field is a draft placeholder
pending the live AWS Cost Explorer + Bedrock token-metering wiring (roadmap), not something
this page should present as a real number. `/wallets` and `/wallets/connections` also have
no frontend consumer yet — reach them directly (curl / an API client with a session
cookie).

See also: `.env.example` (`PLATFORM_ADMIN_WALLETS` documentation block) and
`docs/security/auth-model.md`.
