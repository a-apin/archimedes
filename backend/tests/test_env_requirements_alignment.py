"""`environment.yml` and `backend/requirements.txt` must agree on shared floors.

CLAUDE.md § Linting, formatting, dependencies: "Keep `environment.yml` (local dev)
and `backend/requirements.txt` (Docker / CI) aligned. Drift is the most common
source of 'works on my machine' + 'breaks in CI'."

Nothing enforced that until now, and it had drifted on 18 of 34 shared packages —
every one with the local floor *below* the deployed one, so a conda env could
resolve pandas 2.x while production ran 3.x. Dependabot only ever edits
`backend/requirements.txt`, so the gap reopens on its own every week unless a test
holds it shut.

Deliberately stdlib-only: no PyYAML, no `packaging`. Neither is a declared
dependency of the backend, and a guard that can only run when an undeclared
transitive import happens to be present is not a guard.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
ENVIRONMENT_YML = REPO_ROOT / "environment.yml"
REQUIREMENTS_TXT = REPO_ROOT / "backend" / "requirements.txt"

# A dependency line: optional quotes, a name, optional extras, then the specifier.
_DEP = re.compile(r'^"?(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)(?P<extras>\[[^\]]*\])?(?P<spec>.*)$')
_FLOOR = re.compile(r">=\s*(?P<version>[0-9][0-9A-Za-z.*+!-]*)")


def _canonical(name: str) -> str:
    """PEP 503 normalisation, so `python_dotenv` and `python-dotenv` are one key."""
    return re.sub(r"[-_.]+", "-", name).lower()


def _strip_comment(line: str) -> str:
    return line.split("#", 1)[0].strip()


def _parse_dep(line: str) -> tuple[str, str] | None:
    """Return (canonical name, floor) for a dependency line, or None if it isn't one."""
    body = _strip_comment(line)
    # `@ git+...` pins, index directives and bare option lines carry no comparable floor.
    if not body or body.startswith("-") or "@" in body:
        return None
    match = _DEP.match(body)
    if match is None:
        return None
    floor = _FLOOR.search(match.group("spec") or "")
    if floor is None:
        return None
    return _canonical(match.group("name")), floor.group("version")


def _requirements_floors() -> dict[str, str]:
    out: dict[str, str] = {}
    for line in REQUIREMENTS_TXT.read_text().splitlines():
        parsed = _parse_dep(line)
        if parsed is not None:
            out[parsed[0]] = parsed[1]
    return out


def _environment_floors() -> dict[str, str]:
    """Read the `pip:` block of environment.yml without a YAML parser.

    The block is the run of `      - dep` lines under `  - pip:`; it ends at the
    first line that is neither blank, nor a comment, nor indented past `pip:`.
    """
    out: dict[str, str] = {}
    in_pip = False
    for line in ENVIRONMENT_YML.read_text().splitlines():
        if re.match(r"^\s*-\s*pip:\s*$", line):
            in_pip = True
            continue
        if not in_pip:
            continue
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())
        if indent < 6:
            break
        parsed = _parse_dep(line.lstrip().removeprefix("- "))
        if parsed is not None:
            out[parsed[0]] = parsed[1]
    return out


def _shared() -> list[str]:
    return sorted(set(_environment_floors()) & set(_requirements_floors()))


def test_both_files_parse_into_a_meaningful_shared_set() -> None:
    """Fail loudly if the parser stops matching, rather than passing vacuously.

    Without this, a regex that silently matched nothing would make the alignment
    assertion below trivially true — the exact failure mode CLAUDE.md calls out
    ("a guard must be shown to reject something").
    """
    env, req = _environment_floors(), _requirements_floors()
    assert len(env) >= 30, f"environment.yml pip block parsed to only {len(env)} deps"
    assert len(req) >= 30, f"requirements.txt parsed to only {len(req)} deps"
    assert len(_shared()) >= 30, f"only {len(_shared())} shared deps found; parser likely broken"


def test_shared_dependency_floors_are_identical() -> None:
    env, req = _environment_floors(), _requirements_floors()
    mismatched = {name: (env[name], req[name]) for name in _shared() if env[name] != req[name]}
    assert not mismatched, (
        "environment.yml has drifted from backend/requirements.txt. Local dev would "
        "resolve a different version than Docker/CI for:\n"
        + "\n".join(
            f"  {name}: environment.yml >={e}  vs  requirements.txt >={r}"
            for name, (e, r) in sorted(mismatched.items())
        )
        + "\nRaise the environment.yml floor to match; both files change in the same PR."
    )


def test_no_environment_floor_is_below_the_deployed_floor() -> None:
    """The asymmetric half of the rule, kept separate because it is the harmful one.

    An env floor *above* production is merely strict. An env floor *below* it means
    local dev can run code paths production never executes, which is how a pandas
    2-vs-3 split goes unnoticed.
    """
    env, req = _environment_floors(), _requirements_floors()

    def as_tuple(version: str) -> tuple[int, ...]:
        return tuple(int(part) for part in re.findall(r"\d+", version))

    below = {name: (env[name], req[name]) for name in _shared() if as_tuple(env[name]) < as_tuple(req[name])}
    assert not below, "environment.yml floor is lower than the deployed floor for: " + ", ".join(
        f"{n} ({e} < {r})" for n, (e, r) in sorted(below.items())
    )
