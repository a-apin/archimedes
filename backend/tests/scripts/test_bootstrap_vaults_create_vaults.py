"""Tests for create_vaults()'s createVault revert handling (#655).

Mirrors TestCreateVault in backend/tests/chain/test_chain_executor.py (#651):
create_vaults() submits createVault via Circle's execute_contract and, before
this fix, fell back to all_vaults[-1] (an existing vault's address) on both a
revert and a successful-tx-but-no-event case, indistinguishably.

Hermetic: circle_signer, chain_client, and get_contract_loader are mocked.
No network, no Arc RPC, no Circle.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from archimedes.scripts.bootstrap_vaults import create_vaults
from web3.datastructures import AttributeDict

_TEST_PROFILE = {
    "name": "Test Vault",
    "symbol": "vTEST",
    "management_fee_bps": 100,
    "performance_fee_bps": 1000,
    "agent_assisted": True,
    "allocations": {"USDC": 10000},
}


class TestCreateVaults:
    """Coverage for create_vaults' createVault revert handling (#655)."""

    def test_happy_path_returns_parsed_vault_address(self):
        """status=1 + VaultCreated event found → vault appended with the
        address parsed from the event (not the getVaults()-idempotency-check
        fallback, which is called once upfront per #1102 but returns no
        existing vaults here)."""
        factory = MagicMock()
        factory.events.VaultCreated.return_value.process_log.return_value = {"args": {"vault": "0xNewVault"}}
        factory.functions.getVaults.return_value.call = AsyncMock(return_value=[])
        loader = MagicMock()
        loader.vault_factory = factory
        receipt = AttributeDict({"status": 1, "logs": [MagicMock()]})

        with (
            patch("archimedes.scripts.bootstrap_vaults.VAULT_PROFILES", [_TEST_PROFILE]),
            patch("archimedes.scripts.bootstrap_vaults.get_contract_loader", return_value=loader),
            patch("archimedes.scripts.bootstrap_vaults.circle_signer") as mock_signer,
            patch("archimedes.scripts.bootstrap_vaults.chain_client") as mock_cc,
        ):
            mock_signer.execute_contract = AsyncMock(return_value="0xtxhash")
            mock_cc.w3.eth.wait_for_transaction_receipt = AsyncMock(return_value=receipt)

            vaults = asyncio.run(create_vaults({}))

        assert vaults == [
            {
                "address": "0xNewVault",
                "name": "Test Vault",
                "symbol": "vTEST",
                "allocations": {"USDC": 10000},
            }
        ]

    def test_revert_skips_profile_and_prints_error(self, capsys):
        """status=0 → VaultCreationRevertedError is raised, caught by the
        per-profile try/except, and the profile is NOT appended to vaults
        (the bug #655 guards against: silently returning all_vaults[-1])."""
        factory = MagicMock()
        factory.functions.getVaults.return_value.call = AsyncMock(return_value=[])
        loader = MagicMock()
        loader.vault_factory = factory
        receipt = AttributeDict({"status": 0, "logs": []})

        with (
            patch("archimedes.scripts.bootstrap_vaults.VAULT_PROFILES", [_TEST_PROFILE]),
            patch("archimedes.scripts.bootstrap_vaults.get_contract_loader", return_value=loader),
            patch("archimedes.scripts.bootstrap_vaults.circle_signer") as mock_signer,
            patch("archimedes.scripts.bootstrap_vaults.chain_client") as mock_cc,
        ):
            mock_signer.execute_contract = AsyncMock(return_value="0xtxhash")
            mock_cc.w3.eth.wait_for_transaction_receipt = AsyncMock(return_value=receipt)

            vaults = asyncio.run(create_vaults({}))

        assert vaults == []
        captured = capsys.readouterr()
        assert "Test Vault: creation failed" in captured.out
        assert "reverted on-chain" in captured.out

    def test_no_event_falls_back_with_warning(self, capsys):
        """status=1 but no VaultCreated event found → falls back to
        all_vaults[-1] and prints a warning flagging the indexing gap."""
        factory = MagicMock()
        factory.events.VaultCreated.return_value.process_log.side_effect = Exception("no match")
        factory.functions.getVaults.return_value.call = AsyncMock(return_value=["0xOldVault1", "0xOldVault2"])
        loader = MagicMock()
        loader.vault_factory = factory
        receipt = AttributeDict({"status": 1, "logs": [MagicMock()]})

        with (
            patch("archimedes.scripts.bootstrap_vaults.VAULT_PROFILES", [_TEST_PROFILE]),
            patch("archimedes.scripts.bootstrap_vaults.get_contract_loader", return_value=loader),
            patch("archimedes.scripts.bootstrap_vaults.circle_signer") as mock_signer,
            patch("archimedes.scripts.bootstrap_vaults.chain_client") as mock_cc,
        ):
            mock_signer.execute_contract = AsyncMock(return_value="0xtxhash")
            mock_cc.w3.eth.wait_for_transaction_receipt = AsyncMock(return_value=receipt)

            vaults = asyncio.run(create_vaults({}))

        assert vaults[0]["address"] == "0xOldVault2"
        captured = capsys.readouterr()
        assert "no VaultCreated event found" in captured.out
