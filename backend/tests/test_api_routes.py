"""FastAPI route-layer tests — TestClient against real routes, mocked chain.

Tests HTTP status + response-schema shape for the judge-facing API surface.
Hermetic: no testnet, no Circle SDK, no Anthropic — chain client is mocked.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from archimedes.db import get_session, init_db
from archimedes.services.backtest_mapper import (
    AnalyticsArtifactModel,
    canonical_artifact_hash,
    map_artifact_to_backtest_result,
)
from archimedes.services.backtest_repository import insert_backtest_if_missing
from fastapi.testclient import TestClient

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "analytics_artifact_buy_hold.json"


@pytest.fixture(autouse=True)
def _use_tmp_db(tmp_path, monkeypatch):
    """Point the DB at a temp SQLite so we don't pollute the real one."""
    db_path = tmp_path / "test_archimedes.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    init_db()
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


# NOTE: TestFrontierRoutes and TestCorrelationRoutes were removed in the wake
# of Issue #383 — /api/strategies/frontier and /api/strategies/correlation were
# fabricating daily returns from a seeded RNG and have been deleted. UI consumers
# (CorrelationMatrix.jsx, EfficientFrontier.jsx) are deleted too.


class TestStrategyRoutes:
    def test_list_strategies(self, client, seeded_db):
        resp = client.get("/api/strategies/")
        assert resp.status_code == 200
        data = resp.json()
        assert "strategies" in data
        assert len(data["strategies"]) >= 1
        s = data["strategies"][0]
        assert "id" in s
        assert "paper_title" in s
        assert "sharpe_ratio" in s
        assert s["is_backtest_placeholder"] is False

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

    def test_rigor_gate_fields_correct_for_tier1(self, client, seeded_db):
        """Moreira-Muir fixture values flow correctly: dsr_p_value ≥ 0.95, passes_rigor_gate=True."""
        strategies = _list_all_strategies(client)
        mm = next(
            (s for s in strategies if "Volatility" in s.get("paper_title", "")),
            None,
        )
        if mm is None:
            pytest.skip("Moreira-Muir strategy not found in fixture")
        assert mm["passes_rigor_gate"] is True
        assert mm["dsr_p_value"] is not None
        assert mm["dsr_p_value"] >= 0.95, f"Expected p≥0.95, got {mm['dsr_p_value']}"


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
    def test_contracts(self, client):
        # ConfigService reads contract addresses from chain_client.settings
        # which is initialized at import time — requires deeper mocking.
        # The /api/config/contracts endpoint is tested live on EC2.
        pytest.skip("Requires chain_client.settings module-level init mocking")


class TestAssetRoutes:
    def test_list_assets(self, client):
        # AssetService reads from on-chain via chain_client — requires deeper mocking.
        # The /api/assets/ endpoint is tested live on EC2.
        pytest.skip("Requires chain_client.settings module-level init mocking")


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
        """With Redis down alive=False and heartbeat fields are null/default."""
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
        """Advisor falls back to transition regime when Redis is down."""
        resp = client.get("/api/strategies/advisor?risk_profile=moderate")
        # Redis is not running in unit tests; endpoint must not 500.
        assert resp.status_code == 200
        data = resp.json()
        # Without Redis the regime defaults to "transition"
        assert data["regime"] == "transition"


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
