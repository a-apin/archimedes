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
from fastapi import Depends, FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

load_dotenv("../.env", override=True)  # Project root .env first (has real secrets)
load_dotenv(".env", override=False)  # Backend-local .env fills in any missing (no override)

# Load secrets from AWS SSM Parameter Store — PRODUCTION ONLY. Gated on
# PUBLIC_DOMAIN (same "is this production" signal used below for the docs gate
# and the EMAIL_ENCRYPTION_KEY fail-close) so a local run can never pull real
# prod secrets from SSM, even with ambient AWS creds and AWS_SSM_PATH_PREFIX
# resolving to a real path — issue #1044. Belt-and-suspenders with
# AWS_SSM_PATH_PREFIX defaulting blank in .env.example: this gate is what
# actually stops the fetch regardless of what that var resolves to. Must run
# BEFORE any service imports that read os.environ for API keys / secrets.
from archimedes.services.secrets_service import load_ssm_secrets

if os.getenv("PUBLIC_DOMAIN"):
    load_ssm_secrets()

# Shared rate limiter (Redis-backed, falls back to in-memory).
# Defined in a separate module to avoid circular imports with route modules.
from archimedes.api.account_auth import better_auth_session_middleware, require_current_user
from archimedes.api.account_usage_routes import account_usage_router
from archimedes.api.agent_manifest_routes import agent_manifest_router
from archimedes.api.chat_routes import chat_router
from archimedes.api.corpus_routes import corpus_router
from archimedes.api.explore_routes import explore_router
from archimedes.api.features_routes import features_router
from archimedes.api.generate_routes import generate_public_router, generate_router
from archimedes.api.leaderboard_routes import leaderboard_router
from archimedes.api.limiter import limiter
from archimedes.api.paper_routes import paper_router
from archimedes.api.payment_routes import payment_router

# FAIL-SOFT import: marketplace_routes → service → payments imports circlekit
# (the circle-titanoboa-sdk VCS dependency). If that dependency fails to IMPORT
# in the runtime image (installs fine in the builder but a runtime lib/module is
# missing in the slim final stage), the whole backend must NOT crash on boot —
# it degrades to "marketplace routes absent" (404). PR #958 prod incident:
# the first successful deploy crash-looped the backend on exactly this import.
try:
    from archimedes.api.marketplace_routes import marketplace_router
except Exception as _mkt_exc:
    logging.getLogger(__name__).error(
        "marketplace router unavailable — importing it failed (running WITHOUT marketplace): %s",
        _mkt_exc,
    )
    marketplace_router = None

# (the marketplace route registration was removed — hardcoded fees + invented math, Issue #381)
from archimedes.api.metrics_private_routes import metrics_private_router
from archimedes.api.metrics_routes import metrics_router
from archimedes.api.portfolio_routes import portfolio_router
from archimedes.api.proposals_routes import proposals_router
from archimedes.api.rigor_verify_routes import rigor_verify_router
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
from archimedes.api.wallet_routes import wallet_router
from archimedes.db import init_db

logger = logging.getLogger(__name__)


def _assert_marketplace_live_or_dry(marketplace_router_obj: object, payments_dry_run: bool) -> None:
    """FATAL when circlekit failed to import while PAYMENTS_DRY_RUN=false (#1240).

    The import above degrades on purpose (PR #958 prod incident) so a broken
    circlekit install can never crash-loop the whole backend — correct when
    PAYMENTS_DRY_RUN=true, since no real money is at stake and "marketplace
    absent" (routes 404) is an honest, visible degraded state. It stops being
    correct the moment an operator sets PAYMENTS_DRY_RUN=false: that is a
    deliberate signal real charging is wanted, and "the process boots fine,
    most routes 200, marketplace quietly 404s" is a silent trap for exactly
    that operator, not a safe degrade — see architectural-principles.md's
    fail-soft-is-wrong-for-anything-a-claim-depends-on rule. Pulled into its
    own function so the assertion is unit-testable without importing the
    whole app.
    """
    if marketplace_router_obj is None and not payments_dry_run:
        raise RuntimeError(
            "FATAL: circlekit failed to import (marketplace router unavailable, see "
            "the error logged above) while PAYMENTS_DRY_RUN=false. Refusing to boot "
            "with real-money charging intended but no charging capability available. "
            "Fix the circlekit install, or set PAYMENTS_DRY_RUN=true if dry-run is "
            "actually what's intended."
        )


_assert_marketplace_live_or_dry(
    marketplace_router,
    os.getenv("PAYMENTS_DRY_RUN", "true").lower() in ("1", "true", "yes"),
)


