# ERC-8004 identity registration on Arc

> **status:** runbook
> **owner:** Dan Browne
> **updated:** 2026-08-31
> **superseded-by:** —

**Scope:** minting Archimedes' single ERC-8004 agent identity on the Arc testnet
IdentityRegistry, and turning the discovery surfaces from `registration_pending` to
`registered` — honestly. Issue [#1527](https://github.com/a-apin/archimedes/issues/1527);
the scaffold it builds on is #1552.

**Who runs it:** the platform owner, once. Not CI, not an agent, not the backend. The
transaction is signed by the Circle dev-controlled wallet; no private key is ever read by
this repo's code.

## What "registered" means here, and what it does not

Registration mints an ERC-721 to the platform wallet. The token id **is** the agentId.
That is an *identity* record and nothing more — it asserts nothing about reputation or
validation, and this project claims neither (the ReputationRegistry and ValidationRegistry
legs are deliberately out of scope until an independent validator exists).

The surfaces do not take the operator's word for it. `GET /api/agent/manifest` re-derives
`erc8004.status` on every request from a live `ownerOf(agentId)` call
([`backend/archimedes/chain/erc8004_identity.py`](../../backend/archimedes/chain/erc8004_identity.py)).
`ERC8004_AGENT_ID` tells the verifier *which token to check*; it never makes the claim.
Set it to a token somebody else owns and the manifest keeps saying
`registration_pending` — that is the intended behaviour, not a bug.

## 0. Contract facts (verified 2026-08-31 against the live chain)

| | |
|---|---|
| Chain | Arc testnet, `5042002` (`0x4cef52`) |
| RPC | `https://rpc.testnet.arc.network` |
| IdentityRegistry | `0x8004A818BFB912233c491871b3d84c89A494BD9e` (ERC-1967 proxy) |
| Contract `name()` / `symbol()` | `AgentIdentity` / `AGENT` |
| ERC-721 (`supportsInterface(0x80ac58cd)`) | `true` |
| Function called | `register(string agentURI)`, selector `0xf2c298be` |
| Reads used | `ownerOf(uint256)`, `tokenURI(uint256)`, `balanceOf(address)` |
| Not available | `getAgentId`/`agentIdOf`/`resolveByOwner`/`tokenOfOwnerByIndex` — all revert. There is **no** owner→agentId lookup; discovery goes through the mint log. |

The pinned ABI is [`contracts/abis/ERC8004IdentityRegistry.json`](../../contracts/abis/ERC8004IdentityRegistry.json).
**Schema-change risk:** EIP-8004 is still Draft and the registry sits behind a proxy, so an
upgrade can change these selectors. The failure mode is a read that reverts, which surfaces
as `source: "unavailable"` and holds the status at pending; it cannot become a false
`registered`. Re-run step 1 after any announced registry upgrade.

Re-verify the table above at any time:

```bash
curl -s -X POST https://rpc.testnet.arc.network -H 'content-type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"eth_call","params":[{"to":"0x8004A818BFB912233c491871b3d84c89A494BD9e","data":"0x06fdde03"},"latest"]}'
```

## 1. Plan (read-only — signs nothing, sends nothing)

```bash
python scripts/register_erc8004_identity.py --plan --from 0x<PLATFORM_WALLET>
```

Expect the `register(string)` selector `0xf2c298be`, the agentURI decoding back to
`https://archimedes-arc.com/.well-known/agent-registration.json`, a non-zero registry
bytecode size, and a gas estimate. **If the plan reports a problem, stop.** Registering
against an address with no code burns USDC gas (USDC *is* gas on Arc) and mints nothing.

## 2. Check what the chain already says

```bash
ERC8004_OWNER_ADDRESS=0x<PLATFORM_WALLET> \
  python scripts/register_erc8004_identity.py --verify
```

`registration_pending (no identity found for this wallet)` means step 3 is safe. If it
reports an agentId, **we are already registered** — skip to step 4. Re-registering mints a
second identity that cannot be un-minted and permanently splits the reputation surface the
standard exists to accumulate.

## 3. Register (owner only)

Credentials are Circle's — the same three the oracle already runs on. They go in the
environment, never on the command line, and never into this repo.

```bash
export CIRCLE_API_KEY=...          # TEST_API_KEY:UUID:SECRET
export CIRCLE_ENTITY_SECRET=...    # 32-byte hex
export WALLET_ID=...               # the platform wallet's Circle UUID
export ERC8004_OWNER_ADDRESS=0x<PLATFORM_WALLET>

python scripts/register_erc8004_identity.py --execute
```

The runner reads the chain first (idempotent: already registered ⇒ `action: noop`, no
transaction), submits `register(string)` through
`CircleSigner.execute_contract`, then reads the chain **again** to learn the minted agentId
and confirm ownership. Only `action: registered` with a non-null `agentId` is a success.

- `action: submitted` means the transaction went out but the confirming read did not
  complete. Do not publish anything. Re-run step 2 once the block has settled.
- `action: refused` means nothing was sent; the `detail` line says why.

Independent confirmation with `cast`:

```bash
cast call 0x8004A818BFB912233c491871b3d84c89A494BD9e "ownerOf(uint256)(address)" <AGENT_ID> \
  --rpc-url https://rpc.testnet.arc.network
cast call 0x8004A818BFB912233c491871b3d84c89A494BD9e "tokenURI(uint256)(string)" <AGENT_ID> \
  --rpc-url https://rpc.testnet.arc.network
```

## 4. Turn the surfaces on

`python scripts/register_erc8004_identity.py --print-followup <AGENT_ID>` prints all of it.
Two halves, and they are different kinds of change:

1. **Deployment environment** (SSM / the box `.env`), *not* a code constant:
   `ERC8004_AGENT_ID=<AGENT_ID>` and `ERC8004_OWNER_ADDRESS=0x<PLATFORM_WALLET>`. This tells
   the verifier which token to read. The manifest flips to `registered` only once a live
   `ownerOf()` agrees, so a wrong value here degrades to pending rather than lying.
2. **One commit**, carrying the transaction hash in the message: the `registrations` entry
   in `ui/public/.well-known/agent-registration.json` *and*
   `agent-registration.domain.json`, and the regenerated `erc8004` block in
   `ui/public/.well-known/agent.json`. `backend/tests/test_erc8004_identity.py` fails if
   these move apart, including the deliberately frozen
   `test_the_shipped_state_is_pending` — update that one in the same commit.

## 5. Verify from outside

```bash
curl -s https://archimedes-arc.com/api/agent/manifest | jq '.erc8004, .erc8004_verification'
curl -s https://archimedes-arc.com/.well-known/agent.json | jq .erc8004
```

`erc8004_verification.source` must read `onchain` and `owner` must equal `expectedOwner`.
`unavailable` means the deployment got no ownership answer out of the registry — the
pending status beside it is a refusal to claim, not a finding that we are unregistered.
Check the RPC path from inside the VPC before touching anything else.

## Rollback

There is no un-mint. To stop claiming a registration, unset `ERC8004_AGENT_ID` in the
deployment environment: the next request re-derives `registration_pending` with
`source: "unconfigured"`. Reverting the `.well-known` commit removes the published record.
The token itself stays on-chain.
