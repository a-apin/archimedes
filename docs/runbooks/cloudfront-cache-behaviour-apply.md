# Applying a CloudFront cache-behaviour change

> **status:** current
> **owner:** Dan Browne
> **updated:** 2026-09-03
> **superseded-by:** —

A change to `infra/cloudfront.tf`'s cache behaviours does **nothing** when the PR merges.
CI never runs `terraform apply` — `infra-gate.yml` is `fmt -check` + `validate` only,
deliberately (a plan needs credentials and reads a state file that holds a private key).
Until someone runs `infra/apply.sh --apply`, the merged file describes an edge configuration
that is not live, and the tests that guard it are asserting about the *repo*, not about
production.

Since #1799 CI *can* run a plan, in one place: `terraform-drift.yml`, a separate advisory
workflow on a separate read-only role, which reports drift and still applies nothing. See
[`terraform-apply-and-task-definition-ownership.md`](terraform-apply-and-task-definition-ownership.md)
for that and for the ECS task-definition ownership split, which changes what an `infra/ecs.tf`
edit means but leaves everything on this page intact.

This runbook is the step between those two facts. It is written for the pending change
first, then generalised.

## Pending: issue #1768 — `/app` and `/sign-in*` on CachingDisabled

**What merged.** Three `ordered_cache_behavior` blocks — `/app/*`, `/app`, `/sign-in*` —
bound to `data.aws_cloudfront_cache_policy.caching_disabled`, the `all_viewer` origin
request policy, and the same response headers policy the default behaviour uses. They sit
after `/static/*` and ahead of `*.js` / `*.css`.

`/app/*` and `/app` cover more than the gated pages. The anonymous-browse carve-outs of
#1194 revision d — bare `/app`, `/app/explore`, `/app/leaderboard`, `/app/corpus`,
`/app/strategy/*` — are public and ungated at nginx, and they come off the 60s `html`
policy too. That is the owner's ruling (2026-09-01, on the review of PR #1772), not an
oversight: it costs an origin hit per anonymous visitor for a ~4 KB static shell, and it
buys the guarantee that promoting a carve-out to gated later cannot reintroduce the cached
anonymous 302.

**Why it is not already fixed.** PR #1767 shipped `Cache-Control: private, no-store` from
nginx on the gated `/app` locations and the `@sign_in` redirect, and that is live — the
2026-09-01 ping-pong is stopped today. The behaviours close the structural half: the origin
header is opt-in per response, in a different file, and any new gated path (or an edit that
drops the header) puts the edge back in the business of replaying one viewer's redirect to
another.

### 1. Preconditions

```bash
aws sts get-caller-identity     # must be account 037613907429
ls infra/terraform.tfvars       # must exist — apply.sh refuses without it
```

The guards that describe the intended end state should be green on the merge commit:

```bash
cd backend && pytest tests/test_cloudfront_session_paths_uncached.py \
                     tests/test_cloudfront_health_uncached.py \
                     tests/test_nginx_gated_responses_uncached.py -q
```

### 2. Plan, and read it

```bash
infra/apply.sh          # plan is the default; --apply is a separate, later step
```

**Expected:** exactly three new cache behaviours on the distribution and nothing else.

One rendering caveat, so it is not mistaken for scope creep: the three blocks are inserted
in the *middle* of the behaviour list, ahead of `*.js` / `*.css`. Terraform may render that
index shift as changes to the trailing behaviours rather than as three clean insertions. If
it does, check that every rendered change is a re-ordering only — the `path_pattern`,
`cache_policy_id` and `origin_request_policy_id` values before and after must be the same
set. Three new patterns appear; no existing pattern's policy changes.

```
  # aws_cloudfront_distribution.main will be updated in-place
  ~ resource "aws_cloudfront_distribution" "main" {
      + ordered_cache_behavior {
          + path_pattern               = "/app/*"
          + cache_policy_id            = "<the Managed-CachingDisabled id>"
          ...
        }
      + ordered_cache_behavior { + path_pattern = "/app"      ... }
      + ordered_cache_behavior { + path_pattern = "/sign-in*" ... }
    }

Plan: 0 to add, 1 to change, 0 to destroy.
```

Read the summary line first: **`1 to change`, nothing to add, nothing to destroy**, and the
one resource is `aws_cloudfront_distribution.main`. Any other resource in the plan — an
ECS task definition, a security group, an ACM certificate — means the working tree is
carrying something other than this change, or `terraform.tfvars` drifted. Stop and find out
which; do not apply through it.