class GatewayChainMismatch(RuntimeError):
    """Raised ONLY by _assert_gateway_chain_matches_rpc, and only on a
    confirmed GATEWAY_CHAIN/RPC mismatch or an unresolvable GATEWAY_CHAIN
    name under PAYMENTS_DRY_RUN=false — never on a connectivity failure.

    A dedicated type (rather than a bare RuntimeError) so the lifespan
    startup wrapper can re-raise exactly this failure mode as fatal without
    also catching an unrelated RuntimeError from elsewhere in the same try
    block (e.g. get_chain_id() raising on a closed aiohttp session/event
    loop) — a review finding on #1240: a type-based `except RuntimeError`
    at the call site made ANY RuntimeError fatal, including ones that must
    fall through to the non-fatal "connectivity issue?" warning branch.
    Subclasses RuntimeError so existing `pytest.raises(RuntimeError, ...)`
    tests against this function keep working unchanged.
    """


async def _assert_gateway_chain_matches_rpc(
    chain_client_obj: object, gateway_chain: str, payments_dry_run: bool
) -> None:
    """FATAL when GATEWAY_CHAIN resolves to a chain_id the RPC we actually
    talk to doesn't report — but only when PAYMENTS_DRY_RUN=false (#1240).

    circlekit's ``CHAIN_ALIASES`` maps ``"mainnet"`` -> ``"ethereum"``, so a
    fat-fingered ``GATEWAY_CHAIN`` can silently resolve to a real, DIFFERENT
    chain than the one Arc RPC (and the vault reads/writes) actually target
    — Gateway payments would settle on a chain trades don't execute on, and
    ``GET /api/config/contracts``' "chain" field would be lying about which
    chain we're on. Fatal only under PAYMENTS_DRY_RUN=false (same
    conditioning as ``_assert_marketplace_live_or_dry`` above): a dry-run/
    testnet boot with a stale or experimental GATEWAY_CHAIN value must not
    crash-loop over it, just log loudly.

    A connectivity failure (``chain_client_obj.get_chain_id()`` raising) is
    the CALLER's problem, not this function's — chain_client owns its own
    retry/backoff, and "the RPC was briefly unreachable at boot" is a
    different failure mode than "we are pointed at the wrong chain". This
    function only ever raises ``GatewayChainMismatch`` (never a bare
    ``RuntimeError``) on a confirmed mismatch, or an unresolvable
    GATEWAY_CHAIN name (also a real config bug worth being loud about) — any
    OTHER exception (e.g. get_chain_id() raising for connectivity reasons)
    propagates as whatever type it naturally is, unmodified, precisely so
    the caller can tell the two failure modes apart by type.
    """
    from circlekit.constants import get_chain_config

    try:
        expected_chain_id = get_chain_config(gateway_chain).chain_id
    except ValueError as exc:
        message = f"GATEWAY_CHAIN={gateway_chain!r} is not a chain circlekit recognizes: {exc}"
        if payments_dry_run:
            logger.warning("%s (PAYMENTS_DRY_RUN=true — not fatal, but fix before flipping it)", message)
            return
        raise GatewayChainMismatch(f"FATAL: {message} Refusing to boot with PAYMENTS_DRY_RUN=false.") from exc

    actual_chain_id = await chain_client_obj.get_chain_id()
    if expected_chain_id != actual_chain_id:
        message = (
            f"GATEWAY_CHAIN={gateway_chain!r} resolves to chain_id={expected_chain_id}, but the "
            f"configured RPC reports chain_id={actual_chain_id}. Gateway payments would settle "
            "on a different chain than trades execute on."
        )
        if payments_dry_run:
            logger.warning("%s (PAYMENTS_DRY_RUN=true — not fatal, but fix before flipping it)", message)
            return
        raise GatewayChainMismatch(f"FATAL: {message} Refusing to boot with PAYMENTS_DRY_RUN=false.")


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


