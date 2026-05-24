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
from pathlib import Path

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
