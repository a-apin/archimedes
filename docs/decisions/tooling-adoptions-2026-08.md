# Tooling Adoptions — August 2026

> **status:** current
> **owner:** Dan Browne
> **updated:** 2026-08-31
> **superseded-by:** —

The register of developer tooling adopted (or attempted) in August 2026, and what each one
actually cost and produced. One row per tool, amendments appended in date order underneath.

> **This file was created on 2026-08-31, not found.** The OpenWiki bootstrap work was
> directed to amend a file at this exact path. It did not exist — not in the working tree,
> not on `main`, and not on any remote branch (`git log --all -- docs/decisions/` is empty).
> It is created here so the path resolves, with the standard stated as the amendment that
> governs the row below. **The prior amendment text is recorded as it was handed to the
> session, not recovered from a file**, and the register starts with one row rather than
> pretending to a history it does not have.
>
> **Placement note.** Repo convention ([`../CONVENTIONS.md`](../CONVENTIONS.md)) routes a
> closed-off decision to [`../adr/`](../adr/README.md). This file is at the path it was
> named at instead of being silently relocated; folding it into an ADR is a reasonable
> follow-up, and should set both ends of the chain in one commit.

---

## The standard

> **Amendment, 2026-08-31.** Run it against a real corpus slice and record what it cost and
> what it produced, or remove it. **Installed is not a resting state.**

Three things follow from that, and they are the columns of the register:

1. **A real slice, not a toy.** Real repository content, bounded on purpose so the bill is
   knowable before it is paid.
2. **Recorded cost.** Wall clock, and money or tokens where either is observable. "It
   seemed fast" is not a record.
3. **Recorded output, including a negative verdict.** If the output is not worth reviewing,
   that sentence *is* the deliverable — with the evidence that supports it.

---

## Register

