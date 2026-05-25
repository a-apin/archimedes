"""Archimedes — FastAPI application entrypoint.

Minimal bootstrap for the hackathon MVP. All routes are wired to
chain services that read/write Arc smart contracts.
"""

import os

# Load .env into os.environ at import time for modules that use os.getenv()
# (circle_signer, oracle_updater) — pydantic ChainSettings handles ARC_ vars itself.
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
from archimedes.api.chat_routes import chat_router
from archimedes.api.corpus_routes import corpus_router
from archimedes.api.explore_routes import explore_router
from archimedes.api.generate_routes import generate_router
from archimedes.api.limiter import limiter
from archimedes.api.marketplace_routes import marketplace_router
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

app = FastAPI(
    title="Archimedes",
    description="Peer-reviewed AI portfolios, settled on Arc.",
    version="0.1.0",
    docs_url=_docs_url,
    openapi_url=_openapi_url,
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
_public_domain = _os.getenv("PUBLIC_DOMAIN", "https://archimedes-arc.app")
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


# Initialize database (creates chat tables if needed)
init_db()


@app.on_event("startup")
async def _startup_populate_rigor_gate():
    """On first startup, compute and persist selection-bias rigor gate fields.

    Idempotent: only populates strategies that don't yet have DSR/PBO values.
    Skips entirely if the backtest_results table is empty.
    """
    import logging

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
    import logging

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
app.include_router(selection_bias_router)
app.include_router(papers_router)
app.include_router(user_router)
app.include_router(proposals_router)


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
        pass

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

    return {
        "status": "ok" if connected else "degraded",
        "service": "archimedes-backend",
        "chain_connected": connected,
        "corpus_papers": len(corpus),
        "corpus_db_count": db_count,
        "corpus_source": corpus_source,
        "corpus_last_intake": corpus_last_intake,
        "artifact_hash": artifact_hash,
        "fusion_enabled": _fusion_on,
        "llm_provider": llm_provider,
        "llm_backend": llm_backend,
        "llm_available": is_available,
        "llm_has_api_key": bool(os.getenv("LLM_API_KEY") or os.getenv("ANTHROPIC_API_KEY")),
        "llm_has_auth_token": bool(os.getenv("LLM_AUTH_TOKEN") or os.getenv("ANTHROPIC_AUTH_TOKEN")),
        "llm_has_base_url": bool(os.getenv("LLM_BASE_URL") or os.getenv("ANTHROPIC_BASE_URL")),
        "paper_rag": paper_rag_status,
        "paper_rag_reason": paper_rag_reason,
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
                # Try to read token addresses and reserves
                token_a = await pool_contract.functions.tokenA().call()
                token_b = await pool_contract.functions.tokenB().call()
                reserve_a = await pool_contract.functions.reserveA().call()
                reserve_b = await pool_contract.functions.reserveB().call()
                pool_info.update(
                    {
                        "token_a": token_a,
                        "token_b": token_b,
                        "reserve_a": reserve_a,
                        "reserve_b": reserve_b,
                    }
                )
            except Exception:
                pool_info["error"] = "failed to read pool state"
            pools.append(pool_info)

        return {
            "status": "ok",
            "pool_count": len(pools),
            "pools": pools,
        }

    except Exception as exc:
        return JSONResponse(
            status_code=503,
            content={
                "status": "amm_health_check_failed",
                "reason": str(exc),
            },
        )


@app.get("/")
@limiter.exempt
async def root():
    return {
        "name": "Archimedes",
        "tagline": "Peer-reviewed AI portfolios, settled on Arc.",
        "docs": _docs_url or "disabled (production)",
    }
