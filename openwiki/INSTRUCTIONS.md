# OpenWiki brief — Archimedes

User-authored. OpenWiki reads this for scope and priorities and never rewrites it.

## Scope

This wiki is **scoped to one slice**: `docs/quant/` — the quantitative methodology
and rigor layer. `.openwikiignore` is an allow-list that excludes the rest of the
repository, so the run cannot read backend, contracts, UI, or the other doc trees.
Widen one slice at a time and record what each slice costs in
[`docs/decisions/tooling-adoptions-2026-08.md`](../docs/decisions/tooling-adoptions-2026-08.md).

Because the slice is documentation rather than source, wiki pages are grounded in
the docs themselves (`repo://docs/quant/...`). Do **not** write claims about code
paths the run cannot read — a doc asserting a threshold is evidence that the doc
asserts it, not evidence that the code enforces it. Where the slice itself says the
spec and the implementation win over the doc, say so on the page.

## Accuracy rules that override anything the slice implies

- **Vaults and on-chain execution are ROADMAP, not shipped product.** The
  `Vault`/`VaultFactory` contracts exist and are deployed, but the deploy-a-vault
  journey is gated off every public surface behind `ROADMAP_SURFACES_ENABLED`.
  Write it in the future tense. Never claim vaults are live, non-custodial in
  production, or executing capital.
- **Never state how many library strategies currently pass the gate.** The live
  rigor gate is the only authority on pass/fail. A number written into a wiki page
  is stale the moment the library changes.
- **Do not present the Benjamini–Hochberg / FDR helpers as implemented.** The slice
  records them as written down with zero non-test callers. Board-level selection
  bias is *disclosed*, not corrected.
- **`num_trials = 1` on the curated library** means DSR runs undeflated. Do not
  describe the curated path as multiple-testing corrected.
- Prefer what the slice actually says over what a reader would like it to say.
  Where two docs in the slice disagree, name the disagreement rather than picking
  a winner.

## Style

- Retrieval-oriented: a page should answer a question an agent would actually ask
  before touching the rigor gate or a strategy's numbers.
- Cite thresholds with their source doc. Keep the numbers verbatim.
- No padding. A short accurate page beats a long one with a filled-in checklist.
