"""Retired branding must not return to public surfaces (Dan, 2026-09-01).

Why this exists
---------------
The 2026-08-31 README refresh faithfully re-introduced "Linus for quantitative
finance" because CLAUDE.md still carried it as the product identity — stale
branding upstream propagated downstream through an agent doing exactly what it
was told. The owner's call: product-analogy branding ("Linus for X", "Elicit
for Y") is retired and **banned on every public surface**; competitive comps
live in the private docs repo only. A grep guard is the cheapest thing that
makes the ban self-enforcing instead of one more fact a future refresh can
silently lose.

Scope
-----
Public identity surfaces: the root markdown files, docs/ (excluding archive/,
handovers/, and runbooks/ — those record history and may quote the old brand),
openwiki/, the UI source and public assets, and the CLI/MCP surfaces.

The banned list is small on purpose: patterns land here when the owner retires
them, not speculatively.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

# Each entry: (compiled pattern, why it is banned).
BANNED = [
    (
        re.compile(r"Linus\s+for", re.IGNORECASE),
        'product-analogy branding retired 2026-09-01 ("Linus for quantitative finance")',
    ),
    (
        re.compile(r"Elicit\s+for", re.IGNORECASE),
        "product-analogy branding is banned on public surfaces (comps live in the private docs repo)",
    ),
    (
        re.compile(r"world is your portfolio", re.IGNORECASE),
        'retired tagline clause 2026-09-01 (read as a trading-app promise; replaced by "portfolio strategy, under scrutiny")',
    ),
]

PUBLIC_ROOTS = ["docs", "openwiki", "ui/src", "ui/public", "cli/src", "mcp-server/src"]
ROOT_FILES = ["README.md", "CLAUDE.md", "AGENTS.md", "SETUP.md"]

# History is allowed to quote the old brand; identity surfaces are not.
EXCLUDED_PARTS = {"archive", "handovers", "runbooks", "node_modules", "__pycache__"}

TEXT_SUFFIXES = {".md", ".txt", ".py", ".js", ".jsx", ".ts", ".tsx", ".json", ".html", ".yml", ".yaml"}


def _public_files():
    for name in ROOT_FILES:
        p = REPO / name
        if p.is_file():
            yield p
    for root in PUBLIC_ROOTS:
        base = REPO / root
        if not base.is_dir():
            continue
        for p in base.rglob("*"):
            if not p.is_file() or p.suffix not in TEXT_SUFFIXES:
                continue
            if EXCLUDED_PARTS.intersection(p.relative_to(REPO).parts):
                continue
            # This guard quotes the banned phrases in its own docstring.
            if p.name == "test_public_branding_guard.py":
                continue
            yield p


def test_no_retired_branding_on_public_surfaces():
    hits: list[str] = []
    for path in _public_files():
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            for pattern, why in BANNED:
                if pattern.search(line):
                    rel = path.relative_to(REPO)
                    hits.append(f"{rel}:{lineno}: {line.strip()[:100]}  [{why}]")
    assert not hits, (
        "Retired branding found on public surfaces — the owner banned these "
        "framings (see CLAUDE.md § Project). Remove or rewrite:\n" + "\n".join(hits)
    )


def test_the_guard_actually_rejects_the_banned_phrase(tmp_path):
    """The pattern list must fire on the exact line that shipped in the incident."""
    incident_line = "**Linus for quantitative finance.** Archimedes is a single-user agent"
    assert any(p.search(incident_line) for p, _ in BANNED)
    tagline = "The world is your portfolio."
    assert any(p.search(tagline) for p, _ in BANNED)
