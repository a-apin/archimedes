---
name: vyper-agentic-payments__examples
description: Agent marketplace example — Flask server with x402 paywall, GatewayClient buyer, and ERC-8004 deposit script
triggers: [vyper-agentic-payments examples, agent-marketplace, x402, vyper-agentic-payments server, vyper-agentic-payments client, vyper-agentic-payments Flask]
---

# Examples — Vyper Agentic Payments

**Source**: `vyper-agentic-payments`
**Category**: Integration

## When to use this skill
Working with the agent-marketplace example — the Flask server with x402 payment middleware, the async GatewayClient buyer, or the deposit script. This is the reference implementation for the full payment flow.

## Key files and folders
- `/home/ricardo/github/vyper-agentic-payments/examples/agent-marketplace/README.md` — Example documentation
- `/home/ricardo/github/vyper-agentic-payments/examples/agent-marketplace/server.py` — Flask server: rate-limited endpoints, x402 payment verification middleware, boa VM contract interaction, ERC-8004 agent identity check
- `/home/ricardo/github/vyper-agentic-payments/examples/agent-marketplace/client.py` — Async GatewayClient buyer: fetches quotes, makes payments, checks balances, reusable across agents
- `/home/ricardo/github/vyper-agentic-payments/examples/agent-marketplace/deposit.py` — USDC deposit script using Gateway deposit address
- `/home/ricardo/github/vyper-agentic-payments/examples/agent-marketplace/.env.example` — Example env vars for the marketplace

## Key concepts
- **x402 payment flow**: Client fetches quote (402 + PaymentRequirements) → signs + submits → server verifies via Gateway facilitator → serves content
- **Flask middleware pattern**: `@app.before_request` handler checks x402 headers, verifies payment via GatewayClient
- **ERC-8004 agent identity**: Server validates caller has a valid agent identity NFT before serving gated content
- **GatewayClient**: Async HTTP client wrapping Circle Gateway API (`/v1/x402/settle`, `/v1/x402/quote`)
- **Boa VM integration**: Server loads Vyper contracts via `boa.load()` for on-chain interactions
- **Rate limiting**: Server-side per-wallet rate limiting for x402 requests

## Constraints and rules
- Requires Circle API credentials in `.env` (`CIRCLE_API_KEY`, `CIRCLE_ENTITY_SECRET`)
- Server runs on port 4021 by default (configurable via `SERVER_URL`)
- USDC on Arc testnet — test funds from Circle faucet (faucet.circle.com)
- The example is a reference — not production-ready (no auth, no persistence)

## Related skills
- See `.agents/skills/vyper-agentic-payments__contracts` (contracts the server interacts with)
- See `.agents/skills/vyper-agentic-payments__tests` (integration test that validates this flow)
- See `.agents/skills/shared__arc-blockchain` (Arc testnet, USDC)