class _MarketplaceUnavailable(Exception):
    """Sentinel: the marketplace engine did not start, so rehydration is skipped."""


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncGenerator[None]:
    """FastAPI lifespan context manager — startup before yield, shutdown after."""
    _logger = logging.getLogger("archimedes.startup")

    # ── STARTUP ──────────────────────────────────────────────────────────
    # 1. (removed) Rigor-gate backfill.
    #
    # This used to load every backtest_results row for every curated strategy
    # and then call evaluate_rigor_gate() to backfill DSR/PBO. Both halves were
    # wrong, and it was a primary cause of the 2026-08-19 OOM crash loop:
    #
    #   * The query was `.all()` over full rows. artifact_json and
    #     equity_curve_json are plain Text columns with no deferred loading
    #     (models/backtest_store.py), so every row's JSON blob was materialised
    #     — measured +1079 MB of RSS at 408 rows, strictly linear at ~2.6 MB
    #     per row, and rising daily because nothing dedupes that table.
    #   * Worse, `rows` was a local of THIS function, and lifespan() is an
    #     @asynccontextmanager that suspends at the `yield` below for the entire
    #     process lifetime. Python pins the frame, so those blobs were never
    #     collected — retained garbage for the life of the container, not a
    #     transient spike. `del rows; gc.collect()` in a probe returned 100% of it.
    #   * evaluate_rigor_gate() is a FastAPI route handler whose `strictness`
    #     parameter defaults to Query(DEFAULT_LEVEL). Called directly it stays a
    #     Query object, the whole cohort DSR/PBO computation runs (~14-20 s of
    #     one vCPU), and then StrategyRigorResult validation raises. It had
    #     therefore NEVER once succeeded since 2026-07-05 (commit 4cf8d59b) —
    #     46 days of burning that computation on every boot and discarding it.
    #
    # Nothing is lost by removing it. The rigor gate already runs where it
    # belongs: in the generation pipeline for generated strategies
    # (agents/generation_pipeline.py, via live_rigor_gate.verdict_from_returns),
    # and on demand for the curated library via GET /api/selection-bias/gate —
    # whose _compute() is the only production caller of update_rigor_gate_fields(),
    # i.e. the only thing that ever persisted these columns anyway.

    # 2. Seed papers table from manifest.jsonl (idempotent)
    try:
        from archimedes.services.corpus_service import seed_from_manifest

        inserted = seed_from_manifest()
        if inserted > 0:
            _logger.info("startup: seeded %d new papers from manifest", inserted)
    except Exception as exc:
        _logger.warning("startup: corpus seed failed (non-fatal): %s", exc)

    # 2a. Seed provider examples into strategy_store (idempotent, D1).
    # Each provider example gets a StrategyRecord keyed by its 32-char
    # provider id so the publish route can look it up. content_hash is a
    # domain-separated SHA-256 to avoid collision with real generated hashes.
    try:
        import hashlib
        import json

        from archimedes.db import get_session
        from archimedes.models.strategy_store import StrategyRecord
        from archimedes.services.strategy_provider import default_provider

        provider = default_provider()
        with get_session() as session:
            count = 0
            for s in provider.list_strategies():
                if session.query(StrategyRecord).filter_by(id=s.id).first():
                    continue
                content_hash = "0x" + hashlib.sha256(("example:" + s.id).encode()).hexdigest()
                papers_list = [{"arxiv_id": p.arxiv_id, "title": p.title, "authors": p.authors} for p in s.papers]
                record = StrategyRecord(
                    id=s.id,
                    content_hash=content_hash,
                    is_example=True,
                    generation_method="curated",
                    strategy_name=s.paper_title or s.id,
                    thesis=s.methodology_summary or "",
                    source_papers=json.dumps(papers_list),
                    asset_universe=json.dumps(s.asset_universe or []),
                    risk_profile=(s.risk_profiles[0] if s.risk_profiles else "moderate"),
                    status="live",
                    owner_wallet=None,
                )
                session.add(record)
                count += 1
            if count:
                session.commit()
                _logger.info("startup: seeded %d example strategies into strategy_store", count)
    except Exception as exc:
        _logger.warning("startup: example strategy seed failed (non-fatal): %s", exc)

    # Both money-affecting switches FAIL SAFE (default to dry) and must be
    # turned on together and deliberately. Previously PAYMENTS_DRY_RUN
    # defaulted to "false" while PAPER_TRADING defaulted to "true", so an
    # out-of-the-box deploy mirrored no real trades yet charged real USDC —
    # the worst possible asymmetry. Charging real money now requires an
    # explicit PAYMENTS_DRY_RUN=false.
    #
    # Read BEFORE step 3's try block (not inside it) so it is unconditionally
    # bound going into step 3y below, regardless of whether step 3 itself
    # raises. It used to be assigned inside that try, after the MarketService
    # import and the AGENT_INTERVAL_SECONDS int() parse — either one raising
    # first (e.g. a non-numeric AGENT_INTERVAL_SECONDS) left this name
    # unbound, and step 3y's reference to it then raised a bare NameError
    # that its own `except Exception` swallowed as "connectivity issue?",
    # silently disabling the GATEWAY_CHAIN/RPC mismatch guard (#1240 review).
    payments_dry_run = os.getenv("PAYMENTS_DRY_RUN", "true").lower() in ("1", "true", "yes")

    # 3. Start the in-process marketplace engine (MarketService).
    # FAIL-SOFT: constructing the engine (or importing its deps, e.g. circlekit)
    # must NEVER take down the whole backend — a new subsystem crashing at
    # startup should degrade to "marketplace unavailable" (routes 503), not 502
    # the entire app. market stays None on failure and everything downstream
    # (rehydration, routes via _get_market, shutdown) guards on that.
    market = None
    try:
        from archimedes.marketplace.service import MarketService

        interval = int(os.getenv("AGENT_INTERVAL_SECONDS", "300"))
        paper_trading = os.getenv("PAPER_TRADING", "true").lower() in ("1", "true", "yes")
        market = MarketService(
            interval_seconds=interval, payments_dry_run=payments_dry_run, paper_trading=paper_trading
        )
        _app.state.market = market
        _logger.info(
            "marketplace engine started (interval=%ds, payments_dry_run=%s, paper_trading=%s)",
            interval,
            payments_dry_run,
            paper_trading,
        )
    except Exception as exc:
        _app.state.market = None
        _logger.error("startup: marketplace engine failed to start — running WITHOUT it (non-fatal): %s", exc)

    # 3y. Verify GATEWAY_CHAIN against the RPC we actually talk to (#1240).
    # Gated on marketplace_router (circlekit importable), not `market`
    # (MarketService construction) — generation_payment.py's paywall also
    # resolves GATEWAY_CHAIN independently of the ticking engine, so this
    # matters even when MarketService itself failed to start for some other
    # reason. A connectivity failure (RPC briefly unreachable at boot) is
    # logged and swallowed here — chain_client owns its own retry/backoff,
    # and that is a different failure mode than "pointed at the wrong
    # chain", which is the only thing _assert_gateway_chain_matches_rpc
    # itself treats as fatal (and only when PAYMENTS_DRY_RUN=false).
    if marketplace_router is not None:
        try:
            from archimedes.chain.client import chain_client
            from archimedes.marketplace.config import DEFAULT_GATEWAY_CHAIN

            gateway_chain = os.getenv("GATEWAY_CHAIN", DEFAULT_GATEWAY_CHAIN).strip()
            await _assert_gateway_chain_matches_rpc(chain_client, gateway_chain, payments_dry_run)
            _logger.info("startup: GATEWAY_CHAIN=%s verified against RPC", gateway_chain)
        except GatewayChainMismatch:
            # Re-raise ONLY the dedicated mismatch type, by identity — not
            # "any RuntimeError". A plain RuntimeError from get_chain_id()
            # itself (e.g. a closed aiohttp session/event loop) must fall
            # through to the generic warning branch below instead of aborting
            # boot; catching RuntimeError broadly here used to make it fatal
            # regardless of PAYMENTS_DRY_RUN (#1240 review finding).
            raise
        except Exception as exc:
            _logger.warning("startup: GATEWAY_CHAIN/RPC verification skipped (connectivity issue?): %s", exc)

    # 3z. Register the system/agent addresses in controlled_wallets (issue
    # #1028, D1a/D3). Idempotent upsert — safe to run on every boot.
    #   - The trading-agent signer (chain_client.settings.agent_account): the
    #     SAME key both StrategyRunner (rebalance) and MarketService
    #     (createPool / on-chain settlement) sign with — one row covers both
    #     "agent_runner" and "the marketplace engine" from the issue's scope.
    #   - ARCHIMEDES_TREASURY_WALLET: the platform's revenue-split address
    #     (marketplace_routes.publish_strategy) — never a user identity.
    # Circle DCWs (publisher_settlement / subscriber_payment) are registered
    # per-wallet at provision time (marketplace_routes.py), not here — there's
    # no fixed set of those to enumerate at boot.
    try:
        from archimedes.chain.client import chain_client
        from archimedes.services.identity_events import register_controlled_wallet

        agent_account = chain_client.settings.agent_account
        if agent_account is not None:
            register_controlled_wallet(address=agent_account.address, wallet_class="trading_agent")

        treasury_wallet = os.getenv("ARCHIMEDES_TREASURY_WALLET", "").strip()
        if treasury_wallet:
            register_controlled_wallet(address=treasury_wallet, wallet_class="treasury")
    except Exception as exc:
        _logger.warning("startup: controlled-wallet registration failed (non-fatal): %s", exc)

    # 3a. Rehydrate running publishers from Postgres. Guarded on the engine
    # having started; the surrounding try/except is fail-soft regardless.
    try:
        if market is None:
            raise _MarketplaceUnavailable

        from archimedes.db import get_session
        from archimedes.marketplace.service import Subscriber
        from archimedes.models.marketplace import MarketplaceAgent

        with get_session() as session:
            publishers = (
                session.query(MarketplaceAgent)
                .filter(MarketplaceAgent.role == "publisher", MarketplaceAgent.status == "running")
                .all()
            )
            strategy_ids = [row.strategy_id for row in publishers]
            subscriber_rows = (
                session.query(MarketplaceAgent)
                .filter(
                    MarketplaceAgent.role == "subscriber",
                    MarketplaceAgent.status == "running",
                    MarketplaceAgent.strategy_id.in_(strategy_ids),
                )
                .all()
                if strategy_ids
                else []
            )

        # Group subscriber rows by strategy_id
        subscribers_by_strategy: dict[str, dict[str, Subscriber]] = {}
        for srow in subscriber_rows:
            if not srow.circle_wallet_id:
                _logger.warning(
                    "rehydrate: subscriber %s has NULL circle_wallet_id — marking inactive (fail closed, legacy row)",
                    srow.sub_id,
                )
                subscriber_active = False
            else:
                subscriber_active = not srow.halted
            subscribers_by_strategy.setdefault(srow.strategy_id, {})[srow.sub_id] = Subscriber(
                sub_id=srow.sub_id,
                pool_id=srow.pool_id,
                vault_address=srow.vault_address,
                ephemeral_wallet=srow.ephemeral_wallet,
                subscriber_wallet=srow.subscriber_wallet,
                active=subscriber_active,
                circle_wallet_id=srow.circle_wallet_id or "",
            )

        for row in publishers:
            subs = subscribers_by_strategy.get(row.strategy_id, {})
            if not row.gateway_seller_address:
                _logger.error(
                    "rehydrate: publisher %s has NULL gateway_seller_address — "
                    "skipping (fail closed, legacy row). Set a gateway_seller_address "
                    "on the publisher row before restarting.",
                    row.strategy_id,
                )
                continue
            await market.start_publisher(
                strategy_id=row.strategy_id,
                pool_id=row.pool_id,
                vault_address=row.vault_address,
                creator_wallet=row.creator_wallet,
                gateway_seller_address=row.gateway_seller_address,
                agent_wallet_id=row.agent_wallet_id or "",
                subscribers=subs,
            )
            _logger.info(
                "rehydrated publisher %s (vault=%s, %d subscribers from Postgres)",
                row.strategy_id,
                row.vault_address,
                len(subs),
            )
    except _MarketplaceUnavailable:
        _logger.info("startup: marketplace engine not running — skipping publisher rehydration")
    except Exception as exc:
        _logger.warning("startup: publisher rehydration failed (non-fatal): %s", exc)

    # 4. Arm the in-app backtest refresh scheduler (no operator-invoked CLI
    # runs — see services/backtest_scheduler.py). MUST live in this lifespan:
    # passing a custom lifespan= makes Starlette silently skip any
    # @app.on_event("startup") handlers, so anything not started here does
    # not start at all. Disabled under TESTING / BACKTEST_REFRESH_ENABLED=0;
    # fail-soft: never blocks startup.
    try:
        from archimedes.services.backtest_scheduler import backtest_refresh_loop, refresh_enabled

        if refresh_enabled():
            # Hold a reference (RUF006): a fire-and-forget task can be garbage-
            # collected mid-run. Parked on app.state so it lives for the app's
            # lifetime instead of being reaped once this function returns.
            _app.state.backtest_refresh_task = asyncio.create_task(backtest_refresh_loop())
            _logger.info("startup: backtest refresh scheduler armed")
            # Paper-trading ledgers advance on the same cadence family: one
            # appended bar per deployment per day, replayed on the graded
            # engine (services/paper_trading.py). Same RUF006 reference rule.
            from archimedes.services.paper_trading import paper_advance_loop

            _app.state.paper_advance_task = asyncio.create_task(paper_advance_loop())
            _logger.info("startup: paper-trading advance scheduler armed")
        else:
            _logger.info("startup: backtest refresh scheduler disabled")
    except Exception as exc:
        _logger.warning("startup: backtest scheduler failed to arm (non-fatal): %s", exc)

    # Platform revenue sweep (services/revenue_sweep.py): opt-in Gateway →
    # DCW-token withdrawal loop. Money-switch convention: only the literal
    # REVENUE_SWEEP_ENABLED=true arms it; unset/anything-else stays off and
    # says so. Same RUF006 app.state reference rule as the loops above.
    try:
        from archimedes.services.revenue_sweep import revenue_sweep_loop, sweep_enabled

        if sweep_enabled():
            _app.state.revenue_sweep_task = asyncio.create_task(revenue_sweep_loop())
            _logger.info("startup: revenue sweep scheduler armed")
        else:
            _logger.info("startup: revenue sweep scheduler disabled (REVENUE_SWEEP_ENABLED != 'true')")
    except Exception as exc:
        _logger.warning("startup: revenue sweep failed to arm (non-fatal): %s", exc)

    yield  # ── app is now running ────────────────────────────────────────

    # ── SHUTDOWN ─────────────────────────────────────────────────────────
    market = getattr(_app.state, "market", None)
    if market is not None:
        market._stop.set()
        strategy_ids = list(market.publishers.keys())
        if strategy_ids:
            await asyncio.gather(
                *(market.stop_publisher(sid) for sid in strategy_ids),
            )
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


