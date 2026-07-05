"""Guard tests for issue #130 — the canonical LLMBackend Protocol.

PR for issue #130 deleted the duplicate ``LLMBackend`` / ``ClaudeBackend`` /
``CannedBackend`` definitions that previously lived inline in
``strategy_architect.py`` and ``strategy_fusion.py``. Going forward, the only
``LLMBackend`` Protocol declaration in the backend lives in
``archimedes.services.llm_backend``, and every consumer imports from there.

These tests are a tripwire against re-introducing the duplication. If anyone
adds an inline ``LLMBackend`` / ``CannedBackend`` Protocol declaration in
another module (which historically caused subtle test/prod divergence — the
inline ``CannedBackend`` flavors had slightly different init kwargs), CI fails
fast with a clear pointer at this file.
"""

from __future__ import annotations

import re
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

_BACKEND_DIR = Path(__file__).resolve().parents[2] / "archimedes" / "services"
_AGENTS_DIR = Path(__file__).resolve().parents[2] / "archimedes" / "agents"
_LLM_BACKEND_FILE = _BACKEND_DIR / "llm_backend.py"

# Modules that previously held duplicate Protocol declarations. Even if a future
# refactor moves them, the rule stays the same: nobody else declares LLMBackend.
_HISTORICAL_DUPLICATES = (
    _AGENTS_DIR / "strategy_architect.py",
    _AGENTS_DIR / "strategy_fusion.py",
)

# Any backend service file AND agents file (full sweep — catches new files too).
_ALL_BACKEND_FILES = sorted(_BACKEND_DIR.rglob("*.py")) + sorted(_AGENTS_DIR.rglob("*.py"))


def _grep_class_definitions(path: Path, class_names: tuple[str, ...]) -> list[str]:
    """Return ``{class_name}`` lines that look like top-level class declarations
    (``class Foo(...)``) inside ``path``. Excludes comments and string literals
    well enough for guard-test purposes.
    """
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8")
    hits: list[str] = []
    pattern = re.compile(
        r"^class\s+(?:" + "|".join(re.escape(n) for n in class_names) + r")\b",
        re.MULTILINE,
    )
    for match in pattern.finditer(text):
        hits.append(match.group(0))
    return hits


# ── Canonical-singleton checks ──────────────────────────────────────────────


def test_llm_backend_protocol_declared_exactly_once():
    """The ``LLMBackend`` Protocol must be declared in exactly one place
    (``services/llm_backend.py``) — and nowhere else.
    """
    hits_per_file: dict[str, list[str]] = {}
    for path in _ALL_BACKEND_FILES:
        # Skip __pycache__ / generated files
        if "__pycache__" in path.parts:
            continue
        hits = _grep_class_definitions(path, ("LLMBackend",))
        if hits:
            hits_per_file[str(path.relative_to(_BACKEND_DIR.parent.parent))] = hits

    assert hits_per_file == {"archimedes/services/llm_backend.py": ["class LLMBackend"]}, (
        "LLMBackend Protocol must be declared exactly once, in "
        "services/llm_backend.py. Found:\n" + "\n".join(f"  {p}: {hits}" for p, hits in sorted(hits_per_file.items()))
    )


def test_canned_backend_declared_exactly_once():
    """``CannedBackend`` (the fixture-mode default) must live only in the
    canonical module. Inline duplicates historically caused divergence
    between the architect and fusion code paths.
    """
    hits_per_file: dict[str, list[str]] = {}
    for path in _ALL_BACKEND_FILES:
        if "__pycache__" in path.parts:
            continue
        hits = _grep_class_definitions(path, ("CannedBackend",))
        if hits:
            hits_per_file[str(path.relative_to(_BACKEND_DIR.parent.parent))] = hits

    assert hits_per_file == {"archimedes/services/llm_backend.py": ["class CannedBackend"]}, (
        "CannedBackend must be declared exactly once, in "
        "services/llm_backend.py. Found:\n" + "\n".join(f"  {p}: {hits}" for p, hits in sorted(hits_per_file.items()))
    )


