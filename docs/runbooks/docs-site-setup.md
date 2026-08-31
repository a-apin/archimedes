# Docs Site Setup — GitHub Pages via mkdocs-material

> **Status:** runbook. **Owner:** Dan Browne. **Updated:** 2026-08-31.

`docs/` **and the agent-generated `openwiki/` tree** are built into a static
site by [`mkdocs.yml`](../../mkdocs.yml) (theme: mkdocs-material) and published
by [`.github/workflows/docs-site.yml`](../../.github/workflows/docs-site.yml)
to GitHub Pages. This is issue #1381, Dan's decision: **option B, GitHub
Pages** — migrate to something else later only if we outgrow it.

`openwiki/` lives at the repository root, outside `docs_dir`, so mkdocs cannot
see it by itself; [`.github/scripts/mkdocs_hooks.py`](../../.github/scripts/mkdocs_hooks.py)
mounts it at `/openwiki/` and stamps the agent-generated banner on each page.
The section index, with the full provenance note, is
[`../agent-wiki.md`](../agent-wiki.md).

## Which half is live and which is still gated

The workflow's two jobs have **different** gates, and only one of them is inert:

- **`build`** runs on every qualifying push to `main` and on every PR that
  touches a docs path. It is a real check — `mkdocs build --strict` fails on a
  link into `docs/` or `openwiki/` that names a file which does not exist. It
  needs no repo settings and no DNS, so nothing below blocks it. (It used to be
  gated too, which meant the site could rot unnoticed for as long as the
  variable stayed unset.)
