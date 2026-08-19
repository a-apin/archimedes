"""Guard tests for issue #1044 leak 1 — OllamaBackend must probe honestly.

Before this fix, ``OllamaBackend.available`` was hardcoded ``True`` (unlike
every other backend), so a misconfigured or unreachable local Ollama was
reported live by ``make_llm_backend()`` and ``/health`` right up until the
first real request errored. These tests are hermetic — no real Ollama server,
no network — via a fake ``httpx.get``/``httpx.Response``.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


def _fake_response(*, status_ok: bool = True, models: list[str] | None = None):
    resp = MagicMock()
    if status_ok:
        resp.raise_for_status.return_value = None
    else:
        resp.raise_for_status.side_effect = Exception("boom")
    resp.json.return_value = {"models": [{"name": name} for name in (models or [])]}
    return resp


@pytest.fixture(autouse=True)
def _isolate_llm_env(monkeypatch):
    # These tests construct OllamaBackend directly and don't exercise
    # LLM_PROVIDER routing, but keep the env clean so a stray LLM_MODEL/
    # LLM_BASE_URL from the developer's shell can't change the assertions.
    for var in ("LLM_MODEL", "LLM_BASE_URL", "LLM_PROVIDER"):
        monkeypatch.delenv(var, raising=False)


def test_available_false_when_server_unreachable():
    from archimedes.services.llm_backend import OllamaBackend

    backend = OllamaBackend(model="llama3.1")
    with patch("httpx.get", side_effect=OSError("connection refused")):
        assert backend.available is False


def test_available_false_when_model_not_pulled():
    from archimedes.services.llm_backend import OllamaBackend

    backend = OllamaBackend(model="llama3.1")
    with patch("httpx.get", return_value=_fake_response(models=["mistral:latest"])):
        assert backend.available is False


def test_available_true_when_exact_tag_pulled():
    from archimedes.services.llm_backend import OllamaBackend

    backend = OllamaBackend(model="llama3.1:latest")
    with patch("httpx.get", return_value=_fake_response(models=["llama3.1:latest"])):
        assert backend.available is True


def test_available_true_when_bare_name_matches_tagged_pull():
    """LLM_MODEL=llama3.1 (no variant) must match a pulled "llama3.1:latest"."""
    from archimedes.services.llm_backend import OllamaBackend

    backend = OllamaBackend(model="llama3.1")
    with patch("httpx.get", return_value=_fake_response(models=["llama3.1:latest"])):
        assert backend.available is True


def test_make_llm_backend_returns_live_ollama_when_reachable_and_pulled(monkeypatch):
    """Adversarial-pass counterpart: a correctly configured, reachable server
    with the model pulled must NOT be downgraded to CannedBackend — the fix
    must not overcorrect into always-unavailable.
    """
    from archimedes.services.llm_backend import OllamaBackend, make_llm_backend

    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.setenv("LLM_MODEL", "llama3.1")
    monkeypatch.setenv("LLM_BASE_URL", "http://host.docker.internal:11434")
    with patch("httpx.get", return_value=_fake_response(models=["llama3.1:latest"])):
        backend = make_llm_backend()
    assert isinstance(backend, OllamaBackend)


def test_make_llm_backend_falls_back_to_canned_when_ollama_unreachable(monkeypatch):
    """Integration: the /health honesty contract — an unreachable ollama must
    resolve to CannedBackend, not silently report live.
    """
    from archimedes.services.llm_backend import CannedBackend, make_llm_backend

    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.setenv("LLM_MODEL", "llama3.1")
    monkeypatch.setenv("LLM_BASE_URL", "http://127.0.0.1:1")  # nothing listens here
    with patch("httpx.get", side_effect=OSError("connection refused")):
        backend = make_llm_backend()
    assert isinstance(backend, CannedBackend)
