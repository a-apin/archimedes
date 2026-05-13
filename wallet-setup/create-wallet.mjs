// create-wallet.mjs
//
// Creates a Circle Dev-Controlled Wallet on Arc Testnet and funds it via faucet.
//
// Prerequisites: CIRCLE_API_KEY + CIRCLE_ENTITY_SECRET in ../.env
// Usage: node --env-file=../.env create-wallet.mjs

import fs from "node:fs";
import path from "node:path";

import { initiateDeveloperControlledWalletsClient } from "@circle-fin/developer-controlled-wallets";

const API_KEY = process.env.CIRCLE_API_KEY;
const ENTITY_SECRET = process.env.CIRCLE_ENTITY_SECRET;

if (!API_KEY || !ENTITY_SECRET) {
  console.error("ERROR: CIRCLE_API_KEY and CIRCLE_ENTITY_SECRET required in ../.env");
  process.exit(1);
}

const client = initiateDeveloperControlledWalletsClient({
  apiKey: API_KEY,
  entitySecret: ENTITY_SECRET,
});

async function main() {
  console.log("=== Create Arc Testnet Wallet ===\n");

  // Step 1: Create wallet set
  console.log("Creating wallet set...");
  const walletSetRes = await client.createWalletSet({ name: "Archimedes Arc" });
  const walletSetId = walletSetRes.data?.walletSet?.id;
  console.log(`Wallet Set ID: ${walletSetId}\n`);

  // Step 2: Create SCA wallet on Arc Testnet
  console.log("Creating SCA wallet on Arc Testnet...");
  const walletRes = await client.createWallets({
    walletSetId,
    blockchains: ["ARC-TESTNET"],
    count: 1,
    accountType: "SCA",
  });

  const wallet = walletRes.data?.wallets?.[0];
  if (!wallet) {
    console.error("Failed to create wallet:", JSON.stringify(walletRes, null, 2));
    process.exit(1);
  }

  console.log("✅ Wallet created!");
  console.log(`   Wallet ID:    ${wallet.id}`);
  console.log(`   Address:      ${wallet.address}`);
  console.log(`   Blockchain:   ${wallet.blockchain}`);
  console.log(`   Account Type: ${wallet.accountType}\n`);

  // Step 3: Request testnet USDC from faucet
  console.log("Requesting 20 testnet USDC from Circle Faucet...");
  try {
    const faucetRes = await client.requestTestnetTokens({
      walletId: wallet.id,
      blockchain: "ARC-TESTNET",
      usdc: true,
    });
    console.log("✅ Faucet request submitted!");
    console.log(`   Transaction ID: ${faucetRes.data?.transactionId}`);
  } catch (err) {
    console.error("⚠️  Faucet request failed (may need manual request):", err.message);
    console.log("   Go to https://faucet.circle.com/ and paste your address manually.");
  }

  // Step 4: Save wallet info to .env
  const envPath = path.resolve(import.meta.dirname, "../.env");
  let envContent = "";
  if (fs.existsSync(envPath)) {
    envContent = fs.readFileSync(envPath, "utf8");
  }

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
  console.log(`\n💾 Wallet info saved to root .env`);
  console.log("\n=== Summary ===");
  console.log(`Wallet Address: ${wallet.address}`);
  console.log("Check balance at: https://testnet.arcscan.app/address/" + wallet.address);
}

main().catch((err) => {
  console.error("Failed:", err.message || err);
  process.exit(1);
});
