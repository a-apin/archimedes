// feed-price.mjs
//
// Fetches prices for all assets and pushes them to their oracles on Arc Testnet.
//
// Usage: node --env-file=../.env feed-price.mjs

import crypto from "crypto";

const API_KEY = process.env.CIRCLE_API_KEY;
const ENTITY_SECRET = process.env.CIRCLE_ENTITY_SECRET;
const WALLET_ID = process.env.WALLET_ID;

if (!API_KEY || !ENTITY_SECRET || !WALLET_ID) {
  console.error("ERROR: Need CIRCLE_API_KEY, CIRCLE_ENTITY_SECRET, WALLET_ID");
  process.exit(1);
}

const API = "https://api.circle.com/v1/w3s";

const ASSETS = [
  { id: "TSLA",   env: "TSLA_ORACLE",   yahoo: "TSLA"    },
  { id: "NVDA",   env: "NVDA_ORACLE",   yahoo: "NVDA"    },
  { id: "SPY",    env: "SPY_ORACLE",     yahoo: "SPY"     },
  { id: "BTC",    env: "BTC_ORACLE",     yahoo: "BTC-USD" },
  { id: "GOLD",   env: "GOLD_ORACLE",    yahoo: "GLD"     },
  { id: "OIL",    env: "OIL_ORACLE",     yahoo: "USO"     },
  { id: "NIKKEI", env: "NIKKEI_ORACLE",  yahoo: "EWJ"     },
];

async function getCiphertext() {
  const pkRes = await fetch(`${API}/config/entity/publicKey`, {
    headers: { Authorization: `Bearer ${API_KEY}`, Accept: "application/json" },
  });
  const { data } = await pkRes.json();
  const pub = crypto.createPublicKey({ key: data.publicKey, format: "pem" });
  return crypto.publicEncrypt(
    { key: pub, padding: crypto.constants.RSA_PKCS1_OAEP_PADDING, oaepHash: "sha256" },
    Buffer.from(ENTITY_SECRET, "hex")
  ).toString("base64");
}

async function fetchPrice(yahoo) {
  const url = `https://query1.finance.yahoo.com/v8/finance/chart/${yahoo}?interval=1d&range=1d`;
  const res = await fetch(url, { headers: { "User-Agent": "Mozilla/5.0" } });
  if (!res.ok) throw new Error(`Yahoo ${res.status} for ${yahoo}`);
  const data = await res.json();
  const price = data.chart?.result?.[0]?.meta?.regularMarketPrice;
  if (!price) throw new Error(`No price for ${yahoo}`);
  return price;
}

async function pushPrice(oracleAddress, priceInt) {
  const entitySecretCiphertext = await getCiphertext();
  const res = await fetch(`${API}/developer/transactions/contractExecution`, {
    method: "POST",
    headers: { Authorization: `Bearer ${API_KEY}`, "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify({
      idempotencyKey: crypto.randomUUID(),
      walletId: WALLET_ID,
      contractAddress: oracleAddress,
      abiFunctionSignature: "setPrice(uint256)",
      abiParameters: [priceInt.toString()],
      feeLevel: "MEDIUM",
      entitySecretCiphertext,
    }),
  });
  const d = await res.json();
  if (res.status >= 400) throw new Error(`API ${res.status}: ${JSON.stringify(d)}`);
  return d.data?.id;
}

async function main() {
  console.log("=== Feed All Oracle Prices ===\n");

  for (const asset of ASSETS) {
    const oracleAddress = process.env[asset.env];
    if (!oracleAddress) {
      console.log(`  ${asset.id}: SKIP (no ${asset.env} in .env)`);
      continue;
    }

    try {
      const price = await fetchPrice(asset.yahoo);
      const priceInt = Math.round(price * 1_000_000);
      const txId = await pushPrice(oracleAddress, priceInt);
      console.log(`  ${asset.id}: $${price.toFixed(2)} → ${priceInt} (tx: ${txId})`);
    } catch (err) {
      console.log(`  ${asset.id}: ❌ ${err.message}`);
    }
  }
}

main().catch((err) => {
  console.error("Failed:", err.message || err);
  process.exit(1);
});
