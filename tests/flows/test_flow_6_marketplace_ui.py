"""
FLOW 6: Marketplace UI [MANDATORY]
====================================

User story: User lands on the marketplace, sees vault leaderboard,
            clicks into a vault, sees holdings and traces, and can
            access swap, deposit, and contract address info.

Components exercised:
  - Chuan:  Backend API (all read endpoints)
  - Daniel: Frontend (renders data from API)

This flow tests the API contract that Daniel's frontend depends on.
All tests hit the REST API; frontend rendering is Daniel's responsibility.
"""


# ─────────────────────────────────────────────────────────────
# 6.1 Marketplace landing page (leaderboard)
# ─────────────────────────────────────────────────────────────


class TestMarketplaceLanding:
    """The leaderboard is the first thing users see."""

    async def test_vault_list_returns_data(self, client):
        """GET /api/vaults/ returns at least 1 vault."""
        response = await client.get("/api/vaults/")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] >= 1
        assert len(data["vaults"]) >= 1

    async def test_vault_list_sorted_by_aum(self, client):
        """Default sort is by AUM descending."""
        response = await client.get("/api/vaults/", params={"sort_by": "aum", "order": "desc"})
        data = response.json()
        aums = [v["aum_usdc"] for v in data["vaults"]]
        assert aums == sorted(aums, reverse=True)

    async def test_vault_list_sorted_by_return(self, client):
        """Sort by 7d return."""
        response = await client.get("/api/vaults/", params={"sort_by": "return_7d"})
        assert response.status_code == 200

    async def test_vault_summary_has_required_fields(self, client):
        """Each vault in the list has all fields Daniel needs for the card."""
        response = await client.get("/api/vaults/")
        vault = response.json()["vaults"][0]
        required_fields = [
            "address",
            "name",
            "symbol",
            "tier",
            "creator",
            "aum_usdc",
            "share_price",
            "return_24h",
            "return_7d",
            "management_fee_pct",
            "performance_fee_pct",
            "is_agent_assisted",
            "depositors",
        ]
        for field in required_fields:
            assert field in vault, f"Missing field: {field}"

    async def test_tier_filter_works(self, client):
        """Filter by tier=1 returns only verified vaults."""
        response = await client.get("/api/vaults/", params={"tier": 1})
        for vault in response.json()["vaults"]:
            assert vault["tier"] == 1

    async def test_pagination_works(self, client):
        """limit and offset work correctly."""
        r1 = await client.get("/api/vaults/", params={"limit": 1, "offset": 0})
        r2 = await client.get("/api/vaults/", params={"limit": 1, "offset": 1})
        if r1.json()["total"] > 1:
            assert r1.json()["vaults"][0]["address"] != r2.json()["vaults"][0]["address"]


# ─────────────────────────────────────────────────────────────
# 6.2 Vault detail page
# ─────────────────────────────────────────────────────────────


class TestVaultDetailPage:
    """Daniel's vault detail page renders this data."""

    async def test_vault_detail_has_holdings(self, client, vault_address):
        """Holdings list with symbol, amount, value, weight."""
        response = await client.get(f"/api/vaults/{vault_address}")
        data = response.json()
        assert len(data["holdings"]) > 0
        holding = data["holdings"][0]
        assert "symbol" in holding
        assert "value_usdc" in holding
        assert "weight_pct" in holding

    async def test_vault_detail_has_equity_curve(self, client, vault_address):
        """Equity curve for performance charting."""
        response = await client.get(f"/api/vaults/{vault_address}")
        data = response.json()
        assert "equity_curve" in data
        # May be empty for new vaults, but the field exists

    async def test_vault_detail_has_recent_traces(self, client, vault_address):
        """Recent reasoning traces embedded in the vault detail."""
        response = await client.get(f"/api/vaults/{vault_address}")
        data = response.json()
        assert "recent_traces" in data

    async def test_vault_detail_has_regime(self, client, vault_address):
        """Current regime shown on vault detail (for Tier 1)."""
        response = await client.get(f"/api/vaults/{vault_address}")
        data = response.json()
        if data["tier"] == 1:
            assert data["current_regime"] is not None


