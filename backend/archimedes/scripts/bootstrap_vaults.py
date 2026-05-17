"""Bootstrap real vault ecosystem — creates demo vaults with real synthetic tokens.

This script constructs multiple vaults with different risk profiles using real
on-chain operations via Circle dev-controlled wallet. No fake data.

Flow:
  1. Set oracle prices to desired levels
  2. Mint synthetic tokens (depositing USDC as collateral)
  3. Create vaults via VaultFactory with different profiles
  4. Transfer synth tokens into vaults
  5. Set target allocations on each vault
  6. Verify AUM on-chain

Usage:
  cd backend && uv run python -m archimedes.scripts.bootstrap_vaults
"""

from __future__ import annotations

import asyncio
import os
import sys

# Load env before any archimedes imports
from dotenv import load_dotenv

load_dotenv("../.env", override=True)
load_dotenv(".env", override=False)

from archimedes.chain.client import chain_client
from archimedes.chain.contracts import get_contract_loader
from archimedes.chain.circle_signer import circle_signer

# ─── Configuration ──────────────────────────────────────────────

# Target oracle prices (USD, 6-decimal precision)
TARGET_PRICES = {
    "sTSLA": 285.50,
    "sNVDA": 135.20,
    "sSPY": 592.40,
    "sBTC": 104500.00,
    "sGOLD": 3250.00,
    "sOIL": 62.80,
    "sNKY": 38500.00,
}

# Vault profiles to create
VAULT_PROFILES = [
    {
        "name": "Momentum Alpha",
        "symbol": "vMOM",
        "management_fee_bps": 100,
        "performance_fee_bps": 1500,
        "agent_assisted": True,
        "allocations": {
            "sTSLA": 2500,
            "sSPY": 2500,
            "sBTC": 1500,
            "sGOLD": 1500,
            "USDC": 2000,
        },
    },
    {
        "name": "Yield Optimizer",
        "symbol": "vYLD",
        "management_fee_bps": 50,
        "performance_fee_bps": 1000,
        "agent_assisted": True,
        "allocations": {
            "sSPY": 2000,
            "sGOLD": 1500,
            "sOIL": 1000,
            "USDC": 5500,
        },
    },
    {
        "name": "DeFi Degen",
        "symbol": "vDEGN",
        "management_fee_bps": 200,
        "performance_fee_bps": 2500,
        "agent_assisted": True,
        "allocations": {
            "sTSLA": 3000,
            "sBTC": 3000,
            "sNVDA": 2000,
            "USDC": 2000,
        },
    },
    {
        "name": "Safe Haven",
        "symbol": "vSAFE",
        "management_fee_bps": 30,
        "performance_fee_bps": 500,
        "agent_assisted": True,
        "allocations": {
            "sGOLD": 2500,
            "sSPY": 1500,
            "USDC": 6000,
        },
    },
    {
        "name": "Multi-Factor Quant",
        "symbol": "vMFQ",
        "management_fee_bps": 150,
        "performance_fee_bps": 2000,
        "agent_assisted": True,
        "allocations": {
            "sTSLA": 2000,
            "sNVDA": 1500,
            "sSPY": 2000,
            "sGOLD": 1500,
            "sNKY": 1000,
            "USDC": 2000,
        },
    },
]

# USDC to mint as synth collateral per token (in USDC units)
# We spread the 221 USDC across synth mints + leave some for vault deposits
MINT_BUDGET = {
    "sTSLA": 30,
    "sNVDA": 20,
    "sSPY": 20,
    "sBTC": 15,
    "sGOLD": 15,
    "sOIL": 10,
    "sNKY": 10,
}

# USDC to deposit into each vault (from remaining balance)
DEPOSIT_PER_VAULT = 10  # USDC each


async def set_oracle_prices() -> None:
    """Push target oracle prices on-chain via Circle wallet."""
    print("\n📊 Step 1: Setting oracle prices...")
    oracle_addresses = chain_client.settings.oracle_addresses

    for symbol, price_usd in TARGET_PRICES.items():
        oracle_addr = oracle_addresses.get(symbol)
        if not oracle_addr:
            print(f"  ⚠️  No oracle for {symbol} — skipping")
            continue

        price_int = int(price_usd * 1e6)  # 6-decimal precision
        try:
            tx_hash = await circle_signer.execute_contract(
                contract_address=oracle_addr,
                abi_function="setPrice(uint256)",
                abi_params=[price_int],
            )
            print(f"  ✅ {symbol}: ${price_usd:,.2f} → tx {tx_hash[:16]}...")
        except Exception as e:
            print(f"  ❌ {symbol}: {e}")


