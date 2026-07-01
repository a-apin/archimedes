"""Archimedes — FastAPI application entrypoint.

Minimal bootstrap for the hackathon MVP. All routes are wired to
chain services that read/write Arc smart contracts.
"""

import asyncio
import logging
import os

# Load .env into os.environ at import time for modules that use os.getenv()
# (circle_signer, oracle_updater) — pydantic ChainSettings handles ARC_ vars itself.
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

load_dotenv("../.env", override=True)  # Project root .env first (has real secrets)
load_dotenv(".env", override=False)  # Backend-local .env fills in any missing (no override)

# Load secrets from AWS SSM Parameter Store (production).
# No-op when AWS_SSM_PATH_PREFIX is unset (local dev). Must run BEFORE
# any service imports that read os.environ for API keys / secrets.
from archimedes.services.secrets_service import load_ssm_secrets

load_ssm_secrets()

# Shared rate limiter (Redis-backed, falls back to in-memory).
# Defined in a separate module to avoid circular imports with route modules.
from archimedes.api.auth_siwe import auth_router
from archimedes.api.chat_routes import chat_router
from archimedes.api.corpus_routes import corpus_router
from archimedes.api.explore_routes import explore_router
from archimedes.api.generate_routes import generate_router
from archimedes.api.leaderboard_routes import leaderboard_router
from archimedes.api.limiter import limiter
from archimedes.api.marketplace_routes import marketplace_router

# (the marketplace route registration was removed — hardcoded fees + invented math, Issue #381)
from archimedes.api.metrics_private_routes import metrics_private_router
from archimedes.api.metrics_routes import metrics_router
from archimedes.api.portfolio_routes import portfolio_router
from archimedes.api.proposals_routes import proposals_router
from archimedes.api.risk_routes import risk_router
from archimedes.api.routes import (
    agent_router,
    assets_router,
    config_router,
    papers_router,
    regime_router,
    strategies_router,
    swap_router,
    traces_router,
    vaults_router,
)
from archimedes.api.selection_bias_routes import selection_bias_router
from archimedes.api.user_routes import user_router
from archimedes.db import init_db

logger = logging.getLogger(__name__)

# ── Docs gate: disable /docs and /openapi.json in production ──────────
# Default OFF when PUBLIC_DOMAIN is set (production). Override with
# ENABLE_API_DOCS=1 to re-enable in any environment.
_enable_docs = os.getenv("ENABLE_API_DOCS", "").lower() in ("1", "true", "yes")
_is_production = bool(os.getenv("PUBLIC_DOMAIN"))
if _is_production and not _enable_docs:
    _docs_url = None
    _openapi_url = None
else:
    _docs_url = "/docs"
    _openapi_url = "/openapi.json"

