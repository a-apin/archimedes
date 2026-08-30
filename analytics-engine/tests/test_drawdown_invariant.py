"""The reported max drawdown must be the drawdown of the reported equity curve.

Hermetic: synthetic in-memory frames only, no network, no yfinance.

Background — the defect these tests pin. A real library run
(``avellaneda_lee_2010_pca_statarb``, audited in
``docs/audits/2026-07-09-curated-consolidation.md`` §2.4) reported
``max_drawdown_pct = 130.3``. A >100% drawdown means the account's equity went
NEGATIVE, which for the engine's model of a position — unlevered cash equity,
marked ``size * price`` — cannot happen while prices are positive.

**The root cause is the price feed, not the arithmetic.** ``normalize_ohlcv``
validated NaNs and timestamp ordering but never that a price was positive.
``OIL`` in that strategy's declared universe resolves to ``CL=F``, whose front
month settled at **-$37.63 on 2020-04-20**; a held position marked at a negative
price drags portfolio value straight through zero, which is how a 1.0x-gross,
dollar-neutral book reached ruin. The strategy's own signal path already refused
to build a return from a non-positive price (``if prev <= 0: return None``) —
the marking path did not. It does now, and
``test_negative_price_bar_drives_portfolio_value_negative_when_not_filtered``
shows what that guard is holding back.

Second, smaller change, and it is a **refactor, not a guard**:
``_extract_result`` reported ``bt.analyzers.DrawDown``'s number (measured on
backtrader's ``broker.getvalue()`` path) while shipping an ``equity_curve``
rebuilt by compounding TimeReturn's returns. The two series are equal by
algebraic identity, so no number moved — ``test_reported_drawdown_matches_*``
below pin exactly that. What it buys is that the agreement no longer rests on an
identity that is undefined at ``v[i-1] == 0``, and the bound becomes exact:

    max_drawdown_pct < 100  <=>  the reported equity curve stayed positive

Nothing here clamps a displayed number: a curve that genuinely goes non-positive
still reports >100%, because that is the truth about that curve.
"""

from __future__ import annotations

import random

import backtrader as bt
import pandas as pd
import pytest
from archimedes_analytics_engine.data import normalize_ohlcv
from archimedes_analytics_engine.engine import (
    BuyAndHoldStrategy,
    _max_drawdown_from_curve,
    run_backtest,
)


def _frame(closes: list[float], start: str = "2020-04-01") -> pd.DataFrame:
    idx = pd.date_range(start, periods=len(closes), freq="D")
    return pd.DataFrame(
        {
            "Open": closes,
            "High": [c + abs(c) * 0.01 for c in closes],
            "Low": [c - abs(c) * 0.01 for c in closes],
            "Close": closes,
            "Volume": [1_000_000] * len(closes),
        },
        index=idx,
    )


# ── The invariant, as a property ────────────────────────────────────────────


def _random_curve(rng: random.Random, *, allow_ruin: bool) -> list[float]:
    """A positive-start equity curve; ``allow_ruin`` lets it cross zero."""
    curve = [rng.uniform(1.0, 1_000_000.0)]
    floor = -2.0 if allow_ruin else 0.001
    for _ in range(rng.randint(1, 60)):
        step = rng.uniform(floor, 0.5)
        curve.append(max(curve[-1] * (1.0 + step), floor * abs(curve[0])))
    return curve


@pytest.mark.parametrize("seed", range(200))
def test_max_drawdown_exceeds_100pct_exactly_when_equity_goes_non_positive(seed: int) -> None:
    """Property: for ANY equity curve with a positive start, the computed max
    drawdown is shallower than 100% **if and only if** the curve never touches
    zero or goes below it.

    Stated in the sign convention the UI renders (``-max_drawdown_pct``):
    the displayed drawdown is > -100% exactly when equity never goes <= 0.
    This is the whole point of deriving the number from the curve — the reader
    holding the curve can check the claim, and an impossible drawdown can only
    mean a genuinely ruined account, never a measurement artifact.
    """
    rng = random.Random(seed)
    curve = _random_curve(rng, allow_ruin=bool(seed % 2))
    assert curve[0] > 0

    max_dd_pct, _ = _max_drawdown_from_curve(curve)
    assert max_dd_pct is not None

    survived = min(curve) > 0
    assert (max_dd_pct < 100.0) is survived, (
        f"seed={seed}: max_dd={max_dd_pct!r} but min(curve)={min(curve)!r} — "
        "the >100% bound and the ruin condition must be the same fact"
    )
    # And it is never nonsense in the survivable direction.
    assert max_dd_pct >= 0.0


