from __future__ import annotations

from pydantic import BaseModel, Field

from archimedes.chain.constants import MAX_MANAGEMENT_FEE_BPS


class VaultCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    symbol: str = Field(..., min_length=1, max_length=16)
    # Aligned with the Vault.sol constructor caps (PR #1129 / issue #1138) —
    # the old le=1000 admitted requests the capped contract reverts. The
    # performance cap stays at 3000 (30%): deliberately stricter than the
    # on-chain hard ceiling of MAX_PERFORMANCE_FEE_BPS (5000), matching the
    # ~10-20% industry norm with headroom.
    management_fee_bps: int = Field(0, ge=0, le=MAX_MANAGEMENT_FEE_BPS)
    performance_fee_bps: int = Field(0, ge=0, le=3000)
    agent_assisted: bool = True
    # Off-chain metadata only — not passed to the contract.
    # Stored in response for caller reference; persistence is a v2 hook.
    strategy_ids: list[str] = Field(default_factory=list)
    # Per-user rigor strictness (1 = Conservative/badge … 5 = Speculative). The
    # server re-evaluates each strategy at this level and refuses deploy unless it
    # passes — always-on correctness floors (look-ahead, positive OOS, DSR ≥ 0.50)
    # hold at every level, so no strictness value bypasses the gate. Defaults to
    # the strictest level (fail-safe).
    strictness_level: int = Field(1, ge=1, le=5)


class VaultCreateResponse(BaseModel):
    vault_address: str
    strategy_ids: list[str]


class VaultMetadataRequest(BaseModel):
    vault_address: str = Field(..., pattern=r"^0x[a-fA-F0-9]{40}$")
    name: str = Field("", max_length=64)
    symbol: str = Field("", max_length=16)
    creator_address: str = Field("", pattern=r"^(0x[a-fA-F0-9]{40})?$")
    strategy_ids: list[str] = Field(default_factory=list)
    # Per-user rigor strictness (1..5) at which the linked strategies must pass.
    # This is the choke point for the client-signed deploy path (the UI creates
    # the vault on-chain from the user's wallet, then links strategies here), so
    # the server enforces the gate at this level before persisting the link.
    strictness_level: int = Field(1, ge=1, le=5)


class VaultMetadataResponse(BaseModel):
    vault_address: str
    name: str = ""
    symbol: str = ""
    creator_address: str = ""
    strategy_ids: list[str] = []
    created_at: str | None = None


class AllocationTarget(BaseModel):
    """A single token allocation entry."""

    symbol: str = Field(..., description="Asset symbol, e.g. 'sSPY' or 'USDC'")
    token_address: str = Field(..., description="On-chain ERC-20 address")
    weight_bps: int = Field(..., description="Weight in basis points, e.g. 2500 = 25%")


class SetAllocationsRequest(BaseModel):
    """Derive and return target allocations from selected strategies.

    Does NOT execute on-chain — returns the derived allocations so the UI
    can submit the setTargetAllocations tx via the user's wallet.
    """

    strategy_ids: list[str] = Field(default_factory=list)
    usdc_floor_pct: float = Field(20.0, ge=0, le=80, description="Min USDC allocation (%)")
    risk_profile: str = Field(
        "moderate",
        pattern="^(fixed_income|conservative|moderate|aggressive|hyper_risky)$",
        description="Maps to the Kelly γ table (RISK_AVERSION) for strategy-level sizing",
    )
    # Rigor strictness (1..5) that decides which selected strategies are sizeable:
    # a strategy sizes to a non-zero fraction only if it passes at this level, so a
    # strategy the user deployed at level L still receives capital (rather than
    # being silently zeroed by the level-1 badge check).
    strictness_level: int = Field(1, ge=1, le=5)


class SetAllocationsResponse(BaseModel):
    """Derived target allocations ready for on-chain submission."""

    allocations: list[AllocationTarget]
    total_bps: int = Field(..., description="Should equal 10000")
    strategy_count: int = Field(..., description="Number of strategies used")
    # Kelly-sizing transparency (additive; defaults keep old consumers working):
    risk_profile: str = "moderate"
    sized_strategies: dict[str, float] = Field(
        default_factory=dict,
        description="Per-strategy capital fraction = passport half-Kelly × profile multiplier (post budget scaling)",
    )
    excluded_strategy_ids: list[str] = Field(
        default_factory=list,
        description="Selected strategies sized to zero (rigor-gate CANDIDATE/fail, or no stored kelly_fraction)",
    )
    # Literal default rather than an import of
    # services.portfolio_constructor.REGIME_CONVENTION_NEUTRAL_NO_FEED: this
    # schemas module stays free of service imports. The route always sets the
    # value explicitly from the constructor, so this default only covers a
    # caller constructing the response directly.
    regime_convention: str = Field(
        default="neutral_no_feed",
        description=(
            "Whether the regime tilt actually ran: 'regime_tilt_applied' (a live regime scaled these "
            "weights) or 'neutral_no_feed' (no regime input, weights are pure Kelly sizing). Without "
            "this, an un-tilted allocation and a regime-tilted one that scored 1.0 look identical."
        ),
    )
