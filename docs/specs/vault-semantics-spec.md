# Vault Semantics Spec

> **Status:** Drafted 2026-05-22 as Phase 0 of the
> [Spine+ v2 plan](../plans/spine-plus-v2-plan.md). Authoritative for Phase 4
> implementation; supersedes any conflicting language in earlier docs.
>
> **Lineage:** Builds on the multi-asset NAV `Vault.sol` deployed Day-10
> (Chuan's stack — see [`chuan-architecture-survey.md`](../archive/agora-2026-05/chuan-architecture-survey.md))
> and the 1:1 strategy↔vault decision locked in the Spine+ v2 plan.

## Definition

**A Vault is a time-bound execution container that holds exactly one strategy,
the user's capital deposited against that strategy, and the trade window during
which the agent may act on it.** On-chain it is a `Vault.sol` ERC-4626 multi-asset
NAV contract; off-chain it is a row in `vaults` with a foreign key to the strategy.

A Vault is **not** a perpetual portfolio. It is **not** multi-strategy. It does
**not** outlive its trade window. These constraints are what make the on-chain
provenance trail tractable: a fixed strategy + a fixed window means every
rebalance, every reasoning trace, and every realized P&L attributes
unambiguously to one strategy hash.

## Lifecycle

```
Created → Funded → Active → Unwinding → Completed
                            ↘
                              Expired (un-funded at window-open)
```

| Transition | Trigger | What happens |
|---|---|---|
| → Created | User clicks "Deploy as vault" on a strategy passport, signs vault-creation tx | `VaultFactory` deploys a new `Vault.sol`; row inserted in `vaults` with `state='created'`, `strategy_id=X`, `window_start`, `window_end`, `target_capital_usdc` |
| → Funded | User deposits USDC ≥ `target_capital_usdc` before `window_start` | Vault state flips to `funded`; agent runner is notified |
| → Active | Block timestamp reaches `window_start` AND state is Funded | Agent calls `setTargetAllocations()` with the strategy's initial weights; first trade executes via `AMMRouter` |
| → Unwinding | Block timestamp reaches `window_end` − `unwind_buffer` (default 1h) | Agent ceases new entries; existing positions begin closing back to USDC |
| → Completed | Vault fully unwound to USDC AND block timestamp ≥ `window_end` | User can withdraw via `redeem()`; row marked `completed` with `realized_pnl_usdc` |
| → Expired | Block timestamp ≥ `window_start` AND state is Created (not Funded) | Vault contract self-disables; row marked `expired` for post-hoc inspection |

## Trade window

Two timestamps, both set at creation and immutable thereafter:

- **`window_start`** — earliest time the agent may execute the strategy's initial
  allocation. Before this, the vault is dormant even if funded.
- **`window_end`** — latest time the agent may hold non-USDC positions. The unwind
  buffer (`window_end − 1h` by default) is when the agent stops opening new
  positions and starts closing existing ones.

The window is **fixed at creation** because the strategy's backtest was conducted
over a specific horizon (e.g., "13-week treasury bill alternative, Q3 2026"). A
strategy with a 13-week passport deployed for 52 weeks has lost its evidence base.

## Re-use after completion

**Vaults are single-use.** After `Completed`, the user may:
- Withdraw their (now-USDC) capital, **or**
- Roll into a new vault by generating a fresh strategy.

The contract itself remains on-chain (immutable history) but accepts no new
deposits. **No "redeploy same strategy into same vault" path** — the new market
regime warrants a fresh rigor check.

## Agent interaction during the window

1. **At `window_start`:** Agent calls `setTargetAllocations()` once with the
   strategy's initial weights (e.g., 60% TLT, 40% USDC). `AMMRouter` executes
   the resulting swaps. Reasoning trace anchored on `ReasoningTraceRegistry`.
2. **During Active:** Agent ticks every 5 minutes. It rebalances **only if** the
   strategy DSL says so — e.g., a SMA200 strategy rebalances on cross events; a
   buy-and-hold does not. Each rebalance gets its own trace.
3. **At `window_start + unwind_buffer`:** Agent begins selling all non-USDC
   positions. New entries forbidden. Trace anchors the unwind decision.
4. **At `window_end`:** Vault marked `Completed`. Final NAV recorded in
   `realized_pnl_usdc`. Post-hoc reasoning published to `/learnings`.

## Failure modes

| Failure | Behavior |
|---|---|
| Window passes mid-trade | The in-flight `AMMRouter` tx completes; no new entries; unwind proceeds on next tick. |
| Oracle goes stale (> 2× expected interval) | Agent halts trading, anchors a `regime_unknown` trace; user notified on Portfolio page; vault holds last state until oracle recovers or window ends. |
| AMM has no liquidity | Agent retries with smaller size, then with a different route; if both fail, anchors a `liquidity_failure` trace and halts. Capital is safe; just inactive. |
| User under-deposits | If deposit < `target_capital_usdc` by `window_start`, the agent executes against whatever USDC is on hand (no minimum enforced). Strategy proceeds proportionally. |
| Agent process dies | Vault state on-chain is untouched. Restart of `oracle_runner` resumes from the next 5-minute tick. No double-execution because traces are idempotent. |

## On-chain artifact

- **Contract:** `Vault.sol` (multi-asset NAV ERC-4626, deployed Day-10)
- **Factory:** `VaultFactory.sol` (deploys per-user vaults)
- **Trace anchor:** `ReasoningTraceRegistry.sol`
- **Allocation events:** emitted on every `setTargetAllocations()` call;
  consumed by the backend `oracle_runner` for off-chain mirroring.

## Acceptance

A reviewer can derive every Phase 4 implementation decision from this doc:

- Vault row schema (`state`, `window_start`, `window_end`, `target_capital_usdc`,
  `realized_pnl_usdc`, `strategy_id`)
- State transition rules (cron-driven for time-based; event-driven for capital-based)
- UI states on `/portfolio` and `/strategy/:id` (each vault renders one of the six
  lifecycle states)
- Failure UX (every failure mode above maps to one user-visible status pill)

## Open questions (for Marten + Chuan review)

1. **Unwind buffer default** — is 1 hour right for short-horizon strategies?
   A 4-week vault and a 52-week vault probably want different buffers.
2. **Vault re-use ever?** — current spec says no. If user feedback strongly
   wants "re-deploy same strategy," we'd add a `Created from {parent_vault_id}`
   link rather than literally reusing the contract.
3. **Under-deposit minimum** — should we enforce a floor (e.g., $100 USDC) so
   the agent isn't trading dust?
