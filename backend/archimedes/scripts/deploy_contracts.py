"""Deploy updated contracts to Arc testnet via Circle wallet.

Deploys new instances of all core contracts with the updated Vault.sol
(which has multi-asset NAV accounting via oracles).

After deployment:
  - Updates .env with new contract addresses
  - Prints the addresses for updating ui/src/config.js

Usage:
  cd backend && uv run python -m archimedes.scripts.deploy_contracts
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv("../.env", override=True)
load_dotenv(".env", override=False)

from eth_utils import to_checksum_address
from web3 import AsyncHTTPProvider, AsyncWeb3
from web3.middleware import ExtraDataToPOAMiddleware

from archimedes.chain.circle_signer import circle_signer

# ─── Config ──────────────────────────────────────────────────

WALLET = os.getenv("WALLET_ADDRESS", "")
USDC_ARC = "0x3600000000000000000000000000000000000000"

SYNTHETICS = [
    ("Synthetic TSLA", "sTSLA", 285_500_000),  # $285.50
    ("Synthetic NVDA", "sNVDA", 135_200_000),  # $135.20
    ("Synthetic SPY", "sSPY", 592_400_000),  # $592.40
    ("Synthetic BTC", "sBTC", 104_500_000_000),  # $104,500
    ("Synthetic GOLD", "sGOLD", 3_250_000_000),  # $3,250.00
    ("Synthetic OIL", "sOIL", 62_800_000),  # $62.80
    ("Synthetic NKY", "sNKY", 38_500_000_000),  # $38,500.00
]

ABI_DIR = Path(__file__).resolve().parents[3] / "contracts" / "abis"
ARTIFACTS_DIR = Path(__file__).resolve().parents[3] / "contracts" / "out"

w3 = AsyncWeb3(AsyncHTTPProvider(os.getenv("ARC_ARC_RPC_URL", "https://rpc.testnet.arc.network")))
w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)


def load_bytecode(contract_name: str) -> str:
    """Load creation bytecode from Foundry build artifacts."""
    # Try both <Contract>.sol/<Contract>.json and direct lookup
    for pattern in [f"{contract_name}.sol/{contract_name}.json", f"{contract_name}.json"]:
        path = ARTIFACTS_DIR / pattern
        if path.exists():
            with open(path) as f:
                data = json.load(f)
            return data["bytecode"]["object"]
    raise FileNotFoundError(f"No bytecode found for {contract_name} in {ARTIFACTS_DIR}")


def load_abi(contract_name: str) -> list:
    """Load ABI from contracts/abis/."""
    abi_path = ABI_DIR / f"{contract_name}.json"
    if abi_path.exists():
        with open(abi_path) as f:
            return json.load(f)
    raise FileNotFoundError(f"No ABI found for {contract_name} in {ABI_DIR}")


async def get_nonce() -> int:
    return await w3.eth.get_transaction_count(to_checksum_address(WALLET))


async def deploy_contract(bytecode_hex: str, gas: int = 5_000_000) -> str:
    """Deploy a contract via Circle sign_and_broadcast.

    Builds a raw deployment tx (to=0x0, data=bytecode), gets Circle to sign it,
    then broadcasts to Arc.

    Returns the deployed contract address.
    """
    nonce = await get_nonce()
    gas_price = await w3.eth.gas_price

    tx = {
        "nonce": hex(nonce),
        "gasPrice": hex(gas_price),
        "gas": hex(gas),
        "value": "0x0",
        "data": f"0x{bytecode_hex}" if not bytecode_hex.startswith("0x") else bytecode_hex,
        "chainId": hex(5042002),
        "to": "0x",  # contract creation
    }

    print(f"  ⏳ Submitting deploy tx (nonce={nonce}, gas={gas})...")

    # Use Circle's contract execution with empty address for deployment
    # Actually, Circle doesn't support deployment natively. Use raw tx approach.
    # We need to sign the raw tx via Circle and broadcast via RPC.

    # Circle's sign endpoint needs a different approach for deployment
    # Let's use the low-level sign_and_broadcast
    tx_hash = await circle_signer.sign_and_broadcast(tx)
    print(f"  ✅ Tx submitted: {tx_hash}")

    # Wait for receipt
    receipt = await w3.eth.wait_for_transaction_receipt(w3.to_bytes(hexstr=tx_hash.removeprefix("0x")))

    if receipt.status != 1:
        raise RuntimeError(f"Deploy tx failed: {receipt}")

    contract_address = receipt.contractAddress
    if not contract_address:
        raise RuntimeError("No contract address in receipt")

    print(f"  📍 Deployed to: {contract_address}")
    return contract_address


async def call_contract(contract_address: str, abi: list, function: str, args: list) -> str:
    """Call a write function on a deployed contract via Circle signer."""
    # Build the ABI function signature
    fn = None
    for item in abi:
        if item.get("type") == "function" and item.get("name") == function:
            inputs = item.get("inputs", [])
            input_types = ",".join(i["type"] for i in inputs)
            fn = f"{function}({input_types})"
            break

    if not fn:
        raise ValueError(f"Function {function} not found in ABI")

    # Convert args for Circle API
    circle_params = []
    for arg in args:
        if isinstance(arg, (list, bool, int)):
            circle_params.append(arg)
        else:
            circle_params.append(arg)

    tx_hash = await circle_signer.execute_contract(
        contract_address=contract_address,
        abi_function=fn,
        abi_params=circle_params,
    )
    return tx_hash


async def main():
    print("🏗️  Archimedes Contract Deployer (via Circle Wallet)")
    print("=" * 60)

    if not circle_signer.is_configured:
        print("❌ Circle wallet not configured")
        sys.exit(1)

    if not WALLET:
        print("❌ WALLET_ADDRESS not set")
        sys.exit(1)

    print(f"Wallet: {WALLET}")
    print("Chain:  Arc testnet (5042002)")
    print()

    deployed = {}

    # ═══════════════════════════════════════════════════════════════
    # Phase 1: Core Infrastructure
    # ═══════════════════════════════════════════════════════════════
    print("═══ Phase 1: Core Infrastructure ═══")

    print("\n📦 AMMRouter...")
    bc = load_bytecode("AMMRouter")
    # Constructor: (address _owner)
    # AMMRouter(address)
    from eth_abi import encode

    ctor_args = encode(["address"], [to_checksum_address(WALLET)])
    full_bc = bc + ctor_args.hex()
    deployed["ammRouter"] = await deploy_contract(full_bc)

    print("\n📦 SyntheticFactory...")
    bc = load_bytecode("SyntheticFactory")
    # SyntheticFactory(address _usdc, address _owner)
    ctor_args = encode(["address", "address"], [USDC_ARC, to_checksum_address(WALLET)])
    full_bc = bc + ctor_args.hex()
    deployed["syntheticFactory"] = await deploy_contract(full_bc)

    print("\n📦 ReasoningTraceRegistry...")
    bc = load_bytecode("ReasoningTraceRegistry")
    ctor_args = encode(["address"], [to_checksum_address(WALLET)])
    full_bc = bc + ctor_args.hex()
    deployed["reasoningTraceRegistry"] = await deploy_contract(full_bc)

    print("\n📦 AssetRegistry...")
    bc = load_bytecode("AssetRegistry")
    ctor_args = encode(["address"], [to_checksum_address(WALLET)])
    full_bc = bc + ctor_args.hex()
    deployed["assetRegistry"] = await deploy_contract(full_bc)

    print("\n📦 VaultFactory...")
    bc = load_bytecode("VaultFactory")
    # VaultFactory(address _agentAddress, address _ammRouter, address _usdc,
    #              address _platformFeeRecipient, address _owner)
    ctor_args = encode(
        ["address", "address", "address", "address", "address"],
        [
            to_checksum_address(WALLET),  # agent
            to_checksum_address(deployed["ammRouter"]),
            USDC_ARC,
            to_checksum_address(WALLET),  # platform fee recipient
            to_checksum_address(WALLET),  # owner
        ],
    )
    full_bc = bc + ctor_args.hex()
    deployed["vaultFactory"] = await deploy_contract(full_bc)

    # ═══════════════════════════════════════════════════════════════
    # Phase 2: Synthetic Assets (oracles + tokens + vaults)
    # ═══════════════════════════════════════════════════════════════
    print("\n═══ Phase 2: Synthetic Assets ═══")

    synth_tokens = []
    synth_oracles = []

    for name, symbol, price in SYNTHETICS:
        print(f"\n📦 {symbol} Oracle...")
        bc = load_bytecode("PriceOracle")
        # PriceOracle(string _symbol, uint256 _initialPrice, address _owner)
        ctor_args = encode(
            ["string", "uint256", "address"],
            [symbol, price, to_checksum_address(WALLET)],
        )
        full_bc = bc + ctor_args.hex()
        oracle_addr = await deploy_contract(full_bc)
        synth_oracles.append(oracle_addr)

        print(f"  🔧 {symbol}: Creating synthetic via factory...")
        # Call syntheticFactory.createSynthetic(name, symbol, oracle)
        tx = await call_contract(
            deployed["syntheticFactory"],
            load_abi("SyntheticFactory"),
            "createSynthetic",
            [name, symbol, oracle_addr],
        )
        print(f"  ✅ {symbol} synthetic created: {tx[:16]}...")

        # Find the token address - read from factory
        factory_abi = load_abi("SyntheticFactory")
        factory = w3.eth.contract(
            address=to_checksum_address(deployed["syntheticFactory"]),
            abi=factory_abi,
        )
        synth_list = await factory.functions.getSynthetics().call()
        token_addr = synth_list[-1]  # last created
        synth_tokens.append(token_addr)
        print(f"  📍 {symbol} token: {token_addr}")

    # ═══════════════════════════════════════════════════════════════
    # Phase 3: AMM Pools
    # ═══════════════════════════════════════════════════════════════
    print("\n═══ Phase 3: AMM Pools ═══")

    for i, (_name, symbol, _price) in enumerate(SYNTHETICS):
        print(f"  🏊 {symbol}/USDC pool...")
        await call_contract(
            deployed["ammRouter"],
            load_abi("AMMRouter"),
            "createPool",
            [USDC_ARC, synth_tokens[i]],
        )
        print(f"  ✅ {symbol}/USDC pool created")

    # ═══════════════════════════════════════════════════════════════
    # Phase 4: Tier 1 Vault (agent-created)
    # ═══════════════════════════════════════════════════════════════
    print("\n═══ Phase 4: Tier 1 Vault ═══")

    await call_contract(
        deployed["vaultFactory"],
        load_abi("VaultFactory"),
        "createVault",
        ["Archimedes Momentum Alpha", "vMOM", 150, 2000, True],
    )

    # Find the vault address
    factory = w3.eth.contract(
        address=to_checksum_address(deployed["vaultFactory"]),
        abi=load_abi("VaultFactory"),
    )
    vault_list = await factory.functions.getVaults().call()
    vault_addr = vault_list[-1]
    deployed["tier1Vault"] = vault_addr
    print(f"  📍 Tier 1 vault: {vault_addr}")

    # Set oracle addresses on the vault (THE KEY FIX)
    print("\n  🔮 Setting token oracles on vault...")
    oracle_tokens = [t for t in synth_tokens[:5]]  # first 5 synthetics
    oracle_addrs = synth_oracles[:5]
    await call_contract(
        vault_addr,
        load_abi("Vault"),
        "setTokenOracles",
        [oracle_tokens, oracle_addrs],
    )
    print("  ✅ Token oracles set")

    # Set target allocations
    print("  🎯 Setting target allocations...")
    alloc_tokens = [USDC_ARC, *synth_tokens[:2]]  # USDC + sTSLA + sBTC
    alloc_weights = [4000, 3500, 2500]  # 40% USDC, 35% sTSLA, 25% sBTC
    await call_contract(
        vault_addr,
        load_abi("Vault"),
        "setTargetAllocations",
        [alloc_tokens, alloc_weights],
    )
    print("  ✅ Target allocations set")

    # ═══════════════════════════════════════════════════════════════
    # Summary
    # ═══════════════════════════════════════════════════════════════
    print("\n" + "=" * 60)
    print("🎉 DEPLOYMENT COMPLETE")
    print("=" * 60)
    print(f"\nAMMRouter:              {deployed.get('ammRouter', 'N/A')}")
    print(f"SyntheticFactory:       {deployed.get('syntheticFactory', 'N/A')}")
    print(f"ReasoningTraceRegistry: {deployed.get('reasoningTraceRegistry', 'N/A')}")
    print(f"AssetRegistry:          {deployed.get('assetRegistry', 'N/A')}")
    print(f"VaultFactory:           {deployed.get('vaultFactory', 'N/A')}")
    print(f"Tier 1 Vault:           {deployed.get('tier1Vault', 'N/A')}")
    print("\nSynthetics:")
    for i, (_name, symbol, _price) in enumerate(SYNTHETICS):
        print(f"  {symbol:6s} token: {synth_tokens[i]}")
        print(f"  {symbol:6s} oracle: {synth_oracles[i]}")

    # Save to JSON for easy reference
    output = {
        "contracts": deployed,
        "synthTokens": {SYNTHETICS[i][1]: synth_tokens[i] for i in range(len(SYNTHETICS))},
        "synthOracles": {SYNTHETICS[i][1]: synth_oracles[i] for i in range(len(SYNTHETICS))},
    }
    output_path = Path(__file__).resolve().parents[3] / "deploy_output.json"
    with open(output_path, "w") as f:
        json.dump(output, f, indent=2)
    print(f"\n📄 Full output saved to: {output_path}")

    # Print env var updates
    print("\n📋 Add these to your .env:")
    print(f"ARC_AMM_ROUTER_ADDRESS={deployed.get('ammRouter', '')}")
    print(f"ARC_SYNTHETIC_FACTORY_ADDRESS={deployed.get('syntheticFactory', '')}")
    print(f"ARC_REASONING_TRACE_REGISTRY_ADDRESS={deployed.get('reasoningTraceRegistry', '')}")
    print(f"ARC_ASSET_REGISTRY_ADDRESS={deployed.get('assetRegistry', '')}")
    print(f"ARC_VAULT_FACTORY_ADDRESS={deployed.get('vaultFactory', '')}")


if __name__ == "__main__":
    asyncio.run(main())
