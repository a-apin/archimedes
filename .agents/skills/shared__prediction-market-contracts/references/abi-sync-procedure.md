# ABI Sync Procedure: On-Chain → Off-Chain

When Solidity contracts in `auction-prediction-market` are modified and redeployed,
the CypherLexicon off-chain backend must be updated to match.

## Step-by-step

1. **Rebuild ABIs** from source:
   ```bash
   cd /home/ricardo/github/auction-prediction-market
   forge build
   ```

2. **Copy ABI files** to CypherLexicon:
   ```bash
   # Find the compiled ABI in out/
   cp out/AuctionManager.sol/AuctionManager.json \
      /home/ricardo/github/CypherLexicon-offchain/backend/contractABIs/
   cp out/PredictionMarket.sol/PredictionMarket.json \
      /home/ricardo/github/CypherLexicon-offchain/backend/contractABIs/
   cp out/MarketFactory.sol/MarketFactory.json \
      /home/ricardo/github/CypherLexicon-offchain/backend/contractABIs/
   cp out/PublishingRightsNFT.sol/PublishingRightsNFT.json \
      /home/ricardo/github/CypherLexicon-offchain/backend/contractABIs/
   ```

3. **Update contract addresses** in `/home/ricardo/github/CypherLexicon-offchain/backend/blockchain.js`

4. **Update event listeners** in `blockchain.js` if new events were added

5. **Update off-chain services** that call contract methods:
   - `/home/ricardo/github/CypherLexicon-offchain/backend/auction/service.js`
   - `/home/ricardo/github/CypherLexicon-offchain/backend/market/service.js`

6. **Test**:
   ```bash
   cd /home/ricardo/github/CypherLexicon-offchain
   pnpm test
   ```

## Contract Addresses

(Managed in each repo's configuration — verify the current deployment from GUIDE.md
or the running backend's config.)