async def mint_synthetic_tokens() -> dict[str, float]:
    """Mint synthetic tokens by depositing USDC into SyntheticVault contracts.

    Returns dict of symbol → amount minted (in token units, 18 decimals).
    """
    print("\n🏭 Step 2: Minting synthetic tokens...")

    synth_vault_addresses = {
        "sTSLA": "0xf0356600e26c6c403ec4f5b36b0e3380bb0609ab",
        "sNVDA": "0x4c3cdc2bf44195ad8a4d201c8afbd453949a8781",
        "sSPY": "0xd8d7855f76c384638cf1dfc3575ecff3538764b4",
        "sBTC": "0x92990ed6f5c8cd72752ca9aeafad422269225c43",
        "sGOLD": "0x124b5c5da57d209b28d4997aaf6d4e96711efd5a",
        "sOIL": "0xfa942399e36959c8060c3a82a610d680a7ac6d22",
        "sNKY": "0xb26029ca37c09400ca921f00fc541cd42143b508",
    }

    wallet = os.getenv("WALLET_ADDRESS")
    usdc_address = chain_client.settings.usdc_address
    synth_addresses = chain_client.settings.synth_addresses
    minted: dict[str, float] = {}

    for symbol, usdc_amount in MINT_BUDGET.items():
        vault_addr = synth_vault_addresses[symbol]
        token_addr = synth_addresses[symbol]
        usdc_int = int(usdc_amount * 1e6)  # USDC has 6 decimals

        # Check existing balance first
        try:
            token = get_contract_loader().token(token_addr)
            existing = await token.functions.balanceOf(
                chain_client.to_checksum(wallet)
            ).call()
            existing_float = existing / 1e18
            if existing_float > 0.001:
                print(f"  ⏭️  {symbol}: already have {existing_float:.4f} — skipping mint")
                minted[symbol] = existing_float
                continue
        except Exception:
            pass

        try:
            # Approve USDC for the synth vault
            print(f"  📝 {symbol}: approving {usdc_amount} USDC...")
            await circle_signer.execute_contract(
                contract_address=usdc_address,
                abi_function="approve(address,uint256)",
                abi_params=[vault_addr, usdc_int],
            )

            # Mint
            print(f"  🏭 {symbol}: minting with {usdc_amount} USDC collateral...")
            tx_hash = await circle_signer.execute_contract(
                contract_address=vault_addr,
                abi_function="mint(uint256)",
                abi_params=[usdc_int],
            )

            # Read balance after mint
            token = get_contract_loader().token(token_addr)
            balance = await token.functions.balanceOf(
                chain_client.to_checksum(wallet)
            ).call()
            minted[symbol] = balance / 1e18
            print(f"  ✅ {symbol}: minted {minted[symbol]:.4f} tokens (tx {tx_hash[:16]}...)")
        except Exception as e:
            print(f"  ❌ {symbol}: mint failed — {e}")
            minted[symbol] = 0.0

    return minted


async def create_vaults(minted: dict[str, float]) -> list[dict]:
    """Create vaults with different profiles via VaultFactory."""
    print("\n🏗️ Step 3: Creating vaults...")
    loader = get_contract_loader()
    vaults: list[dict] = []

    for profile in VAULT_PROFILES:
        try:
            # Create vault via VaultFactory
            tx_hash = await circle_signer.execute_contract(
                contract_address=chain_client.settings.vault_factory_address,
                abi_function="createVault(string,string,uint16,uint16,bool)",
                abi_params=[
                    profile["name"],
                    profile["symbol"],
                    profile["management_fee_bps"],
                    profile["performance_fee_bps"],
                    profile["agent_assisted"],
                ],
            )

            # Wait for receipt and find new vault address
            receipt = await chain_client.w3.eth.wait_for_transaction_receipt(
                chain_client.w3.to_bytes(hexstr=tx_hash.removeprefix("0x"))
            )

            vault_address = None
            factory = loader.vault_factory
            for log in receipt.logs:
                try:
                    result = factory.events.VaultCreated().process_log(log)
                    vault_address = result["args"]["vault"]
                    break
                except Exception:
                    continue

            if not vault_address:
                # Fallback: get last vault from factory
                all_vaults = await factory.functions.getVaults().call()
                vault_address = all_vaults[-1]

            print(f"  ✅ {profile['name']} ({profile['symbol']}): {vault_address}")

            vaults.append({
                "address": vault_address,
                "name": profile["name"],
                "symbol": profile["symbol"],
                "allocations": profile["allocations"],
            })
        except Exception as e:
            print(f"  ❌ {profile['name']}: creation failed — {e}")

    return vaults


