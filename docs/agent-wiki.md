# Agent-generated wiki (OpenWiki)

> **Status:** current. **Owner:** Dan Browne. **Last verified:** 2026-08-31.

Every page under **Agent-generated wiki** in this site's navigation — everything
served from `/openwiki/` — was written by an AI agent, not by a person. This page
exists so that fact is stated once, in full, in a place a reader passes through;
the build also stamps a short version of it on each page, because a search engine
drops readers on leaf pages, not on section indexes.

Nothing on this page is agent-written. The wiki itself lives in
[`../openwiki/`](../openwiki/) at the repository root, outside `docs/`, and is
mounted into the site at build time by
[`../.github/scripts/mkdocs_hooks.py`](../.github/scripts/mkdocs_hooks.py).

## What produced it

| | |
|---|---|
| Generator | [OpenWiki](https://github.com/langchain-ai/openwiki) 0.4.3 |
| Mode | coding-agent integration — the host agent's own model session, recorded as `host-agent/claude` in [`../openwiki/.last-update.json`](../openwiki/.last-update.json) |
| Command | `init` |
| Repository state documented | commit `9a84fa25ce861756eec2262b754f07acfaa2790c` |
| Written | 2026-08-31 |
| Landed by | [#1597](https://github.com/a-apin/archimedes/pull/1597) |

The scheduled regeneration workflow
([`../.github/workflows/openwiki-update.yml`](../.github/workflows/openwiki-update.yml))
**cannot run today** — its Bedrock path is blocked on an Anthropic use-case form
that has not been submitted for the production AWS account, and its `schedule:`
trigger is commented out for that reason. The committed wiki was not produced by
that workflow. The tooling decision, the blocker, and what the run cost are
recorded in
[`decisions/tooling-adoptions-2026-08.md`](decisions/tooling-adoptions-2026-08.md).

## What it covers, and what it therefore cannot say

The read boundary is [`../.openwikiignore`](../.openwikiignore), which is an
**allow-list**: it excludes the whole repository and re-includes exactly one
slice, `docs/quant/`. The generation run could not read `backend/`, `contracts/`,
`ui/`, or any other documentation tree.

Two consequences a reader has to hold on to:

- **Every claim is grounded in a document, not in an implementation.** A wiki page
  reporting a threshold is evidence that a doc asserts that threshold — not
  evidence that the code enforces it. For any current verdict, go to the running
  system.
- **Where the wiki disagrees with the hand-written docs, the frozen spec, or the
  live system, they win.** The wiki is a navigational summary layer over
  `docs/quant/`, never an authority over it.

The scope rules the run was given, including the accuracy overrides it was
required to honour (vaults are roadmap; never quote a library pass count; the
FDR helpers are disclosed, not implemented), are in the human-authored brief
[`../openwiki/INSTRUCTIONS.md`](../openwiki/INSTRUCTIONS.md), which OpenWiki reads
and never rewrites.

## What review it did and did not get

**These pages were not reviewed line by line**, and once a page is in the nav it
republishes automatically on every docs-path merge. Treat them the way you would
treat a colleague's competent summary of documents you have not read yourself:
useful for routing, not citable as the source.

What they did get is machine-checked grounding, not human sign-off. Each of the
89 persisted claims carries one or more `repo://docs/quant/...#Lx-Ly` evidence
spans, content-hashed with surrounding context so a claim goes stale when its
evidence moves, and OpenWiki refused to persist claims whose citations did not
resolve (two were rejected on this run). The spans live in one JSON sidecar per
page under `openwiki/.claims/`; those sidecars are **not** published to this site
— they are for re-verification tooling, and they are what to read if you want to
check a specific sentence against its source. The full verdict, costs, and the
seven `docs/quant/` defects the run surfaced are in
[`decisions/tooling-adoptions-2026-08.md`](decisions/tooling-adoptions-2026-08.md).

## Adding to it

Wiki pages are listed in [`../mkdocs.yml`](../mkdocs.yml)'s navigation **by hand**.
That is deliberate: a generated tree that auto-publishes would put new
agent-written pages in front of readers with nobody in the loop.
[`../backend/tests/test_docs_site.py`](../backend/tests/test_docs_site.py) fails
when an `openwiki/**.md` file has no nav entry, so admitting a page is an explicit
act — add the nav row in the same change that adds the page.

Widening the slice is one directory at a time, with the cost recorded in the
tooling-adoptions register; the rules are at the top of
[`../.openwikiignore`](../.openwikiignore), and any edit to it must be re-verified
with `node scripts/check_openwiki_ignore.mjs`.
