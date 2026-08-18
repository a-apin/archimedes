# Runbooks — index, and what is missing

> **status:** current
> **owner:** Dan Browne
> **updated:** 2026-07-28
> **superseded-by:** —

A runbook is a procedure someone follows **under pressure**, usually during an incident.
That is the test for whether something belongs here: commands, expected output, and a way
back. If it argues rather than instructs, it belongs somewhere else — see
[`../CONVENTIONS.md`](../CONVENTIONS.md) § 1.

This index covers `docs/runbooks/`. Deploy- and infrastructure-level procedures live next to
the Terraform that owns them, under `infra/runbooks/`, and are listed here too — a reader in
an incident should not have to know which tree a procedure lives in.

## Runbooks that exist

| Runbook | Owner | What it is for |
|---|---|---|
| [`operations.md`](operations.md) | Dan Browne | Run the stack locally and in production: services, RPC deep-dive, LLM backends, security notes. The general-purpose starting point. |
| [`arc-testnet-e2e.md`](arc-testnet-e2e.md) | Dan Browne | End-to-end smoke test against Arc testnet — the check that the on-chain path is alive. |
| [`arc-testnet-e2e-evidence.md`](arc-testnet-e2e-evidence.md) | Önder Akkaya | Replayable on-chain evidence for SPEC-1. Evidence, not a procedure; kept alongside the test that produces it. |
| [`spec-1-walkthrough.md`](spec-1-walkthrough.md) | Dan Browne | SPEC-1 user-journey walkthrough, start to finish. |
| [`t3.2-contract-redeploy.md`](t3.2-contract-redeploy.md) | Dan Browne | Contract redeploy procedure and secret handling. |
| [`github-security-toggles.md`](github-security-toggles.md) | Dan Browne | Repository security settings and how to change them. |
| `infra/runbooks/ecs-fargate-cutover.md` | owner of `infra/` | The 2026-07-09 EC2 → ECS Fargate cutover, **including the rollback procedure**. This is the closest thing to a break-glass path that currently exists. |
| `infra/runbooks/disaster-recovery.md` | owner of `infra/` | Recovery from data-store and account-level loss. |

Historical, **do not execute**: [`../archive/deployment-runbook.md`](../archive/deployment-runbook.md)
— the EC2-era manual/break-glass deploy runbook. Its procedure routes traffic to instance
`i-01803d3abc271d39b`, which was detached from the ALB target group during the Fargate
cutover, so following it during an incident operates on a host serving no traffic. It is kept
for its incident-response history and diagrams.

## Not yet written

Naming a gap is worth more than leaving it silent. An operator who searches this directory
during an incident and finds nothing cannot tell "no runbook" from "I searched badly."

### Fargate-era break-glass procedure — **MISSING**

**Owner:** whoever owns `infra/`. **Status:** not started. **Blocking:** nothing but the
writing — the underlying capability exists, it is just not written down.

Archiving [`../archive/deployment-runbook.md`](../archive/deployment-runbook.md) left the repo
with **no break-glass runbook at all**. `infra/runbooks/ecs-fargate-cutover.md` § rollback
covers the planned reversal of one specific migration; it is not a procedure for "production
is down at 03:00 and CI cannot deploy." Until this is written, that scenario has no
documented answer.

What it must cover, at minimum:

1. **Roll the ECS task-definition revision.** How to identify the last known-good revision,
   how to force a service update onto it, and what to do when the rollback target itself is
   the broken one. Include the deregistration/draining behaviour so the operator knows how
   long to wait rather than re-running the command.
2. **The ALB target-group state.** How to read current target health, what a healthy set
   looks like versus a draining or empty one, and how to confirm the new tasks actually
   registered. **This is precisely what the archived runbook got wrong** — it operated on a
   detached instance and gave no way to notice. The replacement must make target-group state
   something the operator verifies, not assumes.
3. **The CloudFront invalidation.** When a task rollback alone is insufficient because the
   edge is still serving cached assets, which paths to invalidate, and how long propagation
   actually takes before the operator should conclude it did not work.
4. **How you know it worked.** Explicit success criteria: which health endpoint, which
   CloudWatch alarms should clear, which target counts, and how long to watch before
   declaring the incident over. A break-glass procedure without a verification step is how
   you get a second incident on top of the first.

Write it under `infra/runbooks/`, next to the Terraform whose state it depends on, and link it
from this index and from [`../README.md`](../README.md) in the same commit.
