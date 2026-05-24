"""Outcome Embargo — Xia et al. 2026 § 4.2.

Prevents the Oracle Fallacy: the agent must not retrieve a paper whose
published outcome could leak future information into a backtest decision.

At retrieval time *t*, papers published within ``embargo_days`` of *t*
are filtered out.  Default ``embargo_days = 30`` (configurable).

Reference: Xia et al. 2026, "Agentic Trading: When LLM Agents Meet
Financial Markets" (arxiv 2605.19337), § 4.2.
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_EMBARGO_DAYS = 30


def _parse_published(raw: str) -> date | None:
    """Parse the ``published`` column (YYYY-MM-DD or full ISO) to a date."""
    if not raw:
        return None
    try:
        # Try YYYY-MM-DD first (common arXiv format)
        return date.fromisoformat(raw[:10])
    except (ValueError, IndexError):
        pass
    try:
        return datetime.fromisoformat(raw).date()
    except (ValueError, TypeError):
        return None


def apply_outcome_embargo(
    papers: list[dict[str, Any]],
    *,
    at: date | None = None,
    embargo_days: int = DEFAULT_EMBARGO_DAYS,
) -> list[dict[str, Any]]:
    """Filter papers that respect the outcome embargo.

    Parameters
    ----------
    papers : list[dict]
        Paper dicts with at least ``published`` (YYYY-MM-DD string) and
        ``arxiv_id`` fields.
    at : date | None
        The reference date (defaults to today).  Papers published within
        ``embargo_days`` of this date are excluded.
    embargo_days : int
        Minimum age in days for a paper to be eligible (default 30).

    Returns
    -------
    list[dict]
        Papers whose publication date is before ``at - embargo_days``.
    """
    if at is None:
        at = date.today()

    cutoff = at.toordinal() - embargo_days

    result: list[dict[str, Any]] = []
    for p in papers:
        pub = _parse_published(p.get("published", ""))
        if pub is None:
            # Cannot determine age — keep (conservative: include rather
            # than silently drop papers with missing dates).
            logger.debug("embargo: paper %s has no parseable published date — keeping", p.get("arxiv_id", "?"))
            result.append(p)
            continue
        if pub.toordinal() <= cutoff:
            result.append(p)
        else:
            logger.debug(
                "embargo: paper %s published %s (< %d days from %s) — filtered",
                p.get("arxiv_id", "?"),
                pub,
                embargo_days,
                at,
            )

    return result
