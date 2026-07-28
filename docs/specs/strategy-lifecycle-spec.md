# Strategy Lifecycle Spec

> **Status:** Drafted 2026-05-22 as Phase 0 of the
> [Spine+ v2 plan](../plans/spine-plus-v2-plan.md). Authoritative for the
> `StrategyRecord.status` enum, Library/Portfolio filters, and every state
> transition in Phases 2-5.
>
> **Lineage:** Re-maps the legacy `CANDIDATE / VALIDATED / LIVE / RETIRED` enum
> in [`backend/archimedes/models/strategy.py`](../../backend/archimedes/models/strategy.py)
> to a richer lifecycle that distinguishes paper-grounded examples from
> user-generated strategies and surfaces time-bound expiry. Pairs with
> [`vault-semantics-spec.md`](./vault-semantics-spec.md).

## Lifecycle states

```
                Generated ── (rigor gate fails) ──→ Rejected
                    │
              (rigor passes)
                    │
                    ▼
                Validated ── (TTL elapsed, no deploy) ──→ Expired
                    │
              (user deploys)
                    │
                    ▼
                Deployed ── (window_start reached + funded) ──→ Active
                                                                  │
                                                          (window_end reached)
                                                                  │
                                                                  ▼
                                                              Completed
```

| State | Definition | Who sets it |
|---|---|---|
| `Generated` | Agent has drafted a strategy from a user brief or selected one from the example library. Not yet rigor-checked. | Agent (Generate flow) |
| `Validated` | Strategy passed all four rigor gates (DSR, PBO, walk-forward OOS Sharpe, look-ahead audit). Ready to deploy. | Rigor gate |
| `Rejected` | Strategy failed at least one rigor gate. Browsable for inspection, not deployable. | Rigor gate |
| `Deployed` | User has created a vault around the strategy; vault is awaiting capital or `window_start`. | User (via vault-creation tx) |
| `Active` | Vault is in its trade window; agent is executing. | Vault contract event (`window_start` reached + funded) |
| `Completed` | Vault's `window_end` has passed; realized P&L recorded. | Vault state machine |
| `Expired` | Strategy was Validated but never deployed within TTL. Inspectable in Library; not deployable. | Background sweeper (cron) |

Per [`vault-semantics-spec.md`](./vault-semantics-spec.md), the Strategy lifecycle
and the Vault lifecycle are **coupled but distinct**: one Strategy may be
`Deployed`/`Active`/`Completed` only via its associated Vault, and that Vault has
its own `Created/Funded/Active/Unwinding/Completed/Expired` states. Strategy state
mirrors the Vault for any deployed strategy; for un-deployed strategies the Vault
states don't apply.

## Field population by transition

| Field | Populated at |
|---|---|
| `id`, `name`, `brief_hash`, `is_example` | Generated (Insert) |
| `description`, `source_arxiv_ids`, `reasoning_trace_hash`, `dsl_payload` | Generated |
| `rigor_verdict` (DSR + PBO + OOS Sharpe + lookahead audit results), `passes_rigor_gate` | Validated **or** Rejected |
| `validated_at`, `ttl_expires_at` | Validated |
| `vault_address`, `deployed_at`, `deployer_wallet` | Deployed |
| `realized_pnl_usdc`, `completed_at`, `closing_trace_hash` | Completed |
| `expired_at` | Expired |

`brief_hash` is the keccak256 of the user's normalized brief — used to detect
duplicate-brief regeneration attempts and de-duplicate in Library.

## Transition triggers

| Transition | Trigger | Effects |
|---|---|---|
| → Generated | Agent emits final `candidate_drafted` SSE event with rigor verdict | DB insert; trace persisted off-chain via `AgentStateStore` (module removed) |
| → Validated | `passes_rigor_gate == true` and all four sub-gates green | `status='validated'`; `ttl_expires_at = now() + TTL` |
| → Rejected | At least one rigor sub-gate fails | `status='rejected'`; reason string surfaced in Library |
| → Deployed | User signs vault-creation tx; backend observes `VaultCreated` event | `status='deployed'`; `vault_address` linked |
| → Active | Vault enters Active state (per vault-semantics-spec) | Mirrored automatically; agent runner begins ticking |
| → Completed | Vault enters Completed state | Mirrored automatically; trigger `/learnings` post-hoc reasoning generation |
| → Expired | Cron sweeper sees `validated_at + TTL < now()` AND `vault_address IS NULL` | `status='expired'`; user can still inspect but not deploy |

