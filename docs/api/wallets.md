# Wallet Linking API

> **status:** current
> **owner:** Dan Browne
> **updated:** 2026-08-20

`/api/wallets/*` — a verified EIP-4361 ("Sign-In with Ethereum" message format, repurposed
here as a **link** proof rather than a login proof) that ties an external wallet
(MetaMask, another EIP-1193 wallet, or a Circle Modular smart wallet) to the caller's
canonical Better Auth account. Linking a wallet never creates or replaces an account
session by itself — connecting a wallet is not how you log in; **you must already be
signed in (`POST /api/auth/sign-in/email` or OAuth — see `docs/api/auth-and-accounts.md`)
before you can link one.**

**Auth model.** Every route below requires a Better Auth account session
(`Depends(require_current_user)`, `backend/archimedes/api/account_auth.py`) — the same
`better-auth.session_token` cookie the auth sidecar issues, checked by FastAPI via that
sidecar's `GET /api/auth/get-session`. A verified link does **not** itself prove present
control of the wallet on a later request: `GET /api/wallets` and its neighbors read a
database row, not a fresh signature. Anything that moves funds or performs an irreversible
on-chain action needs its own fresh signature elsewhere in the API, never this cached link.

**The flow.**
1. `POST /api/wallets/challenge` — signed in, ask the server to mint a challenge for
   `{address, chain_id, provider}`. The server stores a SHA-256 hash of a random nonce
   bound to the caller's user ID, the normalized address, chain ID, domain, URI, provider,
   issue time, and a 5-minute expiry, and returns the exact EIP-4361 text to sign.
