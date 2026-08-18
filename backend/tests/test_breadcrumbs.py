"""Regression: Breadcrumbs CRUMB_MAP must stay in sync with routes.js pages (#1219).

Fourth surface carrying the retired navigation scheme — the first three were
fixed in #1192. A dead key fails silently (Breadcrumbs returns null), so the
invariant must be enforced by a test, not by eyeballing.

Hermetic: reads the committed source files, no DB / Redis / RPC / .env.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
BREADCRUMBS = REPO_ROOT / "ui" / "src" / "components" / "Breadcrumbs.jsx"
ROUTES = REPO_ROOT / "ui" / "src" / "routes.js"


def _extract_crumb_keys(text: str) -> set[str]:
    m = re.search(r"const\s+CRUMB_MAP\s*=\s*\{(.*?)\n\}", text, re.DOTALL)
    assert m, "CRUMB_MAP block not found in Breadcrumbs.jsx"
    block = m.group(1)
    # keys are: explore: or 'create-vault': or "create-vault":
    keys = re.findall(r"""^\s*['\"]?([a-z0-9_-]+)['\"]?\s*:""", block, re.MULTILINE | re.IGNORECASE)
    # fallback: unquoted without colon quote
    if not keys:
        keys = re.findall(r"^\s*['\"]?([a-zA-Z0-9_-]+)['\"]?\s*:", block, re.MULTILINE)
    return set(keys)


def _extract_page_to_path_keys(text: str) -> set[str]:
    """Every routable page name from ui/src/routes.js — the navigation SSOT.

    #1194 moved routing out of App.jsx's PAGE_TO_PATH literal into routes.js,
    so the invariant's anchor moved with it. Pages come from three places
    there: the VALUES of PUBLIC_PATHS and APP_PATHS ({'/app/explore':
    'explore', ...}), and the second element of each deepRoutes row
    (['/app/strategy/', 'strategy', 'strategyId']).
    """
    pages: set[str] = set()
    for map_name in ("PUBLIC_PATHS", "APP_PATHS"):
        m = re.search(rf"const\s+{map_name}\s*=\s*\{{(.*?)\n\}}", text, re.DOTALL)
        assert m, f"{map_name} block not found in routes.js"
        pages.update(re.findall(r":\s*'([a-z0-9_-]+)'", m.group(1)))
    m = re.search(r"const\s+deepRoutes\s*=\s*\[(.*?)\n\s*\]", text, re.DOTALL)
    assert m, "deepRoutes block not found in routes.js"
    pages.update(re.findall(r"\[\s*'[^']*'\s*,\s*'([a-z0-9_-]+)'", m.group(1)))
    return pages


def _extract_crumb_groups(text: str) -> list[str | None]:
    m = re.search(r"const\s+CRUMB_MAP\s*=\s*\{(.*?)\n\}", text, re.DOTALL)
    assert m
    block = m.group(1)
    # group: 'Discover' or group: null
    raw = re.findall(r"group\s*:\s*(?:'([^']+)'|\"([^\"]+)\"|null)", block)
    groups: list[str | None] = []
    for a, b in raw:
        if a:
            groups.append(a)
        elif b:
            groups.append(b)
        else:
            groups.append(None)
    return groups


def _extract_crumb_group_pages(text: str) -> list[str | None]:
    m = re.search(r"const\s+CRUMB_MAP\s*=\s*\{(.*?)\n\}", text, re.DOTALL)
    assert m
    block = m.group(1)
    raw = re.findall(r"groupPage\s*:\s*(?:'([^']+)'|\"([^\"]+)\"|null)", block)
    pages: list[str | None] = []
    for a, b in raw:
        if a:
            pages.append(a)
        elif b:
            pages.append(b)
        else:
            pages.append(None)
    return pages


def test_crumb_map_keys_subset_of_page_to_path() -> None:
    crumbs = _extract_crumb_keys(BREADCRUMBS.read_text(encoding="utf-8"))
    pages = _extract_page_to_path_keys(ROUTES.read_text(encoding="utf-8"))
    extra = crumbs - pages
    assert not extra, (
        f"CRUMB_MAP contains keys not in routes.js pages: {sorted(extra)}. "
        f"CRUMB_MAP={sorted(crumbs)} routes_pages={sorted(pages)}. "
        "Remove stale keys or add the page to routes.js (latter is wrong per #1219 anti-goals)."
    )


def test_crumb_map_has_no_retired_group_labels() -> None:
    text = BREADCRUMBS.read_text(encoding="utf-8")
    # only inspect the CRUMB_MAP block, not comments
    m = re.search(r"const\s+CRUMB_MAP\s*=\s*\{(.*?)\n\}", text, re.DOTALL)
    assert m
    block = m.group(1)
    assert "Markets" not in block, "retired group label 'Markets' still in CRUMB_MAP — use Discover/Strategy/etc."
    assert "Portfolio" not in block, "retired group label 'Portfolio' still in CRUMB_MAP — use Position/Market/etc."
    # also ensure the retired dead keys are gone (subset test would catch them, but give a clearer message)
    crumbs = _extract_crumb_keys(text)
    dead = {
        "strategies",
        "trade",
        "dashboard",
        "mint",
        "liquidity",
        "vaults",
        "create-vault",
        "financial",
        "vault-detail",
        "risk",
        "rigor-explainer",
    }
    leaked = crumbs & dead
    assert not leaked, f"CRUMB_MAP still contains retired keys: {sorted(leaked)}"


def test_crumb_map_group_labels_are_current_nav() -> None:
    allowed = {None, "Discover", "Strategy", "Position", "Market", "Ops"}
    groups = _extract_crumb_groups(BREADCRUMBS.read_text(encoding="utf-8"))
    bad = [g for g in groups if g not in allowed]
    assert not bad, f"CRUMB_MAP uses unknown group labels {bad} — allowed {sorted(a for a in allowed if a)} plus null"


def test_crumb_map_group_pages_are_valid() -> None:
    pages = _extract_page_to_path_keys(ROUTES.read_text(encoding="utf-8"))
    group_pages = _extract_crumb_group_pages(BREADCRUMBS.read_text(encoding="utf-8"))
    bad = [p for p in group_pages if p is not None and p not in pages]
    assert not bad, f"CRUMB_MAP groupPage values not in PAGE_TO_PATH: {bad} — must be a valid page or null"


def test_crumb_map_covers_primary_path() -> None:
    crumbs = _extract_crumb_keys(BREADCRUMBS.read_text(encoding="utf-8"))
    primary = {"generate", "leaderboard", "library", "corpus", "architecture"}
    missing = primary - crumbs
    # primary pages may be deliberately excluded, but then a comment must explain why
    if missing:
        text = BREADCRUMBS.read_text(encoding="utf-8")
        # require a comment mentioning the missing page and "deliberately" or "intentionally" or "excluded"
        for page in missing:
            assert page in text.lower() and (
                "deliberat" in text.lower() or "intentional" in text.lower() or "exclud" in text.lower()
            ), (
                f"primary page '{page}' missing from CRUMB_MAP without a deliberate-exclusion comment. "
                "Either add it with a correct group, or leave a comment explaining the exclusion per #1219."
            )
