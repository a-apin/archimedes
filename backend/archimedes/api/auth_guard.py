"""Internal agent authentication guard.

Endpoints that trigger backend-signed on-chain actions or inject system events
must only be callable by the internal agent runner, not by arbitrary public callers.

Usage::

    from archimedes.api.auth_guard import require_internal_agent_key
    from fastapi import Depends

    @router.post("/protected")
    async def endpoint(_: None = Depends(require_internal_agent_key)):
        ...

Set INTERNAL_AGENT_API_KEY in the environment to a random 32-byte hex string.
If the env var is unset, all requests are rejected (fail-closed).
"""

from __future__ import annotations

import hmac
import os

from fastapi import HTTPException, Request


def require_internal_agent_key(request: Request) -> None:
    expected = os.getenv("INTERNAL_AGENT_API_KEY", "")
    provided = request.headers.get("X-Internal-Agent-Key", "")
    if not expected or not hmac.compare_digest(provided, expected):
        raise HTTPException(status_code=403, detail="Forbidden")
