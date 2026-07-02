"""Hermetic tests for the data-quality / fetch-verification harness (#772).

No network: the yfinance boundary is replaced by an injected ``_downloader``
that returns synthetic pandas Series, so each property the harness gates on —
fetchable / sufficient history / gap-free / survivorship-clean — is exercised
deterministically. These prove a known-bad ticker is rejected (the gate excludes
poisoned data rather than padding it to pass), per #772.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from archimedes.services.data_quality import (
    verify_instrument,
    verify_universe,
)

START = "2022-01-03"
END = "2023-12-29"


def _series(index: pd.DatetimeIndex) -> pd.Series:
    # Values are irrelevant to the data-quality checks (which read only the index
    # coverage/gaps), but must be finite + positive like real adjusted closes.
    return pd.Series(100.0 + np.arange(len(index), dtype=float), index=index)


def _clean(start: str = START, end: str = END) -> pd.Series:
    return _series(pd.bdate_range(start, end))


def _fixed(series: pd.Series):
    """A downloader that returns ``series`` regardless of (ticker, start, end)."""

    def _dl(ticker, s, e):
        return series

    return _dl


def _empty(ticker, s, e):
    return pd.Series(dtype=float)


# ── verify_instrument — the four properties ───────────────────────────────


def test_clean_instrument_passes():
    v = verify_instrument("GOOD", START, END, _downloader=_fixed(_clean()))
    assert v.ok and v.fetchable
    assert v.reasons == ()
    assert v.sufficient_history and v.survivorship_ok
    assert v.gap_ratio == 0.0
    assert v.coverage_ratio >= 0.99


def test_unfetchable_rejected():
    v = verify_instrument("DEAD", START, END, _downloader=_empty)
    assert not v.ok and not v.fetchable
    assert v.n_obs == 0
    assert any("unfetchable" in r for r in v.reasons)


def test_insufficient_history_rejected():
    """A late-starting (short) series fails the coverage check, not the gap or
    survivorship check — isolating the sufficiency property."""
    half = _series(pd.bdate_range("2023-01-02", END))  # ~second half of the window
    v = verify_instrument("SHORT", START, END, _downloader=_fixed(half))
    assert not v.ok
    assert not v.sufficient_history
    assert v.coverage_ratio < 0.90
    assert any("insufficient history" in r for r in v.reasons)


def test_gappy_series_rejected():
    """A full-span series with a large internal hole trips the gap check."""
    idx = pd.bdate_range(START, END)
    holed = idx.delete(range(120, 200))  # remove ~80 of ~520 trading days (>10%)
    v = verify_instrument("GAPPY", START, END, _downloader=_fixed(_series(holed)))
    assert not v.ok
    assert v.gap_ratio > 0.10
    assert any("gappy" in r for r in v.reasons)


def test_delisted_survivorship_rejected():
    """A series that starts on time and is dense but stops trading ~1 month
    before the window end fails ONLY survivorship — isolating that property."""
    early = _series(pd.bdate_range(START, "2023-11-30"))
    v = verify_instrument("DELISTED", START, END, _downloader=_fixed(early))
    assert not v.ok
    assert not v.survivorship_ok
    assert v.sufficient_history  # dense + on-time start → sufficiency passes
    assert v.gap_ratio <= 0.10  # no internal gaps
    assert any("survivorship" in r for r in v.reasons)


def test_no_silent_padding_of_short_series():
    """A short series is reported at its ACTUAL length and excluded — never
    padded up to the expected window length to force a pass (#772 anti-goal)."""
    half = _series(pd.bdate_range("2023-06-01", END))
    v = verify_instrument("SHORT", START, END, _downloader=_fixed(half))
    assert v.n_obs == len(half)
    assert v.n_obs < v.expected_obs
    assert not v.ok


# ── verify_universe — aggregation ─────────────────────────────────────────


def test_verify_universe_admits_only_clean_tickers():
    feeds = {
        "GOOD1": _clean(),
        "GOOD2": _clean(),
        "DEAD": pd.Series(dtype=float),
        "SHORT": _series(pd.bdate_range("2023-06-01", END)),
        "DELISTED": _series(pd.bdate_range(START, "2023-09-01")),
    }

    def _dl(ticker, s, e):
        return feeds[ticker]

    report = verify_universe(list(feeds), START, END, _downloader=_dl)
    assert set(report.admitted) == {"GOOD1", "GOOD2"}
    assert set(report.rejected) == {"DEAD", "SHORT", "DELISTED"}
    assert all(report.verdicts[t].ok for t in report.admitted)
    assert all(report.rejected[t] for t in report.rejected)  # every rejection has reasons


def test_verify_universe_empty_input():
    report = verify_universe([], START, END, _downloader=_empty)
    assert report.admitted == ()
    assert report.rejected == {}
