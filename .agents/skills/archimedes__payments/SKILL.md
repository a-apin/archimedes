---
name: archimedes__payments
description: WS-C wallet module, Circle Developer-Controlled Wallets integration, and payment processing
triggers: [archimedes payments, archimedes wallet, archimedes circle, archimedes wsc]
---

# Archimedes Payments

**Source**: archimedes
**Category**: Integration

## When to use this skill
Working on payment flows, wallet management, Circle SDK integration, or the WS-C wallet module within Archimedes.

## Key files and folders
- **Payments module**: `/home/ricardo/github/archimedes/backend/archimedes/payments/`
- **Circle signer**: `/home/ricardo/github/archimedes/backend/archimedes/chain/circle_signer.py`
- **Circle service**: `/home/ricardo/github/archimedes/backend/archimedes/services/circle_service.py`
- **Wallet setup scripts**: `/home/ricardo/github/archimedes/wallet-setup/`

## Key concepts
- Circle Developer-Controlled Wallets: all on-chain writes go through Circle API (no raw private keys)
- WS-C wallet module: REST endpoints + Pydantic schemas for wallet operations
- USDC is both the gas token and settlement asset on Arc testnet

## Related skills
- See `.agents/skills/arc-canteen-context__circle-docs` for Circle developer platform docs
- See `.agents/skills/shared__arc-blockchain` for Arc testnet chain config
- See `.agents/skills/arc-nanopayments__x402-gateway` for the x402 nanopayment alternative (evaluated, not yet adopted here)
