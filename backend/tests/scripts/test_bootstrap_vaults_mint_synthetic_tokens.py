"""Tests for mint_synthetic_tokens()'s tokenVault SSOT lookup (#1102 follow-up).

The reviewer finding these tests close: PR #1152's #1102 fix (live
SyntheticFactory.tokenVault() lookup) was cited as tested via two files that
never actually exercise this code path — test_create_vault_abi_consistency.py
targets #652 (VaultFactory ABI drift) and test_bootstrap_vaults_create_vaults.py
targets #655 (create_vaults revert handling). Neither mocks
get_contract_loader().synthetic_factory.functions.tokenVault.

Hermetic: circle_signer, chain_client, and get_contract_loader are mocked. No
network, no Arc RPC, no Circle. Per CLAUDE.md's rule #3, each assertion below
was checked against the pre-fix code (bare hardcoded legacy fallback, no
BOOTSTRAP_ALLOW_LEGACY_VAULTS gate) and confirmed to fail there.
"""

from __future__ import annotations

import asyncio
import os
from unittest.mock import AsyncMock, MagicMock, patch

from archimedes.scripts.bootstrap_vaults import mint_synthetic_tokens

_SENTINEL_VAULT = "0x00000000000000000000000000000000000BEEF"
_ZERO_ADDR = "0x0000000000000000000000000000000000000000"
_LEGACY_STSLA_VAULT = "0xf0356600e26c6c403ec4f5b36b0e3380bb0609ab"


def _base_mocks(*, wallet="0xWallet", synth_addresses=None):
    """Shared settings/env scaffolding for mint_synthetic_tokens() tests."""
    synth_addresses = synth_addresses if synth_addresses is not None else {"sTSLA": "0xTslaToken"}
    mock_cc = MagicMock()
    mock_cc.settings.usdc_address = "0xUSDC"
    mock_cc.settings.synth_addresses = synth_addresses
    mock_cc.to_checksum = lambda a: a
    return mock_cc


class TestTokenVaultLookupSuccess:
    """A live tokenVault() address must be what actually gets broadcast."""

    def test_lookup_success_reaches_execute_contract(self):
        """tokenVault() returns a live address → that exact address is used
        for both the approve() and mint() calls, not the legacy dict."""
        mock_cc = _base_mocks()
        factory = MagicMock()
        factory.functions.tokenVault.return_value.call = AsyncMock(return_value=_SENTINEL_VAULT)
        loader = MagicMock()
        loader.synthetic_factory = factory
        loader.token.return_value.functions.balanceOf.return_value.call = AsyncMock(return_value=0)

        with (
            patch("archimedes.scripts.bootstrap_vaults.MINT_BUDGET", {"sTSLA": 30}),
            patch("archimedes.scripts.bootstrap_vaults.get_contract_loader", return_value=loader),
            patch("archimedes.scripts.bootstrap_vaults.chain_client", mock_cc),
            patch("archimedes.scripts.bootstrap_vaults.circle_signer") as mock_signer,
            patch.dict(os.environ, {"WALLET_ADDRESS": "0xWallet"}, clear=False),
        ):
            mock_signer.execute_contract = AsyncMock(return_value="0xtxhash")

            asyncio.run(mint_synthetic_tokens())

        calls = mock_signer.execute_contract.await_args_list
        approve_call = next(c for c in calls if c.kwargs["abi_function"] == "approve(address,uint256)")
        mint_call = next(c for c in calls if c.kwargs["abi_function"] == "mint(uint256)")
        assert approve_call.kwargs["abi_params"][0] == _SENTINEL_VAULT
        assert mint_call.kwargs["contract_address"] == _SENTINEL_VAULT


