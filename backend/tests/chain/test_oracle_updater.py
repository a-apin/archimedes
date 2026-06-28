"""Tests for OracleUpdater sanity bounds — audit #13 / issue #508.

Target: backend/archimedes/chain/oracle_updater.py
The oracle runner must refuse to push prices that are non-positive, stale
upstream, or deviate beyond the configured cap vs the last known good price —
while preserving the legitimate update path (anti-goal: bound it, don't
remove it).

Hermetic: chain reads and Circle HTTP calls are mocked at the boundary
(contract loader / aiohttp session). No network, no Arc RPC, no Circle.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from archimedes.chain.oracle_updater import (
    DEFAULT_MAX_DEVIATION_BPS,
    DEFAULT_MAX_UPSTREAM_STALENESS_SECONDS,
    OracleUpdater,
)
from archimedes.models.asset import AssetPrice

# ── Helpers ───────────────────────────────────────────────────


def _price(symbol: str = "sTSLA", usd: float = 100.0, age_seconds: float = 0.0) -> AssetPrice:
    return AssetPrice(
        symbol=symbol,
        price_usd=usd,
        timestamp=datetime.now(UTC) - timedelta(seconds=age_seconds),
        source="yfinance",
    )


def _int6(usd: float) -> int:
    return int(usd * 1e6)


@pytest.fixture
def updater() -> OracleUpdater:
    return OracleUpdater()


# ── _validate_for_push ────────────────────────────────────────


class TestValidateForPush:
    async def test_accepts_fresh_price_within_deviation_cap(self, updater):
        # +10% vs reference — inside the 20% default cap
        price = _price(usd=110.0)
        with patch.object(updater, "_get_reference_price_int", AsyncMock(return_value=(_int6(100.0), True))):
            assert await updater._validate_for_push(price, _int6(110.0)) is None

    async def test_rejects_deviation_beyond_cap(self, updater):
        # +30% vs reference — beyond the 2000 bps default cap
        price = _price(usd=130.0)
        with patch.object(updater, "_get_reference_price_int", AsyncMock(return_value=(_int6(100.0), True))):
            reason = await updater._validate_for_push(price, _int6(130.0))
        assert reason is not None
        assert "deviation" in reason

    async def test_rejects_downward_deviation_beyond_cap(self, updater):
        # -30% is equally out of bounds
        price = _price(usd=70.0)
        with patch.object(updater, "_get_reference_price_int", AsyncMock(return_value=(_int6(100.0), True))):
            reason = await updater._validate_for_push(price, _int6(70.0))
        assert reason is not None
        assert "deviation" in reason

    async def test_rejects_stale_upstream_data(self, updater):
        # 1 hour old — past the 15-minute default staleness cap.
        # Reference mock would pass the deviation check, proving staleness
        # alone is sufficient to refuse the push.
        price = _price(usd=100.0, age_seconds=3600)
        with patch.object(updater, "_get_reference_price_int", AsyncMock(return_value=(_int6(100.0), True))):
            reason = await updater._validate_for_push(price, _int6(100.0))
        assert reason is not None
        assert "stale" in reason

    async def test_rejects_zero_int_price(self, updater):
        # Sub-microdollar float truncates to a 0 on-chain int, which the
        # contract rejects — the backend must refuse before spending a tx.
        price = _price(usd=5e-7)
        reason = await updater._validate_for_push(price, int(5e-7 * 1e6))
        assert reason is not None
        assert "non-positive" in reason

    async def test_accepts_first_push_without_reference(self, updater):
        # Reference confirmed absent (None, known=True) → bootstrap is allowed
        price = _price(usd=100.0)
        with patch.object(updater, "_get_reference_price_int", AsyncMock(return_value=(None, True))):
            assert await updater._validate_for_push(price, _int6(100.0)) is None

    async def test_rejects_when_reference_unobtainable(self, updater):
        # Reference unobtainable (None, known=False) → fail closed: a non-None
        # rejection that names the fail-closed reason (issue #587, part 2).
        price = _price(usd=100.0)
        with patch.object(updater, "_get_reference_price_int", AsyncMock(return_value=(None, False))):
            reason = await updater._validate_for_push(price, _int6(100.0))
        assert reason is not None
        assert "failing closed" in reason

    async def test_naive_timestamp_treated_as_utc(self, updater):
        # A tz-naive timestamp must not crash the age computation
        naive_now = datetime.now(UTC).replace(tzinfo=None)
        price = AssetPrice(symbol="sTSLA", price_usd=100.0, timestamp=naive_now, source="yfinance")
        with patch.object(updater, "_get_reference_price_int", AsyncMock(return_value=(None, True))):
            assert await updater._validate_for_push(price, _int6(100.0)) is None

    async def test_env_overrides_respected(self, monkeypatch):
        monkeypatch.setenv("ORACLE_MAX_DEVIATION_BPS", "5000")
        monkeypatch.setenv("ORACLE_MAX_UPSTREAM_STALENESS_SECONDS", "7200")
        updater = OracleUpdater()
        assert updater._max_deviation_bps == 5000
        assert updater._max_upstream_staleness_s == 7200
        # +30% now passes under the widened 50% cap; 1h-old data passes 2h cap
        price = _price(usd=130.0, age_seconds=3600)
        with patch.object(updater, "_get_reference_price_int", AsyncMock(return_value=(_int6(100.0), True))):
            assert await updater._validate_for_push(price, _int6(130.0)) is None

    async def test_defaults_match_contract_bound(self, updater):
        assert DEFAULT_MAX_DEVIATION_BPS == 2000  # mirrors PriceOracle.maxDeviationBps
        assert updater._max_deviation_bps == DEFAULT_MAX_DEVIATION_BPS
        assert updater._max_upstream_staleness_s == DEFAULT_MAX_UPSTREAM_STALENESS_SECONDS


# ── _get_reference_price_int ─────────────────────────────────


class TestReferencePrice:
    async def test_prefers_onchain_price(self, updater):
        # On-chain read succeeds with a positive price → (price, known=True).
        oracle_contract = MagicMock()
        oracle_contract.functions.price.return_value.call = AsyncMock(return_value=_int6(123.45))
        loader = MagicMock()
        loader.oracle_for.return_value = oracle_contract
        updater._last_pushed_price_int["sTSLA"] = _int6(999.0)
        with patch("archimedes.chain.contracts.get_contract_loader", return_value=loader):
            assert await updater._get_reference_price_int("sTSLA") == (_int6(123.45), True)

    async def test_falls_back_to_last_pushed_when_chain_read_fails(self, updater):
        # On-chain read throws but a cached last-pushed value exists →
        # (last_pushed, known=True): we still have a usable reference.
        updater._last_pushed_price_int["sTSLA"] = _int6(101.0)
        with patch(
            "archimedes.chain.contracts.get_contract_loader",
            side_effect=ConnectionError("RPC down"),
        ):
            assert await updater._get_reference_price_int("sTSLA") == (_int6(101.0), True)

    async def test_confirmed_absent_when_chain_read_succeeds_with_no_price(self, updater):
        # On-chain read SUCCEEDS but reports 0 and nothing is cached →
        # (None, known=True): reference confirmed absent (genuine first push).
        oracle_contract = MagicMock()
        oracle_contract.functions.price.return_value.call = AsyncMock(return_value=0)
        loader = MagicMock()
        loader.oracle_for.return_value = oracle_contract
        with patch("archimedes.chain.contracts.get_contract_loader", return_value=loader):
            assert await updater._get_reference_price_int("sTSLA") == (None, True)

    async def test_unobtainable_when_chain_read_fails_and_no_cache(self, updater):
        # On-chain read THROWS and there is no cached fallback →
        # (None, known=False): reference unobtainable, caller must fail closed.
        with patch(
            "archimedes.chain.contracts.get_contract_loader",
            side_effect=ConnectionError("RPC down"),
        ):
            assert await updater._get_reference_price_int("sTSLA") == (None, False)


# ── push_prices_on_chain refuses bad prices ──────────────────


def _mock_aiohttp_session(post_response: MagicMock | None = None) -> tuple[MagicMock, MagicMock]:
    """Build a mock aiohttp.ClientSession context manager (the HTTP boundary)."""
    session = MagicMock()
    if post_response is not None:
        post_cm = MagicMock()
        post_cm.__aenter__ = AsyncMock(return_value=post_response)
        post_cm.__aexit__ = AsyncMock(return_value=False)
        session.post = MagicMock(return_value=post_cm)
    session_cm = MagicMock()
    session_cm.__aenter__ = AsyncMock(return_value=session)
    session_cm.__aexit__ = AsyncMock(return_value=False)
    return session_cm, session


@pytest.fixture
def circle_creds(monkeypatch):
    monkeypatch.setenv("CIRCLE_API_KEY", "test-api-key")
    monkeypatch.setenv("CIRCLE_ENTITY_SECRET", "ab" * 32)
    monkeypatch.setenv("WALLET_ID", "test-wallet-id")


class TestPushPricesOnChain:
    async def test_refuses_to_push_price_failing_deviation_check(self, circle_creds):
        updater = OracleUpdater()
        updater._circle_public_key = "cached-pem"  # skip key fetch
        session_cm, session = _mock_aiohttp_session()

        bad = _price(symbol="sTSLA", usd=130.0)  # +30% vs reference → rejected
        with (
            patch("archimedes.chain.oracle_updater.aiohttp.ClientSession", return_value=session_cm),
            patch.object(updater, "_get_reference_price_int", AsyncMock(return_value=(_int6(100.0), True))),
        ):
            result = await updater.push_prices_on_chain([bad])

        assert result is None
        session.post.assert_not_called()

    async def test_refuses_to_push_stale_price(self, circle_creds):
        updater = OracleUpdater()
        updater._circle_public_key = "cached-pem"
        session_cm, session = _mock_aiohttp_session()

        stale = _price(symbol="sTSLA", usd=100.0, age_seconds=3600)
        with (
            patch("archimedes.chain.oracle_updater.aiohttp.ClientSession", return_value=session_cm),
            patch.object(updater, "_get_reference_price_int", AsyncMock(return_value=(_int6(100.0), True))),
        ):
            result = await updater.push_prices_on_chain([stale])

        assert result is None
        session.post.assert_not_called()

    async def test_legitimate_update_path_preserved(self, circle_creds):
        # Anti-goal check: a fresh, in-bounds price still pushes through and
        # is recorded as the new fallback reference.
        updater = OracleUpdater()
        updater._circle_public_key = "cached-pem"

        resp = MagicMock(status=201)
        resp.json = AsyncMock(return_value={"data": {"id": "tx-123"}})
        session_cm, session = _mock_aiohttp_session(post_response=resp)

        # sSPY is an on-chain synth in the SSOT (#764); sTSLA is compliance-held
        # off-chain (#725) so it has no oracle address and would be skipped.
        good = _price(symbol="sSPY", usd=110.0)  # +10%, inside the cap
        with (
            patch("archimedes.chain.oracle_updater.aiohttp.ClientSession", return_value=session_cm),
            patch("archimedes.chain.oracle_updater._encrypt_entity_secret", return_value="ciphertext"),
            patch.object(updater, "_get_reference_price_int", AsyncMock(return_value=(_int6(100.0), True))),
        ):
            result = await updater.push_prices_on_chain([good])

        assert result == "tx-123"
        session.post.assert_called_once()
        assert updater._last_pushed_price_int["sSPY"] == _int6(110.0)

    async def test_fails_closed_when_reference_unobtainable(self, circle_creds):
        # Issue #587, part 2: when the deviation reference is unobtainable
        # (on-chain read failed AND no cached fallback), the symbol must NOT be
        # pushed — fail closed, no tx, rather than send it unchecked.
        updater = OracleUpdater()
        updater._circle_public_key = "cached-pem"
        session_cm, session = _mock_aiohttp_session()

        price = _price(symbol="sTSLA", usd=100.0)
        with (
            patch("archimedes.chain.oracle_updater.aiohttp.ClientSession", return_value=session_cm),
            patch.object(updater, "_get_reference_price_int", AsyncMock(return_value=(None, False))),
        ):
            result = await updater.push_prices_on_chain([price])

        assert result is None
        session.post.assert_not_called()

    async def test_fails_closed_on_rpc_outage_after_restart(self, circle_creds):
        # End-to-end variant: simulate the real failure mode — the contract
        # loader throws (RPC outage) and _last_pushed_price_int is empty (fresh
        # process restart). No mock of _get_reference_price_int — it must resolve
        # to (None, False) itself and the push must be skipped (no tx).
        updater = OracleUpdater()
        updater._circle_public_key = "cached-pem"
        assert updater._last_pushed_price_int == {}  # fresh restart, no fallback
        session_cm, session = _mock_aiohttp_session()

        price = _price(symbol="sTSLA", usd=100.0)
        with (
            patch("archimedes.chain.oracle_updater.aiohttp.ClientSession", return_value=session_cm),
            patch(
                "archimedes.chain.contracts.get_contract_loader",
                side_effect=ConnectionError("RPC down"),
            ),
        ):
            result = await updater.push_prices_on_chain([price])

        assert result is None
        session.post.assert_not_called()
