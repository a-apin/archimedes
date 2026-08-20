# Cluster 5 — metering + premium tier (B2 · B5)

**Metering is the prerequisite for selling anything.** Two doc items, one file open
(`generate_routes.py`, 439 lines — readable whole) plus one new module.

Read [README](README.md) session rules first.

## The bug, precisely

`enforce_generation_quota()` opens with `if wallet: return`. Authentication on generation
is unconditional (the `REQUIRE_SIWE_FOR_GENERATION` opt-out was retired and deleted
2026-08-19), so every live caller has a wallet, so **the cap never applies to
anyone** and `WALLET_LESS_GENERATION_DAILY_CAP=5` is dead config (#1199).

## The load-bearing technical call

**Build `services/metering.py` on `marketplace/spend_cap.py`'s Lua, not `generation_quota.py`'s
INCR.** *(Path corrected — the doc says `services/spend_cap.py`; it is at
`backend/archimedes/marketplace/spend_cap.py`.)*

`check_and_increment` is a one-way `INCR` — the wrong primitive for something you charge for,
because a failed generation can never be un-charged. `spend_cap.py:82 _CHECK_AND_RESERVE_LUA`
already does atomic check-and-reserve in one EVAL over a rolling sorted-set window, dedupes on
repeated member, closes the N-concurrent TOCTOU, **and is already tested.**

Generalize it from raw USDC to units-of-SKU and add `release` (a single `ZREM`, ~15 lines).

## The `Principal` seam — build it here, the paid API needs it

`{kind: wallet|api_key|anon, wallet, key_id, meter_key, tier}` where `meter_key` is
`wallet:0x…` | `key:<key_id>` | `ip:<x-real-ip>`. **Reuse `generation_quota.client_ip()`
verbatim** — it already correctly refuses `X-Forwarded-For`.

**The lowercased-wallet constraint is satisfied by construction:** for an API key,
`Principal.wallet` is the resolved *owner* wallet and resolution happens **inside**
`get_verified_wallet` (the `gate_generation` wrapper around it was deleted 2026-08-19),
which keeps returning a lowercased `0x` string. So
`owner_wallet` columns, `wallet_can_publish`, `is_strategy_visible`, `derive_pool_id`, and
`spend_cap._key` all keep working **untouched**.

## Three SKUs

`generation` (one completed job, 5/day free) · `generation_premium` (20/day — **this is #1197's
ceiling, free from this mechanism**) · `rigor_verdict` (20/day).

Price the **job**, not the candidate (server-influenced `n_candidates` invites disputes) and not
the token (unpredictable for the buyer). Absorb the `max_papers` variance at the p90.

## Wiring — `generate_routes.py`

- `:118` resolve principal → `reserve(...)` **before** `store.enqueue` and **before any LLM
  spend**, replacing the `:130` quota call
- carry `meter_reservation` in the job payload
- `commit` on the terminal `done` event
- `release` in `_run_with_cleanup`'s `except`, on `error`, and in `cancel_job` (`:296`)

**TTL is the correctness backstop; release is the optimization.** `_RUNNING_TASKS` is an
in-process dict and Fargate can run more than one task, so a crash between reserve and commit
leaks a reservation — the rolling window ages it out within 24h. **Say that in the module
docstring. Do not build a distributed reaper.**

## Ship observe-first

`METER_ENFORCE=false` on the first deploy: reserve, record, log, return `X-Meter-Used` /
`X-Meter-Remaining`, **never 402/429**. Read the real distribution for 2–3 days, set the ceiling
above the observed p99, then flip. One env var is the difference between launching a paywall and
rate-limiting your only 15 users.

- Keep `WALLET_LESS_GENERATION_DAILY_CAP` as a **deprecated alias** so deploying changes no
  behaviour.
- Keep the fail-closed-on-Redis stance (#929).
- **Map free-wallet-exhausted to 402, not 429** — 429 says "wait", 402 says "pay" and can carry a
  payment option. Keep 429 for the anonymous-IP case with its existing connect-wallet steering.

## The proof test

With a valid SIWE cookie (`_siwe_cookies(wallet)` from `test_user_routes.py`),
`POST /api/generate/start` N+1 times → **the last returns 402.** On today's code that test
asserts unlimited — which is exactly what makes it the #1199 regression proof.

```bash
env -i HOME=$HOME PATH=$PATH PYTHONPATH=backend python -m pytest backend/tests/test_metering.py -q
grep -r "asyncio.get_event_loop" backend/tests/   # must return nothing
```

## B5 — premium tier: the silent downgrade (same file, ~5 lines)

**Verified bug.** `generate_routes.py:151` is
`selected_model = req.model if is_allowed_model(req.model) else None`, and `is_allowed_model`
checks membership in `FREE_TIER_MODELS` — which deliberately **excludes** the premium ids. So an
**entitled** premium request resolves to `None` and silently runs the env default (Nova Micro).
Today nobody is charged, so it is documented rather than dishonest — **the instant you charge for
the tier it becomes charging for a downgrade.**

- Add `PREMIUM_TIER_MODELS` and `is_servable_model(model, *, premium_entitled)`; keep
  `is_allowed_model` as the free-only predicate (tests reference it); call the new one after
  `enforce_model_entitlement`.
- **Fail loud, never fall back**: if Bedrock rejects the premium id at runtime the job errors
  **and releases the meter reservation**.
- Surface `response.model` (already the provenance of record) on the job result and passport.
- Un-grey `ModelCostPanel` rows from a **server** field, not a build flag — Bedrock activation is
  an AWS fact that changes without a deploy.
- Regenerate `ui/src/data/modelPricing.json` (stamped `2026-06-24`) with a test asserting every
  `model_id` in it is in `FREE_TIER_MODELS ∪ PREMIUM_TIER_MODELS`.

**If Bedrock activation misses:** ship the tier anyway against `mistral.mistral-small-2402-v1:0`
or `moonshotai.kimi-k2.5`, relabel the row "Premium tier · frontier-class" naming the actual
model, keep the `us.anthropic.*` rows greyed with the honest tooltip. **The one variant that must
not ship is a premium tier that resolves to the same model as the free tier at a higher price.**

## Anti-goals

- Do not build on `generation_quota.py`'s INCR.
- Do not build a distributed reservation reaper.
- Do not rekey the meter on anything but `principal_id` (see #1194 default in cluster-0).
- Release marker: **this PR is `!minor`.**
