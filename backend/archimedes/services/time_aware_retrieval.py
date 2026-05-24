"""Time-Aware Retrieval — Xia et al. 2026 § 4.2.

Prevents regime drift: old papers with high feature similarity may mislead
post-regime-change.  SPECTER2 retrieval scores are multiplied by a decay
term ``exp(-λ * age_days)`` so that recent papers are preferred when
similarity is comparable.

Default ``λ = 0.002/day`` (half-life ≈ 346 days ≈ 1 year).
Higher ``λ`` in high-volatility regimes (regime-aware tuning).

Reference: Xia et al. 2026 (arxiv 2605.19337), § 4.2.
"""

from __future__ import annotations

import logging
import math
from datetime import date, datetime
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_LAMBDA = 0.002  # per day — ~1-year half-life

# Regime-aware λ multipliers (higher λ = stronger recency preference)
_REGIME_LAMBDA_SCALE: dict[str, float] = {
    "risk_on": 1.0,  # Normal — default decay
    "transition": 1.5,  # Elevated — prefer recent papers more
    "risk_off": 2.5,  # Crisis — strong recency bias
}


def _parse_published(raw: str) -> date | None:
    """Parse the ``published`` column to a date."""
    if not raw:
        return None
    try:
        return date.fromisoformat(raw[:10])
    except (ValueError, IndexError):
        pass
    try:
        return datetime.fromisoformat(raw).date()
    except (ValueError, TypeError):
        return None


def decayed_score(
    similarity: float,
    *,
    age_days: float,
    lam: float = DEFAULT_LAMBDA,
) -> float:
    """Compute the time-decayed retrieval score.

    ``score = similarity * exp(-λ * age_days)``

    Parameters
    ----------
    similarity : float
        Raw similarity score (e.g. SPECTER2 cosine, 0–1).
    age_days : float
        Days since paper publication.
    lam : float
        Decay rate (default 0.002/day).

    Returns
    -------
    float
        Decayed score in [0, similarity].
    """
    if age_days < 0:
        return similarity
    return similarity * math.exp(-lam * age_days)


def regime_lambda(base_lambda: float = DEFAULT_LAMBDA, regime: str = "risk_on") -> float:
    """Return λ scaled by the current regime.

    Parameters
    ----------
    base_lambda : float
        Base decay rate.
    regime : str
        Current regime classification (risk_on / transition / risk_off).

    Returns
    -------
    float
        Scaled decay rate.
    """
    scale = _REGIME_LAMBDA_SCALE.get(regime, 1.0)
    return base_lambda * scale


def apply_time_aware_retrieval(
    papers: list[dict[str, Any]],
    *,
    now: date | None = None,
    lam: float = DEFAULT_LAMBDA,
    score_field: str = "similarity",
) -> list[dict[str, Any]]:
    """Re-score papers by time-decayed similarity.

    Each paper dict gains (or overwrites) ``time_aware_score``.
    Papers are returned sorted by ``time_aware_score`` descending.

    Parameters
    ----------
    papers : list[dict]
        Paper dicts with ``published`` (YYYY-MM-DD) and ``similarity``
        (float 0–1) fields.
    now : date | None
        Reference date (defaults to today).
    lam : float
        Decay rate.
    score_field : str
        Key for the raw similarity score in the paper dict.

    Returns
    -------
    list[dict]
        Papers with ``time_aware_score`` added, sorted descending.
    """
    if now is None:
        now = date.today()
    now_ord = now.toordinal()

    for p in papers:
        raw_sim = p.get(score_field, 0.0)
        if not isinstance(raw_sim, (int, float)):
            raw_sim = 0.0
        pub = _parse_published(p.get("published", ""))
        if pub is None:
            p["time_aware_score"] = raw_sim  # No date — no decay
            p["age_days"] = 0
            continue
        age = now_ord - pub.toordinal()
        p["age_days"] = age
        p["time_aware_score"] = decayed_score(raw_sim, age_days=max(age, 0), lam=lam)

    return sorted(papers, key=lambda p: p.get("time_aware_score", 0.0), reverse=True)