def test_no_claude_backend_class_anywhere():
    """The legacy ``ClaudeBackend`` name was retired by issue #130 in favour of
    the provider-specific ``AnthropicBackend``. No file may re-declare it.
    """
    hits_per_file: dict[str, list[str]] = {}
    for path in _ALL_BACKEND_FILES:
        if "__pycache__" in path.parts:
            continue
        hits = _grep_class_definitions(path, ("ClaudeBackend",))
        if hits:
            hits_per_file[str(path.relative_to(_BACKEND_DIR.parent.parent))] = hits

    assert hits_per_file == {}, (
        "ClaudeBackend was retired by issue #130 (AnthropicBackend is the "
        "canonical name). Do not re-introduce it. Found:\n"
        + "\n".join(f"  {p}: {hits}" for p, hits in sorted(hits_per_file.items()))
    )


# ── Import-path checks ──────────────────────────────────────────────────────


def test_strategy_architect_imports_canonical_llm_backend():
    """``strategy_architect`` must import LLMBackend from the canonical module."""
    text = (_AGENTS_DIR / "strategy_architect.py").read_text(encoding="utf-8")
    assert "from archimedes.services.llm_backend import" in text, (
        "strategy_architect.py must import from archimedes.services.llm_backend"
    )
    assert "LLMBackend" in text, "strategy_architect.py must reference LLMBackend"


def test_strategy_fusion_imports_canonical_llm_backend():
    """``strategy_fusion`` must import LLMBackend from the canonical module."""
    text = (_AGENTS_DIR / "strategy_fusion.py").read_text(encoding="utf-8")
    assert "from archimedes.services.llm_backend import" in text, (
        "strategy_fusion.py must import from archimedes.services.llm_backend"
    )
    assert "LLMBackend" in text, "strategy_fusion.py must reference LLMBackend"


# ── Behavioural checks (the unification has user-visible consequences) ──────


def test_canonical_canned_backend_is_constructible_with_default_kwargs():
    """The unified ``CannedBackend`` must accept zero-arg construction (the
    default-fallback pattern used by both fusion and architect). If anyone
    re-introduces an inline backend with extra required kwargs, the architect
    or fusion fallback path will break — this test catches that early.
    """
    from archimedes.services.llm_backend import CannedBackend

    backend = CannedBackend()
    # Every backend must implement ``model_id`` (the property the fusion
    # module's old inline backend required; #130 confirmed the canonical one
    # has it). Asserting on its presence keeps the Protocol stable.
    assert hasattr(backend, "model_id"), "CannedBackend must expose model_id (required by the unified Protocol)"


def test_make_llm_backend_returns_a_canonical_subclass():
    """The factory returns one of the unified backends — no rogue inline
    implementation slips through ``LLM_PROVIDER`` env routing.
    """
    import os

    from archimedes.services.llm_backend import (
        AnthropicBackend,
        AnthropicCompatibleBackend,
        BedrockBackend,
        BedrockConverseBackend,
        CannedBackend,
        OllamaBackend,
        OpenAIBackend,
        make_llm_backend,
    )

    # Force the canned path so the test is hermetic (no API key required).
    original_provider = os.environ.pop("LLM_PROVIDER", None)
    original_key = os.environ.pop("ANTHROPIC_API_KEY", None)
    original_auth = os.environ.pop("ANTHROPIC_AUTH_TOKEN", None)
    try:
        backend = make_llm_backend()
        assert isinstance(
            backend,
            (
                AnthropicBackend,
                AnthropicCompatibleBackend,
                BedrockBackend,
                BedrockConverseBackend,
                OpenAIBackend,
                OllamaBackend,
                CannedBackend,
            ),
        ), f"make_llm_backend returned a non-canonical type: {type(backend).__name__}"
    finally:
        if original_provider is not None:
            os.environ["LLM_PROVIDER"] = original_provider
        if original_key is not None:
            os.environ["ANTHROPIC_API_KEY"] = original_key
        if original_auth is not None:
            os.environ["ANTHROPIC_AUTH_TOKEN"] = original_auth


# ── Bedrock backend (AWS Bedrock LLM provider) ──────────────────────────────
#
# BedrockBackend uses anthropic.AnthropicBedrock with IAM auth (no API key),
# resolving AWS credentials via boto3. These tests are fully hermetic: they
# inject fake ``boto3`` / ``anthropic`` modules so no real SDK, AWS credentials,
# or network is required — the import boundary inside ``__init__``
# (``import boto3`` / ``from anthropic import AnthropicBedrock``) is what we mock.


