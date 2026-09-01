"""No git conflict marker may be tracked, anywhere.

A ``>>>>>>> origin/main`` line sat inside ``strategy_fusion.py``'s ``_SPEC_CONTRACT``
from ``bdb93547`` until #1744 removed it — i.e. inside BOTH proposer prompts, shipped
to the model on every generation call — and nothing in ruff-gate, pre-commit, or
quality-gate noticed, because a marker inside a string literal is valid Python.
This test is the guard that was missing: it scans every tracked text file.

Only the two unambiguous marker shapes are policed — a line beginning with
``<<<<<<< `` or ``>>>>>>> `` (seven characters then a space; git always writes the
side label after the space). The middle ``=======`` line is deliberately NOT
policed: a bare ``=======`` is a legitimate Markdown/RST setext heading underline
and appears in this repo's docs on purpose.

Hermetic: reads the working tree of the files ``git ls-files`` reports, no network,
no DB. MUTATION (run before pushing, per the adversarial-pass rule): append
``>>>>>>> origin/main`` to any tracked ``.py`` or ``.md`` — this test must go red
naming that file and line; remove it and it is green again.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

_MARKER = re.compile(rb"^(<<<<<<< |>>>>>>> )")

#: Tracked paths that are allowed to contain marker-shaped lines because they
#: are ABOUT conflict markers (fixtures/tests). Keep this list short and named.
_ALLOWED = {
    "backend/tests/test_no_conflict_markers.py",
}


def _tracked_files() -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
    ).stdout
    return [REPO_ROOT / p.decode() for p in out.split(b"\0") if p]


def _looks_binary(head: bytes) -> bool:
    return b"\0" in head


def _marker_hits(path: Path) -> list[str]:
    try:
        data = path.read_bytes()
    except (FileNotFoundError, IsADirectoryError):  # gitlinks (submodules) and deleted-but-tracked
        return []
    if _looks_binary(data[:8192]):
        return []
    shown = path.relative_to(REPO_ROOT) if path.is_relative_to(REPO_ROOT) else path.name
    hits: list[str] = []
    for lineno, line in enumerate(data.splitlines(), start=1):
        if _MARKER.match(line):
            hits.append(f"{shown}:{lineno}: {line[:60].decode('utf-8', 'replace')}")
    return hits


def test_no_tracked_file_contains_a_conflict_marker() -> None:
    files = _tracked_files()
    assert len(files) > 100, "git ls-files returned suspiciously few files — is this the repo root?"
    hits = [h for p in files if str(p.relative_to(REPO_ROOT)) not in _ALLOWED for h in _marker_hits(p)]
    assert not hits, (
        "git conflict marker(s) are tracked — a merge was committed unresolved "
        "(the bdb93547 class: this once shipped inside a live LLM prompt):\n  " + "\n  ".join(hits)
    )


def test_the_scan_is_not_vacuous(tmp_path: Path) -> None:
    """The regex catches both marker shapes and ignores the setext underline."""
    sample = tmp_path / "s.md"
    sample.write_bytes(b"Title\n=======\n<<<<<<< HEAD\nours\n=======\ntheirs\n>>>>>>> origin/main\n")
    hits = _marker_hits(sample)
    assert [h.split(": ", 1)[1] for h in hits] == ["<<<<<<< HEAD", ">>>>>>> origin/main"]
