"""Static map of arxiv category codes → plain-English labels.

Applied at API serialization for the corpus catalog so non-finance users
can read the page without a glossary. Per Phase 3b of the Spine+ v2 plan.
"""

from __future__ import annotations

CATEGORY_LABELS: dict[str, str] = {
    # q-fin
    "q-fin.ST": "Statistical Finance",
    "q-fin.MF": "Mathematical Finance",
    "q-fin.CP": "Computational Finance",
    "q-fin.RM": "Risk Management",
    "q-fin.PM": "Portfolio Management",
    "q-fin.TR": "Trading & Market Microstructure",
    "q-fin.GN": "General Finance",
    "q-fin.PR": "Pricing of Securities",
    "q-fin.EC": "Economics (within q-fin)",
    # cs
    "cs.LG": "Machine Learning",
    "cs.CL": "Natural Language Processing",
    "cs.CE": "Computational Engineering / Finance",
    "cs.AI": "Artificial Intelligence",
    "cs.NE": "Neural & Evolutionary Computing",
    # stat
    "stat.ME": "Statistical Methodology",
    "stat.ML": "Machine Learning (statistics)",
    "stat.AP": "Applied Statistics",
    "stat.TH": "Statistical Theory",
    # math
    "math.OC": "Optimization & Control",
    "math.PR": "Probability",
    "math.ST": "Mathematical Statistics",
    # econ
    "econ.GN": "General Economics",
    "econ.EM": "Econometrics",
    "econ.TH": "Economic Theory",
    # physics adjacents
    "physics.soc-ph": "Social Physics (econophysics)",
    "physics.data-an": "Data Analysis & Statistics (physics)",
    # quant-ph
    "quant-ph": "Quantum Methods",
}


def label_for(code: str | None) -> str | None:
    """Return the plain-English label for an arxiv code, or None if unknown."""
    if not code:
        return None
    return CATEGORY_LABELS.get(code)
