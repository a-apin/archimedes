"""Generate ``docs/asset-universe.md`` from the synthetic-universe SSOT.

A human-readable, drift-proof breakdown of exactly what Archimedes prices on-chain —
rendered FROM ``backend/archimedes/data/synthetic_universe.json`` (the SSOT) so the doc
can never go stale relative to the actual universe. The on-chain deploy-eligible synths
are grouped by asset class with their oracle price and HONEST per-source oracle coverage
(``pyth_feed_id`` present/absent, ``stork_asset_id``, ``chainlink_feed``, and the derived
``oracle_tier`` hint — T1.5b / #759); the single-name equity synths held BACK from the live
path (backtest-only, pending compliance review) are listed separately. A test
(``test_asset_universe_doc.py``) byte-diffs a fresh render against the committed file to fail
CI on stale output (#757).

Usage (the #757 canonical command goes through the thin ``scripts/`` wrapper; the
module form is equivalent)::

    PYTHONPATH=backend python scripts/gen_asset_universe_doc.py            # write the doc
    PYTHONPATH=backend python scripts/gen_asset_universe_doc.py --check    # exit 1 if stale (CI)
    python -m archimedes.scripts.gen_asset_universe_doc [--check]          # equivalent module form
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from archimedes.universe import (
    COMPLIANCE_FLAGGED_SINGLE_STOCKS,
    SYNTHETIC_UNIVERSE,
)

# repo-root-relative: scripts -> archimedes -> backend -> <repo root>
_OUTPUT_PATH = Path(__file__).resolve().parents[3] / "docs" / "asset-universe.md"
# scripts -> archimedes; the SSOT lives at archimedes/data/synthetic_universe.json
_SSOT_PATH = Path(__file__).resolve().parents[1] / "data" / "synthetic_universe.json"


def _compliance_review() -> dict[str, str]:
    """Read the SSOT ``_compliance_review`` block so the doc renders its status + reason
    VERBATIM rather than hardcoding compliance prose that could drift from the SSOT."""
    payload = json.loads(_SSOT_PATH.read_text(encoding="utf-8"))
    cr = payload.get("_compliance_review", {})
    return {"status": str(cr.get("status", "")), "reason": str(cr.get("reason", ""))}


# Friendly labels for the SSOT asset_class keys (anything unmapped falls back to the key).
_CLASS_LABELS = {
    "crypto": "Crypto",
    "fx": "FX",
    "us_equity_etf": "US equity ETFs",
    "us_sector_etf": "US sector ETFs",
    "us_thematic_etf": "US thematic/industry ETFs",
    "factor_etf": "Style / factor ETFs",
    "reit_etf": "REIT / real-estate ETFs",
    "intl_equity_etf": "International equity ETFs",
    "intl_bond": "International bonds",
    "eu_equity_etf": "EU equity ETFs",
    "eu_index": "EU indices",
    "asia_equity_etf": "Asia equity ETFs",
    "asia_index": "Asia indices",
    "em_equity_etf": "EM equity ETFs",
    "latam_equity_etf": "LatAm equity ETFs",
    "tr_equity_etf": "Turkish equity ETFs",
    "tr_index": "Turkish indices",
    "metal_etf": "Metal ETFs",
    "metal_eq_etf": "Metal-miner ETFs",
    "metal_fut": "Metal futures",
    "metal_spot": "Metal spot",
    "commodity_etf": "Commodity ETFs",
    "energy_etf": "Energy ETFs",
    "energy_fut": "Energy futures",
    "agri_etf": "Agricultural ETFs",
    "agri_fut": "Agricultural futures",
    "volatility_etf": "Volatility ETFs",
    "us_bond_agg": "US bonds — aggregate",
    "us_bond_long": "US bonds — long",
    "us_bond_mid": "US bonds — intermediate",
    "us_bond_short": "US bonds — short",
    "us_bond_tbill": "US bonds — T-bills",
    "us_bond_tips": "US bonds — TIPS",
    "us_muni": "US municipal bonds",
    "credit_ig": "Credit — investment grade",
    "credit_hy": "Credit — high yield",
    "em_bond": "EM bonds",
}


def _fmt_price(p: float) -> str:
    """Render the SSOT price at full precision (up to 6 dp, with thousands separators),
    trailing zeros trimmed, but never fewer than 2 dp so ordinary dollar prices keep their
    cents. The doc must reflect the SSOT exactly — e.g. sEURUSD `1.0850` must render as
    ``$1.085``, not round to ``$1.08`` (#757 review)."""
    s = f"{p:,.6f}".rstrip("0")
    int_part, _, frac = s.partition(".")
    if len(frac) < 2:  # 450.000000 -> $450.00 ; 1.085 -> $1.085 ; 0.000123 -> $0.000123
        frac = frac.ljust(2, "0")
    return f"${int_part}.{frac}"


def render_doc() -> str:
    # Sorted by (asset class label, symbol) so the single flat table groups naturally.
    specs = sorted(
        SYNTHETIC_UNIVERSE.values(), key=lambda s: (_CLASS_LABELS.get(s.asset_class, s.asset_class), s.symbol)
    )
    n_total = len(specs)
    n_pyth = sum(1 for s in specs if s.pyth_feed_id)
    n_no_pyth = n_total - n_pyth
    n_stork = sum(1 for s in specs if s.stork_asset_id)
    n_chainlink = sum(1 for s in specs if s.chainlink_feed)
    n_classes = len({s.asset_class for s in specs})
    n_flagged = len(COMPLIANCE_FLAGGED_SINGLE_STOCKS)
    tiers = sorted({s.oracle_tier for s in specs})
    cr = _compliance_review()

    out: list[str] = []
    # Single-line banner per the #757 acceptance format (SSOT + generator path + run command).
    out.append(
        "<!-- GENERATED FROM backend/archimedes/data/synthetic_universe.json BY "
        "scripts/gen_asset_universe_doc.py — DO NOT EDIT BY HAND; run: "
        "PYTHONPATH=backend python scripts/gen_asset_universe_doc.py -->"
    )
    out.append("")
    out.append("# Archimedes asset universe")
    out.append("")
    out.append(
        "What Archimedes prices and trades on-chain. This page is **generated from the single "
        "source of truth** (`backend/archimedes/data/synthetic_universe.json`) so it can never "
        "drift from the actual universe — a CI test fails if it's stale. Regenerate: "
        "`PYTHONPATH=backend python scripts/gen_asset_universe_doc.py`."
    )
    out.append("")
    out.append(f"- **On-chain deploy-eligible synths:** {n_total} across {n_classes} asset classes")
    out.append(
        f"- **Oracle coverage (per source):** {n_pyth} with a real Pyth Hermes feed · "
        f"{n_no_pyth} Pyth-null · {n_stork} Stork · {n_chainlink} Chainlink (of {n_total})"
    )
    out.append(f"- **Expected oracle tiers (hint):** {', '.join(tiers)}")
    out.append(f"- **Single-name equity synths held back (backtest-only, compliance):** {n_flagged}")
    out.append("")
    out.append(
        "**Oracle honesty (T1.5b / #759):** `pyth_feed_id` is a REAL Pyth Hermes id or null "
        "(never fabricated); `stork_asset_id` and `chainlink_feed` are null for now — no public "
        "Stork catalog to resolve yet, and Chainlink price Data Feeds are NOT yet deployed on Arc "
        "testnet (only CCIP as of 2026-06-30). `oracle_tier` is a HINT from the count of configured "
        "feeds (`quorum` ≥2, `single_checked` =1, `admin` =0); the AUTHORITATIVE tier is on-chain via "
        "`QuorumPriceOracle.priceWithProvenance()` (#840)."
    )
    out.append("")
    out.append(
        "**Parity invariant:** every on-chain synth is also backtestable "
        "(`on-chain ⊆ GLOBAL_ASSETS`); every backtestable-but-not-on-chain symbol is an "
        "explained compliance-flagged single stock; and **no on-chain synth is compliance-held** "
        "(`on-chain ∩ compliance-held = ∅`). Enforced by `backend/tests/test_universe_parity.py`."
    )
    out.append("")
    out.append("## On-chain deploy-eligible universe")
    out.append("")
    out.append(f"All {n_total} synths below are **on-chain-eligible** (priced on the live path).")
    out.append("")
    # Machine-greppable columns: BARE symbol first (so the `^\|\s*s[A-Z]` acceptance regex counts
    # exactly these rows — the compliance list below is inline-backticked, not table rows), raw
    # `asset_class`, and HONEST per-source oracle columns. `pyth` renders yes/no so the split is
    # greppable; `chainlink_feed` renders null everywhere (Arc has no feeds yet); `oracle_tier` is
    # the derived hint.
    out.append("| symbol | name | asset_class | price_usd | pyth | stork | chainlink_feed | oracle_tier |")
    out.append("|---|---|---|---:|:---:|:---:|:---:|:---:|")
    for spec in specs:
        pyth = "yes" if spec.pyth_feed_id else "no"
        stork = "yes" if spec.stork_asset_id else "no"
        chainlink = spec.chainlink_feed if spec.chainlink_feed else "null"
        out.append(
            f"| {spec.symbol} | {spec.name} | {spec.asset_class} | "
            f"{_fmt_price(spec.price_usd)} | {pyth} | {stork} | {chainlink} | {spec.oracle_tier} |"
        )
    out.append("")
    out.append("## Held back — single-name equities (backtest-only)")
    out.append("")
    # Status + reason rendered VERBATIM from the SSOT _compliance_review block.
    out.append(f"**Compliance status (verbatim from the SSOT):** {cr['status']}")
    out.append("")
    out.append(f"> {cr['reason']}")
    out.append("")
    out.append(
        f"The {n_flagged} single-name equity synths below are present in the backtest universe but "
        "**NOT** on the live on-chain path. Do not add any to the SSOT `synthetics` without sign-off."
    )
    out.append("")
    flagged = sorted(COMPLIANCE_FLAGGED_SINGLE_STOCKS)
    # Render as a wrapped inline-code list, 8 per line, for readability.
    for i in range(0, len(flagged), 8):
        out.append(" ".join(f"`{s}`" for s in flagged[i : i + 8]))
    out.append("")
    return "\n".join(out) + "\n"


def _force_utf8_stdio() -> None:
    """Make the CLI locale-independent: the doc + unified diff contain non-ASCII (✅ · ⊆ ∩ ∅,
    em-dash), so writing them to stdout/stderr in an ASCII / C-locale shell would raise
    UnicodeEncodeError. Reconfigure stdio to UTF-8 so the documented command is robust when run
    exactly as written, in any locale — not reliant on the caller setting PYTHONUTF8 (#757 review)."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    _force_utf8_stdio()
    argv = list(sys.argv[1:] if argv is None else argv)
    # Output path is overridable via env so the drift test can exercise the real --check CLI
    # (exit code + diff) against a temp file, without mutating the committed doc.
    out_path = Path(os.environ.get("ASSET_UNIVERSE_DOC_PATH", str(_OUTPUT_PATH)))
    content = render_doc()
    if "--check" in argv:
        if not out_path.exists():
            print(f"MISSING: {out_path} does not exist — run the generator.", file=sys.stderr)
            return 1
        committed = out_path.read_text(encoding="utf-8")
        if committed != content:
            import difflib

            sys.stderr.writelines(
                difflib.unified_diff(
                    committed.splitlines(keepends=True),
                    content.splitlines(keepends=True),
                    fromfile=f"committed {out_path}",
                    tofile="freshly generated from SSOT",
                )
            )
            print(
                f"\nSTALE: {out_path} is out of sync with the SSOT (diff above) — regenerate with "
                "`PYTHONPATH=backend python scripts/gen_asset_universe_doc.py`.",
                file=sys.stderr,
            )
            return 1
        print(f"OK: {out_path} is in sync with the SSOT.")
        return 0
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(content, encoding="utf-8")
    print(f"wrote {out_path} ({len(content.splitlines())} lines)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
