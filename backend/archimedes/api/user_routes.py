"""Optional wallet-address profile API owned by canonical Better Auth user.

Endpoints:
  GET  /api/user/profile/{wallet}   — retrieve profile (404 if not set)
  POST /api/user/profile            — create or update profile

Wallet key is legacy provenance, not application identity. Writes require both
account session and matching proof-linked wallet.

Security (Issue #181):
  - Email is encrypted at rest via Fernet (services/email_crypto.py).
  - Email / display_name / marketing_opt_in are only echoed to account owner.
  - Non-owner GET returns only public-safe fields.
  - All log output routes through log_scrubber to prevent PII leakage.
"""

from __future__ import annotations

import json
import logging

from cryptography.fernet import InvalidToken
from fastapi import APIRouter, HTTPException, Request, Response
from sqlalchemy.orm import Session

from archimedes.api.account_auth import get_current_user, require_current_user
from archimedes.api.limiter import limiter
from archimedes.api.user_schemas import UserProfileCreate, UserProfileResponse
from archimedes.db import get_session
from archimedes.models.user_profile import UserProfile
from archimedes.services.email_crypto import decrypt_email, encrypt_email
from archimedes.services.log_scrubber import sanitize_log_value, scrub_profile

logger = logging.getLogger(__name__)

user_router = APIRouter(prefix="/api/user", tags=["user"])

# Public-safe fields returned to non-owner callers.
PUBLIC_FIELDS = ("wallet_address", "interests", "attribution")


def _profile_to_response(p: UserProfile, *, owner: bool = False) -> UserProfileResponse:
    """Build a response from a UserProfile ORM object.

    When *owner* is False (non-owner caller), PII fields are stripped.
    When *owner* is True (caller owns profile), full data
    is returned with email decrypted.
    """
    interests = json.loads(p.interests) if p.interests else None

    if not owner:
        return UserProfileResponse(
            wallet_address=p.wallet_address,
            display_name=None,
            email=None,
            interests=interests,
            attribution=p.attribution,
            marketing_opt_in=False,  # default-safe
        )

    try:
        email = decrypt_email(p.email)
    except InvalidToken:
        # Ciphertext can't be decrypted with the current EMAIL_ENCRYPTION_KEY
        # (e.g. a key rotation left old tokens unreadable). Degrade to a null
        # email rather than 500ing the whole profile for this wallet.
        logger.warning("decrypt_email failed for wallet=%s: InvalidToken", p.wallet_address)
        email = None

    return UserProfileResponse(
        wallet_address=p.wallet_address,
        display_name=p.display_name,
        email=email,
        interests=interests,
        attribution=p.attribution,
        marketing_opt_in=p.marketing_opt_in,
    )


def _extract_linked_wallet(request: Request) -> str | None:
    """Return wallet proven and linked to current Better Auth account."""
    from archimedes.api.wallet_routes import get_linked_wallet_address

    return get_linked_wallet_address(request)


@user_router.get("/profile/{wallet}", response_model=UserProfileResponse)
async def get_profile(wallet: str, request: Request):
    """Retrieve a wallet's profile. Returns 404 if not set.

    PII fields are included only for canonical account owner. Unclaimed legacy
    profile additionally requires current account's verified linked wallet.
    """
    session: Session = get_session()
    try:
        wallet_lower = wallet.lower()
        profile = session.query(UserProfile).filter(UserProfile.wallet_address == wallet_lower).first()
        if not profile:
            raise HTTPException(status_code=404, detail="Profile not found")

        # PII follows canonical account ownership, never a body/header address.
        user = get_current_user(request)
        caller = _extract_linked_wallet(request)
        is_owner = bool(
            user
            and (
                profile.owner_user_id == user.id or (profile.owner_user_id is None and caller == profile.wallet_address)
            )
        )
        response = _profile_to_response(profile, owner=is_owner)

        logger.info(
            "get_profile: wallet=%s owner=%s data=%s",
            sanitize_log_value(wallet_lower),
            is_owner,
            scrub_profile(response.model_dump()),
        )
        return response
    finally:
        session.close()


@user_router.post("/profile", response_model=UserProfileResponse)
@limiter.limit("1/minute")
async def upsert_profile(payload: UserProfileCreate, request: Request, response: Response):  # noqa: ARG001 — response param threaded for downstream cookie/header setting; not used in current body
    """Create or update a wallet's profile. All fields optional except wallet.

    Email is encrypted at rest. Caller needs canonical account session and
    matching verified linked wallet; client-supplied headers grant nothing.
    """
    user = require_current_user(request)
    caller = _extract_linked_wallet(request)
    if caller is None:
        raise HTTPException(status_code=403, detail="Forbidden: verified linked wallet required for profile writes")
    wallet = payload.wallet_address.lower()
    if caller != wallet:
        raise HTTPException(status_code=403, detail="Forbidden: linked wallet does not match payload wallet")

    session: Session = get_session()
    try:
        profile = session.query(UserProfile).filter(UserProfile.wallet_address == wallet).first()

        interests_json = json.dumps(payload.interests) if payload.interests else "[]"
        encrypted_email = encrypt_email(payload.email)

        if profile:
            if profile.owner_user_id not in {None, user.id}:
                raise HTTPException(status_code=409, detail="Profile belongs to another account")
            if profile.owner_user_id is None:
                canonical_profile = session.query(UserProfile).filter(UserProfile.owner_user_id == user.id).first()
                if canonical_profile is not None:
                    raise HTTPException(status_code=409, detail="Account already has a canonical profile")
                profile.owner_user_id = user.id
            # Update existing
            if payload.display_name is not None:
                profile.display_name = payload.display_name
            if payload.email is not None:
                profile.email = encrypted_email
            profile.interests = interests_json
            if payload.attribution is not None:
                profile.attribution = payload.attribution
            profile.marketing_opt_in = payload.marketing_opt_in
        else:
            # Create new
            profile = UserProfile(
                wallet_address=wallet,
                owner_user_id=user.id,
                display_name=payload.display_name,
                email=encrypted_email,
                interests=interests_json,
                attribution=payload.attribution,
                marketing_opt_in=payload.marketing_opt_in,
            )
            session.add(profile)

        session.commit()
        session.refresh(profile)

        logger.info(
            "upsert_profile: wallet=%s data=%s",
            sanitize_log_value(wallet),
            scrub_profile(
                {
                    "wallet_address": sanitize_log_value(wallet),
                    "email": payload.email,
                    "display_name": payload.display_name,
                    "marketing_opt_in": payload.marketing_opt_in,
                }
            ),
        )

        return _profile_to_response(profile, owner=True)
    except HTTPException:
        raise
    except Exception as e:
        session.rollback()
        # Do NOT include PII in error messages
        logger.error("upsert_profile failed for wallet=%s: %s", sanitize_log_value(wallet), type(e).__name__)
        raise HTTPException(status_code=500, detail="Profile update failed") from e
    finally:
        session.close()
