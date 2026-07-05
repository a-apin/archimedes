"""StrategyRunner tick-pipeline coverage (#738 Tier-A + behavior c).

Target: backend/archimedes/chain/agent_runner.py
Complements test_agent_runner.py (deterministic _compute_trades / regime /
dedup) by exercising the *full tick pipeline* and the on-chain commit/reveal +
vault-discovery surface:

  behavior c — one tick (mocked chain/redis/oracle) flows
  strategies → signals → regime classification → position-scale (portfolio
  constructor) → target allocations → per-vault processing.

Plus: _get_managed_vaults, _discover_new_vaults, _commit_trace / _reveal_trace
(DRY_RUN so no on-chain), and the run() loop body.

Hermetic: every boundary (strategy provider/evaluator, oracle, regime detector,
portfolio constructor, chain executor, trace publisher, Redis AgentStateStore)
is mocked. No network, no Arc RPC, no Circle, no Redis.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from archimedes.models.portfolio import Portfolio, PortfolioHolding, TargetAllocation
from archimedes.models.regime import ConsensusLabel, EnsembleConsensus, Regime, RegimeClassification
from archimedes.services.strategy_signal_evaluator import AssetSignal, Signal, StrategySignals

# ── Builders ──────────────────────────────────────────────────


def _signals(asset: str = "sSPY", weight: float = 0.6, sig: Signal = Signal.LONG) -> StrategySignals:
    return StrategySignals(
        strategy_id="faber_001",
        strategy_name="Faber SMA200",
        paper_title="A Quantitative Approach to Tactical Asset Allocation",
        signals=[
            AssetSignal(
                strategy_id="faber_001",
                strategy_name="Faber SMA200",
                asset=asset,
                signal=sig,
                weight=weight,
                reason="Price above SMA200",
            )
        ],
        paper_arxiv_id="0001",
    )


def _allocs(**weights: float) -> list[TargetAllocation]:
    return [TargetAllocation(symbol=s, token_address="", weight=w, strategy_ids=[]) for s, w in weights.items()]


def _portfolio(total: float = 1000.0) -> Portfolio:
    return Portfolio(
        vault_address="0xVault",
        total_value_usdc=total,
        holdings=[PortfolioHolding(symbol="USDC", token_address="0xusdc", amount=total, weight=1.0, value_usdc=total)],
        risk_profile="moderate",
    )


def _regime() -> RegimeClassification:
    cls = MagicMock(spec=RegimeClassification)
    cls.regime = Regime.RISK_ON
    cls.confidence = 0.9
    cls.regime_changed = False
    cls.signals = MagicMock(vix_level=13.0)
    return cls


def _consensus() -> EnsembleConsensus:
    return EnsembleConsensus(flat_pct=0.1, signal_count=3, label=ConsensusLabel.RISK_ON)


@pytest.fixture
def runner_env(monkeypatch):
    """A StrategyRunner with every chain/redis/oracle boundary mocked.

    Yields (runner, mocks-dict). DRY_RUN is forced on so the commit/reveal path
    builds + hashes traces without touching the chain.
    """
    monkeypatch.setattr("archimedes.chain.agent_runner.DRY_RUN", True)
    monkeypatch.setattr("archimedes.chain.agent_runner.EXPLICIT_VAULTS", "")
    with (
        patch("archimedes.chain.agent_runner.chain_client") as mock_client,
        patch("archimedes.chain.agent_runner.chain_executor") as mock_executor,
        patch("archimedes.chain.agent_runner.trace_publisher") as mock_publisher,
        patch("archimedes.chain.agent_runner.default_provider") as mock_provider,
        patch("archimedes.chain.agent_runner.AgentStateStore") as mock_state_cls,
        patch("archimedes.chain.agent_runner.strategy_evaluator") as mock_eval,
    ):
        mock_client.settings = MagicMock(
            synth_addresses={"sSPY": "0xsspy", "sGOLD": "0xsgold"},
            usdc_address="0xusdc",
            oracle_addresses={"sSPY": "0xoraclespy"},
        )
        mock_client.to_checksum = lambda a: a
        # Strategy provider returns a couple of strategies.
        strat = MagicMock(paper_title="Faber SMA200", id="faber_001")
        mock_provider.return_value.list_strategies.return_value = [strat]

        # Evaluator: signals + aggregate weights.
        mock_eval.evaluate_strategies.return_value = [_signals()]
        mock_eval.aggregate_signals.return_value = {"sSPY": 0.6, "USDC": 0.4}

        # Redis state store: all awaitables no-op.
        state = mock_state_cls.return_value
        state.save_regime = AsyncMock()
        state.save_ensemble_consensus = AsyncMock()
        state.save_heartbeat = AsyncMock()
        state.save_trace = AsyncMock()
        state.get_last_trace = AsyncMock(return_value=None)
        state.save_last_rebalance = AsyncMock()

        from archimedes.chain.agent_runner import StrategyRunner

        runner = StrategyRunner()
        # Position-scaler (portfolio constructor) returns scaled allocations.
        runner.portfolio_constructor = MagicMock()
        runner.portfolio_constructor.construct.return_value = _allocs(sSPY=0.6, USDC=0.4)
        # Regime detector + oracle snapshot.
        runner.oracle = MagicMock()
        runner.oracle.fetch_market_snapshot = AsyncMock(return_value=MagicMock(has_regime_signals=True))
        runner.regime_detector = MagicMock()
        runner.regime_detector.classify.return_value = _regime()

        yield (
            runner,
            {
                "client": mock_client,
                "executor": mock_executor,
                "publisher": mock_publisher,
                "eval": mock_eval,
                "state": state,
            },
        )


# ── behavior c: full tick pipeline ────────────────────────────


class TestTickPipeline:
    async def test_tick_flows_signals_regime_scale_allocations(self, runner_env):
        runner, m = runner_env
        # One managed vault with an empty (USDC-only) portfolio.
        m["executor"].get_all_vaults = AsyncMock(return_value=["0xVault"])
        m["executor"].read_portfolio = AsyncMock(return_value=_portfolio())
        m["executor"].set_token_oracles = AsyncMock()
        m["executor"].set_target_allocations = AsyncMock()
        # No metadata → legacy/global-consensus path.
        runner._get_vault_strategy_ids = MagicMock(return_value=None)

        await runner.tick()

        # Pipeline stages all fired:
        m["eval"].evaluate_strategies.assert_called_once()
        m["eval"].aggregate_signals.assert_called()  # global aggregate
        runner.regime_detector.classify.assert_called_once()  # regime classified
        # Position-scaler (portfolio constructor) consumed regime + consensus.
        runner.portfolio_constructor.construct.assert_called()
        ckw = runner.portfolio_constructor.construct.call_args.kwargs
        assert ckw["regime"] is not None
        assert isinstance(ckw["ensemble_consensus"], EnsembleConsensus)
        assert ckw["base_weights"] == {"sSPY": 0.6, "USDC": 0.4}
        # Regime + consensus persisted to Redis.
        m["state"].save_regime.assert_awaited()
        m["state"].save_ensemble_consensus.assert_awaited()
        m["state"].save_heartbeat.assert_awaited()

    async def test_tick_no_strategies_returns_early(self, runner_env):
        runner, m = runner_env
        runner.provider.list_strategies = MagicMock(return_value=[])
        await runner.tick()
        # Bailed before evaluating signals.
        m["eval"].evaluate_strategies.assert_not_called()

    async def test_tick_no_vaults_returns_after_regime(self, runner_env):
        runner, m = runner_env
        m["executor"].get_all_vaults = AsyncMock(return_value=[])
        await runner.tick()
        # Regime still classified, but no vault processed → no portfolio reads.
        runner.regime_detector.classify.assert_called_once()
        m["executor"].read_portfolio.assert_not_called()

    async def test_tick_scoped_vault_filters_signals(self, runner_env):
        runner, m = runner_env
        m["executor"].get_all_vaults = AsyncMock(return_value=["0xVault"])
        m["executor"].read_portfolio = AsyncMock(return_value=_portfolio())
        m["executor"].set_token_oracles = AsyncMock()
        m["executor"].set_target_allocations = AsyncMock()
        # Vault scoped to the strategy that produced signals → scoped path.
        runner._get_vault_strategy_ids = MagicMock(return_value=["faber_001"])
        await runner.tick()
        # aggregate_signals called for the per-vault scope (≥2 total calls).
        assert m["eval"].aggregate_signals.call_count >= 2


# ── vault discovery ───────────────────────────────────────────


class TestVaultDiscovery:
    async def test_get_managed_vaults_from_factory(self, runner_env):
        runner, m = runner_env
        m["executor"].get_all_vaults = AsyncMock(return_value=["0xA", "0xB"])
        vaults = await runner._get_managed_vaults()
        assert set(vaults) == {"0xA", "0xB"}
        assert runner._known_vaults == {"0xA", "0xB"}

    async def test_get_managed_vaults_factory_error_returns_empty(self, runner_env):
        runner, m = runner_env
        m["executor"].get_all_vaults = AsyncMock(side_effect=ConnectionError("RPC down"))
        assert await runner._get_managed_vaults() == []

    async def test_discover_new_vaults_returns_only_unseen(self, runner_env):
        runner, m = runner_env
        runner._known_vaults = {"0xA"}
        m["executor"].get_all_vaults = AsyncMock(return_value=["0xA", "0xB"])
        new = await runner._discover_new_vaults()
        assert new == ["0xB"]
        assert "0xB" in runner._known_vaults

    async def test_discover_new_vaults_none_when_all_known(self, runner_env):
        runner, m = runner_env
        runner._known_vaults = {"0xA", "0xB"}
        m["executor"].get_all_vaults = AsyncMock(return_value=["0xA", "0xB"])
        assert await runner._discover_new_vaults() == []

    async def test_discover_new_vaults_factory_error_returns_empty(self, runner_env):
        runner, m = runner_env
        m["executor"].get_all_vaults = AsyncMock(side_effect=RuntimeError("boom"))
        assert await runner._discover_new_vaults() == []


# ── commit / reveal (DRY_RUN) ─────────────────────────────────


class TestCommitRevealDryRun:
    @staticmethod
    def _trade():
        from archimedes.models.portfolio import TradeDirection, TradeOrder

        return TradeOrder(
            symbol="sSPY",
            token_address="0xsspy",
            direction=TradeDirection.BUY,
            amount=100.0,
            estimated_usdc_value=100.0,
        )

    async def test_commit_trace_dry_run_builds_and_hashes(self, runner_env):
        runner, _ = runner_env
        trace, trace_id, commit_tx, commit_block, claimed_time = await runner._commit_trace(
            "0xVault",
            [self._trade()],
            [_signals()],
            "risk_on",
            _consensus(),
            "tick-1",
            "reasoning text",
            _portfolio(),
            _allocs(sSPY=0.6, USDC=0.4),
        )
        # DRY_RUN → no on-chain ids, but the canonical trace IS built + hashed.
        assert trace_id is None and commit_tx is None and commit_block is None and claimed_time is None
        assert trace.trace_hash and len(trace.trace_hash.removeprefix("0x")) == 64
        assert trace.decision_type.value == "rebalance"
        # portfolio_after is hashed, so it carries the pre-trade INTENDED targets (#903).
        assert trace.portfolio_after["intended"] is True
        assert trace.portfolio_after["target_weights"] == {"sSPY": 0.6, "USDC": 0.4}

    async def test_reveal_trace_dry_run_persists_off_chain(self, runner_env):
        runner, m = runner_env
        # Build a trace via commit, then reveal it (DRY_RUN → no IPFS/chain).
        trace, trace_id, *_ = await runner._commit_trace(
            "0xVault",
            [self._trade()],
            [_signals()],
            "risk_on",
            _consensus(),
            "t",
            "r",
            _portfolio(),
            _allocs(sSPY=0.6, USDC=0.4),
        )
        await runner._reveal_trace(trace, trace_id, "tick-1", tx_hashes=[])
        # Off-chain persist happened with temporal_binding_source = "none" (dry-run).
        m["state"].save_trace.assert_awaited()
        saved = m["state"].save_trace.await_args.args[0]
        assert saved["temporal_binding_source"] == "none"
        assert saved["is_verified"] is False


# ── _publish_trace (legacy SKIP path) ─────────────────────────


class TestPublishTrace:
    async def test_empty_vault_publishes_skip_trace(self, runner_env):
        runner, m = runner_env
        # Empty portfolio → _process_vault publishes an "empty_vault" SKIP trace.
        m["executor"].read_portfolio = AsyncMock(
            return_value=Portfolio(vault_address="0xVault", total_value_usdc=0.0, holdings=[])
        )
        spy = AsyncMock(wraps=runner._publish_trace)
        runner._publish_trace = spy
        await runner._process_vault("0xVault", _allocs(sSPY=0.6), [_signals()], "risk_on", _consensus(), "tick-1")
        spy.assert_awaited_once()
        # The trigger names the empty-vault decision.
        assert spy.await_args.args[2] == "empty_vault"
        # The trace was hashed + persisted off-chain (DRY_RUN → no anchor).
        m["state"].save_trace.assert_awaited()
        saved = m["state"].save_trace.await_args.args[0]
        assert saved["decision_type"] == "skip"
        assert saved["is_verified"] is False
        assert len(saved["trace_hash"].removeprefix("0x")) == 64


# ── run() loop body ───────────────────────────────────────────


class TestRunLoop:
    async def test_run_executes_one_tick_then_sleeps(self, runner_env, monkeypatch):
        runner, m = runner_env
        m["client"].is_connected = AsyncMock(return_value=True)

        class _Stop(Exception):
            pass

        # Patch the module symbols run() uses, plus a sleep that breaks the loop.
        with (
            patch("archimedes.chain.agent_runner.StrategyRunner", return_value=runner),
            patch("archimedes.chain.agent_runner.chain_client", m["client"]),
            patch("archimedes.chain.agent_runner.asyncio.sleep", AsyncMock(side_effect=_Stop)),
        ):
            runner.tick = AsyncMock()
            runner.state.save_heartbeat = AsyncMock()
            from archimedes.chain.agent_runner import run

            with pytest.raises(_Stop):
                await run()
            runner.tick.assert_awaited_once()