# ── Timing middleware: per-request duration + slow-request log (issue #1436) ──
# The ALB reports p50 46ms against p99 16.8s, but its metrics are per-target,
# not per-route, so which endpoint owns the tail was not answerable from logs.
# Tags every response with X-Response-Time-Ms and logs the ones over
# SLOW_REQUEST_MS. SSE streams are exempt — they legitimately run 300s+, which
# is the same contamination that forced the latency alarm off p95 on 2026-08-21.
from archimedes.api.timing_middleware import timing_middleware

app.middleware("http")(timing_middleware)


# ── Telemetry middleware: human-vs-agent traction counter (issue #428) ──
# Registered AFTER _limit_request_body and BEFORE include_router. It classifies
# each request (Better Auth account → human; internal-key / bot-UA → agent), increments
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

# Resolve Better Auth once per request. Protected route dependencies consume
# request.state.current_user; public routes keep working when no session exists.
app.middleware("http")(better_auth_session_middleware)


# Initialize database (creates chat tables if needed)
init_db()


# NOTE: startup work lives in lifespan() above. Do NOT add
# @app.on_event("startup") handlers here — with a custom lifespan= passed to
# FastAPI, Starlette silently skips them (verified on fastapi 0.138.1), so
# they would be dead code that LOOKS like it runs.


