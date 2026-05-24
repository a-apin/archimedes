"""Strategy runner — paper-grounded strategies drive portfolio decisions.

This IS the intelligence layer. No heuristic regime detector, no equal-weight
guessing — each strategy's published signal rule (SMA200 cross, vol-targeting,
12-month momentum) is evaluated against live market data to produce allocation
signals. Signals are aggregated into target weights, then the executor
rebalances vaults and publishes reasoning trace hashes on-chain.

Run as a standalone process:
    python -m archimedes.chain.agent_runner

Env:
    AGENT_INTERVAL_SECONDS  — tick interval in seconds (default: 300 = 5 min)
    AGENT_DRY_RUN           — if "true", skip on-chain execution (default: false)
    AGENT_VAULT_ADDRESSES   — comma-separated vault addresses to manage
    AGENT_USDC_FLOOR        — minimum USDC allocation, 0.0–1.0 (default: 0.20)
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import uuid
from datetime import UTC, datetime

from archimedes.chain.client import chain_client
from archimedes.chain.executor import chain_executor
from archimedes.chain.trace_publisher import trace_publisher
from archimedes.chain.v_check import VCheck
from archimedes.models.portfolio import (
    Portfolio,
    TargetAllocation,
    TradeDirection,
    TradeOrder,
)
from archimedes.models.trace import DecisionType, ReasoningTrace
from archimedes.services.redis_state import AgentStateStore
from archimedes.services.strategy_provider import default_provider
from archimedes.services.strategy_signal_evaluator import (
    StrategySignals,
    strategy_evaluator,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)

INTERVAL = int(os.getenv("AGENT_INTERVAL_SECONDS", "300"))
DRY_RUN = os.getenv("AGENT_DRY_RUN", "false").lower() == "true"
EXPLICIT_VAULTS = os.getenv("AGENT_VAULT_ADDRESSES", "")
USDC_FLOOR = float(os.getenv("AGENT_USDC_FLOOR", "0.20"))

# Drift threshold for rebalance trigger
_DRIFT_THRESHOLD = 0.15


class StrategyRunner:
    """Paper-strategy-driven portfolio runner.

    One tick:
      1. Load strategies from LocalStrategyProvider
      2. Fetch live price histories (1y daily, from yfinance)
      3. Evaluate each strategy's signal rule against prices
      4. Aggregate signals into target portfolio weights
      5. For each managed vault:
         a. Read current portfolio from on-chain
         b. Diff current vs target → trades
         c. Execute if drift > threshold
         d. Publish reasoning trace hash on Arc
      6. Persist heartbeat + state to Redis
    """

    def __init__(self) -> None:
        self.provider = default_provider()
        self.state = AgentStateStore()
        self._synth_addrs = chain_client.settings.synth_addresses
        self._usdc_addr = chain_client.settings.usdc_address
        self._known_vaults: set[str] = set()  # Vaults we've already seen

    async def tick(self) -> None:
        """Run one full strategy evaluation cycle."""
        tick_id = uuid.uuid4().hex[:8]
        logger.info("═══ Strategy tick %s ═══", tick_id)

        try:
            # 1. Load strategies
            strategies = self.provider.list_strategies()
            if not strategies:
                logger.warning("[tick %s] No strategies loaded — sleeping", tick_id)
                return

            logger.info(
                "[tick %s] Loaded %d strategies: %s",
                tick_id,
                len(strategies),
                ", ".join(s.paper_title[:30] for s in strategies),
            )

            # 2. Evaluate strategy signals against live market data
            synth_assets = [sym for sym, addr in self._synth_addrs.items() if addr]

            # Run signal evaluation in thread pool (yfinance is sync)
            all_signals: list[StrategySignals] = await asyncio.to_thread(
                strategy_evaluator.evaluate_strategies,
                strategies,
                synth_assets,
            )

            if not all_signals:
                logger.warning("[tick %s] No signals produced — sleeping", tick_id)
                return

            # Log each strategy's signals
            for ss in all_signals:
                logger.info(
                    "[tick %s] %s: %s",
                    tick_id,
                    ss.paper_title[:35],
                    " | ".join(f"{s.asset}={s.signal.value}({s.weight:.0%})" for s in ss.signals),
                )

            # 3. Aggregate signals into target weights
            target_weights = strategy_evaluator.aggregate_signals(
                all_signals,
                usdc_floor=USDC_FLOOR,
            )
            logger.info(
                "[tick %s] Target weights: %s",
                tick_id,
                " | ".join(f"{k}={v:.0%}" for k, v in target_weights.items()),
            )

            # Derive regime from strategy consensus (how many say flat)
            flat_count = sum(1 for ss in all_signals for s in ss.signals if s.signal.value == "flat")
            total_count = sum(len(ss.signals) for ss in all_signals)
            flat_pct = flat_count / total_count if total_count > 0 else 0

            if flat_pct > 0.6:
                regime = "risk_off"
            elif flat_pct > 0.3:
                regime = "transition"
            else:
                regime = "risk_on"

            # Save regime to Redis
            await self.state.save_regime_from_values(regime, flat_pct, all_signals)

            # 4. Build target allocations
            targets = self._weights_to_targets(target_weights, all_signals)

            # 5. Get managed vaults (polling VaultFactory discovers new vaults)
            vaults = await self._get_managed_vaults()

            # 5a. Discover new vaults from VaultFactory and add to state
            new_vaults = await self._discover_new_vaults()
            if new_vaults:
                logger.info(
                    "[tick %s] Discovered %d new vault(s): %s",
                    tick_id,
                    len(new_vaults),
                    ", ".join(v[:10] for v in new_vaults),
                )
                # Merge discovered vaults into the managed set
                vaults = list(set(vaults) | set(new_vaults))

            if not vaults:
                logger.warning("[tick %s] No vaults available — sleeping", tick_id)
                return

            logger.info("[tick %s] Managing %d vaults", tick_id, len(vaults))

            # 6. Process each vault
            for vault_addr in vaults:
                try:
                    await self._process_vault(
                        vault_addr,
                        targets,
                        all_signals,
                        regime,
                        tick_id,
                    )
                except Exception:
                    logger.exception(
                        "[tick %s] Error processing vault %s — continuing",
                        tick_id,
                        vault_addr,
                    )

            # 7. Heartbeat
            await self.state.save_heartbeat()

        except Exception:
            logger.exception("[tick %s] Strategy tick failed — will retry", tick_id)

    async def _process_vault(
        self,
        vault_address: str,
        targets: list[TargetAllocation],
        all_signals: list[StrategySignals],
        regime: str,
        tick_id: str,
    ) -> None:
        """Process a single vault: read portfolio → diff → maybe trade → publish trace."""
        logger.info("[tick %s] Processing vault %s", tick_id, vault_address[:10])

        # Read current portfolio
        try:
            portfolio = await chain_executor.read_portfolio(vault_address)
        except Exception as e:
            logger.warning(
                "[tick %s] Cannot read portfolio for %s: %s — skipping",
                tick_id,
                vault_address[:10],
                e,
            )
            return

        logger.info(
            "[tick %s] Vault %s: AUM=$%.2f, %d holdings",
            tick_id,
            vault_address[:10],
            portfolio.total_value_usdc,
            len(portfolio.holdings),
        )

        # Skip empty vaults (don't spam traces — just log)
        if portfolio.total_value_usdc <= 0 and not portfolio.holdings:
            logger.info("[tick %s] Vault %s is empty — needs deposit", tick_id, vault_address[:10])
            # Only publish a trace if we haven't already for this vault
            last_trace = await self.state.get_last_trace(vault_address)
            if not last_trace or last_trace.get("trigger") != "empty_vault":
                await self._publish_trace(
                    vault_address,
                    DecisionType.SKIP,
                    "empty_vault",
                    portfolio,
                    [],
                    all_signals,
                    regime,
                    tick_id,
                    "Vault is empty — awaiting initial deposit.",
                )
            return

        # Set oracle addresses for NAV pricing (so totalAssets() prices all holdings)
        try:
            oracle_tokens = []
            oracle_addrs = []
            for t in targets:
                if t.weight > 0 and t.token_address:
                    symbol = t.symbol
                    if symbol == "USDC":
                        continue
                    oracle_addr = chain_client.settings.oracle_addresses.get(symbol)
                    if oracle_addr:
                        oracle_tokens.append(t.token_address)
                        oracle_addrs.append(oracle_addr)

            if oracle_tokens:
                await chain_executor.set_token_oracles(
                    vault_address,
                    oracle_tokens,
                    oracle_addrs,
                )
                logger.info(
                    "[tick %s] Set %d token oracles on vault %s",
                    tick_id,
                    len(oracle_tokens),
                    vault_address[:10],
                )
        except Exception as e:
            logger.warning(
                "[tick %s] Failed to set token oracles on %s: %s",
                tick_id,
                vault_address[:10],
                e,
            )

        # Set target allocations on the vault first (needed for rebalance)
        try:
            alloc_tokens = []
            alloc_weights = []
            for t in targets:
                if t.weight > 0 and t.token_address:
                    alloc_tokens.append(t.token_address)
                    alloc_weights.append(int(t.weight * 10000))  # BPS

            if alloc_tokens:
                # Normalize to exactly 10000 BPS
                total_bps = sum(alloc_weights)
                if total_bps > 0 and total_bps != 10000:
                    scale = 10000 / total_bps
                    alloc_weights = [int(round(w * scale)) for w in alloc_weights]
                    # Fix rounding residue
                    diff = 10000 - sum(alloc_weights)
                    if diff != 0 and alloc_weights:
                        alloc_weights[0] += diff

                await chain_executor.set_target_allocations(
                    vault_address,
                    alloc_tokens,
                    alloc_weights,
                )
                logger.info(
                    "[tick %s] Set target allocations on vault %s",
                    tick_id,
                    vault_address[:10],
                )
        except Exception as e:
            logger.warning(
                "[tick %s] Failed to set allocations on %s: %s",
                tick_id,
                vault_address[:10],
                e,
            )
            # Continue anyway — try rebalance with existing allocations

        trades = self._compute_trades(portfolio, targets)

        if not trades:
            logger.info("[tick %s] No drift — portfolio aligned with strategy signals", tick_id)
            await self._publish_trace(
                vault_address,
                DecisionType.SKIP,
                "aligned",
                portfolio,
                [],
                all_signals,
                regime,
                tick_id,
                "Portfolio aligned with strategy signals. No rebalance needed.",
            )
            return

        # Log the trade plan
        logger.info(
            "[tick %s] REBALANCE vault %s: %d trades (regime=%s)",
            tick_id,
            vault_address[:10],
            len(trades),
            regime,
        )
        for t in trades:
            logger.info(
                "[tick %s]   %s %s ~$%.0f",
                tick_id,
                t.direction.value,
                t.symbol,
                t.estimated_usdc_value,
            )

        # ── V_check — Xia et al. 2026 § 5 Reasoning I/O contract ───
        # Deterministic validity gate: if weights are invalid, SKIP the
        # entire rebalance regardless of LLM confidence.
        alloc_weights_bps: dict[str, int] = {}
        for t in targets:
            if t.weight > 0 and t.token_address:
                alloc_weights_bps[t.symbol] = int(round(t.weight * 10000))
        v_check = VCheck(weights_bps=alloc_weights_bps)
        v_result = v_check.run()
        if not v_result.passed:
            logger.warning(
                "[tick %s] V_check FAILED for vault %s: %s — skipping rebalance",
                tick_id,
                vault_address[:10],
                "; ".join(v_result.failures),
            )
            await self._publish_trace(
                vault_address,
                DecisionType.SKIP,
                "v_check_failed",
                portfolio,
                [],
                all_signals,
                regime,
                tick_id,
                f"V_check rejected: {'; '.join(v_result.failures)}",
            )
            return

        # ── Commit-Reveal Flow ────────────────────────────────────
        # Phase 1: COMMIT — compute hash and anchor on-chain BEFORE trade
        reasoning = self._build_reasoning(all_signals, regime, trades)
        commit_tx = None
        commit_block = None

        if not DRY_RUN:
            commit_tx, commit_block = await self._commit_trace(
                vault_address,
                trades,
                all_signals,
                regime,
                tick_id,
                reasoning,
                portfolio,
            )

        # Phase 2: TRADE — execute the rebalance
        if DRY_RUN:
            logger.info("[tick %s] DRY RUN — skipping on-chain execution", tick_id)
            tx_hashes: list[str] = []
            trade_block = None
        else:
            try:
                tx_hashes = await chain_executor.execute_trades(vault_address, trades)
                # Get trade block number from first tx
                trade_block = None
                if tx_hashes:
                    try:
                        receipt = await chain_client.w3.eth.get_transaction_receipt(
                            chain_client.w3.to_bytes(hexstr=tx_hashes[0].removeprefix("0x"))
                        )
                        trade_block = receipt.blockNumber
                    except Exception:
                        pass
                logger.info(
                    "[tick %s] Executed %d trades: %s",
                    tick_id,
                    len(tx_hashes),
                    [h[:16] for h in tx_hashes],
                )
                await self.state.save_last_rebalance(vault_address)
            except Exception as e:
                logger.error(
                    "[tick %s] Trade execution FAILED for %s: %s",
                    tick_id,
                    vault_address[:10],
                    e,
                )
                await self._publish_trace(
                    vault_address,
                    DecisionType.SKIP,
                    "execution_failed",
                    portfolio,
                    [],
                    all_signals,
                    regime,
                    tick_id,
                    f"Execution failed: {e}",
                    commit_tx=commit_tx,
                    commit_block=commit_block,
                    error=str(e),
                )
                return

        # Phase 3: REVEAL — publish full trace with all data AFTER trade settles
        await self._reveal_trace(
            vault_address,
            DecisionType.REBALANCE,
            "strategy_signal_drift",
            portfolio,
            trades,
            all_signals,
            regime,
            tick_id,
            reasoning,
            tx_hashes,
            commit_tx=commit_tx,
            commit_block=commit_block,
            trade_block=trade_block,
        )

    # ─── Signal → target allocation ───────────────────────────────

    def _weights_to_targets(
        self, weights: dict[str, float], all_signals: list[StrategySignals] | None = None
    ) -> list[TargetAllocation]:
        """Convert weight dict → TargetAllocation list."""
        # Build symbol → strategy_ids map from signals
        symbol_strategies: dict[str, list[str]] = {}
        if all_signals:
            for ss in all_signals:
                for sig in ss.signals:
                    symbol_strategies.setdefault(sig.asset, []).append(ss.strategy_id)

        targets: list[TargetAllocation] = []
        for symbol, weight in weights.items():
            token_address = self._usdc_addr if symbol == "USDC" else self._synth_addrs.get(symbol, "")

            targets.append(
                TargetAllocation(
                    symbol=symbol,
                    token_address=token_address,
                    weight=weight,
                    strategy_ids=symbol_strategies.get(symbol, []),
                )
            )
        return targets

    # ─── Trade computation ─────────────────────────────────────────

    def _compute_trades(
        self,
        portfolio: Portfolio,
        targets: list[TargetAllocation],
    ) -> list[TradeOrder]:
        """Diff current portfolio vs target weights → trade list."""
        current_weights = portfolio.weights_dict
        target_map = {t.symbol: t for t in targets}

        trades: list[TradeOrder] = []
        all_symbols = set(target_map.keys()) | set(current_weights.keys())

        for sym in all_symbols:
            current_w = current_weights.get(sym, 0.0)
            target = target_map.get(sym)
            target_w = target.weight if target else 0.0
            token_addr = target.token_address if target else ""

            drift = target_w - current_w
            if abs(drift) < _DRIFT_THRESHOLD:
                continue

            usdc_value = abs(drift) * portfolio.total_value_usdc
            direction = TradeDirection.BUY if drift > 0 else TradeDirection.SELL

            trades.append(
                TradeOrder(
                    symbol=sym,
                    token_address=token_addr,
                    direction=direction,
                    amount=round(usdc_value, 6),
                    estimated_usdc_value=round(usdc_value, 2),
                )
            )

        return trades

    # ─── Commit-Reveal Trace ────────────────────────────────────

    async def _commit_trace(
        self,
        vault_address: str,
        trades: list[TradeOrder],
        all_signals: list[StrategySignals],
        regime: str,
        tick_id: str,
        reasoning: str,
        portfolio: Portfolio,
    ) -> tuple[str | None, int | None]:
        """Commit phase: publish trace hash on-chain BEFORE the trade executes.

        Returns (commit_tx_hash, commit_block_number) or (None, None) on failure.
        """
        trace = ReasoningTrace(
            id=str(uuid.uuid4()),
            vault_address=vault_address,
            decision_type=DecisionType.REBALANCE,
            trigger="commit_phase",
            timestamp=datetime.now(UTC),
            market_context={
                "regime": regime,
                "strategy_count": len(all_signals),
                "phase": "commit",
            },
            portfolio_before={
                "vault": vault_address[:10],
                "aum_usdc": portfolio.total_value_usdc,
                "holdings": {
                    h.symbol: {"weight": f"{h.weight:.1%}", "value_usdc": h.value_usdc} for h in portfolio.holdings
                },
            },
            reasoning=f"[COMMIT] {reasoning}",
            confidence=1.0
            - (
                sum(1 for ss in all_signals for s in ss.signals if s.signal.value == "flat")
                / max(sum(len(ss.signals) for ss in all_signals), 1)
            ),
            trades_executed=[
                {"symbol": t.symbol, "direction": t.direction.value, "amount": t.amount, "phase": "intended"}
                for t in trades
            ],
            strategies_referenced=[ss.strategy_id for ss in all_signals],
            consulted_paper_hashes=[],
        )

        trace.compute_hash()
        logger.info("[tick %s] COMMIT hash: %s", tick_id, trace.trace_hash[:16])

        try:
            arc_tx = await trace_publisher.publish(trace)
            if arc_tx:
                # Get block number from commit tx
                try:
                    receipt = await chain_client.w3.eth.get_transaction_receipt(
                        chain_client.w3.to_bytes(hexstr=arc_tx.removeprefix("0x"))
                    )
                    block_num = receipt.blockNumber
                    logger.info(
                        "[tick %s] COMMIT anchored: tx=%s block=%d",
                        tick_id,
                        arc_tx[:16],
                        block_num,
                    )
                    return arc_tx, block_num
                except Exception as e:
                    logger.warning("[tick %s] Cannot get commit block: %s", tick_id, e)
                    return arc_tx, None
            return None, None
        except Exception as e:
            logger.error("[tick %s] COMMIT publish FAILED: %s", tick_id, e)
            return None, None

    async def _reveal_trace(
        self,
        vault_address: str,
        decision_type: DecisionType,
        trigger: str,
        portfolio: Portfolio,
        trades: list[TradeOrder],
        all_signals: list[StrategySignals],
        regime: str,
        tick_id: str,
        reasoning: str,
        tx_hashes: list[str] | None = None,
        commit_tx: str | None = None,
        commit_block: int | None = None,
        trade_block: int | None = None,
    ) -> None:
        """Reveal phase: publish full trace AFTER the trade settles.

        Records commit/reveal/trade block numbers for temporal binding verification.
        """
        trace = ReasoningTrace(
            id=str(uuid.uuid4()),
            vault_address=vault_address,
            decision_type=decision_type,
            trigger=trigger,
            timestamp=datetime.now(UTC),
            market_context={
                "regime": regime,
                "strategy_count": len(all_signals),
                "signal_summary": {
                    ss.paper_title[:30]: {s.asset: f"{s.signal.value}({s.weight:.0%})" for s in ss.signals[:5]}
                    for ss in all_signals
                },
                "phase": "reveal",
            },
            portfolio_before={
                "vault": vault_address[:10],
                "aum_usdc": portfolio.total_value_usdc,
                "holdings": {
                    h.symbol: {"weight": f"{h.weight:.1%}", "value_usdc": h.value_usdc} for h in portfolio.holdings
                },
            },
            portfolio_after={"tx_hashes": tx_hashes or []},
            reasoning=f"[REVEAL] {reasoning}",
            confidence=1.0
            - (
                sum(1 for ss in all_signals for s in ss.signals if s.signal.value == "flat")
                / max(sum(len(ss.signals) for ss in all_signals), 1)
            ),
            trades_executed=[{"symbol": t.symbol, "direction": t.direction.value, "amount": t.amount} for t in trades],
            strategies_referenced=[ss.strategy_id for ss in all_signals],
            consulted_paper_hashes=[],
            # Commit-reveal temporal binding
            commit_tx_hash=commit_tx,
            commit_block_number=commit_block,
            trade_tx_hash=tx_hashes[0] if tx_hashes else None,
            trade_block_number=trade_block,
        )

        trace.compute_hash()
        logger.info("[tick %s] REVEAL hash: %s", tick_id, trace.trace_hash[:16])

        # Publish reveal trace on-chain
        reveal_tx = None
        reveal_block = None
        if not DRY_RUN:
            try:
                reveal_tx = await trace_publisher.publish(trace)
                if reveal_tx:
                    try:
                        receipt = await chain_client.w3.eth.get_transaction_receipt(
                            chain_client.w3.to_bytes(hexstr=reveal_tx.removeprefix("0x"))
                        )
                        reveal_block = receipt.blockNumber
                    except Exception:
                        pass
                    logger.info(
                        "[tick %s] REVEAL anchored: tx=%s block=%s",
                        tick_id,
                        reveal_tx[:16],
                        reveal_block,
                    )
            except Exception as e:
                logger.error("[tick %s] REVEAL publish FAILED: %s", tick_id, e)

        # Persist off-chain with full temporal binding data
        try:
            off_chain_data = {
                "id": trace.id,
                "vault_address": trace.vault_address,
                "decision_type": trace.decision_type.value,
                "trigger": trace.trigger,
                "timestamp": trace.timestamp.isoformat(),
                "market_context": trace.market_context,
                "portfolio_before": trace.portfolio_before,
                "portfolio_after": trace.portfolio_after,
                "reasoning": trace.reasoning,
                "confidence": trace.confidence,
                "trades_executed": trace.trades_executed,
                "strategies_referenced": trace.strategies_referenced,
                "trace_hash": trace.trace_hash,
                "arc_tx_hash": reveal_tx,
                "is_verified": reveal_tx is not None,
                # Temporal binding fields
                "commit_tx_hash": commit_tx,
                "commit_block_number": commit_block,
                "reveal_tx_hash": reveal_tx,
                "reveal_block_number": reveal_block,
                "trade_tx_hash": tx_hashes[0] if tx_hashes else None,
                "trade_block_number": trade_block,
                "temporal_binding_valid": (
                    commit_block is not None and trade_block is not None and commit_block < trade_block
                ),
            }
            await self.state.save_trace(off_chain_data)
        except Exception as e:
            logger.error("[tick %s] REVEAL Redis save FAILED: %s", tick_id, e)

    # ─── Reasoning trace (legacy) ──────────────────────────────────

    async def _publish_trace(
        self,
        vault_address: str,
        decision_type: DecisionType,
        trigger: str,
        portfolio: Portfolio,
        trades: list[TradeOrder],
        all_signals: list[StrategySignals],
        regime: str,
        tick_id: str,
        reasoning: str,
        tx_hashes: list[str] | None = None,
        error: str | None = None,
        commit_tx: str | None = None,
        commit_block: int | None = None,
    ) -> None:
        """Build and publish a reasoning trace."""
        trace = ReasoningTrace(
            id=str(uuid.uuid4()),
            vault_address=vault_address,
            decision_type=decision_type,
            trigger=trigger,
            timestamp=datetime.now(UTC),
            market_context={
                "regime": regime,
                "strategy_count": len(all_signals),
                "signal_summary": {
                    ss.paper_title[:30]: {s.asset: f"{s.signal.value}({s.weight:.0%})" for s in ss.signals[:5]}
                    for ss in all_signals
                },
            },
            portfolio_before={
                "vault": vault_address[:10],
                "aum_usdc": portfolio.total_value_usdc,
                "holdings": {
                    h.symbol: {"weight": f"{h.weight:.1%}", "value_usdc": h.value_usdc} for h in portfolio.holdings
                },
            },
            portfolio_after={"tx_hashes": tx_hashes or []},
            reasoning=reasoning + (f" ERROR: {error}" if error else ""),
            confidence=1.0
            - (
                sum(1 for ss in all_signals for s in ss.signals if s.signal.value == "flat")
                / max(sum(len(ss.signals) for ss in all_signals), 1)
            ),
            trades_executed=[{"symbol": t.symbol, "direction": t.direction.value, "amount": t.amount} for t in trades],
            strategies_referenced=[ss.strategy_id for ss in all_signals],
            consulted_paper_hashes=[],
        )

        trace.compute_hash()
        logger.info("[tick %s] Trace hash: %s", tick_id, trace.trace_hash[:16])

        if not DRY_RUN:
            try:
                arc_tx = await trace_publisher.publish(trace)
                if arc_tx:
                    logger.info("[tick %s] Trace anchored on-chain: %s", tick_id, arc_tx[:16])
            except Exception as e:
                logger.error("[tick %s] Trace publish FAILED: %s", tick_id, e)
                arc_tx = None
        else:
            arc_tx = None

        # Persist off-chain to Redis (always, even in dry-run)
        try:
            off_chain_data = {
                "id": trace.id,
                "vault_address": trace.vault_address,
                "decision_type": trace.decision_type.value,
                "trigger": trace.trigger,
                "timestamp": trace.timestamp.isoformat(),
                "market_context": trace.market_context,
                "portfolio_before": trace.portfolio_before,
                "portfolio_after": trace.portfolio_after,
                "reasoning": trace.reasoning,
                "confidence": trace.confidence,
                "trades_executed": trace.trades_executed,
                "strategies_referenced": trace.strategies_referenced,
                "trace_hash": trace.trace_hash,
                "arc_tx_hash": arc_tx,
                "is_verified": arc_tx is not None,
                # Commit-reveal fields (if available)
                "commit_tx_hash": commit_tx,
                "commit_block_number": commit_block,
            }
            await self.state.save_trace(off_chain_data)
        except Exception as e:
            logger.error("[tick %s] Trace Redis save FAILED: %s", tick_id, e)

    # ─── Reasoning builder ─────────────────────────────────────────

    def _build_reasoning(
        self,
        all_signals: list[StrategySignals],
        regime: str,
        trades: list[TradeOrder],
    ) -> str:
        """Build human-readable reasoning from strategy signals."""
        parts = [f"Regime: {regime} (derived from strategy consensus)"]
        for ss in all_signals:
            signal_strs = [f"{s.asset}={s.signal.value}" for s in ss.signals[:4]]
            parts.append(f"{ss.paper_title[:35]}: {', '.join(signal_strs)}")
        parts.append(f"Trades: {len(trades)}")
        return ". ".join(parts)

    # ─── Vault discovery ───────────────────────────────────────────

    async def _get_managed_vaults(self) -> list[str]:
        """Get vault addresses this runner manages."""
        if EXPLICIT_VAULTS:
            return [v.strip() for v in EXPLICIT_VAULTS.split(",") if v.strip()]

        try:
            vaults = await chain_executor.get_all_vaults()
            if vaults:
                # Update known set
                self._known_vaults = set(vaults)
                return vaults
        except Exception as e:
            logger.warning("Cannot fetch vaults from factory: %s", e)
            return []

        # Auto-create default vault if none exist
        if DRY_RUN:
            logger.info("DRY RUN — would auto-create default vault")
            return []

        logger.info("No vaults found — auto-creating default Tier 1 vault…")
        try:
            vault_address = await chain_executor.create_vault(
                name="Archimedes Momentum",
                symbol="vMOMENTUM",
                management_fee_bps=100,
                performance_fee_bps=1500,
                agent_assisted=True,
            )
            logger.info("Default vault created at %s", vault_address)
            self._known_vaults.add(vault_address)
            return [vault_address]
        except Exception as e:
            logger.error("Failed to auto-create default vault: %s", e)
            return []

    async def _discover_new_vaults(self) -> list[str]:
        """Poll VaultFactory.getAllVaults() and return vaults not yet in _known_vaults.

        Called once per tick. Newly discovered vaults are added to _known_vaults
        so they won't be reported again. This allows user-created vaults (via
        CreateVaultModal on the frontend) to be picked up within one tick interval.
        """
        try:
            all_vaults = await chain_executor.get_all_vaults()
        except Exception as e:
            logger.debug("Cannot poll VaultFactory: %s", e)
            return []

        # If EXPLICIT_VAULTS is set, skip auto-discovery
        if EXPLICIT_VAULTS:
            return []

        current = set(chain_client.to_checksum(v) for v in all_vaults)
        known = set(chain_client.to_checksum(v) for v in self._known_vaults)
        new = current - known

        if new:
            for v in new:
                logger.info("Discovered new vault 0x%s…", v[:10])
            self._known_vaults = current
            return list(new)

        # Keep known_vaults in sync even if nothing new
        self._known_vaults = current
        return []


async def run() -> None:
    """Main strategy runner loop."""
    logger.info("Archimedes Strategy Runner starting")
    logger.info("  interval: %ds", INTERVAL)
    logger.info("  dry_run: %s", DRY_RUN)
    logger.info("  usdc_floor: %.0f%%", USDC_FLOOR * 100)
    logger.info("  chain_connected: %s", await chain_client.is_connected())

    runner = StrategyRunner()

    while True:
        await runner.tick()
        await runner.state.save_heartbeat()
        logger.info("Sleeping %ds until next tick", INTERVAL)
        await asyncio.sleep(INTERVAL)


if __name__ == "__main__":
    asyncio.run(run())