def _fake_boto3(creds):
    mod = types.ModuleType("boto3")
    session = MagicMock()
    session.get_credentials.return_value = creds
    mod.Session = MagicMock(return_value=session)
    return mod


def _fake_anthropic(client):
    mod = types.ModuleType("anthropic")
    mod.AnthropicBedrock = MagicMock(return_value=client)
    return mod


def test_bedrock_backend_canned_when_no_credentials():
    """No resolvable AWS credentials → not available (caller falls back to canned)."""
    with patch.dict(sys.modules, {"boto3": _fake_boto3(None), "anthropic": _fake_anthropic(MagicMock())}):
        from archimedes.services.llm_backend import BedrockBackend

        backend = BedrockBackend()
        assert backend.available is False


def test_bedrock_backend_default_model_is_haiku(monkeypatch):
    """The default Bedrock model is Haiku (cheapest tier) when LLM_BEDROCK_MODEL is unset."""
    monkeypatch.delenv("LLM_BEDROCK_MODEL", raising=False)
    with patch.dict(sys.modules, {"boto3": _fake_boto3(None), "anthropic": _fake_anthropic(MagicMock())}):
        from archimedes.services.llm_backend import DEFAULT_BEDROCK_MODEL, BedrockBackend

        backend = BedrockBackend()
        assert backend.model_id == DEFAULT_BEDROCK_MODEL
        assert "haiku" in backend.model_id.lower()


def test_bedrock_backend_available_and_completes_with_mocked_client(monkeypatch):
    """With creds + a mocked AnthropicBedrock client, the backend is available and
    ``complete`` returns the (stripped) model text and tracks the served model.
    """
    monkeypatch.setenv("LLM_BEDROCK_MODEL", "us.anthropic.claude-haiku-4-5-20251001-v1:0")
    response = MagicMock()
    response.content = [MagicMock(text="  HELLO  ")]
    response.model = "claude-haiku-4-5-20251001"
    client = MagicMock()
    client.messages.create.return_value = response

    with patch.dict(sys.modules, {"boto3": _fake_boto3(object()), "anthropic": _fake_anthropic(client)}):
        from archimedes.services.llm_backend import BedrockBackend

        backend = BedrockBackend()
        assert backend.available is True
        assert backend.model_id == "us.anthropic.claude-haiku-4-5-20251001-v1:0"
        assert backend.complete("sys", "user") == "HELLO"
        assert backend.served_model == "claude-haiku-4-5-20251001"
        # The model id handed to the SDK is the Bedrock inference-profile id.
        assert client.messages.create.call_args.kwargs["model"] == "us.anthropic.claude-haiku-4-5-20251001-v1:0"


class _ThinkingBlock:
    """A leading response block with no ``.text`` (thinking/tool_use shape)."""

    def __init__(self, thinking: str) -> None:
        self.thinking = thinking


class _TextBlock:
    def __init__(self, text: str) -> None:
        self.text = text


def test_first_text_block_skips_non_text_and_handles_empty():
    """_first_text_block returns the first block carrying real text, else "" (#930)."""
    from archimedes.services.llm_backend import _first_text_block

    assert _first_text_block([_ThinkingBlock("pondering"), _TextBlock("  hi  ")]) == "hi"
    assert _first_text_block([_ThinkingBlock("only thinking")]) == ""  # no text block at all
    assert _first_text_block([]) == ""
    assert _first_text_block(None) == ""


def test_bedrock_backend_skips_leading_thinking_block(monkeypatch):
    """When the SDK response leads with a thinking block (no ``.text``), the backend
    must iterate to the first real text block instead of AttributeError-ing on
    ``content[0].text`` (#930)."""
    monkeypatch.setenv("LLM_BEDROCK_MODEL", "us.anthropic.claude-haiku-4-5-20251001-v1:0")
    response = MagicMock()
    response.content = [_ThinkingBlock("let me think"), _TextBlock("  ANSWER  ")]
    response.model = "claude-haiku-4-5-20251001"
    client = MagicMock()
    client.messages.create.return_value = response

    with patch.dict(sys.modules, {"boto3": _fake_boto3(object()), "anthropic": _fake_anthropic(client)}):
        from archimedes.services.llm_backend import BedrockBackend

        backend = BedrockBackend()
        assert backend.complete("sys", "user") == "ANSWER"


