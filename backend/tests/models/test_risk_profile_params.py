"""Bounds on RISK_PROFILE_PARAMS — the live risk-ladder constants.

Ported rider from the deleted test_kelly_portfolio.py (Tranche 1, 2026-08-18
audit): its ``test_risk_on_low_usdc_floor`` transitively imposed an UPPER
bound on the aggressive profile's cash floor, and no live-path test
replicated it — deleting the dead Kelly constructor would otherwise have
deleted the only thing pinning these numbers. The params are consumed by
portfolio construction (the "Önder uses these" contract in
models/portfolio.py), so they are pinned here directly, on the live object.
"""

from __future__ import annotations

from archimedes.models.portfolio import RISK_PROFILE_PARAMS, RiskProfile

_LADDER = [
    RiskProfile.FIXED_INCOME,
    RiskProfile.CONSERVATIVE,
    RiskProfile.MODERATE,
    RiskProfile.AGGRESSIVE,
    RiskProfile.HYPER_RISKY,
]


def test_every_profile_has_a_coherent_floor_ceiling_band():
    for profile in _LADDER:
        params = RISK_PROFILE_PARAMS[profile]
        assert 0.0 <= params["usyc_floor"] <= params["usyc_ceiling"] <= 1.0, profile


def test_cash_floor_decreases_as_risk_rises():
    """The ladder's defining property: more risk appetite, less mandatory cash.
    An accidental swap of two profiles' params (the plausible editing slip)
    breaks the strict ordering here."""
    floors = [RISK_PROFILE_PARAMS[p]["usyc_floor"] for p in _LADDER]
    assert floors == sorted(floors, reverse=True)
    assert len(set(floors)) == len(floors), "floors must be strictly decreasing, not merely sorted"


def test_aggressive_profile_keeps_cash_floor_low():
    """The ported rider: risk-on + aggressive must stay nearly fully invested.
    The old Kelly test asserted the derived floor < 0.1; the base param is the
    binding input, so pin it at the source."""
    assert RISK_PROFILE_PARAMS[RiskProfile.AGGRESSIVE]["usyc_floor"] < 0.1
