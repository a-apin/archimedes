> **ARCHIVED 2026-07-28 — historical. Current: [`docs/runbooks/arc-testnet-e2e.md`](../runbooks/arc-testnet-e2e.md)**
>
> Planning-only Phase 5 runbook written overnight in May 2026, gated on a kickoff from a contributor who left the project on 2026-06-24. Never executed as written.

# Phase 5 — Real Testnet Trade Execution Runbook

> **Status:** Planning-only — spec + verification runbook + open questions. No
> code in this PR. Drafted overnight per `dbrowneup/phase5-execution-runbook`
> while Dan slept; per [`docs/specs/spine-plus-v2-plan.md` § Phase 5](../plans/spine-plus-v2-plan.md)
> the implementation needs Chuan's kickoff (signing setup, AMM liquidity
> verification, `setTargetAllocations` semantics, USDC-as-gas) before the code
> goes in.
>
> **Sibling PRs:** Phase 4 scaffold (PR #142, DRAFT) — `StrategyPassport` route
> + `CreateVaultModal` + `StressScenarioPanel`. Phase 5 is the **next** step
> after a vault is created: deposit USDC, call `setTargetAllocations`, let the
> agent execute the first rebalance, anchor the trace.
>
> **Why this exists:** the on-chain trade execution path is largely *coded*
> (`chain/executor.py`, `chain/agent_runner.py`, `chain/circle_signer.py`)
> but **has not been verified end-to-end from a real MetaMask wallet through a
> live Arc-testnet rebalance.** Phase 5 = verify + close the user-facing
> gaps (deposit + setTargetAllocations wallet flow + tracing).

## What already exists (reuse — do not rebuild)

The trade-execution machinery is largely in place:

| Component | Status | File |
|---|---|---|
| Vault deploy via VaultFactory | ✅ wired + working | `chain/executor.py::create_vault` + `POST /api/vaults/create` |
| `setTargetAllocations` call | ✅ implemented (server-signed via Circle) | `chain/executor.py::set_target_allocations` |
| Vault `rebalance(...)` call | ✅ implemented | `chain/executor.py::rebalance` |
| Agent ticking loop | ✅ implemented | `chain/agent_runner.py` (782 lines) |
| Oracle price updates | ✅ implemented (60s tick) | `chain/oracle_updater.py` + `chain/oracle_runner.py` |
| ReasoningTrace publishing | ✅ implemented | `chain/trace_publisher.py` |
| Circle managed-wallet signing | ✅ implemented | `chain/circle_signer.py` |
| AMM bootstrap script | ✅ exists | `scripts/bootstrap_vaults.py` |
| Stress engine | ✅ exists; UI exposed in Phase 4 scaffold | `services/stress_engine.py` |

## What's missing for end-to-end testnet execution

The honest gap list, in priority order:

### 1. Frontend wallet flow for deposit + setTargetAllocations

The Phase 4 scaffold's `CreateVaultModal.jsx` stops after `POST /api/vaults/create`. After a vault exists, the user needs to:

1. **Sign USDC `approve(vault, amount)`** — viem `writeContract` with USDC ABI.
2. **Sign `vault.deposit(amount, receiver)`** — ERC-4626 deposit.
3. **Sign or trigger `setTargetAllocations(tokens, weights)`** — this is the *strategy plan*. The decision tree (default: agent-runner derives the plan from the strategy's DSL; user-signed `setTargetAllocations` is acceptable as a v1 simplification).

**Recommended new component:** `ui/src/components/DepositFlow.jsx` — three-step stepper modal:

```
Step 1 / 3 — Approve USDC      [pending] [signing] [✓ tx 0xabc…]
Step 2 / 3 — Deposit            [pending] [signing] [✓ tx 0xdef…]
Step 3 / 3 — setTargetAllocations [pending] [signing] [✓ tx 0xghi…]
```

Each step is independent; if step 2 fails, step 1's approve persists on-chain and step 2 can be retried.

### 2. Agent-runner trigger on new vault

When a new vault is created mid-tick, the agent runner needs to pick it up. Two options:
- **(a) Polling — agent runner queries `VaultFactory.getAllVaults()` on each tick.** Simple; ~60s latency to first agent action.
- **(b) Event-driven — agent runner subscribes to `VaultFactory.VaultCreated` events.** Lower latency but more code; web3.py subscriptions are flaky on testnet.

Recommend (a) for v1.

### 3. AMM liquidity verification

`scripts/bootstrap_vaults.py` seeds AMM pools, but post-deploy state is unknown. Phase 5 needs a runbook step:

```bash
# Verify all synth pools have ≥ $1000 USDC-side liquidity
cd backend
python -c "from archimedes.chain.client import chain_client; ..."
# OR: hit /api/explore/assets — if oracle prices look stale or asset list shrinks, pools may be empty
```

If any pool is empty, agent rebalances revert with "insufficient liquidity" — silently failing in the trace log. Surface as a `/api/health/amm` endpoint.

### 4. End-to-end happy-path test

A real wallet → real deposit → real rebalance → real trace verification. The runbook below documents the exact steps.

### 5. USDC-as-gas (Paymaster)

**Out of scope for Phase 5 v1** — user pays gas with Arc-native ETH. Phase 5.5 wires Circle's Paymaster.

## Open questions (need Chuan's read before implementation)

These are the 4 open questions from `spine-plus-v2-plan.md` § Phase 5, restated with the defaults the Phase 4 scaffold takes:

1. **Agent signing — is the runner using Circle managed wallet or a local key?**
   `chain/circle_signer.py` exists. Phase 4 scaffold default: **Circle managed wallet for the agent's `setTargetAllocations`/`rebalance` calls; user wallet (MetaMask) for deposit only.** Need confirmation this matches Chuan's intent.

2. **AMM liquidity post-deploy — is `bootstrap_vaults.py` run automatically after each contract redeploy, or manual?**
   If manual, Phase 5 needs a CI step. If automatic, fine.

3. **`setTargetAllocations` semantics — does the vault execute swaps synchronously on this call, or does a separate `rebalance` step follow?**
   Looking at `chain/executor.py`, both methods exist independently. Default assumption: `setTargetAllocations` writes target state; `rebalance` (called by agent runner on next tick) executes the swaps to converge.

4. **USDC-as-gas setup — is the Paymaster active on Arc testnet for our user wallets?**
   If yes, the deposit flow can use Paymaster instead of requiring native ETH. If no, document the ETH requirement honestly in the deposit UX.

## Verification runbook (the end-to-end happy path)

When Phase 5 implementation lands, this is what we should run before merging:

```bash
# 0. Reset — fresh dev wallet, fresh USDC balance, fresh Arc testnet RPC
#    (use a wallet that has NEVER touched mainnet — hackathon hygiene)
arc-canteen status   # confirm RPC + telemetry are live

# 1. Start the stack
docker compose up -d --build

# 2. Verify AMM liquidity
curl -s http://localhost:8000/api/health/amm | jq
# Expected: { "pools": [{symbol: "sSPY", liquidity_usdc: 5000, ...}, ...] }
# If any pool < $1000 → run: docker compose exec backend python -m archimedes.scripts.bootstrap_vaults

# 3. Open http://localhost in MetaMask-connected browser
#    - Connect wallet (Arc testnet)
#    - Generate a strategy (any path; record strategy_id)
#    - Open /strategy/{strategy_id} passport
#    - Click "Deploy as Vault →"
#    - Fill form → Submit → wait for vault_address in success notice
#    - Record: vault_address

# 4. Multi-step deposit (Phase 5 work — does not exist yet in UI)
#    Modal step 1: USDC.approve(vault_address, 10 USDC) — wallet signs
#    Modal step 2: vault.deposit(10 USDC, walletAddr) — wallet signs
#    Modal step 3: vault.setTargetAllocations([token_addrs], [weights_bps]) — wallet signs
#                  (or server-signed if Chuan opts for agent-managed allocations)

# 5. Wait ~60 seconds for the agent runner to tick.
#    Expected: agent reads vault state, computes rebalance, calls vault.rebalance(),
#    AMM executes the swaps, oracle reflects updated holdings.

# 6. Verify on Portfolio page
#    - /portfolio renders the vault with non-zero AUM
#    - Click into vault → vault detail shows synth holdings (not just USDC)
#    - "Recent Agent Activity" feed has a new trace with decision_type='rebalance'

# 7. Verify trace
#    - Click the rebalance trace → /reasoning/{trace_id}
#    - Click "Verify on-chain" → response is_verified=true
#    - The trace_hash matches what's stored in ReasoningTraceRegistry on Arc

# 8. Document evidence in PR description
#    - Arc explorer link for vault.deposit tx
#    - Arc explorer link for vault.rebalance tx
#    - /api/traces/{trace_id}/verify response showing is_verified=true
```

## Suggested file layout (when implementation lands)

| File | Status | Purpose |
|---|---|---|
| `ui/src/components/DepositFlow.jsx` | NEW | Three-step stepper modal: approve → deposit → setTargetAllocations |
| `ui/src/components/CreateVaultModal.jsx` | EXTEND | After successful create, open DepositFlow instead of closing |
| `backend/archimedes/api/health_routes.py` | NEW or EXTEND existing | `/api/health/amm` endpoint that reports per-pool liquidity |
| `backend/archimedes/chain/agent_runner.py` | EDIT | Add VaultFactory polling on each tick to pick up new vaults |
| `backend/archimedes/scripts/verify_arc_e2e.py` | NEW | Scripted version of the runbook above for CI |
| `docs/runbooks/arc-testnet-e2e.md` | NEW | Human-readable version of the runbook |

## What NOT to do without Chuan

Per CLAUDE.md: **smart-contract changes need Chuan's review.** The runbook above does not require ANY Solidity changes:

- `Vault.deposit` — already supports ERC-4626 deposit.
- `Vault.setTargetAllocations` — already deployed.
- `Vault.rebalance` — already callable by the agent.
- `VaultFactory.createVault` — already deployed.

If the verification runbook surfaces a real on-chain bug, file a separate issue and assign to Chuan; do not patch contracts overnight.

## Default choices (matching Phase 4 scaffold)

| # | Open question | Phase 4 scaffold's default |
|---|---|---|
| 1 | Agent signing | Circle managed wallet (existing `circle_signer.py`) |
| 2 | AMM liquidity check | Manual via runbook (Phase 5.5 adds `/api/health/amm` + CI) |
| 3 | setTargetAllocations semantics | Async — `set` writes target; agent's `rebalance` on next tick executes |
| 4 | USDC-as-gas | Out of scope; user provides Arc ETH for v1; Paymaster is Phase 5.5 |

## When this PR can leave planning-only

When the four open questions above have answers from Chuan + Marten (Discord standup or in-PR review comment), this doc graduates from "spec" to "runbook in active use" and Phase 5 implementation can start.
