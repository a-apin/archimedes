"""Unit tests for ``archimedes.services.rf_series`` (issue #1409).

Hermetic: reads only the vendored ``backend/archimedes/data/rf/DGS3MO.csv``
(repo data, no network) plus literal in-test CSV fixtures for the
missing-value-marker case. No DB, no Redis, no `.env`.
"""

from __future__ import annotations

from datetime import date

import pytest
from archimedes.services import rf_series

# ─── _parse_csv_text: literal fixture slices ───────────────────────────


def test_parse_csv_handles_dot_missing_marker() -> None:
    """FRED's classic '.' missing-value marker is skipped, not coerced to 0.0,
    and does not poison the neighboring (real) rows either side of it."""
    text = "DATE,DGS3MO\n2020-01-01,1.55\n2020-01-02,.\n2020-01-03,1.54\n"
    parsed = rf_series._parse_csv_text(text)
    assert parsed == {"2020-01-01": 1.55, "2020-01-03": 1.54}
    assert "2020-01-02" not in parsed


def test_parse_csv_handles_empty_missing_marker() -> None:
    """The fredgraph.csv export path (the one actually vendored) uses an EMPTY
    value field for missing observations, not '.'. Same skip-don't-poison rule."""
    text = "observation_date,DGS3MO\n2020-06-01,0.15\n2020-06-02,\n2020-06-03,0.14\n"
    parsed = rf_series._parse_csv_text(text)
    assert parsed == {"2020-06-01": 0.15, "2020-06-03": 0.14}
    assert "2020-06-02" not in parsed


def test_parse_csv_skips_header_comment_lines() -> None:
    text = "# a staleness comment\n# another comment line\nDATE,DGS3MO\n2020-01-01,1.55\n"
    parsed = rf_series._parse_csv_text(text)
    assert parsed == {"2020-01-01": 1.55}


def test_parse_csv_accepts_both_date_column_names() -> None:
    a = rf_series._parse_csv_text("DATE,DGS3MO\n2020-01-01,1.55\n")
    b = rf_series._parse_csv_text("observation_date,DGS3MO\n2020-01-01,1.55\n")
    assert a == b == {"2020-01-01": 1.55}


def test_parse_csv_skips_unparseable_value(caplog: pytest.LogCaptureFixture) -> None:
    text = "DATE,DGS3MO\n2020-01-01,1.55\n2020-01-02,not-a-number\n2020-01-03,1.54\n"
    with caplog.at_level("WARNING"):
        parsed = rf_series._parse_csv_text(text)
    assert parsed == {"2020-01-01": 1.55, "2020-01-03": 1.54}


# ─── The real vendored file loads and is well-formed ───────────────────


def test_vendored_csv_loads_and_is_nonempty() -> None:
    series = rf_series._load_csv()
    assert len(series) > 1000  # decades of daily/business-day observations
    for v in list(series.values())[:50]:
        assert 0.0 <= v <= 25.0  # sane annualized-percent bound (historical range ~0-17%)


def test_vendored_csv_covers_the_2015_near_zero_rate_window() -> None:
    """Sanity precondition for the 2015-window regression test elsewhere: the
    vendored series must actually cover 2015 (the ZIRP-era near-zero rf window
    the decision record cites) for that test to be meaningful."""
    series = rf_series._load_csv()
    assert "2015-01-02" in series
    assert series["2015-01-02"] < 0.10  # ~0.02%, per the decision record's "rf≈0.05%"


# ─── resolve_annual_rf_for_dates: the alignment contract ───────────────


def test_no_dates_is_the_disclosed_fallback() -> None:
    rates, convention = rf_series.resolve_annual_rf_for_dates(None)
    assert rates == []
    assert convention == rf_series.RF_CONVENTION_FALLBACK

    rates, convention = rf_series.resolve_annual_rf_for_dates([])
    assert rates == []
    assert convention == rf_series.RF_CONVENTION_FALLBACK


def test_exact_dates_resolve_to_the_series() -> None:
    rates, convention = rf_series.resolve_annual_rf_for_dates(["2020-01-02", "2020-01-03"])
    assert convention == rf_series.RF_CONVENTION_SERIES
    assert len(rates) == 2
    assert all(r > 0.0 for r in rates)


def test_saturday_forward_fills_from_friday() -> None:
    """2020-01-03 is a Friday, 2020-01-04 is a Saturday (no published rate,
    the vendored series simply has no row for it) -- the Saturday must resolve
    to Friday's rate exactly, not the next available date's."""
    series = rf_series._load_csv()
    friday_rate = series["2020-01-03"]
    assert "2020-01-04" not in series  # precondition: no row published for the Saturday

    rates, convention = rf_series.resolve_annual_rf_for_dates(["2020-01-03", "2020-01-04"])
    assert convention == rf_series.RF_CONVENTION_SERIES
    assert rates[0] == friday_rate
    assert rates[1] == friday_rate  # forward-filled from Friday, not Monday 2020-01-06