# Wire all routers
app.include_router(assets_router)
app.include_router(agent_manifest_router)
app.include_router(vaults_router)
app.include_router(strategies_router)
app.include_router(traces_router)
app.include_router(regime_router)
app.include_router(swap_router)
app.include_router(config_router)
app.include_router(agent_router)
app.include_router(chat_router)
app.include_router(corpus_router)
app.include_router(paper_router)
app.include_router(explore_router)
app.include_router(generate_router, dependencies=[Depends(require_current_user)])
app.include_router(generate_public_router)  # /quote only — public by design
if marketplace_router is not None:
    app.include_router(marketplace_router)
app.include_router(risk_router)
app.include_router(portfolio_router, dependencies=[Depends(require_current_user)])
app.include_router(selection_bias_router)
app.include_router(rigor_verify_router)
app.include_router(account_usage_router)
app.include_router(payment_router)
app.include_router(papers_router)
app.include_router(user_router, dependencies=[Depends(require_current_user)])
app.include_router(wallet_router)
# Legacy SIWE router remains test-only so signature-verification regression
# tests exercise its reusable proof helpers without exposing wallet login live.
if os.getenv("TESTING"):
    from archimedes.api.auth_siwe import auth_router

    app.include_router(auth_router)
app.include_router(proposals_router, dependencies=[Depends(require_current_user)])
app.include_router(features_router)
app.include_router(metrics_router)
app.include_router(metrics_private_router)
app.include_router(leaderboard_router)