- **`deploy`** is gated on the repo variable `DOCS_SITE_ENABLED == "true"` (the
  same pattern as `deploy-runners.yml`'s `RUNNER_DEPLOY_ENABLED`) and never runs
  from a pull request, so until step 3 below it simply **skips** (grey, not
  red). If the variable is flipped before Pages is enabled, the deploy job fails
  harmlessly at `actions/deploy-pages`: no partial deploy, no site, nothing
  outside the workflow run's own red X.

## Dan's three manual steps to go live

None of these can be done by a workflow or from the CLI with the access this
repo's automation has — all three are one-time, human, console actions.

### 1. Repo Settings → Pages

1. GitHub → `a-apin/archimedes` → **Settings → Pages**.
2. **Build and deployment → Source:** select **GitHub Actions** (not "Deploy
   from a branch"). This is what creates the `github-pages` deployment
   *environment* that `docs-site.yml`'s `deploy` job publishes into — the
   workflow's `actions/deploy-pages` step has nothing to deploy to until this
   is set, which is the whole of why it fails today.
3. **Custom domain:** enter `docs.archimedes-arc.com` and save. GitHub will
   show "improperly configured" / a DNS error until step 2 (Route 53) below
   also exists — that's expected, not a sign anything here is wrong.
4. Once the DNS check goes green, tick **Enforce HTTPS**. GitHub provisions
   the certificate itself (Let's Encrypt, via its own ACME flow) — nothing to
   do in ACM for this.
5. Re-run the `docs-site` workflow (push a no-op or use **Actions → Docs Site
   (GitHub Pages) → Run workflow**) to do the first real deploy.

### 2. Route 53 — CNAME record

In the `archimedes-arc.com` hosted zone (same account/profile as the rest of
prod DNS — `ArchimedesDanAdmin` / us-east-1):

| Field | Value |
|---|---|
| Name | `docs.archimedes-arc.com` |
| Type | `CNAME` |
| Value | `a-apin.github.io` |
| TTL | 300 (or the zone default) |

This is the standard GitHub Pages custom-apex-subdomain pattern — a `CNAME`
record on a subdomain, pointing at `<org>.github.io` (not at a per-repo
`<org>.github.io/<repo>` path; GitHub resolves the right repo from the Pages
config once the custom domain is set in step 1). No record creation was done
as part of this scaffold — this table is instructions for Dan, not evidence
of a change already made.

### 3. Flip the workflow gate

GitHub → `a-apin/archimedes` → **Settings → Secrets and variables → Actions
→ Variables** → New repository variable: `DOCS_SITE_ENABLED` = `true`.
Then run the workflow once (**Actions → Docs Site (GitHub Pages) → Run
workflow**) rather than waiting for the next docs push. Until this variable
exists and equals `true`, the **deploy** job skips — that's the safety that
lets this merge before steps 1–2 are done. The **build** job runs either way;
if it is green, everything except the three steps on this page is done.

### Why there's no `docs/CNAME` file

Classic "Deploy from a branch" GitHub Pages reads the custom domain from a
`CNAME` file committed to the published branch/folder. This repo uses the
`actions/deploy-pages` flow instead (`upload-pages-artifact` +
`deploy-pages`), where the custom domain is a **repo setting** (step 1 above,
part 3), not a file — `deploy-pages` writes it into the Pages environment
config directly. A `docs/CNAME` file would do nothing useful here (mkdocs
would just copy it into the built site as an inert static file) and would be
one more place the domain string could drift from Settings, so it's
deliberately not present. If Archimedes ever migrates *off* `deploy-pages`
back to branch-deploy Pages, that's the moment to add one — not before.

## Local preview

```bash
pip install mkdocs-material==9.7.7   # pin matches docs-site.yml; see its comment
mkdocs serve
```

Serves at `http://127.0.0.1:8000` with live reload on any `docs/**` or
`mkdocs.yml` edit. `mkdocs build --strict` (what CI runs) writes the static
site to `site/` (or `_site/` — CI passes `--site-dir _site` to match
`upload-pages-artifact`'s expected path); either is gitignored-equivalent
scratch output, not something to commit.

`mkdocs serve` watches `docs_dir` and `mkdocs.yml` and nothing else by
default, so `mkdocs.yml` adds `watch: [openwiki]` to pick up wiki edits too.
Editing the hook itself still needs a **restart**, not just a save: mkdocs
`lru_cache`s hook modules for the life of the process.

## `mkdocs --strict` is the gate

`mkdocs build --strict` is what `docs-site.yml` runs, and it is clean. Run it
yourself before changing anything about the build:

```bash
mkdocs build --strict
```

It was not always clean, and the history is worth keeping because it explains
the hook. On the 2026-08-20 scaffold the same command produced **315
warnings**, and on `main` at 2026-08-31 it produced **371** — every single one
of them the same shape:

```
WARNING - Doc file 'X.md' contains a link '../../backend/…', but the target
          '../backend/…' is not found among documentation files.
```

`docs_dir: docs` means mkdocs only ever sees files under `docs/`. This repo's
own convention (`architecture.md`: *"Every claim is a link to a file"*) has
many docs link out to source and to repo-root files — `../CLAUDE.md`,
`../SETUP.md`, `../README.md`, `../AGENTS.md`, and paths into `backend/`,
`ui/`, `contracts/`, `analytics-engine/`, `infra/`, `submodules/`, `cli/`,
plus this runbook's own links to [`../../mkdocs.yml`](../../mkdocs.yml) and
the `.github/workflows/*.yml` files below. Every one resolves correctly on
GitHub — where these docs are read day to day, and where `docs-gate.yml`'s
`docs_links.py` already blocks PRs on them against the real repo tree. But on
the *published site* they resolve to nothing: 371 links that would have
served a 404 to anyone who clicked them.

The scaffold's answer was to drop `--strict` and live with the noise. The
answer now is [`.github/scripts/mkdocs_hooks.py`](../../.github/scripts/mkdocs_hooks.py),
which rewrites those targets **at build time** to
`https://github.com/a-apin/archimedes/blob/main/<path>` (preserving `#Lnn`
line anchors, and using `/tree/` for directories). No committed markdown was
touched — the ~47 unique out-of-tree targets stay exactly as written, correct
on GitHub, and now also correct on the site.

**What the hook deliberately does *not* rewrite, and why that matters.** A
link whose target lands *inside* `docs/` or `openwiki/` and names a file that
exists nowhere in the repository is left exactly as written. Laundering it
into a GitHub URL would convert a broken link into a plausible-looking one
that still 404s, and would silence the only check on it. Left alone, mkdocs
warns and `--strict` fails the build. That branch is covered by
`backend/tests/test_docs_site.py::test_broken_in_site_link_is_left_for_strict_to_catch`.

**Known remaining noise: 20 `INFO`-level anchor mismatches** (in-page `#…`
links whose slug does not exist on the target page, mostly in `archive/`).
`INFO` never escalates under `--strict`, so they do not fail the build. They
are real, small, and a separate cleanup — raising `validation.links.anchors`
to `warn` is the change that would force it.

## Related files

| File | Purpose |
|---|---|
| [`../../mkdocs.yml`](../../mkdocs.yml) | Site config: theme, nav, repo/site URLs, hooks. |
| [`../../.github/scripts/mkdocs_hooks.py`](../../.github/scripts/mkdocs_hooks.py) | Mounts `openwiki/` into the build, stamps its provenance banner, and repoints out-of-`docs_dir` links at GitHub. |
| [`../../.github/workflows/docs-site.yml`](../../.github/workflows/docs-site.yml) | Build + deploy workflow. The build always runs; the deploy is inert until steps 1–3 above are done. |
| [`../agent-wiki.md`](../agent-wiki.md) | Provenance note for the agent-generated section, and the section's index page. |
| [`../../backend/tests/test_docs_site.py`](../../backend/tests/test_docs_site.py) | Drift guard: nav ↔ tree, the provenance label, the workflow's `paths:` filter and `--strict` flag, and the link rewriter's behaviour. |
| [`../CONVENTIONS.md`](../CONVENTIONS.md) | Where a new doc goes — unchanged by this scaffold; the site just publishes what's already there. |
| [`../../.github/workflows/docs-gate.yml`](../../.github/workflows/docs-gate.yml) | The blocking link/index checker this runbook defers to for `docs/**`'s real (in-repo) links. |
