#!/usr/bin/env python3
"""
Complexity gate: aggregate metrics table comparing main vs PR branch.
Rows = metrics, columns = main | PR. Never blocks merge.
"""

import argparse
import ast
import re
import subprocess
from pathlib import Path
from statistics import mean

import lizard


def _max_nesting(source: str) -> float:
    """Average max nesting depth across all functions via AST."""
    depths = []
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return 0.0
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            depths.append(_node_depth(node))
    return round(mean(depths), 1) if depths else 0.0


def _node_depth(
    root: ast.AST, _nesting_nodes=(ast.For, ast.While, ast.If, ast.With, ast.Try, ast.ExceptHandler)
) -> int:
    max_d = [0]

    def walk(node, d):
        if isinstance(node, _nesting_nodes):
            d += 1
            max_d[0] = max(max_d[0], d)
        for child in ast.iter_child_nodes(node):
            walk(child, d)

    walk(root, 0)
    return max_d[0]


def _is_recursive(source: str, name: str) -> bool:
    try:
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
                for child in ast.walk(node):
                    if isinstance(child, ast.Call):
                        fn = child.func
                        if (isinstance(fn, ast.Name) and fn.id == name) or (
                            isinstance(fn, ast.Attribute) and fn.attr == name
                        ):
                            return True
    except SyntaxError:
        pass
    return False


def _is_orphan(filepath: str, name: str) -> bool:
    r = subprocess.run(
        ["grep", "-r", "--include=*.py", "-l", rf"\b{re.escape(name)}\b", "backend/"],
        capture_output=True,
        text=True,
    )
    return not any(f for f in r.stdout.strip().splitlines() if f != filepath)


def _collect(files: list[str], root: str = "") -> dict:
    """Collect aggregate metrics across all functions in the given files."""
    cc_values, nesting_values = [], []
    recursive_count, orphan_count = 0, 0

    for filepath in files:
        p = Path(root) / filepath if root else Path(filepath)
        if not p.exists():
            continue
        info = lizard.analyze_file(str(p))
        if not info:
            continue
        is_py = p.suffix == ".py"
        source = p.read_text(errors="replace") if is_py else ""
        for fn in info.function_list:
            cc_values.append(fn.cyclomatic_complexity)
            if is_py and _is_recursive(source, fn.name):
                recursive_count += 1
            if is_py and _is_orphan(str(p), fn.name):
                orphan_count += 1
        if is_py and source:
            nesting_values.append(_max_nesting(source))

    return {
        "cc": round(mean(cc_values), 1) if cc_values else 0,
        "nesting": round(mean(nesting_values), 1) if nesting_values else 0,
        "recursive": recursive_count,
        "orphan": orphan_count,
        "fns": len(cc_values),
    }


def _fmt_cc(v: float) -> str:
    if v <= 5:
        return f"{v} ✅"
    if v <= 10:
        return f"{v} ⚠️"
    if v <= 15:
        return f"{v} 🟠"
    return f"{v} 🔴"


def _fmt_delta(main_v: float, pr_v: float) -> str:
    d = round(pr_v - main_v, 1)
    if d > 1.0:
        return f"+{d} ⚠️"
    if d > 0:
        return f"+{d}"
    if d < 0:
        return f"{d} ✅"
    return "—"


def analyze(changed_files: list[str], baseline_dir: str) -> str:
    targets = [f for f in changed_files if Path(f).suffix in {".py", ".js", ".ts", ".jsx", ".tsx"}]
    if not targets:
        return "## 🔬 Complexity\n\nNo Python or JS/TS files changed.\n"

    pr = _collect(targets)
    main = _collect(targets, root=baseline_dir)

    rows = [
        ("CC (avg)", _fmt_cc(main["cc"]), _fmt_cc(pr["cc"])),
        ("Δ CC", "—", _fmt_delta(main["cc"], pr["cc"])),
        ("Nesting (avg)", str(main["nesting"]), f"{pr['nesting']}{'⚠️' if pr['nesting'] >= 3 else ''}"),
        ("Recursive fns", str(main["recursive"]), str(pr["recursive"])),
        ("Orphan fns", str(main["orphan"]), f"{pr['orphan']}{'⚠️' if pr['orphan'] > main['orphan'] else ''}"),
    ]

    lines = [
        "## 🔬 Complexity\n",
        f"*{pr['fns']} function{'s' if pr['fns'] != 1 else ''} across {len(targets)} changed file{'s' if len(targets) != 1 else ''}*\n",
        "| Metric | main | PR |",
        "|---|---|---|",
    ]
    lines += [f"| {m} | {b} | {p} |" for m, b, p in rows]
    lines.append("\n*CC: ✅ avg 1–5 · ⚠️ 6–10 · 🟠 11–15 · 🔴 16+*\n")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--changed-files", nargs="+", required=True)
    parser.add_argument("--baseline-dir", required=True)
    parser.add_argument("--output-md", required=True)
    args = parser.parse_args()

    try:
        md = analyze(args.changed_files, args.baseline_dir)
    except Exception as exc:
        import traceback

        md = (
            "## 🔬 Complexity\n\n"
            f"> ⚠️ `{exc}`\n\n"
            f"<details><summary>Traceback</summary>\n\n```\n{traceback.format_exc()}\n```\n\n</details>\n"
        )

    Path(args.output_md).write_text(md)


if __name__ == "__main__":
    main()
