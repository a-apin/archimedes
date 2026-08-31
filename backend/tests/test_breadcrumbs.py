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
LAYOUT = REPO_ROOT / "ui" / "src" / "components" / "Layout.jsx"


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


# ---------------------------------------------------------------------------
# #1370 — shell wayfinding: self-repeating breadcrumbs, a missing crumb, and a
# sidebar item that routes outside the shell. Three new invariants below,
# reusing the extraction helpers above rather than adding new parsers.
# ---------------------------------------------------------------------------


def _extract_crumb_keys_ordered(text: str) -> list[str]:
    """Same source as _extract_crumb_keys, but preserves declaration order —
    needed to report *which* trail collides, not just that one did."""
    m = re.search(r"const\s+CRUMB_MAP\s*=\s*\{(.*?)\n\}", text, re.DOTALL)
    assert m, "CRUMB_MAP block not found in Breadcrumbs.jsx"
    block = m.group(1)
    return re.findall(r"""^\s*['\"]?([a-z0-9_-]+)['\"]?\s*:""", block, re.MULTILINE | re.IGNORECASE)


def _extract_home_page(text: str) -> str:
    """The Home crumb's navigation target, e.g. `{ label: 'Home', page: 'explore' }`.

    Parsed from source rather than hard-coded in the test: if the Home target
    ever changes, the trails built below must move with it, not silently
    compare against a stale literal.
    """
    m = re.search(r"""label:\s*['"]Home['"]\s*,\s*page:\s*['"]([a-z0-9_-]+)['"]""", text)
    assert m, "Home crumb ({ label: 'Home', page: '<page>' }) not found in Breadcrumbs.jsx"
    return m.group(1)


#: Minimum characters of prose after the em dash before an exclusion counts as
#: justified. Long enough to reject a placeholder ("n/a", "TODO", "later"),
#: short enough that a real one-clause reason clears it without padding.
MIN_EXCLUSION_REASON_CHARS = 20


def _extract_excluded_pages(text: str) -> dict[str, str]:
    """Page names -> reason, from Breadcrumbs.jsx's `Deliberately excluded:` block.

    Grammar, enforced rather than assumed (Önder, #1400 review): a bullet is
    `//   - name[, name...] — reason`. Both halves are mandatory. Wrapped
    continuation lines (`//     ...`, no leading `-`) are prose belonging to
    the bullet above, not new entries.

    This parser is deliberately strict on three things the loose earlier
    version let through, because each one silently *widens* the exclusion set
    that `test_every_app_page_has_a_crumb_or_a_documented_exclusion` trusts:

    1. **No em dash, no entry.** The old parser did `line.split("—")[0]`, which
       on an unseparated line returns the *whole* line — so `//   - account`
       registered `account` as excluded with no reason given at all, and a
       sentence of prose registered each comma-separated fragment of it as a
       page name.
    2. **Names must look like page ids** (`[a-z][a-z0-9-]*`). Prose fragments
       that slipped through rule 1 could otherwise land in the set and, by
       coincidence of wording, cover a real page.
    3. **Reasons must be substantive** (see `MIN_EXCLUSION_REASON_CHARS`), so
       `//   - account — TODO` does not clear the gate.

    Malformed bullets raise rather than being skipped: skipping would make a
    typo'd exclusion look like no exclusion, which reads as a *narrower* set
    than intended and produces a confusing failure two tests downstream.
    """
    m = re.search(r"Deliberately excluded:\n(.*?)\n// Primary path", text, re.DOTALL)
    assert m, "'Deliberately excluded:' comment block not found in Breadcrumbs.jsx"
    block = m.group(1)
    excluded: dict[str, str] = {}
    for line in block.splitlines():
        item = re.match(r"^//\s{2,}-\s+(.+)$", line)
        if not item:
            continue
        body = item.group(1)
        names_part, sep, reason = body.partition("—")
        assert sep, (
            f"malformed 'Deliberately excluded' bullet (no ' — reason' half): {line.strip()!r}. "
            "Grammar is `//   - name[, name...] — why it has no crumb`; the reason is what "
            "makes this a documented exclusion rather than a silent one."
        )
        names = [n.strip() for n in names_part.split(",") if n.strip()]
        assert names, f"'Deliberately excluded' bullet names no page: {line.strip()!r}"
        bad_names = [n for n in names if not re.fullmatch(r"[a-z][a-z0-9-]*", n)]
        assert not bad_names, (
            f"'Deliberately excluded' bullet {line.strip()!r} lists {bad_names}, which are not "
            "page ids — write prose after the em dash, not before it."
        )
        reason = reason.strip()
        assert len(reason) >= MIN_EXCLUSION_REASON_CHARS, (
            f"'Deliberately excluded' entry {names} gives reason {reason!r} "
            f"({len(reason)} chars, need >= {MIN_EXCLUSION_REASON_CHARS}) — say why the page has "
            "no crumb, so the next reader can tell a decision from an oversight."
        )
        for name in names:
            excluded[name] = reason
    return excluded


def _extract_app_paths_pages(routes_text: str) -> set[str]:
    m = re.search(r"const\s+APP_PATHS\s*=\s*\{(.*?)\n\}", routes_text, re.DOTALL)
    assert m, "APP_PATHS block not found in routes.js"
    return set(re.findall(r":\s*'([a-z0-9_-]+)'", m.group(1)))


