"""Shared AsyncWeb3 client singleton for Arc chain interactions.

Connects to Arc testnet RPC, loads agent account from env vars.
All contract calls route through this client.
"""

from __future__ import annotations

import json
import logging
import os
from functools import lru_cache
from pathlib import Path

from aiohttp import ClientTimeout
from eth_account import Account
from eth_account.signers.local import LocalAccount
from pydantic_settings import BaseSettings
from web3 import AsyncWeb3
from web3.middleware import ExtraDataToPOAMiddleware
from web3.providers import AsyncHTTPProvider

logger = logging.getLogger(__name__)

# Deployed synth token + oracle addresses for the FULL on-chain universe (all 281),
# loaded from the committed deploy-address SSOT (data/synthetic_addresses.json,
# regenerated per contract redeploy by scripts/gen_synthetic_addresses.py from the
# Foundry deploy manifest). Replaces the old hand-maintained 2-entry map so EVERY
# synth in ON_CHAIN_SYNTHS resolves — the whole 281 become priced + tradable, not just
# a demo handful. Per-synth ARC_<SYMBOL>_ADDRESS / _ORACLE_ADDRESS env vars still
# override at runtime (see _resolve_ssot_addresses). Address metadata lives here
# (per-deploy lifecycle); symbol metadata lives in synthetic_universe.json.
_ADDRESS_SSOT_PATH = Path(__file__).resolve().parent.parent / "data" / "synthetic_addresses.json"


def _load_address_defaults() -> tuple[dict[str, str], dict[str, str]]:
    """Load ``{symbol: token}`` and ``{symbol: oracle}`` from the deploy-address SSOT.

    Fail-safe: on any read/parse error, returns empty maps + logs an error (the app
    boots with env-only resolution rather than crashing) — mirrors universe.py's loader.
    """
    try:
        raw = json.loads(_ADDRESS_SSOT_PATH.read_text())
        tokens = {s: v["token"] for s, v in raw.items() if isinstance(v, dict) and v.get("token")}
        oracles = {s: v["oracle"] for s, v in raw.items() if isinstance(v, dict) and v.get("oracle")}
        return tokens, oracles
    except Exception as exc:  # Never let an address-file issue crash import.
        logger.error("client: could not load %s (%s) — synth addresses resolve from env only", _ADDRESS_SSOT_PATH, exc)
        return {}, {}


_SYNTH_DEFAULTS, _ORACLE_DEFAULTS = _load_address_defaults()