# ── Liveness responses must never be cached (issue #1520) ──────────────
# /health matched no CloudFront ordered_cache_behavior, so it fell through to
# the default one (infra/cloudfront.tf) whose `html` policy has default_ttl=60.
# With no Cache-Control from here, CloudFront cached it: measured `x-cache: Hit`
# with `age: 17` on the live site, and — worse — cached 504s coming back in
# 0.07s, far too fast to have reached the origin. A cached health check is not a
# health check: it keeps reporting "ok" for up to a minute after the origin
# starts failing, which is the plausible-substitute failure this codebase treats
# as the primary defect class. The CloudFront behaviour is the other half of the
# fix and needs a terraform apply; this half travels with the app, so it holds
# wherever the app is deployed and whoever sits in front of it.
_NO_STORE = "no-store, no-cache, must-revalidate, max-age=0"


def _no_store(response: Response) -> None:
    """Mark a liveness response uncacheable by any intermediary."""
    response.headers["Cache-Control"] = _NO_STORE
    response.headers["Pragma"] = "no-cache"


def _no_store_headers() -> dict[str, str]:
    """Same headers, for handlers that build their own Response object.

    A handler that returns a JSONResponse directly never touches the injected
    ``response``, so it needs these passed in explicitly — the AMM probe returns
    503s that way, and an uncacheable 200 with a cacheable 503 is the worse half
    to get wrong.
    """
    return {"Cache-Control": _NO_STORE, "Pragma": "no-cache"}


