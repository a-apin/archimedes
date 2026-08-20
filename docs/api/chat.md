# Chat API

Per-vault chat for the Archimedes marketplace. Reads are deliberately public
(anyone can follow a vault's chat), while writes require an authenticated
account with a verified linked wallet; the AI (Archimedes) auto-responds when
a message mentions `@archimedes`. Two additional endpoints post system events
(rebalance, regime change) into a vault's chat feed from the internal agent
runner — they are never reachable from the browser UI.

**Auth model.** `anonymous` needs nothing. `linked-wallet` requires both a
Better Auth account session (`better-auth.session_token` cookie) **and** a
wallet verified-linked to that account (`require_linked_wallet`) — a session
without a linked wallet is rejected, and a body-supplied `wallet_address` is
checked against the server-resolved linked wallet but can never override it.
`internal-key` requires a matching `X-Internal-Agent-Key` header, compared
with `hmac.compare_digest` against the `INTERNAL_AGENT_API_KEY` env var; if
that env var is unset the gate fails closed and rejects every caller. Examples
needing a session assume an authenticated cookie jar at `/tmp/session.jar`.

## Endpoints

### GET /api/vaults/{address}/chat
Get chat messages for a vault, oldest-first (chat display order). | **Auth**:
anonymous

Request: path `address`; query `limit: int(1..200)=50, before_id: int|null` (pagination cursor — messages before this ID).
Response (`ChatMessageListResponse`): `{messages: [ChatMessageResponse{id, vault_address, wallet_address, message, is_ai: bool, verified: bool, created_at}], total: int, has_more: bool}`.
Errors: none explicit.

```bash
curl -s "https://archimedes-arc.com/api/vaults/<address>/chat?limit=50"
```

### POST /api/vaults/{address}/chat
Post as a verified wallet linked to the canonical account; AI auto-responds to
`@archimedes` mentions. | **Auth**: linked-wallet | **Flags**: rate limit
`20/minute` (disabled under `TESTING`)

Request (`ChatPostRequest`): `{wallet_address: str|null, message: str}` — `wallet_address`, if given, must match the session-resolved linked wallet; it can never override which wallet the message is attributed to.
Response (`ChatPostResponse`): `{message: ChatMessageResponse, ai_response: ChatMessageResponse|null}`.
Errors: 400 `Message cannot be empty`; 400 `Message too long (max 2000 chars)`; 403 `wallet_address is not linked to the authenticated account` (body wallet mismatch); plus `require_linked_wallet`'s own 401 `Authentication required` (no session) and 403 `A verified linked wallet is required` (no linked wallet).

```bash
curl -s -X POST https://archimedes-arc.com/api/vaults/<address>/chat \
  -b /tmp/session.jar -H "Content-Type: application/json" \
  -d '{"message": "@archimedes what changed in this vault today?"}'
```

### GET /api/vaults/{address}/chat/count
Get total message count for a vault. | **Auth**: anonymous

Request: path `address`.
Response: `{vault_address: str, message_count: int}`.
Errors: none.

```bash
curl -s https://archimedes-arc.com/api/vaults/<address>/chat/count
```

### POST /api/vaults/{address}/chat/rebalance
Post a rebalance event from the agent runner — internal-only. | **Auth**:
internal-key | **Flags**: `INTERNAL_AGENT_API_KEY` (required; unset means the
gate rejects every caller, since an empty expected value never matches)

Request: `{reasoning: str="Portfolio rebalanced", trades: [{direction, amount, symbol}]|null}`.
Response: result of `chat_service.post_rebalance_event()`.
Errors: 403 `Forbidden` (missing or wrong `X-Internal-Agent-Key` header); 500 `Failed to post rebalance event` (service returned `None`).

```bash
curl -s -X POST https://archimedes-arc.com/api/vaults/<address>/chat/rebalance \
  -H "X-Internal-Agent-Key: $INTERNAL_AGENT_API_KEY" -H "Content-Type: application/json" \
  -d '{"reasoning": "Rebalanced toward momentum signal", "trades": [{"direction": "sell", "amount": 1000, "symbol": "sTSLA"}]}'
```

### POST /api/vaults/{address}/chat/regime-change
Post a regime change event from the agent runner — internal-only. |
**Auth**: internal-key | **Flags**: `INTERNAL_AGENT_API_KEY` (same gate as
`/chat/rebalance`)

Request: `{old_regime: str="unknown", new_regime: str="unknown", confidence: float=0.0}`.
Response: result of `chat_service.post_regime_change()`.
Errors: 403 `Forbidden` (missing/wrong `X-Internal-Agent-Key` header); 500 `Failed to post regime change` (service returned `None`).

```bash
curl -s -X POST https://archimedes-arc.com/api/vaults/<address>/chat/regime-change \
  -H "X-Internal-Agent-Key: $INTERNAL_AGENT_API_KEY" -H "Content-Type: application/json" \
  -d '{"old_regime": "risk_on", "new_regime": "risk_off", "confidence": 0.85}'
```
