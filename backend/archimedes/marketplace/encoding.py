"""Canonical bytes32 + pool_id encoding for the marketplace (D-BYTES32, D-POOL)."""
from __future__ import annotations

from eth_abi import encode as abi_encode
from eth_utils import keccak
from web3 import Web3


def to_bytes32(hexstr: str) -> bytes:
    """0x-hex string -> 32 raw bytes for a contract call. Rejects bad input."""
    h = hexstr.removeprefix("0x")
    if len(h) != 64:
        raise ValueError(f"expected 32-byte hex, got {len(h)//2} bytes: {hexstr!r}")
    return bytes.fromhex(h)


def to_hexstr(value: bytes | str) -> str:
    """Raw bytes (or hex) -> canonical 0x-prefixed 66-char string for DB/JSON."""
    if isinstance(value, str):
        return "0x" + value.removeprefix("0x").rjust(64, "0")
    return "0x" + value.hex()


def derive_pool_id(strategy_id: str, creator_wallet: str) -> str:
    """pool_id = keccak256(abi_encode(strategy_id, creator)). Returns 0x-hex. (D-POOL)"""
    creator = Web3.to_checksum_address(creator_wallet)
    packed = abi_encode(["string", "address"], [strategy_id, creator])
    return "0x" + keccak(packed).hex()