@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncGenerator[None]:
    """FastAPI lifespan context manager — startup before yield, shutdown after."""
    _logger = logging.getLogger("archimedes.startup")

    # ── STARTUP ──────────────────────────────────────────────────────────
    # 1. Populate selection-bias rigor gate fields (idempotent)
    try:
        from archimedes.api.selection_bias_routes import evaluate_rigor_gate

        result = await evaluate_rigor_gate()
        if result.total > 0:
            _logger.info("startup: rigor gate computed — %d/%d passing", result.passing, result.total)
    except Exception as exc:
        _logger.warning("startup: rigor gate population failed (non-fatal): %s", exc)

    # 2. Seed papers table from manifest.jsonl (idempotent)
    try:
        from archimedes.services.corpus_service import seed_from_manifest

        inserted = seed_from_manifest()
        if inserted > 0:
            _logger.info("startup: seeded %d new papers from manifest", inserted)
    except Exception as exc:
        _logger.warning("startup: corpus seed failed (non-fatal): %s", exc)

    # 3. Start the in-process marketplace engine (MarketService)
    from archimedes.marketplace.service import MarketService

    interval = int(os.getenv("AGENT_INTERVAL_SECONDS", "300"))
    dry_run = os.getenv("AGENT_DRY_RUN", "false").lower() in ("1", "true", "yes")
    market = MarketService(interval_seconds=interval, dry_run=dry_run)
    _app.state.market = market
    _logger.info("marketplace engine started (interval=%ds, dry_run=%s)", interval, dry_run)

    # 3a. Rehydrate running publishers from Postgres
    try:
        from archimedes.db import get_session
        from archimedes.models.marketplace import MarketplaceAgent

        with get_session() as session:
            publishers = (
                session.query(MarketplaceAgent)
                .filter(MarketplaceAgent.role == "publisher", MarketplaceAgent.status == "running")
                .all()
            )
        for row in publishers:
            await market.start_publisher(
                strategy_id=row.strategy_id,
                pool_id=row.pool_id,
                vault_address=row.vault_address,
                creator_wallet=row.creator_wallet,
            )
            _logger.info("rehydrated publisher %s (vault=%s)", row.strategy_id, row.vault_address)
    except Exception as exc:
        _logger.warning("startup: publisher rehydration failed (non-fatal): %s", exc)

    yield  # ── app is now running ────────────────────────────────────────

    # ── SHUTDOWN ─────────────────────────────────────────────────────────
    market = getattr(_app.state, "market", None)
    if market is not None:
        market._stop.set()
        for strategy_id in list(market.publishers.keys()):
            await market.stop_publisher(strategy_id)
        _logger.info("marketplace engine stopped")


app = FastAPI(
    title="Archimedes",
    description="Agentic trading, grounded in research — settled on Arc.",
    version="0.1.0",
    docs_url=_docs_url,
    openapi_url=_openapi_url,
    lifespan=lifespan,
)

# Wire rate limiter into the app state
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


# Custom handler returns JSON 429 with rate-limit headers
@app.exception_handler(RateLimitExceeded)
async def rate_limit_handler(request: Request, exc: RateLimitExceeded):  # noqa: ARG001 — FastAPI exception_handler signature requires request
    """Return 429 JSON with X-RateLimit-* headers."""
    response = JSONResponse(
        status_code=429,
        content={"detail": "Rate limit exceeded. Please slow down and try again later."},
    )
    # slowapi populates these headers on the response via the extension point;
    # we ensure they're forwarded even when we override the handler.
    if hasattr(exc, "detail"):
        response.headers["X-RateLimit-Limit"] = str(getattr(exc, "limit", ""))
    return response


# Allow the Next.js frontend to call the API during development
# Production: restricted to PUBLIC_DOMAIN env var (Issue #178).
# Local dev: CORS_ORIGINS env var (defaults to localhost origins).
import os as _os

_cors_env_origins = _os.getenv("CORS_ORIGINS", "http://localhost:3000,http://localhost:80")
_public_domain = _os.getenv("PUBLIC_DOMAIN", "https://archimedes-arc.com")
_cors_origins = [o.strip() for o in _cors_env_origins.split(",") if o.strip()]
# In production (when PUBLIC_DOMAIN is set), restrict to that domain.
# Local dev keeps the CORS_ORIGINS list (localhost).
if _public_domain and _public_domain not in _cors_origins:
    _cors_origins.append(_public_domain)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=[
        "Authorization",
        "Content-Type",
        "X-Wallet-Address",
        "X-Internal-Agent-Key",
        "X-Requested-With",
    ],
    max_age=600,
)

# ── Fail-closed: require EMAIL_ENCRYPTION_KEY in production ──────────
# Without this, services/email_crypto.py falls back to a hardcoded secret
# that anyone with repo access can use to decrypt stored emails.
if _is_production and not os.getenv("EMAIL_ENCRYPTION_KEY"):
    raise RuntimeError(
        "FATAL: EMAIL_ENCRYPTION_KEY must be set when PUBLIC_DOMAIN is configured. "
        'Generate one with: python -c "import secrets; print(secrets.token_urlsafe(32))"'
    )


