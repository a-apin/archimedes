"""Unit coverage for ConfigService — the contract-addresses surface.

Mocks `chain_client` + the AMM router / vault factory contract loaders.
No live RPC, no real chain calls.

Added 2026-05-24 as part of the #147 coverage-gate lift.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from archimedes.chain.client import ChainEndpoint
from archimedes.services.config_service import ConfigService


def _fake_settings() -> SimpleNamespace:
    return SimpleNamespace(
        usdc_address="0xUSDC",
        synthetic_factory_address="0xSF",
        amm_router_address="0xR",
        vault_factory_address="0xVF",
        reasoning_trace_registry_address="0xRTR",
        asset_registry_address="0xAR",
        # representative price_oracle = first value of oracle_addresses (#764)
        oracle_addresses={"sTSLA": "0xORACLE", "sBTC": "0xORACLEB"},
        synth_addresses={"sTSLA": "0xT", "sBTC": "0xB", "sNULL": ""},
        chain_id=5042002,
        arc_rpc_url="https://rpc.example",
        # Two-chain endpoints (#1240), DERIVED from the single-chain values
        # above rather than restated, so this fake cannot describe a split its
        # own chain_id / arc_rpc_url do not support. Resolution and fallback
        # rules are covered against real ChainSettings in
        # backend/tests/chain/test_two_chain_config.py.
        payments_chain=ChainEndpoint(chain_id=5042002, rpc_url="https://rpc.example", explicit=False),
        execution_chain=ChainEndpoint(chain_id=5042002, rpc_url="https://rpc.example", explicit=False),
        is_split_chain=False,
    )


def _loader_with(pools: list[str], vaults: list[str]):
    loader = MagicMock()
    # contract.functions.getAllPools().call() — AsyncMock returns the list
    loader.amm_router.functions.getAllPools.return_value.call = AsyncMock(return_value=pools)
    loader.vault_factory.functions.getVaults.return_value.call = AsyncMock(return_value=vaults)
    return loader


class TestGetContractAddresses:
    @pytest.mark.asyncio
    async def test_returns_all_singleton_addresses(self) -> None:
        with (
            patch("archimedes.services.config_service.chain_client") as cc,
            patch("archimedes.chain.contracts.get_contract_loader") as get_loader,
        ):
            cc.settings = _fake_settings()
            get_loader.return_value = _loader_with([], [])
            resp = await ConfigService().get_contract_addresses()
            assert resp.usdc == "0xUSDC"
            assert resp.amm_router == "0xR"
            assert resp.vault_factory == "0xVF"
            assert resp.reasoning_trace_registry == "0xRTR"
            assert resp.asset_registry == "0xAR"
            assert resp.price_oracle == "0xORACLE"
            assert resp.chain_id == 5042002
            assert resp.rpc_url == "https://rpc.example"

    @pytest.mark.asyncio
    async def test_synthetics_drop_blank_addresses(self) -> None:
        with (
            patch("archimedes.services.config_service.chain_client") as cc,
            patch("archimedes.chain.contracts.get_contract_loader") as get_loader,
        ):
            cc.settings = _fake_settings()
            get_loader.return_value = _loader_with([], [])
            resp = await ConfigService().get_contract_addresses()
            # sNULL has empty address → dropped from response
            assert resp.synthetics == {"sTSLA": "0xT", "sBTC": "0xB"}

    @pytest.mark.asyncio
    async def test_pools_indexed_by_position(self) -> None:
        with (
            patch("archimedes.services.config_service.chain_client") as cc,
            patch("archimedes.chain.contracts.get_contract_loader") as get_loader,
        ):
            cc.settings = _fake_settings()
            get_loader.return_value = _loader_with(["0xP0", "0xP1"], [])
            resp = await ConfigService().get_contract_addresses()
            assert resp.pools == {"pool_0": "0xP0", "pool_1": "0xP1"}

    @pytest.mark.asyncio
    async def test_vaults_indexed_by_position(self) -> None:
        with (
            patch("archimedes.services.config_service.chain_client") as cc,
            patch("archimedes.chain.contracts.get_contract_loader") as get_loader,
        ):
            cc.settings = _fake_settings()
            get_loader.return_value = _loader_with([], ["0xV0", "0xV1", "0xV2"])
            resp = await ConfigService().get_contract_addresses()
            assert resp.vaults == {"vault_0": "0xV0", "vault_1": "0xV1", "vault_2": "0xV2"}

    @pytest.mark.asyncio
    async def test_chain_read_failure_yields_null_not_empty(self) -> None:
        """A failed chain read must render as null (unread), never {} (#1356).

        {} is a real, distinct value: it means the chain was successfully
        read and reports zero pools/vaults. Collapsing "the RPC call raised"
        into the same {} the success path uses for a genuine zero is exactly
        the defect #1356 exists to close — the frontend has no way to tell
        "outage" from "measured zero" and renders a confident 0 either way.
        """
        loader = MagicMock()
        loader.amm_router.functions.getAllPools.return_value.call = AsyncMock(side_effect=RuntimeError("rpc down"))
        loader.vault_factory.functions.getVaults.return_value.call = AsyncMock(side_effect=RuntimeError("rpc down"))
        with (
            patch("archimedes.services.config_service.chain_client") as cc,
            patch("archimedes.chain.contracts.get_contract_loader") as get_loader,
        ):
            cc.settings = _fake_settings()
            get_loader.return_value = loader
            resp = await ConfigService().get_contract_addresses()
            assert resp.pools is None
            assert resp.vaults is None
