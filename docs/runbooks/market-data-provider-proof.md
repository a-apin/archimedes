# Market-data provider — wiring the Tiingo token and proving the pull

> **status:** current
> **owner:** Dan Browne
> **updated:** 2026-09-01
> **superseded-by:** —

**Scope:** getting `TIINGO_API_TOKEN` onto the running backend container, and producing the
verified-pull record that [`../claims-ledger.md`](../claims-ledger.md)'s "paid analysis runs
on licensed data" row is waiting on. Written for
[#1798](https://github.com/aprin-labs/archimedes/issues/1798); the split it serves is decided
in [`../adr/market-data-sourcing.md`](../adr/market-data-sourcing.md).

**Read the ADR before the flip step.** This page is the procedure. The question of *which
surface runs on which vendor, and what has to be true before money moves*, is the ADR's, and
the flip at the end of this page is the point where that decision becomes real.

---

## What was wrong, in one paragraph

`/archimedes/prod/TIINGO_API_TOKEN` has existed in SSM (SecureString, `us-east-1`, account
`037613907429`) since 2026-08-31. Nothing read it, because no container was ever handed it:
the backend task definition carried no `TIINGO_*` secret at all. `market_data_provider`
refuses to fall back to yfinance when the token is missing — it raises
`TiingoAPIKeyMissingError` at provider construction, deliberately, so that a run can never
carry a "licensed data" provenance it did not earn. So the gap was invisible: everything
worked, because everything was still on yfinance.

## The two paths, and which one actually ships

There are two registrars for the backend task definition, and
[#1799](https://github.com/aprin-labs/archimedes/issues/1799) is the issue that says so:

| Path | Who registers | When it takes effect |
|---|---|---|
| `infra/ecs.tf` → `aws_ecs_task_definition.backend` | `terraform apply` | only when someone applies |
| `deploy.yml` → `.github/scripts/ecs_rewrite_task_def.py` | every CI deploy of `main` | on the next deploy |

The secret is declared on **both**. That is not belt-and-braces: `aws_ecs_service.backend`
carries `lifecycle { ignore_changes = [task_definition, desired_count] }`, so a terraform
apply registers a new revision but does **not** change which revision the service runs. The
deploy is the path that puts the token in front of the process.

**So there is no risky apply to schedule.** Merging and letting `main` deploy is sufficient.
The terraform entry is the declared baseline, so that whenever the task-def resource is next
applied it does not silently un-wire what the pipeline pinned.

## Step 1 — deploy, then confirm the secret is on the live revision

After the merge commit deploys:

```bash
aws ecs describe-services --cluster archimedes-cluster --services archimedes-backend \
  --query 'services[0].taskDefinition' --output text
```

Take the revision that returns and read its backend container's secrets:

```bash
aws ecs describe-task-definition --task-definition <that-arn> \
  --query "taskDefinition.containerDefinitions[?name=='backend'].secrets[].name" --output text
```

Expected: the eight names, `TIINGO_API_TOKEN` among them. If it is absent, the deploy did not
run the rewrite script — check the `deploy-ecs` job's "Register a new task-definition
revision" step, not this page.

No IAM change is needed and none was made. The execution role's SSM statement is a prefix
wildcard over `parameter/archimedes/prod/*`, which already authorises the read, and its
`kms:Decrypt` statement already covers SecureStrings fetched via SSM. That property is pinned
by `test_execution_role_policy_is_a_prefix_wildcard` in
[`../../backend/tests/test_ecs_backend_secrets.py`](../../backend/tests/test_ecs_backend_secrets.py).

Then confirm the *process* has it, not just the definition — without ever printing the value:

```bash
aws ecs execute-command --cluster archimedes-cluster \
  --task <task-id> --container backend --interactive \
  --command 'sh -c "test -n \"$TIINGO_API_TOKEN\" && echo present:${#TIINGO_API_TOKEN} || echo MISSING"'
```

## Step 2 — the proof pull

[`../../scripts/verify_market_data.py`](../../scripts/verify_market_data.py) pulls `SPY` for a
fixed, fully-historical window — `2026-06-01` through `2026-06-12` inclusive, ten US trading
days with no market holiday in it — through the same `get_provider()` seam every backtest
uses, and reports the vendor that answered.

**It does not run inside the backend container.** `backend/Dockerfile` COPYs `backend` into
`/app`, so `/app/scripts` is `backend/scripts`; the repo-root `scripts/` tree is not in the
image. That is why step 1 above checks the container's env separately. Run the pull from a
checkout instead, with the token in the process environment and `--no-cache` (which skips the
`asset_daily_bars` wrapper and so needs no database):

```bash
export TIINGO_API_TOKEN=$(aws ssm get-parameter \
  --name /archimedes/prod/TIINGO_API_TOKEN --with-decryption \
  --query Parameter.Value --output text)
MARKET_DATA_PROVIDER=tiingo python scripts/verify_market_data.py --no-cache
```

The output has this shape — the block below is the format, **not** a recorded run:

```
provider     : tiingo
symbol       : SPY
window       : 2026-06-01 .. 2026-06-13 (end-exclusive)
cache        : bypassed (--no-cache)
rows         : 10
first bar    : 2026-06-01
last bar     : 2026-06-12
result       : OK — tiingo served the known 10-bar window
```

Exit `0` on that; exit `1` on anything else (wrong count, wrong dates, no bars, or a provider
that raised — which is the shape a missing token takes). Dropping `--no-cache` reads through
the production cache wrapper instead and needs a database; the cache is per-vendor, so the
`provider` line is honest either way, but a warm cache means a green run does not by itself
prove the vendor is reachable right now.

**Paste your real output on [#1798](https://github.com/aprin-labs/archimedes/issues/1798)** —
with the `provider` line as it actually printed. That is the verified-pull record; the
claims-ledger row stays `PENDING ADR MERGE` until a run of this shows `provider : tiingo`
*and* the deployed seam is actually reading it.

## Step 3 — the flip, which is an owner decision with a blast radius

`MARKET_DATA_PROVIDER` is **not** set by this wiring, and a guard in
`test_ecs_backend_secrets.py` (`FORBIDDEN_WHY`, which `FORBIDDEN_NAMES` derives from) fails
if it is added to `infra/ecs.tf` without editing that guard first. That is deliberate, and the
reason is a real hazard rather than caution:

> `MARKET_DATA_PROVIDER` is a **global** switch, not a paid-surface-only one. Today
> `TiingoProvider` implements daily bars only: `get_intraday_quote`,
> `get_intraday_quotes_batch` and `get_series` raise `NotImplementedError`. Setting it to
> `tiingo` therefore also takes the live oracle push's equity fetch — which has no handler for
> that exception, so it raises rather than degrading — plus the VIX / S&P-MA regime reads and
> the Explore history modal. The ADR names this under § Consequences — those surfaces are
> **not cutover-ready**. The split it describes ("Tiingo for paid analysis, yfinance for the
> free Explore viewer") is enforced today by which methods exist, not by a second env var, so
> there is no way to flip only the paid seam with the variables that exist.

Step 2's command is exactly that rehearsal: `MARKET_DATA_PROVIDER=tiingo` set for one
short-lived process, which exercises the token, the adapter and the vendor's real responses
and touches nothing else — the process exits. A green run there is evidence the credential
works and the adapter is correct against live vendor responses; whether
the global flip is acceptable is a separate call, and the honest answer today is that it
needs Tiingo's intraday methods implemented first, or a second variable so the two seams can
diverge.

## Rollback

Nothing to roll back for the wiring itself: an unused secret changes no behaviour, because
nothing reads `TIINGO_API_TOKEN` while `MARKET_DATA_PROVIDER` is unset. If a flip is in
effect and the oracle or Explore starts failing, unset `MARKET_DATA_PROVIDER` (or set it to
`yfinance`) and redeploy. Note the ADR's cache consequence: **a provider flip in either
direction starts cold**, because `asset_daily_bars` reads filter on the vendor that wrote
each row. The first run after a flip is slow, and that is correct behaviour, not a fault.

## Related

- [`../adr/market-data-sourcing.md`](../adr/market-data-sourcing.md) — the split, the
  free-tier-is-for-testing position, and the Tiingo Business plan as a mainnet gate.
- [`../claims-ledger.md`](../claims-ledger.md) — the row this proof is for.
- [`../operations/feature-flag-fliplist.md`](../operations/feature-flag-fliplist.md) —
  `MARKET_DATA_PROVIDER`, `ORACLE_CRYPTO_SOURCE` and `TIINGO_MIN_REQUEST_INTERVAL_S`.
