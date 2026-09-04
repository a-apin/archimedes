#!/usr/bin/env python3
"""Build hooks for the Archimedes docs site (issue #1381).

Both hooks exist for the same reason: **the published site's file layout is not
the repository's file layout.** `mkdocs.yml` sets `docs_dir: docs`, so mkdocs
only ever sees files under `docs/` and mounts that directory at the site root.
Two consequences, one per hook.

`on_files` — **mount the agent-generated `openwiki/` tree.** It lives at the
repository root (#1597), outside `docs_dir`, so mkdocs cannot see it at all and
the tree was published nowhere. This hook appends its markdown pages to the
build's file set with `src_dir` set to the repo root, which puts them at
`/openwiki/...` on the site. Nav entries in `mkdocs.yml` name them explicitly
rather than being generated here: a page written by an agent should be
*admitted* to the site by a person, and `backend/tests/test_docs_site.py`
fails when a new `openwiki/**.md` has no nav entry, which is what turns "should"
into "does".

`on_page_markdown` — **repoint links that cannot resolve inside the site, and
stamp provenance on agent-generated pages.** This repo's convention is "every
claim is a link to a file" (`docs/architecture.md`), so docs link out to
`../CLAUDE.md`, `backend/...`, `contracts/...`, `ui/...`. Those links are
correct on GitHub — where these docs are read day to day, and where
`docs-gate.yml`'s `docs_links.py` validates them against the real tree — but
mkdocs, scoped to `docs_dir`, cannot resolve any of them: 371 warnings on
`main` at the time of writing, every one of them a link that would 404 on the
published site. Rather than rewrite ~47 unique targets across dozens of files
to work around a generator limitation (the tradeoff the docs-site scaffold
declined, `docs/runbooks/docs-site-setup.md`), this rewrites them **at build
time** to `https://github.com/aprin-labs/archimedes/blob/main/<path>`, preserving
`#Lnn` line anchors. The committed markdown is untouched.

**The rewrite deliberately does not paper over real breakage.** A link whose
target lands *inside* `docs/` or `openwiki/` but names a file that does not
exist is left exactly as written, so mkdocs still warns and `--strict` still
fails the build. Only targets outside the site's own source trees become
GitHub URLs. See `test_docs_site.py::test_broken_in_site_link_is_left_for_strict_to_catch`.

Importability: mkdocs is imported lazily inside `on_files` so this module can be
imported by the drift test in the backend unit suite, which does not install
mkdocs. Everything else here is stdlib plus `docs_links.py`'s regexes, reused
rather than re-derived (they already carry the nested-badge-link fix from #1262).
"""

from __future__ import annotations

import posixpath
import sys
from pathlib import Path
from typing import Any

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from docs_links import BADGE_LINK, INLINE_LINK, REF_DEF, is_external, strip_code  # noqa: E402

#: Repository root: <root>/.github/scripts/mkdocs_hooks.py -> parents[1] is <root>/.github.
REPO_ROOT = _SCRIPTS_DIR.parents[1]

#: Directory mkdocs mounts at the site root (must match `docs_dir` in mkdocs.yml).
DOCS_DIR = "docs"

#: Agent-generated wiki, mounted at /openwiki/ on the site by `on_files`.
WIKI_DIR = "openwiki"

#: Site URI of the human-written page that introduces the wiki section and carries
#: its provenance note. Source file: docs/agent-wiki.md.
PROVENANCE_PAGE = "agent-wiki.md"

_BLOB_BASE = "https://github.com/aprin-labs/archimedes/blob/main"
_TREE_BASE = "https://github.com/aprin-labs/archimedes/tree/main"

#: Stamped on every openwiki page by `add_provenance_banner`. A reader arriving from
#: a search engine lands on a leaf page, not on the section index, so the label has to
#: travel with the page. `{index}` is filled with a link back to PROVENANCE_PAGE.
_BANNER = """!!! warning "Agent-generated page — not written or line-edited by a person"

    OpenWiki produced this page from the repository itself. It is published
    without line-by-line human review, and it summarises documentation rather
    than reading the implementation. Where it disagrees with the hand-written
    docs, the frozen spec, or the running system, they win. Scope and
    provenance: [Agent-generated wiki]({index}).
"""


# ── openwiki tree ────────────────────────────────────────────────────────────────────


def wiki_pages(root: Path | str | None = None) -> list[Path]:
    """Absolute paths of the `openwiki/` markdown pages that belong on the site.

    Shared with `backend/tests/test_docs_site.py` so the nav-completeness test and
    the build agree on what "an openwiki page" is instead of each deciding
    separately. Dot-prefixed path parts are skipped: `openwiki/.claims/` holds the
    machine-checkable claim sidecars OpenWiki writes alongside each page, and
    `.last-update.json` its bookkeeping — neither is a page.
    """
    root = Path(root) if root is not None else REPO_ROOT
    wiki = root / WIKI_DIR
    if not wiki.is_dir():
        return []
    pages: list[Path] = []
    for path in sorted(wiki.rglob("*.md")):
        if any(part.startswith(".") for part in path.relative_to(root).parts):
            continue
        pages.append(path)
    return pages


def on_files(files: Any, config: Any) -> Any:
    """Append the `openwiki/` tree to the build's file set."""
    # Lazy so this module imports without mkdocs — see the module docstring.
    from mkdocs.structure.files import File

    for path in wiki_pages(REPO_ROOT):
        files.append(
            File(
                path.relative_to(REPO_ROOT).as_posix(),
                str(REPO_ROOT),
                config["site_dir"],
                config["use_directory_urls"],
            )
        )
    return files


