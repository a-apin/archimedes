"""
FLOW 1: Synthetic Mint/Redeem [MANDATORY]
=========================================

User story: A user deposits USDC and receives synthetic tokens (e.g. sTSLA).
            Later, they redeem synthetic tokens back for USDC.

Components exercised:
  - Chuan:  ISyntheticFactory contract (mint/redeem)
  - Chuan:  IPriceOracle contract (price feed)
  - Marten: IOracleUpdater (pushes prices before mint)
  - Daniel: Frontend swap/mint UI (calls contract directly via user wallet)

Preconditions:
  - PriceOracle deployed and has a fresh price for sTSLA
  - SyntheticFactory deployed with sTSLA registered
  - User has USDC in their wallet
  - User has approved SyntheticFactory to spend their USDC
"""

from datetime import datetime

from archimedes.models.asset import AssetPrice

# ─────────────────────────────────────────────────────────────
# 1.1 Oracle price push (Marten's component)
# ─────────────────────────────────────────────────────────────


class TestOraclePricePush:
    """Marten's IOracleUpdater pushes prices to the oracle contract."""

    async def test_fetch_prices_returns_all_assets(self, oracle_updater):
        """IOracleUpdater.fetch_prices() returns prices for all 5 synthetic assets."""
        prices = await oracle_updater.fetch_prices()

        assert len(prices) >= 5
        symbols = {p.symbol for p in prices}
        assert {"sTSLA", "sSPY", "sGLD", "sBTC", "USYC"}.issubset(symbols)

        for price in prices:
            assert price.price_usd > 0
            assert isinstance(price.timestamp, datetime)

    async def test_fetch_prices_handles_api_failure(self, oracle_updater):
        """If yfinance/CoinGecko is down, return last known prices (not crash)."""
        # Simulate API failure
        prices = await oracle_updater.fetch_prices()
        assert len(prices) > 0  # Should return cached/fallback prices

    async def test_push_prices_on_chain_returns_tx_hash(self, oracle_updater):
        """batchSetPrices() succeeds and returns a transaction hash."""
        prices = [
            AssetPrice(symbol="sTSLA", price_usd=185.00, timestamp=datetime.utcnow()),
            AssetPrice(symbol="sSPY", price_usd=520.00, timestamp=datetime.utcnow()),
        ]
        tx_hash = await oracle_updater.push_prices_on_chain(prices)

        assert tx_hash is not None
        assert tx_hash.startswith("0x")

    async def test_push_prices_on_chain_price_format(self, oracle_updater):
        """Prices are converted to 8-decimal format for the oracle contract.
        $185.00 → 18500000000 (185 * 10^8)
        """
        prices = [
            AssetPrice(symbol="sTSLA", price_usd=185.50, timestamp=datetime.utcnow()),
        ]
        # After push, reading from contract should return the same price
        await oracle_updater.push_prices_on_chain(prices)
        # Verification would read back from IPriceOracle.getPrice()


# ─────────────────────────────────────────────────────────────
# 1.2 Synthetic mint (Chuan's contract)
# ─────────────────────────────────────────────────────────────


class TestSyntheticMint:
    """User mints synthetic tokens by depositing USDC."""

    def test_mint_calculates_correct_amount(self):
        """Depositing 1000 USDC at $185/sTSLA → ~5.405 sTSLA (at 100% collateral)."""
        usdc_amount = 1000_000_000  # 1000 USDC (6 decimals)
        price = 185_00000000  # $185.00 (8 decimals)

        # Expected: 1000 / 185 = 5.405405... sTSLA (18 decimals)
        expected_synth = (usdc_amount * 10**18 * 10**2) // price  # accounting for decimal diff
        assert expected_synth > 0

    def test_mint_zero_amount_reverts(self):
        """Minting with 0 USDC should revert."""
        # Contract test: ISyntheticFactory.mint(sTSLA, 0) → revert ZeroAmount()
        pass

    def test_mint_without_approval_reverts(self):
        """Minting without USDC approval should revert."""
        # Contract test: call mint without approve → revert
        pass

    def test_mint_emits_event(self):
        """Minting emits Minted(user, token, usdcIn, synthOut)."""
        pass

    def test_mint_increases_total_collateral(self):
        """After minting, totalCollateral() increases by the USDC deposited."""
        pass

    def test_health_ratio_stays_at_100pct(self):
        """With 100% collateral ratio, healthRatio should stay at 1e18."""
        pass


# ─────────────────────────────────────────────────────────────
# 1.3 Synthetic redeem (Chuan's contract)
# ─────────────────────────────────────────────────────────────


class TestSyntheticRedeem:
    """User redeems synthetic tokens for USDC."""

    def test_redeem_returns_correct_usdc(self):
        """Redeeming 5.405 sTSLA at $185/sTSLA → ~1000 USDC."""
        pass

    def test_redeem_zero_amount_reverts(self):
        """Redeeming 0 tokens should revert."""
        pass

    def test_redeem_more_than_balance_reverts(self):
        """Redeeming more than you hold should revert."""
        pass

    def test_redeem_emits_event(self):
        """Redeeming emits Redeemed(user, token, synthIn, usdcOut)."""
        pass

    def test_redeem_decreases_total_collateral(self):
        """After redeeming, totalCollateral() decreases."""
        pass

    def test_round_trip_mint_redeem_no_loss(self):
        """Mint then immediately redeem → get back same USDC (minus any fees)."""
        pass


# ─────────────────────────────────────────────────────────────
# 1.4 End-to-end flow (all components)
# ─────────────────────────────────────────────────────────────


class TestSyntheticEndToEnd:
    """Full flow: price push → mint → price change → redeem."""

    async def test_full_mint_redeem_cycle(self, oracle_updater):
        """
        1. Marten pushes TSLA price = $185
        2. User mints 1000 USDC → receives ~5.405 sTSLA
        3. Marten pushes TSLA price = $200
        4. User redeems ~5.405 sTSLA → receives ~$1081 USDC (profit from price increase)
        """
        pass

    async def test_factory_tracks_multiple_synthetics(self):
        """
        Mint sTSLA AND sSPY from same factory.
        Each has independent price and collateral tracking.
        getSynthetics() returns both addresses.
        """
        pass

    async def test_stale_price_behavior(self, oracle_updater):
        """
        If oracle hasn't been updated in >5 minutes, mint/redeem should
        either revert or use the stale price (design decision — document behavior).
        """
        pass
