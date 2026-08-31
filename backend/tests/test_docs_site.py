"""The docs site must publish what the repository actually contains (#1381).

`mkdocs.yml` mounts `docs/` at the site root and, via
`.github/scripts/mkdocs_hooks.py`, the agent-generated `openwiki/` tree at
`/openwiki/`. Three ways that arrangement rots, each with a test here:

1. **A nav entry outgrows its file.** `mkdocs build --strict` catches this in the
   docs-site workflow, but that workflow is path-filtered — a PR that renames a
   doc without touching `mkdocs.yml`'s paths never runs it. This suite runs on
   every PR.
2. **A regenerated wiki adds a page nobody publishes.** `openwiki/**` nav entries
   are listed by hand *on purpose*: auto-generating them would put new
   agent-written pages in front of readers with no person in the loop. The cost
   of that choice is that a new page is silently unpublished unless something
   fails, which is `test_every_openwiki_page_is_in_the_nav`.
3. **The provenance label quietly disappears.** An agent-generated section that
   is not labelled as agent-generated is worse than not publishing it.

Also guarded: the workflow's `paths:` filter (a wiki regeneration that does not
trigger the build never reaches the site) and its `--strict` flag (dropping it
turns a broken-link failure back into a silent warning).

Hermetic: reads committed YAML and markdown off disk. No DB, Redis, RPC,
network, or `.env`. `yaml` is available in the CI unit-test image because
`backend/requirements.txt` pins `uvicorn[standard]`, whose `standard` extra
requires `pyyaml>=5.1`.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
MKDOCS_YML = REPO_ROOT / "mkdocs.yml"
DOCS_INDEX = REPO_ROOT / "docs" / "README.md"
DOCS_SITE_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "docs-site.yml"

#: Human-written index of the agent-generated section, and the nav label above it.
PROVENANCE_DOC = "agent-wiki.md"
WIKI_SECTION = "Agent-generated wiki"


def _load_hooks():
    """Import `.github/scripts/mkdocs_hooks.py` by path — a dot-directory is not a package."""
    path = REPO_ROOT / ".github" / "scripts" / "mkdocs_hooks.py"
    spec = importlib.util.spec_from_file_location("archimedes_mkdocs_hooks", path)
    assert spec and spec.loader, f"cannot load {path}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


hooks = _load_hooks()


def _mkdocs_config() -> dict:
    return yaml.safe_load(MKDOCS_YML.read_text(encoding="utf-8"))


def _nav_targets(node) -> list[str]:
    """Every page path in the nav tree, in document order."""
    if isinstance(node, str):
        return [node]
    if isinstance(node, list):
        return [t for item in node for t in _nav_targets(item)]
    if isinstance(node, dict):
        return [t for value in node.values() for t in _nav_targets(value)]
    return []


def _wiki_section(nav) -> list:
    for item in nav:
        if isinstance(item, dict) and WIKI_SECTION in item:
            return item[WIKI_SECTION]
    raise AssertionError(f"mkdocs.yml nav has no '{WIKI_SECTION}' section — the openwiki tree is unpublished again.")


def _source_path(nav_target: str) -> Path:
    """Repository path a nav target names.

    `docs_dir` is `docs/`, so a bare target is relative to it; the hook mounts
    `openwiki/` from the repository root, so those targets are already
    repo-relative.
    """
    if nav_target.startswith(hooks.WIKI_DIR + "/"):
        return REPO_ROOT / nav_target
    return REPO_ROOT / "docs" / nav_target


# ── nav ↔ tree ───────────────────────────────────────────────────────────────────────


def test_every_nav_target_exists() -> None:
    missing = [t for t in _nav_targets(_mkdocs_config()["nav"]) if not _source_path(t).is_file()]
    assert not missing, (
        "mkdocs.yml nav names files that do not exist — the site build (--strict) will fail:\n  " + "\n  ".join(missing)
    )


def test_every_openwiki_page_is_in_the_nav() -> None:
    navigated = set(_nav_targets(_mkdocs_config()["nav"]))
    on_disk = {p.relative_to(REPO_ROOT).as_posix() for p in hooks.wiki_pages(REPO_ROOT)}
    unpublished = sorted(on_disk - navigated)
    assert not unpublished, (
        "openwiki pages exist but are in no nav section, so the site does not serve them:\n  "
        + "\n  ".join(unpublished)
        + f"\n\nAdd each one under the '{WIKI_SECTION}' section of mkdocs.yml, in the same change "
        "that adds the page. The list is hand-maintained on purpose: an agent-written page "
        "should be admitted to the site by a person."
    )


def test_openwiki_pages_are_the_only_thing_in_that_section() -> None:
    """The section index is the one hand-written page there; everything else is generated."""
    targets = _nav_targets(_wiki_section(_mkdocs_config()["nav"]))
    strays = [t for t in targets[1:] if not t.startswith(hooks.WIKI_DIR + "/")]
    assert targets[0] == PROVENANCE_DOC, (
        f"the first entry under '{WIKI_SECTION}' must be {PROVENANCE_DOC} — it is the section "
        f"index a reader lands on, and the page that carries the provenance note; got {targets[0]!r}"
    )
    assert not strays, (
        f"'{WIKI_SECTION}' is labelled agent-generated, so only openwiki/ pages belong under it; "
        f"hand-written docs listed there would inherit a label that is false for them: {strays}"
    )


# ── provenance ───────────────────────────────────────────────────────────────────────


def test_section_index_states_the_provenance() -> None:
    text = (REPO_ROOT / "docs" / PROVENANCE_DOC).read_text(encoding="utf-8")
    required = {
        "names the generator": "OpenWiki",
        "says an agent wrote it": "written by an AI agent",
        "says it was not line-reviewed": "not reviewed line by line",
        "names the read boundary": ".openwikiignore",
        "says the hand-written docs win": "they win",
    }
    missing = sorted(f"{why} ({needle!r})" for why, needle in required.items() if needle not in text)
    assert not missing, f"docs/{PROVENANCE_DOC} no longer states:\n  " + "\n  ".join(missing)


def test_every_wiki_page_gets_the_provenance_banner() -> None:
    """The banner travels with the page — search engines land readers on leaves."""
    for page in hooks.wiki_pages(REPO_ROOT):
        site_uri = page.relative_to(REPO_ROOT).as_posix()
        out = hooks.add_provenance_banner(page.read_text(encoding="utf-8"), site_uri)
        assert "Agent-generated page" in out, f"{site_uri} did not receive the banner"
        assert f"]({'../' * site_uri.count('/')}{PROVENANCE_DOC})" in out, (
            f"{site_uri}'s banner does not link back to the section index at the right depth"
        )


def test_section_index_is_in_the_docs_index() -> None:
    """docs/README.md opens 'A doc not listed here does not exist.'"""
    assert f"({PROVENANCE_DOC})" in DOCS_INDEX.read_text(encoding="utf-8"), (
        f"docs/{PROVENANCE_DOC} has no row in docs/README.md"
    )


# ── the workflow that publishes it ───────────────────────────────────────────────────


def test_workflow_rebuilds_the_site_when_the_wiki_changes() -> None:
    workflow = yaml.safe_load(DOCS_SITE_WORKFLOW.read_text(encoding="utf-8"))
    # PyYAML parses the unquoted key `on:` as the boolean True (YAML 1.1); GitHub
    # reads it as the string. Accept whichever this PyYAML produced.
    triggers = workflow.get("on", workflow.get(True))
    for event in ("push", "pull_request"):
        paths = triggers[event]["paths"]
        assert f"{hooks.WIKI_DIR}/**" in paths, (
            f"docs-site.yml's {event} paths filter does not include '{hooks.WIKI_DIR}/**' — a "
            "regenerated wiki would merge and never reach the site."
        )
        assert ".github/scripts/mkdocs_hooks.py" in paths, (
            f"docs-site.yml's {event} paths filter does not include the build hook — changing how "
            "the site is assembled would not rebuild it."
        )


def test_workflow_builds_strict() -> None:
    text = DOCS_SITE_WORKFLOW.read_text(encoding="utf-8")
    assert "mkdocs build --strict" in text, (
        "docs-site.yml no longer builds with --strict. Without it a link into docs/ or openwiki/ "
        "that names a missing file is a warning nobody reads instead of a failed build."
    )


# ── the link rewriter ────────────────────────────────────────────────────────────────

# A stand-in for the mkdocs file set: what the site actually serves.
KNOWN = {"README.md", "architecture.md", "agent-wiki.md", "adr/README.md", "openwiki/quickstart.md"}


def test_out_of_docs_link_becomes_a_github_url() -> None:
    """`../CLAUDE.md` is correct on GitHub and 404s on the site. Repoint it, keep the anchor."""
    assert (
        hooks.rewrite_target("../backend/archimedes/main.py#L203", "docs/architecture.md", "architecture.md", KNOWN)
        == "https://github.com/a-apin/archimedes/blob/main/backend/archimedes/main.py#L203"
    )


def test_in_site_link_that_already_resolves_is_untouched() -> None:
    assert hooks.rewrite_target("adr/README.md", "docs/architecture.md", "architecture.md", KNOWN) is None


def test_wiki_page_link_into_docs_is_repointed_inside_the_site() -> None:
    """openwiki/ sits beside docs/ in the repo but under it on the site."""
    assert (
        hooks.rewrite_target("../docs/architecture.md", "openwiki/INSTRUCTIONS.md", "openwiki/INSTRUCTIONS.md", KNOWN)
        == "../architecture.md"
    )


def test_broken_in_site_link_is_left_for_strict_to_catch() -> None:
    """The guard: a link naming a file that exists nowhere must NOT be laundered.

    Rewriting it to a GitHub URL would turn a broken link into a link that looks
    fine and 404s, and would silence the `--strict` build failure that is the
    only thing checking it.
    """
    for target, repo_uri, site_uri in (
        ("no-such-doc.md", "docs/architecture.md", "architecture.md"),
        ("../openwiki/no-such-page.md", "docs/agent-wiki.md", "agent-wiki.md"),
    ):
        assert hooks.rewrite_target(target, repo_uri, site_uri, KNOWN) is None, (
            f"{target!r} names no file in the repository; leaving it alone is what makes "
            "`mkdocs build --strict` fail on it."
        )


def test_unpublished_repo_file_under_the_wiki_becomes_a_github_url() -> None:
    """`openwiki/.claims/` and `.last-update.json` are real files the site does not serve."""
    assert (
        hooks.rewrite_target("../openwiki/.last-update.json", "docs/agent-wiki.md", "agent-wiki.md", KNOWN)
        == "https://github.com/a-apin/archimedes/blob/main/openwiki/.last-update.json"
    )


def test_links_inside_code_blocks_are_not_rewritten() -> None:
    markdown = "See [main](../backend/archimedes/main.py).\n\n```\n[main](../backend/archimedes/main.py)\n```\n"
    out = hooks.rewrite_links(markdown, "docs/architecture.md", "architecture.md", KNOWN)
    assert out.count("https://github.com/") == 1, "a path shown inside a fenced block is an illustration, not a link"


def test_wiki_pages_excludes_the_claim_sidecars() -> None:
    pages = {p.relative_to(REPO_ROOT).as_posix() for p in hooks.wiki_pages(REPO_ROOT)}
    assert pages, "openwiki/ has no pages — did the tree move?"
    assert not [p for p in pages if "/." in p or p.startswith(".")], (
        f"wiki_pages() picked up dot-prefixed bookkeeping files: {sorted(pages)}"
    )
