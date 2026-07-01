"""REST API response schemas — Daniel's frontend depends on these.

These are Pydantic models that define the JSON shape of every API response.
Chuan implements the FastAPI endpoints; Daniel codes the frontend against
these schemas. Changes here require a heads-up to Daniel.

Convention:
  - All monetary values in USDC are floats (display-friendly)
  - All on-chain addresses are checksummed hex strings
  - All timestamps are ISO 8601 strings
  - Pagination uses limit/offset
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, model_validator

# ═══════════════════════════════════════════════════════════════
# Assets
# ═══════════════════════════════════════════════════════════════


class AssetResponse(BaseModel):
    """Single asset in the ecosystem."""

    address: str
    symbol: str
    name: str
    asset_type: str  # "synthetic" | "bridged" | "native" | "vault_token"
    decimals: int
    price_usd: float
    price_change_24h: float = 0.0  # Percentage change
    oracle_address: str | None = None


class AssetListResponse(BaseModel):
    assets: list[AssetResponse]


class AssetPriceHistoryResponse(BaseModel):
    """Historical prices for charting."""

    symbol: str
    prices: list[PricePoint]
    interval: str  # "1h" | "1d" | "1w"


class PricePoint(BaseModel):
    timestamp: str  # ISO 8601
    price: float


# ═══════════════════════════════════════════════════════════════
# Vaults
# ═══════════════════════════════════════════════════════════════


class VaultHolding(BaseModel):
    """A single position in a vault."""

    symbol: str
    token_address: str
    amount: float
    value_usdc: float
    weight_pct: float  # e.g. 30.5 = 30.5%


class VaultSummaryResponse(BaseModel):
    """Vault card for the leaderboard/list view."""

    address: str
    name: str
    symbol: str
    tier: int  # 1 or 2
    creator: str  # Wallet address
    aum_usdc: float
    share_price: float
    return_24h: float
    return_7d: float
    return_30d: float
    return_inception: float
    sharpe_ratio: float | None = None
    management_fee_pct: float  # e.g. 1.5
    performance_fee_pct: float  # e.g. 20.0
    is_agent_assisted: bool
    depositors: int
    last_rebalance: str | None = None  # ISO 8601
    created_at: str


class VaultDetailResponse(BaseModel):
    """Full vault detail page data."""

    # Core info (same as summary)
    address: str
    name: str
    symbol: str
    tier: int
    creator: str
    aum_usdc: float
    share_price: float
    is_agent_assisted: bool

    # Fees
    management_fee_pct: float
    performance_fee_pct: float
    high_water_mark: float

    # Holdings
    holdings: list[VaultHolding]
    target_allocations: list[VaultHolding]  # Target weights

    # Performance
    return_24h: float
    return_7d: float
    return_30d: float
    return_inception: float
    sharpe_ratio: float | None = None
    max_drawdown: float | None = None

    # Equity curve for charting
    equity_curve: list[PricePoint] = []

    # Strategy info (Tier 1 only)
    strategy_ids: list[str] = []
    current_regime: str | None = None  # "risk_on" | "risk_off" | etc.

    # Recent reasoning traces
    recent_traces: list[TraceResponse] = []

    # Metadata
    depositors: int = 0
    last_rebalance: str | None = None
    created_at: str = ""


class VaultListResponse(BaseModel):
    vaults: list[VaultSummaryResponse]
    total: int


# ═══════════════════════════════════════════════════════════════
# Strategies
# ═══════════════════════════════════════════════════════════════


class PaperRefResponse(BaseModel):
    """A single paper reference in a strategy passport."""

    arxiv_id: str | None = None
    title: str = ""
    authors: list[str] = []
    doi: str | None = None
    venue: str | None = None
    year: int | None = None
    citation_count: int | None = None
    contribution: str | None = None


class StrategyResponse(BaseModel):
    """Strategy detail for the strategy explorer."""

    id: str
    papers: list[PaperRefResponse] = []
    methodology_summary: str
    asset_universe: list[str]
    position_sizing: str
    rebalance_frequency: str
    status: str  # "candidate" | "validated" | "live" | "retired" | "rejected"

    # Legacy scalar fields (populated from papers[0] for backwards compat)
    paper_arxiv_id: str = ""
    paper_title: str = ""
    paper_authors: list[str] = []
    paper_venue: str | None = None
    paper_year: int | None = None
    paper_doi: str | None = None
    paper_citation_count: int | None = None

    # Passport integrity
    methodology_hash: str | None = None
    extraction_llm: str | None = None
    curator_wallet: str | None = None
    curator_note: str | None = None
    on_chain_registration_tx: str | None = None

    # Backtest results (if evaluated; None = not yet run)
    sharpe_ratio: float | None = None
    sortino_ratio: float | None = None
    max_drawdown: float | None = None
    cagr: float | None = None
    win_rate: float | None = None
    total_trades: int | None = None
    calmar_ratio: float | None = None
    correlation_to_spy: float | None = None
    deflated_sharpe_ratio: float | None = None
    dsr_p_value: float | None = None
    pbo_score: float | None = None
    out_of_sample_sharpe: float | None = None
    kelly_fraction: float | None = None
    passes_rigor_gate: bool = False
    paper_claimed_sharpe: float | None = None
    paper_claim_blended_sharpe: float | None = None
    is_backtest_placeholder: bool = False
    sharpe_ci_lower: float | None = None
    sharpe_ci_upper: float | None = None

    # Backtest period (ISO date strings; what window the metrics were computed over)
    backtest_start: str | None = None
    backtest_end: str | None = None

    # Equity curve for charting
    equity_curve: list[PricePoint] = []

    # Regime suitability
    regime_tag: str = "regime_neutral"  # "bull" | "bear" | "regime_neutral"

    # Return-source classification (T2.5) — the dominant economic source of the
    # strategy's return, plus a one-sentence durability note. Computed by the
    # deterministic heuristic in services/return_source_classifier.py.
    return_source: str = "noise"  # "risk_premium" | "mispricing" | "productive_growth" | "noise"
    return_source_note: str = ""


class StrategyListResponse(BaseModel):
    strategies: list[StrategyResponse]
    total: int


# ═══════════════════════════════════════════════════════════════
# Reasoning Traces
# ═══════════════════════════════════════════════════════════════


class TraceResponse(BaseModel):
    """A single reasoning trace for display."""

    id: str
    vault_address: str
    decision_type: str  # "construction" | "rebalance" | "rotation" | "regime_change" | "skip"
    trigger: str
    timestamp: str  # ISO 8601
    reasoning: str  # Human-readable explanation
    confidence: float

    # On-chain verification
    trace_hash: str
    arc_tx_hash: str | None = None
    is_verified: bool = False  # Has on-chain hash been confirmed?

    # Context
    regime_at_decision: str | None = None
    trades_executed: list[TradeExecutedResponse] = []
    strategies_referenced: list[str] = []

    # Commit-reveal temporal binding (v1.5)
    commit_tx_hash: str | None = None
    commit_block_number: int | None = None
    reveal_tx_hash: str | None = None
    reveal_block_number: int | None = None
    trade_tx_hash: str | None = None
    trade_block_number: int | None = None
    temporal_binding_valid: bool | None = None
    # Provenance of the temporal-binding claim (#714 / T0.3). "chain" only when the
    # real commit-reveal path ran (an on-chain trace_id was minted); "none" otherwise
    # (legacy publishTrace anchor, dry-run, or no commit recorded). The UI "Temporal
    # Binding ✓ VERIFIED" badge must key off this, not a bare boolean from Redis.
    temporal_binding_source: Literal["chain", "none"] = "none"

    @model_validator(mode="after")
    def _temporal_binding_requires_chain_source(self) -> TraceResponse:
        """Claim-integrity guard (#714): temporal_binding_valid may only assert a
        verified binding when it is backed by on-chain commit-reveal receipts.

        Closes the audit finding (AUDIT_2026-06-14 #3) where the badge could read True
        off a Redis boolean: regardless of what the persisted dict holds, a non-"chain"
        source can never surface a True binding — it is coerced to None ("not applicable").
        ``is_verified`` is deliberately left untouched: a publishTrace anchor is a genuine
        on-chain hash confirmation, so reporting it verified is honest — only the stronger
        *temporal* binding claim is gated here.
        """
        if self.temporal_binding_source != "chain" and self.temporal_binding_valid:
            self.temporal_binding_valid = None
        return self


class TradeExecutedResponse(BaseModel):
    symbol: str
    direction: str  # "buy" | "sell"
    amount: float = 0.0
    value_usdc: float = 0.0


class TraceListResponse(BaseModel):
    traces: list[TraceResponse]
    total: int


class TracePublishRequest(BaseModel):
    """Request to publish a reasoning trace on-chain."""

    vault_address: str
    decision_type: str = "construction"  # construction | rebalance | rotation | regime_change | skip
    trigger: str = "manual"
    reasoning: str = ""
    confidence: float = 0.0
    market_context: dict = {}
    portfolio_before: dict = {}
    portfolio_after: dict = {}
    trades_executed: list[dict] = []
    strategies_referenced: list[str] = []


class TracePublishResponse(BaseModel):
    """Response after publishing a trace on-chain."""

    id: str  # UUID
    trace_hash: str  # keccak256 hex
    arc_tx_hash: str | None = None
    is_anchored: bool = False
    timestamp: str  # ISO 8601
    vault_address: str
    decision_type: str


class TraceVerifyResponse(BaseModel):
    """Verification result for a single trace."""

    trace_id: int  # On-chain trace ID
    trace_hash: str
    is_verified: bool
    agent: str = ""
    vault: str = ""
    on_chain_timestamp: int = 0
    details: str  # Human-readable result
    # Temporal binding verification
    temporal_binding_valid: bool | None = None
    commit_block_number: int | None = None
    trade_block_number: int | None = None
    reveal_block_number: int | None = None


# ═══════════════════════════════════════════════════════════════
# Regime
# ═══════════════════════════════════════════════════════════════


class RegimeResponse(BaseModel):
    """Current market regime for display."""

    regime: str  # "risk_on" | "risk_off" | "transition" | "crisis"
    confidence: float
    timestamp: str
    previous_regime: str | None = None
    regime_changed: bool = False
    signals: RegimeSignalsResponse
    transition_probabilities: dict | None = None  # From get_transition_probabilities()
    transitions_source: str = "default_prior"  # "redis_measured" | "default_prior"
    regime_history: dict | None = None  # From get_regime_history_summary()
    recommended_strategies: list[str] | None = None  # Strategy IDs best for this regime
    # Paper titles for each recommended_strategies id, in matching order.
    # Surfaced so the UI can show "Volatility-Managed Portfolios" instead of
    # a raw strategy hash (red-team report 2026-05-24 H3).
    recommended_strategy_titles: list[str] | None = None


class RegimeSignalsResponse(BaseModel):
    # vix_level is nullable: the VIX feed can be unavailable (no data) and we
    # MUST NOT render that as 0.0 — VIX is a price-of-insurance index that
    # floors around 10, so 0 is dishonest. None means "agent feed not
    # connected" (red-team report 2026-05-24 H2).
    vix_level: float | None = None
    sp500_above_ma50: bool
    sp500_above_ma200: bool
    vix_rate_of_change: float | None = None  # VIX momentum
    vix_score: float | None = None  # 0-1 danger score from VIX level
    ma_score: float | None = None  # 0-1 from MA positioning
    composite_score: float | None = None  # Final 0-1 composite
    credit_spread_ig: float | None = None
    credit_spread_hy: float | None = None
    btc_dominance: float | None = None


# ═══════════════════════════════════════════════════════════════
# Swap (AMM preview)
# ═══════════════════════════════════════════════════════════════


class SwapQuoteResponse(BaseModel):
    """Preview a swap before user signs the transaction."""

    token_in: str  # Address
    token_out: str
    amount_in: float
    amount_out: float
    price_impact_pct: float  # e.g. 0.5 = 0.5%
    fee_pct: float  # e.g. 0.3 = 0.3%
    min_amount_out: float  # After slippage tolerance


class PoolResponse(BaseModel):
    """AMM pool summary for the exchange UI."""

    address: str
    token0: str
    token1: str
    symbol0: str
    symbol1: str
    reserve0: float
    reserve1: float
    tvl_usdc: float
    volume_24h_usdc: float = 0.0
    fee_pct: float
    apr_pct: float | None = None
    total_supply: float


class PoolListResponse(BaseModel):
    pools: list[PoolResponse]
    total: int


# ═══════════════════════════════════════════════════════════════
# Contract Addresses (for frontend to call on-chain directly)
# ═══════════════════════════════════════════════════════════════


# ═══════════════════════════════════════════════════════════════
# Chat (per-vault)
# ═══════════════════════════════════════════════════════════════


class ChatMessageResponse(BaseModel):
    """A single chat message in a vault's chat room."""

    id: int
    vault_address: str
    wallet_address: str
    message: str
    is_ai: bool = False
    verified: bool = False  # True when the wallet identity was SIWE-session-proven at post time (#524)
    created_at: str  # ISO 8601


class ChatMessageListResponse(BaseModel):
    """Paginated list of chat messages for a vault."""

    messages: list[ChatMessageResponse]
    total: int
    has_more: bool = False


class ChatPostRequest(BaseModel):
    """Post a new message to a vault's chat.

    wallet_address is optional when the caller has a SIWE session — identity
    then comes from the session, not the body (#524). Without a session it is
    required and the message is stored as unverified.
    """

    wallet_address: str | None = None
    message: str


class ChatPostResponse(BaseModel):
    """Response after posting a message. Includes AI response if triggered."""

    message: ChatMessageResponse
    ai_response: ChatMessageResponse | None = None


# ═══════════════════════════════════════════════════════════════
# Contract Addresses
# ═══════════════════════════════════════════════════════════════


class ContractAddressesResponse(BaseModel):
    """All deployed contract addresses. Frontend needs these for direct on-chain calls."""

    usdc: str
    synthetic_factory: str
    amm_router: str
    vault_factory: str
    reasoning_trace_registry: str
    asset_registry: str
    price_oracle: str

    # Individual synthetic token addresses
    synthetics: dict[str, str]  # symbol → address, e.g. {"sTSLA": "0x..."}

    # AMM pool addresses
    pools: dict[str, str]  # pair → address, e.g. {"USDC/sTSLA": "0x..."}

    # Vault addresses
    vaults: dict[str, str]  # symbol → address, e.g. {"vMOMENTUM": "0x..."}

    # Chain info
    chain_id: int
    rpc_url: str


# ═══════════════════════════════════════════════════════════════
# Strategy Signals (live evaluation)
# ═══════════════════════════════════════════════════════════════


class SignalResponse(BaseModel):
    asset: str
    signal: str  # "long" | "flat" | "scaled"
    weight: float
    reason: str
    strategy_name: str


class StrategySignalResponse(BaseModel):
    strategy_id: str
    paper_title: str
    signals: list[SignalResponse]


class StrategySignalsResponse(BaseModel):
    strategy_count: int
    # `regime` is retained for backward compatibility with existing frontend
    # reads. It is the flat_pct-derived ENSEMBLE CONSENSUS bucket, not a market
    # regime (#659) — `ensemble_consensus` carries the same value under the
    # correct name. A true market regime would come from a detector.
    regime: str
    ensemble_consensus: str | None = None
    confidence: float
    target_weights: dict[str, float]
    strategies: list[StrategySignalResponse]
    timestamp: str


# ═══════════════════════════════════════════════════════════════
# Agent Status (monitoring)
# ═══════════════════════════════════════════════════════════════


class AgentStatusResponse(BaseModel):
    alive: bool
    last_heartbeat: str | None = None
    regime: str | None = None
    regime_confidence: float | None = None
    regime_source: str | None = None
    strategy_count: int = 0
    managed_vaults: int = 0
    last_rebalance: str | None = None
    recent_events: list[dict] = []


# ═══════════════════════════════════════════════════════════════
# AMM Health
# ═══════════════════════════════════════════════════════════════


class AMMPoolHealth(BaseModel):
    """Health status of a single AMM pool (synth/USDC pair)."""

    symbol: str
    status: str  # "healthy" | "low_liquidity" | "empty" | "error"
    liquidity_usdc: float = 0.0
    oracle_price: float | None = None
    reserve_token: float = 0.0
    reserve_usdc: float = 0.0
    last_update: str  # ISO 8601


class AMMHealthResponse(BaseModel):
    """Health status of all AMM pools."""

    pools: list[AMMPoolHealth]
    healthy_count: int = 0
    total_pools: int = 0
