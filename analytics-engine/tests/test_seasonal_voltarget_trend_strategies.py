"""Tests for the seasonal / volatility-targeting / multi-horizon-trend strategies.

Hermetic: synthetic N-feed data, no network. Each strategy file is loaded via
the real strategy_loader (proving its metadata block + single strategy class
resolve) and run through engine.run_multi_backtest. These tests guard the
plumbing, the trade path, the look-ahead audit, and the metadata block — not the
performance numbers.
"""

from __future__ import annotations

import importlib.util
import math
import sys
from pathlib import Path

import pandas as pd
import pytest
from archimedes_analytics_engine.engine import BacktestResult, run_multi_backtest

_STRATEGIES_DIR = Path(__file__).parent.parent / "strategies"
sys.path.insert(0, str(_STRATEGIES_DIR))
sys.path.insert(0, str(_STRATEGIES_DIR.parent / "src"))

# Feed names mirror the production universe; datas[0] (SPY) is what these
# single-asset strategies trade.
_NAMES = ["SPY", "^N225", "GC=F", "TLT", "CL=F"]


def _trending_universe(n: int = 600, start: str = "2015-01-01") -> list[pd.DataFrame]:
    """Five synthetic feeds with distinct drifts/phases. 600 bars (~2.4y) clears
    the 252-bar warmup the multi-horizon-trend strategy needs. ``start`` sets the
    calendar start date (used to place bars in/out of the Halloween season)."""
    specs = [(100.0, 0.06, 0.0), (80.0, 0.03, 1.0), (120.0, 0.01, 2.0), (90.0, 0.02, 3.0), (70.0, 0.05, 4.0)]
    frames = []
    for base, drift, phase in specs:
        idx = pd.date_range(start, periods=n, freq="D")
        closes = [base + drift * i + 8.0 * math.sin(i / 20.0 + phase) for i in range(n)]
        frames.append(
            pd.DataFrame(
                {
                    "Open": [c - 0.3 for c in closes],
                    "High": [c + 0.6 for c in closes],
                    "Low": [c - 0.6 for c in closes],
                    "Close": closes,
                    "Volume": [1_000] * n,
                },
                index=idx,
            )
        )
    return frames


def _load_module(stem: str):
    path = _STRATEGIES_DIR / f"{stem}.py"
    spec = importlib.util.spec_from_file_location(f"_meta_{stem}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# (stem, expected class name, expected REGIME_TAG)
_CASES = [
    ("bouman_jacobsen_2002_halloween", "HalloweenSeasonal", "regime_neutral"),
    ("harvey_2018_volatility_targeting", "VolatilityTargeting", "regime_neutral"),
    ("hurst_2017_multihorizon_trend", "MultiHorizonTrend", "bull"),
]


@pytest.mark.parametrize(("stem", "expected_cls", "_regime"), _CASES)
def test_strategy_loads_and_runs(stem: str, expected_cls: str, _regime: str) -> None:
    from archimedes_analytics_engine.strategy_loader import load_strategy

    bundle = load_strategy(_STRATEGIES_DIR / f"{stem}.py")
    assert bundle.cls.__name__ == expected_cls

    frames = _trending_universe()
    result = run_multi_backtest(frames, strategy_cls=bundle.cls, initial_cash=100_000.0, names=_NAMES)
    assert isinstance(result, BacktestResult)
    # The audit must pass — these strategies use only bars-ago / current-bar data.
    assert result.look_ahead_audit_passed is True
    assert isinstance(result.daily_returns, list)
    assert len(result.daily_returns) > 0
    # Each strategy deploys capital on a trending/seasonal universe, so the final
    # value must diverge from the initial cash.
    assert result.final_value != pytest.approx(100_000.0, rel=1e-3)


@pytest.mark.parametrize(("stem", "expected_cls", "expected_regime"), _CASES)
def test_strategy_metadata(stem: str, expected_cls: str, expected_regime: str) -> None:
    module = _load_module(stem)

    assert isinstance(module.PAPER_TITLE, str) and module.PAPER_TITLE.strip()
    assert isinstance(module.PAPER_AUTHORS, list) and len(module.PAPER_AUTHORS) >= 1
    assert all(isinstance(a, str) and a.strip() for a in module.PAPER_AUTHORS)
    assert isinstance(module.PAPER_YEAR, int) and module.PAPER_YEAR > 1900
    assert expected_regime == module.REGIME_TAG
    assert isinstance(module.RISK_PROFILES, list) and len(module.RISK_PROFILES) >= 1
    assert module.STATUS == "candidate"
    assert isinstance(module.METHODOLOGY_TEXT, str) and len(module.METHODOLOGY_TEXT) > 100
    # Honest paper-claim deltas: the single-asset adaptation must carry a claimed
    # Sharpe so the passport can surface the gap to the realized number.
    assert isinstance(module.PAPER_CLAIMED_SHARPE, (int, float)) and module.PAPER_CLAIMED_SHARPE > 0
    assert hasattr(module, expected_cls)


# The two horizon/vol strategies have a warmup window; the seasonal one does not
# (it trades on the current bar's month, by design), so it is tested separately.
_WARMUP_CASES = [
    ("harvey_2018_volatility_targeting", "VolatilityTargeting"),
    ("hurst_2017_multihorizon_trend", "MultiHorizonTrend"),
]


@pytest.mark.parametrize(("stem", "expected_cls"), _WARMUP_CASES)
def test_insufficient_history_holds_flat(stem: str, expected_cls: str) -> None:
    from archimedes_analytics_engine.strategy_loader import load_strategy

    bundle = load_strategy(_STRATEGIES_DIR / f"{stem}.py")
    assert bundle.cls.__name__ == expected_cls

    # 20 bars is below both warmup windows (vol=20, trend=252) → never deploys.
    frames = _trending_universe(n=20)
    result = run_multi_backtest(frames, strategy_cls=bundle.cls, initial_cash=100_000.0, names=_NAMES)
    assert isinstance(result, BacktestResult)
    assert result.final_value == pytest.approx(100_000.0, rel=1e-3)


def test_halloween_respects_the_season() -> None:
    """The seasonal strategy holds cash out of season (May–Oct) and deploys in
    season (Nov–Apr) — no warmup, decision driven purely by the calendar month."""
    from archimedes_analytics_engine.strategy_loader import load_strategy

    bundle = load_strategy(_STRATEGIES_DIR / "bouman_jacobsen_2002_halloween.py")

    # ~40 bars starting in May → entirely in the out-of-season (cash) half.
    out_of_season = _trending_universe(n=40, start="2015-05-01")
    r_out = run_multi_backtest(out_of_season, strategy_cls=bundle.cls, initial_cash=100_000.0, names=_NAMES)
    assert r_out.final_value == pytest.approx(100_000.0, rel=1e-3)

    # ~40 bars starting in November → in-season, so capital is deployed.
    in_season = _trending_universe(n=40, start="2015-11-01")
    r_in = run_multi_backtest(in_season, strategy_cls=bundle.cls, initial_cash=100_000.0, names=_NAMES)
    assert r_in.final_value != pytest.approx(100_000.0, rel=1e-3)
