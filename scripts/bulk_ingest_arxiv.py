#!/usr/bin/env python3
"""Bulk ingest q-fin papers from arXiv API.

Pages through the arXiv search API in batches of 200, pulling papers
matching the 7 q-fin categories. Writes to manifest.jsonl (append-safe,
dedup by arxiv_id).

Usage:
    python scripts/bulk_ingest_arxiv.py [--max 10000] [--output data/corpus/manifest.jsonl]

Resumable: reads existing manifest to get already-ingested IDs, then
pulls only new ones starting from the newest.
"""

import argparse
import json
import logging
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import httpx

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

QFIN_CATEGORIES = [
    "q-fin.CP",
    "q-fin.EC",
    "q-fin.GN",
    "q-fin.MF",
    "q-fin.PM",
    "q-fin.PR",
    "q-fin.RM",
    "q-fin.ST",
    "q-fin.TR",
]

API_BASE = "https://export.arxiv.org/api/query"
BATCH_SIZE = 200  # arXiv max per request
POLITE_DELAY = 5  # seconds between requests
BACKOFF_BASE = 60  # seconds, doubled on each 429


def load_existing_ids(path: Path) -> set[str]:
    """Load arxiv_ids from existing manifest."""
    ids = set()
    if not path.exists():
        return ids
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            aid = obj.get("arxiv_id", "").strip()
            if aid:
                ids.add(aid)
        except json.JSONDecodeError:
            continue
    return ids


def fetch_batch(start: int, max_results: int) -> list[dict]:
    """Fetch one batch from arXiv API."""
    cat_query = "+OR+".join(f"cat:{c}" for c in QFIN_CATEGORIES)
    url = (
        f"{API_BASE}?search_query=({cat_query})"
        f"&sortBy=submittedDate&sortOrder=descending"
        f"&start={start}&max_results={max_results}"
    )

    resp = httpx.get(url, timeout=60.0)
    resp.raise_for_status()

    ns = {"atom": "http://www.w3.org/2005/Atom"}
    root = ET.fromstring(resp.text)

    # Total results
    total_elem = root.find("{http://a9.com/-/spec/opensearch/1.1/}totalResults")
    total = int(total_elem.text) if total_elem is not None else 0

    entries = root.findall("atom:entry", ns)
    papers = []

    for entry in entries:
        id_elem = entry.find("atom:id", ns)
        if id_elem is None or not id_elem.text:
            continue

        arxiv_url = id_elem.text.strip()
        arxiv_id = arxiv_url.split("/abs/")[-1]
        # Strip version suffix
        if "v" in arxiv_id and arxiv_id[-1].isdigit():
            parts = arxiv_id.rsplit("v", 1)
            if parts[1].isdigit():
                arxiv_id = parts[0]

        title_elem = entry.find("atom:title", ns)
        summary_elem = entry.find("atom:summary", ns)
        published_elem = entry.find("atom:published", ns)
        updated_elem = entry.find("atom:updated", ns)

        title = (title_elem.text or "").strip().replace("\n", " ") if title_elem is not None else ""
        abstract = (summary_elem.text or "").strip().replace("\n", " ") if summary_elem is not None else ""
        published = published_elem.text.strip()[:10] if published_elem is not None and published_elem.text else ""
        updated = updated_elem.text.strip()[:10] if updated_elem is not None and updated_elem.text else ""

        categories = []
        primary_category = ""
        for cat_elem in entry.findall("atom:category", ns):
            term = cat_elem.get("term", "")
            if term:
                categories.append(term)
        primary_category = categories[0] if categories else ""
        for pc in entry.findall("{http://arxiv.org/schemas/atom}primary_category"):
            primary_category = pc.get("term", primary_category)

        authors = []
        for author_elem in entry.findall("atom:author", ns):
            name_elem = author_elem.find("atom:name", ns)
            if name_elem is not None and name_elem.text:
                authors.append(name_elem.text.strip())

        pdf_url = arxiv_url.replace("/abs/", "/pdf/") + ".pdf"

        papers.append(
            {
                "arxiv_id": arxiv_id,
                "title": title,
                "authors": authors,
                "abstract": abstract,
                "primary_category": primary_category,
                "categories": categories,
                "published": published,
                "updated": updated,
                "pdf_url": pdf_url,
                "pdf_sha256": None,
            }
        )

    return papers, total


def main():
    parser = argparse.ArgumentParser(description="Bulk ingest q-fin papers from arXiv")
    parser.add_argument("--max", type=int, default=10000, help="Target paper count")
    parser.add_argument("--output", type=str, default="data/corpus/manifest.jsonl")
    args = parser.parse_args()

    output_path = Path(args.output)
    existing_ids = load_existing_ids(output_path)
    logger.info("Existing papers: %d", len(existing_ids))

    # We'll overwrite with deduped set
    all_papers: dict[str, dict] = {}
    if output_path.exists():
        for line in output_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                aid = obj.get("arxiv_id", "").strip()
                if aid:
                    all_papers[aid] = obj
            except json.JSONDecodeError:
                continue

    start = 0
    total_fetched = 0
    new_count = 0
    backoff = BACKOFF_BASE

    while True:
        remaining = args.max - len(all_papers)
        if remaining <= 0:
            logger.info("Reached target of %d papers", args.max)
            break

        batch_size = min(BATCH_SIZE, remaining + 200)  # fetch a bit extra for dups
        logger.info("Fetching batch start=%d, max_results=%d...", start, batch_size)

        try:
            papers, total_available = fetch_batch(start, batch_size)
            backoff = BACKOFF_BASE  # reset on success
        except Exception as exc:
            logger.error("arXiv API error: %s", exc)
            logger.info("Waiting %ds before retry...", backoff)
            time.sleep(backoff)
            backoff = min(backoff * 2, 300)  # cap at 5 min
            continue

        if not papers:
            logger.info("No more papers from arXiv API")
            break

        batch_new = 0
        for p in papers:
            aid = p["arxiv_id"]
            if aid not in all_papers:
                all_papers[aid] = p
                batch_new += 1

        new_count += batch_new
        total_fetched += len(papers)
        logger.info(
            "Batch: %d fetched, %d new, total unique: %d / %d available",
            len(papers),
            batch_new,
            len(all_papers),
            total_available,
        )

        if batch_new == 0 and start > 0:
            # All papers in this batch were dups — we've exhausted new content
            logger.info("No new papers in batch, stopping")
            break

        start += len(papers)
        time.sleep(POLITE_DELAY)

    # Write sorted by published date (newest first)
    sorted_papers = sorted(
        all_papers.values(),
        key=lambda p: p.get("published", ""),
        reverse=True,
    )

    # Write to temp then atomic rename
    tmp_path = output_path.with_suffix(".jsonl.tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        for p in sorted_papers:
            f.write(json.dumps(p, ensure_ascii=False) + "\n")
    tmp_path.rename(output_path)

    logger.info(
        "Done: %d papers written to %s (%d new)",
        len(sorted_papers),
        output_path,
        new_count,
    )


if __name__ == "__main__":
    main()