class TestTokenVaultLookupFailureNoLegacyFallback:
    """Lookup failure (exception or zero address) must skip, not substitute
    a hardcoded legacy address, unless explicitly opted in."""

    def test_lookup_exception_skips_symbol_without_legacy_address(self, capsys):
        """tokenVault() raises → symbol is skipped entirely: no approve/mint
        broadcast at all, and specifically never with a legacy address."""
        mock_cc = _base_mocks()
        factory = MagicMock()
        factory.functions.tokenVault.return_value.call = AsyncMock(side_effect=RuntimeError("RPC down"))
        loader = MagicMock()
        loader.synthetic_factory = factory

        with (
            patch("archimedes.scripts.bootstrap_vaults.MINT_BUDGET", {"sTSLA": 30}),
            patch("archimedes.scripts.bootstrap_vaults.get_contract_loader", return_value=loader),
            patch("archimedes.scripts.bootstrap_vaults.chain_client", mock_cc),
            patch("archimedes.scripts.bootstrap_vaults.circle_signer") as mock_signer,
            patch.dict(os.environ, {"WALLET_ADDRESS": "0xWallet"}, clear=False),
            patch.dict(os.environ, {}, clear=False),
        ):
            os.environ.pop("BOOTSTRAP_ALLOW_LEGACY_VAULTS", None)
            mock_signer.execute_contract = AsyncMock(return_value="0xtxhash")

            minted = asyncio.run(mint_synthetic_tokens())

        mock_signer.execute_contract.assert_not_awaited()
        assert minted["sTSLA"] == 0.0
        captured = capsys.readouterr()
        assert "sTSLA" in captured.out
        assert "skipping" in captured.out

    def test_zero_address_skips_symbol_without_legacy_address(self):
        """tokenVault() returns the zero address (never-created vault) →
        same skip path as an exception, no legacy substitution."""
        mock_cc = _base_mocks()
        factory = MagicMock()
        factory.functions.tokenVault.return_value.call = AsyncMock(return_value=_ZERO_ADDR)
        loader = MagicMock()
        loader.synthetic_factory = factory

        with (
            patch("archimedes.scripts.bootstrap_vaults.MINT_BUDGET", {"sTSLA": 30}),
            patch("archimedes.scripts.bootstrap_vaults.get_contract_loader", return_value=loader),
            patch("archimedes.scripts.bootstrap_vaults.chain_client", mock_cc),
            patch("archimedes.scripts.bootstrap_vaults.circle_signer") as mock_signer,
            patch.dict(os.environ, {"WALLET_ADDRESS": "0xWallet"}, clear=False),
        ):
            os.environ.pop("BOOTSTRAP_ALLOW_LEGACY_VAULTS", None)
            mock_signer.execute_contract = AsyncMock(return_value="0xtxhash")

            minted = asyncio.run(mint_synthetic_tokens())

        mock_signer.execute_contract.assert_not_awaited()
        assert minted["sTSLA"] == 0.0

    def test_swallowed_exception_is_logged(self, caplog):
        """The tokenVault() lookup exception must be logged, not silently
        swallowed (this repo's #1 rule: no silent fail-soft on a load-bearing
        lookup)."""
        import logging

        mock_cc = _base_mocks()
        factory = MagicMock()
        factory.functions.tokenVault.return_value.call = AsyncMock(side_effect=RuntimeError("RPC down"))
        loader = MagicMock()
        loader.synthetic_factory = factory

        with (
            patch("archimedes.scripts.bootstrap_vaults.MINT_BUDGET", {"sTSLA": 30}),
            patch("archimedes.scripts.bootstrap_vaults.get_contract_loader", return_value=loader),
            patch("archimedes.scripts.bootstrap_vaults.chain_client", mock_cc),
            patch("archimedes.scripts.bootstrap_vaults.circle_signer") as mock_signer,
            patch.dict(os.environ, {"WALLET_ADDRESS": "0xWallet"}, clear=False),
            caplog.at_level(logging.WARNING, logger="archimedes.scripts.bootstrap_vaults"),
        ):
            os.environ.pop("BOOTSTRAP_ALLOW_LEGACY_VAULTS", None)
            mock_signer.execute_contract = AsyncMock(return_value="0xtxhash")

            asyncio.run(mint_synthetic_tokens())

        assert any("tokenVault lookup failed" in r.message for r in caplog.records)


