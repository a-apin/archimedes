"""Provider-agnostic LLM backend factory.

Reads ``LLM_PROVIDER`` ∈ {``anthropic``, ``anthropic_compatible``, ``openai``,
``ollama``} and constructs the right backend.  Falls back to ``CannedBackend``
when no credentials are present — loud degradation, never silent.

Back-compat: ``ANTHROPIC_*`` env vars still work this release (deprecated alias
path, emits a WARN log).
"""

from __future__ import annotations

import json
import logging
import os
from typing import Protocol

logger = logging.getLogger(__name__)

# ── Protocol ─────────────────────────────────────────────────────────

DEFAULT_MODEL = os.getenv("LLM_MODEL", "claude-sonnet-4-20250514")
MAX_TOKENS = 4096


class LLMBackend(Protocol):
    """Minimal text-completion seam consumed by architect + fusion."""

    @property
    def model_id(self) -> str: ...

    @property
    def served_model(self) -> str: ...

    @property
    def available(self) -> bool: ...

    def complete(self, system: str, user: str) -> str: ...


# ── Anthropic (direct API key) ───────────────────────────────────────


class AnthropicBackend:
    """Anthropic SDK with ``LLM_API_KEY``.

    Back-compat: falls back to ``ANTHROPIC_API_KEY`` if the new var is empty.
    """

    def __init__(self, model: str = DEFAULT_MODEL, api_key: str | None = None) -> None:
        import anthropic

        self._model = model
        self._served = model
        self._api_key = api_key or os.getenv("LLM_API_KEY", "") or os.getenv("ANTHROPIC_API_KEY", "")
        if not self._api_key:
            self._client = None
            return
        self._client: anthropic.Anthropic | None = anthropic.Anthropic(api_key=self._api_key)

    @property
    def model_id(self) -> str:
        return self._model

    @property
    def served_model(self) -> str:
        return self._served

    @property
    def available(self) -> bool:
        return self._client is not None

    def complete(self, system: str, user: str) -> str:
        assert self._client is not None
        resp = self._client.messages.create(
            model=self._model,
            max_tokens=MAX_TOKENS,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        served = getattr(resp, "model", None)
        if served:
            self._served = str(served)
        return resp.content[0].text.strip() if resp.content else ""


# ── Anthropic-compatible (auth_token + base_url, e.g. GLM via z.ai) ──


class AnthropicCompatibleBackend:
    """Anthropic SDK with ``LLM_AUTH_TOKEN`` + ``LLM_BASE_URL``.

    Back-compat: falls back to ``ANTHROPIC_AUTH_TOKEN`` / ``ANTHROPIC_BASE_URL``
    if the new vars are empty.
    """

    def __init__(self, model: str = DEFAULT_MODEL) -> None:
        import anthropic

        self._model = model
        self._served = model
        self._auth_token = os.getenv("LLM_AUTH_TOKEN", "") or os.getenv("ANTHROPIC_AUTH_TOKEN", "")
        self._base_url = os.getenv("LLM_BASE_URL", "") or os.getenv("ANTHROPIC_BASE_URL", "")
        if self._auth_token and self._base_url:
            self._client: anthropic.Anthropic | None = anthropic.Anthropic(
                auth_token=self._auth_token,
                base_url=self._base_url,
            )
        else:
            self._client = None

    @property
    def model_id(self) -> str:
        return self._model

    @property
    def served_model(self) -> str:
        return self._served

    @property
    def available(self) -> bool:
        return self._client is not None

    def complete(self, system: str, user: str) -> str:
        assert self._client is not None
        resp = self._client.messages.create(
            model=self._model,
            max_tokens=MAX_TOKENS,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        served = getattr(resp, "model", None)
        if served:
            self._served = str(served)
        return resp.content[0].text.strip() if resp.content else ""


# ── OpenAI-compatible (httpx, no SDK) ────────────────────────────────


class OpenAIBackend:
    """OpenAI-compatible via ``LLM_BASE_URL`` + ``LLM_API_KEY`` (httpx)."""

    def __init__(self, model: str = DEFAULT_MODEL) -> None:
        self._model = model
        self._served = model
        self._base_url = os.getenv("LLM_BASE_URL", "https://api.openai.com/v1").rstrip("/")
        self._api_key = os.getenv("LLM_API_KEY", "")

    @property
    def model_id(self) -> str:
        return self._model

    @property
    def served_model(self) -> str:
        return self._served

    @property
    def available(self) -> bool:
        return bool(self._api_key)

    def complete(self, system: str, user: str) -> str:
        import httpx

        resp = httpx.post(
            f"{self._base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self._api_key}"},
            json={
                "model": self._model,
                "max_tokens": MAX_TOKENS,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            },
            timeout=60.0,
        )
        resp.raise_for_status()
        data = resp.json()
        self._served = data.get("model", self._model)
        # Defensive: OpenAI-style APIs can legitimately return empty `choices`
        # (content filtering, tool-only responses, etc.). Mirror OllamaBackend's
        # `.get()`-chain pattern so we never IndexError mid-request.
        choices = data.get("choices") or []
        first = choices[0] if choices else {}
        return (first.get("message") or {}).get("content", "").strip()


# ── Ollama (local, no key) ───────────────────────────────────────────


class OllamaBackend:
    """Ollama via ``LLM_BASE_URL`` (default http://localhost:11434)."""

    def __init__(self, model: str = DEFAULT_MODEL) -> None:
        self._model = model
        self._served = model
        self._base_url = os.getenv("LLM_BASE_URL", "http://localhost:11434").rstrip("/")

    @property
    def model_id(self) -> str:
        return self._model

    @property
    def served_model(self) -> str:
        return self._served

    @property
    def available(self) -> bool:
        return True

    def complete(self, system: str, user: str) -> str:
        import httpx

        resp = httpx.post(
            f"{self._base_url}/api/chat",
            json={
                "model": self._model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "stream": False,
            },
            timeout=120.0,
        )
        resp.raise_for_status()
        data = resp.json()
        self._served = data.get("model", self._model)
        return data.get("message", {}).get("content", "").strip()


# ── Canned fallback ──────────────────────────────────────────────────


class CannedBackend:
    """Deterministic offline fallback. Explicitly NOT model reasoning."""

    model_id = "canned-fallback"
    served_model = "canned-fallback"

    @property
    def available(self) -> bool:
        return False

    def complete(self, system: str, user: str) -> str:  # noqa: ARG002 — Protocol-shaped fallback; signature matches live LLM backends
        return json.dumps(
            {
                "fallback": True,
                "message": "No LLM backend configured. Set LLM_PROVIDER + credentials.",
            }
        )


# ── Factory ──────────────────────────────────────────────────────────


def make_llm_backend() -> AnthropicBackend | AnthropicCompatibleBackend | OpenAIBackend | OllamaBackend | CannedBackend:
    """Construct the LLM backend from ``LLM_*`` env vars.

    Provider selection (``LLM_PROVIDER``):
      - ``anthropic``: Anthropic SDK with ``LLM_API_KEY``
      - ``anthropic_compatible``: Anthropic SDK with ``LLM_AUTH_TOKEN`` + ``LLM_BASE_URL``
      - ``openai``: httpx to OpenAI-compatible endpoint (``LLM_BASE_URL`` + ``LLM_API_KEY``)
      - ``ollama``: httpx to Ollama (``LLM_BASE_URL``, no key)

    Back-compat: if ``LLM_PROVIDER`` is unset, falls back to ``ANTHROPIC_*``
    env vars (deprecated, emits WARN).
    """
    model = os.getenv("LLM_MODEL", DEFAULT_MODEL)
    provider = os.getenv("LLM_PROVIDER", "").strip().lower()

    # Back-compat: auto-detect from ANTHROPIC_* if LLM_PROVIDER not set
    if not provider:
        return _legacy_backend(model)

    builders = {
        "anthropic": lambda: AnthropicBackend(model=model),
        "anthropic_compatible": lambda: AnthropicCompatibleBackend(model=model),
        "openai": lambda: OpenAIBackend(model=model),
        "ollama": lambda: OllamaBackend(model=model),
    }
    builder = builders.get(provider)
    if builder is None:
        logger.warning("llm: unknown provider %r; falling back to canned", provider)
        return CannedBackend()

    backend = builder()
    if not backend.available:
        logger.warning(
            "llm: provider %s configured but credentials missing; canned fallback",
            provider,
        )
        return CannedBackend()
    logger.info("llm: using provider=%s model=%s", provider, model)
    return backend


def _legacy_backend(model: str) -> AnthropicBackend | AnthropicCompatibleBackend | CannedBackend:
    """Back-compat: resolve from ANTHROPIC_* env vars."""
    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    auth_token = os.getenv("ANTHROPIC_AUTH_TOKEN", "")
    base_url = os.getenv("ANTHROPIC_BASE_URL", "")
    legacy_model = os.getenv("ANTHROPIC_DEFAULT_MODEL", model)

    if not api_key and not (auth_token and base_url):
        return CannedBackend()

    logger.warning("llm: ANTHROPIC_* env vars are deprecated — migrate to LLM_PROVIDER + LLM_*")
    if api_key:
        return AnthropicBackend(model=legacy_model, api_key=api_key)
    return AnthropicCompatibleBackend(model=legacy_model)
