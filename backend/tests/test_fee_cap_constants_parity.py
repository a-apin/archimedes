"""The Python fee-cap mirror is checked against the Solidity truth (G10).

``chain/constants.py`` mirrors ``Vault.sol``'s constructor fee caps
(PR #1129) and MUST stay in lockstep — the backend uses the mirror to refuse
listing/interacting with hostile-fee vaults (#1138), so a silent drift would
re-open exactly the gap the caps closed. This is the house
promise-checked-against-the-truth pattern: parse the OTHER side's actual
source and compare, so neither side can move alone.

Deliberately a HARD FAIL, never a skip (the audit's G10 finding was that the
missing Solidity side let a parity check skip silently): if Vault.sol or its
constants can't be found, that is itself the failure.
"""

from __future__ import annotations

import re
from pathlib import Path

from archimedes.chain.constants import MAX_MANAGEMENT_FEE_BPS, MAX_PERFORMANCE_FEE_BPS

_VAULT_SOL = Path(__file__).resolve().parents[2] / "contracts" / "src" / "Vault.sol"


def _solidity_constant(name: str) -> int:
    """The literal assigned to a uint16 constant in Vault.sol — or a loud fail."""
    assert _VAULT_SOL.is_file(), f"Vault.sol not found at {_VAULT_SOL} — the parity check must fail, not skip"
    source = _VAULT_SOL.read_text()
    match = re.search(rf"uint16\s+public\s+constant\s+{name}\s*=\s*(\d+)\s*;", source)
    assert match, f"{name} not declared in Vault.sol — the Solidity side of the cap is missing (G10)"
    return int(match.group(1))


def test_management_fee_cap_matches_vault_sol() -> None:
    sol = _solidity_constant("MAX_MANAGEMENT_FEE_BPS")
    assert sol == MAX_MANAGEMENT_FEE_BPS, (
        f"Vault.sol MAX_MANAGEMENT_FEE_BPS={sol} != chain.constants {MAX_MANAGEMENT_FEE_BPS} — update both together (#1138)"
    )


def test_performance_fee_cap_matches_vault_sol() -> None:
    sol = _solidity_constant("MAX_PERFORMANCE_FEE_BPS")
    assert sol == MAX_PERFORMANCE_FEE_BPS, (
        f"Vault.sol MAX_PERFORMANCE_FEE_BPS={sol} != chain.constants {MAX_PERFORMANCE_FEE_BPS} — update both together (#1138)"
    )
