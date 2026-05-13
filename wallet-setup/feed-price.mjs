// feed-price.mjs
//
// Fetches TSLA price and pushes it to the oracle on Arc Testnet.
//
// Usage: node --env-file=../.env feed-price.mjs

import { initiateDeveloperControlledWalletsClient } from "@circle-fin/developer-controlled-wallets";

const API_KEY = process.env.CIRCLE_API_KEY;
const ENTITY_SECRET = process.env.CIRCLE_ENTITY_SECRET;
const WALLET_ID = process.env.WALLET_ID;
const ORACLE_ADDRESS = process.env.ORACLE_ADDRESS;

if (!API_KEY || !ENTITY_SECRET || !WALLET_ID) {
  console.error("ERROR: Need CIRCLE_API_KEY, CIRCLE_ENTITY_SECRET, WALLET_ID");
  process.exit(1);
}

if (!ORACLE_ADDRESS) {
  console.error("ERROR: ORACLE_ADDRESS not set in .env");
  console.error("Run 'make deploy' first, then add ORACLE_ADDRESS to .env");
  process.exit(1);
}

const client = initiateDeveloperControlledWalletsClient({
  apiKey: API_KEY,
  entitySecret: ENTITY_SECRET,
});

async function fetchTSLAPrice() {
  const url = "https://query1.finance.yahoo.com/v8/finance/chart/TSLA?interval=1d&range=1d";
  const res = await fetch(url, {
    headers: { "User-Agent": "Mozilla/5.0" },
  });
  if (!res.ok) throw new Error(`Yahoo API returned ${res.status}`);
  const data = await res.json();
  const price = data.chart?.result?.[0]?.meta?.regularMarketPrice;
  if (!price) throw new Error("Could not parse price");
  return price;
}

async function main() {
  const price = await fetchTSLAPrice();
  const priceInt = Math.round(price * 1_000_000); // 6 decimals

  console.log(`TSLA: $${price.toFixed(2)} → ${priceInt}`);

  const txRes = await client.createContractExecutionTransaction({
    walletId: WALLET_ID,
    contractAddress: ORACLE_ADDRESS,
    abiFunctionSignature: "setPrice(uint256)",
    abiParameters: [priceInt.toString()],
    fee: { type: "level", config: { feeLevel: "MEDIUM" } },
  });

  console.log(`✅ Submitted! txId: ${txRes.data?.transactionId}`);
}

main().catch((err) => {
  console.error("Failed:", err.message || err);
  process.exit(1);
});