async def fund_and_allocate_vaults(vaults: list[dict], minted: dict[str, float]) -> None:
    """Transfer synth tokens into vaults and set target allocations."""
    print("\n💰 Step 4: Funding vaults and setting allocations...")
    loader = get_contract_loader()
    synth_addresses = chain_client.settings.synth_addresses
    usdc_address = chain_client.settings.usdc_address
    wallet = os.getenv("WALLET_ADDRESS")

    for vault_info in vaults:
        vault_addr = vault_info["address"]
        alloc = vault_info["allocations"]
        n_vaults = len(vaults)

        # Transfer synth tokens to vault (split minted tokens across vaults)
        for symbol, _ in alloc.items():
            if symbol == "USDC":
                continue

            token_addr = synth_addresses.get(symbol)
            if not token_addr or minted.get(symbol, 0) <= 0:
                continue

            # Transfer 1/n of minted tokens to this vault
            transfer_amount = int(minted[symbol] / n_vaults * 1e18)
            if transfer_amount <= 0:
                continue

            try:
                await circle_signer.execute_contract(
                    contract_address=token_addr,
                    abi_function="transfer(address,uint256)",
                    abi_params=[vault_addr, transfer_amount],
                )
                print(f"  📦 {vault_info['symbol']}: transferred {symbol}")
            except Exception as e:
                print(f"  ⚠️  {vault_info['symbol']}: transfer {symbol} failed — {e}")

        # Deposit USDC into vault
        deposit_amount = int(DEPOSIT_PER_VAULT * 1e6)
        try:
            # Approve vault to spend USDC
            await circle_signer.execute_contract(
                contract_address=usdc_address,
                abi_function="approve(address,uint256)",
                abi_params=[vault_addr, deposit_amount],
            )
            # Deposit
            await circle_signer.execute_contract(
                contract_address=vault_addr,
                abi_function="deposit(uint256,address)",
                abi_params=[deposit_amount, wallet],
            )
            print(f"  💵 {vault_info['symbol']}: deposited {DEPOSIT_PER_VAULT} USDC")
        except Exception as e:
            print(f"  ⚠️  {vault_info['symbol']}: USDC deposit failed — {e}")

        # Set target allocations
        tokens = []
        weights = []
        for symbol, weight_bps in alloc.items():
            if symbol == "USDC":
                tokens.append(usdc_address)
            else:
                tokens.append(synth_addresses[symbol])
            weights.append(weight_bps)

        try:
            await circle_signer.execute_contract(
                contract_address=vault_addr,
                abi_function="setTargetAllocations(address[],uint256[])",
                abi_params=[tokens, weights],
            )
            print(f"  🎯 {vault_info['symbol']}: allocations set ({len(tokens)} tokens)")
        except Exception as e:
            print(f"  ⚠️  {vault_info['symbol']}: setTargetAllocations failed — {e}")

        # Set agent address so the Circle wallet can call rebalance()
        # Since the Circle wallet IS the creator, this is redundant but ensures
        # the agent field is set for display purposes
        try:
            await circle_signer.execute_contract(
                contract_address=vault_addr,
                abi_function="setAgent(address)",
                abi_params=[os.getenv("WALLET_ADDRESS", "")],
            )
            print(f"  🤖 {vault_info['symbol']}: agent set")
        except Exception as e:
            print(f"  ⚠️  {vault_info['symbol']}: setAgent failed — {e}")


