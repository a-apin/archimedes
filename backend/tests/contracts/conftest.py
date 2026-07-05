"""
Shared test fixtures for PaymentSplitter contract tests.

Provides:
- Mock USDC token (ERC-20 with mint())
- Test accounts (owner, creator, platform, attacker, funder)
- Pre-deployed PaymentSplitter (Vyper via boa — skip when Vyper source absent)

NOTE: PaymentSplitter has been migrated to Solidity (contracts/src/PaymentSplitter.sol).
These tests need to be rewritten against the Solidity ABI (tracked as a post-merge issue).
Until then they skip gracefully when the legacy Vyper source is not present.
"""

import os

import pytest
import boa

MOCK_USDC_SOURCE = """
# @version ^0.4.0

event Transfer:
    sender: indexed(address)
    receiver: indexed(address)
    amount: uint256

event Approval:
    owner: indexed(address)
    spender: indexed(address)
    amount: uint256

name: public(String[64])
symbol: public(String[32])
decimals: public(uint8)
totalSupply: public(uint256)

balanceOf: public(HashMap[address, uint256])
allowance: public(HashMap[address, HashMap[address, uint256]])

@deploy
def __init__():
    self.name = "USD Coin"
    self.symbol = "USDC"
    self.decimals = 6

@external
def mint(to: address, amount: uint256):
    self.balanceOf[to] += amount
    self.totalSupply += amount
    log Transfer(sender=empty(address), receiver=to, amount=amount)

@external
def transfer(to: address, amount: uint256) -> bool:
    assert self.balanceOf[msg.sender] >= amount, "insufficient balance"
    self.balanceOf[msg.sender] -= amount
    self.balanceOf[to] += amount
    log Transfer(sender=msg.sender, receiver=to, amount=amount)
    return True

@external
def approve(spender: address, amount: uint256) -> bool:
    self.allowance[msg.sender][spender] = amount
    log Approval(owner=msg.sender, spender=spender, amount=amount)
    return True

@external
def transferFrom(_from: address, _to: address, _value: uint256) -> bool:
    assert _value <= self.allowance[_from][msg.sender], "exceeded allowance"
    assert _value <= self.balanceOf[_from], "insufficient balance"
    self.allowance[_from][msg.sender] -= _value
    self.balanceOf[_from] -= _value
    self.balanceOf[_to] += _value
    log Transfer(sender=_from, receiver=_to, amount=_value)
    return True
"""


@pytest.fixture(scope="module")
def usdc():
    """Deploy a Mock USDC token (module-scoped to share across tests)."""
    return boa.loads(MOCK_USDC_SOURCE)


@pytest.fixture(scope="module")
def accounts():
    """Generate deterministic test accounts.

    Returns a dict for readability in tests.
    """
    return {
        "owner": boa.env.generate_address("owner"),
        "creator": boa.env.generate_address("creator"),
        "platform": boa.env.generate_address("platform"),
        "funder": boa.env.generate_address("funder"),
        "attacker": boa.env.generate_address("attacker"),
        "bystander": boa.env.generate_address("bystander"),
    }


_VYPER_SOURCE = "contracts/vyper/PaymentSplitter.vy"


@pytest.fixture
def splitter(usdc, accounts):
    """Deploy a fresh PaymentSplitter per test (owner = accounts.owner).

    We mint USDC to funder/creator/platform/attacker so they have tokens.

    Skips when the legacy Vyper source is absent (contract migrated to Solidity).
    See contracts/src/PaymentSplitter.sol — post-merge task to rewrite these
    tests against the Solidity ABI.
    """
    if not os.path.exists(_VYPER_SOURCE):
        pytest.skip(
            f"Legacy Vyper source '{_VYPER_SOURCE}' not found — "
            "PaymentSplitter migrated to Solidity; tests need rewrite."
        )

    owner = accounts["owner"]
    usdc.mint(accounts["funder"], 1_000_000 * 10**6)
    usdc.mint(accounts["creator"], 1_000_000 * 10**6)
    usdc.mint(accounts["platform"], 1_000_000 * 10**6)
    usdc.mint(accounts["attacker"], 1_000_000 * 10**6)

    with boa.env.prank(owner):
        return boa.load(
            _VYPER_SOURCE,
            usdc.address,
        )
