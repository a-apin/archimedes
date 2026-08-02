"""Parity check: the Python fee-cap constants must never drift from Solidity.

Dan's review on PR #1139 (issue #1138 follow-up): archimedes/chain/constants.py
mirrors Vault.sol's MAX_MANAGEMENT_FEE_BPS/MAX_PERFORMANCE_FEE_BPS by comment
convention only — nothing ties them together, so if PR #1129 changes the caps
during contract review, the off-chain guard silently starts enforcing a
different bound than the chain does.

Ordering note: #1129 (the Solidity caps) has not merged to main as of this
PR — the constants don't exist in contracts/src/Vault.sol yet. This test
skips (not fails) until they land, so #1139 isn't blocked on #1129's merge
order; once the constants are present, a mismatch fails loudly.
"""

from __future__ import annotations

import re
from pathlib import Path

from archimedes.chain.constants import MAX_MANAGEMENT_FEE_BPS, MAX_PERFORMANCE_FEE_BPS

_MGMT_RE = re.compile(r"MAX_MANAGEMENT_FEE_BPS\s*=\s*(\d+)\s*;")
_PERF_RE = re.compile(r"MAX_PERFORMANCE_FEE_BPS\s*=\s*(\d+)\s*;")


def test_python_caps_match_vault_sol(repo_root: Path) -> None:
    vault_sol = repo_root / "contracts" / "src" / "Vault.sol"
    source = vault_sol.read_text()

    mgmt_match = _MGMT_RE.search(source)
    perf_match = _PERF_RE.search(source)
    if mgmt_match is None or perf_match is None:
        import pytest

        pytest.skip(
            "Vault.sol does not yet define MAX_MANAGEMENT_FEE_BPS/MAX_PERFORMANCE_FEE_BPS "
            "(PR #1129 not merged) — nothing to compare against yet."
        )

    assert int(mgmt_match.group(1)) == MAX_MANAGEMENT_FEE_BPS, (
        f"Vault.sol MAX_MANAGEMENT_FEE_BPS={mgmt_match.group(1)} != "
        f"archimedes.chain.constants.MAX_MANAGEMENT_FEE_BPS={MAX_MANAGEMENT_FEE_BPS} — "
        "update both together (issue #1138)."
    )
    assert int(perf_match.group(1)) == MAX_PERFORMANCE_FEE_BPS, (
        f"Vault.sol MAX_PERFORMANCE_FEE_BPS={perf_match.group(1)} != "
        f"archimedes.chain.constants.MAX_PERFORMANCE_FEE_BPS={MAX_PERFORMANCE_FEE_BPS} — "
        "update both together (issue #1138)."
    )
