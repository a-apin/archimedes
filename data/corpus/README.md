# arXiv q-fin corpus

The recency-biased quantitative-finance reading corpus that grounds every
Tier-1 strategy passport. Built by
[`backend/archimedes/services/arxiv_corpus.py`](../../backend/archimedes/services/arxiv_corpus.py).

**Why recency-biased?** The Archimedes thesis is that *alpha decays as
novelty wears off*. The corpus is deliberately skewed bleeding-edge: we
over-fetch recent submissions across the q-fin categories sorted by
submission date (newest first), then trim to the most recent N after dedupe.

## What is tracked in git

| Path                        | Tracked? | Notes                                              |
| --------------------------- | -------- | -------------------------------------------------- |
| `manifest.jsonl`            | ✅ yes    | One JSON object per paper. The corpus index.       |
| `README.md`                 | ✅ yes    | This file.                                         |
| `pdfs/<arxiv_id>.pdf`       | ❌ no     | gitignored — regenerate locally (sha256-cached).   |
| `text/<arxiv_id>.txt`       | ❌ no     | gitignored — pypdf-extracted body text.            |

The PDF + text caches are content-addressed and reproducible from the
manifest, so they are not committed. Only the metadata manifest is.

## Manifest schema (frozen — one object per line)

```json
{
  "arxiv_id": "2401.12345",
  "title": "...",
  "authors": ["..."],
  "primary_category": "q-fin.PM",
  "categories": ["q-fin.PM", "q-fin.TR"],
  "published": "2024-01-22",
  "updated": "2024-02-01",
  "abstract": "...",
  "pdf_url": "https://...",
  "pdf_sha256": "<hex, or null if the PDF download/extract failed>",
  "pdf_path": "data/corpus/pdfs/2401.12345.pdf",
  "text_path": "data/corpus/text/2401.12345.txt",
  "fetched_at": "2026-05-16T...Z"
}
```

The manifest is **metadata-complete for the full target N**: every row has
title / authors / abstract / categories / dates even when its PDF download
failed. In that case `pdf_sha256` is `null` but `pdf_path` / `text_path` are
still named deterministically (a later re-run fills the gap).

## Categories

Core q-fin: `q-fin.PM q-fin.TR q-fin.ST q-fin.RM q-fin.CP q-fin.MF q-fin.PR`.
Plus q-fin-adjacent cross-lists `cs.LG` / `stat.ML` / `econ.EM`, but those
only count when the paper is **also** tagged q-fin somewhere (so generic ML
papers with no finance content don't leak in).

## Regenerate

Run from the **repo root** (the manifest paths are repo-root-relative), using
the `archimedes` conda env:

```bash
# default: ~200 papers, PDFs + text best-effort
python -m archimedes.services.arxiv_corpus --max 200

# metadata-only (fast, no PDF download)
python -m archimedes.services.arxiv_corpus --max 200 --no-pdfs

# custom target / output
python -m archimedes.services.arxiv_corpus --max 250 \
    --out data/corpus/manifest.jsonl --verbose
```

Re-runs are idempotent: a paper whose PDF is already cached (verified by
sha256) is not re-downloaded. The scraper is polite to the arXiv API
(~3s/request, retries) and a failed PDF download never aborts the run.

A full PDF-fetching run of ~200 papers takes several minutes (arXiv rate
limits to roughly one request every 3 seconds — that is expected).