def _extract_layout_nav_ids(layout_text: str) -> list[str]:
    m = re.search(r"const\s+NAV\s*=\s*\[(.*?)\n\];", layout_text, re.DOTALL)
    assert m, "NAV block not found in Layout.jsx"
    return re.findall(r'id:\s*"([a-z0-9_-]+)"', m.group(1))


def test_no_page_appears_twice_in_one_trail() -> None:
    """A breadcrumb trail that lists the same page twice is a link back to the
    page you're already on wearing a different label (#1370 item 1: Home and
    the Discover mid-crumb both pointed at 'explore', so /app/corpus read
    'Home / Discover / Corpus' with Home and Discover both going nowhere new).
    """
    text = BREADCRUMBS.read_text(encoding="utf-8")
    home_page = _extract_home_page(text)
    keys = _extract_crumb_keys_ordered(text)
    groups = _extract_crumb_groups(text)
    group_pages = _extract_crumb_group_pages(text)
    assert len(keys) == len(groups) == len(group_pages), (
        f"CRUMB_MAP has {len(keys)} entries but {len(groups)} group: values and "
        f"{len(group_pages)} groupPage: values — every entry must declare exactly one of each."
    )
    dupes: dict[str, list[str]] = {}
    # strict=True is belt-and-braces: the length assert above already fires
    # first with a better message, but B905 wants the intent stated.
    for key, group, group_page in zip(keys, groups, group_pages, strict=True):
        trail = [home_page]
        if group:
            trail.append(group_page)
        trail.append(key)
        if len(set(trail)) != len(trail):
            dupes[key] = trail
    assert not dupes, f"trails that visit the same page twice: {dupes}"


def test_every_app_page_has_a_crumb_or_a_documented_exclusion() -> None:
    """Guards against a *silently* omitted crumb, NOT against a missing crumb.

    Read the name literally — the "or a documented exclusion" half is the whole
    point, and it is satisfiable by writing a comment. A page with no crumb
    passes this test the moment someone adds it to Breadcrumbs.jsx's
    `Deliberately excluded:` block with a reason; `explore` and `architecture`
    clear it exactly that way, and that is the intended behaviour, not a hole.
    So: green here means every crumb-less APP_PATHS page has been *noticed and
    justified in the file*. It does NOT mean every page renders a trail. Do not
    cite this test as evidence that breadcrumbs are complete (Önder, #1400
    review — the earlier docstring invited exactly that over-reading).

    What it does buy: a CRUMB_MAP miss fails silently at runtime (`if (!info)
    return null` — #1219's original bug), so the only cheap defence is to force
    the omission to be written down. #1370 item 2 is the failure mode: 'account'
    was a real APP_PATHS route with no crumb and no exclusion entry, so it
    rendered no trail and nothing said so.

    Three checks give the "documented" half teeth, since a comment is otherwise
    free to write. `_extract_excluded_pages` enforces the bullet grammar and a
    substantive reason; here we additionally require that an exclusion names a
    page that actually exists, and that it does not contradict CRUMB_MAP.
    """
    text = BREADCRUMBS.read_text(encoding="utf-8")
    routes_text = ROUTES.read_text(encoding="utf-8")
    app_pages = _extract_app_paths_pages(routes_text)
    all_pages = _extract_page_to_path_keys(routes_text)
    crumbs = _extract_crumb_keys(text)
    excluded = _extract_excluded_pages(text)

    # An exclusion for a page that no longer exists is dead config that keeps
    # this test green while covering nothing — the same unreachable-dead-config
    # failure mode #1370 removed the `architecture` CRUMB_MAP key for.
    phantom = sorted(set(excluded) - all_pages)
    assert not phantom, (
        f"'Deliberately excluded' names pages that are not routes at all: {phantom}. "
        "Every excluded name must be a page in routes.js (PUBLIC_PATHS, APP_PATHS or a "
        "deepRoutes row); drop the entry if the route is gone."
    )

    # An entry that is both excluded and mapped is a contradiction: whichever
    # half the reader believes, the other one is wrong.
    contradictory = sorted(set(excluded) & crumbs)
    assert not contradictory, (
        f"pages listed as deliberately excluded but also present in CRUMB_MAP: {contradictory}. "
        "Remove the exclusion comment or remove the crumb — the file must not claim both."
    )

    missing = sorted(app_pages - crumbs - set(excluded))
    assert not missing, f"APP_PATHS pages with neither a crumb nor a documented exclusion: {missing}"


def test_shell_nav_items_stay_inside_the_shell() -> None:
    """#1370 item 4: Layout.jsx's Architecture nav item routed to the public
    `/architecture` path (routes.js PUBLIC_PATHS, not APP_PATHS) — clicking it
    unmounted the whole authenticated shell, sidebar included, with no way
    back to where the user was. Every non-Home NAV id must resolve inside /app.
    """
    layout_text = LAYOUT.read_text(encoding="utf-8")
    app_pages = _extract_app_paths_pages(ROUTES.read_text(encoding="utf-8"))
    nav_ids = [i for i in _extract_layout_nav_ids(layout_text) if i != "landing"]
    bad = sorted(set(nav_ids) - app_pages)
    assert not bad, f"sidebar nav items that route outside /app (shell disappears): {bad}"
