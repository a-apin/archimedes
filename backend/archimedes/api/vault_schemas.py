from __future__ import annotations

from pydantic import BaseModel, Field


class VaultCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    symbol: str = Field(..., min_length=1, max_length=16)
    management_fee_bps: int = Field(0, ge=0, le=1000)
    performance_fee_bps: int = Field(0, ge=0, le=3000)
    agent_assisted: bool = True
    # Off-chain metadata only — not passed to the contract.
    # Stored in response for caller reference; persistence is a v2 hook.
    strategy_ids: list[str] = Field(default_factory=list)


class VaultCreateResponse(BaseModel):
    vault_address: str
    strategy_ids: list[str]


class VaultMetadataRequest(BaseModel):
    vault_address: str = Field(..., pattern=r"^0x[a-fA-F0-9]{40}$")
    name: str = Field("", max_length=64)
    symbol: str = Field("", max_length=16)
    creator_address: str = Field("", pattern=r"^(0x[a-fA-F0-9]{40})?$")
    strategy_ids: list[str] = Field(default_factory=list)


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


class SetAllocationsResponse(BaseModel):
    """Derived target allocations ready for on-chain submission."""

    allocations: list[AllocationTarget]
    total_bps: int = Field(..., description="Should equal 10000")
    strategy_count: int = Field(..., description="Number of strategies used")
