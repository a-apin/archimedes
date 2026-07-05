"""MarketService — in-process publisher loops + subscriber handlers (monolith).

Replaces the container-per-agent model. Reuses the working rebalance path
from agent_runner (aggregate_signals -> read_portfolio -> compute_trades ->
execute_trades). NO Docker, NO webhook HTTP between agents.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import time
from datetime import UTC, datetime
from dataclasses import asdict, dataclass, field

from sqlalchemy.exc import IntegrityError

from archimedes.chain.circle_signer import circle_signer
from archimedes.chain.client import chain_client
from archimedes.chain.executor import chain_executor
from archimedes.chain.oracle_updater import OracleUpdater
from archimedes.chain.v_check import VCheck
from archimedes.db import get_session
from archimedes.interfaces.math import IRegimeDetector
from archimedes.marketplace import payments
from archimedes.marketplace.state import MarketState
from archimedes.marketplace.settlement import SettlementSweeper
from archimedes.marketplace.tick_registry import (
    HaltSource,
    PIPELINE_STEPS,
    SubscriberTickRecord,
    TickStep,
)
from archimedes.models.marketplace import MarketplaceAgent, SettlementIntent, SubscriberLiability, SubscriberTickLog
from archimedes.models.portfolio import Portfolio, RiskProfile, TargetAllocation, TradeDirection, TradeOrder
from archimedes.models.regime import EnsembleConsensus, RegimeClassification
from archimedes.services.gmm_regime_detector import GmmRegimeDetector
from archimedes.services.portfolio_constructor import PortfolioConstructor
from archimedes.services.strategy_provider import default_provider
from archimedes.services.strategy_signal_evaluator import (
    StrategySignals,
    strategy_evaluator,
)
from archimedes.services.vix_regime_detector import VixRegimeDetector

logger = logging.getLogger(__name__)

_DRIFT_THRESHOLD = 0.15
_USDC_FLOOR = float(os.getenv("AGENT_USDC_FLOOR", "0.20"))
FLAT_FEE_PER_ACTION = int(os.getenv("FLAT_FEE_PER_ACTION", "100"))  # raw 6-dec USDC
CHARGE_BATCH_SIZE = int(os.getenv("CHARGE_BATCH_SIZE", "10"))  # concurrent Circle signing calls per batch
_MARKET_REGIME_UNKNOWN = "unknown"

# Per-publisher ensemble-consensus key prefix (namespaced by strategy_id)
_KEY_ENSEMBLE_CONSENSUS_PREFIX = "archimedes:ensemble_consensus:publisher:"


def _regime_classification_from_cache(cached: dict) -> RegimeClassification | None:
    """Rebuild a RegimeClassification from a cached Redis dict.

    Returns None if the cached dict lacks required fields.
    """
    from datetime import datetime

    from archimedes.models.regime import Regime, RegimeSignals

    try:
        signals = RegimeSignals(
            vix_level=cached.get("vix", 0.0),
            vix_rate_of_change=0.0,
            sp500_above_ma50=cached.get("sp500_above_ma50", False),
            sp500_above_ma200=cached.get("sp500_above_ma200", False),
        )
        return RegimeClassification(
            regime=Regime(cached["regime"]),
            confidence=cached.get("confidence", 0.0),
            signals=signals,
            timestamp=datetime.fromisoformat(cached["timestamp"]),
            regime_changed=cached.get("regime_changed", False),
        )
    except (KeyError, ValueError, TypeError) as exc:
        logger.warning("Failed to rebuild regime from cache: %s", exc)
        return None


def compute_trades(
    portfolio: Portfolio,
    target_weights: dict[str, float],
    token_addresses: dict[str, str] | None = None,
) -> list[TradeOrder]:
    """PORT of StrategyRunner._compute_trades (agent_runner.py:806).

    Diff current portfolio vs target weights → trade list.
    token_addresses maps symbol → checksummed contract address (includes USDC).
    """
    addr_map = token_addresses or {}
    current_weights = portfolio.weights_dict
    target_map = {
        sym: TargetAllocation(symbol=sym, weight=w, token_address=addr_map.get(sym, ""))
        for sym, w in target_weights.items()
    }

    trades: list[TradeOrder] = []
    all_symbols = set(target_map.keys()) | set(current_weights.keys())

    for sym in all_symbols:
        current_w = current_weights.get(sym, 0.0)
        target = target_map.get(sym)
        target_w = target.weight if target else 0.0
        token_addr = target.token_address if target else ""

        # Skip unresolved symbols — no address means we cannot trade them
        if not token_addr and sym != "USDC":
            logger.warning("no token address for %s; skipping", sym)
            continue

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


@dataclass
class Subscriber:
    sub_id: str  # 0x-hex
    pool_id: str  # 0x-hex
    vault_address: str
    ephemeral_wallet: str
    subscriber_wallet: str
    active: bool = True
    circle_wallet_id: str = ""  # Circle Developer-Controlled Wallet UUID for x402 signing


@dataclass
class Publisher:
    strategy_id: str
    pool_id: str
    vault_address: str
    creator_wallet: str
    gateway_seller_address: str = ""
    agent_wallet_id: str = ""
    subscribers: dict[str, Subscriber] = field(default_factory=dict)
    task: asyncio.Task | None = None
    retired: bool = False


@dataclass
class _StepResult:
    halted: bool = False
    reason: str | None = None


@dataclass
class _TickCtx:
    strategy: object | None = None
    synth_assets: list = field(default_factory=list)
    all_signals: list = field(default_factory=list)
    target_weights: dict = field(default_factory=dict)
    consensus: object | None = None
    regime_lock_held: bool = False
    regime_classification: object | None = None
    market_regime: str = _MARKET_REGIME_UNKNOWN
    allocations: list = field(default_factory=list)
    targets: list = field(default_factory=list)
    portfolio: object | None = None
    trades: list = field(default_factory=list)
    action_count: int = 0


class MarketService:
    """In-process marketplace engine.

    Owns publisher loops + subscriber handlers, reuses the real rebalance
    path, charges on-chain, and fans out via in-process + Redis.
    """

    def __init__(self, interval_seconds: int = 300, payments_dry_run: bool = False, paper_trading: bool = True):
        self.settings = chain_client.settings
        self.signer = circle_signer
        self.executor = chain_executor
        self.loader = chain_executor.loader
        self.state = MarketState()
        self.provider = default_provider()
        self.interval = interval_seconds
        self.payments_dry_run = payments_dry_run
        self.paper_trading = paper_trading
        self.publishers: dict[str, Publisher] = {}  # strategy_id -> Publisher
        self._stop = asyncio.Event()
        # Regime detection (same pattern as agent_runner)
        self.oracle = OracleUpdater()
        self._sweeper = SettlementSweeper(self.settings)
        self.regime_detector: IRegimeDetector = GmmRegimeDetector(fallback=VixRegimeDetector())
        # Position sizer — throttles raw weights by regime + consensus
        self.portfolio_constructor: PortfolioConstructor = PortfolioConstructor()
        self._synth_addrs = chain_client.settings.synth_addresses
        self._usdc_addr = chain_client.settings.usdc_address

    # ---- lifecycle -------------------------------------------------------

    async def start_publisher(
        self,
        strategy_id: str,
        pool_id: str,
        vault_address: str,
        creator_wallet: str,
        gateway_seller_address: str = "",
        agent_wallet_id: str = "",
        subscribers: dict[str, Subscriber] | None = None,
    ) -> None:
        """Start a publisher loop for a strategy. Idempotent.

        If *subscribers* is provided (e.g. rehydrated from Postgres on boot —
        the source of truth, D4), it is used as-is and written through to Redis
        to repopulate the cache. Otherwise subscribers are loaded from the Redis
        cache — used by the live /publish path, where no subscribers exist yet.
        """
        if strategy_id in self.publishers and self.publishers[strategy_id].task is not None:
            logger.info("Publisher %s already running", strategy_id)
            return

        if subscribers is not None:
            # Postgres is truth (D4) — overwrite the Redis cache unconditionally,
            # including with an empty dict if Postgres shows zero active subs.
            await self.state.save_subscribers(strategy_id, {sid: vars(s) for sid, s in subscribers.items()})
        else:
            raw = await self.state.load_subscribers(strategy_id)
            subscribers = {sid: Subscriber(**data) for sid, data in raw.items()}

        pub = Publisher(
            strategy_id=strategy_id,
            pool_id=pool_id,
            vault_address=vault_address,
            creator_wallet=creator_wallet,
            gateway_seller_address=gateway_seller_address,
            agent_wallet_id=agent_wallet_id,
            subscribers=subscribers,
        )

        pub.task = asyncio.create_task(self._run_loop(strategy_id))
        self.publishers[strategy_id] = pub
        logger.info("Started publisher for %s (vault=%s, %d subscribers)", strategy_id, vault_address, len(subscribers))

    async def stop_publisher(self, strategy_id: str) -> None:
        """Stop a publisher loop.

        Sets the retired flag so the current tick finishes cleanly (no mid-charge
        cancellation), clears the Redis subscriber cache, and emits a retire event.
        """
        pub = self.publishers.get(strategy_id)
        if pub is None:
            return

        # Signal retirement so _run_loop exits after current tick completes
        pub.retired = True

        # Wait for current sleep + one full tick to complete, not a fixed
        # guess — interval can be configured above the old hardcoded 360s.
        if pub.task and not pub.task.done():
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(pub.task, timeout=self.interval + 60)

        # Safe to remove now that the task has finished
        self.publishers.pop(strategy_id, None)

        # Clear Redis subscriber cache
        await self.state.save_subscribers(strategy_id, {})

        # Emit retire event
        await self.state.append_event(strategy_id, {"type": "publisher_retired", "strategy_id": strategy_id})

        logger.info("Stopped publisher for %s", strategy_id)

    async def add_subscriber(self, strategy_id: str, sub: Subscriber) -> None:
        """Register a subscriber for a strategy.

        The Postgres row is the registry of record (P7). No on-chain
        validation is performed — SubscriptionManager is fully detached.
        Raises ValueError if the publisher is not running.
        """
        pub = self.publishers.get(strategy_id)
        if pub is None:
            raise ValueError(f"no running publisher for {strategy_id}")
        pub.subscribers[sub.sub_id] = sub
        await self.state.save_subscribers(strategy_id, {sid: vars(s) for sid, s in pub.subscribers.items()})
        logger.info("Added subscriber %s to %s", sub.sub_id, strategy_id)

    async def remove_subscriber(self, strategy_id: str, sub_id: str) -> None:
        """Remove a subscriber from a strategy."""
        pub = self.publishers.get(strategy_id)
        if pub and sub_id in pub.subscribers:
            pub.subscribers[sub_id].active = False
            self._deactivate_subscriber_db(strategy_id, sub_id)
            del pub.subscribers[sub_id]
            await self.state.save_subscribers(strategy_id, {sid: vars(s) for sid, s in pub.subscribers.items()})
            logger.info("Removed subscriber %s from %s", sub_id, strategy_id)

    # ---- the loop (leader-guarded, runs one strategy) --------------------

    async def _run_loop(self, strategy_id: str) -> None:
        """Continuously tick a strategy, leader-guarded.

        Each strategy gets its own per-strategy lock so strategies tick
        independently (C2). The loop exits cleanly when the publisher is
        retired (TASK 18) so the current tick finishes uninterrupted.
        """
        while not self._stop.is_set():
            try:
                token = await self.state.try_acquire_leader(strategy_id)  # D-LEADER
                if token is not None:
                    try:
                        await self.tick(strategy_id, leader_token=token)
                    except Exception:
                        logger.exception("tick failed for %s", strategy_id)
            except Exception:
                logger.exception("leader acquisition failed for %s", strategy_id)

            # Check if retired before sleeping — allows clean stop without
            # mid-charge cancellation (TASK 18).
            pub = self.publishers.get(strategy_id)
            if pub is None or pub.retired:
                break

            await asyncio.sleep(self.interval)

    async def tick(self, strategy_id: str, leader_token: str | None = None) -> None:
        """Run one full rebalance cycle for a strategy.

        13 pipeline boundaries charged per-step (agent_runner flow fidelity),
        plus a REBALANCE phase per subscriber. The publisher vault always
        rebalances if the pipeline clears (agent_runner parity).
        Lock is released in ``finally``.
        """
        pub = self.publishers.get(strategy_id)
        if pub is None:
            return

        tick_id = f"{strategy_id}:{int(time.time())}"
        addr_map = {**self.settings.synth_addresses, "USDC": self.settings.usdc_address}

        try:
            # Participating set snapshotted once; deferrals shrink it.
            active = [s for s in pub.subscribers.values() if await self._subscriber_is_ready(s)]
            ctx = _TickCtx()
            step_runners = {
                TickStep.LOAD_STRATEGY: lambda: self._step_load_strategy(ctx, strategy_id),
                TickStep.EVALUATE_SIGNALS: lambda: self._step_evaluate_signals(ctx),
                TickStep.AGGREGATE_WEIGHTS: lambda: self._step_aggregate_weights(ctx, strategy_id, tick_id),
                TickStep.ENSEMBLE_CONSENSUS: lambda: self._step_ensemble(ctx),
                TickStep.REGIME_CLASSIFY: lambda: self._step_regime_classify(ctx, tick_id),
                TickStep.PERSIST_REGIME: lambda: self._step_persist_regime(ctx),
                TickStep.PERSIST_CONSENSUS: lambda: self._step_persist_consensus(ctx, strategy_id),
                TickStep.THROTTLE_WEIGHTS: lambda: self._step_throttle(ctx, strategy_id),
                TickStep.WEIGHTS_TO_TARGETS: lambda: self._step_targets(ctx),
                TickStep.READ_PORTFOLIO: lambda: self._step_read_portfolio(ctx, pub),
                TickStep.COMPUTE_TRADES: lambda: self._step_compute_trades(ctx, addr_map, pub, tick_id),
                TickStep.NO_DRIFT_DEDUP: lambda: self._step_dedup(ctx, tick_id),
                TickStep.V_CHECK: lambda: self._step_vcheck(ctx, strategy_id, tick_id),
            }

            for step in PIPELINE_STEPS:
                # charge everyone still active for reaching this step
                active = await self._charge_step(pub, active, strategy_id, tick_id, step)
                # execute the step; publisher work runs regardless of subscriber count
                try:
                    result = await step_runners[step]()
                except Exception as exc:
                    await self._halt_publisher(active, strategy_id, tick_id, step, str(exc))
                    return
                if result.halted:
                    await self._halt_publisher(active, strategy_id, tick_id, step, result.reason or step.value)
                    return

            trades = ctx.trades
            action_count = ctx.action_count

            # REBALANCE — per surviving subscriber (no-op when active is empty)
            await self._rebalance_phase(
                pub,
                active,
                strategy_id,
                tick_id,
                trades,
                ctx.target_weights,
                action_count,
                addr_map,
                leader_token,
            )

            # Publisher's own vault ALWAYS trades if the pipeline cleared.
            if not self.paper_trading:
                try:
                    await self.executor.execute_trades(pub.vault_address, trades)
                    await self.state.store.save_last_rebalance(pub.vault_address)
                    await self.state.append_event(
                        strategy_id,
                        {
                            "type": "rebalance",
                            "tick_id": tick_id,
                            "action_count": action_count,
                            "target_weights": ctx.target_weights,
                        },
                    )
                except Exception:
                    logger.exception("publisher vault rebalance failed for %s", strategy_id)

            # Settlement sweep (P5) — Gateway → wallet → depositToPool.
            # Runs inside its own try/except so a sweep failure never fails the tick.
            # Gated on payments_dry_run — sweep disburses real collected fees.
            if not self.payments_dry_run:
                try:
                    await self._sweeper.sweep_publisher(pub)
                except Exception:
                    logger.exception("[%s] settlement sweep failed", strategy_id)

            await self.state.save_subscribers(
                strategy_id,
                {sid: vars(s) for sid, s in pub.subscribers.items()},
            )
        finally:
            await self.state.release_leader(strategy_id, token=leader_token)

    # ====== 13 pipeline step methods + _rebalance_phase ========================

    async def _step_load_strategy(self, ctx: _TickCtx, strategy_id: str) -> _StepResult:
        ctx.strategy = self.provider.get_strategy(strategy_id)
        if ctx.strategy is None:
            logger.warning("Strategy %s not found", strategy_id)
            return _StepResult(halted=True, reason="strategy not found")
        ctx.synth_assets = [sym for sym, addr in self.settings.synth_addresses.items() if addr]
        return _StepResult()

    async def _step_evaluate_signals(self, ctx: _TickCtx) -> _StepResult:
        ctx.all_signals = await asyncio.to_thread(
            strategy_evaluator.evaluate_strategies,
            [ctx.strategy],
            ctx.synth_assets,
        )
        if not ctx.all_signals:
            logger.warning("No signals produced")
            return _StepResult(halted=True, reason="no signals")
        return _StepResult()

    async def _step_aggregate_weights(self, ctx: _TickCtx, strategy_id: str, tick_id: str) -> _StepResult:
        ctx.target_weights = strategy_evaluator.aggregate_signals(ctx.all_signals, usdc_floor=_USDC_FLOOR)
        if not ctx.target_weights:
            return _StepResult(halted=True, reason="no target weights")
        await self.state.append_event(
            strategy_id,
            {
                "type": "evaluation_step",
                "tick_id": tick_id,
                "target_weights": ctx.target_weights,
            },
        )
        return _StepResult()

    async def _step_ensemble(self, ctx: _TickCtx) -> _StepResult:
        flat_count = sum(1 for ss in ctx.all_signals for s in ss.signals if s.signal.value == "flat")
        total_count = sum(len(ss.signals) for ss in ctx.all_signals)
        if total_count > 0:
            ctx.consensus = EnsembleConsensus.from_signal_counts(flat_count, total_count)
        else:
            ctx.consensus = None
        return _StepResult()

    async def _step_regime_classify(self, ctx: _TickCtx, tick_id: str) -> _StepResult:
        ctx.regime_lock_held = await self.state.try_acquire_regime_lock()
        if ctx.regime_lock_held:
            ctx.regime_classification, ctx.market_regime = await self._classify_market_regime(tick_id)
        else:
            cached = await self.state.store.load_regime()
            if cached:
                ctx.regime_classification = _regime_classification_from_cache(cached)
                ctx.market_regime = (
                    ctx.regime_classification.regime.value if ctx.regime_classification else _MARKET_REGIME_UNKNOWN
                )
            else:
                ctx.regime_classification = None
                ctx.market_regime = _MARKET_REGIME_UNKNOWN
        return _StepResult()

    async def _step_persist_regime(self, ctx: _TickCtx) -> _StepResult:
        if ctx.regime_lock_held and ctx.regime_classification is not None:
            await self.state.store.save_regime(ctx.regime_classification)
        return _StepResult()

    async def _step_persist_consensus(self, ctx: _TickCtx, strategy_id: str) -> _StepResult:
        if ctx.consensus is not None:
            await self._save_publisher_consensus(strategy_id, ctx.consensus, ctx.all_signals)
        return _StepResult()

    async def _step_throttle(self, ctx: _TickCtx, strategy_id: str) -> _StepResult:
        strategies = [ctx.strategy] if ctx.strategy else []
        ctx.allocations = self.portfolio_constructor.construct(
            risk_profile=RiskProfile.MODERATE,
            strategies=strategies,
            backtest_results={},
            regime=ctx.regime_classification,
            ensemble_consensus=ctx.consensus,
            base_weights=ctx.target_weights,
        )
        return _StepResult()

    async def _step_targets(self, ctx: _TickCtx) -> _StepResult:
        ctx.targets = self._weights_to_targets({a.symbol: a.weight for a in ctx.allocations}, ctx.all_signals)
        return _StepResult()

    async def _step_read_portfolio(self, ctx: _TickCtx, pub) -> _StepResult:
        ctx.portfolio = await self.executor.read_portfolio(pub.vault_address)
        return _StepResult()

    async def _step_compute_trades(self, ctx: _TickCtx, addr_map, pub, tick_id) -> _StepResult:
        # Step 13.3: Set token oracles (NO-HALT)
        try:
            oracle_tokens = []
            oracle_addrs = []
            for t in ctx.targets:
                if t.weight > 0 and t.token_address:
                    symbol = t.symbol
                    if symbol == "USDC":
                        continue
                    oracle_addr = self.settings.oracle_addresses.get(symbol)
                    if oracle_addr:
                        oracle_tokens.append(t.token_address)
                        oracle_addrs.append(oracle_addr)
            if oracle_tokens:
                await self.executor.set_token_oracles(pub.vault_address, oracle_tokens, oracle_addrs)
                logger.info(
                    "[%s] Set %d token oracles on vault %s", tick_id, len(oracle_tokens), pub.vault_address[:10]
                )
        except Exception as e:
            logger.warning("[%s] Failed to set token oracles on %s: %s", tick_id, pub.vault_address[:10], e)

        # Step 13.4: Set target allocations (NO-HALT)
        try:
            alloc_tokens = []
            alloc_weights = []
            for t in ctx.targets:
                if t.weight > 0 and t.token_address:
                    alloc_tokens.append(t.token_address)
                    alloc_weights.append(int(t.weight * 10000))
            if alloc_tokens:
                total_bps = sum(alloc_weights)
                if total_bps > 0 and total_bps != 10000:
                    scale = 10000 / total_bps
                    alloc_weights = [int(round(w * scale)) for w in alloc_weights]
                    diff = 10000 - sum(alloc_weights)
                    if diff != 0 and alloc_weights:
                        alloc_weights[0] += diff
                await self.executor.set_target_allocations(pub.vault_address, alloc_tokens, alloc_weights)
                logger.info("[%s] Set target allocations on vault %s", tick_id, pub.vault_address[:10])
        except Exception as e:
            logger.warning("[%s] Failed to set allocations on %s: %s", tick_id, pub.vault_address[:10], e)

        # Step 13.5: Compute trades
        ctx.trades = compute_trades(ctx.portfolio, ctx.target_weights, token_addresses=addr_map)
        ctx.action_count = len(ctx.trades)
        return _StepResult()

    async def _step_dedup(self, ctx: _TickCtx, tick_id: str) -> _StepResult:
        if not ctx.trades:
            logger.info("[%s] No trades needed", tick_id)
            return _StepResult(halted=True, reason="no_drift")
        return _StepResult()

    async def _step_vcheck(self, ctx: _TickCtx, strategy_id: str, tick_id: str) -> _StepResult:
        alloc_weights_bps: dict[str, int] = {}
        for t in ctx.targets:
            if t.weight > 0 and t.token_address:
                alloc_weights_bps[t.symbol] = int(round(t.weight * 10000))
        if alloc_weights_bps:
            residual = sum(alloc_weights_bps.values()) - 10000
            if residual != 0:
                largest = max(alloc_weights_bps, key=alloc_weights_bps.get)
                alloc_weights_bps[largest] -= residual
        v_check = VCheck(weights_bps=alloc_weights_bps)
        v_result = v_check.run()
        if not v_result.passed:
            logger.warning("[%s] V_check FAILED: %s — skipping tick", tick_id, "; ".join(v_result.failures))
            await self.state.append_event(
                strategy_id,
                {
                    "type": "skip",
                    "reason": "v_check_failed",
                    "tick_id": tick_id,
                    "failures": v_result.failures,
                },
            )
            return _StepResult(halted=True, reason="v_check: " + "; ".join(v_result.failures))
        return _StepResult()

    async def _rebalance_phase(
        self, pub, active, strategy_id, tick_id, trades, target_weights, action_count, addr_map, leader_token
    ):
        """Per-subscriber REBALANCE with midpoint leader renewal.

        Charges are batched into groups of CHARGE_BATCH_SIZE for concurrent
        Circle signing within each half. Midpoint leader-renewal structure
        is preserved.
        """
        if not trades:
            return
        sem = asyncio.Semaphore(5)

        async def _one(sub):
            async with sem:
                paid = await self._charge_one(pub, sub, strategy_id, tick_id, TickStep.REBALANCE, action_count)
                if not paid:
                    self._defer_subscriber(pub, sub)
                    await self.record_subscriber_tick(
                        SubscriberTickRecord(
                            sub_id=sub.sub_id,
                            strategy_id=strategy_id,
                            tick_id=tick_id,
                            timestamp=datetime.now(UTC),
                            step_reached=TickStep.REBALANCE,
                            halted=True,
                            halt_source=HaltSource.PAYMENT,
                            halt_reason="could not afford rebalance",
                            charged=False,
                            action_count=action_count,
                        )
                    )
                    return
                mirrored, trades_or_exc = await self._apply_to_subscriber(sub, target_weights, addr_map)
                await self.record_subscriber_tick(
                    SubscriberTickRecord(
                        sub_id=sub.sub_id,
                        strategy_id=strategy_id,
                        tick_id=tick_id,
                        timestamp=datetime.now(UTC),
                        step_reached=TickStep.REBALANCE,
                        halted=not mirrored,
                        halt_source=None if mirrored else HaltSource.EXECUTION,
                        halt_reason=None if mirrored else str(trades_or_exc),
                        charged=True,
                        action_count=action_count,
                        trade_orders=[asdict(t) for t in trades_or_exc] if mirrored else None,
                    )
                )
                if not mirrored:
                    self._defer_subscriber(pub, sub)
                    await self._record_liability(sub, strategy_id, tick_id, action_count)

        async def _charge_half(half):
            for i in range(0, len(half), CHARGE_BATCH_SIZE):
                chunk = half[i : i + CHARGE_BATCH_SIZE]
                for r in await asyncio.gather(*[_one(s) for s in chunk], return_exceptions=True):
                    if isinstance(r, Exception):
                        logger.error("Subscriber processing failed: %s", r)

        midpoint = len(active) // 2
        first = active[:midpoint] if midpoint else active
        if first:
            await _charge_half(first)
        await self.state.renew_leader(strategy_id, token=leader_token)
        if midpoint:
            await _charge_half(active[midpoint:])

    # ---- helpers ---------------------------------------------------------

    async def refund_subscriber(
        self, *, circle_wallet_id: str | None, dcw_address: str | None, to_wallet: str, sub_id: str
    ) -> str | None:
        """Return a subscriber's remaining prepaid-fee balance to their own wallet.

        Called on unsubscribe (the exit from the interim custodial fee model,
        issue #975). No-op under payments_dry_run — there is no real DCW balance
        to move. Delegates to the settlement sweeper; best-effort (never raises).
        """
        if self.payments_dry_run:
            return None
        return await self._sweeper.withdraw_subscriber(
            circle_wallet_id=circle_wallet_id or "",
            dcw_address=dcw_address or "",
            to_wallet=to_wallet,
            sub_id=sub_id,
        )

    # F6.1 — record subscriber tick log (SQL + Redis mirror, best-effort)
    async def record_subscriber_tick(self, rec: SubscriberTickRecord) -> None:
        """Persist one subscriber tick record. Best-effort: never aborts the tick."""
        try:
            with get_session() as session:
                session.add(
                    SubscriberTickLog(
                        sub_id=rec.sub_id,
                        strategy_id=rec.strategy_id,
                        tick_id=rec.tick_id,
                        step_reached=rec.step_reached.value,
                        halted=rec.halted,
                        halt_source=rec.halt_source.value if rec.halt_source else None,
                        halt_reason=rec.halt_reason,
                        charged=rec.charged,
                        action_count=rec.action_count,
                    )
                )
                session.commit()
        except Exception:
            logger.exception("Failed to persist tick log for %s / %s", rec.sub_id, rec.tick_id)
        try:
            await self.state.push_subscriber_tick(
                rec.sub_id,
                {
                    "sub_id": rec.sub_id,
                    "strategy_id": rec.strategy_id,
                    "tick_id": rec.tick_id,
                    "timestamp": rec.timestamp.isoformat(),
                    "step_reached": rec.step_reached.value,
                    "halted": rec.halted,
                    "halt_source": rec.halt_source.value if rec.halt_source else None,
                    "halt_reason": rec.halt_reason,
                    "charged": rec.charged,
                    "action_count": rec.action_count,
                    "trade_orders": rec.trade_orders,
                },
            )
        except Exception:
            logger.exception("Failed to mirror tick log to Redis for %s", rec.sub_id)

    # F6.2 — raw ERC20 balanceOf (6-dec USDC units)
    async def _usdc_balance_of(self, address: str) -> int:
        usdc = self.loader._contract(self.settings.usdc_address, "IERC20")
        return await usdc.functions.balanceOf(address).call()

    # F6.3 — readiness gate (funded, active, non-chargeable)
    async def _subscriber_is_ready(self, sub: Subscriber) -> bool:
        if not sub.active:
            return False
        try:
            vault = await self.executor.read_portfolio(sub.vault_address)
            # sub.ephemeral_wallet IS the subscriber's Circle Developer-Controlled
            # Wallet address (P3) — this check truthfully validates the funded
            # wallet that the x402 signer controls.
            eph_raw = await self._usdc_balance_of(sub.ephemeral_wallet)
        except Exception:
            return False
        required_eph_raw = self.settings.min_active_action_buffer * FLAT_FEE_PER_ACTION
        return (vault.total_value_usdc >= self.settings.min_vault_usdc) and (eph_raw >= required_eph_raw)

    # F6.4 — defer subscriber (mark inactive + persist halt)
    def _defer_subscriber(self, pub, sub: Subscriber) -> None:
        sub.active = False
        self._persist_halt_state(pub.strategy_id, sub.sub_id)

    # F6.5 — single charge, reused by pipeline + REBALANCE
    def _claim_settlement_intent(self, strategy_id: str, tick_id: str, sub_id: str, step: str) -> str:
        """Atomically claim the idempotency slot for one logical charge.

        Returns:
          "claimed"         — a fresh pending intent was inserted; proceed to settle.
          "already_settled" — this exact charge already settled; caller returns paid.
          "in_flight"       — a pending/failed row exists (concurrent or crashed
                              attempt owns this key); caller must NOT re-charge.

        The unique index on (strategy_id, tick_id, sub_id, step) makes the insert
        the authoritative claim under concurrency — a racing insert raises
        IntegrityError and is reported as in_flight.
        """
        try:
            with get_session() as session:
                existing = (
                    session.query(SettlementIntent)
                    .filter_by(strategy_id=strategy_id, tick_id=tick_id, sub_id=sub_id, step=step)
                    .first()
                )
                if existing is not None:
                    return "already_settled" if existing.status == "settled" else "in_flight"
                session.add(
                    SettlementIntent(
                        strategy_id=strategy_id,
                        tick_id=tick_id,
                        sub_id=sub_id,
                        step=step,
                        status="pending",
                    )
                )
                session.commit()
                return "claimed"
        except IntegrityError:
            # A concurrent claim won the unique index — do not double-charge.
            return "in_flight"

    def _finalize_settlement_intent(
        self, strategy_id: str, tick_id: str, sub_id: str, step: str, *, settled: bool
    ) -> None:
        """Mark a claimed intent settled (with timestamp) or failed. Best-effort."""
        try:
            with get_session() as session:
                row = (
                    session.query(SettlementIntent)
                    .filter_by(strategy_id=strategy_id, tick_id=tick_id, sub_id=sub_id, step=step)
                    .first()
                )
                if row is None:
                    return
                row.status = "settled" if settled else "failed"
                if settled:
                    row.settled_at = datetime.now(UTC)
                session.commit()
        except Exception:
            logger.exception("Failed to finalize settlement intent %s / %s / %s", strategy_id, tick_id, sub_id)

    async def _charge_one(self, pub, sub, strategy_id, tick_id, step: TickStep, action_count: int) -> bool:
        if self.payments_dry_run:
            return True
        if not pub.gateway_seller_address:
            logger.warning("[%s] no gateway_seller_address for pub %s — unpaid", tick_id, strategy_id)
            return False
        if not sub.circle_wallet_id:
            logger.warning("[%s] no circle_wallet_id for sub %s — unpaid", tick_id, sub.sub_id)
            return False

        # Idempotency guard (x402 is NOT crash-retry-idempotent — a retry signs a
        # fresh EIP-3009 nonce that settles as a new payment). Claim the logical
        # charge BEFORE settling so a crash/retry cannot double-charge.
        claim = self._claim_settlement_intent(strategy_id, tick_id, sub.sub_id, step.value)
        if claim == "already_settled":
            return True  # this exact (strategy, tick, sub, step) already paid
        if claim == "in_flight":
            logger.warning(
                "[%s] settlement intent already in-flight for sub %s step %s — skipping to avoid double-charge",
                tick_id,
                sub.sub_id,
                step.value,
            )
            return False

        paid = await payments.charge(
            sub_id=sub.sub_id,
            wallet_id=sub.circle_wallet_id,
            wallet_address=sub.ephemeral_wallet,
            seller_address=pub.gateway_seller_address,
            strategy_id=strategy_id,
            tick_id=tick_id,
            action_count=action_count,
            flat_fee_raw=FLAT_FEE_PER_ACTION,
            step=step.value,
        )
        self._finalize_settlement_intent(strategy_id, tick_id, sub.sub_id, step.value, settled=paid)
        return paid

    # F6.6 — charge all active subscribers for one pipeline step
    # Batched into groups of CHARGE_BATCH_SIZE for concurrent Circle signing.
    async def _charge_step(self, pub, active, strategy_id, tick_id, step: TickStep):
        survivors = []
        for i in range(0, len(active), CHARGE_BATCH_SIZE):
            chunk = active[i : i + CHARGE_BATCH_SIZE]
            results = await asyncio.gather(
                *[self._charge_one(pub, s, strategy_id, tick_id, step, action_count=1) for s in chunk],
                return_exceptions=True,
            )
            for sub, paid in zip(chunk, results):
                if isinstance(paid, Exception):
                    paid = False
                if paid:
                    await self.record_subscriber_tick(
                        SubscriberTickRecord(
                            sub_id=sub.sub_id,
                            strategy_id=strategy_id,
                            tick_id=tick_id,
                            timestamp=datetime.now(UTC),
                            step_reached=step,
                            halted=False,
                            charged=True,
                            action_count=1,
                        )
                    )
                    survivors.append(sub)
                else:
                    self._defer_subscriber(pub, sub)
                    await self.record_subscriber_tick(
                        SubscriberTickRecord(
                            sub_id=sub.sub_id,
                            strategy_id=strategy_id,
                            tick_id=tick_id,
                            timestamp=datetime.now(UTC),
                            step_reached=step,
                            halted=True,
                            halt_source=HaltSource.PAYMENT,
                            halt_reason=f"could not afford {step.value}",
                            charged=False,
                            action_count=1,
                        )
                    )
                    await self.state.append_event(
                        strategy_id,
                        {
                            "type": "halt",
                            "sub_id": sub.sub_id,
                            "reason": "payment_required",
                            "step": step.value,
                            "tick_id": tick_id,
                        },
                    )
        return survivors

    # F6.7 — halt all still-active subscribers due to publisher pipeline halt
    async def _halt_publisher(self, active, strategy_id, tick_id, step: TickStep, reason: str):
        for sub in active:
            await self.record_subscriber_tick(
                SubscriberTickRecord(
                    sub_id=sub.sub_id,
                    strategy_id=strategy_id,
                    tick_id=tick_id,
                    timestamp=datetime.now(UTC),
                    step_reached=step,
                    halted=True,
                    halt_source=HaltSource.PUBLISHER,
                    halt_reason=reason,
                    charged=True,
                    action_count=1,
                )
            )
        await self.state.append_event(
            strategy_id,
            {
                "type": "publisher_halt",
                "tick_id": tick_id,
                "step": step.value,
                "reason": reason,
            },
        )

    # F6.8 — apply publisher trades to subscriber (frozen TradeOrder → asdict)
    async def _apply_to_subscriber(self, sub, target_weights, addr_map=None) -> tuple[bool, object]:
        if self.paper_trading:
            return True, []
        try:
            sub_portfolio = await self.executor.read_portfolio(sub.vault_address)
            sub_trades = compute_trades(sub_portfolio, target_weights, token_addresses=addr_map)
            await self.executor.execute_trades(sub.vault_address, sub_trades)
            return True, sub_trades
        except Exception as exc:
            logger.exception("subscriber apply failed for %s", sub.sub_id)
            return False, exc

    # back-compat wrapper: fused _evaluate for legacy callers / tests
    async def _evaluate(self, strategy_id: str) -> tuple[dict[str, float], list[StrategySignals]]:
        """Return (target_weights, all_signals).

        Reuse strategy_evaluator.aggregate_signals exactly as agent_runner.tick()
        does. Return ({}, []) on empty.
        """
        strategy = self.provider.get_strategy(strategy_id)
        if strategy is None:
            logger.warning("Strategy %s not found", strategy_id)
            return {}, []

        synth_assets = [sym for sym, addr in self.settings.synth_addresses.items() if addr]

        # Run signal evaluation in thread pool (yfinance is sync)
        all_signals: list[StrategySignals] = await asyncio.to_thread(
            strategy_evaluator.evaluate_strategies,
            [strategy],
            synth_assets,
        )

        if not all_signals:
            logger.warning("No signals produced for %s", strategy_id)
            return {}, []

        target_weights = strategy_evaluator.aggregate_signals(
            all_signals,
            usdc_floor=_USDC_FLOOR,
        )
        return target_weights, all_signals

    # ─── Exogenous market-regime classification (port from agent_runner) ───

    async def _classify_market_regime(self, tick_id: str) -> tuple[RegimeClassification | None, str]:
        """Fetch a market snapshot and classify the exogenous market regime.

        Returns ``(classification, regime_value)``. On any failure degrades
        gracefully to ``(None, "unknown")``.
        """
        try:
            snapshot = await self.oracle.fetch_market_snapshot()
        except Exception as e:
            logger.warning(
                "[tick %s] Market snapshot fetch failed (%s) — regime=unknown",
                tick_id,
                e,
            )
            return None, _MARKET_REGIME_UNKNOWN

        if not snapshot.has_regime_signals:
            logger.warning(
                "[tick %s] Snapshot missing regime signals — regime=unknown",
                tick_id,
            )
            return None, _MARKET_REGIME_UNKNOWN

        try:
            classification = self.regime_detector.classify(snapshot)
        except Exception as e:
            logger.warning(
                "[tick %s] Regime classification failed (%s) — regime=unknown",
                tick_id,
                e,
            )
            return None, _MARKET_REGIME_UNKNOWN

        logger.info(
            "[tick %s] Market regime: %s (confidence=%.2f, VIX=%.1f, changed=%s)",
            tick_id,
            classification.regime.value,
            classification.confidence,
            classification.signals.vix_level,
            classification.regime_changed,
        )
        return classification, classification.regime.value

    # ─── Weights → target allocations with provenance ───────────────────

    def _weights_to_targets(
        self, weights: dict[str, float], all_signals: list[StrategySignals] | None = None
    ) -> list[TargetAllocation]:
        """Convert weight dict → TargetAllocation list (port of agent_runner.py:779)."""
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

    # ─── Per-publisher ensemble consensus persistence ───────────────────

    async def _save_publisher_consensus(
        self, strategy_id: str, consensus: EnsembleConsensus, all_signals: list[StrategySignals]
    ) -> None:
        """Persist ensemble consensus under a per-publisher key."""
        from datetime import UTC, datetime

        r = await self.state.store._get_redis()
        signal_summary = {}
        for ss in all_signals:
            for s in ss.signals:
                signal_summary[s.asset] = {
                    "signal": s.signal.value,
                    "weight": s.weight,
                    "reason": s.reason,
                    "strategy": ss.paper_title[:40],
                }
        flat_pct = consensus.flat_pct
        if all_signals:
            directional = [s for ss in all_signals for s in ss.signals if s.signal.value != "flat"]
            vote_ratio = 1.0 - flat_pct
            avg_strength = sum(abs(s.weight) for s in directional) / max(len(directional), 1) if directional else 0.0
            avg_strength = min(avg_strength, 1.0)
            all_weights = [s.weight for ss in all_signals for s in ss.signals]
            if len(all_weights) >= 2:
                mean_w = sum(all_weights) / len(all_weights)
                variance = sum((w - mean_w) ** 2 for w in all_weights) / len(all_weights)
                dispersion_penalty = min(variance**0.5 * 2, 0.3)
            else:
                dispersion_penalty = 0.0
            dyn_confidence = max(0.05, min(0.99, vote_ratio * (0.5 + 0.5 * avg_strength) - dispersion_penalty))
        else:
            dyn_confidence = 0.5
        data = {
            "label": consensus.label.value,
            "confidence": round(dyn_confidence, 4),
            "flat_pct": round(flat_pct, 2),
            "strategy_count": consensus.signal_count or len(all_signals),
            "signals": signal_summary,
            "timestamp": datetime.now(UTC).isoformat(),
            "source": "strategy_consensus",
        }
        key = f"{_KEY_ENSEMBLE_CONSENSUS_PREFIX}{strategy_id}"
        await r.set(key, json.dumps(data))

    async def _verify_payment(
        self,
        pub: Publisher,
        sub: Subscriber,
        strategy_id: str,
        tick_id: str,
        action_count: int,
    ) -> bool:
        """Legacy delegate — replaced by _charge_one.  Kept for test compat."""
        return await self._charge_one(pub, sub, strategy_id, tick_id, TickStep.LOAD_STRATEGY, action_count)

    async def _record_liability(self, sub: Subscriber, strategy_id: str, tick_id: str, action_count: int) -> None:
        """Record a charge-succeeded/mirror-failed liability. Best-effort:
        a failure here must not abort the tick or block subsequent subscribers."""
        unit_price = FLAT_FEE_PER_ACTION
        amount_owed = action_count * unit_price

        try:
            with get_session() as session:
                session.add(
                    SubscriberLiability(
                        sub_id=sub.sub_id,
                        strategy_id=strategy_id,
                        tick_id=tick_id,
                        action_count=action_count,
                        unit_price_usdc=unit_price,
                        amount_owed_usdc=amount_owed,
                    )
                )
                session.commit()
            await self.state.append_event(
                strategy_id,
                {
                    "type": "liability_recorded",
                    "sub_id": sub.sub_id,
                    "tick_id": tick_id,
                    "action_count": action_count,
                    "amount_owed_usdc": amount_owed,
                },
            )
            logger.warning(
                "Liability recorded: sub=%s tick=%s action_count=%d amount_owed=%s",
                sub.sub_id,
                tick_id,
                action_count,
                amount_owed,
            )
        except Exception:
            logger.exception("Failed to record liability for %s / %s", sub.sub_id, tick_id)

    def _deactivate_subscriber_db(self, strategy_id: str, sub_id: str) -> None:
        """Persist subscriber deactivation to Postgres on unsubscribe (M5/A1)."""
        try:
            with get_session() as session:
                row = (
                    session.query(MarketplaceAgent)
                    .filter(
                        MarketplaceAgent.role == "subscriber",
                        MarketplaceAgent.strategy_id == strategy_id,
                        MarketplaceAgent.sub_id == sub_id,
                        MarketplaceAgent.status == "running",
                    )
                    .first()
                )
                if row is not None:
                    row.status = "stopped"
                    session.commit()
        except Exception:
            logger.exception("Failed to persist subscriber deactivation for %s/%s", strategy_id, sub_id)

    def _persist_halt_state(self, strategy_id: str, sub_id: str) -> None:
        """Persist subscriber halt state to Postgres on payment failure (C-5)."""
        try:
            with get_session() as session:
                row = (
                    session.query(MarketplaceAgent)
                    .filter(
                        MarketplaceAgent.role == "subscriber",
                        MarketplaceAgent.strategy_id == strategy_id,
                        MarketplaceAgent.sub_id == sub_id,
                        MarketplaceAgent.status == "running",
                    )
                    .first()
                )
                if row is not None:
                    row.halted = True
                    session.commit()
        except Exception:
            logger.exception("Failed to persist halt state for %s/%s", strategy_id, sub_id)
