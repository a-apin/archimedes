"""The FastAPI lifespan must not read backtest_results (2026-08-19 OOM guard).

The startup handler used to `.all()` every backtest_results row for every
curated strategy and then call ``evaluate_rigor_gate()``.

Two things made that a container-killer rather than a slow boot:

* ``artifact_json`` and ``equity_curve_json`` are plain ``Text`` columns with no
  deferred loading, so every row's JSON blob was materialised. Measured in the
  production image: +1079 MB of RSS at 408 rows, strictly linear at ~2.6 MB/row,
  and growing daily because nothing dedupes that table.
* ``lifespan`` is an ``@asynccontextmanager`` that suspends at its ``yield`` for
  the entire process lifetime, so Python pins the frame and every local in it.
  The blobs were retained garbage for the life of the container, not a spike.

``evaluate_rigor_gate()`` was also being called as a plain function despite
being a FastAPI route handler with a ``Query(...)`` default, so it burned the
full cohort DSR/PBO computation and then raised on response validation — it had
never once succeeded.

This is a source-level guard on purpose: booting the whole app to observe the
absence of a query is far more fragile than asserting the code is not there, and
the property we care about ("the lifespan does not touch this table") is exactly
a property of the source.
"""

from __future__ import annotations

import inspect

import archimedes.main as main_module


def _lifespan_source() -> str:
    # __wrapped__: lifespan is decorated with @asynccontextmanager, so the
    # decorated object is not the function whose body we want to inspect.
    fn = getattr(main_module.lifespan, "__wrapped__", main_module.lifespan)
    return inspect.getsource(fn)


def _code_lines(source: str) -> list[str]:
    """Source lines with comments and docstring-ish content stripped."""
    out = []
    for raw in source.splitlines():
        line = raw.split("#", 1)[0]
        if line.strip():
            out.append(line)
    return out


def test_lifespan_does_not_query_backtest_results() -> None:
    code = "\n".join(_code_lines(_lifespan_source()))
    assert "BacktestResultRecord" not in code, (
        "lifespan references BacktestResultRecord again. Reading that table at "
        "startup retains every artifact_json/equity_curve_json blob for the life "
        "of the process (measured +1079 MB at 408 rows) because the lifespan "
        "frame is pinned at its yield."
    )


def test_lifespan_does_not_call_the_rigor_gate_route() -> None:
    code = "\n".join(_code_lines(_lifespan_source()))
    assert "evaluate_rigor_gate" not in code, (
        "lifespan calls evaluate_rigor_gate() again. It is a FastAPI route "
        "handler whose `strictness` parameter defaults to Query(...); called "
        "directly it runs the whole cohort DSR/PBO computation and then raises "
        "on response validation. The rigor gate belongs in the generation "
        "pipeline and in GET /api/selection-bias/gate, not at boot."
    )


# NOTE: deliberately NOT asserting "no .all() anywhere in lifespan". The
# marketplace rehydration block (main.py:269,279) legitimately `.all()`s
# MarketplaceAgent rows filtered to status == "running" — small metadata rows
# with no blob columns. A blanket rule would fail on that pre-existing query and
# force unrelated restructuring without guarding anything real. The two
# assertions above name the actual defect: the blob-carrying table, and the
# route handler that was called as a function.
