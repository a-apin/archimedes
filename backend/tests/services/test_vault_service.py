"""Unit coverage for VaultService — the API-layer composer over chain reads.

Targets the high-traffic methods (`list_vaults`, `_metrics_to_summary`,
`_compute_returns` fallback, `_token_to_symbol`, `_get_on_chain_names`,
`_get_target_allocations`, `_get_recent_traces`). Mocks `chain_executor`,
`trace_publisher`, the contract loader, and Redis so no live network /
DB calls fire.

Added 2026-05-24 as part of the #147 coverage-gate lift.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from archimedes.api.schemas import VaultHolding
from archimedes.services.vault_service import VaultService


def _metrics(
    address: str = "0xabc",
    tier: int = 1,
    aum: float = 1_000.0,
    share_price: float = 1.0,
    is_agent: bool = True,
) -> dict:
    return {
        "vault_address": address,
        "tier": tier,
        "creator": "0xc0ffee",
        "total_aum_usdc": aum,
        "share_price_usdc": share_price,
        "is_agent_assisted": is_agent,
        "management_fee_bps": 150,  # 1.5%
        "performance_fee_bps": 2000,  # 20%
        "high_water_mark": 1.0,
    }


class TestMetricsToSummary:
    def test_field_propagation(self) -> None:
        summary = VaultService()._metrics_to_summary(_metrics())
        assert summary.address == "0xabc"
        assert summary.tier == 1
        assert summary.creator == "0xc0ffee"
        assert summary.aum_usdc == 1_000.0
        assert summary.share_price == 1.0
        assert summary.is_agent_assisted is True
        # bps → percent conversion
        assert summary.management_fee_pct == 1.5
        assert summary.performance_fee_pct == 20.0

    def test_name_falls_back_to_short_address_when_no_metadata(self) -> None:
        """Without VaultMetadata, name shows the address slug — honest fallback
        instead of the misleading 'Vault T2' shared by every tier-2 vault."""
        summary = VaultService()._metrics_to_summary(_metrics(address="0xdeadbeef1234567890abcdef", tier=2))
        assert summary.name == "Vault 0xdead…cdef"

    def test_symbol_falls_back_to_address_prefix(self) -> None:
        """Symbol fallback uses the 0x-stripped 4-char address slug."""
        summary = VaultService()._metrics_to_summary(_metrics(address="0xdeadbeef1234"))
        assert summary.symbol == "vdead"

    def test_metadata_overrides_fallbacks(self) -> None:
        """When VaultMetadata is provided, real name/symbol/creator/created_at
        flow through — replacing the address-slug placeholders."""
        from datetime import UTC, datetime

        meta = SimpleNamespace(
            name="Momentum Alpha",
            symbol="vMOM",
            creator_address="0xb0bb1e",
            created_at=datetime(2026, 5, 1, 12, 0, tzinfo=UTC),
        )
        summary = VaultService()._metrics_to_summary(_metrics(), meta=meta)
        assert summary.name == "Momentum Alpha"
        assert summary.symbol == "vMOM"
        assert summary.creator == "0xb0bb1e"
        assert summary.created_at == "2026-05-01T12:00:00+00:00"

    def test_created_at_empty_when_no_metadata(self) -> None:
        """No metadata → no fake `now()` timestamp; surface emptiness honestly."""
        summary = VaultService()._metrics_to_summary(_metrics())
        assert summary.created_at == ""

    def test_returns_default_to_zero(self) -> None:
        summary = VaultService()._metrics_to_summary(_metrics())
        assert summary.return_24h == 0.0
        assert summary.return_7d == 0.0
        assert summary.return_30d == 0.0
        assert summary.return_inception == 0.0


class TestListVaults:
    @pytest.mark.asyncio
    async def test_chain_failure_returns_empty_response(self) -> None:
        with patch("archimedes.services.vault_service.chain_executor") as ce:
            ce.get_all_vaults = AsyncMock(side_effect=RuntimeError("rpc down"))
            resp = await VaultService().list_vaults()
            assert resp.vaults == []
            assert resp.total == 0

    @pytest.mark.asyncio
    async def test_per_vault_failure_is_skipped(self) -> None:
        with patch("archimedes.services.vault_service.chain_executor") as ce:
            ce.get_all_vaults = AsyncMock(return_value=["0xA", "0xB", "0xC"])

            def metrics_side_effect(addr):
                if addr == "0xB":
                    raise RuntimeError("boom")
                return _metrics(address=addr, aum=100.0)

            ce.get_vault_metrics = AsyncMock(side_effect=metrics_side_effect)
            resp = await VaultService().list_vaults()
            assert resp.total == 2
            addresses = {v.address for v in resp.vaults}
            assert addresses == {"0xA", "0xC"}

    @pytest.mark.asyncio
    async def test_tier_filter_excludes_other_tiers(self) -> None:
        with patch("archimedes.services.vault_service.chain_executor") as ce:
            ce.get_all_vaults = AsyncMock(return_value=["0xA", "0xB"])
            ce.get_vault_metrics = AsyncMock(
                side_effect=lambda addr: _metrics(
                    address=addr,
                    tier=1 if addr == "0xA" else 2,
                )
            )
            resp = await VaultService().list_vaults(tier=2)
            assert [v.address for v in resp.vaults] == ["0xB"]

    @pytest.mark.asyncio
    async def test_sort_by_aum_usdc_desc(self) -> None:
        # The sort_by key is matched against VaultSummaryResponse field names
        # via getattr; "aum_usdc" hits the actual field while the default
        # "aum" misses (returns 0 for every row → source order).
        with patch("archimedes.services.vault_service.chain_executor") as ce:
            ce.get_all_vaults = AsyncMock(return_value=["0xA", "0xB", "0xC"])
            aums = {"0xA": 200, "0xB": 500, "0xC": 100}
            ce.get_vault_metrics = AsyncMock(side_effect=lambda addr: _metrics(address=addr, aum=aums[addr]))
            resp = await VaultService().list_vaults(sort_by="aum_usdc", order="desc")
            assert [v.aum_usdc for v in resp.vaults] == [500, 200, 100]

    @pytest.mark.asyncio
    async def test_sort_by_aum_usdc_asc(self) -> None:
        with patch("archimedes.services.vault_service.chain_executor") as ce:
            ce.get_all_vaults = AsyncMock(return_value=["0xA", "0xB"])
            aums = {"0xA": 50, "0xB": 200}
            ce.get_vault_metrics = AsyncMock(side_effect=lambda addr: _metrics(address=addr, aum=aums[addr]))
            resp = await VaultService().list_vaults(sort_by="aum_usdc", order="asc")
            assert [v.aum_usdc for v in resp.vaults] == [50, 200]

    @pytest.mark.asyncio
    async def test_default_sort_key_misses_and_preserves_source_order(self) -> None:
        # Document the existing behavior: sort_by="aum" (the default) does NOT
        # match any VaultSummaryResponse field, so the sort is a no-op and the
        # source order is preserved. This pins the behavior so a future rename
        # of the field is a deliberate decision, not a silent regression.
        with patch("archimedes.services.vault_service.chain_executor") as ce:
            ce.get_all_vaults = AsyncMock(return_value=["0xA", "0xB", "0xC"])
            aums = {"0xA": 200, "0xB": 500, "0xC": 100}
            ce.get_vault_metrics = AsyncMock(side_effect=lambda addr: _metrics(address=addr, aum=aums[addr]))
            resp = await VaultService().list_vaults(sort_by="aum", order="desc")
            assert [v.address for v in resp.vaults] == ["0xA", "0xB", "0xC"]

    @pytest.mark.asyncio
    async def test_pagination_respects_limit_and_offset(self) -> None:
        addrs = ["0x" + chr(0x41 + i) for i in range(5)]  # 0xA..0xE
        with patch("archimedes.services.vault_service.chain_executor") as ce:
            ce.get_all_vaults = AsyncMock(return_value=addrs)
            ce.get_vault_metrics = AsyncMock(side_effect=lambda addr: _metrics(address=addr))
            resp = await VaultService().list_vaults(limit=2, offset=1, sort_by="aum")
            assert resp.total == 5  # total is pre-pagination
            assert len(resp.vaults) == 2


class TestComputeReturnsFallback:
    @pytest.mark.asyncio
    async def test_empty_allocations_yield_zero_returns(self) -> None:
        # When Redis is unreachable AND allocations is empty → all zeros
        svc = VaultService()
        result = await svc._compute_returns("0xV", [])
        assert result == {
            "return_24h": 0.0,
            "return_7d": 0.0,
            "return_30d": 0.0,
            "return_inception": 0.0,
        }

    @pytest.mark.asyncio
    async def test_zero_weight_allocation_treated_as_empty(self) -> None:
        svc = VaultService()
        alloc = VaultHolding(symbol="sTSLA", token_address="0xT", amount=0.0, value_usdc=0.0, weight_pct=0.0)
        result = await svc._compute_returns("0xV", [alloc])
        # All zeros: total_weight == 0 falls through to the empty-allocation shape
        assert result["return_24h"] == 0.0
        assert result["return_30d"] == 0.0

    @pytest.mark.asyncio
    async def test_known_symbol_uses_expected_return_table(self) -> None:
        # sSPY → 10% annual per ASSET_EXPECTED_RETURN. Verify the period
        # decomposition is finite and structured.
        svc = VaultService()
        alloc = VaultHolding(symbol="sSPY", token_address="0xS", amount=0.0, value_usdc=0.0, weight_pct=100.0)
        result = await svc._compute_returns("0xVaultZ", [alloc])
        assert "return_24h" in result
        assert "return_30d" in result
        # Sanity: 30-day return should be larger in magnitude than 24h
        assert abs(result["return_30d"]) >= abs(result["return_24h"])


class TestComputeReturnsAsyncRedis:
    @pytest.mark.asyncio
    async def test_uses_async_redis_and_awaits_snapshot(self) -> None:
        """Regression for #914: the Redis snapshot path must use the asyncio
        client and await ping/get so it never blocks the event loop. We mock
        redis.asyncio.Redis with a valid snapshot plus a mocked oracle; the
        returns must come from the Redis price path (not the deterministic
        fallback), which only happens if the awaits land. A revert to the sync
        redis.Redis would miss this patch and fall through to the fallback."""
        import json

        alloc = VaultHolding(symbol="sSPY", token_address="0xT", amount=1.0, value_usdc=1.0, weight_pct=100.0)

        fake_redis = MagicMock()
        fake_redis.ping = AsyncMock(return_value=True)
        fake_redis.get = AsyncMock(return_value=json.dumps({"0xT": 1.0}))
        fake_redis.aclose = AsyncMock()

        # Oracle reports $2.0 (raw 2_000_000 / 1e6) → +100% vs the $1.0 creation
        # price stored in the snapshot.
        oracle = MagicMock()
        oracle.functions.price.return_value.call = AsyncMock(return_value=2_000_000)
        loader = MagicMock()
        loader.oracle_for.return_value = oracle

        svc = VaultService()
        with (
            patch("redis.asyncio.Redis", return_value=fake_redis),
            patch("archimedes.chain.contracts.get_contract_loader", return_value=loader),
        ):
            result = await svc._compute_returns("0xVault", [alloc])

        fake_redis.ping.assert_awaited_once()
        fake_redis.get.assert_awaited_once()
        fake_redis.aclose.assert_awaited_once()
        # 100% weighted return → return_30d == 1.0, return_24h == 1.0 * 0.033.
        assert result["return_30d"] == pytest.approx(1.0)
        assert result["return_24h"] == pytest.approx(0.033)


class TestRecentTracesSwallowFailures:
    @pytest.mark.asyncio
    async def test_chain_failure_returns_empty_list(self) -> None:
        with patch("archimedes.services.vault_service.trace_publisher") as tp:
            tp.loader.trace_registry.functions.getTracesByVault.return_value.call = AsyncMock(
                side_effect=RuntimeError("rpc down")
            )
            traces = await VaultService()._get_recent_traces("0xV", limit=3)
            assert traces == []


class TestTargetAllocationsSwallowFailures:
    @pytest.mark.asyncio
    async def test_loader_failure_returns_empty_list(self) -> None:
        with patch("archimedes.chain.contracts.get_contract_loader") as gl:
            gl.side_effect = RuntimeError("contracts unavailable")
            allocs = await VaultService()._get_target_allocations("0xV")
            assert allocs == []


class TestOnChainNamesSwallowFailures:
    @pytest.mark.asyncio
    async def test_loader_failure_returns_none_pair(self) -> None:
        with patch("archimedes.chain.contracts.get_contract_loader") as gl:
            gl.side_effect = RuntimeError("contracts unavailable")
            name, symbol = await VaultService()._get_on_chain_names("0xV")
            assert name is None
            assert symbol is None


class TestTokenToSymbol:
    @pytest.mark.asyncio
    async def test_usdc_address_returns_usdc_symbol(self) -> None:
        settings = SimpleNamespace(usdc_address="0xUSDC", synth_addresses={})
        with patch("archimedes.chain.client.chain_client") as cc:
            cc.settings = settings
            sym = await VaultService()._token_to_symbol("0xUSDC")
            assert sym == "USDC"

    @pytest.mark.asyncio
    async def test_synth_address_matches_table(self) -> None:
        settings = SimpleNamespace(usdc_address="0xUSDC", synth_addresses={"sTSLA": "0xT"})
        with patch("archimedes.chain.client.chain_client") as cc:
            cc.settings = settings
            sym = await VaultService()._token_to_symbol("0xT")
            assert sym == "sTSLA"

    @pytest.mark.asyncio
    async def test_unknown_token_falls_back_to_unknown(self) -> None:
        settings = SimpleNamespace(usdc_address="0xUSDC", synth_addresses={"sTSLA": "0xT"})
        loader = MagicMock()
        loader.token.return_value.functions.symbol.return_value.call = AsyncMock(side_effect=RuntimeError())
        with patch("archimedes.chain.client.chain_client") as cc:
            cc.settings = settings
            sym = await VaultService()._token_to_symbol("0xUNKNOWN", loader=loader)
            assert sym == "UNKNOWN"
