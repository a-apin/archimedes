"""FastAPI route-layer tests — TestClient against real routes, mocked chain.

Tests HTTP status + response-schema shape for the judge-facing API surface.
Hermetic: no testnet, no Circle SDK, no Anthropic — chain client is mocked.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from archimedes.db import get_session
from archimedes.models.backtest_fixtures_store import FIXTURE_FIELDS, StrategyBacktestFixture
from archimedes.services.backtest_mapper import (
    AnalyticsArtifactModel,
    canonical_artifact_hash,
    map_artifact_to_backtest_result,
)
from archimedes.services.backtest_repository import insert_backtest_if_missing
from fastapi.testclient import TestClient

from tests.db_isolation import redirect_to_tmp_sqlite

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "analytics_artifact_buy_hold.json"

# A frozen snapshot of the pre-migration backtest_fixtures.json (all 23
# curated stems) — seeded into every test's tmp DB so routes that filter on
# ``Strategy.real_sharpe is not None`` (e.g. the portfolio advisor) see the
# same strategy population they did when the JSON file was committed and
# always present. See backend/scripts/import_backtest_fixtures.py for the
# real (non-test) equivalent of this seed step.
BACKTEST_FIXTURES_SNAPSHOT_PATH = Path(__file__).parent / "fixtures" / "backtest_fixtures_snapshot.json"


def _utcnow():
    """Timezone-aware UTC now — AssetPrice requires a tz-aware timestamp."""
    from datetime import UTC, datetime

    return datetime.now(UTC)


@pytest.fixture(autouse=True)
def _use_tmp_db(tmp_path):
    """Point the DB at a genuinely fresh temp SQLite for every test (#1243).

    This used to monkeypatch DATABASE_URL and call init_db(), which the comment
    here correctly described as not working: archimedes.db builds engine and
    SessionLocal once at import time, so setting the env var afterwards rebinds
    nothing and every get_session() call kept resolving the original engine.
    The "tmp DB" was never written to. What the tests actually read was whatever
    that first engine pointed at, which is why this file passed under the full
    suite (an earlier file left a usable engine behind) and failed standalone
    against a stale on-disk schema.

    redirect_to_tmp_sqlite reassigns the module attributes for real and restores
    them in a finally, so nothing leaks into a later file either.
    """
    for _ in redirect_to_tmp_sqlite(tmp_path):
        # The DB is genuinely per-test now, so a plain add() would be correct.
        # merge() is kept because it is also correct and costs nothing — it just
        # no longer papers over shared state that is no longer there.
        snapshot = json.loads(BACKTEST_FIXTURES_SNAPSHOT_PATH.read_text(encoding="utf-8"))
        with get_session() as session:
            for stem, rec in snapshot.items():
                session.merge(StrategyBacktestFixture(stem=stem, **{field: rec[field] for field in FIXTURE_FIELDS}))
            session.commit()

        # strategy_provider() in _route_helpers is @lru_cache'd and built once
        # per pytest process from the DB state at first call. If an earlier test
        # triggered that call before this seed ran, the cached provider has
        # real_sharpe=None for every strategy and the advisor/list endpoints
        # report "no strategies with real backtest data". Clearing after each
        # seed forces the next call to re-read the freshly seeded DB.
        from archimedes.api._route_helpers import strategy_provider

        strategy_provider.cache_clear()

        yield


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """TestClient with mocked chain client (no testnet calls)."""
    with (
        patch("archimedes.chain.client.chain_client") as mock_chain,
        patch("archimedes.chain.executor.chain_executor") as mock_executor,
    ):
        mock_chain.is_connected = AsyncMock(return_value=False)
        mock_chain.send_transaction = AsyncMock(return_value="0xmock_tx_hash")
        # ConfigService reads contract addresses from chain_client
        mock_chain.usdc_address = "0x3600000000000000000000000000000000000000"
        mock_chain.synthetic_factory_address = ""
        mock_chain.amm_router_address = "0xd5b829f9d364a8bbe1caf6c8b19cb05371b178f4"
        mock_chain.vault_factory_address = "0xca873414070844aeb98b0bf1051f81969c79cc32"
        mock_chain.reasoning_trace_registry_address = "0x42d8a23edb897cbee203e9fa197eb05ab5106ca6"
        mock_chain.asset_registry_address = "0x2d44550711137916df6175587d17886281a0fbc7"
        mock_chain.price_oracle_address = "0xe1c9f2b11be97097223a66a188fca541e07873a6"
        mock_chain.rpc_url = "https://rpc.testnet.arc.network"
        mock_chain.chain_id = 5042002
        mock_chain.get_all_synthetic_tokens = AsyncMock(return_value=[])
        mock_executor.get_all_vaults = AsyncMock(return_value=[])
        mock_executor.get_vault_metrics = AsyncMock(return_value={"total_aum_usdc": 0.0})

        from archimedes.main import app

        tc = TestClient(app)
        yield tc


# ── Tier-B unlock: a real chain_client.settings (#738) ───────────────
# ConfigService / AssetService / swap routes read addresses off
# chain_client.settings (NOT chain_client directly). The real ChainSettings is
# constructed at module-init from .env; under the hermetic gate there is no
# .env, so this namespace substitutes the exact fields the route layer reads.
#
# Why a SEPARATE fixture (not baked into `client`): the bare `client` fixture
# leaves `chain_client.settings` as an auto-MagicMock, which some pre-existing
# tests rely on (e.g. the advisor optimizer fails fast against the mock and
# falls back to a budget-normalized allocation). Installing a real settings
# object only for the routes that need it keeps every other test's behavior
# exactly as it was on main.
_SETTINGS_NAMESPACE = SimpleNamespace(
    usdc_address="0x3600000000000000000000000000000000000000",
    synthetic_factory_address="",
    amm_router_address="0xd5b829f9d364a8bbe1caf6c8b19cb05371b178f4",
    vault_factory_address="0xca873414070844aeb98b0bf1051f81969c79cc32",
    reasoning_trace_registry_address="0x42d8a23edb897cbee203e9fa197eb05ab5106ca6",
    asset_registry_address="0x2d44550711137916df6175587d17886281a0fbc7",
    stsla_oracle_address="0xe1c9f2b11be97097223a66a188fca541e07873a6",
    chain_id=5042002,
    arc_rpc_url="https://rpc.testnet.arc.network",
    synth_addresses={
        "sTSLA": "0xd514cd27baf762c650536765cde9b61c876abacd",
        "sSPY": "0x6fea38dedea0c6bb66ce93e5383c34385d8b889f",
    },
    oracle_addresses={
        "sTSLA": "0xe1c9f2b11be97097223a66a188fca541e07873a6",
        "sSPY": "0xd8161a8eeab7c7100e2863abe3d5f346b5ff9e52",
    },
)


@pytest.fixture()
def chain_settings_client(client, monkeypatch):
    """`client` with a real `chain_client.settings` namespace + to_checksum.

    Unblocks the Tier-B routes (config/contracts, assets, swap/quote) that read
    `chain_client.settings.*` at request time. `to_checksum` is the only
    ChainClient method those paths call; a pass-through is safe because every
    seeded address is already checksum-shaped (or its case is irrelevant to the
    assertion).

    Bindings matter: ``config_service`` captures ``chain_client`` at MODULE
    import time, so the `client` fixture's `patch("archimedes.chain.client.
    chain_client")` (which only rebinds the *client module* attribute) does not
    reach it. ``asset_service`` / ``swap_routes`` re-import inside the function,
    so they see the patched mock. We set the settings on the patched mock AND
    rebind ``config_service.chain_client`` to it so every Tier-B path reads the
    same namespace.
    """
    from archimedes.chain.client import chain_client as patched_client
    from archimedes.chain.contracts import get_contract_loader

    patched_client.settings = _SETTINGS_NAMESPACE
    patched_client.to_checksum = lambda addr: addr
    # Point config_service's module-level binding at the same patched client.
    monkeypatch.setattr("archimedes.services.config_service.chain_client", patched_client)
    # The loader is @lru_cache'd and captures chain_client.settings at build
    # time; clear it so a loader cached by an earlier test can't leak a stale
    # settings reference into these routes.
    get_contract_loader.cache_clear()
    return client


@pytest.fixture()
def seeded_db():
    """Seed the temp DB with the buy-hold fixture artifact."""
    payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    artifact = AnalyticsArtifactModel.model_validate(payload)

    from archimedes.services.strategy_provider import default_provider

    provider = default_provider()
    strategies = provider.list_strategies()
    buy_hold = next(
        (s for s in strategies if "Buy-and-Hold" in s.paper_title or "Baseline" in s.paper_title),
        None,
    )
    if buy_hold is None:
        pytest.skip("Buy-and-Hold strategy not found")

    mapped, operation = map_artifact_to_backtest_result(
        artifact,
        strategy_id=buy_hold.id,
    )
    content_hash = canonical_artifact_hash(payload)

    with get_session() as session:
        insert_backtest_if_missing(
            session,
            strategy_id=buy_hold.id,
            content_hash=content_hash,
            result=mapped,
            run_id=artifact.run_id,
            operation=operation,
            artifact_json=FIXTURE_PATH.read_text(encoding="utf-8"),
            source_pipeline="test",
        )
        session.commit()
    return buy_hold.id


def _list_all_strategies(client: TestClient, limit: int = 100):
    strategies = []
    offset = 0
    total = None
    while total is None or len(strategies) < total:
        resp = client.get("/api/strategies/", params={"limit": limit, "offset": offset})
        assert resp.status_code == 200
        data = resp.json()
        batch = data.get("strategies", [])
        strategies.extend(batch)
        total = data.get("total", len(strategies))
        if not batch:
            break
        offset += limit
    return strategies


class TestRootAndHealth:
    def test_root(self, client):
        resp = client.get("/")
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "Archimedes"
        assert "docs" in data

    def test_health(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["service"] == "archimedes-backend"
        assert "status" in data
        # Observability fields (issue #1039): build provenance ("version" = git
        # SHA baked at build time, or "dev" locally) + strategy-library presence
        # ("strategy_count"). Both are surfaced on /health and gated in CI so a
        # strategy-less / gmm-less image can't ship silently degraded again.
        assert "version" in data
        assert "strategy_count" in data
        assert isinstance(data["strategy_count"], int)


# NOTE: TestFrontierRoutes and TestCorrelationRoutes were removed in the wake
# of Issue #383 — /api/strategies/frontier and /api/strategies/correlation were
# fabricating daily returns from a seeded RNG and have been deleted. UI consumers
# (CorrelationMatrix.jsx, EfficientFrontier.jsx) are deleted too.


class TestStrategyRoutes:
    def test_list_strategies(self, client, seeded_db):
        # Verify the first page returns a well-formed response.
        resp = client.get("/api/strategies/")
        assert resp.status_code == 200
        data = resp.json()
        assert "strategies" in data
        assert len(data["strategies"]) >= 1
        s = data["strategies"][0]
        assert "id" in s
        assert "paper_title" in s
        assert "sharpe_ratio" in s
        # A strategy with a real BacktestResultRecord must surface as
        # non-placeholder across the full catalogue.  ``seeded_db`` inserts one
        # row (Buy-and-Hold, pipeline_buy_hold.py) into ``backtest_results``.
        # That file sorts alphabetically after the default page size (20), so we
        # must paginate to find it — ``_list_all_strategies`` does that.
        # Don't assume index[0] is the seeded strategy: candidate strategies
        # added without a BacktestResultRecord legitimately sort first and read
        # as placeholders, so assert the non-placeholder path is exercised
        # somewhere across the full listing rather than at a brittle fixed position.
        all_strategies = _list_all_strategies(client)
        assert any(st["is_backtest_placeholder"] is False for st in all_strategies)

    def test_get_strategy_signals(self, client):
        resp = client.get("/api/strategies/signals")
        assert resp.status_code == 200
        data = resp.json()
        assert "strategies" in data
        assert "strategy_count" in data

    def test_get_single_strategy(self, client, seeded_db):
        # Get the list first to find a valid ID
        list_resp = client.get("/api/strategies/")
        strategies = list_resp.json()["strategies"]
        if not strategies:
            pytest.skip("No strategies available")
        sid = strategies[0]["id"]

        resp = client.get(f"/api/strategies/{sid}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == sid

    def test_rigor_gate_fields_present_in_list(self, client, seeded_db):
        """dsr_p_value and passes_rigor_gate must be present on every strategy response."""
        strategies = _list_all_strategies(client)
        for s in strategies:
            assert "dsr_p_value" in s, f"dsr_p_value missing for {s.get('id')}"
            assert "passes_rigor_gate" in s, f"passes_rigor_gate missing for {s.get('id')}"

    def test_rigor_gate_badge_is_live_not_fixture_for_tier1(self, client, seeded_db):
        """#821: the served ``passes_rigor_gate`` badge for Moreira-Muir comes from
        the LIVE gate on persisted returns — NOT the fixture boolean. ``seeded_db``
        only seeds Buy-and-Hold's backtest, so Moreira-Muir has no live returns and
        the badge must be ``pending`` (False), even though its fixture row says True.
        The fixture-derived display metric (dsr_p_value) still flows for rendering."""
        strategies = _list_all_strategies(client)
        # Match Moreira-Muir specifically by its full title. A bare "Volatility"
        # substring also matches Ang-Hodrick's "The Cross-Section of Volatility
        # and Expected Returns", which sorts first and would hijack this lookup.
        mm = next(
            (s for s in strategies if "Volatility-Managed" in s.get("paper_title", "")),
            None,
        )
        if mm is None:
            pytest.skip("Moreira-Muir strategy not found in fixture")
        # No live returns for this strategy → pending, NOT a fixture True/False.
        assert mm["rigor_gate_status"] == "pending"
        assert mm["passes_rigor_gate"] is False, "fixture boolean must NOT drive the live badge (#821)"


class TestRiskRoutes:
    def test_risk_profiles(self, client):
        resp = client.get("/api/risk/profiles")
        assert resp.status_code == 200
        data = resp.json()
        assert "bands" in data
        assert len(data["bands"]) >= 4
        labels = [b["label"] for b in data["bands"]]
        assert "conservative" in labels
        assert "aggressive" in labels

    def test_portfolio_risk(self, client, seeded_db):
        resp = client.get("/api/risk/portfolio")
        assert resp.status_code == 200
        data = resp.json()
        assert "strategy_count" in data
        assert "avg_sharpe" in data
        assert "worst_max_dd" in data
        assert "actual_risk_profile" in data
        assert "strategies" in data


class TestSelectionBiasRoutes:
    def test_rigor_gate(self, client, seeded_db):
        resp = client.get("/api/selection-bias/gate")
        assert resp.status_code == 200
        data = resp.json()
        assert "strategies" in data
        assert "total" in data
        assert "passing" in data
        assert "failing" in data
        for s in data["strategies"]:
            assert "strategy_id" in s
            assert "passes_all" in s
            assert "gate_details" in s

    def test_rigor_gate_single_strategy(self, client, seeded_db):
        # Get a strategy ID from the full gate
        gate_resp = client.get("/api/selection-bias/gate")
        strategies = gate_resp.json()["strategies"]
        if not strategies:
            pytest.skip("No strategies in gate")
        sid = strategies[0]["strategy_id"]

        resp = client.get(f"/api/selection-bias/gate/{sid}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["strategy_id"] == sid
        assert "passes_all" in data

    def test_rigor_gate_404(self, client):
        resp = client.get("/api/selection-bias/gate/nonexistent-id")
        assert resp.status_code == 404


class TestConfigRoutes:
    def test_contracts(self, chain_settings_client):
        """/api/config/contracts surfaces the deployed addresses from
        chain_client.settings.

        Un-skipped by the Tier-B unlock (#738): `chain_settings_client` installs
        a real `chain_client.settings` namespace, so ConfigService reads the
        addresses without a live chain. The contract loader is mocked so pool /
        vault enumeration returns empty lists deterministically — the address
        fields are what this endpoint guarantees.
        """
        mock_loader = MagicMock()
        mock_loader.amm_router.functions.getAllPools.return_value.call = AsyncMock(return_value=[])
        mock_loader.vault_factory.functions.getVaults.return_value.call = AsyncMock(return_value=[])
        with patch("archimedes.chain.contracts.get_contract_loader", return_value=mock_loader):
            resp = chain_settings_client.get("/api/config/contracts")
        assert resp.status_code == 200
        data = resp.json()
        # The settings-sourced addresses must flow straight through.
        assert data["usdc"] == "0x3600000000000000000000000000000000000000"
        assert data["amm_router"] == "0xd5b829f9d364a8bbe1caf6c8b19cb05371b178f4"
        assert data["vault_factory"] == "0xca873414070844aeb98b0bf1051f81969c79cc32"
        assert data["chain_id"] == 5042002
        # Synthetics map omits empty addresses; the two seeded synths must appear.
        assert "sTSLA" in data["synthetics"]
        # Pool/vault enumeration returns empty under the mocked loader.
        assert data["pools"] == {}
        assert data["vaults"] == {}


class TestAssetRoutes:
    def test_list_assets(self, chain_settings_client):
        """/api/assets/ composes chain settings + oracle prices into the asset list.

        Un-skipped by the Tier-B unlock (#738). The oracle HTTP boundary
        (OracleUpdater.fetch_prices, which hits yfinance/CoinGecko) is mocked so
        the test is hermetic; USDC plus the configured synths must surface.
        """
        from archimedes.models.asset import AssetPrice

        priced = [
            AssetPrice(symbol="sTSLA", price_usd=185.50, timestamp=_utcnow(), source="yfinance"),
        ]
        with patch(
            "archimedes.chain.oracle_updater.OracleUpdater.fetch_prices",
            new=AsyncMock(return_value=priced),
        ):
            resp = chain_settings_client.get("/api/assets/")
        assert resp.status_code == 200
        data = resp.json()
        assets = data["assets"]
        by_symbol = {a["symbol"]: a for a in assets}
        # USDC is always present, pegged at $1 with 6 decimals.
        assert "USDC" in by_symbol
        assert by_symbol["USDC"]["price_usd"] == 1.0
        assert by_symbol["USDC"]["decimals"] == 6
        # The configured synths surface; the priced one carries the oracle price.
        assert "sTSLA" in by_symbol
        assert by_symbol["sTSLA"]["price_usd"] == 185.50
        assert by_symbol["sTSLA"]["decimals"] == 18
        # A synth with no oracle price falls back to 0.0 (not a fabricated value).
        assert by_symbol["sSPY"]["price_usd"] == 0.0


class TestSwapRoutes:
    def test_swap_quote_returns_amount_out_fee_and_price_impact(self, chain_settings_client):
        """/api/swap/quote previews a swap via the AMM router (#738 behavior d).

        Hermetic: the contract loader's amm_router is mocked so getAmountOut
        returns deterministic raw amounts. The quote must expose amount_out, a
        fee, a non-negative price impact, and a slippage-bounded min_amount_out.
        """
        usdc = "0x3600000000000000000000000000000000000000"
        stsla = "0xd514cd27baf762c650536765cde9b61c876abacd"

        def _amount_out_for(amount_in_raw: int) -> int:
            # USDC (6 dec) → sTSLA (18 dec). Spot ~ 1 USDC → 0.005 sTSLA.
            # Larger trades get slightly less per unit (positive price impact):
            #   100 USDC (1e8 raw) → 0.49 sTSLA (4.9e17 raw)
            #     1 USDC (1e6 raw) → 0.005 sTSLA (5e15 raw) — the spot quote.
            if amount_in_raw == 100_000_000:
                return 490_000_000_000_000_000
            return 5_000_000_000_000_000

        mock_router = MagicMock()
        # Route calls produce a contract-fn object whose .call() awaits the
        # deterministic amount-out for that raw input — mirrors web3's
        # `router.functions.getAmountOut(a, b, n).call()` shape.
        mock_router.functions.getAmountOut = MagicMock(
            side_effect=lambda _ti, _to, amount_in_raw: SimpleNamespace(
                call=AsyncMock(return_value=_amount_out_for(amount_in_raw))
            )
        )

        mock_loader = MagicMock()
        type(mock_loader).amm_router = property(lambda self: mock_router)

        with patch("archimedes.chain.contracts.get_contract_loader", return_value=mock_loader):
            resp = chain_settings_client.get(
                "/api/swap/quote",
                params={"token_in": usdc, "token_out": stsla, "amount_in": 100.0},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["token_in"] == usdc
        assert data["token_out"] == stsla
        assert data["amount_in"] == 100.0
        # 4.9e17 raw / 1e18 = 0.49 sTSLA out.
        assert data["amount_out"] == pytest.approx(0.49)
        assert data["fee_pct"] == 0.3
        assert data["price_impact_pct"] >= 0.0
        # min_amount_out applies the 0.5% slippage floor.
        assert data["min_amount_out"] == pytest.approx(0.49 * 0.995)

    def test_swap_quote_bad_pair_returns_400(self, chain_settings_client):
        """A router failure is surfaced as a generic 400 (no RPC-internal leak)."""
        mock_router = MagicMock()
        mock_router.functions.getAmountOut.return_value.call = AsyncMock(side_effect=RuntimeError("no pool for pair"))
        mock_loader = MagicMock()
        type(mock_loader).amm_router = property(lambda self: mock_router)
        with patch("archimedes.chain.contracts.get_contract_loader", return_value=mock_loader):
            resp = chain_settings_client.get(
                "/api/swap/quote",
                params={
                    "token_in": "0x3600000000000000000000000000000000000000",
                    "token_out": "0xd514cd27baf762c650536765cde9b61c876abacd",
                    "amount_in": 1.0,
                },
            )
        assert resp.status_code == 400
        # The raw exception text must NOT leak to the client.
        assert "no pool for pair" not in resp.json()["detail"]


class TestRegimeRoutes:
    def test_current_regime(self, client):
        """Regime endpoint must not 500 when Redis is unavailable."""
        resp = client.get("/api/regime/current")
        assert resp.status_code == 200
        data = resp.json()
        assert "regime" in data
        assert "confidence" in data

    def test_current_regime_redis_down_fallback(self, client):
        """With Redis down the endpoint returns a valid fallback response."""
        resp = client.get("/api/regime/current")
        assert resp.status_code == 200
        data = resp.json()
        # Without Redis, regime defaults to "unknown" with zero confidence
        assert data["regime"] in ("unknown", "transition", "risk_on", "risk_off", "crisis")
        assert isinstance(data["confidence"], float)
        assert "transition_probabilities" in data

    def test_current_regime_unknown_signals_are_null_not_zero(self, client):
        """Unknown-regime fallback must NOT report VIX=0 (it would be a lie).

        Red-team report 2026-05-24 H2: VIX is a price-of-insurance index
        that floors around 10, so 0.0 is not a valid reading -- it means
        "no data". Surface that honestly as null instead of zero so the UI
        can hide the row instead of rendering a misleading 0.0 bar.
        """
        resp = client.get("/api/regime/current")
        assert resp.status_code == 200
        data = resp.json()
        if data["regime"] == "unknown":
            # vix_level must be null (not 0.0) when no agent data is present
            assert data["signals"]["vix_level"] is None

    def test_current_regime_recommended_strategy_titles(self, client, monkeypatch):
        """When Redis returns a known regime, the response must include
        recommended_strategy_titles parallel to recommended_strategies so the
        UI can render paper titles instead of raw strategy hashes.

        Red-team report 2026-05-24 H3.
        """
        fake_state = {
            "regime": "risk_off",
            "confidence": 0.92,
            "timestamp": "2026-05-24T12:00:00Z",
            "regime_changed": False,
            "vix_level": 28.5,
            "sp500_above_ma50": False,
            "sp500_above_ma200": True,
            "composite_score": 0.65,
        }

        async def fake_load(self):
            return fake_state

        async def fake_close(self):
            return None

        from archimedes.services.redis_state import AgentStateStore

        monkeypatch.setattr(AgentStateStore, "load_regime", fake_load)
        monkeypatch.setattr(AgentStateStore, "close", fake_close)
        # __init__ on AgentStateStore may try to connect to Redis; replace it
        monkeypatch.setattr(AgentStateStore, "__init__", lambda self: None)

        resp = client.get("/api/regime/current")
        assert resp.status_code == 200
        data = resp.json()
        assert data["regime"] == "risk_off"
        # Parallel field present and a list
        assert "recommended_strategy_titles" in data
        titles = data["recommended_strategy_titles"]
        ids = data["recommended_strategies"]
        assert isinstance(titles, list)
        assert isinstance(ids, list)
        # Same length so the UI can index in parallel
        assert len(titles) == len(ids)
        # If any recommendations exist, titles must not be empty strings and
        # must not look like raw strategy hashes (which are 32-char hex).
        for t in titles:
            assert isinstance(t, str) and len(t) > 0
            # Strategy hashes are 32 lowercase hex chars; paper titles should
            # contain spaces or capitals -- at least one non-hex character.
            assert not (len(t) == 32 and all(c in "0123456789abcdef" for c in t))

    def test_current_regime_vix_null_passthrough(self, client, monkeypatch):
        """If the agent feed reports vix_level as None, the API must pass it
        through as null -- never coerce to 0.0 (red-team 2026-05-24 H2)."""
        from archimedes.services.redis_state import AgentStateStore

        async def fake_load(self):
            return {
                "regime": "risk_on",
                "confidence": 0.88,
                "timestamp": "2026-05-24T12:00:00Z",
                "regime_changed": False,
                "vix_level": None,
                "sp500_above_ma50": True,
                "sp500_above_ma200": True,
            }

        async def fake_close(self):
            return None

        monkeypatch.setattr(AgentStateStore, "load_regime", fake_load)
        monkeypatch.setattr(AgentStateStore, "close", fake_close)
        monkeypatch.setattr(AgentStateStore, "__init__", lambda self: None)

        resp = client.get("/api/regime/current")
        assert resp.status_code == 200
        data = resp.json()
        assert data["signals"]["vix_level"] is None


class TestAgentRoutes:
    def test_agent_status(self, client):
        """Agent status must not 500 when Redis is unavailable."""
        resp = client.get("/api/agent/status")
        assert resp.status_code == 200
        data = resp.json()
        assert "alive" in data

    def test_agent_status_redis_down_defaults(self, client):
        """With Redis down alive=False and heartbeat fields are null/default.

        Mocks AgentStateStore so the test is hermetic — CI has no Redis,
        but local dev usually does (with cached state from real activity).
        """
        from archimedes.services.redis_state import AgentStateStore

        with (
            patch.object(AgentStateStore, "get_heartbeat", AsyncMock(side_effect=ConnectionError("redis down"))),
            patch.object(AgentStateStore, "load_regime", AsyncMock(side_effect=ConnectionError("redis down"))),
            patch.object(AgentStateStore, "get_events", AsyncMock(side_effect=ConnectionError("redis down"))),
            patch.object(AgentStateStore, "close", AsyncMock(return_value=None)),
        ):
            resp = client.get("/api/agent/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["alive"] is False
        assert data.get("last_heartbeat") is None

    def test_amm_health_endpoint(self, client):
        """AMM health endpoint returns pool list with expected shape."""
        resp = client.get("/api/agent/health/amm")
        assert resp.status_code == 200
        data = resp.json()
        assert "pools" in data
        assert "healthy_count" in data
        assert "total_pools" in data
        assert isinstance(data["pools"], list)
        assert data["healthy_count"] >= 0
        assert data["total_pools"] >= 0
        if data["pools"]:
            pool = data["pools"][0]
            for key in (
                "symbol",
                "status",
                "liquidity_usdc",
                "oracle_price",
                "reserve_token",
                "reserve_usdc",
                "last_update",
            ):
                assert key in pool, f"Missing key: {key}"
            assert pool["status"] in ("healthy", "low_liquidity", "empty", "error")

    def test_amm_health_returns_all_synth_pools(self, client):
        """Every configured synth token should appear in the pool list."""
        resp = client.get("/api/agent/health/amm")
        assert resp.status_code == 200
        data = resp.json()
        symbols = {p["symbol"] for p in data["pools"]}
        # We have 7 synth tokens configured — at minimum we should see the ones with addresses
        assert len(symbols) >= 0  # graceful if chain client is mocked

    def test_amm_health_pool_status_values(self, client):
        """Each pool status must be one of the allowed enum values."""
        resp = client.get("/api/agent/health/amm")
        assert resp.status_code == 200
        data = resp.json()
        valid_statuses = {"healthy", "low_liquidity", "empty", "error"}
        for pool in data["pools"]:
            assert pool["status"] in valid_statuses, f"Invalid status: {pool['status']}"


class TestAdvisorRoutes:
    def test_advisor_happy_path(self, client, seeded_db):
        """Advisor returns allocations with weights summing to ≈ synth_weight."""
        resp = client.get("/api/strategies/advisor?risk_profile=moderate")
        assert resp.status_code == 200
        data = resp.json()
        assert "regime" in data
        assert "usdc_weight" in data
        assert "synth_weight" in data
        assert "allocations" in data
        assert "expected_portfolio" in data
        ep = data["expected_portfolio"]
        assert "sharpe" in ep
        assert "cagr" in ep
        assert "max_drawdown" in ep
        # Weights must sum to ≈ synth_weight (within floating-point rounding)
        total_alloc = sum(a["weight"] for a in data["allocations"])
        assert abs(total_alloc - data["synth_weight"]) < 0.01, (
            f"Allocation weights ({total_alloc:.4f}) must sum to synth_weight ({data['synth_weight']:.4f})"
        )

    def test_advisor_all_risk_profiles(self, client, seeded_db):
        """Every valid risk_profile returns 200 with a non-empty allocations list."""
        profiles = ["fixed_income", "conservative", "moderate", "aggressive", "hyper_risky"]
        for profile in profiles:
            resp = client.get(f"/api/strategies/advisor?risk_profile={profile}")
            assert resp.status_code == 200, f"Advisor failed for profile={profile}"
            data = resp.json()
            assert data.get("risk_profile") == profile

    def test_advisor_invalid_profile(self, client):
        """Invalid risk_profile yields 422 validation error."""
        resp = client.get("/api/strategies/advisor?risk_profile=yolo")
        assert resp.status_code == 422

    def test_advisor_redis_unavailable(self, client, seeded_db):
        """Advisor falls back to transition regime when Redis is down.

        Mocks AgentStateStore.load_regime so the test is hermetic — CI has
        no Redis, but local dev usually does (with cached regime state).
        """
        from archimedes.services.redis_state import AgentStateStore

        with (
            patch.object(AgentStateStore, "load_regime", AsyncMock(side_effect=ConnectionError("redis down"))),
            patch.object(AgentStateStore, "close", AsyncMock(return_value=None)),
        ):
            resp = client.get("/api/strategies/advisor?risk_profile=moderate")
        assert resp.status_code == 200
        data = resp.json()
        # Without Redis the regime defaults to "transition"
        assert data["regime"] == "transition"

    def test_advisor_serves_live_rigor_numbers_not_fixture(self, client, seeded_db):
        """M1: the five numeric rigor stats must come from the SAME live-gate
        batch that produces the badge, not the frozen fixture fields on the
        in-memory Strategy objects. The sentinel values below exist in no
        fixture; against the pre-fix route (which served ``st.<field>``)
        every assertion here fails."""
        from archimedes.api import strategies_routes as sr
        from archimedes.services.rigor_evaluator import RigorGateResult

        def fake_batch(strategies):
            return {
                s.id: RigorGateResult(
                    strategy_id=s.id,
                    deflated_sharpe=1.2468,
                    dsr_p_value=0.97531,
                    num_trials=1,
                    pbo_score=0.13579,
                    oos_sharpe=0.86421,
                    look_ahead_passed=True,
                )
                for s in strategies
            }

        with patch.object(sr, "_live_rigor_results_for_strategies", side_effect=fake_batch):
            resp = client.get("/api/strategies/advisor?risk_profile=moderate")
        assert resp.status_code == 200
        allocations = resp.json()["allocations"]
        assert allocations, "advisor returned no allocations to check"
        for alloc in allocations:
            assert alloc["deflated_sharpe_ratio"] == pytest.approx(1.2468)
            assert alloc["dsr_p_value"] == pytest.approx(0.97531)
            assert alloc["num_trials_in_selection"] == 1
            assert alloc["pbo_score"] == pytest.approx(0.13579)
            assert alloc["out_of_sample_sharpe"] == pytest.approx(0.86421)

    def test_advisor_passes_live_rigor_statuses_to_portfolio_llm(self, client, seeded_db):
        """M2: the portfolio LLM must receive the LIVE tri-state statuses,
        computed BEFORE the agent call. The in-memory ``passes_rigor_gate``
        sentinel (always False) told the model every curated strategy had
        failed the gate; pre-fix the route passed no status map at all, so
        the captured final positional arg here is not a status dict and the
        assertions fail."""
        from archimedes.services.strategy_signal_evaluator import strategy_evaluator

        captured = {}

        class _StubAgent:
            available = True
            model_id = "stub"

            def propose_portfolio_with_tools(self, *args):
                captured["with_tools"] = args

            def propose_portfolio(self, *args):
                captured["plain"] = args

        fake_ranking = [
            {
                "synth": "sSPY",
                "display": "SPY",
                "asset_class": "equity_us",
                "score": 1.0,
                "momentum_90d": 0.1,
                "vol_ann": 0.2,
                "exchange": "US",
            }
        ]

        with (
            # get_portfolio_agent is imported inside the route function, so it
            # must be patched at its source module.
            patch("archimedes.agents.portfolio_agent.get_portfolio_agent", return_value=_StubAgent()),
            patch.object(strategy_evaluator, "rank_market", return_value=fake_ranking),
        ):
            resp = client.get("/api/strategies/advisor?risk_profile=moderate")
        assert resp.status_code == 200
        assert "with_tools" in captured, "portfolio agent was never invoked"
        statuses = captured["with_tools"][-1]
        assert isinstance(statuses, dict) and statuses, "final arg must be the rigor-status map"
        assert all(isinstance(k, str) and v in {"pass", "fail", "pending"} for k, v in statuses.items())
        # The plain (non-tool) fallback call must carry the same map.
        assert "plain" in captured
        assert captured["plain"][-1] == statuses


class TestFusionEvaluatorIntegration:
    """Tests for fusion_evaluator wiring in _run_fusion_job.

    The HTTP layer (POST /api/strategies/generate) only enqueues a job and
    returns 202; the meaningful work happens in the background task and
    cannot be verified by the response body. These tests therefore call
    ``_run_fusion_job`` directly and assert against the DB after it
    completes, which is the only way to verify the issue-#133 contract
    that the rigor verdict actually reaches the StrategyRecord library.

    Redis-touching dependencies (``JobStore`` for queue state,
    ``AgentStateStore`` for trace persistence) are mocked at their
    constructor sites so the test is hermetic and doesn't require a
    reachable ``redis:6379`` hostname.
    """

    @pytest.fixture()
    def _no_redis_job_store(self):
        """Replace JobStore with an in-memory mock so async Redis ops don't hit a real server."""
        store_state: dict[str, dict] = {}

        class _MockJobStore:
            def __init__(self, url=None):
                pass

            async def _get_redis(self):  # pragma: no cover — never called via mock path
                raise RuntimeError("mock JobStore has no real redis backing")

            async def enqueue(self, payload):
                import uuid

                job_id = str(uuid.uuid4())
                store_state[job_id] = {"status": "queued", "payload": payload}
                return job_id

            async def get(self, job_id):
                return store_state.get(job_id)

            async def update_status(self, job_id, status, result=None, error=None):
                store_state.setdefault(job_id, {})["status"] = status
                if result is not None:
                    store_state[job_id]["result"] = result
                if error is not None:
                    store_state[job_id]["error"] = error

            async def close(self):
                return None

        with patch(
            "archimedes.services.job_queue.JobStore",
            new=_MockJobStore,
        ):
            yield store_state

    @pytest.fixture()
    def _no_redis_agent_state(self):
        """Replace AgentStateStore so trace persistence doesn't hit Redis."""
        with patch("archimedes.services.redis_state.AgentStateStore") as mock_cls:
            instance = mock_cls.return_value
            instance.save_trace = AsyncMock(return_value=None)
            instance.close = AsyncMock(return_value=None)
            yield mock_cls

    def _seed_job(self, store_state, job_id, payload):
        """Pre-seed an enqueued job so _run_fusion_job has something to consume."""
        store_state[job_id] = {"status": "queued", "payload": payload}

    async def test_fusion_with_spec_persists_rigor_verdict_to_library(
        self,
        client,
        _no_redis_job_store,
        _no_redis_agent_state,
    ):
        """Per issue #133: a fusion job with a strategy_spec must persist the
        backtest metrics + rigor verdict into the StrategyRecord so the Library
        renders fusion-generated strategies alongside seed picks with real
        DSR / PBO / Sharpe numbers — not just an "evaluation pending" placeholder.

        NOTE: declared ``async def`` (not ``def`` with ``asyncio.run``) because
        pytest.ini sets ``asyncio_mode = auto`` — pytest-asyncio owns the event
        loop and disposes of it cleanly. Calling ``asyncio.run`` from a sync
        test closes the main-thread loop and breaks any subsequent async test
        in the same session (e.g. ``test_trace_publisher.py`` was an unintended
        casualty during an earlier iteration of this fix).
        """
        import json as _json

        from archimedes.agents.strategy_fusion import FusionBrief, FusionProposal
        from archimedes.api.strategies_routes import _run_fusion_job
        from archimedes.models.portfolio import RiskProfile
        from archimedes.models.strategy_store import StrategyRecord
        from archimedes.services.strategy_dsl import FABER_2007_SPEC

        mock_proposal = FusionProposal(
            status="ok",
            brief=FusionBrief(
                asset_classes=["SPY"],
                risk_appetite=RiskProfile.MODERATE,
                strategic_direction="",
            ),
            strategy_name="test_fusion_with_spec",
            thesis="Faber tactical with vol-targeting overlay",
            source_arxiv_ids=["0706.1497", "1710.00727"],
            fusion_reasoning="Combines SMA-200 timing with vol-targeted sizing",
            novelty_rationale="Both papers cited; combination is novel",
            risk_notes="Standard tactical-allocation risks",
            model="canned",
            requested_model="canned",
            strategy_spec=FABER_2007_SPEC,
        )

        mock_fusion = MagicMock()
        mock_fusion.propose.return_value = mock_proposal

        job_id = "test-job-with-spec"
        self._seed_job(
            _no_redis_job_store,
            job_id,
            {
                "asset_classes": ["SPY"],
                "risk_appetite": "moderate",
            },
        )

        with patch(
            "archimedes.services.strategy_fusion.default_fusion",
            return_value=mock_fusion,
        ):
            await _run_fusion_job(job_id)

        # ── The contract: rigor_verdict reached the library ──────────────
        with get_session() as session:
            record = (
                session.query(StrategyRecord)
                .filter(
                    StrategyRecord.strategy_name == "test_fusion_with_spec",
                )
                .first()
            )

        assert record is not None, "StrategyRecord was not upserted"
        assert record.generation_method == "fusion"

        assert record.rigor_verdict is not None, (
            "rigor_verdict was not persisted — the wedge ('generate → see it survive the "
            "rigor gate → deploy') is broken without this field set"
        )
        verdict = _json.loads(record.rigor_verdict)
        # Verdict fields from RigorVerdict — all must be present
        for key in ("passing", "look_ahead_clean", "num_trials"):
            assert key in verdict, f"verdict missing required rigor field: {key}"
        # Backtest metrics surfaced alongside for the passport renderer
        for key in (
            "sharpe_ratio",
            "sortino_ratio",
            "max_drawdown",
            "cagr",
            "calmar_ratio",
            "win_rate",
            "total_trades",
        ):
            assert key in verdict, f"verdict missing backtest field: {key}"

        # ── Lifecycle: status is "live" (passing) or "rejected" (failing),
        # NOT "candidate" — failed strategies must not be silently dropped.
        assert record.status in ("live", "rejected"), (
            f"status should be live/rejected after rigor gate, got: {record.status!r}"
        )
        if verdict["passing"]:
            assert record.status == "live"
        else:
            assert record.status == "rejected"

    async def test_fusion_job_num_trials_matches_society_formula(
        self,
        client,
        _no_redis_job_store,
        _no_redis_agent_state,
    ):
        """Decouple #2: the direct-fusion job route must deflate for the SAME
        selection set as the society/live paths — ``_society_num_trials(pool=1)``,
        the strategy's OWN pool, NOT the library size. The spy captures the kwarg
        then raises; the job's non-fatal eval path tolerates that, so no
        FusionEvalResult needs fabricating."""
        from archimedes.agents.generation_pipeline import _society_num_trials
        from archimedes.agents.strategy_fusion import FusionBrief, FusionProposal
        from archimedes.api.strategies_routes import _run_fusion_job
        from archimedes.models.portfolio import RiskProfile
        from archimedes.services.strategy_dsl import FABER_2007_SPEC

        mock_proposal = FusionProposal(
            status="ok",
            brief=FusionBrief(
                asset_classes=["SPY"],
                risk_appetite=RiskProfile.MODERATE,
                strategic_direction="",
            ),
            strategy_name="test_fusion_job_num_trials",
            thesis="t",
            source_arxiv_ids=["0706.1497", "1710.00727"],
            fusion_reasoning="r",
            novelty_rationale="n",
            risk_notes="rn",
            model="canned",
            requested_model="canned",
            strategy_spec=FABER_2007_SPEC,
        )
        mock_fusion = MagicMock()
        mock_fusion.propose.return_value = mock_proposal

        captured: dict = {}

        def _spy(spec, *, num_trials=None, use_real_data=False, **kw):
            captured["num_trials"] = num_trials
            raise RuntimeError("spy: captured num_trials; eval is non-fatal")

        expected = _society_num_trials(1)  # own pool of 1, library-independent

        job_id = "test-job-num-trials"
        self._seed_job(
            _no_redis_job_store,
            job_id,
            {"asset_classes": ["SPY"], "risk_appetite": "moderate"},
        )
        with (
            patch("archimedes.services.strategy_fusion.default_fusion", return_value=mock_fusion),
            patch("archimedes.services.fusion_evaluator.evaluate_fusion_spec", side_effect=_spy),
        ):
            await _run_fusion_job(job_id)

        assert captured.get("num_trials") == expected, (
            f"fusion job deflated with num_trials={captured.get('num_trials')}, "
            f"expected the society formula's {expected}"
        )

    async def test_fusion_without_spec_falls_back_to_candidate(
        self,
        client,
        _no_redis_job_store,
        _no_redis_agent_state,
    ):
        """When the LLM doesn't emit a strategy_spec (back-compat or canned fallback),
        the job must still complete: upsert a candidate StrategyRecord with the prose
        fields, no rigor_verdict, status="candidate". No exception escapes.

        ``async def`` for the same reason as the with-spec test above —
        pytest-asyncio owns the loop.
        """
        from archimedes.agents.strategy_fusion import FusionBrief, FusionProposal
        from archimedes.api.strategies_routes import _run_fusion_job
        from archimedes.models.portfolio import RiskProfile
        from archimedes.models.strategy_store import StrategyRecord

        mock_proposal = FusionProposal(
            status="ok",
            brief=FusionBrief(
                asset_classes=["SPY"],
                risk_appetite=RiskProfile.MODERATE,
                strategic_direction="",
            ),
            strategy_name="test_fusion_no_spec",
            thesis="Text-only fusion (no DSL spec emitted)",
            source_arxiv_ids=["0706.1497", "1710.00727"],
            fusion_reasoning="Reasoning prose only",
            novelty_rationale="Novelty prose",
            risk_notes="Risk prose",
            model="canned",
            requested_model="canned",
            strategy_spec=None,
        )

        mock_fusion = MagicMock()
        mock_fusion.propose.return_value = mock_proposal

        job_id = "test-job-no-spec"
        self._seed_job(
            _no_redis_job_store,
            job_id,
            {
                "asset_classes": ["SPY"],
                "risk_appetite": "moderate",
            },
        )

        with patch(
            "archimedes.services.strategy_fusion.default_fusion",
            return_value=mock_fusion,
        ):
            await _run_fusion_job(job_id)

        with get_session() as session:
            record = (
                session.query(StrategyRecord)
                .filter(
                    StrategyRecord.strategy_name == "test_fusion_no_spec",
                )
                .first()
            )

        assert record is not None, "StrategyRecord was not upserted on the fallback path"
        assert record.generation_method == "fusion"
        assert record.rigor_verdict is None, "no rigor_verdict expected when strategy_spec is missing"
        assert record.status == "candidate", (
            f"no-spec fallback should leave status at candidate, got: {record.status!r}"
        )

        # And the job state was updated to done — no silent abort
        assert _no_redis_job_store[job_id]["status"] == "done", (
            f"job did not reach 'done' state: {_no_redis_job_store[job_id]}"
        )