# ── Request body size limit middleware ────────────────────────────────
_MAX_BODY_BYTES = 1 * 1024 * 1024  # 1 MB


@app.middleware("http")
async def _limit_request_body(request: Request, call_next):
    """Reject request bodies larger than _MAX_BODY_BYTES (1 MB)."""
    content_length = request.headers.get("content-length")
    if content_length and int(content_length) > _MAX_BODY_BYTES:
        return JSONResponse(status_code=413, content={"detail": "Request body too large (max 1 MB)"})
    return await call_next(request)


# ── Telemetry middleware: human-vs-agent traction counter (issue #428) ──
# Registered AFTER _limit_request_body and BEFORE include_router. It classifies
# each request (SIWE session → human; internal-key / bot-UA → agent), increments
# the matching Redis counter, and tags the response with X-Telemetry-Agent.
# Graceful-degrades on any error — telemetry never turns a request into a 5xx.
from archimedes.api.telemetry_middleware import telemetry_middleware

app.middleware("http")(telemetry_middleware)


# ── Visitor-id middleware: anonymous funnel attribution (issue #787) ────
# Registered LAST so it is the OUTERMOST middleware: it guarantees every request
# carries a stable anonymous `archimedes_vid` cookie and exposes it as
# request.state.visitor_id BEFORE any route or downstream middleware runs. This
# is what lets the conversion funnel (landed → generation_started →
# wallet_connected → vault_deployed) join stages for the same visitor. Fail-safe
# — never turns a request into a 5xx.
from archimedes.api.funnel_middleware import ensure_visitor_id_middleware

app.middleware("http")(ensure_visitor_id_middleware)


# Initialize database (creates chat tables if needed)
init_db()


@app.on_event("startup")
async def _startup_populate_rigor_gate():
    """On first startup, compute and persist selection-bias rigor gate fields.

    Idempotent: only populates strategies that don't yet have DSR/PBO values.
    Skips entirely if the backtest_results table is empty.
    """
    _logger = logging.getLogger("archimedes.startup")
    try:
        from archimedes.db import get_session
        from archimedes.models.backtest_store import BacktestResultRecord
        from archimedes.services.strategy_provider import default_provider

        provider = default_provider()
        strategies = provider.list_strategies()
        if not strategies:
            return

        strategy_ids = [s.id for s in strategies]
        with get_session() as session:
            rows = session.query(BacktestResultRecord).filter(BacktestResultRecord.strategy_id.in_(strategy_ids)).all()

            # Check if any need rigor gate computation
            needs_rigor = [r for r in rows if r.deflated_sharpe_ratio is None]
            if not needs_rigor:
                _logger.info("startup: all %d backtest rows have rigor gate fields", len(rows))
                return

        _logger.info("startup: computing rigor gate for %d strategies...", len(needs_rigor))

        # Call the rigor gate endpoint logic (triggers full computation + persist)

        from archimedes.api.selection_bias_routes import evaluate_rigor_gate

        result = await evaluate_rigor_gate()
        _logger.info(
            "startup: rigor gate computed — %d/%d passing",
            result.passing,
            result.total,
        )

        # Refresh provider's backtest cache so /api/strategies serves the new DSR/PBO values
        provider.refresh()
    except Exception as exc:
        _logger.warning("startup: rigor gate population failed (non-fatal): %s", exc)


@app.on_event("startup")
async def _startup_seed_corpus():
    """Seed papers table from manifest.jsonl (idempotent — adds new papers only)."""
    _logger = logging.getLogger("archimedes.startup")
    try:
        from archimedes.services.corpus_service import seed_from_manifest

        inserted = seed_from_manifest()
        if inserted > 0:
            _logger.info("startup: seeded %d new papers from manifest", inserted)
        else:
            _logger.info("startup: corpus seed — no new papers to add")
    except Exception as exc:
        _logger.warning("startup: corpus seed failed (non-fatal): %s", exc)