def test_property_sampler_actually_produces_both_regimes() -> None:
    """Guard against a vacuous property test: the sampler above must generate
    both surviving and ruined curves, or the iff is only ever checked on one
    side and the test proves nothing."""
    survived = ruined = 0
    for seed in range(200):
        curve = _random_curve(random.Random(seed), allow_ruin=bool(seed % 2))
        if min(curve) > 0:
            survived += 1
        else:
            ruined += 1
    assert survived > 10, f"only {survived} surviving curves sampled"
    assert ruined > 10, f"only {ruined} ruined curves sampled"


# ── The -130% shape, as a regression ────────────────────────────────────────


def test_130_percent_drawdown_shape_requires_a_negative_equity_curve() -> None:
    """The exact audited number, reproduced from the only curve that can produce
    it: peak 100,000 -> trough -30,300 is (100000 - -30300) / 100000 = 130.3%.

    Pins the direction of the implication the fix establishes. Reading 130.3%
    off an artifact now tells you something specific and checkable about the
    equity curve shipped with it — that it went below zero — instead of being a
    number from a series nobody can see.
    """
    ruined = [100_000.0, 80_000.0, 20_000.0, -30_300.0, -5_000.0]
    max_dd_pct, _ = _max_drawdown_from_curve(ruined)
    assert max_dd_pct == pytest.approx(130.3)
    assert min(ruined) <= 0

    # The same peak-to-trough magnitude is unreachable while equity survives:
    # the deepest a positive curve can go is asymptotically 100%.
    survivor = [100_000.0, 80_000.0, 20_000.0, 0.01, 5_000.0]
    survivor_dd, _ = _max_drawdown_from_curve(survivor)
    assert survivor_dd < 100.0


def test_negative_price_bar_drives_portfolio_value_negative_when_not_filtered() -> None:
    """The mechanism, demonstrated end to end: feed backtrader a bar with a
    NEGATIVE close (the CL=F 2020-04-20 shape) and a long position is marked at
    ``size * -37.63``, dragging portfolio value below zero and producing exactly
    the impossible >100% drawdown. This is what ``normalize_ohlcv`` now stops
    at the boundary — the test asserts the raw, unfiltered behaviour so the
    guard below is provably load-bearing rather than decorative.
    """
    closes = [20.0, 18.0, 15.0, -37.63, 10.0, 12.0]
    result = run_backtest(
        _frame(closes),
        strategy_cls=BuyAndHoldStrategy,
        initial_cash=10_000.0,
        transaction_cost_bps=0,
    )
    assert min(result.equity_curve) <= 0.0, "expected the negative mark to ruin the account"
    assert result.max_drawdown_pct is not None
    assert result.max_drawdown_pct > 100.0
    # ...and the invariant still holds on that run: the impossible number and
    # the non-positive curve are the same fact, reported consistently.
    recomputed, _ = _max_drawdown_from_curve(result.equity_curve)
    assert result.max_drawdown_pct == pytest.approx(recomputed)


def test_zero_price_bar_crashes_the_run_outright_without_the_guard() -> None:
    """A zero mark is worse than a negative one: backtrader's TimeReturn divides
    by the previous portfolio value, so a bar that zeroes equity takes the whole
    run down with ZeroDivisionError. Second concrete consequence of admitting a
    non-positive price, and the second thing the boundary guard prevents."""
    with pytest.raises(ZeroDivisionError):
        run_backtest(
            _frame([20.0, 20.0, 0.0, 10.0, 12.0]),
            strategy_cls=BuyAndHoldStrategy,
            initial_cash=10_000.0,
            transaction_cost_bps=0,
        )

    survives = run_backtest(
        normalize_ohlcv(_frame([20.0, 20.0, 0.0, 10.0, 12.0]), symbol="ZERO"),
        strategy_cls=BuyAndHoldStrategy,
        initial_cash=10_000.0,
        transaction_cost_bps=0,
    )
    assert min(survives.equity_curve) > 0


def test_ruin_is_reported_loudly_rather_than_clamped(caplog) -> None:
    """A curve that genuinely goes non-positive keeps its >100% drawdown — the
    number is the truth about that run — but the engine says out loud that the
    account was ruined, so the figure cannot be misread as a survivable loss."""
    with caplog.at_level("WARNING", logger="archimedes_analytics_engine.engine"):
        result = run_backtest(
            _frame([20.0, 18.0, 15.0, -37.63, 10.0, 12.0]),
            strategy_cls=BuyAndHoldStrategy,
            initial_cash=10_000.0,
            transaction_cost_bps=0,
        )

    assert result.max_drawdown_pct > 100.0  # not clamped
    assert any("ruined" in r.getMessage() for r in caplog.records), caplog.text


