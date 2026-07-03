"""Config service — serves deployed contract addresses to the frontend."""

from __future__ import annotations

import logging

from archimedes.api.schemas import ContractAddressesResponse
from archimedes.chain.client import chain_client

logger = logging.getLogger(__name__)


class ConfigService:
    """Serves configuration data to the API layer."""

    async def get_contract_addresses(self) -> ContractAddressesResponse:
        from archimedes.chain.contracts import get_contract_loader

        settings = chain_client.settings
        loader = get_contract_loader()

        # Get pools from AMM router
        pools: dict[str, str] = {}
        try:
            pool_addresses = await loader.amm_router.functions.getAllPools().call()
            for i, addr in enumerate(pool_addresses):
                pools[f"pool_{i}"] = addr
        except Exception:
            logger.debug("amm pool enumeration failed", exc_info=True)

        # Get vaults from factory
        vaults: dict[str, str] = {}
        try:
            vault_addresses = await loader.vault_factory.functions.getVaults().call()
            for i, addr in enumerate(vault_addresses):
                vaults[f"vault_{i}"] = addr
        except Exception:
            logger.debug("vault enumeration failed", exc_info=True)

        return ContractAddressesResponse(
            usdc=settings.usdc_address,
            synthetic_factory=settings.synthetic_factory_address,
            amm_router=settings.amm_router_address,
            vault_factory=settings.vault_factory_address,
            reasoning_trace_registry=settings.reasoning_trace_registry_address,
            asset_registry=settings.asset_registry_address,
            # Representative oracle: the first NON-EMPTY address. oracle_addresses
            # already excludes empties, but skip them here too so an unfiltered
            # mapping can never yield "" while a valid oracle exists later. (Copilot #765)
            price_oracle=next((v for v in settings.oracle_addresses.values() if v), ""),
            synthetics={k: v for k, v in settings.synth_addresses.items() if v},
            pools=pools,
            vaults=vaults,
            chain_id=settings.chain_id,
            rpc_url=settings.arc_rpc_url,
        )