## TTL — strategy-expiry semantics

A **Validated** strategy that isn't deployed becomes **Expired** after a TTL
window. Why: market regimes drift; a strategy validated against last week's
volatility regime may not match this week's.

**Default TTL: 72 hours.** Justification:

- Short enough that the validation evidence (rigor metrics computed against
  data up to `validated_at`) is still recent.
- Long enough that a user who generates Friday evening can still deploy Monday
  morning.
- Configurable via `STRATEGY_TTL_HOURS` env var — operator can shrink for live
  demos or extend for long-tail backtests.

**Sweeper:** a 5-minute cron in `chain/oracle_runner.py` (or a new
`scripts/sweep_expired_strategies.py`) runs `UPDATE strategies SET status='expired',
expired_at=now() WHERE status='validated' AND validated_at + interval '72 hours' < now() AND vault_address IS NULL`.

## Mapping from legacy enum

| Legacy (`StrategyStatus`) | New | Notes |
|---|---|---|
| `CANDIDATE` | `Generated` | Same semantic — pre-rigor. Migration: rename. |
| `VALIDATED` | `Validated` | Direct match. |
| `LIVE` | `Active` (when in-vault) | Old `LIVE` was overloaded — sometimes meant "passed validation", sometimes meant "in an active vault". The new split disambiguates. |
| `RETIRED` | `Completed` or `Expired` or `Rejected` | One-to-many. Migration: inspect each `RETIRED` row; classify by whether it ever had `vault_address`. |

**Migration plan** — single idempotent SQL block run on startup (mirroring the
existing `db.py` `ALTER TABLE` pattern):

```sql
ALTER TYPE strategy_status ADD VALUE IF NOT EXISTS 'generated';
ALTER TYPE strategy_status ADD VALUE IF NOT EXISTS 'rejected';
ALTER TYPE strategy_status ADD VALUE IF NOT EXISTS 'deployed';
ALTER TYPE strategy_status ADD VALUE IF NOT EXISTS 'active';
ALTER TYPE strategy_status ADD VALUE IF NOT EXISTS 'completed';
ALTER TYPE strategy_status ADD VALUE IF NOT EXISTS 'expired';
ALTER TABLE strategies ADD COLUMN IF NOT EXISTS ttl_expires_at TIMESTAMPTZ;
ALTER TABLE strategies ADD COLUMN IF NOT EXISTS vault_address TEXT;
ALTER TABLE strategies ADD COLUMN IF NOT EXISTS deployed_at TIMESTAMPTZ;
ALTER TABLE strategies ADD COLUMN IF NOT EXISTS realized_pnl_usdc NUMERIC(20,6);
ALTER TABLE strategies ADD COLUMN IF NOT EXISTS expired_at TIMESTAMPTZ;
UPDATE strategies SET status='generated' WHERE status='candidate';
```

`LIVE` and `RETIRED` rows are kept as-is until manual reclassification (handful
of rows; not worth automating).

## UI surface

- **Library > Generated tab** — shows `Generated | Validated | Deployed | Active |
  Completed | Expired | Rejected`. Default filter: hide Expired + Rejected
  (toggles available).
- **Library > Examples tab** — only `is_example=true` rows; status irrelevant
  (examples are reference, never deployed by users in their name).
- **`/strategy/:id`** — passport page renders the full status pill + state-specific
  CTA (Validated → Deploy; Active → View Vault; Expired → Re-generate; Rejected
  → See why).
- **`/portfolio`** — only `Deployed | Active | Completed` (the user's actual
  vault footprint).
- **`/learnings`** — only `Completed`.

## Acceptance

- Every Library/Portfolio filter maps cleanly to one or more states above.
- Every state transition in Phases 2-5 cites a row in the "Transition triggers"
  table.
- A reviewer can answer "where does an Expired strategy show up in the UI?" by
  reading this doc alone.

## Open questions

1. **TTL value** — is 72h right? Live-demo context may want 24h or even 12h
   so judges see Expired in the Library without long waits.
2. **Re-validation on expiry** — should an Expired strategy be auto-revalidated
   if user wants to deploy it? (Current spec: no — user regenerates fresh.)
3. **Rejected strategy provenance** — do we show the full reasoning + rigor
   verdict for Rejected strategies, or just a summary? (Current spec: full,
   because that's the honesty wedge.)