def test_normalize_ohlcv_drops_non_positive_price_bars() -> None:
    """The guard: a non-positive mark never reaches the broker."""
    raw = _frame([20.0, 18.0, 15.0, -37.63, 10.0, 12.0])
    cleaned = normalize_ohlcv(raw, symbol="CL=F")

    assert len(cleaned) == 5
    assert (cleaned[["Open", "High", "Low", "Close"]] > 0).all().all()
    assert pd.Timestamp("2020-04-04") not in cleaned.index  # the negative bar

    # A zero close is just as unmarkable as a negative one.
    with_zero = normalize_ohlcv(_frame([20.0, 0.0, 15.0]), symbol="ZERO")
    assert len(with_zero) == 2


def test_cleaned_feed_cannot_produce_an_impossible_drawdown() -> None:
    """The two fixes composed: run the SAME series through the boundary guard
    and the reported drawdown lands back inside the possible range."""
    cleaned = normalize_ohlcv(_frame([20.0, 18.0, 15.0, -37.63, 10.0, 12.0]), symbol="CL=F")
    result = run_backtest(
        cleaned,
        strategy_cls=BuyAndHoldStrategy,
        initial_cash=10_000.0,
        transaction_cost_bps=0,
    )
    assert min(result.equity_curve) > 0
    assert result.max_drawdown_pct is not None
    assert result.max_drawdown_pct < 100.0


# ── The reported number still matches backtrader's, where both are defined ──


def _analyzer_drawdown(prices: pd.DataFrame, *, commission: float, slippage: float) -> tuple[float, int]:
    """``bt.analyzers.DrawDown`` on its own cerebro — the external ground truth."""
    cerebro = bt.Cerebro(stdstats=False)
    cerebro.broker.setcash(10_000.0)
    cerebro.broker.setcommission(commission=commission)
    if slippage > 0:
        cerebro.broker.set_slippage_perc(perc=slippage)
    cerebro.adddata(bt.feeds.PandasData(dataname=prices))
    cerebro.addstrategy(BuyAndHoldStrategy)
    cerebro.addanalyzer(bt.analyzers.DrawDown, _name="dd")
    worst = cerebro.run()[0].analyzers.dd.get_analysis()["max"]
    return float(worst["drawdown"]), int(worst["len"])


def test_reported_drawdown_matches_bt_drawdown_analyzer_on_a_surviving_run() -> None:
    """Deriving the drawdown from the reported curve did not change the number.

    This is the behaviour-preservation check on that refactor, not a guard —
    while broker value stays positive the compounded curve reproduces the
    broker path exactly, so the curve-derived drawdown and
    ``bt.analyzers.DrawDown`` (still registered as the external ground truth)
    must agree in BOTH magnitude and duration.
    """
    closes = [100.0, 100.0, 90.0, 80.0, 90.0, 100.0, 95.0, 90.0, 100.0, 110.0, 100.0]
    prices = _frame(closes, start="2022-01-01")

    result = run_backtest(
        prices,
        strategy_cls=BuyAndHoldStrategy,
        initial_cash=10_000.0,
        transaction_cost_bps=0,
    )
    analyzer_dd, analyzer_len = _analyzer_drawdown(prices, commission=0.0, slippage=0.0)

    assert result.max_drawdown_pct is not None
    assert result.max_drawdown_pct > 0.0  # not a vacuous pass on a flat curve
    assert result.max_drawdown_pct == pytest.approx(analyzer_dd, rel=1e-9)
    assert result.max_drawdown_duration_bars == analyzer_len


@pytest.mark.parametrize("seed", range(25))
def test_reported_drawdown_matches_the_analyzer_across_random_series(seed: int) -> None:
    """The same equivalence over randomized price paths, WITH commissions and
    slippage on, so the refactor is pinned across the parameter space the
    library actually runs in rather than on one hand-picked curve."""
    rng = random.Random(seed)
    price = 100.0
    closes = [price]
    for _ in range(rng.randint(5, 40)):
        price *= 1.0 + rng.uniform(-0.3, 0.3)
        closes.append(round(price, 4))
    prices = _frame(closes, start="2020-01-01")

    result = run_backtest(
        prices,
        strategy_cls=BuyAndHoldStrategy,
        initial_cash=10_000.0,
        transaction_cost_bps=10,
        slippage_bps=5,
    )
    analyzer_dd, analyzer_len = _analyzer_drawdown(prices, commission=0.001, slippage=0.0005)

    assert result.max_drawdown_pct == pytest.approx(analyzer_dd, rel=1e-9)
    assert result.max_drawdown_duration_bars == analyzer_len
