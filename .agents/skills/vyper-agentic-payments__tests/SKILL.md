---
name: vyper-agentic-payments__tests
description: Titanoboa-based test suite for Vyper contracts — comprehensive coverage with mock USDC, time travel, account impersonation
triggers: [vyper-agentic-payments tests, titanoboa, boa, vyper-agentic-payments pytest, vyper-agentic-payments testing]
---

# Tests — Vyper Agentic Payments

**Source**: `vyper-agentic-payments`
**Category**: Core

## When to use this skill
Running or writing tests for the Vyper contracts — titanoboa VM patterns, mock USDC fixtures, challenge verification tests, or integration tests with Flask/x402.

## Key files and folders
- `/home/ricardo/github/vyper-agentic-payments/tests/conftest.py` — Shared fixtures: MockUSDC, MockERC721Receiver, test accounts, `agent_identity`, `agent_reputation`, `funded_usdc`
- `/home/ricardo/github/vyper-agentic-payments/tests/test_spending_limiter.py` — ~25 test cases (deployment, auth, spending, limits, views)
- `/home/ricardo/github/vyper-agentic-payments/tests/test_agent_escrow.py` — ~25 test cases (task lifecycle, disputes, deadlines)
- `/home/ricardo/github/vyper-agentic-payments/tests/test_subscription_manager.py` — ~30 test cases (plans, subscriptions, charging, pause/resume)
- `/home/ricardo/github/vyper-agentic-payments/tests/test_payment_splitter.py` — ~25 test cases (pool creation, claims, management)
- `/home/ricardo/github/vyper-agentic-payments/tests/test_hackathon_challenges.py` — ~30 challenge verification cases (all NotImplementedError until filled)
- `/home/ricardo/github/vyper-agentic-payments/tests/test_sdk_contract_integration.py` — Flask server + x402 middleware + GatewayClient integration (~8 tests)
- `/home/ricardo/github/vyper-agentic-payments/test_smoke.py` — Simple Vyper sanity check
- `/home/ricardo/github/vyper-agentic-payments/run_tests.sh` — Runs each test file individually (avoids titanoboa caching bug)
- `/home/ricardo/github/vyper-agentic-payments/pyproject.toml` — pytest config: `integration`, `challenge`, `real_chain` markers

## Key concepts
- **Titanoboa** (`boa`): Local Vyper VM — compile + execute contracts in Python without a real chain
- **Account impersonation**: `boa.env.prank(address)` to simulate any caller
- **Time travel**: `boa.env.time_travel(seconds)` to advance block.timestamp
- **Mock USDC**: ERC20 with `mint()` for test setup — deployed in conftest.py
- **Caching bug**: Running all tests together triggers titanoboa caching failures — use `run_tests.sh` to run files individually
- **Integration tests**: Use `pytest-asyncio` + Flask + `httpx.AsyncClient` for x402 payment flow testing
- **Markers**: `pytest -m integration` for SDK-dependent tests, `pytest -m challenge` for hackathon verification

## Constraints and rules
- Always run tests via `bash run_tests.sh` (or individual `pytest` per file) — never `pytest tests/` at once
- Integration tests require `circlekit` (circle-titanoboa-sdk) installed
- Challenge tests expect `NotImplementedError` until challenge TODOs are filled
- New contracts should follow conftest.py fixture patterns (MockUSDC + boa.env.prank)

## Related skills
- See `.agents/skills/vyper-agentic-payments__contracts` (contracts these tests verify)
- See `.agents/skills/vyper-agentic-payments__challenges` (hackathon verification tests)
