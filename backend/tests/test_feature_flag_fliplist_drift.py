"""Every feature flag in the tree must have a row in the flip-list (#834).

``docs/operations/feature-flag-fliplist.md`` is the go-live checklist: the one
page that says, per flag, what it gates, what it is set to in the deployed
config, and what has to be true before someone flips it. A flip-list is only
worth reading if it is complete — an undocumented money switch is exactly the
failure mode #834 was opened to prevent, and the issue has already drifted
twice (2026-07-05 found four live flags missing from it; 2026-08-20 found a
fifth, ``AGENT_DRY_RUN``, the live-signal switch).

Hand-maintenance is what let that happen, so this guard mechanises the grep.

**The contract is one-directional**: every flag this module *discovers* must
appear in the doc. The doc may name more (retired flags, mode selectors,
companion tunables) — extra rows are history and context, and forcing them out
would make the page worse. What CI now blocks is the direction that hurts: a
new flag landing in code with no row on the checklist.

Discovery rules, all mechanical, all reproducible with ripgrep:

1. **Python env reads** (``os.getenv("X")`` / ``os.environ.get("X")`` /
   ``os.environ["X"]`` / ``environ.get("X")``) under the runtime source roots,
   kept when either the *name* is flag-shaped (``*_ENABLED``, ``*_DRY_RUN``,
   ``*_REQUIRED``, ``ENABLE_*``, ``*_HALT``, ``*_ENFORCED``, ``*_FIXTURE``,
   ``*_ALLOW_*``, ``FEATURE_*``) or the *read site* parses a boolean literal
   (``"true"``/``"false"``/``"yes"``/``"no"``/``"on"``/``"off"``, ``_TRUTHY``,
   ``_FALSY``, ``_env_bool``, ``strtobool``).
2. **Python env-name constants** — ``_PUBLISH_ENV = "PAPER_TRACE_PUBLISH"``
   style — kept when the constant is passed to a boolean parser in the same
   file. This is how ``paper_trace.py`` spells its two switches, and rule 1
   cannot see them.
3. **UI flags**: every ``import.meta.env`` name in ``ui/src/featureFlags.js``,
   which is the frontend's whole flag surface by construction, plus
   flag-shaped names elsewhere in ``ui/src`` and ``auth/``.
4. **Deployed config**: flag-shaped env names in ``infra/**.tf``,
   ``docker-compose*.yml``, ``.env.example``, and ``ui/.env.example`` — this is
   what catches a flag that is *injected* but has no reader (a dead flag) as
   well as one that has a reader but no row.
5. **Repo-variable flags**: ``vars.X`` in ``.github/workflows/*.yml``
   (``DEPLOY_ENABLED``, ``RUNNER_DEPLOY_ENABLED``, ``DOCS_SITE_ENABLED`` gate
   whole deploy jobs — they belong on a go-live checklist).
6. **Shell escape hatches**: ``${X:-}`` in ``infra/scripts`` / ``scripts``,
   flag-shaped only.

Hermetic: reads committed files off disk and matches regexes in memory. No
``.env``, no network, no DB, no Redis, no app import.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
FLIPLIST = REPO_ROOT / "docs" / "operations" / "feature-flag-fliplist.md"

# An underscore-delimited segment from any of these makes a name flag-shaped.
# "DRY_RUN" is checked as a substring because it spans two segments.
FLAG_TOKENS = frozenset(
    {
        "ENABLE",
        "ENABLED",
        "DISABLE",
        "DISABLED",
        "REQUIRE",
        "REQUIRED",
        "ENFORCED",
        "HALT",
        "FIXTURE",
        "ALLOW",
        "FEATURE",
    }
)

# Boolean parsing at a read site. Bare "1"/"0" are deliberately excluded: they
# are the default for numeric knobs (GENERATION_MAX_CONCURRENT, timeouts) and
# would drag half the config surface in as "flags".
_BOOL_PARSE_RE = re.compile(r'"(?:true|false|yes|no|on|off)"|_TRUTHY|_FALSY|_env_bool|strtobool')

_NAME = r"([A-Z][A-Z0-9]*(?:_[A-Z0-9]+)*)"
_PY_READ_RE = re.compile(
    r'os\.getenv\(\s*"' + _NAME + r'"'
    r'|os\.environ\.get\(\s*"' + _NAME + r'"'
    r'|os\.environ\[\s*"' + _NAME + r'"'
    r'|(?<![.\w])environ\.get\(\s*"' + _NAME + r'"'
)
_PY_ENV_CONST_RE = re.compile(r'^(_?[A-Z][A-Z0-9_]*)\s*(?::\s*[^=]+?)?\s*=\s*"' + _NAME + r'"\s*$', re.M)
_JS_READ_RE = re.compile(r"(?:process\.env|import\.meta\.env\??)\." + _NAME + r"|(?<![.\w])env\." + _NAME + r"\b")
_TF_ENV_RE = re.compile(r'name\s*=\s*"' + _NAME + r'"')
_DOTENV_RE = re.compile(r"^\s*#?\s*" + _NAME + r"=", re.M)
_COMPOSE_RE = re.compile(r"^\s+" + _NAME + r":\s*\$\{", re.M)
_GHA_VAR_RE = re.compile(r"vars\." + _NAME)
_SH_READ_RE = re.compile(r"\$\{" + _NAME + r"(?::-|\}|:\?)")

_PY_ROOTS = ("backend/archimedes", "scripts", "cli", "analytics-engine/src")
_JS_ROOTS = ("ui/src", "auth")
_SH_ROOTS = ("infra/scripts", "scripts")
_DOTENV_FILES = (".env.example", "ui/.env.example")
_UI_FLAG_MODULE = "ui/src/featureFlags.js"


def _is_flag_shaped(name: str) -> bool:
    return "DRY_RUN" in name or bool(FLAG_TOKENS & set(name.split("_")))


def _names(pattern: re.Pattern[str], text: str):
    """Yield (name, offset) for every capturing group that matched."""
    for match in pattern.finditer(text):
        for group in match.groups():
            if group:
                yield group, match.start()


def _is_test_path(rel: str) -> bool:
    base = rel.rsplit("/", 1)[-1]
    return "/tests/" in f"/{rel}" or "/test/" in f"/{rel}" or base.startswith("test_") or ".test." in base


def discover_flags(root: Path) -> dict[str, set[str]]:
    """Map every discovered flag name to the repo-relative files that mention it.

    ``root`` is a parameter (not a module constant) so the adversarial test can
    point it at a synthetic tree and prove the guard actually rejects.
    """
    found: dict[str, set[str]] = {}

    def add(name: str, rel: str) -> None:
        found.setdefault(name, set()).add(rel)

    def walk(pattern: str, *dirs: str):
        for directory in dirs:
            base = root / directory
            if not base.is_dir():
                continue
            for path in sorted(base.rglob(pattern)):
                rel = path.relative_to(root).as_posix()
                if _is_test_path(rel) or "node_modules" in rel:
                    continue
                yield path, rel

    for path, rel in walk("*.py", *_PY_ROOTS):
        text = path.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines()
        for name, offset in _names(_PY_READ_RE, text):
            line = lines[text.count("\n", 0, offset)] if lines else ""
            if _is_flag_shaped(name) or _BOOL_PARSE_RE.search(line):
                add(name, rel)
        for match in _PY_ENV_CONST_RE.finditer(text):
            ident, name = match.group(1), match.group(2)
            bool_call = re.compile(
                r"_(?:env_)?bool\(\s*" + re.escape(ident) + r"\b|_truthy\(\s*" + re.escape(ident) + r"\b"
            )
            if bool_call.search(text):
                add(name, rel)

    for pattern in ("*.js", "*.jsx"):
        for path, rel in walk(pattern, *_JS_ROOTS):
            text = path.read_text(encoding="utf-8", errors="replace")
            whole_file_is_flags = rel == _UI_FLAG_MODULE
            for name, _ in _names(_JS_READ_RE, text):
                if whole_file_is_flags or _is_flag_shaped(name):
                    add(name, rel)

    for path, rel in walk("*.tf", "infra"):
        for name, _ in _names(_TF_ENV_RE, path.read_text(encoding="utf-8", errors="replace")):
            if _is_flag_shaped(name):
                add(name, rel)

    for rel in _DOTENV_FILES:
        path = root / rel
        if path.is_file():
            for name, _ in _names(_DOTENV_RE, path.read_text(encoding="utf-8", errors="replace")):
                if _is_flag_shaped(name):
                    add(name, rel)

    for path in sorted(root.glob("docker-compose*.yml")):
        for name, _ in _names(_COMPOSE_RE, path.read_text(encoding="utf-8", errors="replace")):
            if _is_flag_shaped(name):
                add(name, path.name)

    for path, rel in walk("*.yml", ".github/workflows"):
        for name, _ in _names(_GHA_VAR_RE, path.read_text(encoding="utf-8", errors="replace")):
            if _is_flag_shaped(name):
                add(name, rel)

    for path, rel in walk("*.sh", *_SH_ROOTS):
        for name, _ in _names(_SH_READ_RE, path.read_text(encoding="utf-8", errors="replace")):
            if _is_flag_shaped(name):
                add(name, rel)

    return found


def undocumented_flags(root: Path, doc: Path) -> dict[str, set[str]]:
    """Discovered flags with no mention in ``doc``. Empty dict = the doc is complete."""
    text = doc.read_text(encoding="utf-8")
    documented = set(re.findall(r"[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+", text))
    return {name: files for name, files in discover_flags(root).items() if name not in documented}


# ── The guard ────────────────────────────────────────────────────────────────


def test_fliplist_exists() -> None:
    assert FLIPLIST.is_file(), (
        f"{FLIPLIST.relative_to(REPO_ROOT)} is missing. It is the go-live checklist "
        "referenced by issue #834 and indexed in docs/README.md — restore it rather "
        "than deleting this guard."
    )


def test_discovery_is_not_vacuous() -> None:
    """A scanner that finds nothing would make the drift guard pass forever.

    These five span all six discovery rules (boolean read, name constant, UI
    module, terraform env, repo variable). If a regex regresses, this fails
    before the completeness assertion can pass vacuously.
    """
    discovered = discover_flags(REPO_ROOT)
    for anchor in (
        "PAYMENTS_DRY_RUN",  # rule 1 — boolean read site
        "PAPER_TRACE_PUBLISH",  # rule 2 — env-name constant
        "VITE_ROADMAP_SURFACES",  # rule 3 — ui/src/featureFlags.js
        "EMAIL_VERIFICATION_ENFORCED",  # rule 4 — infra/ecs.tf + auth/auth.js
        "DEPLOY_ENABLED",  # rule 5 — GitHub Actions repo variable
    ):
        assert anchor in discovered, f"flag discovery regressed: {anchor} is no longer found"
    assert len(discovered) >= 20, f"flag discovery collapsed to {len(discovered)} names — a regex regressed"


def test_every_discovered_flag_is_on_the_fliplist() -> None:
    missing = undocumented_flags(REPO_ROOT, FLIPLIST)
    assert not missing, (
        "Feature flags exist in the tree with no row on the go-live checklist "
        f"({FLIPLIST.relative_to(REPO_ROOT)}). Add a row classifying each as LIVE, "
        "FLIP-AT-LAUNCH, or DEAD — issue #834.\n"
        + "\n".join(f"  {name}: {sorted(files)}" for name, files in sorted(missing.items()))
    )


# ── Adversarial: the guard must be shown to reject ───────────────────────────


@pytest.fixture()
def synthetic_tree(tmp_path: Path) -> Path:
    """A minimal tree the discovery rules can walk, with a complete doc."""
    src = tmp_path / "backend" / "archimedes" / "services"
    src.mkdir(parents=True)
    (src / "widget.py").write_text(
        "import os\n\n\ndef widget_enabled() -> bool:\n"
        '    return os.getenv("WIDGET_ENABLED", "false").strip().lower() == "true"\n',
        encoding="utf-8",
    )
    doc = tmp_path / "fliplist.md"
    doc.write_text("| `WIDGET_ENABLED` | LIVE | gates the widget |\n", encoding="utf-8")
    return tmp_path


def test_guard_passes_on_a_complete_synthetic_doc(synthetic_tree: Path) -> None:
    assert discover_flags(synthetic_tree).keys() == {"WIDGET_ENABLED"}
    assert undocumented_flags(synthetic_tree, synthetic_tree / "fliplist.md") == {}


def test_guard_rejects_a_flag_added_without_a_doc_row(synthetic_tree: Path) -> None:
    """Adding a flag to the code and not the doc must fail — the drift case."""
    (synthetic_tree / "backend" / "archimedes" / "services" / "sneaky.py").write_text(
        "import os\n\n\ndef sneaky() -> bool:\n"
        '    return os.getenv("SNEAKY_MONEY_DRY_RUN", "false").lower() == "true"\n',
        encoding="utf-8",
    )
    missing = undocumented_flags(synthetic_tree, synthetic_tree / "fliplist.md")
    assert "SNEAKY_MONEY_DRY_RUN" in missing
    assert missing["SNEAKY_MONEY_DRY_RUN"] == {"backend/archimedes/services/sneaky.py"}


def test_guard_rejects_a_row_deleted_from_the_doc(synthetic_tree: Path) -> None:
    """The other drift direction: the flag stays, the row is removed."""
    doc = synthetic_tree / "fliplist.md"
    doc.write_text("(no flags documented)\n", encoding="utf-8")
    assert "WIDGET_ENABLED" in undocumented_flags(synthetic_tree, doc)
