"""Chain executor — executes on-chain vault operations.

Implements IChainExecutor from archimedes/interfaces/chain.py.
Handles reading portfolio state, executing rebalance trades, and vault creation.
"""

from __future__ import annotations

import logging
import os

from web3.contract import AsyncContract

from archimedes.chain.circle_signer import circle_signer
from archimedes.chain.client import chain_client
from archimedes.chain.contracts import ContractLoader, get_contract_loader
from archimedes.models.portfolio import (
    Portfolio,
    PortfolioHolding,
    TradeDirection,
    TradeOrder,
)

logger = logging.getLogger(__name__)

# Minimum USDC-equivalent reserve a pool must hold for the executor to submit
# a swap through it. Prevents doomed trades on completely empty pools.
# Set to $5 for testnet (wallet balance is limited by faucet rate).
# Production would raise this to $1000+.
MIN_HEALTHY_LIQUIDITY_USDC = 5.0


class InsufficientLiquidityError(RuntimeError):
    """Raised when an AMM pool's USDC reserve is below MIN_HEALTHY_LIQUIDITY_USDC."""


class TradeRevertedError(RuntimeError):
    """Raised when a submitted rebalance transaction reverts on-chain (status 0).

    Without this, a reverted rebalance is logged as "sent" and returned as a
    success hash — the agent then records a reasoning trace as if the trade
    executed when it actually failed.
    """


class VaultCreationRevertedError(RuntimeError):
    """Raised when a submitted createVault transaction reverts on-chain (status 0).

    Without this, a reverted createVault is logged as "sent" and the Circle
    path falls through to all_vaults[-1] — returning a pre-existing vault's
    address as the result of a creation call that never happened.
    """


