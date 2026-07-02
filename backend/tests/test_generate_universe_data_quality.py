"""Hermetic tests for the generator's data-quality admission gate (#772).

The SSOT builder (`scripts/generate_universe.py --write`) is the single place
new instruments enter the universe, so it is where the #772 harness gates
admission. All feeds are injected — no network.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from archimedes.scripts.generate_universe import CURATED, _dq_gate, vet_data_quality


def _clean_feed(ticker, start, end):
    idx = pd.bdate_range(start, end)
    return pd.Series(100.0 + np.arange(len(idx), dtype=float), index=idx)


def test_vet_covers_every_curated_ticker():
    report = vet_data_quality(_downloader=_clean_feed)
    curated_tickers = {a.yf for a in CURATED}
    assert set(report.verdicts) == curated_tickers
    assert report.rejected == {}
    assert set(report.admitted) == curated_tickers


def test_gate_passes_on_clean_report():
    report = vet_data_quality(_downloader=_clean_feed)
    _dq_gate(report)  # must not raise


def test_gate_refuses_write_on_bad_ticker():
    bad = sorted({a.yf for a in CURATED})[0]

    def _one_dead(ticker, start, end):
        if ticker == bad:
            return pd.Series(dtype=float)
        return _clean_feed(ticker, start, end)

    report = vet_data_quality(_downloader=_one_dead)
    assert bad in report.rejected
    with pytest.raises(SystemExit) as exc:
        _dq_gate(report)
    # The refusal names the ticker and its reason, so the curator can act on it.
    assert bad in str(exc.value)
    assert "unfetchable" in str(exc.value)


def test_gate_window_matches_backtest_fetch():
    # The evaluator fetches period="2y"; a series that only covers the last
    # year must be rejected as insufficient for the window the backtests use.
    def _short_feed(ticker, start, end):
        late_start = pd.Timestamp(end) - pd.DateOffset(years=1)
        idx = pd.bdate_range(late_start, end)
        return pd.Series(100.0 + np.arange(len(idx), dtype=float), index=idx)

    report = vet_data_quality(_downloader=_short_feed)
    assert report.admitted == ()
    assert all(any("insufficient history" in r for r in reasons) for reasons in report.rejected.values())
