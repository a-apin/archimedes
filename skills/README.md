# Archimedes skills

First-party agent skills for working with and on Archimedes — for ourselves
(Claude Code sessions, the agentic issue pipeline) and for external agents/humans
integrating against the live API. Each skill is a self-contained directory with a
`SKILL.md` that uses frontmatter (`name`, `description`, `triggers`) in the style
of manifest-driven skills, so a skill-aware agent can discover and load the right
one by matching its `triggers` against the task at hand.

Note the location: this is `skills/`, tracked in git at the repo root — distinct
from `.agents/`, which is gitignored and never the place for anything meant to
ship with the repo.

## What's here

| Skill | Use it when |
|---|---|
| [`verdict-api/`](verdict-api/SKILL.md) | Calling `/api/generate/*` over HTTP — starting a generation job, reading its SSE event stream, understanding auth/rate-limit requirements as they actually are on `main`, and interpreting DSR/PBO/holdout numbers without over-claiming. |
| [`x402-payment/`](x402-payment/SKILL.md) | Understanding or touching the x402/Circle Gateway copy-trading fee flow (`marketplace/payments.py`) — the in-process 402→sign→verify→settle protocol, testnet posture, and `PAYMENTS_DRY_RUN` semantics. |
| [`strategy-passport/`](strategy-passport/SKILL.md) | Reading or summarizing a strategy passport — every `strategy_passports` field, what `passes_rigor_gate` does and does not mean, why `status="live"` is not a rigor claim, and the five claims never to make about a strategy's rigor. |
| [`repo-dev/`](repo-dev/SKILL.md) | Doing general development work in this repo — the conda env (Python + Node), hermetic pytest conventions, merge-commit-only branch policy, CI gates, and where things live. |

## Ground rules for every skill in this directory

- **Every factual claim traces to a `file:line` citation in the working tree.**
  A skill that asserts an endpoint, a field, or a default without pointing at
  the line that proves it is a liability, not a shortcut — re-run the greps in
  each skill's "Verify" section before trusting a claim that's gone stale.
  Endpoints and behavior described here were verified against `main` as of the
  skill's last update; if the code has since moved, the skill is wrong until
  someone re-verifies and updates it.
- **State what's deliberately NOT covered.** Every skill ends with a short list
  of adjacent things it intentionally leaves to a different skill, so an agent
  doesn't infer coverage that isn't there.
- **No skill here claims more rigor, security, or capability than the code
  currently has.** If a behavior is a stub, a dry-run default, or an
  interim/custodial arrangement, the skill says so — see `strategy-passport/`'s
  "five forbidden phrasings" and `x402-payment/`'s custody-model note for the
  house style on this.
- **The CLI is deliberately not covered.** `cli/` is a `0.0.1`
  name-reservation stub — every subcommand exits `NOT_IMPLEMENTED`
  (`cli/src/archimedes_cli/cli.py`). There is no `skills/cli/` here on purpose;
  add one only once the CLI actually does something.

## Adding a new skill

Copy the shape of an existing `SKILL.md`: YAML frontmatter with `name`,
`description`, and `triggers`, then grounded prose with file:line citations and
a "Verify" section a reader can re-run. Keep each skill scoped to one coherent
surface — cross-link to a sibling skill rather than duplicating its content.
