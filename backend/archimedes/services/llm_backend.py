"""Provider-agnostic LLM backend factory.

Reads ``LLM_PROVIDER`` ∈ {``anthropic``, ``anthropic_compatible``, ``bedrock``,
``bedrock_converse``, ``openai``, ``ollama``} and constructs the right backend.
Falls back to ``CannedBackend`` when no credentials are present — loud degradation.

Back-compat: ``ANTHROPIC_*`` env vars still work this release (deprecated alias
path, emits a WARN log).
"""

from __future__ import annotations

import json
import logging
import os
from typing import Protocol

from archimedes.services.cost_meter import record_llm_call

logger = logging.getLogger(__name__)

# ── Protocol ─────────────────────────────────────────────────────────

DEFAULT_MODEL = os.getenv("LLM_MODEL", "claude-sonnet-4-20250514")
# Bedrock requires a Bedrock model id / cross-region inference-profile id, which
# differs from the public Anthropic alias above. Default is Haiku 4.5 — by far the
# cheapest option (the free/default tier). Pricier, stronger models (Sonnet/Opus)
# are available via LLM_BEDROCK_MODEL and are intended to be gated to paying users.
DEFAULT_BEDROCK_MODEL = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
# Default for the Converse path (provider=bedrock_converse). Amazon Nova Micro is
# the cheapest competitive text model on Bedrock ($0.035/$0.14 per 1M) and — being
# AWS-native — is invokable immediately, with NO Anthropic use-case form. Overridable
# via LLM_BEDROCK_MODEL (e.g. zai.glm-4.7-flash, deepseek.v3.2, us.meta.llama3-3-70b-instruct-v1:0).
DEFAULT_CONVERSE_MODEL = "amazon.nova-micro-v1:0"
MAX_TOKENS = 4096

# ── Free-tier model allowlist (server-side defense-in-depth) ─────────────
# The Generate page exposes a model picker, but only FREE/default-tier models
# may actually be selected. This set is the server-side enforcement of that
# UI restriction: a user-supplied ``model`` is honored ONLY if it appears here.
# Anything else (premium Anthropic-on-Bedrock ids, junk, etc.) is ignored and
# the request falls back to the env default — so the picker can never route a
# free user onto a premium model before the #723 HTTP-402 entitlement gate
# lands. Mirrors the ``works_now: true`` rows in ui/src/data/modelPricing.json;
# keep the two in sync. Premium ids (Claude Haiku/Sonnet on Bedrock) are
# deliberately ABSENT.
FREE_TIER_MODELS: frozenset[str] = frozenset(
    {
        "amazon.nova-micro-v1:0",
        "amazon.nova-lite-v1:0",
        "amazon.nova-pro-v1:0",
        "openai.gpt-oss-20b-1:0",
        "zai.glm-4.7-flash",
        "zai.glm-4.7",
        "qwen.qwen3-32b-v1:0",
        "us.meta.llama4-scout-17b-instruct-v1:0",
        "us.meta.llama3-3-70b-instruct-v1:0",
        "deepseek.v3.2",
        "moonshotai.kimi-k2.5",
        "mistral.mistral-small-2402-v1:0",
    }
)


def is_allowed_model(model: str | None) -> bool:
    """True iff ``model`` is a non-empty, allowlisted free-tier model id.

    Defense-in-depth: the UI already disables premium rows, but the server
    re-checks so a hand-crafted request can't bypass the gate.
    """
    return bool(model) and model in FREE_TIER_MODELS


def _first_text_block(content) -> str:
    """Return the first text block's text from an Anthropic-style content list.

    The first block is not always text: with extended thinking or tool use the
    response leads with a ``thinking``/``tool_use`` block that has no ``.text``
    attribute, so ``content[0].text`` raises ``AttributeError`` (issue #930).
    Iterate to the first block that actually carries text; return "" if none does.
    """
    for block in content or []:
        text = getattr(block, "text", None)
        if isinstance(text, str) and text.strip():
            return text.strip()
    return ""


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
        # Cost instrumentation (#1217): the provider's own usage block, banked
        # against whatever job meter is bound to this context. No-op when none is.
        record_llm_call(model=self._served, response=resp)
        return _first_text_block(resp.content)


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
        # Cost instrumentation (#1217): the provider's own usage block, banked
        # against whatever job meter is bound to this context. No-op when none is.
        record_llm_call(model=self._served, response=resp)
        return _first_text_block(resp.content)


# ── AWS Bedrock (IAM auth, no API key) ───────────────────────────────


