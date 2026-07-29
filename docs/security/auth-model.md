# Authentication and authorization model

> Audience: API security reviewers and contributors. Updated 2026-07-28.

## Trust boundaries

| Surface | Protection |
| --- | --- |
| Account/session | Better Auth email/password or configured OAuth provider |
| PII/profile reads and writes | Better Auth session; row scoped by canonical user ID |
| Strategy generation and job reads | Better Auth session; jobs scoped by canonical user ID |
| Wallet linking | Better Auth session plus short-lived, single-use EIP-4361 proof |
| Wallet-attributed chat and on-chain actions | Better Auth session plus wallet linked to same user |
| Trace publish, rebalance/regime events, AMM bootstrap | Internal agent key |
| On-chain funds | Vault contract ownership and agent-role constraints |

## Canonical identity

Better Auth `auth_users.id` is application identity. FastAPI forwards request cookie to
colocated auth service `/api/auth/get-session`; successful result is stored as immutable
request state and authorization dependencies scope database access by user ID.

Wallet address headers and wallet connection state never create or replace account
session. Circle wallet passkeys authorize Circle wallets only. They are not Better Auth
credentials.

## Wallet proof

`POST /api/wallets/challenge` creates exact EIP-4361 message bound to authenticated user,
normalized `<chain-id>:<lowercase-address>`, domain, URI, provider, nonce hash, issue time,
and five-minute expiry. `POST /api/wallets/verify` atomically consumes challenge before
link insertion, preventing replay across workers.

Verifier supports EOA recovery and ERC-6492/EIP-1271 smart-wallet signatures. Link identity
is unique. Existing wallet owned by another user returns 409; ownership is never moved
automatically. Matching unowned legacy rows may be claimed only after valid proof.

Selected wallet headers are hints only: backend accepts them only when address and chain
resolve to current user's verified link. Sensitive account routes need no wallet.

## Service credentials

Integrity-critical agent writes depend on `require_internal_agent_key` in
[`api/auth_guard.py`](../../backend/archimedes/api/auth_guard.py). User sessions cannot
forge reasoning traces or internal rebalance events.

Production Better Auth requires `BETTER_AUTH_SECRET` with at least 32 characters from SSM.
FastAPI-to-auth calls use private ECS loopback; public auth routes pass through nginx.
Cookies remain HttpOnly and Secure in production. Secrets must never enter logs or Git.

## Defense in depth

nginx rejects anonymous `/app/*` requests through `auth_request`, UI repeats route guard,
and FastAPI independently protects private APIs. Quant feature gate is server-owned and
returns 404 when disabled. Same-origin checks, trusted origins, exact wallet-message
bindings, database uniqueness, and no-existence-oracle 404s protect cross-user boundaries.

See [`../account-authentication.md`](../account-authentication.md) for topology, rollout,
and rollback details.
