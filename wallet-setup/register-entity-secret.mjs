// register-entity-secret.mjs
//
// Generates a 32-byte entity secret and registers it with Circle.
// Uses the official Circle SDK which handles encryption automatically.
//
// Prerequisites: CIRCLE_API_KEY in ../.env
// Usage: node --env-file=../.env register-entity-secret.mjs

import fs from "node:fs";
import path from "node:path";
import crypto from "node:crypto";

import { registerEntitySecretCiphertext } from "@circle-fin/developer-controlled-wallets";

const API_KEY = process.env.CIRCLE_API_KEY;
if (!API_KEY) {
  console.error("ERROR: CIRCLE_API_KEY not found in ../.env");
  process.exit(1);
}

async function main() {
  console.log("=== Circle Entity Secret Registration ===\n");

  // Step 1: Generate 32-byte entity secret (hex)
  const entitySecret = crypto.randomBytes(32).toString("hex");
  console.log("🔑 Generated entity secret:");
  console.log(`   ${entitySecret}\n`);

  // Step 2: Register with Circle (SDK handles RSA encryption)
  console.log("Registering with Circle...");
  const response = await registerEntitySecretCiphertext({
    apiKey: API_KEY,
    entitySecret: entitySecret,
  });

  if (response.data?.recoveryFile) {
    const recoveryPath = path.join(import.meta.dirname, "recovery_file.dat");
    fs.writeFileSync(recoveryPath, response.data.recoveryFile);
    console.log(`📦 Recovery file saved to: wallet-setup/recovery_file.dat`);
    console.log("   ⚠️  SAVE THIS SECURELY - it can only be downloaded once!\n");
  }

  // Step 3: Save entity secret to ../.env
  const envPath = path.resolve(import.meta.dirname, "../.env");
  let envContent = "";
  if (fs.existsSync(envPath)) {
    envContent = fs.readFileSync(envPath, "utf8");
  }

  if (envContent.includes("CIRCLE_ENTITY_SECRET=")) {
    envContent = envContent.replace(
      /CIRCLE_ENTITY_SECRET=.*/,
      `CIRCLE_ENTITY_SECRET=${entitySecret}`
    );
  } else {
    envContent += `\nCIRCLE_ENTITY_SECRET=${entitySecret}\n`;
  }

  fs.writeFileSync(envPath, envContent);
  console.log(`💾 Entity secret saved to root .env`);
  console.log("\n✅ Done! Next: npm run create-wallet");
}

main().catch((err) => {
  console.error("Failed:", err.message || err);
  process.exit(1);
});
