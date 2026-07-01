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
from archimedes.marketplace.encoding import to_bytes32
from archimedes.marketplace.state import MarketState
from archimedes.models.portfolio import Portfolio, TargetAllocation, TradeDirection, TradeOrder
from archimedes.services.strategy_provider import default_provider
from archimedes.services.strategy_signal_evaluator import (
    StrategySignals,
    strategy_evaluator,
)

logger = logging.getLogger(__name__)

_DRIFT_THRESHOLD = 0.15
_USDC_FLOOR = float(os.getenv("AGENT_USDC_FLOOR", "0.20"))


def compute_trades(portfolio: Portfolio, target_weights: dict[str, float]) -> list[TradeOrder]:
    """PORT of StrategyRunner._compute_trades (agent_runner.py:806).

    Diff current portfolio vs target weights → trade list.
    """
    current_weights = portfolio.weights_dict
    target_map = {sym: TargetAllocation(symbol=sym, weight=w, token_address="") for sym, w in target_weights.items()}

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

    async def start_publisher(self, strategy_id: str, pool_id: str, vault_address: str, creator_wallet: str) -> None:
        """Start a publisher loop for a strategy. Idempotent."""
        if strategy_id in self.publishers and self.publishers[strategy_id].task is not None:
            logger.info("Publisher %s already running", strategy_id)
            return

        # Rehydrate subscribers from Redis
        raw = await self.state.load_subscribers(strategy_id)
        subscribers: dict[str, Subscriber] = {}
        for sid, data in raw.items():
            subscribers[sid] = Subscriber(**data)

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
        """Stop a publisher loop."""
        pub = self.publishers.pop(strategy_id, None)
        if pub and pub.task:
            pub.task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await pub.task
            logger.info("Stopped publisher for %s", strategy_id)

    async def add_subscriber(self, strategy_id: str, sub: Subscriber) -> None:
        """Register a subscriber for a strategy."""
        # Validate on-chain unless dry_run
        if not self.dry_run:
            try:
                c = self.loader._contract(self.settings.subscription_manager_address, "SubscriptionManager")
                sub_data = await c.functions.subscriptions(to_bytes32(sub.sub_id)).call()
                active = sub_data[5]  # 6th field: active
                if not active:
                    raise ValueError("subscription not active on-chain")
            except ValueError as exc:
                if "subscription not active" in str(exc):
                    raise
                logger.warning("Could not validate sub_id %s on-chain: %s", sub.sub_id, exc)

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
            del pub.subscribers[sub_id]
            await self.state.save_subscribers(strategy_id, {sid: vars(s) for sid, s in pub.subscribers.items()})
            logger.info("Removed subscriber %s from %s", sub_id, strategy_id)

    # ---- the loop (leader-guarded, runs one strategy) --------------------

    async def _run_loop(self, strategy_id: str) -> None:
        """Continuously tick a strategy, leader-guarded."""
        while not self._stop.is_set():
            try:
                if await self.state.try_acquire_leader():  # D-LEADER
                    try:
                        await self.tick(strategy_id)
                    except Exception:
                        logger.exception("tick failed for %s", strategy_id)
            except Exception:
                logger.exception("leader acquisition failed for %s", strategy_id)
            await asyncio.sleep(self.interval)

    async def tick(self, strategy_id: str) -> None:
        """Run one full rebalance cycle for a strategy."""
        pub = self.publishers.get(strategy_id)
        if pub is None:
            return
        tick_id = f"{strategy_id}:{int(time.time())}"

        # 1. REAL rebalance computation (reuse main) — NOT a stub.
        target_weights = await self._evaluate(strategy_id)
        portfolio = await self.executor.read_portfolio(pub.vault_address)
        trades = compute_trades(portfolio, target_weights)

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

        # 2. Pre-charge each active subscriber on-chain, then apply in-process.
        for sub in list(pub.subscribers.values()):
            if not sub.active:
                continue
            ok = await self._charge(sub.sub_id, action_count)
            if not ok:
                sub.active = False
                await self.state.append_event(
                    strategy_id,
                    {
                        "type": "halt",
                        "sub_id": sub.sub_id,
                        "reason": "insufficient_balance",
                        "tick_id": tick_id,
                    },
                )
                continue
            await self._apply_to_subscriber(sub, trades, target_weights, tick_id)

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

    async def _charge(self, sub_id: str, action_count: int) -> bool:
        """Charge a subscriber on-chain. Returns True on success."""
        if self.dry_run:
            return True
        addr = self.settings.subscription_manager_address
        if not addr:
            logger.warning("subscription_manager_address not set")
            return False
        try:
            if self.signer.is_configured:
                await self.signer.execute_contract(
                    addr, "chargeActions(bytes32,uint256)", [to_bytes32(sub_id), action_count]
                )
            else:
                c = self.loader._contract(addr, "SubscriptionManager")
                tx = await c.functions.chargeActions(to_bytes32(sub_id), action_count).build_transaction(
                    {
                        "from": self.settings.agent_account.address,
                        "nonce": await self.loader.client.w3.eth.get_transaction_count(
                            self.settings.agent_account.address
                        ),
                        "gas": 200_000,
                        "gasPrice": await self.loader.client.w3.eth.gas_price,
                    }
                )
                signed = self.settings.agent_account.sign_transaction(tx)
                h = await self.loader.client.w3.eth.send_raw_transaction(signed.raw_transaction)
                await self.loader.client.w3.eth.wait_for_transaction_receipt(h)
            return True
        except Exception as exc:
            logger.warning("chargeActions failed for %s: %s", sub_id, exc)
            return False

    async def _apply_to_subscriber(
        self, sub: Subscriber, _trades: list[TradeOrder], target_weights: dict[str, float], _tick_id: str
    ) -> None:
        """In-process fan-out (D-FANOUT): map publisher trades to the subscriber's own
        vault via read_portfolio + compute_trades, then execute_trades on sub.vault_address."""
        if self.dry_run:
            return
        try:
            sub_portfolio = await self.executor.read_portfolio(sub.vault_address)
            sub_trades = compute_trades(sub_portfolio, target_weights)
            await self.executor.execute_trades(sub.vault_address, sub_trades)
        except Exception:
            logger.exception("subscriber apply failed for %s", sub.sub_id)
