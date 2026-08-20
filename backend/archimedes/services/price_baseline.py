"""Canonical Redis key + address normalization for the vault price baseline.

The baseline WRITER (``chain/executor._write_price_baseline_if_absent``) and
READER (``services/vault_service._compute_returns``) must agree on two things:
the per-vault Redis key, and the casing of the token-address keys inside the
JSON snapshot. EVM addresses appear both EIP-55-checksummed and lowercase
across the codebase, so a raw f-string on either side lets the pair silently
miss each other — the baseline looks absent (or a token looks unpriced) and
returns read "unavailable" forever (#1201 review). Both sides import these
two functions instead of restating the convention.

No project imports here on purpose: this module must be importable from both
``chain`` and ``services`` without cycles.
"""

from __future__ import annotations


def baseline_key(vault_address: str) -> str:
    """The per-vault baseline snapshot key — vault address always lowercased."""
    return f"vault:prices:{(vault_address or '').lower()}"


def normalize_token(token_address: str) -> str:
    """Token-address keys inside the snapshot — always lowercased."""
    return (token_address or "").lower()
