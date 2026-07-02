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
from dataclasses import dataclass, field

from archimedes.chain.circle_signer import circle_signer
from archimedes.chain.client import chain_client
from archimedes.chain.executor import chain_executor
from archimedes.chain.oracle_updater import OracleUpdater
from archimedes.chain.v_check import VCheck
from archimedes.db import get_session
from archimedes.interfaces.math import IRegimeDetector
from archimedes.marketplace import payments
from archimedes.marketplace.encoding import to_bytes32
from archimedes.marketplace.state import MarketState
from archimedes.models.marketplace import MarketplaceAgent, SubscriberLiability
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
        self.settings = chain_client.settings
        self.signer = circle_signer
        self.executor = chain_executor
        self.loader = chain_executor.loader
        self.state = MarketState()
        self.provider = default_provider()
        self.interval = interval_seconds
        self.dry_run = dry_run
        self.publishers: dict[str, Publisher] = {}  # strategy_id -> Publisher
        self._stop = asyncio.Event()
        # Regime detection (same pattern as agent_runner)
        self.oracle = OracleUpdater()
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
            # ── Step 2-3: Evaluate + aggregate signals ─────────────────
            target_weights, all_signals = await self._evaluate(strategy_id)
            if not target_weights:
                logger.info("[%s] No target weights — skipping tick", tick_id)
                return

            await self.state.append_event(
                strategy_id,
                {
                    "type": "evaluation_step",
                    "tick_id": tick_id,
                    "target_weights": target_weights,
                },
            )

            # ── Step 4: Ensemble consensus ─────────────────────────────
            flat_count = sum(1 for ss in all_signals for s in ss.signals if s.signal.value == "flat")
            total_count = sum(len(ss.signals) for ss in all_signals)
            if total_count > 0:
                consensus = EnsembleConsensus.from_signal_counts(flat_count, total_count)
            else:
                consensus = None

            # ── Steps 5 & 6: Regime classify + persist (M1 gate) ──────
            if await self.state.try_acquire_regime_lock():
                regime_classification, market_regime = await self._classify_market_regime(tick_id)
                if regime_classification is not None:
                    await self.state.store.save_regime(regime_classification)
            else:
                cached = await self.state.store.load_regime()
                if cached:
                    regime_classification = _regime_classification_from_cache(cached)
                    market_regime = regime_classification.regime.value if regime_classification else _MARKET_REGIME_UNKNOWN
                else:
                    regime_classification = None
                    market_regime = _MARKET_REGIME_UNKNOWN

            # ── Step 7: Persist ensemble consensus (per-publisher key) ─
            if consensus is not None:
                await self._save_publisher_consensus(strategy_id, consensus, all_signals)

            # ── Step 8: Position-scale throttle via PortfolioConstructor ─
            strategy = self.provider.get_strategy(strategy_id)
            strategies = [strategy] if strategy else []
            allocations = self.portfolio_constructor.construct(
                risk_profile=RiskProfile.MODERATE,
                strategies=strategies,
                backtest_results={},
                regime=regime_classification,
                ensemble_consensus=consensus,
                base_weights=target_weights,
            )

            # ── Step 9: Weights → targets with provenance ──────────────
            targets = self._weights_to_targets(
                {a.symbol: a.weight for a in allocations}, all_signals
            )

            # ── Step 13.1: Read portfolio ──────────────────────────────
            portfolio = await self.executor.read_portfolio(pub.vault_address)

            # ── Step 13.3: Set token oracles ───────────────────────────
            try:
                oracle_tokens = []
                oracle_addrs = []
                for t in targets:
                    if t.weight > 0 and t.token_address:
                        symbol = t.symbol
                        if symbol == "USDC":
                            continue
                        oracle_addr = self.settings.oracle_addresses.get(symbol)
                        if oracle_addr:
                            oracle_tokens.append(t.token_address)
                            oracle_addrs.append(oracle_addr)

                if oracle_tokens:
                    await self.executor.set_token_oracles(
                        pub.vault_address,
                        oracle_tokens,
                        oracle_addrs,
                    )
                    logger.info(
                        "[%s] Set %d token oracles on vault %s",
                        tick_id,
                        len(oracle_tokens),
                        pub.vault_address[:10],
                    )
            except Exception as e:
                logger.warning(
                    "[%s] Failed to set token oracles on %s: %s",
                    tick_id,
                    pub.vault_address[:10],
                    e,
                )

            # ── Step 13.4: Set target allocations (normalize 10000 BPS) ─
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

                    await self.executor.set_target_allocations(
                        pub.vault_address,
                        alloc_tokens,
                        alloc_weights,
                    )
                    logger.info(
                        "[%s] Set target allocations on vault %s",
                        tick_id,
                        pub.vault_address[:10],
                    )
            except Exception as e:
                logger.warning(
                    "[%s] Failed to set allocations on %s: %s",
                    tick_id,
                    pub.vault_address[:10],
                    e,
                )

            # ── Step 13.5: Compute trades ──────────────────────────────
            trades = compute_trades(portfolio, target_weights, token_addresses=addr_map)

            if not trades:
                logger.info("[%s] No trades needed", tick_id)
                # ── Step 13.11 (no-op): no trades → no artifacts ───────
                return

            action_count = len(trades)

            # ── Step 13.7: VCheck gate ──────────────────────────────────
            alloc_weights_bps: dict[str, int] = {}
            for t in targets:
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
                logger.warning(
                    "[%s] V_check FAILED for %s: %s — skipping tick",
                    tick_id,
                    pub.vault_address[:10],
                    "; ".join(v_result.failures),
                )
                await self.state.append_event(strategy_id, {
                    "type": "skip", "reason": "v_check_failed",
                    "tick_id": tick_id, "failures": v_result.failures,
                })
                return

            # ── Step 2 (subscriber block, left untouched per spec) ─────
            # Verify x402 payment for each active subscriber, then apply
            # in-process trades.  Bound concurrency to 5 simultaneous
            # subscribers (M6).
            _sub_sem = asyncio.Semaphore(5)

            async def _process_subscriber(sub: Subscriber) -> None:
                async with _sub_sem:
                    paid = await self._verify_payment(
                        sub, strategy_id, tick_id, action_count,
                    )
                    if not paid:
                        sub.active = False
                        self._persist_halt_state(strategy_id, sub.sub_id)
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
            await self.state.renew_leader(strategy_id, token=leader_token)

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

            # ── Step 13.10: Execute publisher vault trades ─────────────
            if not self.dry_run:
                try:
                    await self.executor.execute_trades(pub.vault_address, trades)
                    # ── Step 13.11: Post-rebalance audit artifacts ──────
                    await self.state.store.save_last_rebalance(pub.vault_address)
                    await self.state.append_event(strategy_id, {
                        "type": "rebalance", "tick_id": tick_id,
                        "action_count": action_count, "target_weights": target_weights,
                    })
                except Exception:
                    logger.exception("publisher vault rebalance failed for %s", strategy_id)

            await self.state.save_subscribers(
                strategy_id,
                {sid: vars(s) for sid, s in pub.subscribers.items()},
            )
        finally:
            await self.state.release_leader(strategy_id, token=leader_token)

    # ---- helpers ---------------------------------------------------------

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
                tick_id, e,
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
                tick_id, e,
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
        sub: Subscriber,
        strategy_id: str,
        tick_id: str,
        action_count: int,
    ) -> bool:
        """Charge one subscriber for this tick via Circle's Gateway (x402).

        Builds payment requirements, signs them in-process with the
        subscriber's ephemeral key, and verify+settles through Circle's
        facilitator (circlekit). Circle batches and settles on-chain on its
        own cadence — no settlement logic lives here.

        In dry-run mode all payments are considered valid so the engine is
        exercisable without a real gateway.
        """
        if self.dry_run:
            return True
        eph_key = await self.state.get_ephemeral_key(sub.sub_id)
        if not eph_key:
            logger.warning(
                "[%s] no ephemeral key stored for sub %s — treating as unpaid",
                tick_id, sub.sub_id,
            )
            return False
        return await payments.charge(
            sub_id=sub.sub_id,
            ephemeral_key=eph_key,
            strategy_id=strategy_id,
            tick_id=tick_id,
            action_count=action_count,
            flat_fee_raw=FLAT_FEE_PER_ACTION,
        )

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
