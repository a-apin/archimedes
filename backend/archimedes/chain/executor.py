"""Chain executor — executes on-chain vault operations.

Implements IChainExecutor from archimedes/interfaces/chain.py.
Handles reading portfolio state, executing rebalance trades, and vault creation.
"""

from __future__ import annotations

import logging

from web3.contract import AsyncContract

from archimedes.chain.client import chain_client
from archimedes.chain.contracts import ContractLoader, get_contract_loader
from archimedes.models.portfolio import (
    Portfolio,
    PortfolioHolding,
    TradeOrder,
    TradeDirection,
)

logger = logging.getLogger(__name__)


class ChainExecutor:
    """Executes on-chain vault operations: read portfolio, rebalance, create vault."""

    def __init__(self, loader: ContractLoader | None = None):
        self.loader = loader or get_contract_loader()

    async def read_portfolio(self, vault_address: str) -> Portfolio:
        """Read a vault's current holdings from on-chain state."""
        vault = self.loader.vault(vault_address)
        settings = chain_client.settings

        # Read holdings from vault contract
        holdings_data = await vault.functions.getHoldings().call()
        token_addresses = holdings_data[0]
        amounts = holdings_data[1]

        # Read target allocations
        target_data = await vault.functions.getTargetAllocations().call()
        target_tokens = target_data[0]
        target_weights = target_data[1]

        # Read total assets
        total_assets = await vault.functions.totalAssets().call()

        # Build portfolio
        holdings: list[PortfolioHolding] = []
        for i in range(len(token_addresses)):
            token_addr = token_addresses[i]
            amount = amounts[i]

            if amount == 0:
                continue

            # Get token symbol from contract
            symbol = await self._get_token_symbol(token_addr)
            decimals = await self._get_token_decimals(token_addr)

            # Calculate USDC value
            value_usdc = await self._token_to_usdc(token_addr, amount, decimals)
            weight = value_usdc / total_assets if total_assets > 0 else 0.0

            holdings.append(
                PortfolioHolding(
                    symbol=symbol,
                    token_address=token_addr,
                    amount=amount / 10**decimals if decimals else float(amount),
                    value_usdc=value_usdc / 1e6,  # USDC has 6 decimals
                    weight=weight,
                )
            )

        return Portfolio(
            vault_address=vault_address,
            holdings=holdings,
            total_value_usdc=total_assets / 1e6,
        )

    async def execute_trades(
        self,
        vault_address: str,
        trades: list[TradeOrder],
    ) -> list[str]:
        """Execute rebalance trades for a vault.

        Calls Vault.rebalance() with the buy and sell arrays.
        """
        account = chain_client.settings.agent_account
        if not account:
            raise RuntimeError("No agent account configured")

        # Split trades into buys and sells
        tokens_in: list[str] = []
        amounts_in: list[int] = []
        tokens_out: list[str] = []
        amounts_out: list[int] = []

        for trade in trades:
            token_addr = chain_client.to_checksum(trade.token_address)
            if trade.direction == TradeDirection.BUY:
                tokens_in.append(token_addr)
                amounts_in.append(int(trade.amount))
            else:
                tokens_out.append(token_addr)
                amounts_out.append(int(trade.amount))

        vault = self.loader.vault(vault_address)
        nonce = await chain_client.w3.eth.get_transaction_count(account.address)

        tx = await vault.functions.rebalance(
            tokens_in, amounts_in, tokens_out, amounts_out
        ).build_transaction(
            {
                "from": account.address,
                "nonce": nonce,
                "chainId": chain_client.settings.chain_id,
                "gas": 2_000_000,  # Rebalance can be gas-heavy
                "gasPrice": await chain_client.w3.eth.gas_price,
            }
        )

        signed = account.sign_transaction(tx)
        tx_hash = await chain_client.w3.eth.send_raw_transaction(signed.raw_transaction)

        logger.info(f"Rebalance tx sent: {tx_hash.hex()}")
        return [tx_hash.hex()]

    async def create_vault(
        self,
        name: str,
        symbol: str,
        management_fee_bps: int,
        performance_fee_bps: int,
        agent_assisted: bool,
    ) -> str:
        """Deploy a new vault via VaultFactory."""
        account = chain_client.settings.agent_account
        if not account:
            raise RuntimeError("No agent account configured")

        factory = self.loader.vault_factory
        nonce = await chain_client.w3.eth.get_transaction_count(account.address)

        tx = await factory.functions.createVault(
            name, symbol, management_fee_bps, performance_fee_bps, agent_assisted
        ).build_transaction(
            {
                "from": account.address,
                "nonce": nonce,
                "chainId": chain_client.settings.chain_id,
                "gas": 5_000_000,  # Vault deployment is expensive
                "gasPrice": await chain_client.w3.eth.gas_price,
            }
        )

        signed = account.sign_transaction(tx)
        tx_hash = await chain_client.w3.eth.send_raw_transaction(signed.raw_transaction)

        # Wait for receipt to get the vault address from the event
        receipt = await chain_client.w3.eth.wait_for_transaction_receipt(tx_hash)

        # Parse VaultCreated event to get the new vault address
        vault_address = None
        for log in receipt.logs:
            try:
                result = factory.events.VaultCreated().process_log(log)
                vault_address = result["args"]["vault"]
                break
            except Exception:
                continue

        if not vault_address:
            raise RuntimeError("Failed to extract vault address from deployment receipt")

        logger.info(f"Vault created at {vault_address} in tx {tx_hash.hex()}")
        return vault_address

    async def get_vault_metrics(self, vault_address: str) -> dict:
        """Read vault metrics from on-chain state."""
        vault = self.loader.vault(vault_address)

        try:
            total_assets = await vault.functions.totalAssets().call()
        except Exception:
            total_assets = 0
        try:
            total_supply = await vault.functions.totalSupply().call()
        except Exception:
            total_supply = 0

        try:
            hwm = await vault.functions.highWaterMark().call()
        except Exception:
            hwm = 0
        try:
            creator = await vault.functions.creator().call()
        except Exception:
            creator = "0x0000000000000000000000000000000000000000"
        try:
            tier = await vault.functions.tier().call()
        except Exception:
            tier = 2
        try:
            mgmt_fee_bps = await vault.functions.managementFeeBps().call()
        except Exception:
            mgmt_fee_bps = 0
        try:
            perf_fee_bps = await vault.functions.performanceFeeBps().call()
        except Exception:
            perf_fee_bps = 0
        try:
            is_agent = await vault.functions.isAgentAssisted().call()
        except Exception:
            is_agent = False
        try:
            paused = await vault.functions.paused().call()
        except Exception:
            paused = False

        share_price = total_assets / total_supply if total_supply > 0 else 1e6  # 1 USDC

        return {
            "vault_address": vault_address,
            "total_aum_usdc": total_assets / 1e6,
            "total_supply": total_supply,
            "share_price_usdc": share_price / 1e6,
            "high_water_mark": hwm,
            "creator": creator,
            "tier": tier,
            "management_fee_bps": mgmt_fee_bps,
            "performance_fee_bps": perf_fee_bps,
            "is_agent_assisted": is_agent,
            "paused": paused,
        }

    async def get_all_vaults(self) -> list[str]:
        """Get all vault addresses from VaultFactory."""
        factory = self.loader.vault_factory
        vaults = await factory.functions.getVaults().call()
        return [v for v in vaults]

    async def get_vault_count(self) -> int:
        factory = self.loader.vault_factory
        return await factory.functions.vaultCount().call()

    # ─── Helpers ───────────────────────────────────────────────────

    async def _get_token_symbol(self, token_address: str) -> str:
        """Get ERC-20 symbol, with fallback for known tokens."""
        known = {
            chain_client.settings.usdc_address.lower(): "USDC",
        }
        for sym, addr in chain_client.settings.synth_addresses.items():
            if addr:
                known[addr.lower()] = sym

        addr_lower = token_address.lower()
        if addr_lower in known:
            return known[addr_lower]

        try:
            token = self.loader.token(token_address)
            return await token.functions.symbol().call()
        except Exception:
            return token_address[:8]

    async def _get_token_decimals(self, token_address: str) -> int:
        """Get ERC-20 decimals, with fallback for USDC."""
        if token_address.lower() == chain_client.settings.usdc_address.lower():
            return 6
        try:
            token = self.loader.token(token_address)
            return await token.functions.decimals().call()
        except Exception:
            return 18

    async def _token_to_usdc(self, token_address: str, amount: int, decimals: int) -> int:
        """Estimate USDC value of a token holding."""
        if token_address.lower() == chain_client.settings.usdc_address.lower():
            return amount

        # For synth tokens, use the oracle price
        for sym, addr in chain_client.settings.synth_addresses.items():
            if addr and addr.lower() == token_address.lower():
                try:
                    oracle = self.loader.oracle_for(sym)
                    price = await oracle.functions.price().call()  # 6 decimals
                    # USDC value = amount (18 dec) * price (6 dec) / 1e18
                    return (amount * price) // (10**decimals)
                except Exception:
                    pass

        # Fallback: return raw amount
        return amount


# Singleton
chain_executor = ChainExecutor()
