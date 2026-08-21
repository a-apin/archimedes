"""``docs/api-surface-status.md`` must list exactly the routers `main.py` mounts.

Cluster-7's api-surface-status sprint item ("leave every backend router registered,
unchanged") asked for "a test asserting every router registered in main.py appears in
[the doc] -- documentation with teeth rather than prose that drifts." This is that test,
checked in BOTH directions:

  (a) every router `app.include_router(...)` in `main.py` mounts must have a row in the
      doc's census table -- a new router shipped without a doc row fails the suite.
  (b) every router the doc's table names must still be registered in `main.py` -- a
      router removed from `main.py` without updating the doc also fails the suite, so
      the doc can't silently go stale in the other direction either.

Hermetic by construction: both sides are read as plain text and parsed (`ast` for
`main.py`, a regex for the doc's table cells). Neither `archimedes.main` nor any other
`archimedes.*` module is ever imported, so nothing here can trip `init_db()`, SSM secret
loading, the rate limiter's env checks, or any other `main.py` import-time side effect --
this file only ever reads two files off disk. No DB / Redis / RPC / network.

Mutation-tested per this repo's guard discipline (CLAUDE.md "a guard must be shown to
reject something"): a fake router temporarily appended to `main.py`'s include_router
block, and separately a row temporarily deleted from the doc, each independently made
`test_doc_and_main_agree_on_router_set` fail with the expected missing/stale name before
this file was pushed; both edits were reverted afterward. See the PR description for the
exact commands run.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest


def _repo_root() -> Path:
    import archimedes  # backend/archimedes/__init__.py -> parents: archimedes, backend, <repo>

    return Path(archimedes.__file__).resolve().parents[2]


def _main_py_source() -> str:
    return (_repo_root() / "backend" / "archimedes" / "main.py").read_text(encoding="utf-8")


def _doc_text() -> str:
    return (_repo_root() / "docs" / "api-surface-status.md").read_text(encoding="utf-8")


def registered_router_names(source: str) -> set[str]:
    """Every router variable name passed as the first positional arg to
    ``app.include_router(...)`` anywhere in ``source``.

    Parsed via ``ast`` against the source TEXT -- deliberately never by importing
    ``archimedes.main``, which does real work at import time (DB init, SSM secret
    loading, rate-limiter setup) that this doc-completeness check has no business
    triggering. Conditionally-registered routers (``marketplace_router`` behind a
    fail-soft import try/except, ``auth_router`` behind ``if os.getenv("TESTING")``)
    still count as "registered" for this purpose -- the ``include_router`` call is
    present in the source either way, which is exactly the signal the doc's census
    tracks (see the "gated" status column).
    """
    tree = ast.parse(source)
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr == "include_router"):
            continue
        if not node.args:
            continue
        first_arg = node.args[0]
        if isinstance(first_arg, ast.Name):
            names.add(first_arg.id)
    return names


# The doc's census table renders every router as a fully-qualified, backtick-code cell
# in the shape `archimedes.api.<module>.<router_name>`, e.g.
# `archimedes.api.assets_routes.assets_router`. Anchoring on the `archimedes.api.`
# prefix plus a `_router` suffix means this only ever matches a census-table row --
# never one of the bare module mentions (`archimedes.api.auth_siwe`,
# `archimedes.api.papers_routes`) that appear in the prose notes below the table.
_DOC_ROUTER_CELL_RE = re.compile(r"archimedes\.api\.[a-zA-Z0-9_.]+\.([a-zA-Z_][a-zA-Z0-9_]*_router)\b")


def documented_router_names(text: str) -> set[str]:
    return set(_DOC_ROUTER_CELL_RE.findall(text))


@pytest.fixture(scope="module")
def live_names() -> set[str]:
    return registered_router_names(_main_py_source())


@pytest.fixture(scope="module")
def doc_names() -> set[str]:
    return documented_router_names(_doc_text())


def test_parser_sanity_floor(live_names: set[str], doc_names: set[str]) -> None:
    """Guard the guard: both parses must find a realistic number of routers.

    If either regex/AST extraction regressed to matching nothing, the bidirectional
    equality check below would compare two empty sets and pass vacuously -- silently
    disabling the whole completeness gate. 25 is comfortably below the 30 routers on
    record as of this test's authorship (2026-08-20), so it tolerates future router
    removals without being a brittle exact-count assertion, while still catching a
    parser that stopped matching anything.
    """
    assert len(live_names) >= 25, (
        f"only found {len(live_names)} app.include_router(...) call(s) in main.py -- "
        "the AST walk may be broken (a silent break here would make the completeness "
        "check below vacuously pass)."
    )
    assert len(doc_names) >= 25, (
        f"only found {len(doc_names)} documented router cell(s) in "
        "docs/api-surface-status.md -- the doc-table regex may be broken."
    )


def test_doc_and_main_agree_on_router_set(live_names: set[str], doc_names: set[str]) -> None:
    """Every router main.py registers has a doc row, and every doc row is still live.

    This is the mechanical enforcement of cluster-7's requirement: adding a router to
    main.py without documenting it here must fail this suite.
    """
    missing_from_doc = live_names - doc_names
    stale_in_doc = doc_names - live_names

    assert not missing_from_doc, (
        "router(s) registered in backend/archimedes/main.py but missing a row in "
        f"docs/api-surface-status.md: {sorted(missing_from_doc)} -- add a row (prefix, "
        "router module, auth model, status; see docs/CONVENTIONS.md) in the same "
        "commit that registers the router."
    )
    assert not stale_in_doc, (
        "docs/api-surface-status.md documents router(s) that are no longer registered "
        f"in backend/archimedes/main.py: {sorted(stale_in_doc)} -- update or remove the "
        "row in the same commit that unregisters the router."
    )
