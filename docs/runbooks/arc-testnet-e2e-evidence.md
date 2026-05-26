# Arc Testnet E2E Evidence — 2026-05-26

> **Status:** Live, on-chain, replayable. SPEC-1 executed by Önder Akkaya
> against `https://archimedes-arc.app/` via a user-controlled MetaMask
> wallet (no backend private keys, no canned data).

**Generated:** 2026-05-26 (TR night, ahead of final demo)
**Operator:** Önder Akkaya (`@onder-akkaya`)
**Chain ID:** 5042002 (Arc Testnet)
**Live URL:** <https://archimedes-arc.app/>

---

## Identity

- **Wallet (user):** `0x8b5EEE65c78aB9e39825A4a7991b13686442fc14`
- **Funded via:** Circle faucet (`https://faucet.circle.com/`) — testnet USDC at `0x3600000000000000000000000000000000000000`

## Vaults deployed

Both Tier-1 rigor-passing strategies were deployed as **separate
non-custodial vaults**, each owned by the operator's wallet. This is the
architectural proof point from PR #342 Part 2: `vault.creator == user`,
not `vault.creator == backend`.

### Vault 1 — Volatility-Managed Portfolios (Moreira–Muir 2017)

- **Vault address:** `0x09eb31944d8C03C7C60532c39E6E1F52B0a4806e`
- **Creator (on-chain):** `0x8b5eee65c78ab9e39825a4a7991b13686442fc14` ← matches operator
- **AUM at deploy:** `10.00 USDC`
- **Strategy ID:** `223826b62d5c1c014dcfde677597a5b8`
- **Rigor pass:** DSR p = 0.995 (≥ 0.95 ✅), PBO = 0.390 (< 0.5 ✅), OOS Sharpe = 0.969 ✅, look-ahead audit ✅
- **Position sizing:** inverse-volatility
- **Asset universe:** `[SPY, NIKKEI, GOLD, TREASURY, OIL]` → on-chain synth path

### Vault 2 — Time Series Momentum (Moskowitz–Ooi–Pedersen 2012)

- **Vault address:** `0x84B0ef7dE1155E4Df137a2A1064ACC36e90F05ec`
- **Creator (on-chain):** `0x8b5eee65c78ab9e39825a4a7991b13686442fc14` ← matches operator
- **AUM at deploy:** `2.00 USDC`
- **Strategy ID:** `8dab1b3550fcdd998366166d1f4caba9`
- **Rigor pass:** DSR p = 0.976 (≥ 0.95 ✅), PBO = 0.390 (< 0.5 ✅), OOS Sharpe = 0.762 ✅, look-ahead audit ✅
- **Position sizing:** equal-weight
- **Asset universe:** `[SPY, NIKKEI, GOLD, TREASURY, OIL]` → on-chain synth path

## Phase 5 — Deploy transactions (Arc testnet)

The deploy flow per [`spec-1-walkthrough.md`](./spec-1-walkthrough.md):

| Step | Action | What it does |
| --- | --- | --- |
| 1 | `VaultFactory.createVault()` | Creates ERC-4626 vault, user = creator |
| 2 | `vault.setAgent(operatorAddr)` | Grants the agent rebalance authority (NOT withdraw) |
| 3 | `USDC.approve(vault, depositAmount)` | Bounded spending cap — never unlimited |
| 4 | `vault.deposit() + setTargetAllocations()` | Pulls USDC + locks strategy targets |

### Vault 1 deploy — Volatility-Managed Portfolios

