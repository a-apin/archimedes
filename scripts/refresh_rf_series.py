#!/usr/bin/env python3
"""Re-download the vendored 3-month T-bill series (FRED DGS3MO), issue #1409.

The rigor gate's risk-free rate is a VENDORED file
(``backend/archimedes/data/rf/DGS3MO.csv``), never a runtime network call —
determinism is load-bearing there (see ``backend/archimedes/services/
rf_series.py``'s module docstring). This script is the one place network I/O
for this series is allowed: an explicit, human-run refresh, never invoked
from the grading path, from tests, or from CI.

Historical FRED values never change once published; only new dates append
over time. FRED's ``fredgraph.csv`` endpoint returns the full history on
every call (there is no incremental "since" query on this path), so this
script re-fetches the whole series and overwrites the vendored file's data
rows — it is not a diff/merge. It REGENERATES the header comment block from
``HEADER_TEMPLATE`` below (not a preserve-and-patch of the existing file's
header — a prior claim here that it "preserves the header comment block" was
inaccurate, corrected 2026-08-20 review) and sanity-checks that the new
download is a superset of the previously-vendored date range before writing,
so a truncated or corrupted download can never silently regress the series.

Usage::

    python scripts/refresh_rf_series.py            # refresh in place
    python scripts/refresh_rf_series.py --check     # exit 1 if the vendored
                                                      # file is stale (CI-style,
                                                      # not wired into any gate)
"""

from __future__ import annotations

import argparse
import sys
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

FRED_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=DGS3MO"
CSV_PATH = Path(__file__).resolve().parent.parent / "backend" / "archimedes" / "data" / "rf" / "DGS3MO.csv"

HEADER_TEMPLATE = """\
# FRED DGS3MO -- Market Yield on U.S. Treasury Securities at 3-Month Constant
# Maturity, Quoted on an Investment Basis. (Not DTB3 "3-Month Treasury Bill
# Secondary Market Rate, Discount Basis" -- a different FRED series/quotation
# convention; corrected 2026-08-20 review, see issue #1409.)
# Source: {url}
# Vendored (issue #1409): {vendored_date}.
# Values are ANNUALIZED PERCENT (e.g. 3.86 means 3.86%/yr); convert to a
# per-bar daily rate via rate/100/252 -- see rf_series.py.
# Missing observations (market holidays) publish with an empty value, or
# FRED's classic "." marker on some export paths -- both are treated as
# missing and skipped without poisoning the forward-fill of neighboring
# dates (see rf_series._parse_csv_text).
# STALENESS -- historical values never change once published; only new
# dates append over time. Re-run `python scripts/refresh_rf_series.py` to
# extend coverage. A backtest window whose bars run more than 14 calendar
# days past the last vendored date below falls back to the flat convention
# (rf_convention = "excess_flat_fallback", disclosed loudly, never silent)
# -- see docs/rigor-methods.md and issue #1409 for the full design record.
"""


def _split_header_and_rows(text: str) -> tuple[str, list[str]]:
    lines = text.splitlines()
    body_start = next((i for i, ln in enumerate(lines) if not ln.lstrip().startswith("#")), len(lines))
    return "\n".join(lines[:body_start]), lines[body_start:]


def _dates_in(rows: list[str]) -> set[str]:
    out = set()
    for ln in rows[1:]:  # rows[0] is the CSV header (observation_date,DGS3MO)
        if not ln.strip():
            continue
        out.add(ln.split(",", 1)[0])
    return out


def refresh(check_only: bool = False) -> int:
    existing_text = CSV_PATH.read_text(encoding="utf-8") if CSV_PATH.exists() else ""
    _, existing_rows = _split_header_and_rows(existing_text)
    existing_dates = _dates_in(existing_rows)

    with urllib.request.urlopen(FRED_URL, timeout=30) as resp:
        downloaded = resp.read().decode("utf-8")

    _, new_rows = _split_header_and_rows(downloaded)
    new_dates = _dates_in(new_rows)

    missing = existing_dates - new_dates
    if missing:
        print(
            f"refresh_rf_series: REFUSING to write -- the fresh download is missing "
            f"{len(missing)} date(s) already in the vendored file (e.g. {sorted(missing)[:3]}). "
            "Historical FRED values never disappear; this looks like a truncated or bad "
            "download, not a real API change.",
            file=sys.stderr,
        )
        return 1

    new_only = sorted(new_dates - existing_dates)
    if check_only:
        if new_only:
            print(f"refresh_rf_series: STALE -- {len(new_only)} new date(s) available (e.g. {new_only[:3]}).")
            return 1
        print("refresh_rf_series: up to date.")
        return 0

    header = HEADER_TEMPLATE.format(
        url=FRED_URL,
        vendored_date=datetime.now(UTC).date().isoformat(),
    )
    CSV_PATH.parent.mkdir(parents=True, exist_ok=True)
    CSV_PATH.write_text(header + "\n".join(new_rows) + "\n", encoding="utf-8")
    print(f"refresh_rf_series: wrote {len(new_dates)} dates ({len(new_only)} new) to {CSV_PATH}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="exit 1 if new dates are available; don't write")
    args = parser.parse_args()
    return refresh(check_only=args.check)


if __name__ == "__main__":
    raise SystemExit(main())
