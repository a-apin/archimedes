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
from archimedes.chain.executor import InsufficientLiquidityError, chain_executor
from archimedes.chain.oracle_updater import OracleUpdater
from archimedes.chain.provenance_publisher import pin_public_provenance
from archimedes.chain.trace_publisher import trace_publisher
from archimedes.chain.v_check import VCheck
from archimedes.interfaces.math import IRegimeDetector
from archimedes.models.portfolio import (
    Portfolio,
    RiskProfile,
    TargetAllocation,
    TradeDirection,
    TradeOrder,
)
from archimedes.models.regime import EnsembleConsensus, RegimeClassification
from archimedes.models.trace import DecisionType, ReasoningTrace
from archimedes.services.gmm_regime_detector import GmmRegimeDetector
from archimedes.services.portfolio_constructor import PortfolioConstructor
from archimedes.services.redis_state import AgentStateStore
from archimedes.services.source_tracker import build_consulted_hashes
from archimedes.services.strategy_provider import default_provider
from archimedes.services.strategy_signal_evaluator import (
    StrategySignals,
    strategy_evaluator,
)
from archimedes.services.vix_regime_detector import VixRegimeDetector

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

# The exogenous market regime is detected each tick by VixRegimeDetector
# (issue #660) and written to KEY_REGIME. This is the value used only when that
# classification cannot be produced (snapshot fetch fails or lacks VIX/MA
# signals) — the regime then degrades honestly to "unknown". Distinct from the
# *endogenous* ensemble consensus (issue #659); traces carry both.
_MARKET_REGIME_UNKNOWN = "unknown"


def _compute_confidence(all_signals: list[StrategySignals]) -> float:
    """Compute dynamic confidence from signal consensus + magnitude.

    Combines two factors (Issue #359):
    1. Vote ratio: fraction of signals that are non-flat (directional)
    2. Signal strength: average weight magnitude across all directional signals
       (higher weights = stronger conviction = higher confidence)

    Formula: confidence = vote_ratio * (0.5 + 0.5 * avg_strength)
    Range: [0.0, 1.0] — varies naturally with signal composition.
    """
    total_signals = sum(len(ss.signals) for ss in all_signals)
    if total_signals == 0:
        return 0.5  # No data — neutral confidence

    directional = [s for ss in all_signals for s in ss.signals if s.signal.value != "flat"]
    flat_count = total_signals - len(directional)
    vote_ratio = 1.0 - (flat_count / total_signals)

    # Average magnitude of directional signals (weight represents conviction)
    if directional:
        avg_strength = sum(abs(s.weight) for s in directional) / len(directional)
        # Normalize: weights are typically 0.0-1.0, but clamp for safety
        avg_strength = min(avg_strength, 1.0)
    else:
        avg_strength = 0.0

    # Weight dispersion penalty: high disagreement in weights → lower confidence
    # std-dev of weights across all signals (not just directional)
    all_weights = [s.weight for ss in all_signals for s in ss.signals]
    if len(all_weights) >= 2:
        mean_w = sum(all_weights) / len(all_weights)
        variance = sum((w - mean_w) ** 2 for w in all_weights) / len(all_weights)
        dispersion = variance**0.5  # std dev, typically 0.0-0.3
        dispersion_penalty = min(dispersion * 2, 0.3)  # cap penalty at 0.3
    else:
        dispersion_penalty = 0.0

    # Final confidence: vote_ratio × strength_boost − dispersion_penalty
    raw = vote_ratio * (0.5 + 0.5 * avg_strength) - dispersion_penalty
    return max(0.05, min(0.99, round(raw, 4)))  # clamp + round to 4dp


