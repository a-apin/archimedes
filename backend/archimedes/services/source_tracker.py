"""Source Tracking — Xia et al. 2026 § 4.3.

Every agent decision trace records the content hashes of papers it consulted,
binding the decision to a specific, verifiable corpus snapshot.

This module provides helpers for:
  - Building ``consulted_paper_hashes`` lists from paper dicts.
  - Resolving corpus content hashes for a set of arXiv ids.
  - Verifying that claimed source papers exist in the corpus.

Reference: Xia et al. 2026 (arxiv 2605.19337), § 4.3.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from typing import Any

logger = logging.getLogger(__name__)


class CorpusUnavailable(RuntimeError):
    """The corpus could not be read — as distinct from "it has no such paper".

    Raised by :func:`corpus_content_hashes` when the database itself is
    unreachable. Callers must NOT collapse this into an empty result: an
    outage that reads as "none of these papers exist" turns a verification
    endpoint into a machine that reports fabricated provenance whenever
    Postgres blinks (the same failure class #1359 fixed for the on-chain half
    of ``/verify``).
    """


def build_consulted_hashes(papers: Iterable[dict[str, Any]]) -> list[str]:
    """Extract a sorted list of ``{arxiv_id}:{content_hash}`` strings.

    Accepts ``assoc/v1`` records (``models/paper_assoc.py``) and the legacy
    ``{arxiv_id, content_hash|pdf_sha256}`` paper dicts alike.

    Two properties this function's callers depend on (#1637):

    * **Every cited paper appears.** The suffix is empty when no content hash
      is known — ``"2301.00001:"`` — rather than the entry being dropped. The
      field's job is to say *which papers the decision consulted*; omitting the
      ones whose hash has not been hydrated (#1091: the corpus columns are NULL
      in production) under-reports the decision itself. An empty suffix claims
      nothing, and :func:`verify_source_papers` treats it as "no hash asserted"
      and checks existence only. This is deliberately a different policy from
      ``paper_trace.resolve_paper_hashes``, which *does* drop unresolved ids
      because on that path the bare ids are separately recorded in
      ``market_context.source_arxiv_ids``.
    * **Deterministic and de-duplicated.** Sorted, with duplicates collapsed,
      so the same cited set always produces the same hashed field regardless of
      the order a caller happened to assemble it in.

    Associations whose ``role`` is not ``"cited"`` are excluded: a *considered*
    paper is one the selector surfaced and the strategy did not use, and
    binding a decision to it would claim a provenance that does not exist.
    """
    entries: set[str] = set()
    for p in papers or []:
        arxiv_id = (p.get("arxiv_id") or "").strip()
        if not arxiv_id:
            continue
        if p.get("role") not in (None, "cited"):
            continue
        content_hash = p.get("content_hash") or p.get("pdf_sha256") or ""
        entries.add(f"{arxiv_id}:{content_hash}")
    return sorted(entries)


def split_consulted_entry(entry: str) -> tuple[str, str]:
    """``"2301.1:abc"`` → ``("2301.1", "abc")``; ``"2301.1:"`` → ``("2301.1", "")``."""
    arxiv_id, _, claimed = entry.partition(":")
    return arxiv_id, claimed


def corpus_content_hashes(arxiv_ids: Iterable[str]) -> dict[str, str]:
    """``{arxiv_id: content_hash}`` for the requested ids **found in the corpus**.

    A paper present with no hydrated hash maps to ``""`` — that is what makes
    "the corpus has this paper but no hash for it" distinguishable from "the
    corpus does not have this paper", which is the distinction every caller
    here actually needs. Ids absent from the corpus are absent from the result.

    Raises :class:`CorpusUnavailable` when the database is unreachable. Bugs in
    this function (a wrong model class, a malformed query) are deliberately NOT
    converted — the imports sit outside the ``try`` and only the DBAPI family is
    caught, so a typo cannot masquerade as an outage. That exact defect shipped
    once already: ``resolve_paper_hashes`` imported a class name that did not
    exist, swallowed the ``ImportError``, and returned "nothing resolves"
    permanently and indistinguishably from the honest empty answer.
    """
    wanted = sorted({i.strip() for i in (arxiv_ids or []) if i and i.strip()})
    if not wanted:
        return {}

    from sqlalchemy.exc import DBAPIError

    from archimedes.db import get_session
    from archimedes.models.corpus_store import PaperRecord

    try:
        with get_session() as session:
            rows = session.query(PaperRecord).filter(PaperRecord.arxiv_id.in_(wanted)).all()
    except DBAPIError as exc:
        logger.warning("source_tracker: corpus content-hash lookup failed", exc_info=True)
        raise CorpusUnavailable("corpus content-hash lookup failed") from exc

    # Outside the try: an attribute this code is wrong about is a bug, not an
    # outage, and must not be laundered into an empty dict.
    return {row.arxiv_id: (row.content_hash or row.pdf_sha256 or "") for row in rows}


def verify_source_papers(
    consulted_hashes: list[str],
    corpus: list[dict[str, Any]],
) -> dict[str, Any]:
    """Verify that all consulted papers exist in the current corpus.

    Parameters
    ----------
    consulted_hashes : list[str]
        ``arxiv_id:hash`` strings from a trace.
    corpus : list[dict]
        Current paper corpus dicts.

    Returns
    -------
    dict
        ``{"verified": bool, "missing": list[str], "hash_mismatch": list[str]}``

    An **empty claimed hash asserts nothing** and is checked for existence
    only. That is the honest reading while the corpus's ``content_hash`` /
    ``pdf_sha256`` columns are NULL (#1091): treating ``""`` as a hash to
    compare would either fail every trace or, worse, invite synthesizing a
    value on the corpus side to make the comparison pass.
    """
    corpus_by_id: dict[str, str] = {}
    for p in corpus:
        aid = p.get("arxiv_id", "")
        ch = p.get("content_hash") or p.get("pdf_sha256") or ""
        if aid:
            corpus_by_id[aid] = ch

    missing: list[str] = []
    hash_mismatch: list[str] = []

    for entry in consulted_hashes:
        arxiv_id, claimed_hash = split_consulted_entry(entry)

        if arxiv_id not in corpus_by_id:
            missing.append(arxiv_id)
        elif claimed_hash and corpus_by_id[arxiv_id] != claimed_hash:
            hash_mismatch.append(arxiv_id)

    return {
        "verified": len(missing) == 0 and len(hash_mismatch) == 0,
        "missing": missing,
        "hash_mismatch": hash_mismatch,
    }
