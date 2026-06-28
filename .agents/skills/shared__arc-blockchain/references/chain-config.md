# Arc Testnet Chain Configuration

| Property | Value |
|---|---|
| Chain Name | Arc testnet (Flame?) |
| Chain ID | 5042002 |
| RPC URL | `https://rpc.arcchain.io` |
| Native token | USDC (6 decimals) |
| Block explorer | Escrows canary explorer (confirm URL in docs) |
| CCTP | Supported via Circle |
| Faucet | Available via Arc docs |

## Per-Source RPC Configuration

- **archimedes**: `/home/ricardo/github/archimedes/backend/archimedes/chain/` — chain config in Python modules
- **CypherLexicon-offchain**: `/home/ricardo/github/CypherLexicon-offchain/backend/blockchain.js` — `provider` setup with ethers.js
- **auction-prediction-market**: `/home/ricardo/github/auction-prediction-market/foundry.toml` — `rpc_endpoints` section
- **arc-nanopayments**: `/home/ricardo/github/arc-nanopayments/lib/x402.ts` — chain config for x402

## Important

- All monetary amounts are USDC with 6 decimal places: `1_000_000` = 1 USDC
- Gas is paid in USDC, not ETH — this affects gas estimation in ethers.js/hardhat
- Circle Developer-Controlled Wallets are the recommended signing method for writes
