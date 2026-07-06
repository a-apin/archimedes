"""Precision tests for swap-quote amount conversion (#922).

`int(amount_in * 10**decimals)` runs the multiply in float64 and loses integer
precision for large amounts (and truncates sub-unit amounts). The Decimal-based
`_to_token_raw` must be exact.
"""

from __future__ import annotations

from archimedes.api.swap_routes import _to_token_raw


def test_large_amount_is_exact_not_float_rounded():
    # The float path `int(1e9 * 10**18)` lands ~1.3e13 off; Decimal is exact.
    assert _to_token_raw(1e9, 18) == 10**27
    assert _to_token_raw(1e9, 18) != int(1e9 * 10**18)


def test_typical_usdc_amount():
    assert _to_token_raw(100.0, 6) == 100_000_000
    assert _to_token_raw(1234.56, 6) == 1_234_560_000


def test_sub_unit_amount_floors_to_whole_base_units():
    # 1.5 base units → floor to 1 (not lost to a float artifact).
    assert _to_token_raw(0.0000015, 6) == 1
    # Genuinely below one base unit → 0 (correct: below token resolution).
    assert _to_token_raw(0.0000004, 6) == 0


def test_eighteen_decimal_fractional_amount_exact():
    assert _to_token_raw(1.5, 18) == 1_500_000_000_000_000_000
