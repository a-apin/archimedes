// create-wallet.mjs
//
// Creates a Circle Dev-Controlled Wallet on Arc Testnet.
// Idempotent: skips if WALLET_ID already set in .env.
//
// Usage: node --env-file=../.env create-wallet.mjs

import fs from "node:fs";
import path from "node:path";

import { initiateDeveloperControlledWalletsClient } from "@circle-fin/developer-controlled-wallets";

const API_KEY = process.env.CIRCLE_API_KEY;
const ENTITY_SECRET = process.env.CIRCLE_ENTITY_SECRET;
const EXISTING_WALLET = process.env.WALLET_ID;

if (!API_KEY || !ENTITY_SECRET) {
  console.error("ERROR: Need CIRCLE_API_KEY + CIRCLE_ENTITY_SECRET in ../.env");
  console.error("Run: make register");
  process.exit(1);
}

if (EXISTING_WALLET) {
  console.log(`Wallet already exists: ${EXISTING_WALLET}`);
  console.log(`Address: ${process.env.WALLET_ADDRESS}`);
  console.log("To create a new one, remove WALLET_ID/WALLET_ADDRESS from .env.");
  process.exit(0);
}

const client = initiateDeveloperControlledWalletsClient({
  apiKey: API_KEY,
  entitySecret: ENTITY_SECRET,
});

async function main() {
  console.log("=== Create Arc Testnet Wallet ===\n");

  console.log("Creating wallet set...");
  const walletSetRes = await client.createWalletSet({ name: "Archimedes Arc" });
  const walletSetId = walletSetRes.data?.walletSet?.id;
  console.log(`Wallet Set ID: ${walletSetId}\n`);

  console.log("Creating SCA wallet on Arc Testnet...");
  const walletRes = await client.createWallets({
    walletSetId,
    blockchains: ["ARC-TESTNET"],
    count: 1,
    accountType: "SCA",
  });

  const wallet = walletRes.data?.wallets?.[0];
  if (!wallet) {
    console.error("Failed:", JSON.stringify(walletRes, null, 2));
    process.exit(1);
  }

  console.log("✅ Wallet created!");
  console.log(`   Wallet ID:    ${wallet.id}`);
  console.log(`   Address:      ${wallet.address}`);
  console.log(`   Blockchain:   ${wallet.blockchain}\n`);

  console.log("Requesting testnet USDC from faucet...");
  try {
    const faucetRes = await client.requestTestnetTokens({
      walletId: wallet.id,
      blockchain: "ARC-TESTNET",
      usdc: true,
    });
    console.log(`✅ Faucet: ${faucetRes.data?.transactionId}`);
  } catch {
    console.log("⚠️  API faucet failed. Use: make fund");
  }

  const envPath = path.resolve(import.meta.dirname, "../.env");
  let envContent = fs.existsSync(envPath) ? fs.readFileSync(envPath, "utf8") : "";

  const additions = [
    `WALLET_ID=${wallet.id}`,
    `WALLET_ADDRESS=${wallet.address}`,
    `WALLET_SET_ID=${walletSetId}`,
  ];

  for (const line of additions) {
    const key = line.split("=")[0];
    if (envContent.includes(`${key}=`)) {
      envContent = envContent.replace(new RegExp(`${key}=.*`), line);
    } else {
      envContent += `\n${line}`;
    }
  }

  fs.writeFileSync(envPath, envContent);
  console.log(`\n💾 Saved to .env`);
  console.log(`\nNext: make fund`);
}

main().catch((err) => {
  console.error("Failed:", err.message || err);
  process.exit(1);
});
