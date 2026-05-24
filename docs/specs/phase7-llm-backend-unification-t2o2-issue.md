# Issue spec for t2o2 — Unify LLM backends on `llm_backend.py`

> **Status:** ✓ filed + closed as [#130](https://github.com/a-apin/archimedes-arcadia/issues/130) on 2026-05-23 — shipped on `main` (commit `dc91b43`). This file is the spec source-of-truth; PR archive lives on the issue.
>
> **Ready-to-file issue body.** Per CLAUDE.md's agentic issue pipeline.
>
> **Why this exists:** `services/llm_backend.py` defines the canonical
> `LLMBackend` Protocol + provider implementations (Anthropic, Anthropic-
> compatible, OpenAI, Ollama, Canned fallback). BUT two other modules
> (`strategy_architect.py`, `strategy_fusion.py`) **each define their own
> parallel** `LLMBackend` Protocol + `ClaudeBackend` + `CannedBackend` —
> three copies of the same abstraction. The new `portfolio_agent.py`
> (Day-10) correctly uses `llm_backend.py`, and the Phase 2 `_validate_brief`
> step also uses it — so canonical usage is already established. This issue
> migrates the two stragglers.

---

## APIN - Backend - Unify all LLM backend usage on `services/llm_backend.py`

## Summary

Three parallel `LLMBackend` Protocol + provider implementations exist:

| File | Lines | Implements |
|---|---|---|
| `services/llm_backend.py` | 307 | **Canonical.** AnthropicBackend, AnthropicCompatibleBackend, OpenAIBackend, OllamaBackend, CannedBackend. `make_llm_backend()` factory. |
| `services/strategy_architect.py` | 411 | Owns its own `LLMBackend` Protocol + `ClaudeBackend` + `CannedBackend`. |
| `services/strategy_fusion.py` | 650 | Owns its own `LLMBackend` Protocol + `ClaudeBackend` + `CannedBackend`. |

This issue: delete the duplicate Protocol+backends from `strategy_architect.py`
and `strategy_fusion.py`. Both modules import from `llm_backend.py` going
forward.

Confirmed-clean reference patterns:
- `services/portfolio_agent.py` already uses `llm_backend.py` via `make_llm_backend()`.
- `services/generation_pipeline.py::_validate_brief` (Phase 2 follow-up) uses
  the same pattern.

## Scope (exact files)

Files to modify:
- `backend/archimedes/services/strategy_architect.py` — delete the inline
  `LLMBackend`, `ClaudeBackend`, `CannedBackend`. Import from
  `archimedes.services.llm_backend`. Any caller-facing signature (`propose`,
  the result dataclasses) MUST stay identical.
- `backend/archimedes/services/strategy_fusion.py` — same migration. The
  fusion module's `LLMBackend` was slightly different (it carried a
  `model_id` property the architect's didn't); confirm the canonical
  Protocol exposes the same property, add if missing.
- `backend/archimedes/services/llm_backend.py` — add a `model_id`
  property to the Protocol IF and ONLY IF fusion's existing Protocol has
  it and the canonical one doesn't. (As of 2026-05-22 both have it; verify.)

Files to add a thin test for:
- `backend/tests/services/test_llm_backend_unification.py` — assert that
  `strategy_architect` and `strategy_fusion` use the canonical Protocol
  by checking `isinstance(architect._backend, LLMBackend)` (the canonical
  one from `llm_backend.py`).

Files to NOT touch:
- `services/portfolio_agent.py`, `services/generation_pipeline.py` — already
  canonical.
- Any frontend file. The migration is pure backend internals.

## Acceptance criteria

- [ ] **Only one `LLMBackend` Protocol class declaration in the backend:**
  `grep -rn "^class LLMBackend" backend/` → exactly one line, pointing at
  `services/llm_backend.py`.
- [ ] **No duplicate ClaudeBackend/CannedBackend classes:**
  `grep -rn "^class ClaudeBackend\|^class CannedBackend" backend/` → at most
  one line each (in `services/llm_backend.py`).
- [ ] **All tests still green:**
  `pytest -q --deselect backend/tests/test_api_routes.py::TestAgentRoutes::test_agent_status_redis_down_defaults --deselect backend/tests/test_api_routes.py::TestAdvisorRoutes::test_advisor_redis_unavailable`
  → 307+ passed.
- [ ] **Existing canned/fixture tests in architect + fusion still pass** —
  the unified `CannedBackend` from `llm_backend.py` must accept the same
  init kwargs and produce the same fixture-mode output.
- [ ] **`make_llm_backend()` is used in production paths only** — fixtures
  and tests inject backends directly, never via the factory. No regression
  on test isolation.

## Verify

```bash
grep -rn "^class LLMBackend\|^class ClaudeBackend\|^class CannedBackend" backend/
# Expect: exactly the three in services/llm_backend.py
pytest -q backend/tests/services/test_strategy_architect.py backend/tests/services/test_strategy_fusion.py
# Should pass without any modifications to the test files themselves.
```

## Anti-goals

- Do NOT broaden scope to add new backends (OpenRouter, etc.) — separate issue.
- Do NOT change the `propose()` / fusion result dataclasses — they are
  the frontend contract.
- Do NOT remove the `CannedBackend` from `llm_backend.py`. It's the
  test-default and is wired into the factory's fallback.
- Do NOT alter `LLM_PROVIDER` env var semantics.

## Precedent / shape to copy

- For the import migration shape, look at how
  `services/portfolio_agent.py::PortfolioAgent.__init__` accepts
  `backend: LLMBackend | None = None` and defaults via `make_llm_backend()`.
  Architect and fusion should follow that exact constructor shape.
- For the fixture-injection test pattern, copy
  `backend/tests/services/test_strategy_architect.py::test_proposes_with_canned_backend`.

## Out of scope

- Provider extensions (Bedrock, Vertex AI) — file a new issue if needed.
- Streaming/tool-use abstractions at the LLMBackend layer (portfolio_agent
  uses the raw Anthropic SDK directly because the Protocol seam is text-in-
  text-out only; widening the Protocol is a separate design call).

## Owner suggestion

t2o2 executes. Daniel R. or Dan reviews — the changes touch two modules
they both have context on.
