"""Generate the deployed-synth address SSOT from a contract-deploy manifest.

The on-chain synth *metadata* SSOT is ``data/synthetic_universe.json`` (symbols,
asset_class, yfinance_ticker, pyth_feed_id, …) — it deliberately carries NO
addresses, because addresses change on every contract redeploy while the metadata
does not. This script produces the companion *address* SSOT
``data/synthetic_addresses.json`` — ``{symbol: {token, oracle}}`` for every synth
— from a deploy manifest, so ``chain/client.py`` can resolve all 281 (not just a
hand-maintained handful). Env ``ARC_<SYMBOL>_ADDRESS`` / ``ARC_<SYMBOL>_ORACLE_ADDRESS``
still override per-synth at runtime (see ``client._resolve_ssot_addresses``).

Regenerate after every contract redeploy:

    python -m archimedes.scripts.gen_synthetic_addresses \
        --manifest-dir /path/to/<deploy-output> --write

The manifest dir must contain ``synth-tokens.tsv`` and ``synth-oracles.tsv``,
each a ``<symbol>\\t<0x-address>`` TSV (as emitted by the Foundry deploy).

INVARIANT (enforced): every symbol written here MUST exist in the metadata SSOT
(``ON_CHAIN_SYNTHS``), and every deployed synth in the manifest must map 1:1 — a
divergence means the deploy and the SSOT disagree, which must be fixed before wiring.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
_UNIVERSE_PATH = _DATA_DIR / "synthetic_universe.json"
_OUT_PATH = _DATA_DIR / "synthetic_addresses.json"


def _read_tsv(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in path.read_text().splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[1].startswith("0x") and len(parts[1]) == 42:
            out[parts[0]] = parts[1].lower()
    return out


def build(manifest_dir: Path) -> dict[str, dict[str, str]]:
    ssot = set(json.loads(_UNIVERSE_PATH.read_text())["synthetics"].keys())
    tokens = _read_tsv(manifest_dir / "synth-tokens.tsv")
    oracles = _read_tsv(manifest_dir / "synth-oracles.tsv")

    # Parity: manifest ↔ SSOT must agree exactly.
    missing_from_manifest = sorted(ssot - set(tokens))
    extra_in_manifest = sorted(set(tokens) - ssot)
    if missing_from_manifest or extra_in_manifest:
        raise SystemExit(
            "SSOT/manifest divergence — fix before wiring addresses:\n"
            f"  SSOT symbols with no deployed token: {missing_from_manifest}\n"
            f"  deployed tokens not in the SSOT: {extra_in_manifest}"
        )

    addrs: dict[str, dict[str, str]] = {}
    for sym in sorted(ssot):
        entry: dict[str, str] = {"token": tokens[sym]}
        if sym in oracles:
            entry["oracle"] = oracles[sym]
        addrs[sym] = entry
    return addrs


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--manifest-dir", required=True, type=Path, help="dir with synth-tokens.tsv + synth-oracles.tsv")
    ap.add_argument("--write", action="store_true", help="write data/synthetic_addresses.json (else dry-run)")
    args = ap.parse_args()

    addrs = build(args.manifest_dir)
    n_oracle = sum(1 for v in addrs.values() if v.get("oracle"))
    print(f"built {len(addrs)} synth addresses ({n_oracle} with an oracle) from {args.manifest_dir}")
    if args.write:
        _OUT_PATH.write_text(json.dumps(addrs, indent=1, sort_keys=True) + "\n")
        print(f"wrote {_OUT_PATH}")
    else:
        print("dry-run — pass --write to persist")
    return 0


if __name__ == "__main__":
    sys.exit(main())
