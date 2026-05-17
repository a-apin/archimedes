"""Shared AsyncWeb3 client singleton for Arc chain interactions.

Connects to Arc testnet RPC, loads agent account from env vars.
All contract calls route through this client.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from eth_account import Account
from eth_account.signers.local import LocalAccount
from pydantic_settings import BaseSettings
from web3 import AsyncWeb3
from web3.middleware import ExtraDataToPOAMiddleware
from web3.providers import AsyncHTTPProvider


class ChainSettings(BaseSettings):
    """On-chain connection settings — loaded from .env or environment variables."""

    # RPC
    arc_rpc_url: str = "https://rpc.testnet.arc.network"
    chain_id: int =  5042002  # Arc testnet chain ID (0x4cef52)

    # Agent account (the address that calls rebalance, publishes traces, etc.)
    agent_private_key: str = ""
    agent_address: str = ""  # Will be derived from private key if empty

    # Owner account (for admin operations like oracle price updates)
    owner_private_key: str = ""

    # Contract addresses — set these after deploying via Deploy.s.sol
    usdc_address: str = "0x3600000000000000000000000000000000000000"
    amm_router_address: str = ""
    synthetic_factory_address: str = ""
    vault_factory_address: str = ""
    reasoning_trace_registry_address: str = ""
    asset_registry_address: str = ""

    # Individual synthetic token addresses (defaults = deployed Arc testnet contracts)
    stsla_address: str = "0xd514cd27baf762c650536765cde9b61c876abacd"
    snvda_address: str = "0x805e75019a1291a598dfc134ad2519121a35fb11"
    sspy_address: str = "0x6fea38dedea0c6bb66ce93e5383c34385d8b889f"
    sbtc_address: str = "0x317e82be8f7cba6c162ab968fcf695d88e8e0359"
    sgold_address: str = "0xf384562c8bdafce52400eb6839f195695f6fa276"
    soil_address: str = "0x46cead4120f17a968ba1168f1a56563962cf3c4b"
    snky_address: str = "0x445b8f0f827a0d384d1b8ccf18cbc6ec8a543376"

    # Individual oracle addresses (defaults = deployed Arc testnet contracts)
    stsla_oracle_address: str = "0xe1c9f2b11be97097223a66a188fca541e07873a6"
    snvda_oracle_address: str = "0xeb36acf88e739dd312de8278985262146a017374"
    sspy_oracle_address: str = "0xd8161a8eeab7c7100e2863abe3d5f346b5ff9e52"
    sbtc_oracle_address: str = "0x6cc5f621c4e3b46152e69e5c9873689cbb4a85e8"
    sgold_oracle_address: str = "0x35fccde01ae8728c7a7cb83c3f59c701ebecc633"
    soil_oracle_address: str = "0x79f354524fd09af16d841a2221af2b2b7bc432c8"
    snky_oracle_address: str = "0xcd34a4103ad64a3cf729b1b1a58295ccc957fcee"

    # Paths
    abi_dir: str = str(
        Path(__file__).resolve().parents[3] / "contracts" / "abis"
    )

    model_config = {"env_prefix": "ARC_", "env_file": ".env", "extra": "ignore"}

    @property
    def agent_account(self) -> LocalAccount | None:
        if not self.agent_private_key:
            return None
        return Account.from_key(self.agent_private_key)

    @property
    def owner_account(self) -> LocalAccount | None:
        if not self.owner_private_key:
            return None
        return Account.from_key(self.owner_private_key)

    @property
    def synth_addresses(self) -> dict[str, str]:
        return {
            "sTSLA": self.stsla_address,
            "sNVDA": self.snvda_address,
            "sSPY": self.sspy_address,
            "sBTC": self.sbtc_address,
            "sGOLD": self.sgold_address,
            "sOIL": self.soil_address,
            "sNKY": self.snky_address,
        }

    @property
    def oracle_addresses(self) -> dict[str, str]:
        return {
            "sTSLA": self.stsla_oracle_address,
            "sNVDA": self.snvda_oracle_address,
            "sSPY": self.sspy_oracle_address,
            "sBTC": self.sbtc_oracle_address,
            "sGOLD": self.sgold_oracle_address,
            "sOIL": self.soil_oracle_address,
            "sNKY": self.snky_oracle_address,
        }


class ChainClient:
    """Singleton Web3 client for all on-chain interactions."""

    def __init__(self, settings: ChainSettings | None = None):
        self.settings = settings or ChainSettings()
        self.w3 = AsyncWeb3(AsyncHTTPProvider(self.settings.arc_rpc_url))

        # Arc uses POA consensus — add the middleware
        self.w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)

    async def is_connected(self) -> bool:
        try:
            return await self.w3.is_connected()
        except Exception:
            return False

    async def get_chain_id(self) -> int:
        return await self.w3.eth.chain_id

    async def get_block_number(self) -> int:
        return await self.w3.eth.block_number

    async def get_native_balance(self, address: str) -> int:
        return await self.w3.eth.get_balance(address)

    def to_checksum(self, address: str) -> str:
        return self.w3.to_checksum_address(address)

    def to_wei(self, value: float, unit: str = "ether") -> int:
        return self.w3.to_wei(value, unit)

    def from_wei(self, value: int, unit: str = "ether") -> float:
        return self.w3.from_wei(value, unit)


@lru_cache(maxsize=1)
def get_chain_client() -> ChainClient:
    """Get or create the singleton chain client."""
    return ChainClient()


# Module-level convenience
chain_client = get_chain_client()
