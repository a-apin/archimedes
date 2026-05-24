"""
FLOW 3: Vault Create + Deposit + Rebalance [MANDATORY]
========================================================

User story: Platform creates a Tier 1 vault. Users deposit USDC and
            receive vault tokens. Agent rebalances the vault.

Components exercised:
  - Chuan:  IVaultFactory + IVault contracts (create, deposit, rebalance)
  - Chuan:  IAgentOrchestrator (decides rebalance)
  - Önder:  IPortfolioConstructor (produces target allocations)
  - Marten: IChainExecutor (executes rebalance trades on-chain)
  - Daniel: Frontend vault detail page

Preconditions:
  - SyntheticFactory deployed with all 5 assets
  - AMM pools seeded with liquidity
  - VaultFactory deployed
"""

from archimedes.models.portfolio import RiskProfile

# ─────────────────────────────────────────────────────────────
# 3.1 Vault creation (Chuan's contract)
# ─────────────────────────────────────────────────────────────


class TestVaultCreation:
    """VaultFactory creates vaults."""

    def test_create_tier1_vault(self):
        """Agent address creates vault → tier = 1 (Archimedes Verified)."""
        pass

    def test_create_tier2_vault(self):
        """Random wallet creates vault → tier = 2 (Community)."""
        pass

    def test_vault_has_correct_fees(self):
        """Created vault has the specified management and performance fees."""
        pass

    def test_vault_is_agent_assisted(self):
        """Vault created with agentAssisted=true → isAgentAssisted() returns true."""
        pass

    def test_factory_tracks_vaults(self):
        """getVaults() and getVaultsByCreator() return the created vaults."""
        pass

    def test_vault_token_is_erc20(self):
        """Vault token is a standard ERC-20 (for AMM trading — aspirational #8)."""
        pass


# ─────────────────────────────────────────────────────────────
# 3.2 Deposit / withdraw (Chuan's contract, Daniel's frontend)
# ─────────────────────────────────────────────────────────────


class TestVaultDeposit:
    """User deposits USDC into a vault."""

    def test_deposit_mints_shares(self):
        """Depositing 1000 USDC → receives vault shares > 0."""
        pass

    def test_first_deposit_1_to_1(self):
        """First deposit: 1000 USDC → 1000 shares (1:1 ratio on empty vault)."""
        pass

    def test_second_deposit_at_current_price(self):
        """After NAV changes, new deposits mint shares at current price."""
        pass

    def test_deposit_zero_reverts(self):
        """Depositing 0 USDC should revert."""
        pass

    def test_preview_deposit_matches_actual(self):
        """previewDeposit(1000) matches the actual shares from deposit(1000)."""
        pass


class TestVaultWithdraw:
    """User withdraws USDC from a vault."""

    def test_redeem_returns_usdc(self):
        """Redeeming shares → receive USDC proportional to NAV."""
        pass

    def test_redeem_all_empties_position(self):
        """Redeeming all shares → user has 0 shares, receives full NAV share."""
        pass

    def test_withdraw_more_than_balance_reverts(self):
        """Withdrawing more shares than held should revert."""
        pass

    def test_preview_redeem_matches_actual(self):
        """previewRedeem(shares) matches the actual USDC from redeem(shares)."""
        pass

    def test_withdraw_after_profit(self):
        """If vault NAV increased, withdrawing returns more than deposited."""
        pass

    def test_withdraw_after_loss(self):
        """If vault NAV decreased, withdrawing returns less than deposited."""
        pass


# ─────────────────────────────────────────────────────────────
# 3.3 Set target allocations (Önder + Chuan)
# ─────────────────────────────────────────────────────────────


class TestTargetAllocations:
    """Vault creator sets target allocations (from Önder's math)."""

    def test_set_allocations_sums_to_100pct(self):
        """setTargetAllocations with weights summing to 10000 bps succeeds."""
        pass

    def test_set_allocations_not_summing_reverts(self):
        """Weights not summing to 10000 bps should revert."""
        pass

    def test_get_target_allocations_returns_set_values(self):
        """getTargetAllocations() returns what was set."""
        pass

    def test_only_creator_can_set_allocations(self):
        """Non-creator address calling setTargetAllocations should revert."""
        pass


