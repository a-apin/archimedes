# SPEC-1 — End-to-end User Journey Walkthrough

> **Goal:** capture evidence that the entire user flow works end-to-end on
> the live site, from wallet connection through to an on-chain reasoning
> trace. The resulting tx hashes + screenshots are committed to
> [`arc-testnet-e2e-evidence.md`](./arc-testnet-e2e-evidence.md) and linked
> from the README + ARC-OSS-SHOWCASE as the canonical "judge can replay this"
> artifact.

> **Time estimate:** 25–35 minutes wall-clock, mostly waiting for tx confirmations.

> **Audience:** a non-expert user with MetaMask installed. No CLI experience
> needed for the core flow; one optional terminal command for the wallet
> balance probe. Phases are self-contained.

---

## Security & wallet hygiene (read first)

- **Never paste your private key anywhere.** Not in chat, not in a commit,
  not as a CLI argument. The runbook records only **addresses and tx hashes** —
  both are public information.
- **Use a fresh dev wallet.** Generate one (in MetaMask: account dropdown →
  "Add a new account") with **no real assets on any chain.**
- **Fund the wallet via the Circle faucet** at <https://faucet.circle.com> →
  select **Arc Sepolia** → request 20 USDC. Refills every 2h.
- Verify MetaMask shows **Arc Sepolia** as the active network (chain ID
  `5042002` / `0x4cef52`) before connecting to the site.

---

## Phase 1 — Wallet ready