async def add_amm_liquidity(minted: dict[str, float]) -> None:
    """Add liquidity to AMM pools so vault rebalances can execute swaps.

    For each synth token with an existing pool, add a proportional amount
    of USDC and synth tokens. Uses the oracle price to determine the ratio.
    """
    print("\n🏊 Step 3: Adding AMM pool liquidity...")
    loader = get_contract_loader()
    router = loader.amm_router
    usdc_address = chain_client.settings.usdc_address
    synth_addresses = chain_client.settings.synth_addresses
    oracle_addresses = chain_client.settings.oracle_addresses

    # How much USDC to allocate per pool for liquidity
    USDC_PER_POOL = 5.0  # USDC units

    for symbol, token_addr in synth_addresses.items():
        if not token_addr or minted.get(symbol, 0) <= 0:
            print(f"  ⏭️  {symbol}: no minted tokens — skipping pool liquidity")
            continue

        # Check if pool exists
        try:
            pool_addr = await router.functions.getPool(
                chain_client.to_checksum(usdc_address),
                chain_client.to_checksum(token_addr),
            ).call()
        except Exception:
            print(f"  ⚠️  {symbol}: pool lookup failed")
            continue

        if pool_addr == "0x0000000000000000000000000000000000000000":
            print(f"  ⚠️  {symbol}: no AMM pool exists — skipping")
            continue

        # Get oracle price to compute proper ratio
        oracle_addr = oracle_addresses.get(symbol)
        if not oracle_addr:
            print(f"  ⚠️  {symbol}: no oracle — skipping")
            continue

        try:
            oracle = loader.oracle_for(symbol)
            price_raw = await oracle.functions.price().call()  # 6 decimals
            price_usd = price_raw / 1e6
        except Exception as e:
            print(f"  ⚠️  {symbol}: oracle read failed — {e}")
            continue

        if price_usd <= 0:
            print(f"  ⚠️  {symbol}: zero price — skipping")
            continue

        # Compute amounts
        # We want to add USDC_PER_POOL USDC worth of liquidity on each side
        usdc_raw = int(USDC_PER_POOL * 1e6)  # 6 decimals
        # Token amount = USDC value / price per token, in 18 decimals
        token_amount = USDC_PER_POOL / price_usd
        token_raw = int(token_amount * 1e18)

        # Cap token amount to what we actually have minted
        available_raw = int(minted[symbol] * 1e18)
        if token_raw > available_raw:
            token_raw = available_raw
            # Adjust USDC proportionally
            actual_token_units = token_raw / 1e18
            usdc_raw = int(actual_token_units * price_usd * 1e6)

        if token_raw <= 0 or usdc_raw <= 0:
            print(f"  ⚠️  {symbol}: amounts too small — skipping")
            continue

        try:
            # Approve router to spend USDC
            await circle_signer.execute_contract(
                contract_address=usdc_address,
                abi_function="approve(address,uint256)",
                abi_params=[chain_client.settings.amm_router_address, usdc_raw],
            )

            # Approve router to spend synth token
            await circle_signer.execute_contract(
                contract_address=token_addr,
                abi_function="approve(address,uint256)",
                abi_params=[chain_client.settings.amm_router_address, token_raw],
            )

            # Add liquidity
            tx_hash = await circle_signer.execute_contract(
                contract_address=chain_client.settings.amm_router_address,
                abi_function="addLiquidity(address,address,uint256,uint256,uint256)",
                abi_params=[
                    chain_client.to_checksum(usdc_address),
                    chain_client.to_checksum(token_addr),
                    usdc_raw,
                    token_raw,
                    0,  # min LP tokens
                ],
            )
            print(
                f"  ✅ {symbol}: added ${USDC_PER_POOL:.0f} USDC + "
                f"{token_amount:.4f} tokens to pool (tx {tx_hash[:16]}...)"
            )
        except Exception as e:
            print(f"  ❌ {symbol}: addLiquidity failed — {e}")


async def verify_ecosystem(vaults: list[dict]) -> None:
    """Verify the final state of all vaults."""
    print("\n✅ Step 6: Verifying ecosystem...")
    loader = get_contract_loader()

    total_aum = 0.0

    for vault_info in vaults:
        addr = vault_info["address"]
        vault = loader.vault(addr)

        try:
            total_assets = await vault.functions.totalAssets().call()
            total_supply = await vault.functions.totalSupply().call()
            tier = await vault.functions.tier().call()
            aum = total_assets / 1e6
            total_aum += aum

            # Read target allocations
            t_tokens, t_weights = await vault.functions.getTargetAllocations().call()

            alloc_str = ", ".join(
                f"{w / 100}%" for w in t_weights
            )

            print(
                f"  {vault_info['name']} ({vault_info['symbol']}): "
                f"T{tier}, AUM=${aum:,.2f}, "
                f"shares={total_supply}, "
                f"alloc=[{alloc_str}]"
            )
        except Exception as e:
            print(f"  ⚠️  {vault_info['name']}: verify failed — {e}")

    # Total vault count
    factory = loader.vault_factory
    count = await factory.functions.vaultCount().call()
    print(f"\n  📊 Total vaults on-chain: {count}")
    print(f"  💰 Total ecosystem AUM: ${total_aum:,.2f} USDC")


async def main() -> None:
    print("🏗️  Archimedes Vault Ecosystem Bootstrapper")
    print("=" * 50)

    if not circle_signer.is_configured:
        print("❌ Circle wallet not configured. Set CIRCLE_API_KEY, CIRCLE_ENTITY_SECRET, WALLET_ID.")
        sys.exit(1)

    # Step 1: Set oracle prices
    await set_oracle_prices()

    # Step 2: Mint synthetic tokens
    minted = await mint_synthetic_tokens()

    # Step 3: Add AMM pool liquidity
    await add_amm_liquidity(minted)

    # Step 4: Create vaults
    vaults = await create_vaults(minted)

    if not vaults:
        print("❌ No vaults created — aborting.")
        sys.exit(1)

    # Step 5: Fund and allocate
    await fund_and_allocate_vaults(vaults, minted)

    # Step 6: Verify
    await verify_ecosystem(vaults)

    print("\n🎉 Bootstrap complete!")


if __name__ == "__main__":
    asyncio.run(main())
