"""Oracle on-chain price formatting (#738 behavior b).

Target: backend/archimedes/chain/oracle_updater.py — push_prices_on_chain()

The issue speculated an 8-decimal convention ($185.50 → 18_550_000_000), but the
code (and its inline comment "6 decimals, matches PriceOracle.sol") fixes the
on-chain integer at SIX decimals:

    price_int = int(price.price_usd * 1e6)

So $185.50 → 185_500_000. This test pins that real convention by asserting the
exact `abiParameters` value submitted to Circle's contractExecution endpoint —
not by re-deriving the formula in the test (which would just restate the bug if
there were one). Guards against a silent 6↔8 decimal drift that would mis-price
every on-chain push.

Hermetic: the aiohttp HTTP boundary + the deviation-reference read are mocked.
No network, no Circle, no Arc RPC.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import pytest
from archimedes.chain.client import chain_client
from archimedes.chain.oracle_updater import OracleUpdater
from archimedes.models.asset import AssetPrice

# oracle_addresses is SSOT-driven (#764/#765) and excludes compliance-held single stocks
# (sTSLA/sNVDA are not in the SSOT), so this decimal-formatting test mocks the address
# boundary to stay independent of the deployed-universe contents — it pins the 6-dec int
# formula, not address resolution.
_TEST_ORACLE_ADDRS = {"sTSLA": "0x" + "11" * 20, "sNVDA": "0x" + "22" * 20}


def _price(symbol: str = "sTSLA", usd: float = 185.50) -> AssetPrice:
    return AssetPrice(symbol=symbol, price_usd=usd, timestamp=datetime.now(UTC), source="yfinance")


def _mock_aiohttp_session(post_response: MagicMock):
    """Build a mock aiohttp.ClientSession CM whose .post yields post_response."""
    session = MagicMock()
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
    monkeypatch.setenv("WALLET_ID", "wallet-uuid")


class TestOraclePriceFormatting:
    async def test_185_50_pushes_six_decimal_integer(self, circle_creds):
        """$185.50 → 185_500_000 (6-dec), NOT 18_550_000_000 (8-dec)."""
        updater = OracleUpdater()
        updater._circle_public_key = "cached-pem"  # skip the key fetch

        resp = MagicMock(status=201)
        resp.json = AsyncMock(return_value={"data": {"id": "tx-1"}})
        session_cm, session = _mock_aiohttp_session(resp)

        with (
            patch("archimedes.chain.oracle_updater.aiohttp.ClientSession", return_value=session_cm),
            patch("archimedes.chain.oracle_updater._encrypt_entity_secret", return_value="ciphertext"),
            patch.object(
                type(chain_client.settings),
                "oracle_addresses",
                new_callable=PropertyMock,
                return_value=_TEST_ORACLE_ADDRS,
            ),
            # First push for this symbol → reference confirmed absent → allowed.
            patch.object(updater, "_get_reference_price_int", AsyncMock(return_value=(None, True))),
        ):
            result = await updater.push_prices_on_chain([_price(usd=185.50)])

        assert result == "tx-1"
        session.post.assert_called_once()
        payload = session.post.call_args.kwargs["json"]
        # The on-chain int is the single uint256 abiParameter.
        assert payload["abiFunctionSignature"] == "setPrice(uint256)"
        assert payload["abiParameters"] == ["185500000"]
        assert payload["abiParameters"] != ["18550000000"]  # explicitly NOT 8-dec
        # The cached last-pushed reference is recorded in the SAME 6-dec units.
        assert updater._last_pushed_price_int["sTSLA"] == 185_500_000

    async def test_fractional_cents_truncate_toward_zero(self, circle_creds):
        """int(x * 1e6) truncates — $1.0000005 → 1_000_000 (not 1_000_001)."""
        updater = OracleUpdater()
        updater._circle_public_key = "cached-pem"
        resp = MagicMock(status=201)
        resp.json = AsyncMock(return_value={"data": {"id": "tx-2"}})
        session_cm, session = _mock_aiohttp_session(resp)

        with (
            patch("archimedes.chain.oracle_updater.aiohttp.ClientSession", return_value=session_cm),
            patch("archimedes.chain.oracle_updater._encrypt_entity_secret", return_value="ciphertext"),
            patch.object(
                type(chain_client.settings),
                "oracle_addresses",
                new_callable=PropertyMock,
                return_value=_TEST_ORACLE_ADDRS,
            ),
            patch.object(updater, "_get_reference_price_int", AsyncMock(return_value=(None, True))),
        ):
            await updater.push_prices_on_chain([_price(symbol="sNVDA", usd=1.0000005)])

        payload = session.post.call_args.kwargs["json"]
        assert payload["abiParameters"] == ["1000000"]
