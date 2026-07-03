"""Shared AsyncWeb3 client singleton for Arc chain interactions.

Connects to Arc testnet RPC, loads agent account from env vars.
All contract calls route through this client.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from eth_account import Account
from eth_account.signers.local import LocalAccount
from pydantic_settings import BaseSettings
from web3 import AsyncWeb3
from web3.middleware import ExtraDataToPOAMiddleware
from web3.providers import AsyncHTTPProvider


class ChainSettings(BaseSettings):
    """On-chain connection settings — loaded from .env or environment variables.

    **Contract addresses are externalized (roadmap T2.3).** Every address field
    below is read from an environment variable, falling back to the value shown as
    its default when the variable is unset — so nothing breaks if the env is
    unset, while a redeploy can repoint the backend at new contracts without a
    code change. Because ``env_prefix = "ARC_"``, the override variable for a
    field is its name upper-cased with the ``ARC_`` prefix:

    - ``usdc_address``      ← ``ARC_USDC_ADDRESS``
    - ``amm_router_address`` ← ``ARC_AMM_ROUTER_ADDRESS``
    - ``vault_factory_address`` ← ``ARC_VAULT_FACTORY_ADDRESS``
    - ``stsla_address``     ← ``ARC_STSLA_ADDRESS``  (one per synth)
    - ``stsla_oracle_address`` ← ``ARC_STSLA_ORACLE_ADDRESS``  (one per oracle)

    The defaults match the deployed Arc-testnet contracts and the ``ARC_*=...``
    lines emitted by ``backend/archimedes/scripts/deploy_contracts.py``; the full
    set of override variables is documented in ``.env.example``. The public API
    (field names plus the ``synth_addresses`` / ``oracle_addresses`` /
    ``*_account`` properties) is unchanged — only the source-of-truth for each
    address moved from "hardcoded constant" to "env var with hardcoded fallback".
    """

    # RPC
    arc_rpc_url: str = "https://rpc.testnet.arc.network"
    chain_id: int = 5042002  # Arc testnet chain ID (0x4cef52)

    # Agent account (the address that calls rebalance, publishes traces, etc.)
    agent_private_key: str = ""
    agent_address: str = ""  # Will be derived from private key if empty

    # Marketplace subscription readiness thresholds
    min_vault_usdc: float = 10.0          # ARC_MIN_VAULT_USDC
    min_active_action_buffer: int = 20    # ARC_MIN_ACTIVE_ACTION_BUFFER — in action-fees

    # Owner account (for admin operations like oracle price updates)
    owner_private_key: str = ""

    # Contract addresses — env-overridable via ARC_<FIELD>; defaults = deployed
    # Arc testnet contracts (Deploy.s.sol / deploy_contracts.py emits the
    # ARC_*=... lines). Empty defaults mark deployment-specific addresses that
    # must be supplied via .env before that contract can be used.
    usdc_address: str = "0x3600000000000000000000000000000000000000"  # ARC_USDC_ADDRESS
    amm_router_address: str = ""  # ARC_AMM_ROUTER_ADDRESS
    synthetic_factory_address: str = ""  # ARC_SYNTHETIC_FACTORY_ADDRESS
    vault_factory_address: str = ""  # ARC_VAULT_FACTORY_ADDRESS
    reasoning_trace_registry_address: str = ""  # ARC_REASONING_TRACE_REGISTRY_ADDRESS
    asset_registry_address: str = ""  # ARC_ASSET_REGISTRY_ADDRESS
    strategy_registry_address: str = ""  # ARC_STRATEGY_REGISTRY_ADDRESS
    payment_splitter_address: str = ""  # ARC_PAYMENT_SPLITTER_ADDRESS

    # Individual synthetic token addresses — env-overridable via
    # ARC_<SYMBOL>_ADDRESS; defaults = deployed Arc testnet contracts.
    stsla_address: str = "0xd514cd27baf762c650536765cde9b61c876abacd"  # ARC_STSLA_ADDRESS
    snvda_address: str = "0x805e75019a1291a598dfc134ad2519121a35fb11"  # ARC_SNVDA_ADDRESS
    sspy_address: str = "0x6fea38dedea0c6bb66ce93e5383c34385d8b889f"  # ARC_SSPY_ADDRESS
    sbtc_address: str = "0x317e82be8f7cba6c162ab968fcf695d88e8e0359"  # ARC_SBTC_ADDRESS
    sgold_address: str = "0xf384562c8bdafce52400eb6839f195695f6fa276"  # ARC_SGOLD_ADDRESS
    soil_address: str = "0x46cead4120f17a968ba1168f1a56563962cf3c4b"  # ARC_SOIL_ADDRESS
    snky_address: str = "0x445b8f0f827a0d384d1b8ccf18cbc6ec8a543376"  # ARC_SNKY_ADDRESS

    # Individual oracle addresses — env-overridable via
    # ARC_<SYMBOL>_ORACLE_ADDRESS; defaults = deployed Arc testnet contracts.
    stsla_oracle_address: str = "0xe1c9f2b11be97097223a66a188fca541e07873a6"  # ARC_STSLA_ORACLE_ADDRESS
    snvda_oracle_address: str = "0xeb36acf88e739dd312de8278985262146a017374"  # ARC_SNVDA_ORACLE_ADDRESS
    sspy_oracle_address: str = "0xd8161a8eeab7c7100e2863abe3d5f346b5ff9e52"  # ARC_SSPY_ORACLE_ADDRESS
    sbtc_oracle_address: str = "0x6cc5f621c4e3b46152e69e5c9873689cbb4a85e8"  # ARC_SBTC_ORACLE_ADDRESS
    sgold_oracle_address: str = "0x35fccde01ae8728c7a7cb83c3f59c701ebecc633"  # ARC_SGOLD_ORACLE_ADDRESS
    soil_oracle_address: str = "0x79f354524fd09af16d841a2221af2b2b7bc432c8"  # ARC_SOIL_ORACLE_ADDRESS
    snky_oracle_address: str = "0xcd34a4103ad64a3cf729b1b1a58295ccc957fcee"  # ARC_SNKY_ORACLE_ADDRESS

    # Paths
    abi_dir: str = str(Path(__file__).resolve().parents[3] / "contracts" / "abis")

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