@app.on_event("startup")
async def _startup_backtest_scheduler():
    """Keep curated backtests fresh IN-APP — no operator-invoked CLI runs.

    The scheduler owns staleness checks + refresh cadence; see
    services/backtest_scheduler.py. Disabled under TESTING and via
    BACKTEST_REFRESH_ENABLED=0. Fail-soft: never blocks startup.
    """
    _logger = logging.getLogger("archimedes.startup")
    try:
        from archimedes.services.backtest_scheduler import backtest_refresh_loop, refresh_enabled

        if refresh_enabled():
            asyncio.create_task(backtest_refresh_loop())
            _logger.info("startup: backtest refresh scheduler armed")
        else:
            _logger.info("startup: backtest refresh scheduler disabled")
    except Exception as exc:
        _logger.warning("startup: backtest scheduler failed to arm (non-fatal): %s", exc)


# Wire all routers
app.include_router(assets_router)
app.include_router(vaults_router)
app.include_router(strategies_router)
app.include_router(traces_router)
app.include_router(regime_router)
app.include_router(swap_router)
app.include_router(config_router)
app.include_router(agent_router)
app.include_router(chat_router)
app.include_router(corpus_router)
app.include_router(explore_router)
app.include_router(generate_router)
app.include_router(marketplace_router)
app.include_router(risk_router)
app.include_router(portfolio_router)
app.include_router(selection_bias_router)
app.include_router(papers_router)
app.include_router(user_router)
app.include_router(auth_router)
app.include_router(proposals_router)
app.include_router(metrics_router)
app.include_router(metrics_private_router)
app.include_router(leaderboard_router)