def _paper_hashes_from_signals(all_signals: list[StrategySignals]) -> list[str]:
    """Build consulted-paper hash list (Xia § 4.3 Source Tracking) from strategy signals.

    Uses strategy_id as the content hash — it is a SHA-256 of paper + methodology,
    so it IS a stable content fingerprint even without a separate pdf_sha256.
    """
    papers = [{"arxiv_id": ss.paper_arxiv_id or ss.strategy_id, "content_hash": ss.strategy_id} for ss in all_signals]
    return build_consulted_hashes(papers)


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
        # Oracle for market snapshots (VIX + S&P MAs) feeding regime detection.
        self.oracle = OracleUpdater()
        # Exogenous market-regime classifier (issues #660, #661). Data-driven
        # 4-component Gaussian Mixture (Hamilton 1989 / Ang & Bekaert 2002 in
        # spirit) that DROPS IN for the rule-based detector — but falls back to
        # VixRegimeDetector whenever no fitted gmm_model.pkl is present, history
        # is too short (<22 days), or VIX/price are missing. Until the offline
        # fit (scripts/fit_gmm_regime.py) produces an artifact, this behaves
        # exactly like the rule-based VIX/MA classifier. Kept SEPARATE from the
        # endogenous EnsembleConsensus signal (issue #659): the two are
        # complementary; the regime says what the market is doing, the consensus
        # how decisive our strategies are.
        self.regime_detector: IRegimeDetector = GmmRegimeDetector(fallback=VixRegimeDetector())
        # Position sizer (issue #662): throttles aggregated target weights by
        # the exogenous market regime AND the endogenous ensemble consensus.
        self.portfolio_constructor: PortfolioConstructor = PortfolioConstructor()
        self._synth_addrs = chain_client.settings.synth_addresses
        self._usdc_addr = chain_client.settings.usdc_address
        self._known_vaults: set[str] = set()  # Vaults we've already seen
        # Dedup: track last reasoning per vault to avoid publishing identical traces
        self._last_reasoning: dict[str, str] = {}  # vault_address → reasoning text
        self._last_reasoning_count: dict[str, int] = {}  # vault_address → repeat count

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

            # Compute the ensemble consensus (how decisive the ensemble is).
            # NOTE: flat_pct is an *endogenous* ensemble-uncertainty signal — it
            # is NOT a market regime. We keep the flat_pct computation (it is a
            # useful consensus signal) but label it honestly as agent consensus
            # rather than overloading it onto the exogenous market regime (#659).
            flat_count = sum(1 for ss in all_signals for s in ss.signals if s.signal.value == "flat")
            total_count = sum(len(ss.signals) for ss in all_signals)
            consensus = EnsembleConsensus.from_signal_counts(flat_count, total_count)

            # Exogenous market regime (issue #660). Fetch a market snapshot
            # (VIX + S&P MAs) and classify it with the rule-based detector.
            # This is INDEPENDENT of the endogenous EnsembleConsensus above —
            # the regime says what the *market* is doing, the consensus says
            # how decisive *our strategies* are. Traces carry both.
            regime_classification, market_regime = await self._classify_market_regime(tick_id)

            # Persist the exogenous market regime under KEY_REGIME so the
            # /api/regime/current surface returns the *real* detector output
            # (it falls back to ensemble consensus only while this is absent —
            # see regime_routes.get_current_regime). Guarded: the classifier
            # returns None on snapshot-fetch failure or missing regime signals.
            if regime_classification is not None:
                await self.state.save_regime(regime_classification)

            # Persist the ensemble consensus under its OWN Redis key so it does
            # not shadow the market regime.
            await self.state.save_ensemble_consensus(consensus, all_signals)

            # 4. Build target allocations — throttle raw weights by the
            # exogenous market regime AND the endogenous ensemble consensus
            # (issue #662), then resolve symbols → token addresses.
            allocations = self.portfolio_constructor.construct(
                risk_profile=RiskProfile.MODERATE,
                strategies=strategies,
                backtest_results={},
                regime=regime_classification,
                ensemble_consensus=consensus,
                base_weights=target_weights,
            )
            targets = self._weights_to_targets({a.symbol: a.weight for a in allocations}, all_signals)

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

            # 6. Process each vault with per-vault strategy scoping (Issue #307)
            for vault_addr in vaults:
                try:
                    # Per-vault scoping: each vault executes only its selected strategies
                    vault_strategy_ids = self._get_vault_strategy_ids(vault_addr)

                    if vault_strategy_ids is None:
                        # Legacy vault (deployed before strategy-selection flow
                        # shipped, or via tooling that doesn't write VaultMetadata)
                        # — fall back to global consensus so existing vaults keep
                        # rebalancing. Vaults created via the UI's strategy-selection
                        # flow set strategy_ids and follow the scoped path below.
                        logger.info(
                            "[tick %s] Vault %s: no VaultMetadata (legacy) — using global consensus",
                            tick_id,
                            vault_addr[:10],
                        )
                        await self._process_vault(
                            vault_addr,
                            targets,
                            all_signals,
                            market_regime,
                            consensus,
                            tick_id,
                        )
                        continue

                    # Filter signals to this vault's strategies
                    scoped_signals = [ss for ss in all_signals if ss.strategy_id in vault_strategy_ids]

                    if not scoped_signals:
                        logger.warning(
                            "[tick %s] Vault %s strategy_ids=%s — none produced signals, skipping",
                            tick_id,
                            vault_addr[:10],
                            vault_strategy_ids,
                        )
                        continue

                    # Aggregate scoped signals into per-vault target weights,
                    # then throttle by regime + ensemble consensus (issue #662).
                    vault_weights = strategy_evaluator.aggregate_signals(
                        scoped_signals,
                        usdc_floor=USDC_FLOOR,
                    )
                    vault_allocations = self.portfolio_constructor.construct(
                        risk_profile=RiskProfile.MODERATE,
                        strategies=strategies,
                        backtest_results={},
                        regime=regime_classification,
                        ensemble_consensus=consensus,
                        base_weights=vault_weights,
                    )
                    vault_targets = self._weights_to_targets(
                        {a.symbol: a.weight for a in vault_allocations}, scoped_signals
                    )

                    logger.info(
                        "[tick %s] Vault %s: scoped to %d/%d strategies → %s",
                        tick_id,
                        vault_addr[:10],
                        len(scoped_signals),
                        len(all_signals),
                        " | ".join(f"{k}={v:.0%}" for k, v in vault_weights.items()),
                    )

                    await self._process_vault(
                        vault_addr,
                        vault_targets,
                        scoped_signals,
                        market_regime,
                        consensus,
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

    # ─── Exogenous market-regime classification (Issue #660) ─────────

    async def _classify_market_regime(self, tick_id: str) -> tuple[RegimeClassification | None, str]:
        """Fetch a market snapshot and classify the exogenous market regime.

        Returns ``(classification, regime_value)``. On any failure — snapshot
        fetch error, or a snapshot without enough regime signals (VIX / S&P
        MAs unavailable) — degrades gracefully to ``(None, "unknown")`` and
        logs a warning rather than crashing the tick.
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
                "[tick %s] Snapshot missing regime signals (VIX/MA unavailable) — regime=unknown",
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

    # ─── Per-vault strategy scoping (Issue #307) ─────────────────────

    def _get_vault_strategy_ids(self, vault_address: str) -> list[str] | None:
        """Load strategy_ids from VaultMetadata for a vault.

        Returns:
            list[str] — the user's selected strategies for this vault.
            None — if no metadata exists (vault deployed outside UI).
        """
        try:
            from archimedes.db import get_session
            from archimedes.models.chat import VaultMetadata

            with get_session() as session:
                meta = session.query(VaultMetadata).filter(VaultMetadata.vault_address == vault_address).first()
                if meta is None:
                    return None
                ids = meta.get_strategy_ids()
                return ids if ids else None
        except Exception as exc:
            logger.debug("_get_vault_strategy_ids(%s) failed: %s", vault_address[:10], exc)
            return None

    async def _process_vault(
        self,
        vault_address: str,
        targets: list[TargetAllocation],
        all_signals: list[StrategySignals],
        market_regime: str,
        consensus: EnsembleConsensus,
        tick_id: str,
    ) -> None:
        """Process a single vault: read portfolio → diff → maybe trade → publish trace.

        ``market_regime`` is the exogenous regime (``"unknown"`` until a detector
        is wired); ``consensus`` is the endogenous ensemble consensus (#659).
        """
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
                    market_regime,
                    consensus,
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
            # Deduplicate identical aligned decisions: when the portfolio stays
            # within drift thresholds tick after tick with the same reasoning,
            # re-anchoring the SKIP trace wastes gas and clutters the Reasoning
            # page. Increment a repeat counter and skip the publish instead.
            aligned_reasoning = self._build_reasoning(all_signals, market_regime, consensus, trades, portfolio)
            if self._last_reasoning.get(vault_address) == aligned_reasoning:
                count = self._last_reasoning_count.get(vault_address, 1) + 1
                self._last_reasoning_count[vault_address] = count
                logger.info(
                    "[tick %s] Vault %s: identical aligned decision (repeat #%d) — skipping trace publish",
                    tick_id,
                    vault_address[:10],
                    count,
                )
                return
            self._last_reasoning[vault_address] = aligned_reasoning
            self._last_reasoning_count[vault_address] = 1
            await self._publish_trace(
                vault_address,
                DecisionType.SKIP,
                "aligned",
                portfolio,
                [],
                all_signals,
                market_regime,
                consensus,
                tick_id,
                "Portfolio aligned with strategy signals. No rebalance needed.",
            )
            return

        # Log the trade plan
        logger.info(
            "[tick %s] REBALANCE vault %s: %d trades (market_regime=%s, consensus=%s)",
            tick_id,
            vault_address[:10],
            len(trades),
            market_regime,
            consensus.label.value,
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
        # Fix float→int rounding drift: individually rounded weights can
        # sum to 10001 or 9999 instead of 10000. Adjust the largest weight
        # to absorb the residual (max 1 BPS correction).
        if alloc_weights_bps:
            residual = sum(alloc_weights_bps.values()) - 10000
            if residual != 0:
                largest = max(alloc_weights_bps, key=alloc_weights_bps.get)
                alloc_weights_bps[largest] -= residual
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
                market_regime,
                consensus,
                tick_id,
                f"V_check rejected: {'; '.join(v_result.failures)}",
            )
            return

        # ── Commit-Reveal Flow ────────────────────────────────────
        # Phase 1: COMMIT — compute hash and anchor on-chain BEFORE trade
        reasoning = self._build_reasoning(all_signals, market_regime, consensus, trades, portfolio)

        # A rebalance always publishes — trades are real actions that must be
        # anchored, so identical reasoning is never deduplicated here. Record
        # this decision so the next aligned (no-trade) tick dedups against the
        # most recently published reasoning (the no-trade branch above is the
        # only place identical decisions are skipped).
        self._last_reasoning[vault_address] = reasoning
        self._last_reasoning_count[vault_address] = 1

        commit_tx = None
        commit_block = None
        commit_trace_id: int | None = None

        # Build + commit the canonical trace ONCE. The same trace object is revealed
        # after settlement so the on-chain keccak256 binding holds. In dry-run we still
        # build it (no on-chain commit) so the reveal/persist path has a trace to use.
        committed_trace, commit_trace_id, commit_tx, commit_block = await self._commit_trace(
            vault_address,
            trades,
            all_signals,
            market_regime,
            consensus,
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
                        logger.debug("trade receipt block lookup failed", exc_info=True)
                logger.info(
                    "[tick %s] Executed %d trades: %s",
                    tick_id,
                    len(tx_hashes),
                    [h[:16] for h in tx_hashes],
                )
                await self.state.save_last_rebalance(vault_address)
            except InsufficientLiquidityError as e:
                logger.warning(
                    "[tick %s] Vault %s: swap skipped — thin pool: %s; will retry next tick",
                    tick_id,
                    vault_address[:10],
                    e,
                )
                await self._publish_trace(
                    vault_address,
                    DecisionType.SKIP,
                    "insufficient_liquidity",
                    portfolio,
                    [],
                    all_signals,
                    market_regime,
                    consensus,
                    tick_id,
                    f"Swap skipped — thin pool: {e}",
                    commit_tx=commit_tx,
                    commit_block=commit_block,
                )
                return
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
                    market_regime,
                    consensus,
                    tick_id,
                    f"Execution failed: {e}",
                    commit_tx=commit_tx,
                    commit_block=commit_block,
                    error=str(e),
                )
                return

        # Phase 3: REVEAL — pin public provenance + reveal the SAME committed trace
        await self._reveal_trace(
            committed_trace,
            commit_trace_id,
            tick_id,
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

    # How far in the future the committed execution is claimed to land. The
    # contract requires claimedExecutionTime > commit-block timestamp, so the
    # reveal can only succeed once this lead window has elapsed — proving the
    # trade landed strictly after the commit (causal ordering).
    _COMMIT_EXECUTION_LEAD_S = 60

    async def _commit_trace(
        self,
        vault_address: str,
        trades: list[TradeOrder],
        all_signals: list[StrategySignals],
        market_regime: str,
        consensus: EnsembleConsensus,
        tick_id: str,
        reasoning: str,
        portfolio: Portfolio,
    ) -> tuple[ReasoningTrace, int | None, str | None, int | None]:
        """Commit phase: anchor the trace hash on-chain BEFORE the trade executes.

        Builds the canonical trace ONCE and commits its keccak256 via the registry's
        real ``commit()`` (with claimedExecutionTime) so the same bytes can be revealed
        after settlement and the on-chain hash binding holds. Falls back to the v1
        ``publishTrace`` anchor when the deployed registry is pre-v1.5 (#588 redeploy).

        Returns (trace, on_chain_trace_id, commit_tx_hash, commit_block_number).
        The trace is always returned so reveal() can submit the identical content;
        trace_id is None when commit-reveal is unavailable (publishTrace fallback).
        """
        trace = ReasoningTrace(
            id=str(uuid.uuid4()),
            vault_address=vault_address,
            decision_type=DecisionType.REBALANCE,
            trigger="strategy_signal_drift",
            timestamp=datetime.now(UTC),
            market_context={
                "regime": market_regime,
                "ensemble_consensus": consensus.label.value,
                "ensemble_flat_pct": round(consensus.flat_pct, 4),
                "strategy_count": len(all_signals),
            },
            portfolio_before={
                "vault": vault_address[:10],
                "aum_usdc": portfolio.total_value_usdc,
                "holdings": {
                    h.symbol: {"weight": f"{h.weight:.1%}", "value_usdc": h.value_usdc} for h in portfolio.holdings
                },
            },
            reasoning=reasoning,
            confidence=_compute_confidence(all_signals),
            trades_executed=[{"symbol": t.symbol, "direction": t.direction.value, "amount": t.amount} for t in trades],
            strategies_referenced=[ss.strategy_id for ss in all_signals],
            consulted_paper_hashes=_paper_hashes_from_signals(all_signals),
        )

        trace.compute_hash()
        logger.info("[tick %s] COMMIT hash: %s", tick_id, trace.trace_hash[:16])

        if DRY_RUN:
            logger.info("[tick %s] DRY RUN — skipping on-chain commit", tick_id)
            return trace, None, None, None

        claimed_execution_time = int(datetime.now(UTC).timestamp()) + self._COMMIT_EXECUTION_LEAD_S
        intent_summary = f"{trace.decision_type.value}:{len(trades)}".encode()

        try:
            if trace_publisher.supports_commit_reveal():
                trace_id, commit_tx, commit_block = await trace_publisher.commit(
                    trace, claimed_execution_time, intent_summary
                )
                if commit_tx:
                    logger.info(
                        "[tick %s] COMMIT anchored (commit-reveal): id=%s tx=%s block=%s",
                        tick_id,
                        trace_id,
                        commit_tx[:16],
                        commit_block,
                    )
                return trace, trace_id, commit_tx, commit_block

            # Fallback: v1 publishTrace anchor (no temporal binding).
            arc_tx = await trace_publisher.publish(trace)
            commit_block = None
            if arc_tx:
                try:
                    receipt = await chain_client.w3.eth.get_transaction_receipt(
                        chain_client.w3.to_bytes(hexstr=arc_tx.removeprefix("0x"))
                    )
                    commit_block = receipt.blockNumber
                except Exception as e:
                    logger.warning("[tick %s] Cannot get commit block: %s", tick_id, e)
                logger.info(
                    "[tick %s] COMMIT anchored (publishTrace fallback): tx=%s block=%s",
                    tick_id,
                    arc_tx[:16],
                    commit_block,
                )
            return trace, None, arc_tx, commit_block
        except Exception as e:
            logger.error("[tick %s] COMMIT FAILED: %s", tick_id, e)
            return trace, None, None, None

    async def _reveal_trace(
        self,
        trace: ReasoningTrace,
        trace_id: int | None,
        tick_id: str,
        tx_hashes: list[str] | None = None,
        commit_tx: str | None = None,
        commit_block: int | None = None,
        trade_block: int | None = None,
    ) -> None:
        """Reveal phase: pin public provenance to IPFS, then reveal the SAME trace.

        ``trace`` is the exact object committed in the commit phase — we do NOT rebuild
        it, so the canonical bytes revealed on-chain match the committed hash. Steps:
          1. Pin the PUBLIC provenance layer (papers/methodology/rigor — not executable
             params) to IPFS → CID. Degrades loudly to hash-only if PINATA_JWT is unset.
          2. reveal(trace_id, cid, canonicalBytes) — contract recomputes keccak256 and
             enforces commit block < execution < reveal block.
          3. Fall back to publishTrace if the deployed registry is pre-v1.5 (#588).
        """
        # Annotate the (already-committed) trace with trade settlement data. These
        # fields are NOT in the hashed canonical set (_HASH_FIELDS), so adding them
        # does not change trace_hash — the commit binding stays intact.
        trace.portfolio_after = {"tx_hashes": tx_hashes or []}
        trace.commit_tx_hash = commit_tx
        trace.commit_block_number = commit_block
        trace.trade_tx_hash = tx_hashes[0] if tx_hashes else None
        trace.trade_block_number = trade_block

        logger.info("[tick %s] REVEAL hash: %s", tick_id, (trace.trace_hash or trace.compute_hash())[:16])

        reveal_tx = None
        reveal_block = None
        ipfs_cid = None
        if not DRY_RUN:
            # ── 1. Pin public provenance to IPFS (loud-degrade if unconfigured) ──
            try:
                ipfs_cid, _payload = await pin_public_provenance(trace)
                if ipfs_cid:
                    logger.info("[tick %s] Public provenance pinned: cid=%s", tick_id, ipfs_cid)
            except Exception as e:
                logger.error("[tick %s] IPFS pin FAILED (continuing hash-only): %s", tick_id, e)

            # ── 2. Reveal on-chain, anchoring the CID as the storage pointer ──
            try:
                if trace_id is not None and trace_publisher.supports_commit_reveal():
                    reveal_tx, reveal_block = await trace_publisher.reveal(
                        trace_id, trace, storage_pointer=ipfs_cid or ""
                    )
                else:
                    # Pre-v1.5 registry: anchor the canonical content via publishTrace.
                    reveal_tx = await trace_publisher.publish(trace)
                    if reveal_tx:
                        try:
                            receipt = await chain_client.w3.eth.get_transaction_receipt(
                                chain_client.w3.to_bytes(hexstr=reveal_tx.removeprefix("0x"))
                            )
                            reveal_block = receipt.blockNumber
                        except Exception:
                            logger.debug("reveal receipt block lookup failed", exc_info=True)
                if reveal_tx:
                    logger.info(
                        "[tick %s] REVEAL anchored: tx=%s block=%s cid=%s",
                        tick_id,
                        reveal_tx[:16],
                        reveal_block,
                        ipfs_cid,
                    )
            except Exception as e:
                logger.error("[tick %s] REVEAL publish FAILED: %s", tick_id, e)

        # Persist off-chain with full temporal binding + IPFS pointer
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
                # IPFS provenance pointer (T1.4)
                "ipfs_cid": ipfs_cid,
                # Temporal binding fields. The "chain" source — and any True binding —
                # require the REAL commit-reveal path: trace_id is non-None ONLY when
                # ReasoningTraceRegistry.commit() minted an on-chain id (the publishTrace
                # fallback returns trace_id=None but still carries a commit_block from its
                # receipt). Gating on trace_id stops a fallback anchor's blocks from
                # masquerading as a verified temporal binding (#714 / AUDIT_2026-06-14 #3).
                "commit_tx_hash": commit_tx,
                "commit_block_number": commit_block,
                "reveal_tx_hash": reveal_tx,
                "reveal_block_number": reveal_block,
                "trade_tx_hash": tx_hashes[0] if tx_hashes else None,
                "trade_block_number": trade_block,
                "temporal_binding_source": "chain" if trace_id is not None else "none",
                "temporal_binding_valid": (
                    trace_id is not None
                    and commit_block is not None
                    and trade_block is not None
                    and reveal_block is not None
                    and commit_block < trade_block <= reveal_block
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
        market_regime: str,
        consensus: EnsembleConsensus,
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
                "regime": market_regime,
                "ensemble_consensus": consensus.label.value,
                "ensemble_flat_pct": round(consensus.flat_pct, 4),
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
            confidence=_compute_confidence(all_signals),
            trades_executed=[{"symbol": t.symbol, "direction": t.direction.value, "amount": t.amount} for t in trades],
            strategies_referenced=[ss.strategy_id for ss in all_signals],
            consulted_paper_hashes=_paper_hashes_from_signals(all_signals),
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
        market_regime: str,
        consensus: EnsembleConsensus,
        trades: list[TradeOrder],
        portfolio: Portfolio | None = None,
    ) -> str:
        """Build human-readable reasoning from strategy signals.

        Includes portfolio context + trade specifics so each tick's reasoning
        is distinguishable even when the underlying strategy consensus is
        identical (addresses issue #334).

        Market regime (exogenous) and ensemble consensus (endogenous) are
        surfaced as distinct lines — the consensus is derived from strategy
        signals; the market regime is not (issue #659).
        """
        parts = [
            f"Market regime: {market_regime}",
            f"Ensemble consensus: {consensus.label.value} "
            f"(flat_pct={consensus.flat_pct:.0%}, derived from strategy signals)",
        ]
        for ss in all_signals:
            signal_strs = [f"{s.asset}={s.signal.value}" for s in ss.signals[:4]]
            parts.append(f"{ss.paper_title[:35]}: {', '.join(signal_strs)}")
        if portfolio and portfolio.holdings:
            top = sorted(portfolio.holdings, key=lambda h: h.value_usdc, reverse=True)[:3]
            parts.append("Portfolio: " + ", ".join(f"{h.symbol} {h.weight:.0%}" for h in top))
        if trades:
            parts.append("Trades: " + ", ".join(f"{t.direction.value} {t.amount:.2f} {t.symbol}" for t in trades[:4]))
        else:
            parts.append("No trades — within drift threshold")
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
            # T0.2 — non-custodial: there is no user wallet at auto-create time, so
            # create_vault resolves the owner from ARC_VAULT_GOVERNANCE_ADDRESS (a
            # cold key DISTINCT from the agent). If that env var is unset, the
            # vault stays backend-owned and create_vault logs a loud warning — it
            # NEVER silently hands ownership to the agent. Set
            # ARC_VAULT_GOVERNANCE_ADDRESS in the runner's env to make
            # auto-created demo vaults non-custodial.
            vault_address = await chain_executor.create_vault(
                name="Archimedes Momentum",
                symbol="vMOMENTUM",
                management_fee_bps=100,
                performance_fee_bps=1500,
                agent_assisted=True,
                owner_wallet=None,  # → governance address, or warn-and-stay-backend-owned
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

        current = {chain_client.to_checksum(v) for v in all_vaults}
        known = {chain_client.to_checksum(v) for v in self._known_vaults}
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
