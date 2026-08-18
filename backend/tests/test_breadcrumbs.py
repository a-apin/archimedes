"""Regression: Breadcrumbs CRUMB_MAP must stay in sync with App.jsx PAGE_TO_PATH (#1219).

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
APP = REPO_ROOT / "ui" / "src" / "App.jsx"


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
    m = re.search(r"const\s+PAGE_TO_PATH\s*=\s*\{(.*?)\n\}", text, re.DOTALL)
    assert m, "PAGE_TO_PATH block not found in App.jsx"
    block = m.group(1)
    keys = re.findall(r"^\s*['\"]?([a-z0-9_-]+)['\"]?\s*:", block, re.MULTILINE | re.IGNORECASE)
    return set(keys)


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
    pages = _extract_page_to_path_keys(APP.read_text(encoding="utf-8"))
    extra = crumbs - pages
    assert not extra, (
        f"CRUMB_MAP contains keys not in PAGE_TO_PATH: {sorted(extra)}. "
        f"CRUMB_MAP={sorted(crumbs)} PAGE_TO_PATH={sorted(pages)}. "
        "Remove stale keys or add the page to App.jsx PAGE_TO_PATH (latter is wrong per #1219 anti-goals)."
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
    pages = _extract_page_to_path_keys(APP.read_text(encoding="utf-8"))
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