# ─────────────────────────────────────────────────────────────
# 6.3 Asset prices
# ─────────────────────────────────────────────────────────────


class TestAssetPrices:
    """Asset price data for the marketplace."""

    async def test_list_assets_returns_all(self, client):
        """GET /api/assets/ returns all registered assets with prices."""
        response = await client.get("/api/assets/")
        assert response.status_code == 200
        data = response.json()
        symbols = {a["symbol"] for a in data["assets"]}
        assert {"sTSLA", "sSPY", "sGLD", "sBTC", "USYC"}.issubset(symbols)

    async def test_asset_has_current_price(self, client):
        """Each asset has a non-zero price."""
        response = await client.get("/api/assets/")
        for asset in response.json()["assets"]:
            if asset["symbol"] != "USDC":
                assert asset["price_usd"] > 0

    async def test_asset_price_history(self, client):
        """GET /api/assets/sTSLA/history returns price points for charting."""
        response = await client.get("/api/assets/sTSLA/history", params={"interval": "1d", "limit": 7})
        assert response.status_code == 200
        data = response.json()
        assert data["symbol"] == "sTSLA"
        assert len(data["prices"]) <= 7


# ─────────────────────────────────────────────────────────────
# 6.4 Regime display
# ─────────────────────────────────────────────────────────────


class TestRegimeDisplay:
    """Current regime for the header/dashboard indicator."""

    async def test_current_regime_endpoint(self, client):
        """GET /api/regime/current returns the current regime."""
        response = await client.get("/api/regime/current")
        assert response.status_code == 200
        data = response.json()
        assert data["regime"] in ("risk_on", "risk_off", "transition", "crisis")
        assert 0 <= data["confidence"] <= 1
        assert "signals" in data


# ─────────────────────────────────────────────────────────────
# 6.5 Contract addresses (frontend → on-chain bridge)
# ─────────────────────────────────────────────────────────────


class TestContractAddresses:
    """Frontend needs contract addresses for direct on-chain calls."""

    async def test_contract_addresses_endpoint(self, client):
        """GET /api/config/contracts returns all addresses."""
        response = await client.get("/api/config/contracts")
        assert response.status_code == 200
        data = response.json()

        # Core contracts
        assert data["usdc"].startswith("0x")
        assert data["synthetic_factory"].startswith("0x")
        assert data["amm_router"].startswith("0x")
        assert data["vault_factory"].startswith("0x")
        assert data["reasoning_trace_registry"].startswith("0x")
        assert data["price_oracle"].startswith("0x")

        # Chain info
        assert data["chain_id"] > 0
        assert data["rpc_url"]

        # Synthetic addresses
        assert "sTSLA" in data["synthetics"]
        assert data["synthetics"]["sTSLA"].startswith("0x")

    async def test_contract_addresses_include_pools(self, client):
        """Pool addresses for the swap UI."""
        response = await client.get("/api/config/contracts")
        data = response.json()
        assert "USDC/sTSLA" in data["pools"]


# ─────────────────────────────────────────────────────────────
# 6.6 Swap preview (API supports frontend swap UI)
# ─────────────────────────────────────────────────────────────


class TestSwapPreview:
    """Swap quote endpoint for the frontend swap UI."""

    async def test_swap_quote_valid_pair(self, client):
        """GET /api/swap/quote returns a valid quote."""
        response = await client.get(
            "/api/swap/quote",
            params={"token_in": "0xUSDC", "token_out": "0xsTSLA", "amount_in": 100},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["amount_out"] > 0
        assert data["price_impact_pct"] >= 0
        assert data["fee_pct"] > 0
        assert data["min_amount_out"] <= data["amount_out"]

    async def test_swap_quote_price_impact_increases_with_size(self, client):
        """Larger swaps have more price impact."""
        small = await client.get(
            "/api/swap/quote",
            params={"token_in": "0xUSDC", "token_out": "0xsTSLA", "amount_in": 100},
        )
        large = await client.get(
            "/api/swap/quote",
            params={"token_in": "0xUSDC", "token_out": "0xsTSLA", "amount_in": 50000},
        )
        assert large.json()["price_impact_pct"] > small.json()["price_impact_pct"]
