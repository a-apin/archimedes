"""Idempotent deploy-time corpus hydration.

Iterates the committed ``manifest.jsonl`` and populates a persistent named
volume with extracted text (sha256 content-addressed, matching the
``arxiv_corpus.py`` cache contract).  Keyed off the manifest — never a fresh
arXiv search.  Skips already-cached entries (idempotent); rate-limits politely
to arXiv.

Usage (from deploy workflow or manually):

    python -m archimedes.scripts.hydrate_corpus

Environment variables:
    ARCHIMEDES_CORPUS_MANIFEST  — path to manifest.jsonl (default: auto-resolve)
    ARCHIMEDES_TEXT_DIR          — directory for extracted text (default: data/corpus/text)
    ARCHIMEDES_PDF_DIR           — directory for PDF cache (default: data/corpus/pdfs)
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import sys
import time
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_TEXT_DIR = Path("data/corpus/text")
DEFAULT_PDF_DIR = Path("data/corpus/pdfs")
# Polite delay between arXiv downloads (seconds).
_DOWNLOAD_DELAY = 3.0


def _resolve_manifest() -> Path | None:
    env = os.getenv("ARCHIMEDES_CORPUS_MANIFEST")
    if env:
        p = Path(env)
        return p if p.exists() else None
    candidates = [
        Path("data/corpus/manifest.jsonl"),
        Path("/app/data/corpus/manifest.jsonl"),
        Path("/data/corpus/manifest.jsonl"),
    ]
    for c in candidates:
        if c.exists():
            return c
    return None


def hydrate(
    manifest_path: Path | None = None,
    text_dir: Path | None = None,
    pdf_dir: Path | None = None,
) -> dict:
    manifest_path = manifest_path or _resolve_manifest()
    if manifest_path is None:
        logger.warning("hydrate: no manifest found; nothing to do")
        return {"manifest_found": False, "papers": 0, "hydrated": 0, "skipped": 0}

    text_dir = text_dir or Path(os.getenv("ARCHIMEDES_TEXT_DIR", str(DEFAULT_TEXT_DIR)))
    pdf_dir = pdf_dir or Path(os.getenv("ARCHIMEDES_PDF_DIR", str(DEFAULT_PDF_DIR)))

    text_dir.mkdir(parents=True, exist_ok=True)
    pdf_dir.mkdir(parents=True, exist_ok=True)

    papers: list[dict] = []
    with manifest_path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                papers.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    hydrated = 0
    skipped = 0
    for paper in papers:
        arxiv_id = paper.get("arxiv_id", "")
        if not arxiv_id:
            continue
        text_path = text_dir / f"{arxiv_id}.txt"
        if text_path.exists() and text_path.stat().st_size > 0:
            skipped += 1
            continue
        pdf_path = pdf_dir / f"{arxiv_id}.pdf"
        if not (pdf_path.exists() and pdf_path.stat().st_size > 0):
            pdf_url = paper.get("pdf_url", "")
            if not pdf_url:
                logger.debug("hydrate: no pdf_url for %s", arxiv_id)
                continue
            try:
                import requests

                resp = requests.get(
                    pdf_url,
                    timeout=30,
                    headers={"User-Agent": "archimedes-corpus-hydration/1.0"},
                )
                resp.raise_for_status()
                pdf_path.write_bytes(resp.content)
                logger.info("hydrate: downloaded PDF for %s", arxiv_id)
                time.sleep(_DOWNLOAD_DELAY)
            except Exception as exc:
                logger.warning("hydrate: PDF download failed for %s: %s", arxiv_id, exc)
                continue
        if pdf_path.exists() and pdf_path.stat().st_size > 0:
            try:
                import pypdf

                reader = pypdf.PdfReader(str(pdf_path))
                parts: list[str] = []
                for page in reader.pages:
                    with contextlib.suppress(Exception):
                        parts.append(page.extract_text() or "")
                text = "\n".join(parts).strip()
                if text:
                    text_path.write_text(text, encoding="utf-8")
                    hydrated += 1
                    logger.info("hydrate: extracted text for %s (%d chars)", arxiv_id, len(text))
            except Exception as exc:
                logger.warning("hydrate: text extraction failed for %s: %s", arxiv_id, exc)

    logger.info(
        "hydrate: %d papers in manifest, %d newly hydrated, %d already cached",
        len(papers),
        hydrated,
        skipped,
    )
    return {
        "manifest_found": True,
        "manifest_path": str(manifest_path),
        "papers": len(papers),
        "hydrated": hydrated,
        "skipped": skipped,
    }


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    result = hydrate()
    print(json.dumps(result, indent=2))
    return 0 if result["manifest_found"] else 1


if __name__ == "__main__":
    sys.exit(main())