@app.get("/health")
@app.get("/api/health")
@limiter.exempt
async def health():
    """Health check — used by Docker healthcheck and CI/CD.

    Reports corpus state so silent degradation is visible.
    """

    from archimedes.agents.strategy_fusion import fusion_enabled, load_corpus
    from archimedes.chain.client import chain_client
    from archimedes.services.corpus_service import get_corpus_meta, get_paper_count
    from archimedes.services.llm_backend import make_llm_backend

    connected = await chain_client.is_connected()
    corpus = load_corpus()
    _fusion_on = fusion_enabled()
    backend = make_llm_backend()
    llm_provider = os.getenv("LLM_PROVIDER", "auto")
    is_available = getattr(backend, "available", False)
    llm_backend = "live" if is_available else backend.model_id if hasattr(backend, "model_id") else "unavailable"

    # DB-backed corpus diagnostics
    db_count = 0
    corpus_source = "file"
    corpus_last_intake = None
    artifact_hash = None
    try:
        db_count = get_paper_count()
        meta = get_corpus_meta()
        if meta:
            corpus_source = meta.get("source", "unknown")
            corpus_last_intake = meta.get("last_intake_at")
            artifact_hash = meta.get("artifact_hash")
    except Exception:
        logger.debug("corpus meta read failed", exc_info=True)

    # Paper RAG health (semantic retrieval)
    paper_rag_status = "disabled"
    paper_rag_reason = ""
    try:
        from archimedes.services.paper_rag import paper_rag_health as _prag_health

        _diag = _prag_health()
        paper_rag_status = _diag.status
        paper_rag_reason = _diag.reason
    except Exception:
        paper_rag_reason = "import failed"

    # GMM regime-detector health (T0.5 — loud fallback telemetry).
    # "degraded" => no fitted artifact, rule-based VixRegimeDetector fallback
    # active. Surfaced so rule-based regime calls aren't presented as data-driven.
    regime_detector_status = "unknown"
    regime_detector_reason = ""
    try:
        from archimedes.services.gmm_regime_detector import gmm_regime_health

        _gmm_diag = gmm_regime_health()
        regime_detector_status = _gmm_diag.status
        regime_detector_reason = _gmm_diag.reason
    except Exception:
        regime_detector_reason = "import failed"

    # Risk-analysis data health (T0.5 — loud fallback telemetry).
    # "mock" => no persisted backtest equity curves, so the Risk UI renders
    # placeholder mockReturns. Surfaced so mock tail-risk isn't presented as real.
    risk_data_status = "unknown"
    risk_data_reason = ""
    try:
        from archimedes.api.risk_routes import risk_data_health

        _risk_diag = risk_data_health()
        risk_data_status = _risk_diag.status
        risk_data_reason = _risk_diag.reason
    except Exception:
        risk_data_reason = "import failed"

    # Human-vs-agent traction counts (issue #428). Fail-safe: get_counts
    # returns (0, 0) when Redis is unreachable, so /health never degrades on it.
    # NOTE: these are cumulative per-request tallies (site traffic, NOT users).
    human_count = 0
    agent_count = 0
    try:
        from archimedes.services.telemetry_store import TelemetryStore

        _store = TelemetryStore()
        try:
            human_count, agent_count = await _store.get_counts()
        finally:
            await _store.close()
    except Exception:
        logger.debug("telemetry counts read failed", exc_info=True)

    # Distinct "real users" = wallet rows in user_profiles (issue #830). Surfaced
    # next to the request tallies so no doc/monitor can conflate traffic with
    # users. Fail-safe: returns 0 on any DB error.
    real_users = 0
    try:
        from archimedes.services.user_stats import get_distinct_user_count

        real_users = get_distinct_user_count()
    except Exception:
        logger.debug("real_users count read failed", exc_info=True)

    # Claim-integrity: corpus honesty fields (issue #778).
    # The `corpus_papers` / `corpus_db_count` counts above are *metadata records*
    # seeded from the JSONL manifest into the `papers` table — they do NOT imply
    # the corpus has been embedded, clustered, or graphed. These three fields make
    # the real state machine-readable so no surface can present manifest metadata
    # as "embedded / knowledge-graphed". Each is driven from actual state, never a
    # constant:
    #   corpus_embedded         — live sentence-transformer embeddings active
    #                             (paper_rag == "live"; "degraded" => TF-IDF, NOT embedded)
    #   corpus_kg_built         — at least one KG entity exists (REBEL/SciSpacy output)
    #   corpus_artifact_present — a real KB-pipeline artifact (S3/local manifest) exists
    corpus_embedded = paper_rag_status == "live"
    kg_entity_count = 0
    kg_relation_count = 0
    try:
        from sqlalchemy import func

        from archimedes.db import get_session
        from archimedes.models.kg import KGEntity, KGRelation

        with get_session() as session:
            kg_entity_count = session.query(func.count(KGEntity.id)).scalar() or 0
            kg_relation_count = session.query(func.count(KGRelation.id)).scalar() or 0
    except Exception:
        logger.debug("kg counts read failed", exc_info=True)
    corpus_kg_built = kg_entity_count > 0

    corpus_artifact_present = False
    try:
        from archimedes.services.kb_artifacts import ArtifactNotFound, load_manifest

        try:
            load_manifest()
            corpus_artifact_present = True
        except ArtifactNotFound:
            corpus_artifact_present = False
    except Exception:
        logger.debug("kb artifact probe failed", exc_info=True)

    return {
        "status": "ok" if connected else "degraded",
        "service": "archimedes-backend",
        "chain_connected": connected,
        # human_count / agent_count are cumulative per-request tallies (site
        # traffic, NOT users). real_users is the honest distinct-user count.
        "human_count": human_count,
        "agent_count": agent_count,
        "real_users": real_users,
        "corpus_papers": len(corpus),
        "corpus_db_count": db_count,
        "corpus_source": corpus_source,
        "corpus_last_intake": corpus_last_intake,
        "artifact_hash": artifact_hash,
        # Claim-integrity honesty fields (issue #778). The counts above are
        # manifest-seeded *metadata records*; these say what has actually been
        # built on top of them. New keys only — existing keys are unchanged so
        # current UI/monitoring consumers don't break.
        "corpus_embedded": corpus_embedded,
        "corpus_kg_built": corpus_kg_built,
        "corpus_kg_entities": kg_entity_count,
        "corpus_kg_relations": kg_relation_count,
        "corpus_artifact_present": corpus_artifact_present,
        "fusion_enabled": _fusion_on,
        "llm_provider": llm_provider,
        "llm_backend": llm_backend,
        "llm_model": getattr(backend, "model_id", None),
        "llm_available": is_available,
        "llm_has_api_key": bool(os.getenv("LLM_API_KEY") or os.getenv("ANTHROPIC_API_KEY")),
        "llm_has_auth_token": bool(os.getenv("LLM_AUTH_TOKEN") or os.getenv("ANTHROPIC_AUTH_TOKEN")),
        "llm_has_base_url": bool(os.getenv("LLM_BASE_URL") or os.getenv("ANTHROPIC_BASE_URL")),
        "paper_rag": paper_rag_status,
        "paper_rag_reason": paper_rag_reason,
        "regime_detector": regime_detector_status,
        "regime_detector_reason": regime_detector_reason,
        "risk_data": risk_data_status,
        "risk_data_reason": risk_data_reason,
    }


