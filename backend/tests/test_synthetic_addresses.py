"""The deploy-address SSOT (synthetic_addresses.json) must cover the FULL 281-synth
universe, keyed by the SAME symbols as the metadata SSOT — so chain/client.py resolves
every synth (not just a demo handful) and Explore/trade cover the whole set.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

_DATA = Path(__file__).resolve().parent.parent / "archimedes" / "data"
_ADDR = _DATA / "synthetic_addresses.json"
_UNIV = _DATA / "synthetic_universe.json"
_ADDR_RE = re.compile(r"^0x[0-9a-f]{40}$")


def _addresses() -> dict:
    return json.loads(_ADDR.read_text())


def _universe_symbols() -> set[str]:
    return set(json.loads(_UNIV.read_text())["synthetics"].keys())


def test_address_ssot_covers_the_full_universe():
    """Every SSOT symbol has a deployed token address, and there are no extras."""
    addrs = _addresses()
    univ = _universe_symbols()
    assert set(addrs.keys()) == univ, (
        f"address SSOT ≠ metadata SSOT — missing: {sorted(univ - set(addrs))[:5]}, "
        f"extra: {sorted(set(addrs) - univ)[:5]}"
    )
    assert len(addrs) == 281, f"expected 281 synths, got {len(addrs)}"


def test_every_synth_has_a_token_and_oracle_address():
    for sym, entry in _addresses().items():
        assert entry.get("token"), f"{sym} has no token address"
        assert entry.get("oracle"), f"{sym} has no oracle address"


def test_all_addresses_are_well_formed_lowercase_hex():
    for sym, entry in _addresses().items():
        for kind in ("token", "oracle"):
            addr = entry[kind]
            assert _ADDR_RE.match(addr), f"{sym}.{kind} malformed: {addr!r}"


def test_client_resolves_all_281_from_the_file(monkeypatch):
    """chain/client.py must expose all 281 synth + oracle addresses with NO env overrides
    set — i.e. purely from the committed address SSOT (the file-backed default path)."""
    import os

    for k in list(os.environ):
        if k.startswith("ARC_"):
            monkeypatch.delenv(k, raising=False)
    # Module defaults already come from the committed file; rebuilding settings
    # is enough to apply the clean env without replacing shared module globals.
    from archimedes.chain import client as client_mod

    settings = client_mod.ChainSettings()
    assert len(settings.synth_addresses) == 281
    assert len(settings.oracle_addresses) == 281
    # A non-demo synth resolves (the whole point of the convergence).
    assert settings.synth_addresses.get("sAAVE")