def test_accepts_date_objects_not_just_strings() -> None:
    rates, convention = rf_series.resolve_annual_rf_for_dates([date(2020, 1, 3)])
    assert convention == rf_series.RF_CONVENTION_SERIES
    assert len(rates) == 1


def test_within_grace_window_forward_fills_past_series_end() -> None:
    series = rf_series._load_csv()
    sorted_dates = sorted(series)
    last_date = sorted_dates[-1]
    last_rate = series[last_date]
    last_date_obj = date.fromisoformat(last_date)

    # 5 calendar days past the last published date -- inside the 14-day grace.
    from datetime import timedelta

    near_future = (last_date_obj + timedelta(days=5)).isoformat()
    rates, convention = rf_series.resolve_annual_rf_for_dates([last_date, near_future])
    assert convention == rf_series.RF_CONVENTION_SERIES
    assert rates == [last_rate, last_rate]


def test_beyond_grace_window_falls_back_for_the_whole_grade(caplog: pytest.LogCaptureFixture) -> None:
    """Design item 2: a date more than 14 calendar days past the vendored
    series' end makes the WHOLE grade fall back to flat -- not just that one
    bar -- and the fallback is logged loudly (never silent)."""
    series = rf_series._load_csv()
    last_date_obj = date.fromisoformat(max(series))

    from datetime import timedelta

    far_future = (last_date_obj + timedelta(days=30)).isoformat()
    with caplog.at_level("WARNING"):
        rates, convention = rf_series.resolve_annual_rf_for_dates(["2020-01-02", far_future], flat_annual_pct=5.0)
    assert convention == rf_series.RF_CONVENTION_FALLBACK
    # The WHOLE grade falls back -- including the in-window 2020-01-02 bar,
    # not just the out-of-coverage one.
    assert rates == [5.0, 5.0]
    assert any("14" in rec.message or "grace" in rec.message.lower() for rec in caplog.records)


def test_date_before_series_start_falls_back() -> None:
    rates, convention = rf_series.resolve_annual_rf_for_dates(["1900-01-01"])
    assert convention == rf_series.RF_CONVENTION_FALLBACK
    assert rates == [5.0]


def test_custom_flat_annual_pct_used_in_fallback() -> None:
    rates, convention = rf_series.resolve_annual_rf_for_dates(None, flat_annual_pct=7.5)
    assert rates == []  # empty-dates fallback signals via [], caller broadcasts
    assert convention == rf_series.RF_CONVENTION_FALLBACK

    rates, convention = rf_series.resolve_annual_rf_for_dates(["1900-01-01"], flat_annual_pct=7.5)
    assert rates == [7.5]
    assert convention == rf_series.RF_CONVENTION_FALLBACK


# ─── Malformed / unsupported date input fails SOFT, not crashes ─────────
# (2026-08-20 review fix.) Every other unresolvable-date case above degrades
# to the flat rate, loudly logged, never a crash — a non-ISO string or an
# unsupported type used to be the one exception, raising uncaught out of
# `resolve_annual_rf_for_dates` and crashing the whole rigor gate. That
# contradicts this module's own fail-soft posture and the PR's own claim that
# "the mechanism is fully built, tested, and ready to wire the moment a
# caller has real per-bar dates" — a caller's date format is exactly the kind
# of input this function must not trust blindly.


def test_non_iso_date_string_falls_back_instead_of_crashing(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level("WARNING"):
        rates, convention = rf_series.resolve_annual_rf_for_dates(["2015-01-02", "01/01/2015"], flat_annual_pct=6.0)
    assert convention == rf_series.RF_CONVENTION_FALLBACK
    assert rates == [6.0, 6.0]  # WHOLE grade falls back, including the valid first bar
    assert any("malformed" in rec.message.lower() for rec in caplog.records)


def test_unsupported_date_type_falls_back_instead_of_crashing(caplog: pytest.LogCaptureFixture) -> None:
    """A numpy.datetime64 -- the type a pandas/numpy backtest series is most
    likely to actually emit -- must degrade the same way, not raise."""
    np = pytest.importorskip("numpy")
    with caplog.at_level("WARNING"):
        rates, convention = rf_series.resolve_annual_rf_for_dates([np.datetime64("2015-01-02")], flat_annual_pct=6.0)
    assert convention == rf_series.RF_CONVENTION_FALLBACK
    assert rates == [6.0]
    assert any("unsupported date type" in rec.message.lower() for rec in caplog.records)
