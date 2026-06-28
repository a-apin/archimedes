---
name: vyper-agentic-payments__challenges
description: Three-track hackathon workshop (13 challenges) — Vyper basics, Circle integration, and advanced payment primitives
triggers: [vyper-agentic-payments challenges, hackathon, vyper-agentic-payments workshop, vyper-agentic-payments track, challenge]
---

# Challenges — Vyper Agentic Payments

**Source**: `vyper-agentic-payments`
**Category**: Domain

## When to use this skill
Working on hackathon challenges — understanding challenge specs, implementing TODO templates, validating challenge completion, or running the workshop.

## Key files and folders
- `/home/ricardo/github/vyper-agentic-payments/challenges/README.md` — Full challenge overview and track descriptions
- `/home/ricardo/github/vyper-agentic-payments/challenges.md` — Consolidated challenge documentation
- Track A — Vyper Basics (4 challenges):
  - `track_a/a1_environment_setup/` — Instruction-only (no code)
  - `track_a/a2_first_contract/` — Deploy a Vault contract (`challenge.py` template)
  - `track_a/a3_test_suite/` — Write pytest tests for Vault (`challenge.py` template)
  - `track_a/a4_erc8004_agent/` — ERC-8004 agent identity NFT integration (`challenge.py` template)
- Track B — Circle Integration (4 challenges):
  - `track_b/b1_api_key/` — Instruction-only (Circle API key setup)
  - `track_b/b2_programmable_wallet/` — Instruction-only (wallet setup)
  - `track_b/b3_deploy_from_wallet/` — Deploy from Circle wallet (`challenge.py` template)
  - `track_b/b4_x402_payment/` — x402 payment integration (`challenge.py` template)
- Track C — Advanced Primitives (5 challenges):
  - `track_c/c1_spending_limiter/` — SpendingLimiter contract (`challenge.py` template)
  - `track_c/c2_agent_escrow/` — AgentEscrow contract (`challenge.py` template)
  - `track_c/c3_subscription_manager/` — SubscriptionManager contract (`challenge.py` template)
  - `track_c/c4_payment_splitter/` — PaymentSplitter contract (`challenge.py` template)
  - `track_c/c5_payment_channel/` — PaymentChannel (bonus, `challenge.py` template)
- `/home/ricardo/github/vyper-agentic-payments/tests/test_hackathon_challenges.py` — Verification tests for all 13 challenges

## Key concepts
- **3 tracks**: A (pure Vyper, no external deps), B (Circle API integration), C (advanced on-chain)
- **Challenge pattern**: Each has a `README.md` (spec) + optional `challenge.py` (TODO template)
- **Verification**: `test_hackathon_challenges.py` validates all implemented challenges
- **Instruction-only challenges** (A1, B1, B2): README with setup steps, no code template
- **Progression**: Each track builds on previous — Track A teaches Vyper, Track B adds Circle, Track C combines into advanced primitives

## Constraints and rules
- Challenge templates raise `NotImplementedError` — replace with real implementations
- Run `pytest tests/test_hackathon_challenges.py -v -m challenge` to verify progress
- Track B requires real Circle API credentials (set in `.env`)
- Track A4 requires the `erc-8004-vyper` Moccasin dependency

## Related skills
- See `.agents/skills/vyper-agentic-payments__contracts` (contracts used in challenges)
- See `.agents/skills/vyper-agentic-payments__tests` (challenge verification tests)
- See `.agents/skills/vyper-agentic-payments__examples` (agent-marketplace reference for Track B/C)