@app.get("/health")
@app.get("/api/health")
@limiter.exempt
async def health(response: Response):
    """Health check — used by Docker healthcheck and CI/CD.

    Reports corpus state so silent degradation is visible.
    """
    _no_store(response)

    from archimedes.agents.strategy_fusion import fusion_enabled, load_corpus
    from archimedes.chain.client import chain_client
    from archimedes.services.corpus_service import get_corpus_meta, get_paper_count
    from archimedes.services.llm_backend import make_llm_backend

    connected = await chain_client.is_connected()
    if not connected:
        # N2 (infra #1039): /health's status code is deliberately unchanged (still
        # 200 — see the module docstring above and infra/runbooks) so a transient
        # Arc RPC blip can't cascade the whole ECS service down. But "degraded but
        # HTTP 200" is silent by default, so log loudly here — a CloudWatch Logs
        # metric filter on this exact string (infra/cloudwatch.tf) turns repeated
        # occurrences into a paging alarm without touching the response contract.
        logger.warning("HEALTH_CHAIN_DISCONNECTED: chain_connected=false (Arc RPC unreachable or timed out)")
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

    # Oracle-freshness health (issue #1371 — isFresh()/lastUpdated() had zero
    # backend callers; every deployed PriceOracle has been stale since the
    # T3.2 redeploy with nothing reporting it). oracle_fresh is true only if
    # EVERY probed oracle is fresh; oracle_probed_count/oracle_universe_count
    # are always both present so a 2-of-281-fresh push set can never be read
    # as "oracles are healthy" system-wide. A chain-read failure reports
    # oracle_fresh=false with an explicit marker (never fail-soft "assume
    # fresh") — see services/oracle_health.py's module docstring.
    oracle_fresh = False
    oracle_oldest_age_s: int | None = None
    oracle_probed_count = 0
    oracle_universe_count = 0
    # oracle_reason needs no initializer: the try body and the except handler
    # both assign it unconditionally before any use.
    try:
        from archimedes.services.oracle_health import oracle_health as _oracle_health_probe

        _oracle_diag = await _oracle_health_probe()
        oracle_fresh = _oracle_diag.oracle_fresh
        oracle_oldest_age_s = _oracle_diag.oracle_oldest_age_s
        oracle_probed_count = _oracle_diag.oracle_probed_count
        oracle_universe_count = _oracle_diag.oracle_universe_count
        oracle_reason = _oracle_diag.reason
    except Exception as exc:
        oracle_reason = f"oracle_health probe_error: {exc}"

    if not oracle_fresh:
        # Loud, greppable marker — infra/cloudwatch.tf's metric filter keys off
        # this exact literal (mirrors HEALTH_CHAIN_DISCONNECTED at :545-553).
        # /health's HTTP status stays 200 for the same ALB/ECS reason documented
        # there; this is what makes the degraded state page a human instead of
        # sitting silently in a JSON body nobody is reading.
        logger.warning(
            "HEALTH_ORACLE_STALE: oracle_fresh=false oldest_age_s=%s probed=%d/%d (%s)",
            oracle_oldest_age_s,
            oracle_probed_count,
            oracle_universe_count,
            oracle_reason,
        )

    # Strategy-library presence (issue #1039). count_strategy_files() is a cheap
    # directory file count (NO provider construction → no filesystem refresh, DB
    # backtest load, or unified-table sync side effect — /health is hit by the ALB
    # every 30s). 0 here means the curated library is missing from the image — the
    # exact regression that shipped a strategy-less Fargate build (→ risk_data=mock,
    # empty Explore). CI asserts > 0.
    strategy_count = 0
    try:
        from archimedes.services.strategy_provider import count_strategy_files

        strategy_count = count_strategy_files()
    except Exception:
        logger.debug("strategy count read failed", exc_info=True)

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

    # Distinct real users = canonical Better Auth accounts. Surfaced
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
    #   paper_rerank_model_live — a sentence-transformer object is loaded IN THIS
    #                             PROCESS (paper_rag == "live"). "ready" (weights on
    #                             disk, nothing has retrieved yet) is deliberately not
    #                             live: presence on disk is not proof. Flips to true on
    #                             the first real retrieval, and back to false on restart.
    #   corpus_embedded_at_rest — whether STORED vectors exist, derived from the schema.
    #                             This was previously published as `corpus_embedded` with
    #                             the value of the field above it (#1488): the name
    #                             asserted a property of the corpus while the value
    #                             measured a property of the process, so one retrieval
    #                             made /api/health say the 10k corpus was embedded. It
    #                             was never embedded; retrieval is a keyword filter plus
    #                             a query-time rerank of at most `rerank_candidate_cap`
    #                             candidates, and everything past that cap is appended at
    #                             score 0.0. Both fields ship because the pair is what
    #                             makes the absence legible — a lone false reads as an
    #                             outage, and a lone true reads as the claim we must not
    #                             make. Same reason `corpus_kg_built: false` sits beside
    #                             `corpus_kg_entities: 0`.
    #   corpus_kg_built         — at least one KG entity exists (REBEL/SciSpacy output)
    #   corpus_artifact_present — a real KB-pipeline artifact (S3/local manifest) exists
    paper_rerank_model_live = paper_rag_status == "live"
    # Reporting "not embedded" on a failed read is the only safe direction here:
    # the sole way this field can do harm is by claiming vectors that are absent.
    corpus_embedded_at_rest = False
    corpus_embedded_at_rest_reason = "probe failed"
    rerank_cap = 0
    try:
        from archimedes.services.paper_rag import corpus_embedding_at_rest, rerank_candidate_cap

        _at_rest = corpus_embedding_at_rest()
        corpus_embedded_at_rest = _at_rest.embedded_at_rest
        corpus_embedded_at_rest_reason = _at_rest.reason
        rerank_cap = rerank_candidate_cap()
    except Exception as exc:
        logger.warning("corpus_embedding_at_rest read failed (%s) — reporting not-embedded", type(exc).__name__)
        corpus_embedded_at_rest_reason = f"probe failed ({type(exc).__name__})"
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
        # Build provenance (issue #1039): the git SHA stamped in at image-build
        # time (deploy.yml --build-arg GIT_SHA). "Which code is live?" in one glance
        # — the question that turned the Fargate cutover into an hour of forensics.
        "version": os.getenv("ARCHIMEDES_GIT_SHA", "dev"),
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
        "paper_rerank_model_live": paper_rerank_model_live,
        "corpus_embedded_at_rest": corpus_embedded_at_rest,
        "corpus_embedded_at_rest_reason": corpus_embedded_at_rest_reason,
        "rerank_candidate_cap": rerank_cap,
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
        # Oracle-freshness health (issue #1371). oracle_fresh is true only when
        # EVERY probed oracle is fresh — oracle_probed_count/oracle_universe_count
        # are always both present so a fully-fresh push set is never read as
        # "the oracle subsystem is healthy" when the push set is a small
        # fraction of the deployed universe. See services/oracle_health.py.
        "oracle_fresh": oracle_fresh,
        "oracle_oldest_age_s": oracle_oldest_age_s,
        "oracle_probed_count": oracle_probed_count,
        "oracle_universe_count": oracle_universe_count,
        "oracle_reason": oracle_reason,
        # Strategy-library presence (issue #1039) — 0 means the image is missing
        # analytics-engine/strategies (the Fargate-cutover regression). CI gates on > 0.
        "strategy_count": strategy_count,
    }


