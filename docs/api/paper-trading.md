# Paper Trading API

> **status:** current
> **owner:** Dan Browne
> **updated:** 2026-08-20

`/api/paper/*` — the verdict → paper-trade path (MVP). Deploying a strategy to
paper trading snapshots its DSL spec and forward-runs the **same graded
engine** one appended bar per day, so a paper track record tracks the graded
backtest semantics by construction rather than a separate simulated broker.
This surface never moves funds — it exists to let a strategy earn a forward
track record before anyone considers a real deployment, and its ownership,
rate limits, and error handling all read that way (an account session is
sufficient proof; there is no fresh-wallet-signature gate here).

**Ledger is append-only, never rewritten.** Each daily advance replays the
strategy's full history and diffs it against what is already written. New
dates are appended; dates the ledger already has are **never overwritten**
even when a fresh replay disagrees with the stored value (an upstream data
restatement — e.g. yfinance revising history) — the deployment is instead
stamped `drift_detected_at` and the disagreement is logged loudly. A paper
track record that could silently rewrite its own past is exactly the failure
this product exists to oppose; see `services/paper_trading.py` module
docstring.

**Auth model.** Every route requires a Better Auth account session (the
`better-auth.session_token` cookie via `require_current_user`) — ownership is
`owner_user_id` only. `owner_wallet` is recorded on deploy as **provenance
only** (the caller's linked wallet at that moment, if any) and never grants
access; an account-only user with no linked wallet can deploy normally.
Examples below assume an authenticated cookie jar at `/tmp/session.jar`.

## Endpoints

### POST /api/paper/deployments
Deploy a strategy to paper trading — snapshots its DSL spec and opens a new
ledger. | **Auth**: account-session | **Flags**: rate limit `10/minute`
(disabled under `TESTING`)

Request: `{strategy_id: str}`.
Response (201): a `deployment_summary` object — see [Deployment summary shape](#deployment-summary-shape) below. The
first daily advance runs synchronously and best-effort: if it fails (e.g. a
transient data-source hiccup) the deployment is still created with zero rows,
and the scheduled `paper_advance_loop` backfills it from `deployed_at` on its
next pass.
Errors:
- `422` `strategy_id is required` — missing/blank body field.
- `404` `Strategy not found` — unknown strategy, or not visible to the caller
  (private-until-published rules, #850).
- `422` `no_strategy_spec` — the strategy has no machine-readable DSL spec to
  paper-trade.
- `422` `invalid_strategy_spec` — the stored spec fails DSL validation.

```bash
curl -s -X POST https://archimedes-arc.com/api/paper/deployments \
  -b /tmp/session.jar -H "Content-Type: application/json" \
  -d '{"strategy_id": "<strategy_id>"}'
```

### GET /api/paper/deployments
List the caller's own paper deployments, newest first. | **Auth**:
account-session

Request: none.
Response: `{deployments: [deployment_summary, ...]}` — every deployment
owned by the caller (`owner_user_id`), most recently created first.
Errors: none beyond the global 401.

```bash
curl -s -b /tmp/session.jar https://archimedes-arc.com/api/paper/deployments
```

### GET /api/paper/deployments/{deployment_id}
Get one owned deployment's current summary. | **Auth**: account-session

Request: path `deployment_id`.
Response: a `deployment_summary` object.
Errors: `404` `Paper deployment not found` — missing, or owned by a different
account (existence is private — a wrong-owner lookup 404s exactly like an
unknown ID, never a 403).

```bash
curl -s -b /tmp/session.jar https://archimedes-arc.com/api/paper/deployments/<deployment_id>
```

### POST /api/paper/deployments/{deployment_id}/stop
Stop an owned deployment — sets its status to `stopped`; the ledger already
written is untouched. | **Auth**: account-session

Request: path `deployment_id`.
Response: `{deployment_id: str, status: "stopped"}`.
Errors: `404` `Paper deployment not found` — missing, or not owned by the
caller.

```bash
curl -s -X POST -b /tmp/session.jar https://archimedes-arc.com/api/paper/deployments/<deployment_id>/stop
```

## Deployment summary shape

`POST /deployments`, `GET /deployments`, and `GET /deployments/{id}` all
return this same shape (a bare list of it for `GET /deployments`):

| Field | Type | Meaning |
|---|---|---|
| `deployment_id` | str | The deployment's ID. |
| `strategy_id` | str | The strategy this deployment paper-trades. |
| `deployed_at` | str (date) | The date the snapshot was opened; the ledger only ever holds dates `>= deployed_at`. |
| `status` | str | `active` or `stopped`. |
| `days` | int | Number of daily ledger rows written so far. |
| `total_return` | float | Compounded return over the ledger, `equity_index - 1.0` on the last row (`0.0` with zero rows). |
| `drift_detected_at` | str (datetime) \| null | Set the first time a fresh replay disagreed with an already-written ledger row; never cleared automatically once set. |
| `series` | array | One entry per ledger day, oldest first — see below. |

Each `series` entry:

| Field | Type | Meaning |
|---|---|---|
| `date` | str (date) | The bar's date. |
| `daily_return` | float | That day's portfolio return (dollar-sleeve-weighted across the strategy's asset universe, faithful to the graded backtest path). |
| `equity_index` | float | Cumulative compounded equity starting from `1.0` at deploy — this is the sparkline series a client plots directly. |

```json
{
  "deployment_id": "dep_abc123",
  "strategy_id": "strat_xyz",
  "deployed_at": "2026-08-01",
  "status": "active",
  "days": 3,
  "total_return": 0.0142,
  "drift_detected_at": null,
  "series": [
    {"date": "2026-08-01", "daily_return": 0.004, "equity_index": 1.004},
    {"date": "2026-08-02", "daily_return": -0.001, "equity_index": 1.003},
    {"date": "2026-08-03", "daily_return": 0.0112, "equity_index": 1.0142}
  ]
}
```

---

See also: `backend/archimedes/services/paper_trading.py` (replay + ledger
mechanics) and `docs/specs/strategy-dsl-spec.md` (the DSL the snapshot spec
must validate against).
