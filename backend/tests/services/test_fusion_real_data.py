"""Real-data wiring for fusion/DSL backtests (#788/#818 deployability unlock).

Hermetic: the yfinance boundary (``fusion_market_data._fetch_one``) is
monkeypatched with deterministic frames — no network. These tests prove:

1. universe resolution goes through the SSOT (synth key / display / yf ticker),
   never arbitrary tickers;
2. panels are strictly inner-joined (anti-lookahead) and cached, and the cache
   hit path enforces the same min-bars floor as the miss path;
3. ``evaluate_fusion_spec(use_real_data=True)`` produces an admissible-eligible
   verdict with real provenance, and fails CLOSED to synthetic (inadmissible)
   when data is unavailable;
4. feed factories yield a FRESH feed per run — the variant grid on a shared
   concrete feed is the silent-corruption bug this design exists to prevent.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from archimedes.services import fusion_market_data as fmd
from archimedes.services.fusion_evaluator import evaluate_fusion_spec, run_dsl_backtest
from archimedes.services.strategy_dsl import FABER_2007_SPEC


def _frame(seed: int, n: int = 700, start: str = "2019-01-02") -> pd.DataFrame:
    idx = pd.bdate_range(start, periods=n)
    rng = np.random.default_rng(seed)
    close = 100.0 * np.cumprod(1.0 + rng.normal(0.0004, 0.012, n))
    return pd.DataFrame(
        {
            "Open": close * 0.999,
            "High": close * 1.005,
            "Low": close * 0.995,
            "Close": close,
            "Volume": np.full(n, 1_000_000.0),
        },
        index=idx,
    )


@pytest.fixture(autouse=True)
def _clear_panel_cache():
    fmd._panel_cache.clear()
    yield
    fmd._panel_cache.clear()


def _patch_fetch(monkeypatch, frames_by_ticker: dict[str, pd.DataFrame], calls: list | None = None):
    def _fake(yf_ticker: str, start: str, end: str) -> pd.DataFrame:
        if calls is not None:
            calls.append(yf_ticker)
        if yf_ticker not in frames_by_ticker:
            raise ValueError(f"no data for {yf_ticker}")
        return frames_by_ticker[yf_ticker]

    monkeypatch.setattr(fmd, "_fetch_one", _fake)


# ── universe resolution ───────────────────────────────────────────────


def test_resolve_universe_maps_synth_display_and_yf_forms():
    resolved = fmd.resolve_universe(["sSPY", "AAVE", "AAVE-USD", "TOTALLY_FAKE_XYZ"])
    assert resolved.get("sSPY") == "SPY"
    assert resolved.get("AAVE") == "AAVE-USD"
    assert resolved.get("AAVE-USD") == "AAVE-USD"
    assert "TOTALLY_FAKE_XYZ" not in resolved  # outside the SSOT → skipped


def test_resolve_universe_caps_asset_fanout():
    from archimedes.services.strategy_signal_evaluator import GLOBAL_ASSETS

    many = list(GLOBAL_ASSETS)[: fmd._MAX_ASSETS + 4]
    resolved = fmd.resolve_universe(many)
    assert len(resolved) == fmd._MAX_ASSETS


# ── panel fetch: inner join, cache, fail-closed ───────────────────────


def test_fetch_real_panel_inner_joins_on_common_dates(monkeypatch):
    # AAVE-USD starts 100 business days later → the join must shrink to the overlap.
    _patch_fetch(monkeypatch, {"SPY": _frame(1, n=700), "AAVE-USD": _frame(2, n=600, start="2019-05-22")})
    panel = fmd.fetch_real_panel(["SPY", "AAVE"], min_bars=252)
    assert panel is not None
    lengths = {len(df) for df in panel.frames.values()}
    assert lengths == {panel.n_bars}  # every frame aligned to the common index
    assert panel.n_bars < 700  # strictly the overlap, not the union
    assert panel.label == "yfinance:SPY,AAVE-USD"
    assert panel.start < panel.end


def test_fetch_real_panel_caches_by_ticker_set(monkeypatch):
    calls: list[str] = []
    _patch_fetch(monkeypatch, {"SPY": _frame(1)}, calls)
    first = fmd.fetch_real_panel(["SPY"])
    again = fmd.fetch_real_panel(["SPY"])
    assert first is not None and again is not None
    assert calls == ["SPY"]  # second call served from cache — no refetch


def test_fetch_real_panel_min_bars_fails_closed_even_from_cache(monkeypatch):
    _patch_fetch(monkeypatch, {"SPY": _frame(1, n=100)})
    assert fmd.fetch_real_panel(["SPY"], min_bars=252) is None
    # The short frames are cached; the HIT path must enforce the same floor.
    assert fmd.fetch_real_panel(["SPY"], min_bars=252) is None


def test_fetch_real_panel_unmappable_universe_returns_none(monkeypatch):
    _patch_fetch(monkeypatch, {})
    assert fmd.fetch_real_panel(["NOT_IN_SSOT"]) is None


# ── evaluate_fusion_spec on real data ─────────────────────────────────


def test_evaluate_real_data_multi_asset_admissible_flow(monkeypatch):
    _patch_fetch(monkeypatch, {"SPY": _frame(1), "AAVE-USD": _frame(2)})
    spec = {
        **FABER_2007_SPEC,
        "asset_universe": ["SPY", "AAVE"],
        "parameter_variants": {"sma_200": [150, 200, 250]},
    }
    result = evaluate_fusion_spec(spec, use_real_data=True)
    assert result.success, f"evaluation failed: {result.error}"
    assert result.backtest is not None and result.rigor is not None
    # Real provenance end-to-end: metrics AND verdict carry the yfinance label.
    assert result.backtest.data_source == "yfinance:SPY,AAVE-USD"
    assert result.rigor.data_source == "yfinance:SPY,AAVE-USD"
    # Admissibility now tracks the statistics (no synthetic disqualifier).
    assert result.rigor.admissible == result.rigor.passing
    # Portfolio curve covers the joined panel (700 aligned bars), real dates set.
    assert len(result.backtest.equity_curve) > 600
    assert result.backtest.backtest_start is not None
    assert result.backtest.backtest_end is not None
    # The 3-variant grid ran on real data → CSCV PBO is a real number.
    assert result.rigor.pbo_score is not None
    assert 0.0 <= result.rigor.pbo_score <= 1.0


def test_evaluate_real_data_unavailable_falls_back_synthetic(monkeypatch):
    def _boom(yf_ticker, start, end):
        raise RuntimeError("yfinance down")

    monkeypatch.setattr(fmd, "_fetch_one", _boom)
    result = evaluate_fusion_spec(dict(FABER_2007_SPEC), use_real_data=True)
    assert result.success
    # Fail-closed: synthetic provenance, never admissible — the live gate
    # keeps reading an honest "pending" for this strategy.
    assert result.backtest.data_source == "synthetic"
    assert result.rigor.admissible is False


def test_evaluate_default_stays_hermetic_and_synthetic(monkeypatch):
    # No use_real_data → the fetch boundary must never be touched.
    def _forbidden(*a, **k):
        raise AssertionError("network path must not run by default")

    monkeypatch.setattr(fmd, "_fetch_one", _forbidden)
    result = evaluate_fusion_spec(dict(FABER_2007_SPEC))
    assert result.success
    assert result.backtest.data_source == "synthetic"


# ── feed factories ────────────────────────────────────────────────────


def test_feed_factory_yields_fresh_consumable_feeds():
    factory = fmd.feed_factory(_frame(7))
    assert factory() is not factory()  # fresh object per call
    from archimedes.services.strategy_dsl import validate_strategy_spec

    spec = validate_strategy_spec(dict(FABER_2007_SPEC))
    # Two full cerebro runs off the SAME factory: a shared concrete feed would
    # leave the second run with an exhausted feed (degenerate 1-bar curve).
    m1 = run_dsl_backtest(spec, data_feed_factory=factory, data_source_label="yfinance:SPY")
    m2 = run_dsl_backtest(spec, data_feed_factory=factory, data_source_label="yfinance:SPY")
    assert len(m1.equity_curve) > 600
    assert len(m2.equity_curve) > 600
    assert m1.data_source == "yfinance:SPY"


# ── env gate ──────────────────────────────────────────────────────────


def test_real_data_enabled_env_gate(monkeypatch):
    monkeypatch.setenv("TESTING", "1")
    assert fmd.real_data_enabled() is False  # hermetic under pytest, always

    monkeypatch.delenv("TESTING", raising=False)
    monkeypatch.setenv("ARCHIMEDES_FUSION_REAL_DATA", "0")
    assert fmd.real_data_enabled() is False  # explicit kill switch

    monkeypatch.delenv("ARCHIMEDES_FUSION_REAL_DATA", raising=False)
    assert fmd.real_data_enabled() is True  # default ON in prod
