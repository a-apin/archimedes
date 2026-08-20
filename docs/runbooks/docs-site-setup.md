# Docs Site Setup — GitHub Pages via mkdocs-material

> **Status:** runbook. **Owner:** Dan Browne. **Updated:** 2026-08-20.

`docs/` is built into a static site by [`mkdocs.yml`](../../mkdocs.yml) (theme:
mkdocs-material) and published by
[`.github/workflows/docs-site.yml`](../../.github/workflows/docs-site.yml) to
GitHub Pages. This is issue #1381, Dan's decision: **option B, GitHub
Pages** — migrate to something else later only if we outgrow it.

The workflow is **inert as merged** — see its own header comment. Both of
its jobs are gated on the repo variable `DOCS_SITE_ENABLED == "true"` (the
same pattern as `deploy-runners.yml`'s `RUNNER_DEPLOY_ENABLED`), so until
step 3 below it simply **skips** (grey, not red) on every qualifying push.
If the variable is flipped before Pages is enabled, the deploy job fails
harmlessly at `actions/deploy-pages`: no partial deploy, no site, nothing
outside the workflow run's own red X.

## Dan's three manual steps to go live

Neither of these can be done by a workflow or from the CLI with the access
this repo's automation has — both are one-time, human, console actions.

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
exists and equals `true`, both workflow jobs skip — that's the safety that
lets the scaffold merge before steps 1–2 are done.

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
`mkdocs.yml` edit. `mkdocs build` (what CI runs) writes the static site to
`site/` (or `_site/` — CI passes `--site-dir _site` to match
`upload-pages-artifact`'s expected path); either is gitignored-equivalent
scratch output, not something to commit.

## `mkdocs --strict` findings

`mkdocs build --strict` was run against the full `docs/` tree while building
this scaffold (2026-08-20, mkdocs-material 9.7.7) and is **not** clean: 315
warnings, which `--strict` promotes to a hard failure. `mkdocs.yml` and
`docs-site.yml` both run plain `mkdocs build` (no `--strict`) deliberately,
for the reasons below — re-run the command yourself before changing that:

```bash
mkdocs build --strict
```

**305 of the 315 — pre-existing, correct, out-of-tree links (not a docs
bug).** `docs_dir: docs` means mkdocs only ever sees files under `docs/`.
This repo's own convention (`architecture.md`: *"Every claim is a link to a
file"*) has many docs link out to source and to repo-root files —
`../CLAUDE.md`, `../SETUP.md`, `../README.md`, `../AGENTS.md`, and paths into
`backend/`, `ui/`, `contracts/`, `analytics-engine/`, `infra/`,
`submodules/`, `cli/`, plus this runbook's own links to
[`../../mkdocs.yml`](../../mkdocs.yml) and the two `.github/workflows/*.yml`
files below. Every one of those resolves correctly on GitHub (where these
docs are actually read day to day) and is already the thing
`docs-gate.yml`'s `docs_links.py` blocks PRs on, against the real repo tree —
not this site's. mkdocs, scoped to `docs_dir`, cannot resolve any of them and
warns on all 305 (47 unique targets, repeated across the files that link
them). Rewriting ~47 links across dozens of files to work around a
site-generator limitation, when they're correct as written and already
gated elsewhere, was judged not worth doing — see
`docs/CONVENTIONS.md` if that tradeoff should be revisited.

**10 of the 315 — `docs/api/*.md` nav entries, pending a branch merge.**
`mkdocs.yml`'s "API Reference" section names the files that
`dbrowneup/api-reference-docs` (issue: separate PR, may land before or after
this one) adds under `docs/api/`. Until that branch merges, those files
don't exist yet on `main`, so mkdocs warns "not found" on each nav entry.
Non-strict, this is silent-safe: the section just doesn't populate. No
change needed here once that branch merges — the nav entries already point
at the right paths.

Everything else in the `--strict` output is `INFO`-level (in-page anchor
links that don't match a heading slug, and a couple of directory-style
relative links) — `INFO` never escalates under `--strict`; only `WARNING`
does, and the two categories above account for all 315 of those.

## Related files

| File | Purpose |
|---|---|
| [`../../mkdocs.yml`](../../mkdocs.yml) | Site config: theme, nav, repo/site URLs. |
| [`../../.github/workflows/docs-site.yml`](../../.github/workflows/docs-site.yml) | Build + deploy workflow. Inert until step 1 above is done. |
| [`../CONVENTIONS.md`](../CONVENTIONS.md) | Where a new doc goes — unchanged by this scaffold; the site just publishes what's already there. |
| [`../../.github/workflows/docs-gate.yml`](../../.github/workflows/docs-gate.yml) | The blocking link/index checker this runbook defers to for `docs/**`'s real (in-repo) links. |
