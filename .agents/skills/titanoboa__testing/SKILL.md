---
name: titanoboa__testing
description: Pytest plugin, Vyper code coverage, Hypothesis fuzzing strategies, test fixture isolation
---

# Testing Infrastructure

**Source**: titanoboa
**Category**: Infrastructure

## When to use this skill
Writing or running Titanoboa tests, using the pytest plugin, measuring Vyper code coverage, or generating fuzz tests with Hypothesis.

## Key files and folders
- `/home/ricardo/github/titanoboa/boa/test/plugin.py` — Pytest plugin: fixture isolation, gas profiling markers
- `/home/ricardo/github/titanoboa/boa/test/strategies.py` — Hypothesis fuzzing strategies for Vyper types
- `/home/ricardo/github/titanoboa/boa/coverage.py` — `TitanoboaPlugin` for coverage.py, `CoverageTracer`
- `/home/ricardo/github/titanoboa/tests/unitary/` — Unit tests (no RPC needed)
- `/home/ricardo/github/titanoboa/tests/integration/` — Integration tests (need RPC endpoints)
- `/home/ricardo/github/titanoboa/tests/unitary/stateful/` — Stateful test isolation tests
- `/home/ricardo/github/titanoboa/tests/unitary/strategy/` — Hypothesis strategy tests

## Key concepts
- **Pytest plugin**: auto-loaded via `setup.cfg` entry point. Provides `boa.env` fixture isolation and `@pytest.mark.gas_profile` marker.
- **Coverage**: `TitanoboaPlugin` hooks into `coverage.py` to measure Vyper source line/branch coverage via JUMPI opcode tracing.
- **Fuzzing**: Hypothesis strategies for `address`, `uint256`, `int256`, `bytes`, `bytes32`, arrays, and string types.

## Running tests
```bash
# Unit tests only
cd /home/ricardo/github/titanoboa && make test

# Integration (requires RPC env vars)
cd /home/ricardo/github/titanoboa && make test-integration
```

## Related skills
- See `.agents/skills/titanoboa__core-api` — the API under test
- See `.agents/skills/titanoboa__evm-layer` — opcode tracing used by coverage
