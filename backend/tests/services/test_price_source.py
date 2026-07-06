"""Hermetic tests for the Pyth Hermes price source + the oracle cascade.

No network: the Hermes HTTP call is mocked at the aiohttp boundary; the oracle
integration mocks the lazy-imported price_source functions + yfinance/crypto
fetchers. Pins: fail-safe behavior (never raise), correct mantissa·10^expo
parsing, publish_time → timestamp, the Pyth→yfinance→admin cascade, and that the
DEFAULT mode is byte-for-byte the legacy path.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

from archimedes.models.asset import AssetPrice
from archimedes.services.price_source import (
    PYTH_FEED_IDS,
    _parse_hermes_price,
    fetch_pyth_prices,
    hermes_base_url,
    load_admin_prices,
    merge_fill,
    price_source_mode,
)

NOW = datetime(2026, 6, 30, tzinfo=UTC)
BTC_ID = PYTH_FEED_IDS["sBTC"]
SPY_ID = PYTH_FEED_IDS["sSPY"]


# ── Mock aiohttp ──────────────────────────────────────────────────────────


class _Resp:
    def __init__(self, status, payload):
        self.status = status
        self._payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def json(self):
        return self._payload


class _Session:
    def __init__(self, resp=None, raise_exc=None):
        self._resp = resp
        self._raise = raise_exc
        self.closed = False

    def get(self, *a, **k):
        if self._raise:
            raise self._raise
        return self._resp

    async def close(self):
        self.closed = True


def _hermes_entry(feed_id, price_int, expo, publish_time):
    return {"id": feed_id, "price": {"price": str(price_int), "expo": expo, "publish_time": publish_time}}


# ── _parse_hermes_price ───────────────────────────────────────────────────


def test_parse_applies_exponent():
    e = _hermes_entry(BTC_ID, 5824734000000, -8, 1782824614)  # 58247.34
    p = _parse_hermes_price(e, "sBTC", NOW)
    assert p is not None
    assert abs(p.price_usd - 58247.34) < 0.01
    assert p.source == "pyth_hermes"
    # timestamp comes from publish_time, not the fallback
    assert p.timestamp == datetime.fromtimestamp(1782824614, tz=UTC)


def test_parse_nonpositive_returns_none():
    assert _parse_hermes_price(_hermes_entry(BTC_ID, 0, -8, 1782824614), "sBTC", NOW) is None
    assert _parse_hermes_price(_hermes_entry(BTC_ID, -5, -8, 1782824614), "sBTC", NOW) is None


def test_parse_garbled_returns_none():
    assert _parse_hermes_price({"price": {}}, "sBTC", NOW) is None
    assert _parse_hermes_price({}, "sBTC", NOW) is None


def test_parse_missing_publish_time_uses_fallback_ts():
    e = {"id": BTC_ID, "price": {"price": "100000000", "expo": -8}}  # 1.0, no publish_time
    p = _parse_hermes_price(e, "sBTC", NOW)
    assert p is not None and p.timestamp == NOW


# ── fetch_pyth_prices ─────────────────────────────────────────────────────


async def test_fetch_returns_mapped_prices():
    payload = {
        "parsed": [
            _hermes_entry(BTC_ID, 5800000000000, -8, 1782824614),  # 58000
            _hermes_entry(SPY_ID, 51230000000, -8, 1782824600),  # 512.30
        ]
    }
    out = await fetch_pyth_prices(["sBTC", "sSPY"], session=_Session(_Resp(200, payload)))
    assert set(out) == {"sBTC", "sSPY"}
    assert abs(out["sBTC"].price_usd - 58000) < 1
    assert out["sSPY"].source == "pyth_hermes"


async def test_fetch_skips_unmapped_symbols():
    # sOIL/sNKY have no feed id → no request needed, empty result.
    out = await fetch_pyth_prices(["sOIL", "sNKY"], session=_Session(_Resp(200, {"parsed": []})))
    assert out == {}


async def test_fetch_non_200_is_failsafe_empty():
    out = await fetch_pyth_prices(["sBTC"], session=_Session(_Resp(503, {})))
    assert out == {}


async def test_fetch_exception_is_failsafe_empty():
    out = await fetch_pyth_prices(["sBTC"], session=_Session(raise_exc=RuntimeError("boom")))
    assert out == {}


async def test_fetch_strips_0x_prefix_on_response_ids():
    payload = {"parsed": [_hermes_entry("0x" + BTC_ID, 5800000000000, -8, 1782824614)]}
    out = await fetch_pyth_prices(["sBTC"], session=_Session(_Resp(200, payload)))
    assert "sBTC" in out


# ── load_admin_prices ─────────────────────────────────────────────────────


def test_admin_prices_inline_json(monkeypatch):
    monkeypatch.setenv("ADMIN_PRICES_JSON", '{"sSPY": 512.3, "sBTC": 61000}')
    out = load_admin_prices()
    assert out["sSPY"].price_usd == 512.3
    assert out["sBTC"].source == "admin"


def test_admin_prices_unset_is_empty(monkeypatch):
    monkeypatch.delenv("ADMIN_PRICES_JSON", raising=False)
    assert load_admin_prices() == {}


def test_admin_prices_skips_nonpositive(monkeypatch):
    monkeypatch.setenv("ADMIN_PRICES_JSON", '{"sSPY": -1, "sBTC": 61000}')
    out = load_admin_prices()
    assert "sSPY" not in out and "sBTC" in out


def test_admin_prices_garbled_is_empty(monkeypatch):
    monkeypatch.setenv("ADMIN_PRICES_JSON", "not json")
    assert load_admin_prices() == {}


# ── pure helpers ──────────────────────────────────────────────────────────


def test_merge_fill_primary_wins():
    prim = {"sBTC": AssetPrice("sBTC", 100, NOW, "pyth_hermes")}
    fb = {"sBTC": AssetPrice("sBTC", 999, NOW, "yfinance"), "sSPY": AssetPrice("sSPY", 5, NOW, "yfinance")}
    merged = merge_fill(prim, fb)
    assert merged["sBTC"].source == "pyth_hermes"  # primary wins
    assert merged["sSPY"].source == "yfinance"  # gap filled by fallback


def test_price_source_mode_default_and_override(monkeypatch):
    monkeypatch.delenv("PRICE_SOURCE", raising=False)
    assert price_source_mode() == "yfinance"
    monkeypatch.setenv("PRICE_SOURCE", "  Cascade ")
    assert price_source_mode() == "cascade"


def test_hermes_base_url_override(monkeypatch):
    monkeypatch.delenv("PYTH_HERMES_URL", raising=False)
    assert hermes_base_url() == "https://hermes.pyth.network"
    monkeypatch.setenv("PYTH_HERMES_URL", "https://my-hermes.example/")
    assert hermes_base_url() == "https://my-hermes.example"


# ── OracleUpdater cascade integration ─────────────────────────────────────


async def test_default_mode_is_legacy_path(monkeypatch):
    """PRICE_SOURCE unset → fetch_prices uses _fetch_yfinance + _fetch_crypto only,
    never touches Pyth. This pins that a deploy is a no-op until the flag flips."""
    monkeypatch.delenv("PRICE_SOURCE", raising=False)
    monkeypatch.delenv("ADMIN_PRICES_JSON", raising=False)
    from archimedes.chain.oracle_updater import OracleUpdater

    upd = OracleUpdater()
    with (
        patch.object(upd, "_fetch_yfinance", return_value=[AssetPrice("sSPY", 500, NOW, "yfinance")]),
        patch.object(upd, "_fetch_crypto", AsyncMock(return_value=[AssetPrice("sBTC", 60000, NOW, "coingecko")])),
        patch(
            "archimedes.services.price_source.fetch_pyth_prices",
            AsyncMock(return_value={"sBTC": AssetPrice("sBTC", 1, NOW, "pyth_hermes")}),
        ) as pyth_mock,
    ):
        prices = await upd.fetch_prices()
    pyth_mock.assert_not_called()  # legacy path never calls Pyth
    by = {p.symbol: p for p in prices}
    assert by["sBTC"].source == "coingecko"
    assert by["sSPY"].source == "yfinance"


async def test_cascade_mode_prefers_pyth_then_yfinance(monkeypatch):
    monkeypatch.setenv("PRICE_SOURCE", "cascade")
    monkeypatch.delenv("ADMIN_PRICES_JSON", raising=False)
    from archimedes.chain.oracle_updater import OracleUpdater

    upd = OracleUpdater()
    # Pyth covers sBTC (FRESH observation) but not sSPY (the only remaining synth
    # in YFINANCE_MAP after the #943 prune) — yfinance must fill that gap. Use a
    # real-fresh timestamp: a fresh Pyth read is what counts as "covered" now that
    # stale observations fall through (below).
    fresh = datetime.now(UTC)
    pyth_ret = {
        "sBTC": AssetPrice("sBTC", 58000, fresh, "pyth_hermes"),
    }
    with (
        patch("archimedes.services.price_source.fetch_pyth_prices", AsyncMock(return_value=pyth_ret)),
        patch.object(upd, "_fetch_yfinance", return_value=[AssetPrice("sSPY", 512, fresh, "yfinance")]) as yf_mock,
        patch.object(upd, "_fetch_crypto", AsyncMock(return_value=[])) as crypto_mock,
    ):
        prices = await upd.fetch_prices()
    by = {p.symbol: p for p in prices}
    assert by["sBTC"].source == "pyth_hermes"  # Pyth wins for covered
    assert by["sSPY"].source == "yfinance"  # fallback filled the gap
    # yfinance was asked only for the symbols Pyth didn't cover
    called_symbols = set(yf_mock.call_args[0][0].keys())
    assert called_symbols == {"sSPY"}
    crypto_mock.assert_not_called()  # sBTC came from Pyth → no CoinGecko


async def test_cascade_stale_pyth_falls_back_to_yfinance(monkeypatch):
    # A Pyth observation older than the upstream-staleness cap must NOT count as
    # "covered" — the symbol falls through to yfinance instead of being silently
    # dropped later by the on-chain staleness gate with nothing to fill the gap.
    monkeypatch.setenv("PRICE_SOURCE", "cascade")
    monkeypatch.delenv("ADMIN_PRICES_JSON", raising=False)
    from archimedes.chain.oracle_updater import OracleUpdater

    upd = OracleUpdater()
    fresh = datetime.now(UTC)
    stale = fresh - timedelta(seconds=upd._max_upstream_staleness_s + 600)
    pyth_ret = {
        "sBTC": AssetPrice("sBTC", 58000, fresh, "pyth_hermes"),  # fresh → covered
        "sSPY": AssetPrice("sSPY", 512, stale, "pyth_hermes"),  # STALE → must fall through
    }
    with (
        patch("archimedes.services.price_source.fetch_pyth_prices", AsyncMock(return_value=pyth_ret)),
        patch.object(upd, "_fetch_yfinance", return_value=[AssetPrice("sSPY", 515, fresh, "yfinance")]) as yf_mock,
        patch.object(upd, "_fetch_crypto", AsyncMock(return_value=[])),
    ):
        prices = await upd.fetch_prices()
    by = {p.symbol: p for p in prices}
    assert by["sBTC"].source == "pyth_hermes"  # fresh Pyth still wins
    assert by["sSPY"].source == "yfinance"  # stale Pyth → yfinance fallback filled it
    # yfinance was asked for sSPY precisely because its Pyth read was stale.
    assert "sSPY" in set(yf_mock.call_args[0][0].keys())


async def test_admin_override_wins_in_any_mode(monkeypatch):
    monkeypatch.delenv("PRICE_SOURCE", raising=False)  # legacy mode
    monkeypatch.setenv("ADMIN_PRICES_JSON", '{"sSPY": 999.0}')
    from archimedes.chain.oracle_updater import OracleUpdater

    upd = OracleUpdater()
    with (
        patch.object(upd, "_fetch_yfinance", return_value=[AssetPrice("sSPY", 500, NOW, "yfinance")]),
        patch.object(upd, "_fetch_crypto", AsyncMock(return_value=[])),
    ):
        prices = await upd.fetch_prices()
    by = {p.symbol: p for p in prices}
    assert by["sSPY"].price_usd == 999.0
    assert by["sSPY"].source == "admin"