1. In MetaMask, switch to your fresh dev account. Confirm:
   - Active address starts with `0x...` and you can copy it
   - Network selector shows **Arc Sepolia**
   - USDC balance shows ~20 USDC (import the token at
     `0x3600000000000000000000000000000000000000` if it doesn't show)
2. *Optional CLI sanity check* (substitute `$YOUR_ADDR`):

   ```bash
   YOUR_ADDR=0x...   # paste your wallet address
   ARC_RPC=$(grep -E "^(RPC|ARC_ARC_RPC_URL)=" .env | head -1 | cut -d= -f2-)
   cast balance "$YOUR_ADDR" --rpc-url "$ARC_RPC" \
     --erc20 0x3600000000000000000000000000000000000000
   ```

   Expected: `20000000` (= 20 USDC in 6-decimal units).

**Checkpoint 1:** wallet address + USDC balance confirmed.

---

## Phase 2 — Connect to the site

1. Open <https://archimedes-arc.app/> in a browser tab where MetaMask is
   active.
2. Verify the **hero text** reads
   *"Agentic trading, grounded in research."* / *"Your Intent. Our Rigor."*
3. Click **Connect wallet** in the top-right.
4. In the MetaMask popup, confirm your active account, then click **Connect**.
5. The site should show your address (truncated) in the top-right.

**Checkpoint 2:** site shows connected wallet.

---

## Phase 3 — Generate a strategy (no signing)

1. Sidebar → **Generate**.
2. Open the **"How this works · tips · examples"** collapsible.
3. Click one of the example briefs (e.g. *"Trend-following momentum on liquid
   US equity ETFs, rebalanced monthly, regime-aware"*) — it auto-fills the
   textarea and collapses the panel.
4. Click **Generate Strategy**.
5. Watch the SSE stream — expected events in order:
   - `job_queued` → `brief_validated` → `pipeline_selected`
   - `candidates_selected: 2` (bull + bear)
   - `🟢 Bull Moderate Blend — <brief>` candidate drafted + evaluated
   - `🔴 Bear Moderate Blend — <brief>` candidate drafted + evaluated
   - `best_selected` → `trace_hashed` → `persisted` → `done`
6. Two cards appear side-by-side after completion: 🟢 Bull and 🔴 Bear,
   each with asset-weight preview + **View in Library →** button.

**Note on rigor:** freshly-generated strategies almost always fail rigor at
generation time because the return series is too short for DSR/PBO. That's
expected. Use an Example-tab strategy for the actual Deploy step (Phase 4b
below).

**Checkpoint 3:** strategy_id of the bull candidate + confirmation that
rigor failed (or passed — record either honestly).

---

## Phase 4 — Verify the Library landed correctly

1. Click **View in Library →** on either card.
2. Lands on `/library?highlight=<strategy_id>` with the **Generated** tab
   active and your new strategy highlighted at the top.
3. Confirm the new pair is visible. If they failed rigor, they appear in the
   collapsible **"Rejected (N) — did not pass the rigor gate"** section below
   the main table — expand it to inspect.
4. Click into the bull strategy to open the **Strategy Passport**.

On the passport, confirm:
- Title includes your brief intent (e.g. *"🟢 Bull Moderate Blend —
  Trend-following momentum..."*)
- Methodology summary references your brief
- Source papers section shows ≥ 5 anchored papers
- Rigor metrics: DSR/OOS/Trials likely show "—" for fresh generation; PBO
  also "—" (not a fake 0.0%)
- **Deploy as Vault** button is visibly grayed-out + `cursor: not-allowed`
  if rigor failed

**Checkpoint 4:** Passport renders the user's brief correctly + Deploy
button correctly disabled if rigor failed.

### Phase 4b — Pick a passing-rigor strategy for the actual deploy

Because fresh generations fail rigor, do the Deploy from a **passing
Example strategy**. As of 2026-05-27 exactly two library strategies clear
all four gates: **Moreira-Muir 2017 Volatility-Managed Portfolios** (DSR
p = 0.995) and **Moskowitz-Ooi-Pedersen Time Series Momentum** (DSR
p = 0.976). Faber 2007 GTAA does **not** pass (DSR p = 0.612) despite a
similar raw Sharpe — see [`docs/analysis/faber-dsr-finding.md`](../analysis/faber-dsr-finding.md)
for why (skew/kurtosis deflation, working as designed).

1. Library → **Examples** tab → click a passing-rigor entry (green ✓).
2. Open the Passport. Confirm Deploy button is enabled (full opacity,
   normal cursor).

**Checkpoint 4b:** which passing-rigor strategy you'll deploy.

---

## Phase 5 — Deploy as a vault (4 MetaMask signatures)

Per PR #342 Part 2, the user (not the backend) signs `createVault()`, so
`vault.creator == user` and the new vault will show up in `/portfolio`.

1. On the chosen Passport, click **Deploy as Vault**.
2. Modal opens with a deposit amount field. Use **5 USDC** (leaves a buffer).
3. Click **Deploy** / **Start**.

MetaMask will pop 4 times:

| Step | Action | What it does |
|------|--------|--------------|
| 1 | `VaultFactory.createVault()` | Creates a new ERC-4626 vault; user is the creator |
| 2 | `vault.setAgent(operatorAddr)` | Gives the agent rebalance authority |
| 3 | `USDC.approve(vault, 5e6)` | Standard ERC-20 approval |
| 4 | `vault.deposit() + setTargetAllocations()` | Funds vault + locks strategy targets |

After each confirmation, MetaMask shows "Activity" with the tx. Click into
it → **View on explorer** → opens `testnet.arcscan.app/tx/0x...`. Copy each
tx hash; screenshot the success page.

**Checkpoint 5:** 4 tx hashes labelled by step.

---

## Phase 6 — Confirm Portfolio shows YOUR vault

The architectural fix from #342 Part 2 unblocks this — previously the
backend was the creator and `getVaultsByCreator(walletAddr)` returned `[]`.

1. Sidebar → **Portfolio**.
2. The 4-tile account header should show:
   - **Wallet USDC** — ~15 USDC (20 - 5 deposit, minus a bit for gas)
   - **Vault AUM** — ~5 USDC
   - **Unrealized PnL** — `$0.00 / +0.00%`
   - **Agent** — Alive (green dot)
3. Below the header: a **vault card** with your new vault address + 5 USDC
   AUM + a per-vault PnL chip.

**Checkpoint 6:** new vault address visible in Portfolio with expected
numbers. **This is the demo-critical architectural verification.**

---

## Phase 7 — Wait for agent rebalance (~5–10 min)

The agent ticks every ~5 min. When it picks up your vault on the next tick:

- Reads your strategy's target allocations
- Computes drift vs current holdings (USDC only at deposit time)
- Decides whether to rebalance
- **If pools have sufficient liquidity** (≥ $1000 USDC reserve per PR #358):
  rebalance fires, trace records `[REVEAL] Regime: ... Trades: N` with real
  `arc_tx_hash`
- **If pools are below the threshold:** trace records `Swap skipped — thin
  pool: ...` — the rigor guard from #342 Part 3 working honestly

Open `/reasoning` in another tab. Filter or scroll to find traces whose
`vault_address` matches your new vault.

**Checkpoint 7:** timestamp + reasoning text of the first trace for your
vault. Either outcome is acceptable evidence.

---

## Phase 8 — Verify on-chain

On the trace card for your vault (on `/reasoning`):

1. Click the **arcscan link** at the top of the card — opens
   `testnet.arcscan.app/tx/0x...` in a new tab.
2. Confirm arcscan shows **Status: Success** + block number + gas used +
   timestamp.
3. Back on the trace card, click **Verify hash on-chain** — should flip to
   **"Hash verified ✓"** within ~1 second (post-#308 O(1) verify).

**Checkpoint 8:** arcscan screenshot + "Hash verified ✓" confirmation.

---

## Phase 9 — Evidence commit

Assemble all checkpoints into
[`docs/runbooks/arc-testnet-e2e-evidence.md`](./arc-testnet-e2e-evidence.md)
under headings:

```markdown
# Arc Testnet E2E Evidence — <date>

**Wallet:** `0x...`
**Vault:** `0x...`
**Strategy used:** `<name>` (`<id>`)

## Phase 5 — Deploy txs
- Step 1 (createVault): https://testnet.arcscan.app/tx/0x...
- Step 2 (setAgent):    https://testnet.arcscan.app/tx/0x...
- Step 3 (USDC.approve): https://testnet.arcscan.app/tx/0x...
- Step 4 (deposit + setTargetAllocations): https://testnet.arcscan.app/tx/0x...

## Phase 6 — Portfolio verification
Screenshot showing 4-tile header + new vault card.

## Phase 7 — First rebalance trace
- timestamp: 2026-MM-DDTHH:MM:SS UTC
- trace_id: ...
- reasoning excerpt: "[REVEAL]..." OR "Swap skipped — thin pool..."
- arc_tx_hash (if rebalance): 0x... → https://testnet.arcscan.app/tx/0x...

## Phase 8 — Verify on-chain
- Hash verified: ✓
- arcscan Status: Success
```

Commit this file to a fresh branch off `main`, open PR, link from README
and ARC-OSS-SHOWCASE primitive #3. After sign-off, merge.

---

## Recovery paths

| Symptom | Likely cause | Action |
|---------|--------------|--------|
| MetaMask won't switch accounts | Extension state issue | Use a different existing account on Arc Sepolia; note in evidence |
| Site hero still says "Linus for quantitative finance" | Brand refresh (#357) hasn't deployed yet | Wait ~3 min, reload — or proceed (cosmetic) |
| Generate stream stalls > 90s | LLM rate-limit or backend hiccup | "New Generation" → retry; second failure → file an issue |
| Deploy Step 1 reverts on `createVault` | Stale contract address (Part 1 of #342 was deferred) | Capture the revert error verbatim — may need a contract address probe |
| Portfolio shows 0 vaults after deploy | Architectural fix didn't engage | Capture vault address from tx receipt + wallet address — query `getVaultsByCreator` directly to triage |
| "Verify hash on-chain" hangs | Verify endpoint timeout | Refresh + retry; if persistent, capture the response body |

---

## Why this exists

The pitch claim is "the agent autonomously rebalances on-chain on Arc and
the user owns the vault." SPEC-1's evidence trail makes the claim
replicable for any judge or auditor — every tx hash above resolves to a
real `Status: Success` transaction on Arc testnet that can be inspected
without trusting our screenshots.

Live URL: <https://archimedes-arc.app/>
Chain ID: 5042002 (Arc Sepolia testnet)
USDC address: `0x3600000000000000000000000000000000000000`
