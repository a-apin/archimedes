---
name: arc-nanopayments__wallet-management
description: Wallet generation, withdrawal processing, and balance management for ARC nanopayments
triggers: [nanopayments wallet, arc wallet generation, nanopayments withdraw, arc wallet balance]
---

# ARC Nanopayments Wallet Management

**Source**: arc-nanopayments
**Category**: Core

## When to use this skill
Generating wallets, processing withdrawals, managing wallet balances, or working with the Circle Wallet SDK in the nanopayments app.

## Key files and folders
- **Wallet generator**: `/home/ricardo/github/arc-nanopayments/generate-wallets.mts`
- **Withdraw dialog**: `/home/ricardo/github/arc-nanopayments/components/dashboard/withdraw-dialog.tsx`
- **Withdrawal hook**: `/home/ricardo/github/arc-nanopayments/hooks/use-withdrawals.ts`
- **Transaction hook**: `/home/ricardo/github/arc-nanopayments/hooks/use-transactions.ts`
- **Supabase client**: `/home/ricardo/github/arc-nanopayments/lib/supabase/client.ts`

## Key concepts
- Wallets generated via Circle Developer-Controlled Wallets API
- Withdrawals go through dashboard with confirmation dialog
- Transaction history tracked via Supabase

## Related skills
- See `.agents/skills/arc-nanopayments__x402-gateway` — gateway balance and spending
- See `.agents/skills/arc-nanopayments__supabase-auth` — wallet association with user accounts
- See `.agents/skills/arc-canteen-context__circle-docs` — Circle wallets documentation
