"""Laws of the Gateway withdrawal fee reserve (marketplace/config.py).

Both sweep paths withdraw ``available - reserve``, never ``available``,
because Circle charges its fee on top of the burn amount. These tests pin the
arithmetic and show the defensive parses rejecting bad input rather than
silently producing a zero reserve — which is exactly the state that caused the
production failure of 2026-08-26.
"""

from __future__ import annotations

import pytest
from archimedes.marketplace import config as mc


class TestReserveParsing:
    def test_default_when_unset(self, monkeypatch):
        monkeypatch.delenv("GATEWAY_WITHDRAW_FEE_RESERVE_USDC", raising=False)
        assert mc.gateway_fee_reserve_raw() == 50_000  # $0.05

    def test_override_is_honoured(self, monkeypatch):
        monkeypatch.setenv("GATEWAY_WITHDRAW_FEE_RESERVE_USDC", "0.25")
        assert mc.gateway_fee_reserve_raw() == 250_000

    @pytest.mark.parametrize("bad", ["garbage", "", "   ", "-1", "0", "0.0"])
    def test_bad_values_fall_back_never_to_zero(self, monkeypatch, bad):
        """A zero or unparseable reserve is the bug this module prevents, so
        no input may produce one."""
        monkeypatch.setenv("GATEWAY_WITHDRAW_FEE_RESERVE_USDC", bad)
        assert mc.gateway_fee_reserve_raw() == 50_000


class TestSweepAmount:
    def test_holds_back_the_reserve(self, monkeypatch):
        monkeypatch.delenv("GATEWAY_WITHDRAW_FEE_RESERVE_USDC", raising=False)
        amount, reserve = mc.gateway_sweep_amount(36_000_000)
        assert amount == "35.950000"
        assert reserve == 50_000

    def test_the_production_case_would_now_be_affordable(self, monkeypatch):
        """The exact prod numbers: available 36.000000, Circle's fee 0.0035.

        Old behaviour asked for 36.000000 and Circle computed required
        36.0035 > 36.000000. The reserved request clears it with room.
        """
        monkeypatch.delenv("GATEWAY_WITHDRAW_FEE_RESERVE_USDC", raising=False)
        available_raw = 36_000_000
        observed_fee_raw = 3_500
        amount, reserve = mc.gateway_sweep_amount(available_raw)
        amount_raw = int(float(amount) * 10**6)

        assert amount_raw + observed_fee_raw <= available_raw
        assert observed_fee_raw <= reserve, "reserve must cover the observed fee"
        # and the old behaviour genuinely did not clear it
        assert available_raw + observed_fee_raw > available_raw

    def test_balance_at_or_under_the_reserve_raises(self, monkeypatch):
        monkeypatch.setenv("GATEWAY_WITHDRAW_FEE_RESERVE_USDC", "0.05")
        for available in (0, 1, 49_999, 50_000):
            with pytest.raises(ValueError, match="does not cover"):
                mc.gateway_sweep_amount(available)

    def test_amount_is_exact_six_decimals(self, monkeypatch):
        """circlekit re-parses this string; a float-ish repr would drift."""
        monkeypatch.delenv("GATEWAY_WITHDRAW_FEE_RESERVE_USDC", raising=False)
        amount, _ = mc.gateway_sweep_amount(10_000_001)
        assert amount == "9.950001"
        assert len(amount.split(".")[1]) == 6