| Tool | Version | Adopted | Status | Slice run against | Verdict |
|---|---|---|---|---|---|
| [OpenWiki](https://github.com/langchain-ai/openwiki) | 0.4.3 | 2026-08-30 | **run, output kept** | `docs/quant/` — 8 files, 2,006 lines, 113 KB | 8 wiki pages, 89 grounded claims, 7 real documentation defects found. Kept. Model spend **$0.00** — see amendment 2. |

---

## OpenWiki

### Amendment 1 — 2026-08-30: installed, never run

`npm install openwiki@0.4.3` into `tools/openwiki/` (242 packages). A first
`openwiki --init` was started against the whole repository on 2026-08-31 at 02:42 UTC and
**was interrupted in the planning phase**: `openwiki/.run.json` recorded
`"phase": "planning"`, `"status": "interrupted"`, and `initialPages: []`. It produced no
page, no claim, and no cost record. That state is exactly what the standard above was
written to stop, and it is why this row exists.

The session also left three artefacts worth keeping: a `.openwikiignore` read boundary, the
scheduled-update workflow, and the `OPENWIKI:START/END` marker blocks in `AGENTS.md` and
`CLAUDE.md`. Those were carried forward. The interrupted run state was deleted.

### Amendment 2 — 2026-08-31: run against `docs/quant/`, output kept

**The provider blocker, measured before anything else.** OpenWiki's native mode needs a
model provider. No `ANTHROPIC_API_KEY` is present in the environment, so the intended path
was Bedrock under the caller's own SSO credentials (account `037613907429`, `us-east-1`).
Bedrock's Anthropic models are **not enabled on that account**:

```
$ aws bedrock-runtime converse --model-id us.anthropic.claude-sonnet-4-6 …
ResourceNotFoundException: Model use case details have not been submitted for this
account. Fill out the Anthropic use case details form before using the model.

$ aws bedrock-runtime converse --model-id us.anthropic.claude-sonnet-5 …
AccessDeniedException: anthropic.claude-sonnet-5 is not available for this account.
```

Two facts from that, both now recorded in the workflow file. Unblocking needs a **console
action nobody can automate** — submitting the Anthropic use-case form in Bedrock. And
`us.anthropic.claude-sonnet-5`, which the workflow originally named, is refused for this
account even after the form; `us.anthropic.claude-sonnet-4-6` is the newest profile the
account lists. The scheduled cron is **disabled** until a manual dispatch succeeds, because
a job that fails every morning on a credential gap trains people to ignore it.

**How the run happened anyway.** OpenWiki 0.4.3 ships a coding-agent integration: it exposes
its lifecycle (`begin → submit_plan → next_page → submit_page → finish`) over a local MCP
server and lets the host agent do the research and authoring with its own authenticated
session. OpenWiki keeps everything that makes it more than a prompt — the durable page
queue, claim validation and persistence, evidence resolution, indexes, provenance, and
finalisation. **Provider credentials are not required in this mode**, which is what made a
real run possible past the Bedrock wall. The server was driven directly over stdio
(`openwiki mcp --host claude`).

**Scope.** `.openwikiignore` was rewritten as an **allow-list**: exclude the repository,
re-include `docs/quant/` plus OpenWiki's own output tree. The boundary was adversarially
tested against OpenWiki's own matcher — 35 cases, including path traversal
(`docs/quant/../../backend/…`), case variants, backslash spellings, and secret patterns
nested *inside* the allowed slice. **35/35 passed**, negatives included.

That check is committed as [`../../scripts/check_openwiki_ignore.mjs`](../../scripts/check_openwiki_ignore.mjs)
and was itself shown to reject, before being trusted — three deliberately broken boundaries
each exit `1`, and the correct one exits `0`:

| Injected break | Result |
|---|---|
| Drop the `/*` exclude-everything rule (allow-list collapses to nothing) | exit 1 — 8 out-of-slice paths become readable |
| Move `node_modules/` *above* the allow-list | exit 1 — `docs/quant/node_modules/x.js` and `openwiki/node_modules/x.js` re-admitted by the `!` rules below it |
| Empty the file | exit 1 — "parsed to zero usable rules, enforcement is a no-op" |
| Restored | exit 0 — 35/35 |

Run it after **any** edit to `.openwikiignore`, and especially when widening: ordering is
last-match-wins, so a deny placed above the allow-list is silently undone by it.

**Cost and time.**

| Measure | Value |
|---|---|
| Wall clock, `openwiki_begin` → `openwiki_finish` | **12 min 14 s** (05:55:41 → 06:07:55 UTC) |
| OpenWiki's own compute inside that | **< 2 s total** across ~30 lifecycle calls (each 1–225 ms) |
| Model spend attributable to OpenWiki | **$0.00** — no provider was configured or called |
| Authoring cost | Borne by the host agent session: ~2,000 lines of source read, ~1,400 lines written. Not separately metered, and OpenWiki cannot meter it in this mode. |
| Input slice | 8 files, 2,006 lines, 113 KB |

**Honest reading of that $0.00.** It is not free — the authoring tokens were spent, just on
the host session's account rather than through a provider key OpenWiki controls. The
number that transfers to a native Bedrock run is the *slice size*, not the cost: budget for
a model reading ~113 KB of source and emitting ~60 KB of prose plus 89 structured claims.

**Output.**

| | |
|---|---|
| Pages | 8 (plus 5 generated indexes) |
| Lines of generated prose | 1,416 |
| Grounded claims persisted | **89**, each with one or more `repo://path#Lx-Ly` evidence spans |
| Evidence versioning | Every span content-hashed, with 3 lines of leading/trailing context, so a claim goes stale when its evidence moves |

**Verdict: worth reviewing, and kept.** Two things earn that, neither of which is "the prose
reads well":

1. **The tool refuses unverifiable evidence.** Two `submit_page` calls were rejected with
   `invalid_input: Evidence does not resolve` — both were line spans one line past the end
   of the cited file. A claim cannot be persisted against a citation that does not exist.
   That is a real guard, not a formatting check, and it is the reason to prefer this over a
   prompt that emits Markdown.
2. **The run found seven genuine defects in `docs/quant/`** that no linter would catch,
   collected in `openwiki/rigor/documented-conflicts.md`. Among them: the DSR bar is stated
   as a literal `0.90` in one doc and as the top rung of a five-level profile ladder in
   another; a single document assigns `num_trials` both `1` and `len(strategy_library)` a
   hundred lines apart; a worked table still evaluates against the retired `p ≥ 0.95` bar
   and marks `p = 0.941` as failing; three strategy headings contradict the status lines
   directly beneath them, one with text about a *different* strategy stranded under it; and
   two passages explain a failure as "OOS Sharpe 0.612, under the 0.90 gate" — comparing a
   Sharpe ratio to a probability threshold. Those are follow-up work, filed as evidence
   rather than fixed here.

**What it does not do.** In this mode the wiki is grounded in whatever the boundary lets it
read. A slice of documentation yields claims about what the *docs* say, never about what the
code enforces — the generated pages say so on every page, which is correct behaviour and
also a hard ceiling on the value of a docs-only slice.

**Next slice, if this continues.** Widen `.openwikiignore` by one `!` line, run, and add a
row here with its cost. `docs/api/` (10 files) or `backend/archimedes/services/` are the
obvious candidates; the second is the first slice that would produce claims about behaviour
rather than about prose.