class BedrockBackend:
    """Anthropic SDK over AWS Bedrock (``anthropic.AnthropicBedrock``).

    Auth is IAM via the standard boto3 credential chain — the EC2 instance role
    in production, ``AWS_PROFILE`` / keys locally. There is NO API key or auth
    token. The model id is a Bedrock model id or, more commonly, a cross-region
    inference-profile id (most current Anthropic models on Bedrock are
    INFERENCE_PROFILE-only): e.g. ``us.anthropic.claude-haiku-4-5-20251001-v1:0``.
    Resolved from ``LLM_BEDROCK_MODEL`` (else a sane default), NOT the generic
    ``LLM_MODEL`` whose default is a non-Bedrock alias. Region defaults to
    ``AWS_REGION`` (us-east-1 in prod).
    """

    def __init__(self, model: str | None = None) -> None:
        self._region = os.getenv("LLM_BEDROCK_REGION", "") or os.getenv("AWS_REGION", "") or "us-east-1"
        self._model = model or os.getenv("LLM_BEDROCK_MODEL", "") or DEFAULT_BEDROCK_MODEL
        self._served = self._model
        self._client = None
        try:
            import boto3
            from anthropic import AnthropicBedrock

            # IAM auth: only "available" when boto3 can actually resolve
            # credentials (instance role / profile). Without this guard a client
            # that constructs but 401s on first call would mask the canned
            # fallback and surface as a runtime error instead of loud degradation.
            if boto3.Session().get_credentials() is None:
                logger.warning("llm: Bedrock selected but no AWS credentials resolvable; canned fallback")
                return
            self._client: AnthropicBedrock | None = AnthropicBedrock(aws_region=self._region)
        except ImportError as exc:
            logger.warning("llm: Bedrock unavailable (%s) — needs anthropic[bedrock] + boto3; canned fallback", exc)
            self._client = None
        except Exception as exc:
            logger.warning("llm: Bedrock client init failed (%s); canned fallback", exc)
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
        # Cost instrumentation (#1217): the provider's own usage block, banked
        # against whatever job meter is bound to this context. No-op when none is.
        record_llm_call(model=self._served, response=resp)
        return _first_text_block(resp.content)


# ── AWS Bedrock via the Converse API (uniform across ALL providers, IAM auth) ──


class BedrockConverseBackend:
    """Bedrock **Converse** API via boto3 — one request/response shape across every
    Bedrock provider (Amazon Nova, Meta Llama, Mistral, DeepSeek, Qwen, Z.AI GLM,
    Moonshot Kimi, Anthropic, ...). IAM auth via the boto3 credential chain (EC2
    instance role in prod); no API key.

    Unlike the Anthropic-SDK ``BedrockBackend``, this works with the many
    non-Anthropic models that are invokable WITHOUT the Anthropic use-case form, so
    it serves real intelligence immediately and cheaply. Default: Amazon Nova Micro.
    Model id from ``LLM_BEDROCK_MODEL`` (else ``DEFAULT_CONVERSE_MODEL``). This is
    also the path a future per-user model picker rides on (one API, any model).
    """

    def __init__(self, model: str | None = None) -> None:
        self._region = os.getenv("LLM_BEDROCK_REGION", "") or os.getenv("AWS_REGION", "") or "us-east-1"
        self._model = model or os.getenv("LLM_BEDROCK_MODEL", "") or DEFAULT_CONVERSE_MODEL
        self._served = self._model
        self._client = None
        try:
            import boto3

            if boto3.Session().get_credentials() is None:
                logger.warning("llm: Bedrock(Converse) selected but no AWS credentials resolvable; canned fallback")
                return
            self._client = boto3.client("bedrock-runtime", region_name=self._region)
        except ImportError as exc:
            logger.warning("llm: Bedrock(Converse) unavailable (%s) — needs boto3; canned fallback", exc)
            self._client = None
        except Exception as exc:
            logger.warning("llm: Bedrock(Converse) client init failed (%s); canned fallback", exc)
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
        kwargs: dict = {
            "modelId": self._model,
            "messages": [{"role": "user", "content": [{"text": user}]}],
            "inferenceConfig": {"maxTokens": MAX_TOKENS},
        }
        if system and system.strip():
            kwargs["system"] = [{"text": system}]
        try:
            resp = self._client.converse(**kwargs)
        except Exception as exc:
            if system and "system" in str(exc).lower():
                kwargs.pop("system", None)
                kwargs["messages"] = [{"role": "user", "content": [{"text": f"{system}\n\n{user}"}]}]
                resp = self._client.converse(**kwargs)
            else:
                raise
        # Cost instrumentation (#1217). Converse reports usage as
        # {"usage": {"inputTokens": n, "outputTokens": n, "totalTokens": n}}.
        record_llm_call(model=self._served, response=resp)
        blocks = resp.get("output", {}).get("message", {}).get("content", []) or []
        # Reasoning models may emit a reasoningContent block before the text — return
        # the first block that actually carries text.
        for b in blocks:
            if isinstance(b, dict) and b.get("text"):
                return b["text"].strip()
        return ""


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
        # Cost instrumentation (#1217): {"usage": {"prompt_tokens", "completion_tokens"}}.
        record_llm_call(model=self._served, response=data)
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
        """Probe the Ollama server for reachability AND that ``LLM_MODEL`` is
        actually pulled there.

        Unlike every other backend, Ollama has no credential to check
        statically — so ``available`` used to be hardcoded ``True``, which made
        ``make_llm_backend()`` treat a misconfigured/unreachable ollama as
        live and ``/health`` report ``llm_available: true`` right up until the
        first real request errored (issue #1044). A real (short-timeout) probe
        against ``GET {base_url}/api/tags`` makes an unreachable server or an
        unpulled model correctly fall back to ``CannedBackend``.
        """
        import httpx

        try:
            resp = httpx.get(f"{self._base_url}/api/tags", timeout=3.0)
            resp.raise_for_status()
            tags = {m.get("name", "") for m in resp.json().get("models", [])}
        except Exception:
            return False
        # Ollama tags carry a ":variant" suffix (e.g. "llama3.1:latest"); match
        # the bare name too so LLM_MODEL=llama3.1 matches a pulled default tag.
        return any(tag == self._model or tag.partition(":")[0] == self._model for tag in tags)

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
        # Cost instrumentation (#1217): Ollama reports counts at the top level
        # as prompt_eval_count / eval_count, with no usage block.
        record_llm_call(model=self._served, response=data)
        return data.get("message", {}).get("content", "").strip()