@app.get("/health/paper-rag")
@limiter.exempt
async def health_paper_rag():
    """Dedicated paper-rag health endpoint."""
    from archimedes.services.paper_rag import paper_rag_health

    diag = paper_rag_health()
    return {
        "paper_rag": diag.status,
        "reason": diag.reason,
    }


@app.get("/health/amm")
@app.get("/api/health/amm")
@limiter.exempt
async def health_amm():
    """AMM pool liquidity health — per-pool status for operator/judge probes.

    Returns 200 with pool list when pools exist, or 503 with an explicit
    status message when they haven't been initialized. Never returns 404.
    """
    from fastapi.responses import JSONResponse

    from archimedes.chain.client import chain_client

    try:
        connected = await chain_client.is_connected()
        if not connected:
            return JSONResponse(
                status_code=503,
                content={"status": "chain_disconnected", "reason": "Cannot reach Arc RPC"},
            )

        from archimedes.chain.contracts import get_contract_loader

        loader = get_contract_loader()
        router = loader.amm_router

        # getAllPools() returns list of pool addresses
        pool_addresses = await router.functions.getAllPools().call()

        if not pool_addresses:
            return JSONResponse(
                status_code=503,
                content={
                    "status": "amm_pools_not_initialized",
                    "reason": "No AMM pools exist yet. Run bootstrap_vaults to create pools.",
                    "pools": [],
                },
            )

        # For each pool, read basic state
        pools = []
        for addr in pool_addresses:
            pool_info = {"address": addr}
            try:
                pool_contract = loader.amm_pool(addr)
                # ABI uses UniswapV2-style names: token0/token1/reserve0/reserve1
                t0 = await pool_contract.functions.token0().call()
                t1 = await pool_contract.functions.token1().call()
                r0 = await pool_contract.functions.reserve0().call()
                r1 = await pool_contract.functions.reserve1().call()
                pool_info.update(
                    {
                        "token0": t0,
                        "token1": t1,
                        "reserve0": r0,
                        "reserve1": r1,
                    }
                )
            except Exception as exc:
                pool_info["error"] = f"failed to read pool state: {type(exc).__name__}: {exc}"
            pools.append(pool_info)

        return {
            "status": "ok",
            "pool_count": len(pools),
            "pools": pools,
        }

    except Exception:
        logging.getLogger(__name__).exception("AMM health check failed")
        return JSONResponse(
            status_code=503,
            content={
                "status": "amm_health_check_failed",
                "reason": "AMM health check failed — see server logs.",
            },
        )


@app.get("/")
@limiter.exempt
async def root():
    return {
        "name": "Archimedes",
        "tagline": "Agentic trading, grounded in research — settled on Arc.",
        "docs": _docs_url or "disabled (production)",
    }
