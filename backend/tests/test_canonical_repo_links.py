"""Live surfaces must name the canonical repository (#1434).

`ui/src/components/WalletConnect.jsx` pointed at `a-apin/archimedes-arcadia`
for months. GitHub redirects renamed repositories, so the link *worked* — which
is exactly why nobody noticed. `docs-gate.yml`'s link checker could not catch it
either: it verifies that links resolve, and a redirect resolves.

So the check has to be on the name, not on the response. Three stale spellings
exist now. Two came from the *repository* rename (`hackagora/archimedes-arcadia`
and `a-apin/archimedes-arcadia`). The third came from the **organisation**
rename of **2026-09-01**, `a-apin` → `aprin-labs`, which makes
`a-apin/archimedes` one more redirecting spelling rather than the canonical one.
All three redirect to `aprin-labs/archimedes` today, and a redirect is a
courtesy, not a guarantee — it breaks the moment any of the old names is
re-claimed.

Scope of the scan below, stated plainly: it polices the `archimedes-arcadia`
substring, which covers both repository spellings. It does **not** yet police
the bare `a-apin` org name, because `a-apin.github.io` is quoted on purpose by
the docs-site guards (`test_docs_site.py`, `ui/test/docs-link.test.js`) to
record what #1634 moved off. `CANONICAL_REPO` below is the name those failure
messages point offenders at.

**Historical documents are deliberately exempt.** `docs/archive/`,
`docs/handovers/` and `docs/audits/` record what was true at a point in time,
and CLAUDE.md is explicit that superseded history is not rewritten. A handover
saying "origin currently redirects to hackagora/archimedes-arcadia" was accurate
when written and should stay that way. `submodules/` are separate repositories.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

CANONICAL_REPO = "aprin-labs/archimedes"

# Assembled at runtime so this file does not match its own scan.
PRE_RENAME_NAMES = ("archimedes-" + "arcadia",)

#: Trees whose contents describe the project as it is now.
LIVE_ROOTS = ("ui/src", "ui/public", "infra", "backend", "contracts", "docs/runbooks", "scripts")

#: Point-in-time records, and other people's repositories.
EXEMPT_PARTS = ("archive", "handovers", "audits", "submodules", "node_modules", "dist", ".git")

SCANNED_SUFFIXES = {".js", ".jsx", ".ts", ".tsx", ".py", ".md", ".json", ".sh", ".tf", ".yml", ".yaml", ".sol"}


#: This file names the stale spellings in prose so its failure message can quote
#: them, so it must not police itself. Excluded by resolved path rather than by
#: filename, so renaming it cannot silently reintroduce the self-match.
_SELF = Path(__file__).resolve()


def _is_exempt(path: Path) -> bool:
    return any(part in EXEMPT_PARTS for part in path.parts)


def _live_files() -> list[Path]:
    seen: list[Path] = []
    for root in LIVE_ROOTS:
        base = REPO_ROOT / root
        if not base.exists():
            continue
        for path in base.rglob("*"):
            if (
                path.is_file()
                and path.suffix in SCANNED_SUFFIXES
                and not _is_exempt(path.relative_to(REPO_ROOT))
                and path.resolve() != _SELF
            ):
                seen.append(path)
    # Root-level markdown (README.md, CLAUDE.md, SETUP.md …) describes the
    # project as it is now and is the first thing a reader opens.
    seen.extend(p for p in REPO_ROOT.glob("*.md") if p.is_file())
    return seen


def test_the_scan_reaches_the_files_it_is_policing() -> None:
    """Guard on the guard: a scan that matched nothing would pass vacuously."""
    scanned = {str(p.relative_to(REPO_ROOT)) for p in _live_files()}
    assert len(scanned) > 200, f"only {len(scanned)} files scanned — the walk is broken"
    for expected in (
        "ui/src/components/WalletConnect.jsx",
        "infra/README.md",
        "docs/runbooks/github-security-toggles.md",
        "CLAUDE.md",
    ):
        assert expected in scanned, f"{expected} not scanned — the guard is blind"


def test_the_scan_does_not_reach_historical_records() -> None:
    """The exemption must actually exempt, or this test would fail the repo's own history."""
    scanned = {str(p.relative_to(REPO_ROOT)) for p in _live_files()}
    assert not [p for p in scanned if p.startswith(("docs/archive/", "docs/handovers/", "docs/audits/"))]


@pytest.mark.parametrize("stale_name", PRE_RENAME_NAMES)
def test_no_live_surface_names_the_pre_rename_repository(stale_name: str) -> None:
    offenders: list[str] = []
    for path in _live_files():
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if stale_name in text:
            offenders.append(str(path.relative_to(REPO_ROOT)))
    assert not offenders, (
        f"{sorted(offenders)} still name '{stale_name}'. The canonical repository is "
        f"'{CANONICAL_REPO}'. GitHub's redirect makes the stale link work, so nothing "
        "else catches this — that redirect breaks if the old name is ever re-claimed."
    )
