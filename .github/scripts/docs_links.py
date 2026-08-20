#!/usr/bin/env python3
"""Relative-link validator for markdown docs.

Blocking check for .github/workflows/docs-gate.yml. Self-contained: no
third-party imports, no network. Runs identically in CI and locally via
`make docs-check`.

What counts as a failure: a markdown link (or image) whose target is a
*relative* path that does not resolve to a file or directory on disk.
Anchors are checked for existence of the file only, not the heading.

What is deliberately NOT a failure:
  - external links (http, https, mailto, tel, ftp, data)
  - pure in-page anchors (#section)
  - absolute paths (/foo) — those are site-root URLs, not repo paths
  - anything under submodules/ or contracts/lib/. Those are gitlinks. In a
    plain `actions/checkout` without `submodules: recursive` the directories
    are empty, so every link into them "breaks" and the gate goes
    permanently red for a reason that has nothing to do with the PR. Cloning
    them on every docs PR costs minutes for no signal. See SKIP_PREFIXES.

Usage:
    python .github/scripts/docs_links.py [--root .] [--format text|github]
Exit status: 0 = all links resolve, 1 = at least one broken link.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

# Link targets that start with one of these resolve only in an initialised
# checkout (git submodules / forge libs). Treat them as out of scope rather
# than as broken. Keep this list short and justified — every entry is a hole
# in the gate.
SKIP_PREFIXES = ("submodules/", "contracts/lib/")

# Directories we never walk looking for markdown to check.
SKIP_DIRS = {
    ".git",
    "node_modules",
    "submodules",
    "venv",
    ".venv",
    "__pycache__",
    "site-packages",
    "dist",
    "build",
    ".next",
}

# Inline links [text](target) and images ![alt](target). The target group
# stops at whitespace so `[x](path "title")` yields `path`.
INLINE_LINK = re.compile(r"!?\[[^\]]*\]\(\s*<?([^)\s>]+)>?(?:\s+[\"'][^\"']*[\"'])?\s*\)")
# Badge-style nested links: [![alt](img-url)](target). INLINE_LINK's
# non-nesting `[^\]]*` alt group stops at the first `]`, so it consumes the
# inner image link and never sees the OUTER (target) — a badge pointing at a
# deleted file sailed through as "0 broken" (#1262 review). This second pass
# captures that outer target.
BADGE_LINK = re.compile(r"\[!\[[^\]]*\]\([^)]*\)\]\(\s*<?([^)\s>]+)>?(?:\s+[\"'][^\"']*[\"'])?\s*\)")
# Reference definitions: [label]: target. `[^n]:` is a *footnote* definition
# whose body is prose, not a target — excluding `^` keeps citation-heavy docs
# (citation-heavy archived analyses carry 40+ footnotes) from reporting the first
# word of each footnote as a broken link.
REF_DEF = re.compile(r"^\s{0,3}\[(?!\^)[^\]]+\]:\s*<?([^\s>]+)>?", re.MULTILINE)

# Fenced code blocks and inline code — links inside them are illustrative,
# not navigational, and are frequently intentional non-paths.
FENCE = re.compile(r"^([ \t]*)(`{3,}|~{3,})[^\n]*\n.*?^\1\2[ \t]*$", re.MULTILINE | re.DOTALL)
INLINE_CODE = re.compile(r"`[^`\n]*`")


def strip_code(text: str) -> str:
    """Blank out code spans/blocks, preserving newline count for line numbers."""

    def blank(m: re.Match) -> str:
        return re.sub(r"[^\n]", " ", m.group(0))

    return INLINE_CODE.sub(blank, FENCE.sub(blank, text))


def is_external(target: str) -> bool:
    lowered = target.lower()
    if lowered.startswith(("http://", "https://", "mailto:", "tel:", "ftp://", "data:")):
        return True
    # Any scheme-like prefix (foo://) is external.
    return bool(re.match(r"^[a-z][a-z0-9+.-]*://", lowered))


def markdown_files(root: Path) -> list[Path]:
    """Markdown files this gate governs: docs/**/*.md plus root-level *.md.

    Scope deliberately matches the workflow's `paths:` filter. Widening it to
    the whole tree pulls in README files under backend/ and frontend/ that
    already carry broken relative links predating this gate; a gate that is
    red on the commit that introduces it teaches people to ignore it. Those
    files are a separate cleanup, and `--scope all` reports on them.
    """
    out: list[Path] = []
    for name in sorted(os.listdir(root)):
        if name.endswith(".md") and (root / name).is_file():
            out.append(root / name)
    docs = root / "docs"
    if docs.is_dir():
        for dirpath, dirnames, filenames in os.walk(docs):
            dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
            for name in sorted(filenames):
                if name.endswith(".md"):
                    out.append(Path(dirpath) / name)
    return out


def all_markdown_files(root: Path) -> list[Path]:
    """Whole-tree walk, used by `--scope all` for the wider cleanup view."""
    out: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".git"))
        if Path(dirpath).relative_to(root).as_posix().startswith("contracts/lib"):
            dirnames[:] = []
            continue
        for name in sorted(filenames):
            if name.endswith(".md"):
                out.append(Path(dirpath) / name)
    return out


def line_of(text: str, index: int) -> int:
    return text.count("\n", 0, index) + 1


def check_file(path: Path, root: Path) -> list[tuple[int, str]]:
    """Return [(line, target)] for every unresolvable relative link."""
    raw = path.read_text(encoding="utf-8", errors="replace")
    text = strip_code(raw)
    broken: list[tuple[int, str]] = []

    targets: list[tuple[int, str]] = []
    for m in INLINE_LINK.finditer(text):
        targets.append((m.start(1), m.group(1)))
    for m in BADGE_LINK.finditer(text):
        targets.append((m.start(1), m.group(1)))
    for m in REF_DEF.finditer(text):
        targets.append((m.start(1), m.group(1)))

    for pos, target in targets:
        target = target.strip()
        if not target or target.startswith("#") or is_external(target):
            continue
        # Absolute paths are site-root URLs; the repo has no site root to
        # resolve them against, so we do not judge them.
        if target.startswith("/"):
            continue
        if target.startswith("{{") or "$" in target or "<" in target:
            continue  # template placeholder, not a path
        path_part = target.split("#", 1)[0].split("?", 1)[0]
        if not path_part:
            continue
        try:
            resolved = (path.parent / path_part).resolve()
            rel = resolved.relative_to(root.resolve())
        except (ValueError, OSError):
            # Escapes the repo root — out of scope, same reasoning as absolute.
            continue
        rel_posix = rel.as_posix()
        if rel_posix.startswith(SKIP_PREFIXES):
            continue
        if not resolved.exists():
            broken.append((line_of(text, pos), target))
    return broken


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".", help="repository root (default: cwd)")
    ap.add_argument("--format", choices=("text", "github"), default="text")
    ap.add_argument(
        "--scope",
        choices=("gate", "all"),
        default="gate",
        help="gate = docs/**/*.md + root *.md (what this workflow blocks on); all = whole tree",
    )
    args = ap.parse_args()

    root = Path(args.root).resolve()
    files = markdown_files(root) if args.scope == "gate" else all_markdown_files(root)
    total_links = 0
    all_broken: list[tuple[Path, int, str]] = []

    for f in files:
        raw = strip_code(f.read_text(encoding="utf-8", errors="replace"))
        total_links += len(INLINE_LINK.findall(raw)) + len(BADGE_LINK.findall(raw)) + len(REF_DEF.findall(raw))
        for line, target in check_file(f, root):
            all_broken.append((f, line, target))

    for f, line, target in all_broken:
        rel = f.relative_to(root).as_posix()
        if args.format == "github":
            print(f"::error file={rel},line={line}::broken relative link: {target}")
        else:
            print(f"{rel}:{line}: broken relative link: {target}")

    print(
        f"\nlinks: scanned {total_links} in {len(files)} markdown files; "
        f"broken {len(all_broken)} "
        f"({(len(all_broken) / total_links * 100) if total_links else 0:.1f}%)"
    )
    if all_broken:
        print("Skipped by design: submodules/, contracts/lib/, external URLs, absolute paths.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
