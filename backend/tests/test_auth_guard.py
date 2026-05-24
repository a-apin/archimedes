"""Unit tests for the internal agent key guard."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException


def _make_request(header_value: str | None) -> MagicMock:
    req = MagicMock()
    if header_value is None:
        req.headers = {}
    else:
        req.headers = {"X-Internal-Agent-Key": header_value}
    return req


def test_missing_header_is_rejected(monkeypatch):
    monkeypatch.setenv("INTERNAL_AGENT_API_KEY", "deadbeef01234567")
    from archimedes.api.auth_guard import require_internal_agent_key

    with pytest.raises(HTTPException) as exc_info:
        require_internal_agent_key(_make_request(None))
    assert exc_info.value.status_code == 403


def test_wrong_key_is_rejected(monkeypatch):
    monkeypatch.setenv("INTERNAL_AGENT_API_KEY", "deadbeef01234567")
    from archimedes.api.auth_guard import require_internal_agent_key

    with pytest.raises(HTTPException) as exc_info:
        require_internal_agent_key(_make_request("wrongvalue"))
    assert exc_info.value.status_code == 403


def test_correct_key_passes(monkeypatch):
    monkeypatch.setenv("INTERNAL_AGENT_API_KEY", "deadbeef01234567")
    from archimedes.api.auth_guard import require_internal_agent_key

    result = require_internal_agent_key(_make_request("deadbeef01234567"))
    assert result is None


def test_unset_env_var_rejects_any_value(monkeypatch):
    monkeypatch.delenv("INTERNAL_AGENT_API_KEY", raising=False)
    from archimedes.api.auth_guard import require_internal_agent_key

    with pytest.raises(HTTPException) as exc_info:
        require_internal_agent_key(_make_request("anyvalue"))
    assert exc_info.value.status_code == 403
