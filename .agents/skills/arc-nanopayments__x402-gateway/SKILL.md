---
name: arc-nanopayments__x402-gateway
description: x402 protocol integration for nanopayments — Gateway, agent wallet, and @x402/core + @circle-fin/x402-batching usage
triggers: [x402, nanopayments gateway, arc x402, nanopayment protocol]
---

# ARC Nanopayments x402 Gateway

**Source**: arc-nanopayments
**Category**: Core

## When to use this skill
Working on x402 protocol integration: Gateway balance management, micropayment processing, agent wallet setup, or batching.

## Key files and folders
- **x402 library**: `/home/ricardo/github/arc-nanopayments/lib/x402.ts`
- **Gateway proxy**: `/home/ricardo/github/arc-nanopayments/proxy.ts`
- **Agent definition**: `/home/ricardo/github/arc-nanopayments/agent.mts`
- **Wallet generator**: `/home/ricardo/github/arc-nanopayments/generate-wallets.mts`
- **Dashboard gateway controls**: `/home/ricardo/github/arc-nanopayments/components/dashboard/top-bar-gateway-controls.tsx`
- **Gateway balance dialog**: `/home/ricardo/github/arc-nanopayments/components/dashboard/gateway-balance-dialog.tsx`

## Key concepts
- x402: HTTP 402-based protocol for machine-to-machine nanopayments
- Dependencies: `@x402/core`, `@x402/evm`, `@circle-fin/x402-batching`
- Agent wallet (via `@circle-fin/cli`) for automated spending
- Gateway provides unified balance across Circle products
- Batching support for cost-efficient multi-payment scenarios

## Related skills
- See `.agents/skills/arc-nanopayments__wallet-management` — wallet generation and withdrawal
- See `.agents/skills/arc-canteen-context__circle-docs` — Circle Agent Stack and Gateway docs
- See `.agents/skills/shared__arc-blockchain` — Arc chain config for x402 settlement