# ── link rewriting ───────────────────────────────────────────────────────────────────


def _site_uri_for(repo_path: str) -> str | None:
    """Site URI a repo-relative path would have, or None if it is not on the site."""
    if repo_path == DOCS_DIR:
        return ""
    if repo_path.startswith(DOCS_DIR + "/"):
        return repo_path[len(DOCS_DIR) + 1 :]
    if repo_path == WIKI_DIR or repo_path.startswith(WIKI_DIR + "/"):
        return repo_path
    return None


def _resolves(site_uri: str, known: set[str]) -> str | None:
    """Return the file `site_uri` addresses, following directory -> index/README."""
    for candidate in (site_uri, posixpath.join(site_uri, "index.md"), posixpath.join(site_uri, "README.md")):
        if candidate in known:
            return candidate
    return None


def rewrite_target(target: str, page_repo_uri: str, page_site_uri: str, known: set[str]) -> str | None:
    """Replacement for one link target, or None to leave it exactly as written.

    `page_repo_uri` is the page's path in the repository (`docs/architecture.md`,
    `openwiki/quickstart.md`); `page_site_uri` is its path in the mkdocs source
    tree (`architecture.md`, `openwiki/quickstart.md`). They differ for `docs/**`,
    which is why a link authored against the repo layout can be unresolvable
    against the site layout even though it is perfectly correct.
    """
    if is_external(target) or target.startswith(("#", "/")):
        return None
    path_part, sep, frag = target.partition("#")
    if not path_part:
        return None  # pure in-page anchor

    page_site_dir = posixpath.dirname(page_site_uri)

    # 1. mkdocs can already resolve it against the site tree — leave it alone.
    if _resolves(posixpath.normpath(posixpath.join(page_site_dir, path_part)), known):
        return None

    # 2. Resolve it the way the author wrote it: against the repository tree.
    repo_target = posixpath.normpath(posixpath.join(posixpath.dirname(page_repo_uri), path_part))
    if repo_target.startswith(".."):
        return None  # escapes the repository; nothing sensible to point at

    site_uri = _site_uri_for(repo_target)
    if site_uri is not None:
        found = _resolves(site_uri, known)
        if found is not None:
            new_path = posixpath.relpath(found, page_site_dir or ".")
            return new_path + sep + frag
        if not (REPO_ROOT / repo_target).exists():
            # Inside docs/ or openwiki/ and not a file anywhere: genuinely broken.
            # Leave it exactly as written so mkdocs warns and --strict fails. This
            # branch is the whole reason --strict is worth running.
            return None
        # Exists in the repository but the site does not publish it — the
        # openwiki/.claims/ evidence sidecars and .last-update.json, which
        # `wiki_pages` deliberately excludes. Fall through to a GitHub link.

    # 3. A repository path the site does not serve: link out to GitHub. Whether it
    # exists is not this build's gate — docs-gate.yml's docs_links.py checks every
    # docs/** link against the real tree, including the two into submodules/ that
    # only resolve in a recursive checkout.
    base = _TREE_BASE if (REPO_ROOT / repo_target).is_dir() else _BLOB_BASE
    return f"{base}/{repo_target}{sep}{frag}"


def rewrite_links(markdown: str, page_repo_uri: str, page_site_uri: str, known: set[str]) -> str:
    """Apply `rewrite_target` to every link target in `markdown` outside code."""
    # Matches are found in a code-blanked copy (same length, so offsets carry over)
    # and applied to the original: a path inside a fenced block is an illustration,
    # not a link.
    blanked = strip_code(markdown)
    spans: dict[tuple[int, int], str] = {}
    for pattern in (INLINE_LINK, BADGE_LINK, REF_DEF):
        for match in pattern.finditer(blanked):
            spans[match.span(1)] = match.group(1).strip()

    edits = []
    for (start, end), target in spans.items():
        replacement = rewrite_target(target, page_repo_uri, page_site_uri, known)
        if replacement is not None and replacement != target:
            edits.append((start, end, replacement))

    out = markdown
    for start, end, replacement in sorted(edits, reverse=True):
        out = out[:start] + replacement + out[end:]
    return out


def add_provenance_banner(markdown: str, page_site_uri: str) -> str:
    """Insert the agent-generated banner just below an openwiki page's first heading."""
    index_link = posixpath.relpath(PROVENANCE_PAGE, posixpath.dirname(page_site_uri) or ".")
    banner = _BANNER.format(index=index_link)

    lines = markdown.split("\n")
    for i, line in enumerate(lines):
        if line.startswith("# "):
            # Below the H1 so the page still opens with its own title.
            return "\n".join([*lines[: i + 1], "", banner, *lines[i + 1 :]])
    return banner + "\n" + markdown


# `config` is unused but the name is load-bearing: mkdocs dispatches plugin events
# by keyword (`method(item, **kwargs)`), so renaming or dropping it breaks the call.
def on_page_markdown(markdown: str, page: Any, config: Any, files: Any) -> str:  # noqa: ARG001
    known = {f.src_uri for f in files}
    page_repo_uri = Path(page.file.abs_src_path).resolve().relative_to(REPO_ROOT).as_posix()
    out = rewrite_links(markdown, page_repo_uri, page.file.src_uri, known)
    if page_repo_uri.startswith(WIKI_DIR + "/"):
        out = add_provenance_banner(out, page.file.src_uri)
    return out