2. The wallet signs that message (MetaMask, a browser EIP-1193 provider, a Circle passkey
   wallet, or a raw key for a headless/agent caller — `provider: "headless"` exists
   specifically for the last case, #1293).
3. `POST /api/wallets/verify` — send back `{message, signature}`. The server re-derives the
   expected message from the stored challenge byte-for-byte, atomically marks the challenge
   consumed (a race loses with `409`), recovers the signer (EOA `secp256k1` first, falling
   through to an ERC-1271/ERC-6492 deployless `eth_call` against Arc RPC for smart wallets),
   and on success inserts (or returns) the `LinkedWallet` row.

The EIP-4361 challenge message (`_challenge_message` in `wallet_routes.py`) has this exact
shape:

```
<domain> wants you to sign in with your Ethereum account:
<checksummed-address>

Link this wallet to your authenticated Archimedes account.

URI: <site-url>
Version: 1
Chain ID: <chain_id>
Nonce: <32-hex-char nonce>
Issued At: <ISO-8601 UTC>
Expiration Time: <ISO-8601 UTC, issued + 5 min>
```

`domain`/`URI` resolve from `PUBLIC_DOMAIN`, falling back to `BETTER_AUTH_URL` — `503`
("Wallet linking is not configured") if neither is a valid `http(s)://host` URL. `chain_id`
must equal `ARC_CHAIN_ID` (env, default `5042002`) or the challenge is refused with `400`
("Unsupported wallet chain") before any message is even built. The normalized link
identity is `<chain_id>:<lowercase-address>` — unique per wallet-per-chain; a wallet
already linked to a *different* account returns `409`, and ownership is never transferred
automatically.

**Legacy-data claim (side effect, not a separate call).** A successful verify auto-claims
any pre-account (SIWE-era) `StrategyRecord` / `StrategyPassportRecord` /
`StrategyProposal` / `VaultMetadata` row still carrying that wallet address with a `NULL
owner_user_id` — and the first linked wallet on an account is automatically marked
`is_primary`. `GET /api/wallets/check` lets the UI ask ahead of time, for a candidate
address, whether linking it would actually claim anything — without yet proving control of
that address (it answers only a boolean; counts stay behind the signature proof).

---

### GET /api/wallets
List the caller's own linked wallets. | **Auth**: account-session

Request: none.
Response: `list[{id, address, display_address, chain_id, provider, is_primary,
verified_at}]` — `provider ∈ {metamask, browser, circle, headless}`, recorded as
provenance only (never a permission grant).
Errors: `401` — no session.

```bash
curl -sS -b /tmp/session.jar http://localhost:8080/api/wallets
```

### GET /api/wallets/check
Would linking `address` reclaim pre-account (SIWE-era) data for me? | **Auth**:
account-session

Request: query `address: str` (`^0x[a-fA-F0-9]{40}$`).
Response: `{has_legacy_data: bool}`.
Errors: `401` — no session. `422` — `address` fails the hex-address pattern.

```bash
curl -sS -b /tmp/session.jar \
  "http://localhost:8080/api/wallets/check?address=0x1234567890123456789012345678901234567890"
```

### POST /api/wallets/challenge
Issue an EIP-4361 wallet-link challenge to sign. | **Auth**: account-session | **Flags**:
`chain_id` must equal `ARC_CHAIN_ID` (env, default `5042002`)

Request: JSON body `{address: "0x"+40hex, chain_id: int>0, provider:
"metamask"|"browser"|"circle"|"headless", circle_wallet_id?: str (only valid with
provider="circle")}`.
Response: `{message: str, expires_at: datetime}` — the exact text (see the flow above) to
hand to the wallet for signing; valid 5 minutes.
Errors:
- `400` "Unsupported wallet chain" — `chain_id` isn't the supported Arc chain.
- `503` "Wallet linking is not configured" — `PUBLIC_DOMAIN`/`BETTER_AUTH_URL` unset or
  malformed.
- `422` — body validation (bad address pattern, `circle_wallet_id` set with a
  non-`circle` provider).
- `401` — no session.

```bash
curl -sS -b /tmp/session.jar -X POST http://localhost:8080/api/wallets/challenge \
  -H 'Content-Type: application/json' \
  -d '{"address":"0x1234567890123456789012345678901234567890","chain_id":5042002,"provider":"metamask"}'
```

### POST /api/wallets/verify
Verify the signed challenge and link the wallet to the account. | **Auth**:
account-session | **Flags**: EOA secp256k1 recovery first, ERC-1271/ERC-6492 smart-wallet
fallback second (deployless `eth_call` against Arc RPC)

Request: JSON body `{message: str (1-4096 chars), signature: str (1-8192 chars)}` —
`message` must be byte-for-byte the text `POST /api/wallets/challenge` returned.
Response: `{id, address, display_address, chain_id, provider, is_primary, verified_at}` —
the newly linked (or, if you already own this exact `<chain_id>:<address>`, the existing)
wallet row.
Errors:
- `401` "Wallet challenge is invalid or expired" — no session, no matching challenge, or
  challenge expired.
- `401` "Wallet proof does not match its challenge" — the signed message doesn't
  byte-for-byte match the stored challenge (checked twice: raw text equality, then a
  field-by-field re-derivation).
- `401` "Invalid wallet signature" — both EOA recovery and ERC-1271/ERC-6492 verification
  failed.
- `409` "Wallet challenge was already used" — race-condition guard, checked twice (an
  atomic update-count check, then an `IntegrityError` fallback).
- `409` "Wallet is already linked to another account" — the normalized `<chain_id>:<address>`
  identity already belongs to a different user; ownership is never transferred
  automatically. (A same-user re-verify of an already-linked wallet is idempotent, not an
  error.) A rarer race path (concurrent link attempts hitting the `IntegrityError`
  fallback) returns the same `409` with the shorter message "Wallet is already linked".
- `422` — body validation (message/signature length bounds).

```bash
curl -sS -b /tmp/session.jar -X POST http://localhost:8080/api/wallets/verify \
  -H 'Content-Type: application/json' \
  -d '{"message":"<exact challenge text from /challenge>","signature":"0x..."}'
```

### POST /api/wallets/{wallet_id}/primary
Set a linked wallet as the account's primary wallet. | **Auth**: account-session

Request: path `wallet_id: str`.
Response: `{id, address, display_address, chain_id, provider, is_primary, verified_at}`
(now `is_primary: true`).
Errors: `401` — no session. `404` "Wallet not found" — `wallet_id` not found for this user.

```bash
curl -sS -b /tmp/session.jar -X POST http://localhost:8080/api/wallets/wallet-abc123/primary
```

### DELETE /api/wallets/{wallet_id}
Unlink a wallet from the account. | **Auth**: account-session

Request: path `wallet_id: str`.
Response: HTTP `204 No Content`.
Errors:
- `401` — no session.
- `404` "Wallet not found" — `wallet_id` not found for this user.
- `409` "Wallet backs existing Archimedes data and cannot be unlinked" — refused whenever
  the wallet still owns a `StrategyRecord`, `StrategyPassportRecord`, `StrategyProposal`,
  `VaultMetadata`, or `UserProfile` row.

```bash
curl -sS -b /tmp/session.jar -X DELETE http://localhost:8080/api/wallets/wallet-abc123
```

---

See also: `docs/account-authentication.md` (§ Wallet linking, topology, migration and
rollback) and `docs/security/auth-model.md` (§ Wallet proof, trust boundaries).