# ── Canned fallback ──────────────────────────────────────────────────


class CannedBackend:
    """Deterministic offline fallback. Explicitly NOT model reasoning."""

    model_id = "canned-fallback"
    served_model = "canned-fallback"

    @property
    def available(self) -> bool:
        return False

    def complete(self, system: str, user: str) -> str:  # noqa: ARG002 — LLM backend interface contract
        return json.dumps(
            {
                "fallback": True,
                "message": "No LLM backend configured. Set LLM_PROVIDER + credentials.",
            }
        )


# ── Factory ──────────────────────────────────────────────────────────


def make_llm_backend(
    model: str | None = None,
) -> (
    AnthropicBackend
    | AnthropicCompatibleBackend
    | BedrockBackend
    | BedrockConverseBackend
    | OpenAIBackend
    | OllamaBackend
    | CannedBackend
):
    """Construct the LLM backend from ``LLM_*`` env vars.

    Provider selection (``LLM_PROVIDER``):
      - ``anthropic``: Anthropic SDK with ``LLM_API_KEY``
      - ``anthropic_compatible``: Anthropic SDK with ``LLM_AUTH_TOKEN`` + ``LLM_BASE_URL``
      - ``bedrock``: Anthropic SDK over AWS Bedrock (IAM auth, no key; ``LLM_BEDROCK_MODEL``)
      - ``bedrock_converse``: Bedrock Converse API over boto3 — ANY provider (Nova/Llama/
        Mistral/DeepSeek/GLM/Kimi/…), IAM auth, no key (``LLM_BEDROCK_MODEL``)
      - ``openai``: httpx to OpenAI-compatible endpoint (``LLM_BASE_URL`` + ``LLM_API_KEY``)
      - ``ollama``: httpx to Ollama (``LLM_BASE_URL``, no key)

    ``model`` is an optional per-call override (e.g. a user's pick from the
    Generate page's model picker). When ``None`` (the default), the model is
    resolved from env exactly as before — behavior is UNCHANGED. When supplied,
    it overrides the resolved model for this backend instance only. Callers are
    responsible for allowlisting untrusted input via :func:`is_allowed_model`
    BEFORE passing it here; this factory does not re-validate the string.

    Back-compat: if ``LLM_PROVIDER`` is unset, falls back to ``ANTHROPIC_*``
    env vars (deprecated, emits WARN).
    """
    resolved_model = model or os.getenv("LLM_MODEL", DEFAULT_MODEL)
    provider = os.getenv("LLM_PROVIDER", "").strip().lower()

    # Back-compat: auto-detect from ANTHROPIC_* if LLM_PROVIDER not set
    if not provider:
        return _legacy_backend(resolved_model)

    # The Bedrock paths resolve their own (Bedrock-specific) model id from
    # LLM_BEDROCK_MODEL and ignore the generic LLM_MODEL. A caller-supplied
    # override IS a Bedrock model id (the picker lists Bedrock ids), so thread
    # it through to those ctors too — otherwise the picker would be inert on the
    # live bedrock_converse path. When `model` is None, ctor(None) preserves the
    # exact env-resolution behavior.
    builders = {
        "anthropic": lambda: AnthropicBackend(model=resolved_model),
        "anthropic_compatible": lambda: AnthropicCompatibleBackend(model=resolved_model),
        "bedrock": lambda: BedrockBackend(model=model),  # Anthropic-SDK path; else resolves its own id
        "bedrock_converse": lambda: BedrockConverseBackend(model=model),  # Converse API — any provider
        "openai": lambda: OpenAIBackend(model=resolved_model),
        "ollama": lambda: OllamaBackend(model=resolved_model),
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
    logger.info("llm: using provider=%s model=%s", provider, getattr(backend, "model_id", resolved_model))
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