class TestPortfolioConstructionIntegration:
    """Önder's IPortfolioConstructor → Vault target allocations."""

    async def test_construct_moderate_portfolio(self, portfolio_constructor, strategies, backtest_results, regime):
        """
        Given: moderate risk profile, 5 validated strategies, RISK_ON regime
        When:  construct() is called
        Then:  returns allocations with USYC between 20-40% (moderate floor)
        """
        allocations = portfolio_constructor.construct(
            risk_profile=RiskProfile.MODERATE,
            strategies=strategies,
            backtest_results=backtest_results,
            regime=regime,
        )

        total_weight = sum(a.weight for a in allocations)
        assert abs(total_weight - 1.0) < 0.001  # Weights sum to 1

        usyc_alloc = next((a for a in allocations if a.symbol == "USYC"), None)
        assert usyc_alloc is not None
        assert 0.20 <= usyc_alloc.weight <= 0.40  # Moderate USYC floor

    async def test_construct_crisis_increases_usyc(
        self, portfolio_constructor, strategies, backtest_results, crisis_regime
    ):
        """In CRISIS regime, USYC allocation should increase toward ceiling."""
        allocations = portfolio_constructor.construct(
            risk_profile=RiskProfile.MODERATE,
            strategies=strategies,
            backtest_results=backtest_results,
            regime=crisis_regime,
        )

        usyc_alloc = next((a for a in allocations if a.symbol == "USYC"), None)
        assert usyc_alloc is not None
        # In crisis, moderate profile should push USYC toward 40% ceiling
        assert usyc_alloc.weight >= 0.35

    async def test_no_single_strategy_exceeds_30pct(self, portfolio_constructor, strategies, backtest_results, regime):
        """Max 30% in any single strategy (design.md § 4.3.2 constraint)."""
        allocations = portfolio_constructor.construct(
            risk_profile=RiskProfile.AGGRESSIVE,
            strategies=strategies,
            backtest_results=backtest_results,
            regime=regime,
        )

        for alloc in allocations:
            assert alloc.weight <= 0.30 + 0.001  # 30% cap


# ─────────────────────────────────────────────────────────────
# 3.4 Rebalance execution (Chuan + Marten)
# ─────────────────────────────────────────────────────────────


class TestRebalance:
    """Agent rebalances vault holdings via AMM swaps."""

    def test_rebalance_restricted_to_creator_or_agent(self):
        """Random address calling rebalance() should revert."""
        pass

    def test_rebalance_changes_holdings(self):
        """After rebalance, getHoldings() reflects the new positions."""
        pass

    def test_rebalance_emits_event(self):
        """Rebalance emits Rebalanced(caller, tradesCount, timestamp)."""
        pass

    async def test_rebalance_executes_via_amm(self, chain_executor):
        """
        IChainExecutor.execute_trades() submits swap txs to AMM.
        Each trade returns a tx hash.
        """
        pass

    async def test_rebalance_cost_benefit_gate(self, agent_orchestrator):
        """
        Agent only rebalances if expected benefit > 2x estimated cost.
        Small drift → skip rebalance.
        """
        decision = await agent_orchestrator.evaluate_vault("0xVault")
        if decision.estimated_benefit < 2 * decision.estimated_cost_usdc:
            assert not decision.should_rebalance


# ─────────────────────────────────────────────────────────────
# 3.5 Vault API (Chuan's backend → Daniel's frontend)
# ─────────────────────────────────────────────────────────────


class TestVaultAPI:
    """Backend serves vault data to the frontend."""

    async def test_list_vaults_endpoint(self, client):
        """GET /api/vaults/ returns VaultListResponse."""
        response = await client.get("/api/vaults/")
        assert response.status_code == 200
        data = response.json()
        assert "vaults" in data
        assert "total" in data

    async def test_list_vaults_filter_by_tier(self, client):
        """GET /api/vaults/?tier=1 returns only Tier 1 vaults."""
        response = await client.get("/api/vaults/", params={"tier": 1})
        assert response.status_code == 200
        for vault in response.json()["vaults"]:
            assert vault["tier"] == 1

    async def test_vault_detail_endpoint(self, client, vault_address):
        """GET /api/vaults/{address} returns VaultDetailResponse with holdings."""
        response = await client.get(f"/api/vaults/{vault_address}")
        assert response.status_code == 200
        data = response.json()
        assert "holdings" in data
        assert "equity_curve" in data
        assert "recent_traces" in data

    async def test_vault_detail_404_for_invalid(self, client):
        """GET /api/vaults/0xINVALID returns 404."""
        response = await client.get("/api/vaults/0xINVALID")
        assert response.status_code == 404