def test_make_llm_backend_selects_bedrock(monkeypatch):
    """LLM_PROVIDER=bedrock routes the factory to BedrockBackend (creds present)."""
    monkeypatch.setenv("LLM_PROVIDER", "bedrock")
    with patch.dict(sys.modules, {"boto3": _fake_boto3(object()), "anthropic": _fake_anthropic(MagicMock())}):
        from archimedes.services.llm_backend import BedrockBackend, make_llm_backend

        backend = make_llm_backend()
        assert isinstance(backend, BedrockBackend)
        assert backend.available is True


# ── Bedrock Converse backend (multi-provider, uniform Converse API) ─────────


def _fake_boto3_converse(creds, converse_return=None):
    """Fake boto3 whose client('bedrock-runtime').converse returns a canned response.
    The created client is exposed as mod._client for call-arg assertions."""
    mod = types.ModuleType("boto3")
    session = MagicMock()
    session.get_credentials.return_value = creds
    mod.Session = MagicMock(return_value=session)
    client = MagicMock()
    client.converse.return_value = converse_return
    mod.client = MagicMock(return_value=client)
    mod._client = client
    return mod


def test_bedrock_converse_canned_when_no_credentials():
    with patch.dict(sys.modules, {"boto3": _fake_boto3_converse(None)}):
        from archimedes.services.llm_backend import BedrockConverseBackend

        assert BedrockConverseBackend().available is False


def test_bedrock_converse_default_model_is_nova_micro(monkeypatch):
    """Default Converse model is Amazon Nova Micro (cheapest, no Anthropic form)."""
    monkeypatch.delenv("LLM_BEDROCK_MODEL", raising=False)
    with patch.dict(sys.modules, {"boto3": _fake_boto3_converse(None)}):
        from archimedes.services.llm_backend import DEFAULT_CONVERSE_MODEL, BedrockConverseBackend

        backend = BedrockConverseBackend()
        assert backend.model_id == DEFAULT_CONVERSE_MODEL
        assert "nova-micro" in backend.model_id


def test_bedrock_converse_available_and_completes(monkeypatch):
    monkeypatch.setenv("LLM_BEDROCK_MODEL", "amazon.nova-micro-v1:0")
    resp = {"output": {"message": {"content": [{"text": "  HELLO  "}]}}}
    fake = _fake_boto3_converse(object(), converse_return=resp)
    with patch.dict(sys.modules, {"boto3": fake}):
        from archimedes.services.llm_backend import BedrockConverseBackend

        backend = BedrockConverseBackend()
        assert backend.available is True
        assert backend.complete("be terse", "hi") == "HELLO"
        kwargs = fake._client.converse.call_args.kwargs
        assert kwargs["modelId"] == "amazon.nova-micro-v1:0"
        assert kwargs["system"] == [{"text": "be terse"}]  # system passed as a Converse system block


def test_bedrock_converse_skips_reasoning_block_and_omits_empty_system():
    """Reasoning models emit a reasoningContent block before text; return the text.
    And an empty system must NOT be sent as a Converse system block."""
    resp = {"output": {"message": {"content": [{"reasoningContent": {"x": 1}}, {"text": "ANSWER"}]}}}
    fake = _fake_boto3_converse(object(), converse_return=resp)
    with patch.dict(sys.modules, {"boto3": fake}):
        from archimedes.services.llm_backend import BedrockConverseBackend

        assert BedrockConverseBackend().complete("", "q") == "ANSWER"
        assert "system" not in fake._client.converse.call_args.kwargs


def test_make_llm_backend_selects_bedrock_converse(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "bedrock_converse")
    resp = {"output": {"message": {"content": [{"text": "ok"}]}}}
    with patch.dict(sys.modules, {"boto3": _fake_boto3_converse(object(), converse_return=resp)}):
        from archimedes.services.llm_backend import BedrockConverseBackend, make_llm_backend

        backend = make_llm_backend()
        assert isinstance(backend, BedrockConverseBackend)
        assert backend.available is True
