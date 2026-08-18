# ADR: Migrate production to Dan's own AWS account

> **Audience:** Archimedes team (decision owner: Dan Browne, AWS account holder)
> **Status:** Accepted
> **Date:** 2026-06-24
> **Owner:** Dan Browne
> **Supersedes:** —
> **Superseded-by:** —
> **Question being decided:** After the hackathon, who owns the production AWS infrastructure?
> **Related:** CLAUDE.md § "Project / Status" (2026-06-24 revision), [`docs/architecture.md`](../architecture.md), `infra/`.

## TL;DR

**Migrate production off the shared Agora hackathon account onto Dan's own AWS account (`037613907429` / `us-east-1`).** Dan owns the smart contracts and on-chain integration; owning the AWS account that runs the oracle + backend makes the ownership surface congruent and unblocks the Lepton Sprint without waiting on Agora-managed infrastructure. The live app stays at `https://archimedes-arc.com/`; GitHub Actions auto-deploy is re-pointed and ON.

## Context

The hackathon (May 11–25) provided a shared cloud account for the demo. Post-event, continuing on it meant: ambiguous ownership (Chuan, who held admin, is stepping back), shared blast radius with other teams, and dependence on Agora-managed approvals — too slow for the Lepton Sprint's continuous-ship cadence. Dan was already the de facto owner of contracts, the `backend/archimedes/chain/` layer, and deployment. Concentrating AWS ownership with him clarifies responsibility. A related cleanup: the prior `.app`/`.com` domain split caused the Circle passkey rpId bug (now fixed by standardizing on `.com`).

## Decision

**Migrate the full stack to Dan's account in `us-east-1` (complete 2026-06-24):**
1. **Ownership** — Dan is the human owner of record. Teammates get IAM users on request (`SecurityAudit` + `ViewOnlyAccess`, MFA, keys via a secure channel — never Discord/email). Long-term: migrate to IAM Identity Center (no long-lived keys).
2. **What moved** — CloudFront → WAF → ALB → the serving tier, Postgres, Redis, deploy credentials, all re-pointed. At the time of the migration the serving tier was a single EC2 box running docker-compose; only the hosting account changed. *(Mechanism note, 2026-07-28: the serving tier is now an ECS Fargate service — see [`ec2-to-ecs-fargate-cutover.md`](ec2-to-ecs-fargate-cutover.md). The account decision recorded here is unaffected.)*
3. **CI/CD** — GitHub Actions re-pointed to the new account; auto-redeploy on every merge to `main`.
4. **Secrets** — in AWS Secrets Manager / SSM (no plaintext in git or Actions), fetched at boot.
5. **Terraform state** — S3 backend, versioned + encrypted (SSE-S3) + TLS-only bucket policy + S3-native locking; treated as a secrets store with scoped IAM read.

## Consequences

### Positive
- **Clear ownership + autonomous ops** — Dan approves all infra changes; the sprint ships continuously without external approvals.
- **Clean audit + cost control** — full CloudTrail; Dan controls the bill + budgets; clean separation from other teams.

### Negative / costs we accept
- **Bus-factor concentration on Dan** (AWS owner + sole contract deployer + on-chain owner), who is evenings/weekends-only. Mitigated by **Bogdan (`mnemonik-dev`) as contract/on-chain reviewer**, **Marten as ops backup**, `ViewOnlyAccess` IAM for teammates, and documented runbooks.
- **AWS cost is now Dan's personal bill** for the sprint (his explicit commitment) until a funding event absorbs it.

## Alternatives considered
- **Keep the shared Agora account — rejected:** ambiguous post-event ownership, shared blast radius, and too slow to iterate.
- **A new co-owned team account — rejected:** heavyweight (legal entity, billing) and consensus-slow for a fast sprint; Dan is the de facto owner already, and Bogdan/Marten can step in for review.

## Ratification

Adopted and completed 2026-06-24. Reflects the post-hackathon reality (Chuan stepping back, Dan taking full ownership). See [build-on-deploy](build-on-deploy-main-only.md) for the deploy mechanics this account hosts.
