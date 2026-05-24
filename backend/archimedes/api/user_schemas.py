"""Schemas for the user profile API (WelcomeProfileModal)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class UserProfileCreate(BaseModel):
    """Payload from WelcomeProfileModal. All fields optional except wallet."""

    wallet_address: str = Field(..., pattern=r"^0x[a-fA-F0-9]{40}$")
    display_name: str | None = Field(None, max_length=128)
    email: str | None = Field(None, max_length=256)
    interests: list[str] | None = Field(None, max_length=20)
    attribution: str | None = Field(None, max_length=256)
    marketing_opt_in: bool = False


class UserProfileResponse(BaseModel):
    """Profile returned to frontend."""

    wallet_address: str
    display_name: str | None = None
    email: str | None = None
    interests: list[str] | None = None
    attribution: str | None = None
    marketing_opt_in: bool = False