### 3. Apply

```bash
infra/apply.sh --apply      # interactive confirmation; --yes skips it, don't
```

The distribution goes to `InProgress` and takes roughly 3–5 minutes to reach `Deployed`
across the edge:

```bash
aws cloudfront get-distribution --id "$(cd infra && terraform output -raw cloudfront_distribution_id)" \
  --query 'Distribution.Status' --output text
```

### 4. Purge what the old behaviour already cached

The new behaviour stops the edge caching *new* responses. Objects cached under the old
default behaviour can still be served until they age out, so invalidate them once:

```bash
DIST=$(cd infra && terraform output -raw cloudfront_distribution_id)
aws cloudfront create-invalidation --distribution-id "$DIST" \
  --paths '/app*' '/app/*' '/sign-in*'
```

`/app*`, not `/app` — the `html` cache policy sets `query_string_behavior = "all"`, so every
distinct query string is a distinct cache entry and an un-wildcarded path invalidates only
the bare URL. (Same reasoning as `deploy.yml`'s "Invalidate CloudFront" step. The incident
mitigation on 2026-09-01 was invalidation `I9XCS33QBAVEUFMJRO9R0NJLAU` on these paths.)

### 5. Verify against production

```bash
# Anonymous: gated /app must 302 and must NEVER be a cache hit, on any repeat.
for i in 1 2 3; do
  curl -sSI https://archimedes-arc.com/app/generate | grep -iE '^HTTP/|^x-cache|^cache-control'
done
```

Expected on every iteration: `302`, `x-cache: Miss from cloudfront` (CachingDisabled never
produces a `Hit`), and `cache-control: private, no-store` from nginx (#1767 — both halves
should now be visible on the same response). A `Hit from cloudfront` on any repeat means the
apply did not take or an invalidation is still propagating.

Then the public carve-out, which must also be a `Miss` — deliberately, per the ruling
above. Twice, because one `Miss` proves nothing:

```bash
for i in 1 2; do
  curl -sI https://archimedes-arc.com/app/explore | grep -iE '^HTTP/|^x-cache'
done
```

Expected on both: `200` and `x-cache: Miss from cloudfront`. A `Hit` on the second means a
carve-out has been put back on the `html` policy — check
`backend/tests/test_cloudfront_session_paths_uncached.py::TestThePublicCarveOutsAreDeCachedOnPurpose`,
which exists to make that a red test rather than a surprise here.

Then the real check, which needs a browser: sign in at `https://archimedes-arc.com/sign-in`
and confirm you land on `/app/...` once, without the address bar bouncing. The failure this
closes is a loop, not a status code.

### 6. If it goes wrong

The dangerous failure mode is not "still cached" — it is **`/app` signed-out for everyone**,
which happens if the behaviours end up without the `all_viewer` origin request policy:
CloudFront then forwards no cookies, `auth_request /_auth_session` sees an anonymous request
from every viewer, and every signed-in user is bounced to `/sign-in`.

```bash
git revert <merge sha> && infra/apply.sh && infra/apply.sh --apply
```

Reverting restores the previous behaviour list; the distribution converges in the same 3–5
minutes. The 2026-09-01 defect is *not* reintroduced by the revert on its own, because
#1767's origin `no-store` is a separate change in the nginx image and stays live.

## Generalising

For any future cache-behaviour edit, the shape is the same and only step 2's expectation
changes:

1. State the expected plan **before** running it — how many behaviours, on which resource.
2. Plan, and compare against that sentence rather than skimming for red text.
3. Apply, wait for `Deployed`.
4. Invalidate the paths whose *old* cached copies are now wrong (wildcard them if the cache
   policy forwards query strings).
5. Verify from outside: `x-cache` on repeat requests, then the user journey the behaviour
   exists to protect.

Two properties of this file are load-bearing enough to be guarded rather than trusted, both
by text-parsing tests that need no AWS: the behaviour list's **order** (CloudFront takes the
first matching pattern, not the most specific) and each behaviour's **cache policy id**.
`terraform validate` accepts a behaviour bound to the wrong policy or ordered behind a
pattern that swallows it — see `backend/tests/test_cloudfront_session_paths_uncached.py` and
`backend/tests/test_cloudfront_health_uncached.py`.
