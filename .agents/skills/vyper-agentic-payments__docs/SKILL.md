---
name: vyper-agentic-payments__docs
description: Architecture documentation, contract specs, and workshop guide for the Vyper agentic payment system
triggers: [vyper-agentic-payments docs, architecture, vyper-agentic-payments workshop, vyper-agentic-payments contracts documentation]
---

# Documentation — Vyper Agentic Payments

**Source**: `vyper-agentic-payments`
**Category**: CrossCutting

## When to use this skill
Understanding the system architecture — the 5-layer design, data flows, security model, contract invariants, or workshop guide. Also use when you need architectural context before writing code.

## Key files and folders
- `/home/ricardo/github/vyper-agentic-payments/docs/architecture.md` — 5-layer architecture (Blockchain → Settlement → Payment Protocol → Governance → Application), data flow diagrams, security model, invariants, gas comparison
- `/home/ricardo/github/vyper-agentic-payments/docs/workshop.md` — Step-by-step workshop guide (~30 min sessions), 3 advanced patterns, 3 exercises
- `/home/ricardo/github/vyper-agentic-payments/docs/contracts/README.md` — Contract overview table, deployment order, common patterns
- `/home/ricardo/github/vyper-agentic-payments/docs/contracts/AgentEscrow.md` — Escrow contract spec
- `/home/ricardo/github/vyper-agentic-payments/docs/contracts/SpendingLimiter.md` — Spending limiter spec
- `/home/ricardo/github/vyper-agentic-payments/docs/contracts/PaymentSplitter.md` — Payment splitter spec
- `/home/ricardo/github/vyper-agentic-payments/docs/contracts/SubscriptionManager.md` — Subscription manager spec

## Key concepts
- **5-layer architecture**: Arc blockchain → Circle Gateway settlement → x402 SDK → Vyper governance contracts → Application layer
- **Security model**: Admin vs agent vs public function access per contract
- **Invariants**: USDC conservation (total deposits = sum of balances + spent), escrow safety, spending limit enforcement
- **Gas comparison**: Vyper vs Solidity — data on deployment and execution costs
- **Workshop structure**: 3 sessions covering x402 integration, full agent workflow, and advanced patterns

## Constraints and rules
- `docs/architecture.md` is the canonical architecture reference
- `docs/contracts/` docs focus on contract-level specs — read alongside the Vyper source
- `docs/workshop.md` is the facilitator guide for hackathon workshops

## Related skills
- See `.agents/skills/vyper-agentic-payments__contracts` (contracts documented in these docs)
- See `.agents/skills/vyper-agentic-payments__challenges` (workshop challenges complementing the workshop guide)
