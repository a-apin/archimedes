"""MarketService — in-process publisher loops + subscriber handlers (monolith).

Replaces the container-per-agent model. Reuses the working rebalance path
from agent_runner (aggregate_signals -> read_portfolio -> compute_trades ->
execute_trades). NO Docker, NO webhook HTTP between agents.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import time
from dataclasses import dataclass, field

from archimedes.chain.circle_signer import CircleSigner
from archimedes.chain.client import ChainSettings
from archimedes.chain.contracts import ContractLoader
from archimedes.chain.executor import ChainExecutor
from archimedes.db import get_session
from archimedes.marketplace.encoding import to_bytes32
from archimedes.marketplace.state import MarketState
from archimedes.models.marketplace import SubscriberLiability
from archimedes.models.portfolio import Portfolio, TargetAllocation, TradeDirection, TradeOrder
from archimedes.services.strategy_provider import default_provider
from archimedes.services.strategy_signal_evaluator import (
    StrategySignals,
    strategy_evaluator,
)

logger = logging.getLogger(__name__)

_DRIFT_THRESHOLD = 0.15
_USDC_FLOOR = float(os.getenv("AGENT_USDC_FLOOR", "0.20"))


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


@dataclass
class Publisher:
    strategy_id: str
    pool_id: str
    vault_address: str
    creator_wallet: str
    subscribers: dict[str, Subscriber] = field(default_factory=dict)
    task: asyncio.Task | None = None
    retired: bool = False


class MarketService:
    """In-process marketplace engine.

    Owns publisher loops + subscriber handlers, reuses the real rebalance
    path, charges on-chain, and fans out via in-process + Redis.
    """

    def __init__(self, interval_seconds: int = 300, dry_run: bool = False):
        self.settings = ChainSettings()
        self.loader = ContractLoader()
        self.executor = ChainExecutor(loader=self.loader)
        self.signer = CircleSigner()
        self.state = MarketState()
        self.provider = default_provider()
        self.interval = interval_seconds
        self.dry_run = dry_run
        self.publishers: dict[str, Publisher] = {}  # strategy_id -> Publisher
        self._stop = asyncio.Event()

    # ---- lifecycle -------------------------------------------------------

    async def start_publisher(
        self,
        strategy_id: str,
        pool_id: str,
        vault_address: str,
        creator_wallet: str,
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
            await self.state.save_subscribers(
                strategy_id, {sid: vars(s) for sid, s in subscribers.items()}
            )
        else:
            raw = await self.state.load_subscribers(strategy_id)
            subscribers = {sid: Subscriber(**data) for sid, data in raw.items()}

        pub = Publisher(
            strategy_id=strategy_id,
            pool_id=pool_id,
            vault_address=vault_address,
            creator_wallet=creator_wallet,
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

        # Wait for current tick to finish (no mid-charge cancellation)
        if pub.task and not pub.task.done():
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(pub.task, timeout=360)

        # Safe to remove now that the task has finished
        self.publishers.pop(strategy_id, None)

        # Clear Redis subscriber cache
        await self.state.save_subscribers(strategy_id, {})

        # Emit retire event
        await self.state.append_event(strategy_id, {"type": "publisher_retired", "strategy_id": strategy_id})

        logger.info("Stopped publisher for %s", strategy_id)

    async def add_subscriber(self, strategy_id: str, sub: Subscriber) -> None:
        """Register a subscriber for a strategy.

        Validates on-chain before adding. Any validation failure (inactive,
        RPC error, invalid sub_id) raises — fails closed (M4).
        """
        if not self.dry_run:
            c = self.loader._contract(self.settings.subscription_manager_address, "SubscriptionManager")
            try:
                sub_data = await c.functions.subscriptions(to_bytes32(sub.sub_id)).call()
            except Exception as exc:
                raise ValueError(f"on-chain validation failed for {sub.sub_id}: {exc}") from exc
            active = bool(sub_data[5])  # 6th field: active
            if not active:
                raise ValueError(f"subscription {sub.sub_id} not active on-chain")

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
                if await self.state.try_acquire_leader(strategy_id):  # D-LEADER
                    try:
                        await self.tick(strategy_id)
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

    async def tick(self, strategy_id: str) -> None:
        """Run one full rebalance cycle for a strategy.

        Lock is released in ``finally`` so tick cadence is governed purely by
        ``interval`` and not by TTL expiry.  A midpoint ``renew_leader`` call
        acts as cheap crash-safety insurance for slow ticks.
        """
        pub = self.publishers.get(strategy_id)
        if pub is None:
            return

        tick_id = f"{strategy_id}:{int(time.time())}"

        # Build token address map once for all compute_trades calls
        addr_map = {**self.settings.synth_addresses, "USDC": self.settings.usdc_address}

        try:
            # 1. REAL rebalance computation (reuse main) — NOT a stub.
            target_weights = await self._evaluate(strategy_id)
            portfolio = await self.executor.read_portfolio(pub.vault_address)
            trades = compute_trades(portfolio, target_weights, token_addresses=addr_map)

            await self.state.append_event(
                strategy_id,
                {
                    "type": "evaluation_step",
                    "tick_id": tick_id,
                    "target_weights": target_weights,
                },
            )

            if not trades:
                logger.info("[%s] No trades needed", tick_id)
                return

            action_count = len(trades)

            # 2. Verify x402 payment for each active subscriber, then apply
            # in-process trades.  Bound concurrency to 5 simultaneous
            # subscribers (M6).
            _sub_sem = asyncio.Semaphore(5)

            async def _process_subscriber(sub: Subscriber) -> None:
                async with _sub_sem:
                    paid = await self._verify_payment(sub.sub_id)
                    if not paid:
                        sub.active = False
                        await self.state.append_event(
                            strategy_id,
                            {
                                "type": "halt",
                                "sub_id": sub.sub_id,
                                "reason": "payment_required",
                                "tick_id": tick_id,
                            },
                        )
                        return
                    mirrored = await self._apply_to_subscriber(
                        sub, trades, target_weights, tick_id, addr_map,
                    )
                    if not mirrored:
                        await self._record_liability(sub, strategy_id, tick_id, action_count)

            # Split active subscribers into two halves; renew the per-strategy
            # leader lock at the midpoint as crash-safety insurance.
            active_subs = [sub for sub in pub.subscribers.values() if sub.active]
            midpoint = len(active_subs) // 2

            # Process first half (or all subscribers if fewer than 2)
            first_batch = active_subs[:midpoint] if midpoint else active_subs
            if first_batch:
                results = await asyncio.gather(
                    *[_process_subscriber(sub) for sub in first_batch],
                    return_exceptions=True,
                )
                for r in results:
                    if isinstance(r, Exception):
                        logger.error("Subscriber processing failed: %s", r)

            # Midpoint renewal — cheap crash insurance for a slow tick.
            # Called exactly once per tick regardless of subscriber count.
            await self.state.renew_leader(strategy_id)

            # Process second half (only if we split above)
            if midpoint:
                second_half = active_subs[midpoint:]
                results = await asyncio.gather(
                    *[_process_subscriber(sub) for sub in second_half],
                    return_exceptions=True,
                )
                for r in results:
                    if isinstance(r, Exception):
                        logger.error("Subscriber processing failed: %s", r)

            # 3. Execute on publisher's own vault too (non-dry-run).
            if not self.dry_run:
                try:
                    await self.executor.execute_trades(pub.vault_address, trades)
                except Exception:
                    logger.exception("publisher vault rebalance failed for %s", strategy_id)

            await self.state.append_event(
                strategy_id,
                {
                    "type": "rebalance",
                    "tick_id": tick_id,
                    "action_count": action_count,
                },
            )
            await self.state.save_subscribers(
                strategy_id,
                {sid: vars(s) for sid, s in pub.subscribers.items()},
            )
        finally:
            await self.state.release_leader(strategy_id)

    # ---- helpers ---------------------------------------------------------

    async def _evaluate(self, strategy_id: str) -> dict[str, float]:
        """Return target_weights dict.

        Reuse strategy_evaluator.aggregate_signals exactly as agent_runner.tick()
        does. Return {} on empty.
        """
        strategy = self.provider.get_strategy(strategy_id)
        if strategy is None:
            logger.warning("Strategy %s not found", strategy_id)
            return {}

        synth_assets = [sym for sym, addr in self.settings.synth_addresses.items() if addr]

        # Run signal evaluation in thread pool (yfinance is sync)
        all_signals: list[StrategySignals] = await asyncio.to_thread(
            strategy_evaluator.evaluate_strategies,
            [strategy],
            synth_assets,
        )

        if not all_signals:
            logger.warning("No signals produced for %s", strategy_id)
            return {}

        return strategy_evaluator.aggregate_signals(
            all_signals,
            usdc_floor=_USDC_FLOOR,
        )

    async def _verify_payment(self, sub_id: str) -> bool:
        """Verify x402 payment for a subscriber.

        Checks Redis for an active payment record.  In dry-run mode all
        payments are considered valid so the engine is exercisable without
        a real gateway.
        """
        if self.dry_run:
            return True
        return await self.state.has_active_payment(sub_id)

    async def _record_liability(
        self, sub: Subscriber, strategy_id: str, tick_id: str, action_count: int
    ) -> None:
        """Record a charge-succeeded/mirror-failed liability. Best-effort:
        a failure here must not abort the tick or block subsequent subscribers."""
        unit_price = None
        try:
            addr = self.settings.subscription_manager_address
            if addr:
                c = self.loader._contract(addr, "SubscriptionManager")
                unit_price = await c.functions.flat_fee_per_action().call()
        except Exception:
            logger.warning("Could not read flat_fee_per_action for liability record on %s", sub.sub_id)

        amount_owed = action_count * unit_price if unit_price is not None else None

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
                sub.sub_id, tick_id, action_count, amount_owed,
            )
        except Exception:
            logger.exception("Failed to record liability for %s / %s", sub.sub_id, tick_id)

    async def _apply_to_subscriber(
        self,
        sub: Subscriber,
        _trades: list[TradeOrder],
        target_weights: dict[str, float],
        _tick_id: str,
        addr_map: dict[str, str] | None = None,
    ) -> bool:
        """In-process fan-out (D-FANOUT): map publisher trades to the subscriber's own
        vault via read_portfolio + compute_trades, then execute_trades on sub.vault_address.

        Returns ``True`` on success (or dry-run). Returns ``False`` if the mirror
        fails — the caller (``tick``) decides whether to record a liability.
        """
        if self.dry_run:
            return True
        try:
            sub_portfolio = await self.executor.read_portfolio(sub.vault_address)
            sub_trades = compute_trades(sub_portfolio, target_weights, token_addresses=addr_map)
            await self.executor.execute_trades(sub.vault_address, sub_trades)
            return True
        except Exception:
            logger.exception("subscriber apply failed for %s", sub.sub_id)
            return False
