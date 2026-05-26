# Authentication model — what's enforced, what's a known testnet gap

> **Audience:** anyone reviewing the API for security (judges, auditors,
> future contributors). Written 2026-05-27 to give an accurate picture after
> the SIWE + internal-agent-key hardening, and to scope the one residual gap
> honestly rather than leave it implicit.

## TL;DR

| Surface | Protection | Forgeable? |
| --- | --- | --- |
| PII reads (email, display_name) | **SIWE session only** (`get_verified_wallet`) | No — cryptographic |
| Trace publish (`POST /api/traces/publish`) | **Internal agent key** (`require_internal_agent_key`) | No |
| Rebalance / regime chat events | **Internal agent key** | No |
| AMM bootstrap | **Internal agent key** | No |
| **Non-PII profile/chat writes** | SIWE **or** `X-Wallet-Address` header fallback | **Yes — see gap below** |
| On-chain funds | Vault contract (agent has rebalance-only authority) | No — enforced on-chain |

## What is cryptographically enforced

1. **SIWE (Sign-In with Ethereum, EIP-4361).** [`api/auth_siwe.py`](../../backend/archimedes/api/auth_siwe.py)
   issues a challenge nonce, verifies the signed message with
   `Account.recover_message`, confirms the recovered address matches the
   claimed wallet, and sets a signed session cookie. This is the trust anchor
   for anything sensitive.

2. **PII reads are SIWE-only.** [`api/user_routes.py`](../../backend/archimedes/api/user_routes.py)
   gates email / display_name behind `_extract_caller_wallet_siwe()`, which
   reads **only** the verified SIWE session — never the header. A forged
   header cannot read another wallet's PII.

3. **Agent-authored writes require the internal agent key.** Trace publishing,
   rebalance/regime chat events, and AMM bootstrap all depend on
   `require_internal_agent_key` ([`api/auth_guard.py`](../../backend/archimedes/api/auth_guard.py)).
   A user **cannot** forge a reasoning trace or inject agent chat events — these
   are the integrity-critical surfaces, and they are not user-reachable.

   > This closes the most serious part of the earlier "anyone can forge
   > traces/chat" concern: the provenance-bearing writes are agent-key-gated.

## The one residual gap (known testnet limitation)

**Non-PII profile/chat *writes* still accept a forgeable `X-Wallet-Address`
header as a fallback** when no SIWE session is present
(`_extract_caller_wallet` in `user_routes.py`). The write path enforces
`caller == payload.wallet_address`, which blocks *mismatched* writes — but an
attacker can simply set both the header and the payload to a victim's address
and write to it.

**Blast radius is limited:**
- Only **non-PII** fields (display_name, interests, attribution,
  marketing_opt_in) and user chat messages — **no funds, no PII reads, no
  reasoning traces.**
- On-chain funds are unaffected: the vault contract grants the agent
  rebalance-only authority, with no withdraw-to-platform path, so no API-layer
  auth bug can move user money.

**Why the fallback still exists:** the live frontend lets a MetaMask user
interact (e.g. save a display name) before completing a SIWE signature. Removing
the header fallback outright would break that write path until the frontend
requires SIWE login first — a coordinated frontend + backend change, tracked as
a follow-up issue rather than a unilateral backend edit.

## The fix (tracked)

Require a verified SIWE session for **all** write operations — drop the
`X-Wallet-Address` header fallback in `_extract_caller_wallet` — paired with a
frontend change that prompts SIWE sign-in before the first write. Until then,
this gap is accepted for the testnet demo and documented here so it is not
mistaken for an oversight.
