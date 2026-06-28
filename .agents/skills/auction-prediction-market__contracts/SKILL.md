---
name: auction-prediction-market__contracts
description: Four core Solidity contracts — AuctionManager, PredictionMarket, MarketFactory, PublishingRightsNFT
triggers: [auction prediction contracts, auction solidity, prediction market solidity, market factory, publishing rights nft]
---

# Auction Prediction Market Contracts

**Source**: auction-prediction-market
**Category**: Core

## When to use this skill
Working on the on-chain Solidity contracts: modifying AuctionManager, PredictionMarket, MarketFactory, or PublishingRightsNFT.

## Key files and folders
- **Contracts source**: `/home/ricardo/github/auction-prediction-market/src/`
- **AuctionManager.sol**: `/home/ricardo/github/auction-prediction-market/src/AuctionManager.sol` — manages term auctions, proposal submission, winner resolution
- **PredictionMarket.sol**: `/home/ricardo/github/auction-prediction-market/src/PredictionMarket.sol` — binary outcome market (YES/NO shares)
- **MarketFactory.sol**: `/home/ricardo/github/auction-prediction-market/src/MarketFactory.sol` — creates PredictionMarket instances
- **PublishingRightsNFT.sol**: `/home/ricardo/github/auction-prediction-market/src/PublishingRightsNFT.sol` — ERC-721 for won term publishing rights
- **MockUSDC.sol (test)**: `/home/ricardo/github/auction-prediction-market/test/helpers/MockUSDC.sol` — test token
- **Tests**: `/home/ricardo/github/auction-prediction-market/test/`
- **Deploy script**: `/home/ricardo/github/auction-prediction-market/script/Deploy.s.sol`
- **lib/**: `/home/ricardo/github/auction-prediction-market/lib/` — OpenZeppelin + Forge Std

## Key concepts
- **Lifecycle**: Auction runs → Winning term selected → Prediction market created → Winners claim YES/NO → PublishingRightsNFT minted to winner
- **AuctionManager**: accepts proposals, AI scoring is submitted, winner resolved
- **PredictionMarket**: binary outcomes, USDC (6 decimals) as collateral
- **MarketFactory**: deployer pattern for PredictionMarket instances
- **PublishingRightsNFT**: soul-bound to winning term, minted after market resolution

## Constraints and rules
- Solidity 0.8.19 with via-IR compilation, optimizer 200 runs
- OpenZeppelin v4.9+ for ERC-721, Ownable, ReentrancyGuard
- All amounts in USDC with 6 decimal places
- Events must be emitted for all state changes

## Related skills
- See `.agents/skills/auction-prediction-market__deployment-testing` — Foundry tests and deploy scripts
- See `.agents/skills/shared__prediction-market-contracts` — shared interfaces and lifecycle knowledge
- See `.agents/skills/shared__arc-blockchain` — Arc testnet deployment config
- See `.agents/skills/cypherlexicon-offchain__blockchain-layer` — consumer of these contracts
