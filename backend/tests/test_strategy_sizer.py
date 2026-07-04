"""Hermetic tests for strategy_sizer — Kelly-sized vault allocation (Priority 3.1).

Pure computation tests (no chain, no DB, no network) plus an endpoint test for
the derive-allocations wiring using the boundary-mock pattern from
test_api_routes.py (mock chain_client / strategy_evaluator / strategy_provider,
never internals).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from unittest.mock import MagicMock, patch

import pytest
from archimedes.services.strategy_signal_evaluator import AssetSignal, Signal, StrategySignals
from archimedes.services.strategy_sizer import (
    kelly_multiplier,
    kelly_weighted_allocations,
    scale_to_budget,
    size_strategies,
)

# ── helpers ────────────────────────────────────────────────────────────────────


@dataclass
class _Strat:
    """Minimal stand-in carrying only the fields the sizer reads."""

    id: str
    passes_rigor_gate: bool = False
    kelly_fraction: float | None = None


def _signals(strategy_id: str, votes: dict[str, float]) -> StrategySignals:
    return StrategySignals(
        strategy_id=strategy_id,
        strategy_name=strategy_id,
        paper_title=strategy_id,
        signals=[
            AssetSignal(
                strategy_id=strategy_id,
                strategy_name=strategy_id,
                asset=asset,
                signal=Signal.LONG if w > 0 else Signal.FLAT,
                weight=w,
                reason="test",
            )
            for asset, w in votes.items()
        ],
    )


# ── kelly_multiplier: 2/γ from the optimizer's RISK_AVERSION, capped at 1 ──────


def test_multiplier_tracks_gamma_table() -> None:
    assert kelly_multiplier("fixed_income") == pytest.approx(2 / 12)
    assert kelly_multiplier("conservative") == pytest.approx(2 / 6)
    assert kelly_multiplier("moderate") == pytest.approx(2 / 3)
    assert kelly_multiplier("aggressive") == pytest.approx(1.0)  # γ=2 = half-Kelly exactly


def test_multiplier_capped_at_half_kelly() -> None:
    # hyper_risky γ=1.5 would give 1.33× the stored half-Kelly — capped.
    assert kelly_multiplier("hyper_risky") == 1.0


def test_unknown_profile_falls_back_to_moderate() -> None:
    assert kelly_multiplier("yolo") == kelly_multiplier("moderate")


# ── size_strategies: gate-passers only, deterministic ─────────────────────────


def test_validated_strategy_gets_deterministic_capped_fraction() -> None:
    # The handover's acceptance case: kelly_fraction=0.3, moderate profile.
    sized = size_strategies([_Strat("s1", passes_rigor_gate=True, kelly_fraction=0.3)], "moderate")
    assert sized == {"s1": pytest.approx(0.2)}  # 0.3 × 2/3


def test_candidate_gets_zero() -> None:
    sized = size_strategies([_Strat("cand", passes_rigor_gate=False, kelly_fraction=0.4)], "aggressive")
    assert sized == {"cand": 0.0}


def test_no_kelly_fraction_gets_zero() -> None:
    sized = size_strategies([_Strat("nokelly", passes_rigor_gate=True, kelly_fraction=None)], "moderate")
    assert sized == {"nokelly": 0.0}


def test_negative_kelly_clamped_to_zero() -> None:
    sized = size_strategies([_Strat("neg", passes_rigor_gate=True, kelly_fraction=-0.1)], "moderate")
    assert sized == {"neg": 0.0}


# ── scale_to_budget: scale down, never up ──────────────────────────────────────


def test_under_budget_is_not_levered_up() -> None:
    fractions = {"a": 0.1, "b": 0.2}
    assert scale_to_budget(fractions, budget=0.8) == fractions


def test_over_budget_scales_down_proportionally() -> None:
    scaled = scale_to_budget({"a": 0.6, "b": 0.6}, budget=0.8)
    assert scaled["a"] == pytest.approx(0.4)
    assert scaled["b"] == pytest.approx(0.4)


# ── kelly_weighted_allocations ────────────────────────────────────────────────


def test_unclaimed_budget_stays_in_usdc() -> None:
    # One strategy sized at 0.2 voting 100% on sSPY: sSPY=0.2, USDC=0.8 —
    # the synth budget is NOT re-normalized up to fill (1 − floor).
    weights = kelly_weighted_allocations(
        [_signals("s1", {"sSPY": 1.0})],
        {"s1": 0.2},
        usdc_floor=0.20,
    )
    assert weights == {"USDC": pytest.approx(0.8), "sSPY": pytest.approx(0.2)}


def test_votes_within_strategy_normalized_to_its_fraction() -> None:
    # Votes sum to 2.0 → normalized so the strategy deploys exactly its 0.3.
    weights = kelly_weighted_allocations(
        [_signals("s1", {"sSPY": 1.0, "sGOLD": 1.0})],
        {"s1": 0.3},
        usdc_floor=0.20,
    )
    assert weights["sSPY"] == pytest.approx(0.15)
    assert weights["sGOLD"] == pytest.approx(0.15)
    assert weights["USDC"] == pytest.approx(0.7)


def test_zero_sized_strategy_contributes_nothing() -> None:
    weights = kelly_weighted_allocations(
        [_signals("cand", {"sSPY": 1.0})],
        {"cand": 0.0},
        usdc_floor=0.20,
    )
    assert weights == {"USDC": 1.0}


def test_weights_sum_to_one() -> None:
    weights = kelly_weighted_allocations(
        [_signals("s1", {"sSPY": 0.5, "sGOLD": 0.3}), _signals("s2", {"sTLT": 1.0})],
        {"s1": 0.25, "s2": 0.35},
        usdc_floor=0.10,
    )
    assert sum(weights.values()) == pytest.approx(1.0, abs=2e-4)


# ── endpoint wiring: derive-allocations consumes the sizer ─────────────────────


@dataclass
class _PassportStrat:
    id: str
    passes_rigor_gate: bool
    kelly_fraction: float | None
    paper_title: str = "t"
    asset_universe: list = field(default_factory=list)


@pytest.mark.asyncio
async def test_derive_allocations_sizes_passers_and_excludes_candidates(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/t.db")

    validated = _PassportStrat("val", passes_rigor_gate=True, kelly_fraction=0.3)
    candidate = _PassportStrat("cand", passes_rigor_gate=False, kelly_fraction=0.4)

    mock_settings = MagicMock()
    mock_settings.synth_addresses = {"sSPY": "0x" + "1" * 40}
    mock_settings.usdc_address = "0x" + "2" * 40

    from archimedes.api import vaults_routes
    from archimedes.api.vault_schemas import SetAllocationsRequest
    from archimedes.services.strategy_signal_evaluator import strategy_evaluator

    with (
        # strategy_provider is now a lazily-cached accessor (strategy_provider());
        # patch the name wholesale and configure the mocked callable's return_value.
        patch.object(vaults_routes, "strategy_provider") as mock_provider,
        patch("archimedes.chain.client.chain_client") as mock_chain,
        patch.object(
            strategy_evaluator,
            "evaluate_strategies",
            return_value=[
                _signals("val", {"sSPY": 1.0}),
                _signals("cand", {"sSPY": 1.0}),
            ],
        ),
    ):
        mock_provider.return_value.list_strategies.return_value = [validated, candidate]
        mock_chain.settings = mock_settings
        req = SetAllocationsRequest(strategy_ids=["val", "cand"], usdc_floor_pct=20.0, risk_profile="moderate")
        resp = await vaults_routes.derive_vault_allocations.__wrapped__("0x" + "3" * 40, req, MagicMock(), MagicMock())

    # val: 0.3 × (2/3) = 0.2 of capital on sSPY; cand contributes nothing.
    by_symbol = {a.symbol: a.weight_bps for a in resp.allocations}
    assert by_symbol["sSPY"] == 2000
    assert by_symbol["USDC"] == 8000
    assert resp.total_bps == 10000
    assert resp.sized_strategies == {"val": pytest.approx(0.2)}
    assert resp.excluded_strategy_ids == ["cand"]
    assert resp.risk_profile == "moderate"
