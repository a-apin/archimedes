"""Corpus service — DB-backed paper corpus with seed, intake, and reads.

Replaces the static ``manifest.jsonl`` with a Postgres-backed corpus.
The manifest is now a *seed source* only; all reads go through the DB.

Intake pulls new papers from the arXiv API (OAI-PMH-style bulk fetch),
deduplicates by arxiv_id, and enforces CORPUS_MAX.
"""

from __future__ import annotations

import json
import logging
import os
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import func

from archimedes.db import get_session
from archimedes.models.corpus_store import CorpusMetaRecord, PaperRecord

logger = logging.getLogger(__name__)

CORPUS_MAX = int(os.getenv("CORPUS_MAX", "2000"))

# arXiv categories to pull during incremental intake
QFIN_CATEGORIES = [
    "q-fin.CP",  # Computational Finance
    "q-fin.EC",  # Economics
    "q-fin.GN",  # General Finance
    "q-fin.MF",  # Mathematical Finance
    "q-fin.PM",  # Portfolio Management
    "q-fin.PR",  # Pricing of Securities
    "q-fin.RM",  # Risk Management
    "q-fin.ST",  # Statistical Trading
    "q-fin.TR",  # Trading and Market Microstructure
]


def seed_from_manifest(manifest_path: Path | None = None) -> int:
    """Idempotently upsert manifest.jsonl rows into the papers table.

    Returns the number of new rows inserted (0 if already seeded).
    """
    if manifest_path is None:
        env = os.getenv("ARCHIMEDES_CORPUS_MANIFEST")
        if env:
            manifest_path = Path(env)
        else:
            here = Path(__file__).resolve()
            candidates = [
                here.parents[3] / "data" / "corpus" / "manifest.jsonl",
                Path("/app/data/corpus/manifest.jsonl"),
            ]
            manifest_path = next((c for c in candidates if c.exists()), None)

    if manifest_path is None or not manifest_path.exists():
        logger.info("corpus: no manifest to seed from")
        return 0

    try:
        raw = manifest_path.read_text(encoding="utf-8")
    except OSError as exc:
        logger.warning("corpus: cannot read manifest %s: %s", manifest_path, exc)
        return 0

    rows: list[dict[str, Any]] = []
    for lineno, line in enumerate(raw.splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            logger.debug("corpus: skip manifest line %d (bad JSON)", lineno)
            continue
        arxiv_id = str(obj.get("arxiv_id", "")).strip()
        if not arxiv_id:
            continue
        rows.append(obj)

    if not rows:
        return 0

    inserted = 0
    now = datetime.now(UTC)
    with get_session() as session:
        existing = {r[0] for r in session.query(PaperRecord.arxiv_id).all()}
        for obj in rows:
            arxiv_id = str(obj.get("arxiv_id", "")).strip()
            if arxiv_id in existing:
                continue
            authors = obj.get("authors", [])
            categories = obj.get("categories", [])
            record = PaperRecord(
                arxiv_id=arxiv_id,
                title=str(obj.get("title", "")).strip(),
                authors=json.dumps(authors if isinstance(authors, list) else [authors]),
                abstract=str(obj.get("abstract", "")).strip(),
                primary_category=str(obj.get("primary_category", "")).strip(),
                categories=json.dumps(categories if isinstance(categories, list) else [categories]),
                published=str(obj.get("published", "")).strip(),
                updated=str(obj.get("updated", "")).strip(),
                pdf_url=obj.get("pdf_url"),
                pdf_sha256=obj.get("pdf_sha256"),
                full_text_path=obj.get("text_path") or obj.get("full_text_path"),
                source="seed",
                ingested_at=now,
            )
            session.add(record)
            existing.add(arxiv_id)
            inserted += 1
        session.commit()

        _update_meta(session, source="seed")
        logger.info("corpus: seeded %d new papers (total %d)", inserted, len(existing))
    return inserted


def _update_meta(session, *, source: str = "unknown") -> None:
    """Upsert the singleton corpus_meta row."""
    meta = session.query(CorpusMetaRecord).first()
    count = session.query(func.count(PaperRecord.arxiv_id)).scalar() or 0
    if meta is None:
        meta = CorpusMetaRecord(
            last_intake_at=datetime.now(UTC),
            paper_count=count,
            source=source,
        )
        session.add(meta)
    else:
        meta.last_intake_at = datetime.now(UTC)
        meta.paper_count = count
        meta.source = source
    session.flush()


def intake_from_arxiv(max_results: int | None = None) -> int:
    """Pull new q-fin papers from the arXiv API, dedup, upsert.

    Uses the arXiv Atom API (no key required, rate-limit polite).
    Returns the number of new papers inserted.
    """
    import httpx

    cap = max_results or (CORPUS_MAX - get_paper_count())
    if cap <= 0:
        logger.info("corpus: at CORPUS_MAX (%d), skipping intake", CORPUS_MAX)
        return 0

    # Build query for q-fin categories
    cat_query = "+OR+".join(f"cat:{c}" for c in QFIN_CATEGORIES)
    url = (
        f"https://export.arxiv.org/api/query"
        f"?search_query=({cat_query})"
        f"&sortBy=submittedDate&sortOrder=descending"
        f"&max_results={min(cap, 200)}"
    )

    try:
        resp = httpx.get(url, timeout=30.0)
        resp.raise_for_status()
    except Exception as exc:
        logger.warning("corpus: arxiv API failed: %s", exc)
        return 0

    # Parse Atom feed
    ns = {"atom": "http://www.w3.org/2005/Atom"}
    try:
        root = ET.fromstring(resp.text)
    except ET.ParseError as exc:
        logger.warning("corpus: arxiv XML parse failed: %s", exc)
        return 0

    entries = root.findall("atom:entry", ns)
    if not entries:
        return 0

    now = datetime.now(UTC)
    inserted = 0
    with get_session() as session:
        existing = {r[0] for r in session.query(PaperRecord.arxiv_id).all()}
        for entry in entries:
            id_elem = entry.find("atom:id", ns)
            if id_elem is None or not id_elem.text:
                continue
            # Extract arxiv_id from URL like http://arxiv.org/abs/2605.12345v1
            arxiv_url = id_elem.text.strip()
            arxiv_id = arxiv_url.split("/abs/")[-1]
            # Strip version suffix
            if "v" in arxiv_id and arxiv_id[-1].isdigit():
                parts = arxiv_id.rsplit("v", 1)
                if parts[1].isdigit():
                    arxiv_id = parts[0]

            if arxiv_id in existing:
                continue

            title_elem = entry.find("atom:title", ns)
            summary_elem = entry.find("atom:summary", ns)
            published_elem = entry.find("atom:published", ns)
            updated_elem = entry.find("atom:updated", ns)

            title = (title_elem.text or "").strip().replace("\n", " ") if title_elem is not None else ""
            abstract = (summary_elem.text or "").strip().replace("\n", " ") if summary_elem is not None else ""
            published = published_elem.text.strip()[:10] if published_elem is not None and published_elem.text else ""
            updated = updated_elem.text.strip()[:10] if updated_elem is not None and updated_elem.text else ""

            # Extract categories
            categories = []
            primary_category = ""
            for cat_elem in entry.findall("atom:category", ns):
                term = cat_elem.get("term", "")
                if term:
                    categories.append(term)
            for _pc_elem in entry.findall("atom:primary_category", ns):
                # arXiv uses a non-namespaced primary_category attribute in some feeds
                pass
            primary_category = categories[0] if categories else ""
            # Also check arxiv:primary_category
            for pc in entry.findall("{http://arxiv.org/schemas/atom}primary_category"):
                primary_category = pc.get("term", primary_category)

            # Authors
            authors = []
            for author_elem in entry.findall("atom:author", ns):
                name_elem = author_elem.find("atom:name", ns)
                if name_elem is not None and name_elem.text:
                    authors.append(name_elem.text.strip())

            pdf_url = arxiv_url.replace("/abs/", "/pdf/") + ".pdf"

            record = PaperRecord(
                arxiv_id=arxiv_id,
                title=title,
                authors=json.dumps(authors),
                abstract=abstract,
                primary_category=primary_category,
                categories=json.dumps(categories),
                published=published,
                updated=updated,
                pdf_url=pdf_url,
                source="arxiv_api",
                ingested_at=now,
            )
            session.add(record)
            existing.add(arxiv_id)
            inserted += 1

        session.commit()
        _update_meta(session, source="arxiv_api")
        logger.info("corpus: intake inserted %d new papers (total %d)", inserted, len(existing))

    return inserted


def get_paper_count() -> int:
    """Return current paper count in the DB."""
    with get_session() as session:
        return session.query(func.count(PaperRecord.arxiv_id)).scalar() or 0


def get_corpus_meta() -> dict | None:
    """Return the singleton corpus_meta row as a dict, or None."""
    with get_session() as session:
        meta = session.query(CorpusMetaRecord).first()
        if meta is None:
            return None
        return {
            "last_intake_at": meta.last_intake_at.isoformat() if meta.last_intake_at else None,
            "paper_count": meta.paper_count,
            "source": meta.source,
            "corpus_hash": meta.corpus_hash,
            "artifact_hash": meta.artifact_hash,
            "artifact_built_at": meta.artifact_built_at.isoformat() if meta.artifact_built_at else None,
        }


def load_papers_from_db(
    *,
    embargo_days: int = 30,
    decay_lambda: float = 0.002,
    regime: str = "risk_on",
    apply_embargo: bool = True,
    apply_decay: bool = True,
) -> list[dict]:
    """Load papers from DB with Xia 2026 protocol enforcement.

    Parameters
    ----------
    embargo_days : int
        Outcome Embargo window (default 30 days).
    decay_lambda : float
        Time-Aware Retrieval base decay rate (default 0.002/day).
    regime : str
        Current regime for regime-aware λ scaling.
    apply_embargo : bool
        Whether to apply Outcome Embargo filtering.
    apply_decay : bool
        Whether to apply Time-Aware Retrieval scoring.

    Returns
    -------
    list[dict]
        Paper dicts, embargo-filtered and time-scored.
    """
    from archimedes.services.embargo_filter import apply_outcome_embargo
    from archimedes.services.time_aware_retrieval import (
        apply_time_aware_retrieval,
        regime_lambda,
    )

    with get_session() as session:
        rows = session.query(PaperRecord).order_by(PaperRecord.published.desc()).all()
        papers = [r.to_dict() for r in rows]

    if apply_embargo:
        papers = apply_outcome_embargo(papers, embargo_days=embargo_days)

    if apply_decay:
        lam = regime_lambda(decay_lambda, regime=regime)
        papers = apply_time_aware_retrieval(papers, lam=lam)

    return papers