def _resolve_ssot_addresses(defaults: dict[str, str], suffix: str) -> dict[str, str]:
    """Build ``{SSOT symbol: address}`` over the deploy-eligible universe (#764).

    For each symbol in ``universe.ON_CHAIN_SYNTHS``, resolve ``ARC_<SYMBOL>_<suffix>``
    else the committed transitional default. Symbols with no address — most of the SSOT
    until the T3.2 redeploy mints them — are EXCLUDED, so consumers only ever see DEPLOYED
    synths with non-empty addresses (the invariant the previous 7-field map upheld).
    Compliance-held single stocks (sTSLA/sNVDA) are not in the SSOT, so they no longer
    appear on the live path.

    **Resolution source (important):** overrides are read from ``os.environ`` via
    ``os.getenv`` — NOT through pydantic-settings' ``env_file`` source. Unlike the declared
    ``ChainSettings`` fields (which pydantic resolves from ``.env`` directly), these
    per-synth keys are only seen when the variables are present in the *process
    environment*. Every real entrypoint satisfies this: the FastAPI app via
    ``main.load_dotenv`` (it loads ``.env`` into ``os.environ`` at import); the
    ``oracle`` / ``agent_runner`` processes via docker-compose's ``env_file: .env``
    (which injects ``.env`` into the container environment); AND a *bare, non-docker*
    ``python -m archimedes.chain.{oracle,agent}_runner`` run, because those modules
    now call ``load_dotenv`` in their ``__main__`` block (mirroring ``main.py``;
    ``override=False`` so an exported env / docker env_file still wins). Tests use
    ``monkeypatch.setenv``. So the only way to miss the per-synth overrides is to
    embed ``ChainSettings`` in a *new* custom entrypoint that neither loads ``.env``
    nor exports the vars — in which case add a ``load_dotenv`` there too.
    """
    from archimedes.universe import ON_CHAIN_SYNTHS

    resolved: dict[str, str] = {}
    for symbol in ON_CHAIN_SYNTHS:
        addr = os.getenv(f"ARC_{symbol.upper()}_{suffix}", "") or defaults.get(symbol, "")
        if addr:
            resolved[symbol] = addr
    return resolved


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

    Per-synth token + oracle addresses are NOT individual fields — they are resolved
    SSOT-driven over ``universe.ON_CHAIN_SYNTHS`` in the ``synth_addresses`` /
    ``oracle_addresses`` properties (``ARC_<SYMBOL>_ADDRESS`` /
    ``ARC_<SYMBOL>_ORACLE_ADDRESS`` env, else a committed transitional default;
    undeployed synths excluded). See #764.

    The defaults match the deployed Arc-testnet contracts and the ``ARC_*=...`` lines
    emitted by ``backend/archimedes/scripts/deploy_contracts.py``; the full set of
    override variables is documented in ``.env.example``.
    """

    # RPC
    arc_rpc_url: str = "https://rpc.testnet.arc.network"
    chain_id: int = 5042002  # Arc testnet chain ID (0x4cef52)
    # Connect+read timeout for the AsyncHTTPProvider (N2 — dead-egress detection).
    # A blackholed NAT (e.g. a NAT instance down, see infra/cloudwatch.tf's
    # per-NAT StatusCheckFailed alarm) otherwise hangs the RPC call with no
    # timeout at all, which can exceed /health's own health-check budget (the
    # ECS task healthCheck timeout is 5s — infra/ecs.tf) and gets a perfectly
    # healthy app process killed for an RPC-layer problem. 3s leaves headroom
    # inside that 5s budget for /health's other work (DB, corpus, LLM probes).
    # ARC_RPC_TIMEOUT_SECONDS overrides.
    rpc_timeout_seconds: float = 3.0

    # Agent account (the address that calls rebalance, publishes traces, etc.)
    agent_private_key: str = ""
    agent_address: str = ""  # Will be derived from private key if empty

    # Marketplace subscription readiness thresholds
    min_vault_usdc: float = 10.0  # ARC_MIN_VAULT_USDC
    min_active_action_buffer: int = 20  # ARC_MIN_ACTIVE_ACTION_BUFFER — in action-fees

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

    # NOTE: per-synth token + oracle addresses are NO LONGER individual fields. They
    # are resolved SSOT-driven over universe.ON_CHAIN_SYNTHS in synth_addresses /
    # oracle_addresses below — each from ARC_<SYMBOL>_ADDRESS / ARC_<SYMBOL>_ORACLE_ADDRESS
    # (env) else a committed transitional default (_SYNTH_DEFAULTS / _ORACLE_DEFAULTS),
    # with undeployed synths excluded. This keeps the map keyed to the SSOT (no
    # sTSLA/sNVDA drift) and can't go stale as the universe grows. (#764)

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
        """Deployed synth token addresses keyed by SSOT symbol (#764).

        SSOT-driven over ``universe.ON_CHAIN_SYNTHS``; each resolved from
        ``ARC_<SYMBOL>_ADDRESS`` (env, loaded from ``.env`` by ``main.load_dotenv``) else a
        committed transitional default. Undeployed synths (most of the SSOT until the T3.2
        redeploy) are EXCLUDED, so consumers only ever see deployed synths with non-empty
        addresses. Compliance-held single stocks (sTSLA/sNVDA) are not in the SSOT and no
        longer appear.
        """
        return _resolve_ssot_addresses(_SYNTH_DEFAULTS, "ADDRESS")

    @property
    def oracle_addresses(self) -> dict[str, str]:
        """Deployed price-oracle addresses keyed by SSOT symbol — see ``synth_addresses`` (#764)."""
        return _resolve_ssot_addresses(_ORACLE_DEFAULTS, "ORACLE_ADDRESS")


class ChainClient:
    """Singleton Web3 client for all on-chain interactions."""

    def __init__(self, settings: ChainSettings | None = None):
        self.settings = settings or ChainSettings()
        # request_kwargs={"timeout": ClientTimeout(...)} — NOT a bare number:
        # aiohttp's ClientSession._request coerces a bare number fine on the
        # request itself, but web3's HTTPSessionManager.async_cache_and_return_session
        # separately accesses `request_timeout.total` on its own session-eviction
        # path, which requires an actual ClientTimeout instance (see N2).
        self.w3 = AsyncWeb3(
            AsyncHTTPProvider(
                self.settings.arc_rpc_url,
                request_kwargs={"timeout": ClientTimeout(total=self.settings.rpc_timeout_seconds)},
            )
        )

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