class ChainExecutor:
    """Executes on-chain vault operations: read portfolio, rebalance, create vault."""

    def __init__(self, loader: ContractLoader | None = None):
        self.loader = loader or get_contract_loader()

    async def read_portfolio(self, vault_address: str) -> Portfolio:
        """Read a vault's current holdings from on-chain state.

        Falls back to off-chain NAV computation if totalAssets() reverts
        (e.g. stale oracle prices on testnet).
        """
        vault = self.loader.vault(vault_address)

        # Read holdings from vault contract
        holdings_data = await vault.functions.getHoldings().call()
        token_addresses = holdings_data[0]
        amounts = holdings_data[1]

        # Read total assets — fall back to off-chain NAV if stale price
        total_assets = await self._safe_total_assets(vault, token_addresses, amounts)

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
            value_usdc = await self._token_to_usdc(token_addr, amount, decimals, total_assets > 0)
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

    async def _validate_trade_liquidity(self, trades: list[TradeOrder]) -> None:
        """Pre-flight: verify AMM pools have sufficient liquidity for proposed trades.

        Reads reserve0/reserve1 from each pool, identifies the USDC-side reserve,
        and raises InsufficientLiquidityError if it's below the threshold.
        """
        usdc_addr = chain_client.to_checksum(chain_client.settings.usdc_address)
        router = self.loader.amm_router

        for trade in trades:
            token_addr = chain_client.to_checksum(trade.token_address)
            # A USDC-denominated leg (e.g. the cash / TREASURY allocation) needs
            # no swap — it is already in the settlement asset. getPool(USDC, USDC)
            # has no pool and would otherwise raise InsufficientLiquidityError,
            # aborting the whole rebalance before any fundable leg is reached
            # (issue #399). Skip it.
            if token_addr == usdc_addr:
                continue
            try:
                pool_addr = await router.functions.getPool(usdc_addr, token_addr).call()
                if pool_addr == "0x" + "0" * 40:
                    raise InsufficientLiquidityError(f"No AMM pool for {trade.symbol}: getPool returned zero address")

                pool = self.loader.amm_pool(pool_addr)
                r0 = await pool.functions.reserve0().call()
                r1 = await pool.functions.reserve1().call()
                t0 = await pool.functions.token0().call()

                # Identify USDC-side reserve (6 decimals)
                usdc_reserve = r0 / 1e6 if chain_client.to_checksum(t0) == usdc_addr else r1 / 1e6

                if usdc_reserve < MIN_HEALTHY_LIQUIDITY_USDC:
                    raise InsufficientLiquidityError(
                        f"Pool {pool_addr[:10]}… ({trade.symbol}): USDC reserve "
                        f"${usdc_reserve:,.2f} below threshold ${MIN_HEALTHY_LIQUIDITY_USDC:,.0f}"
                    )
                logger.debug(
                    "liquidity OK for %s: pool %s USDC reserve $%.2f",
                    trade.symbol,
                    pool_addr[:10],
                    usdc_reserve,
                )
            except InsufficientLiquidityError:
                raise
            except Exception as exc:
                # Fail closed: a probe error (RPC failure, ABI decode error, etc.)
                # means we could NOT determine whether this pool has healthy
                # liquidity — treat that the same as "confirmed insufficient"
                # rather than letting the trade through unguarded. The message
                # is worded distinctly ("probe failed") so logs/operators can
                # tell a probe failure apart from a confirmed-thin-pool result,
                # even though the caller-visible effect (skip this leg) matches.
                logger.warning(
                    "liquidity probe failed for %s — skipping leg (fail-closed): %s",
                    trade.symbol,
                    exc,
                )
                raise InsufficientLiquidityError(
                    f"Liquidity probe failed for {trade.symbol} (treating as insufficient): {exc}"
                ) from exc

    async def _confirm_receipt(self, tx_hash: str | bytes) -> str:
        """Wait for a tx receipt and raise TradeRevertedError if it reverted.

        Mirrors the receipt-wait already done in create_vault. Returns the
        normalized 0x-prefixed hex hash on success.
        """
        if isinstance(tx_hash, str):
            hexstr = tx_hash if tx_hash.startswith("0x") else "0x" + tx_hash
            wait_arg = chain_client.w3.to_bytes(hexstr=hexstr)
            norm = hexstr
        else:
            wait_arg = tx_hash
            norm = tx_hash.hex()
            norm = norm if norm.startswith("0x") else "0x" + norm

        receipt = await chain_client.w3.eth.wait_for_transaction_receipt(wait_arg)
        if receipt.get("status") == 0:
            raise TradeRevertedError(f"Rebalance tx reverted on-chain: {norm}")
        return norm

    async def execute_trades(
        self,
        vault_address: str,
        trades: list[TradeOrder],
    ) -> list[str]:
        """Execute rebalance trades for a vault.

        Uses Circle dev-controlled wallet if configured, falls back to raw private key.
        Pre-flight: validates AMM pool liquidity before submitting.

        Amounts in TradeOrder are in human-readable USDC units (e.g. 8.0 for $8).
        The vault's rebalance() expects raw token amounts:
          - BUY (USDC → synth): amount in USDC raw (6 decimals)
          - SELL (synth → USDC): amount in synth raw (18 decimals)
        """
        # Drop USDC-denominated legs (cash / TREASURY allocation): holding USDC
        # is already the settlement asset, so there is no swap to make and no
        # USDC/USDC pool to find (issue #399). Filter once so both the liquidity
        # check and the swap construction below see only real swap legs.
        usdc_addr = chain_client.to_checksum(chain_client.settings.usdc_address)
        trades = [t for t in trades if chain_client.to_checksum(t.token_address) != usdc_addr]

        # Pre-flight liquidity check (raises InsufficientLiquidityError)
        await self._validate_trade_liquidity(trades)

        # Split trades into buys and sells with proper decimal conversion
        tokens_in: list[str] = []
        amounts_in: list[int] = []
        tokens_out: list[str] = []
        amounts_out: list[int] = []

        for trade in trades:
            token_addr = chain_client.to_checksum(trade.token_address)
            if trade.direction == TradeDirection.BUY:
                # BUY: amount is USDC to spend → convert to raw (6 decimals)
                tokens_in.append(token_addr)
                amounts_in.append(int(trade.amount * 1e6))
            else:
                # SELL: amount is USDC value → convert to token raw amount via oracle price
                token_raw = await self._usdc_value_to_token_raw(
                    token_addr,
                    trade.estimated_usdc_value,
                )
                tokens_out.append(token_addr)
                amounts_out.append(token_raw)

        # Circle path
        if circle_signer.is_configured:
            # Convert address[] and uint256[] to ABI-compatible strings
            checksummed_in = [chain_client.to_checksum(t) for t in tokens_in]
            checksummed_out = [chain_client.to_checksum(t) for t in tokens_out]
            tx_hash = await circle_signer.execute_contract(
                contract_address=vault_address,
                abi_function="rebalance(address[],uint256[],address[],uint256[])",
                abi_params=[checksummed_in, amounts_in, checksummed_out, amounts_out],
            )
            confirmed = await self._confirm_receipt(tx_hash)
            logger.info(f"Rebalance tx via Circle confirmed: {confirmed}")
            return [confirmed]

        # Fallback: raw private key
        account = chain_client.settings.agent_account
        if not account:
            raise RuntimeError("No agent account configured — set CIRCLE_API_KEY or ARC_AGENT_PRIVATE_KEY")

        vault = self.loader.vault(vault_address)
        # Use the pending-block nonce so a quick second rebalance doesn't reuse
        # a nonce that's already in the mempool (reduces nonce-collision drops).
        nonce = await chain_client.w3.eth.get_transaction_count(account.address, "pending")

        tx = await vault.functions.rebalance(tokens_in, amounts_in, tokens_out, amounts_out).build_transaction(
            {
                "from": account.address,
                "nonce": nonce,
                "chainId": chain_client.settings.chain_id,
                "gas": 2_000_000,
                "gasPrice": await chain_client.w3.eth.gas_price,
            }
        )

        signed = account.sign_transaction(tx)
        tx_hash = await chain_client.w3.eth.send_raw_transaction(signed.raw_transaction)

        # Confirm the receipt and raise on revert — don't return a "success"
        # hash for a transaction that failed on-chain.
        confirmed = await self._confirm_receipt(tx_hash)
        logger.info(f"Rebalance tx confirmed: {confirmed}")
        return [confirmed]

    async def create_vault(
        self,
        name: str,
        symbol: str,
        management_fee_bps: int,
        performance_fee_bps: int,
        agent_assisted: bool,
        owner_wallet: str | None = None,
    ) -> str:
        """Deploy a new vault via VaultFactory, then make it NON-CUSTODIAL.

        Uses Circle dev-controlled wallet if configured, falls back to raw private key.

        Non-custodial ownership (T0.2 — re-lands the intent of reverted PR #646):
            The deployed ``VaultFactory.createVault`` has a 5-arg selector and
            constructs the Vault with ``Ownable(msg.sender)`` — i.e. the BACKEND
            SIGNER becomes the vault's owner AND (implicitly) its rebalance
            authority. That is custodial: a single compromised backend key could
            re-point the NAV/slippage oracle and bleed the vault.

            #646 tried to fix this by adding a 6th ``address _vaultOwner`` arg to
            ``createVault``, but the on-chain bytecode only has the 5-arg selector,
            so every call reverted and #646 was reverted in full by #650.

            We re-land the SAME non-custodial outcome WITHOUT touching the deployed
            selector or the Vault constructor: right after creation (while the
            backend signer is still owner) we call ``setAgent(backendAgent)`` and
            then ``transferOwnership(owner_wallet)``. Both functions already exist
            in the live Vault ABI, so nothing can mismatch the deployed bytecode.
            The result is ``owner == depositing user`` and ``agent == backend
            signer`` — the agent keeps rebalance-only authority and can never touch
            the owner-only setters (oracles, slippage cap, pause). See
            ``_apply_non_custodial_ownership`` for the owner-resolution policy
            (user wallet → governance address → fail-loud; NEVER silently the agent).

        Args:
            owner_wallet: The depositing user's wallet, which becomes the vault's
                Ownable owner. Pass the SIWE-verified caller for user-facing
                creation. ``None`` is only valid for backend bootstrap/auto-create,
                where an explicit governance owner is resolved instead (see helper).
        """
        factory = self.loader.vault_factory

        # Circle path
        if circle_signer.is_configured:
            tx_hash = await circle_signer.execute_contract(
                contract_address=chain_client.settings.vault_factory_address,
                abi_function="createVault(string,string,uint16,uint16,bool)",
                abi_params=[name, symbol, management_fee_bps, performance_fee_bps, agent_assisted],
            )
            logger.info(f"Vault created via Circle, tx: {tx_hash}")

            # Wait for confirmation, then find the new vault address
            # Circle's tx_hash is the on-chain hash — parse receipt for VaultCreated event
            receipt = await chain_client.w3.eth.wait_for_transaction_receipt(
                chain_client.w3.to_bytes(hexstr=tx_hash.removeprefix("0x")) if isinstance(tx_hash, str) else tx_hash
            )
            if receipt.get("status") == 0:
                raise VaultCreationRevertedError(f"createVault tx reverted on-chain: {tx_hash}")
            vault_address = self._parse_vault_created(factory, receipt)
            if not vault_address:
                # Tx succeeded but no VaultCreated event was found — fall back
                # to the most recently created vault, but flag it: this masks
                # an event-decoding/indexing issue, not a revert (that's
                # already handled above).
                logger.warning(
                    "createVault tx %s succeeded but no VaultCreated event found; "
                    "falling back to most recent vault from getVaults()",
                    tx_hash,
                )
                all_vaults = await factory.functions.getVaults().call()
                vault_address = all_vaults[-1]
            logger.info(f"Vault created at {vault_address}")

            # Make the vault non-custodial: owner = user (or governance), agent = backend.
            await self._apply_non_custodial_ownership(vault_address, owner_wallet)
            return vault_address

        # Fallback: raw private key
        account = chain_client.settings.agent_account
        if not account:
            raise RuntimeError("No agent account configured — set CIRCLE_API_KEY or ARC_AGENT_PRIVATE_KEY")

        nonce = await chain_client.w3.eth.get_transaction_count(account.address)

        tx = await factory.functions.createVault(
            name, symbol, management_fee_bps, performance_fee_bps, agent_assisted
        ).build_transaction(
            {
                "from": account.address,
                "nonce": nonce,
                "chainId": chain_client.settings.chain_id,
                "gas": 5_000_000,
                "gasPrice": await chain_client.w3.eth.gas_price,
            }
        )

        signed = account.sign_transaction(tx)
        tx_hash = await chain_client.w3.eth.send_raw_transaction(signed.raw_transaction)
        receipt = await chain_client.w3.eth.wait_for_transaction_receipt(tx_hash)
        vault_address = self._parse_vault_created(factory, receipt)

        if not vault_address:
            raise RuntimeError("Failed to extract vault address from deployment receipt")

        logger.info(f"Vault created at {vault_address} in tx {tx_hash.hex()}")

        # Make the vault non-custodial: owner = user (or governance), agent = backend.
        await self._apply_non_custodial_ownership(vault_address, owner_wallet)
        return vault_address

    def _backend_signer_address(self) -> str | None:
        """The EVM address the backend uses to sign vault txs (the agent/creator).

        Circle path: the managed wallet's EVM address, surfaced via WALLET_ADDRESS
        (same env var the bootstrap script already reads for setAgent). Raw-key
        path: the address derived from ARC_AGENT_PRIVATE_KEY. Returns None if it
        can't be determined (Circle configured but WALLET_ADDRESS unset).
        """
        if circle_signer.is_configured:
            addr = os.getenv("WALLET_ADDRESS", "").strip()
            return addr or None
        account = chain_client.settings.agent_account
        return account.address if account else None

    def _resolve_vault_owner(self, owner_wallet: str | None) -> str | None:
        """Decide who should own the vault — and refuse to default to the agent.

        Priority:
          1. ``owner_wallet`` — the depositing user (user-facing creation). Always
             preferred; this is the whole point of non-custodial vaults.
          2. ``ARC_VAULT_GOVERNANCE_ADDRESS`` — an explicit cold/governance key for
             backend bootstrap/auto-create where no user wallet exists. Documented
             in .env.example; intended to be a key DISTINCT from the hot agent
             signer.
          3. ``None`` — neither is available. The caller MUST NOT silently leave the
             vault owned by the agent (that re-creates the custodial drain vector).
             We return None and the helper logs a loud warning + skips the transfer
             rather than mint an agent-owned vault behind the operator's back.
        """
        if owner_wallet and owner_wallet.strip():
            return owner_wallet.strip()
        gov = os.getenv("ARC_VAULT_GOVERNANCE_ADDRESS", "").strip()
        return gov or None

    async def _apply_non_custodial_ownership(self, vault_address: str, owner_wallet: str | None) -> None:
        """Separate the vault's owner from its agent so a compromised agent key
        cannot drain it (T0.2).

        After ``createVault`` the backend signer is the vault's Ownable owner AND
        the de-facto rebalancer. This method, run by that same signer while it is
        still owner:

          1. ``setAgent(backendAgent)`` — pin the rebalance-only authority to the
             backend signer explicitly (it stays the agent after the handoff).
          2. ``transferOwnership(resolvedOwner)`` — hand the owner role (oracles,
             slippage cap, pause, setAgent) to the depositing user, or to an
             explicit governance key for bootstrap. After this, ``owner != agent``.

        Fail-safe: if no non-agent owner can be resolved (no user wallet AND no
        governance address), we DO NOT transfer ownership — but we log a prominent
        warning. Leaving the freshly-created vault owned by the backend signer is
        the status quo (no regression), and refusing to invent an owner avoids
        bricking a vault by transferring it to a wrong address. Operators see the
        warning and set ARC_VAULT_GOVERNANCE_ADDRESS to close the gap.

        On-chain failures here are logged but NOT raised: the vault already exists
        and is usable; a failed ownership handoff is an operational alert, not a
        reason to 500 the create call (and never a reason to leave funds at risk —
        no user funds are in the vault yet at creation time).
        """
        agent_addr = self._backend_signer_address()
        resolved_owner = self._resolve_vault_owner(owner_wallet)

        if not resolved_owner:
            logger.warning(
                "Vault %s created WITHOUT an owner≠agent handoff: no user wallet was "
                "supplied and ARC_VAULT_GOVERNANCE_ADDRESS is unset. The vault is "
                "owned by the backend signer (custodial). Set "
                "ARC_VAULT_GOVERNANCE_ADDRESS (a cold key distinct from the agent) "
                "to make bootstrap/auto-created vaults non-custodial.",
                vault_address,
            )
            return

        if resolved_owner.lower() == (agent_addr or "").lower():
            # Defensive: never hand ownership to the agent address — that is the
            # exact custodial configuration we are removing.
            logger.warning(
                "Refusing to transferOwnership of vault %s to the agent address %s "
                "(owner would equal agent — custodial). Configure a distinct "
                "ARC_VAULT_GOVERNANCE_ADDRESS or pass a user wallet.",
                vault_address,
                resolved_owner,
            )
            return

        try:
            # 1) Pin the agent (rebalance-only authority) to the backend signer.
            if agent_addr:
                await self._send_vault_admin_tx(
                    vault_address,
                    raw_fn_name="setAgent",
                    raw_fn_args=(chain_client.to_checksum(agent_addr),),
                    circle_sig="setAgent(address)",
                    circle_params=[chain_client.to_checksum(agent_addr)],
                )

            # 2) Hand ownership to the user/governance — owner != agent after this.
            await self._send_vault_admin_tx(
                vault_address,
                raw_fn_name="transferOwnership",
                raw_fn_args=(chain_client.to_checksum(resolved_owner),),
                circle_sig="transferOwnership(address)",
                circle_params=[chain_client.to_checksum(resolved_owner)],
            )
            logger.info(
                "Vault %s is now non-custodial: owner=%s, agent=%s",
                vault_address,
                resolved_owner,
                agent_addr,
            )
        except Exception:
            # Non-fatal: the vault exists and holds no funds yet. Surface loudly so
            # an operator can re-run the handoff, but don't fail the create call.
            logger.exception(
                "owner≠agent handoff failed for vault %s (vault created but still "
                "backend-owned — re-run the handoff before users deposit)",
                vault_address,
            )

    async def _send_vault_admin_tx(
        self,
        vault_address: str,
        *,
        raw_fn_name: str,
        raw_fn_args: tuple,
        circle_sig: str,
        circle_params: list,
    ) -> None:
        """Submit a single owner-only admin tx to a vault via whichever signer is
        configured (Circle managed wallet, else raw key), confirming it on-chain.

        Used by the non-custodial ownership handoff for setAgent /
        transferOwnership. Raises (TradeRevertedError) on an on-chain revert so the
        caller can surface a failed handoff.
        """
        if circle_signer.is_configured:
            tx_hash = await circle_signer.execute_contract(
                contract_address=vault_address,
                abi_function=circle_sig,
                abi_params=circle_params,
            )
            await self._confirm_receipt(tx_hash)
            return

        account = chain_client.settings.agent_account
        if not account:
            raise RuntimeError("No agent account configured for vault admin tx")

        vault = self.loader.vault(vault_address)
        nonce = await chain_client.w3.eth.get_transaction_count(account.address, "pending")
        fn = getattr(vault.functions, raw_fn_name)(*raw_fn_args)
        tx = await fn.build_transaction(
            {
                "from": account.address,
                "nonce": nonce,
                "chainId": chain_client.settings.chain_id,
                "gas": 200_000,
                "gasPrice": await chain_client.w3.eth.gas_price,
            }
        )
        signed = account.sign_transaction(tx)
        tx_hash = await chain_client.w3.eth.send_raw_transaction(signed.raw_transaction)
        await self._confirm_receipt(tx_hash)

    async def get_vault_metrics(self, vault_address: str) -> dict:
        """Read vault metrics from on-chain state."""
        vault = self.loader.vault(vault_address)

        try:
            total_assets = await vault.functions.totalAssets().call()
        except Exception:
            # Stale price fallback — compute from holdings + raw oracle prices
            try:
                holdings_data = await vault.functions.getHoldings().call()
                token_addresses = holdings_data[0]
                amounts = holdings_data[1]
                total_assets = await self._safe_total_assets(vault, token_addresses, amounts)
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
        return list(vaults)

    async def get_vault_count(self) -> int:
        factory = self.loader.vault_factory
        return await factory.functions.vaultCount().call()

    async def set_token_oracles(
        self,
        vault_address: str,
        tokens: list[str],
        oracles: list[str],
    ) -> str:
        """Set oracle addresses on a vault for NAV pricing.

        Uses Circle dev-controlled wallet if configured, falls back to raw private key.
        """
        checksummed_tokens = [chain_client.to_checksum(t) for t in tokens]
        checksummed_oracles = [chain_client.to_checksum(o) for o in oracles]

        # Circle path
        if circle_signer.is_configured:
            tx_hash = await circle_signer.execute_contract(
                contract_address=vault_address,
                abi_function="setTokenOracles(address[],address[])",
                abi_params=[checksummed_tokens, checksummed_oracles],
            )
            logger.info(f"setTokenOracles via Circle: {tx_hash} for vault {vault_address}")
            return tx_hash

        # Fallback: raw private key
        account = chain_client.settings.agent_account
        if not account:
            raise RuntimeError("No agent account configured")

        vault = self.loader.vault(vault_address)
        # Use the pending-block nonce so a second raw-key tx submitted in the
        # same tick (e.g. set_target_allocations right after this) doesn't reuse
        # a nonce already sitting in the mempool and silently drop one of them.
        # Matches execute_rebalance / _send_vault_admin_tx in this file.
        nonce = await chain_client.w3.eth.get_transaction_count(account.address, "pending")

        tx = await vault.functions.setTokenOracles(
            checksummed_tokens,
            checksummed_oracles,
        ).build_transaction(
            {
                "from": account.address,
                "nonce": nonce,
                "chainId": chain_client.settings.chain_id,
                "gas": 500_000,
                "gasPrice": await chain_client.w3.eth.gas_price,
            }
        )

        signed = account.sign_transaction(tx)
        tx_hash = await chain_client.w3.eth.send_raw_transaction(signed.raw_transaction)

        logger.info(f"setTokenOracles tx sent: {tx_hash.hex()} for vault {vault_address}")
        return tx_hash.hex()

    async def set_target_allocations(
        self,
        vault_address: str,
        tokens: list[str],
        weights_bps: list[int],
    ) -> str:
        """Set target allocations on a vault via setTargetAllocations.

        Uses Circle dev-controlled wallet if configured, falls back to raw private key.
        """
        checksummed = [chain_client.to_checksum(t) for t in tokens]

        # Circle path
        if circle_signer.is_configured:
            tx_hash = await circle_signer.execute_contract(
                contract_address=vault_address,
                abi_function="setTargetAllocations(address[],uint256[])",
                abi_params=[checksummed, weights_bps],
            )
            logger.info(f"setTargetAllocations via Circle: {tx_hash} for vault {vault_address}")
            return tx_hash

        # Fallback: raw private key
        account = chain_client.settings.agent_account
        if not account:
            raise RuntimeError("No agent account configured — set CIRCLE_API_KEY or ARC_AGENT_PRIVATE_KEY")

        vault = self.loader.vault(vault_address)
        # Pending-block nonce (see set_token_oracles): when _process_vault calls
        # this right after set_token_oracles, the first tx is still in the
        # mempool and "latest" would hand back its nonce, dropping one tx.
        nonce = await chain_client.w3.eth.get_transaction_count(account.address, "pending")

        tx = await vault.functions.setTargetAllocations(
            checksummed,
            weights_bps,
        ).build_transaction(
            {
                "from": account.address,
                "nonce": nonce,
                "chainId": chain_client.settings.chain_id,
                "gas": 500_000,
                "gasPrice": await chain_client.w3.eth.gas_price,
            }
        )

        signed = account.sign_transaction(tx)
        tx_hash = await chain_client.w3.eth.send_raw_transaction(signed.raw_transaction)

        logger.info(f"setTargetAllocations tx sent: {tx_hash.hex()} for vault {vault_address}")
        return tx_hash.hex()

    async def _usdc_value_to_token_raw(
        self,
        token_address: str,
        usdc_value: float,
    ) -> int:
        """Convert a USDC value to raw token amount using oracle price.

        Args:
            token_address: The synth token address.
            usdc_value: Value in USDC units (e.g. 8.0 for $8).

        Returns:
            Raw token amount in the token's native decimals (18 for synths).
        """
        # Check if it's USDC itself
        if token_address.lower() == chain_client.settings.usdc_address.lower():
            return int(usdc_value * 1e6)

        # Look up oracle price for the synth token
        loader = self.loader
        for sym, addr in chain_client.settings.synth_addresses.items():
            if addr and addr.lower() == token_address.lower():
                try:
                    oracle = loader.oracle_for(sym)
                    price_raw = await oracle.functions.price().call()  # 6 decimals
                    price_usd = price_raw / 1e6  # e.g. 592.40 for sSPY
                    if price_usd > 0:
                        token_amount = usdc_value / price_usd
                        return int(token_amount * 1e18)  # synths have 18 decimals
                except Exception as e:
                    logger.warning(f"Oracle price lookup failed for {sym}: {e}")
                    break

        # Fallback: treat as 18-decimal token, estimate at $1
        logger.warning(f"No oracle for {token_address[:10]}, estimating 1:1 USDC for SELL amount")
        return int(usdc_value * 1e18)

    # ─── Helpers ───────────────────────────────────────────────────

    @staticmethod
    def _parse_vault_created(factory, receipt) -> str | None:
        """Extract vault address from VaultCreated event in receipt."""
        for log in receipt.logs:
            try:
                result = factory.events.VaultCreated().process_log(log)
                return result["args"]["vault"]
            except Exception:
                continue
        return None

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

    async def _token_to_usdc(self, token_address: str, amount: int, decimals: int, use_raw_price: bool = False) -> int:
        """Estimate USDC value of a token holding.

        If use_raw_price is True (stale oracle fallback), uses the raw price()
        getter which doesn't check staleness.
        """
        if token_address.lower() == chain_client.settings.usdc_address.lower():
            return amount

        # For synth tokens, use the oracle price
        for sym, addr in chain_client.settings.synth_addresses.items():
            if addr and addr.lower() == token_address.lower():
                try:
                    oracle = self.loader.oracle_for(sym)
                    if use_raw_price:
                        # Use raw price getter (no staleness check) as fallback
                        price = await oracle.functions.price().call()
                    else:
                        price = await oracle.functions.getPrice().call()
                    # USDC value = amount (18 dec) * price (6 dec) / 1e18
                    return (amount * price) // (10**decimals)
                except Exception:
                    pass

        # Fallback: return raw amount
        return amount

    async def _safe_total_assets(
        self,
        vault: AsyncContract,
        token_addresses: list,
        amounts: list,
    ) -> int:
        """Read totalAssets() with stale-price fallback.

        If totalAssets() reverts (e.g. StalePrice), compute NAV off-chain
        using the raw price() getter that doesn't check staleness.
        """
        try:
            return await vault.functions.totalAssets().call()
        except Exception:
            # Fallback: compute off-chain using raw prices
            logging.getLogger(__name__).warning("totalAssets() reverted — computing NAV off-chain with raw prices")
            usdc_address = chain_client.settings.usdc_address
            nav = 0
            for i in range(len(token_addresses)):
                amount = amounts[i]
                if amount == 0:
                    continue
                token_addr = token_addresses[i]
                if token_addr.lower() == usdc_address.lower():
                    nav += amount
                else:
                    # Use raw price() (no staleness check)
                    decimals = await self._get_token_decimals(token_addr)
                    value = await self._token_to_usdc(token_addr, amount, decimals, use_raw_price=True)
                    nav += value
            return nav


# Singleton
chain_executor = ChainExecutor()