@app.get("/health/paper-rag")
@limiter.exempt
async def health_paper_rag(response: Response):
    """Dedicated paper-rag health endpoint."""
    _no_store(response)
    from archimedes.services.paper_rag import paper_rag_health

    # probe=True: this endpoint exists to PROVE the model loads, so it is the
    # one place allowed to pay the ~521 MB load cost. The ALB-polled /health
    # deliberately does not (see paper_rag_health's docstring).
    diag = paper_rag_health(probe=True)
    return {
        "paper_rag": diag.status,
        "reason": diag.reason,
    }


@app.get("/health/amm")
@app.get("/api/health/amm")
@limiter.exempt
async def health_amm(response: Response):
    """AMM pool liquidity health — per-pool status for operator/judge probes.

    Returns 200 with pool list when pools exist, or 503 with an explicit
    status message when they haven't been initialized. Never returns 404.
    """
    from fastapi.responses import JSONResponse

    from archimedes.chain.client import chain_client

    _no_store(response)
    try:
        connected = await chain_client.is_connected()
        if not connected:
            return JSONResponse(
                status_code=503,
                content={"status": "chain_disconnected", "reason": "Cannot reach Arc RPC"},
                headers=_no_store_headers(),
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
                headers=_no_store_headers(),
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
                # Log full detail server-side only — the exception text (which
                # can include RPC/contract internals) must not flow into this
                # public health-check response (CodeQL py/stack-trace-exposure, #9).
                logging.getLogger(__name__).warning(
                    "AMM health check: failed to read pool state for %s", addr, exc_info=exc
                )
                pool_info["error"] = "failed to read pool state — see server logs"
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
            headers=_no_store_headers(),
        )


@app.get("/")
@limiter.exempt
async def root():
    return {
        "name": "Archimedes",
        "tagline": "Agentic trading, grounded in research — settled on Arc.",
        "docs": _docs_url or "disabled (production)",
        # Agent-discoverability pointers (additive, backwards-compatible — see /llms.txt
        # and docs/agent-api.md for the full agent-facing contract).
        "llms_txt": "/llms.txt",
        "agent_manifest": "/api/agent/manifest",
        "agent_docs": "see /llms.txt",
    }