| # | Nonce | Block | Action | Tx hash (click to view on arcscan) |
| --- | --- | --- | --- | --- |
| 1 | 0 | 44028414 | `createVault()` | [`0xd568766f...c8cd80781`](https://testnet.arcscan.app/tx/0xd568766f294fcd669161fda5d364afd37ed92d3ad9bd53034bfdbf1c8cd80781) |
| 2 | 1 | 44028428 | `vault.setAgent()` | [`0xbec0689d...39ace73e`](https://testnet.arcscan.app/tx/0xbec0689d277c30ceecadc85783e60d20c56d9c0a5951c288be79747439ace73e) |
| 3 | 2 | 44029200 | `USDC.approve(vault, 10e6)` | [`0x7a0a6153...09a2045e6`](https://testnet.arcscan.app/tx/0x7a0a6153229d7dc551350edc1b926fc48f10f05357243ee604c322e09a2045e6) |
| 4 | 3 | 44029208 | `vault.deposit() + setTargetAllocations()` | [`0xe74124d3...4a9a31f277b`](https://testnet.arcscan.app/tx/0xe74124d3071b8997f27cf70924ca4e40b4c5951f9011ed30055084a9a31f277b) |

All four confirmed on-chain with `txreceipt_status: 1` (success).

### Vault 2 deploy — Time Series Momentum

| # | Nonce | Block | Action | Tx hash (click to view on arcscan) |
| --- | --- | --- | --- | --- |
| 1 | 4 | 44031006 | `createVault()` | [`0xa80a5665...4edf3080`](https://testnet.arcscan.app/tx/0xa80a5665b25113dee2dd0805eeb2ec63b9e3bc13d4421893cdab67934edf3080) |
| 2 | 5 | 44031114 | `vault.setAgent()` | [`0x69c17c1d...1221e49f`](https://testnet.arcscan.app/tx/0x69c17c1d61b5b260f4f613d5c270f892c268d064fc354b38676eb2ec1221e49f) |
| 3 | 6 | 44031322 | `USDC.approve(vault, 2e6)` | [`0x6be875c3...25cf927`](https://testnet.arcscan.app/tx/0x6be875c33e02a7e30d07daecfd74178ac7155ddc7b76381a0a418cf4025cf927) |
| 4 | 7 | 44031374 | `vault.deposit() + setTargetAllocations()` | [`0x952e7fef...b62e91`](https://testnet.arcscan.app/tx/0x952e7fef743b10924f099c3c3384ac0d353adf48a0c00c44653ba28b2ab62e91) |

All four confirmed on-chain. Receipt for step 4 verified live:

- Block: `44031374`
- Status: **SUCCESS** ✅
- Gas used: `190,890`
- From: `0x8b5eee65c78ab9e39825a4a7991b13686442fc14` (operator)
- To: `0x84B0ef7dE1155E4Df137a2A1064ACC36e90F05ec` (vault)
- Logs emitted: 4 (deposit + share mint + approval delta + target allocation set)

### Summary

**8 transactions total**, nonces 0–7, all `txreceipt_status: 1`. Two complete
4-signature deploy sequences in ~25 minutes of wall-clock time. Every hash
above resolves to a public, replayable receipt on Arc testnet.

## Phase 6 — Portfolio dashboard verification (the architectural fix)

Per PR #342 Part 2, the user-as-creator architecture was the load-bearing
change. The pre-#342 backend-as-creator architecture returned `[]` from
`getVaultsByCreator(walletAddr)`, so user-deployed vaults never showed
up on the Portfolio page. After #342 Part 2, the Portfolio page lists
the operator's own vaults correctly.

**Live evidence (screenshot of `https://archimedes-arc.app/portfolio` at
~03:10 TR time, 2026-05-26):**

| Tile | Value |
| --- | --- |
| **Wallet USDC** | `$7.88` (idle — initial 9.94 minus deposits + gas) |
| **Vault AUM** | `$12.00 across 2 vaults` |
| **Unrealized PnL** | `$0.00 / +0.00%` |
| **Agent** | **Alive** (heartbeat 1m ago) |
| **Regime** | `RISK ON 92.0% confidence` |

Both vaults appear in the **Your vault positions** section with correct
addresses, AUM, and tier (Community). The architectural proof point —
*"the user controls the vault, the backend doesn't"* — is confirmed.

## Phase 7 — First reasoning trace (pending agent tick)

The agent ticks every ~5 min. The first trace for these vaults is
expected within 5–10 min of deploy. Given the on-chain AMM state at the
time of deploy:

- Only `pool_0` (sTSLA) carries liquidity (~$1500 USDC equivalent).
- Pools 1-4 (sNVDA, sSPY, sBTC, sGOLD) are dry.
- `sOIL` and `sNKY` synthetics have no pools at all (issue #362
  acknowledged; documented as a testnet limitation in the README).

Both deployed strategies route into the SPY/NIKKEI/GOLD/TREASURY/OIL
synth universe — *none* of which currently have AMM liquidity. **The
expected trace content is therefore `"Swap skipped — thin pool: ..."`,
the rigor guard from PR #342 Part 3 working honestly.** Either outcome
(a real rebalance, or an honest skip-with-reason) is acceptable evidence
per the runbook.

This is the demo-honest path: we don't fake liquidity to make the chart
move. We surface the constraint and rebalance only when the AMM can
absorb the trade safely.

## Phase 8 — Verify on-chain

Each tx hash above resolves to a `Status: Success` row on
<https://testnet.arcscan.app> that judges or auditors can inspect without
trusting any screenshot in this file. The trace verification path
(post-PR #308 O(1) verify) lets a viewer click "Verify hash on-chain"
on any reasoning card and confirm the merkle root within ~1 second.

## What this proves

1. **The wedge is real.** Of 6 strategies in the library, exactly the 2
   that pass the four selection-bias gates (DSR ≥ 0.95, PBO < 0.5, OOS
   ratio ≥ 0.5, look-ahead AST audit clean) were deployable. The other 4
   — including the buy-and-hold baseline — were correctly gate-blocked.
2. **Non-custodial.** The user's wallet is the on-chain creator. The
   agent has rebalance authority only; no `withdrawTo(operator)` exists
   on the vault interface. Funds never pass through platform custody.
3. **End-to-end on real chain.** Every state change is anchored to an
   Arc testnet transaction with a public receipt. No mock backend, no
   sandboxed signer.
4. **Honest under constraints.** The pre-trade rigor guard (V-Check from
   PR #342 Part 3) skips trades into thin pools rather than reverting
   loudly or — worse — executing into shallow liquidity and getting
   sandwich-eaten. The "rigor" framing applies to runtime, not just
   admission.

## Replay instructions for judges

1. Open <https://archimedes-arc.app/>.
2. Connect MetaMask on Arc Testnet (chain ID `5042002`, RPC `https://rpc.testnet.arc.network`).
3. Fund the wallet via <https://faucet.circle.com/> (20 USDC every 2h).
4. Sidebar → **Library** → **Examples** tab → click any green-✅ rigor-passing entry.
5. **Deploy as Vault** → enter ≥ 2 USDC → sign 4 MetaMask popups.
6. Sidebar → **Portfolio** to see the new vault.
7. Wait 5 min, then **Reasoning** for the first trace; click **Verify hash on-chain**.

Detailed walkthrough: [`spec-1-walkthrough.md`](./spec-1-walkthrough.md).