class TestLookupFailureWithExistingBalance:
    """Reviewer-specified regression (PR #1377 round 2): a failed/zero
    tokenVault() lookup must not stomp a real existing balance to 0.0 on a
    rerun. The existing-balance check needs only token_addr + wallet (never
    vault_addr), so it must run BEFORE the hard-skip-on-unresolved-vault_addr
    decision — otherwise a rerun where the wallet already holds tokens from
    a prior successful run, hitting one transient tokenVault() RPC failure,
    records minted[symbol]=0.0 despite a real on-chain balance, and
    fund_and_allocate_vaults() then silently zero-funds that symbol into
    every new vault."""

    def test_lookup_exception_with_existing_balance_uses_real_balance(self):
        """tokenVault() raises AND balanceOf shows a real existing balance
        (from a prior successful run) → minted[symbol] equals the existing
        balance, not 0.0, and no mint is broadcast."""
        mock_cc = _base_mocks()
        factory = MagicMock()
        factory.functions.tokenVault.return_value.call = AsyncMock(side_effect=RuntimeError("RPC down"))
        loader = MagicMock()
        loader.synthetic_factory = factory
        # Wallet already holds 12.5 sTSLA from a prior successful run.
        loader.token.return_value.functions.balanceOf.return_value.call = AsyncMock(return_value=int(12.5 * 1e18))

        with (
            patch("archimedes.scripts.bootstrap_vaults.MINT_BUDGET", {"sTSLA": 30}),
            patch("archimedes.scripts.bootstrap_vaults.get_contract_loader", return_value=loader),
            patch("archimedes.scripts.bootstrap_vaults.chain_client", mock_cc),
            patch("archimedes.scripts.bootstrap_vaults.circle_signer") as mock_signer,
            patch.dict(os.environ, {"WALLET_ADDRESS": "0xWallet"}, clear=False),
        ):
            os.environ.pop("BOOTSTRAP_ALLOW_LEGACY_VAULTS", None)
            mock_signer.execute_contract = AsyncMock(return_value="0xtxhash")

            minted = asyncio.run(mint_synthetic_tokens())

        mock_signer.execute_contract.assert_not_awaited()
        assert minted["sTSLA"] == 12.5


class TestLegacyFallbackRequiresExplicitOptIn:
    """The legacy address dict must never engage without BOOTSTRAP_ALLOW_LEGACY_VAULTS=1."""

    def test_opt_in_env_var_enables_legacy_fallback(self):
        """With the explicit opt-in set, a zero-address lookup falls back to
        the known legacy sTSLA vault address — proving the gate, not the
        address, is what changed."""
        mock_cc = _base_mocks()
        factory = MagicMock()
        factory.functions.tokenVault.return_value.call = AsyncMock(return_value=_ZERO_ADDR)
        loader = MagicMock()
        loader.synthetic_factory = factory
        loader.token.return_value.functions.balanceOf.return_value.call = AsyncMock(return_value=0)

        with (
            patch("archimedes.scripts.bootstrap_vaults.MINT_BUDGET", {"sTSLA": 30}),
            patch("archimedes.scripts.bootstrap_vaults.get_contract_loader", return_value=loader),
            patch("archimedes.scripts.bootstrap_vaults.chain_client", mock_cc),
            patch("archimedes.scripts.bootstrap_vaults.circle_signer") as mock_signer,
            patch.dict(
                os.environ,
                {"WALLET_ADDRESS": "0xWallet", "BOOTSTRAP_ALLOW_LEGACY_VAULTS": "1"},
                clear=False,
            ),
        ):
            mock_signer.execute_contract = AsyncMock(return_value="0xtxhash")

            asyncio.run(mint_synthetic_tokens())

        calls = mock_signer.execute_contract.await_args_list
        approve_call = next(c for c in calls if c.kwargs["abi_function"] == "approve(address,uint256)")
        assert approve_call.kwargs["abi_params"][0] == _LEGACY_STSLA_VAULT
