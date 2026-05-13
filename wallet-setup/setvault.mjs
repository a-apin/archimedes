// setvault.mjs
//
// Sets vault as minter for all deployed tokens.
// Idempotent — only calls setVault if env vars exist.
//
// Usage: node --env-file=../.env setvault.mjs

import crypto from "crypto";

const API_KEY = process.env.CIRCLE_API_KEY;
const ENTITY_SECRET = process.env.CIRCLE_ENTITY_SECRET;
const WALLET_ID = process.env.WALLET_ID;

if (!API_KEY || !ENTITY_SECRET || !WALLET_ID) {
  console.error("ERROR: Need CIRCLE_API_KEY, CIRCLE_ENTITY_SECRET, WALLET_ID");
  process.exit(1);
}

const API = "https://api.circle.com/v1/w3s";

const ASSETS = ["TSLA", "NVDA", "SPY", "BTC", "GOLD", "OIL", "NIKKEI"];

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

function wait(ms) { return new Promise(r => setTimeout(r, ms)); }

async function waitForTx(txId, timeoutMs = 60_000) {
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    const res = await fetch(`${API}/transactions/${txId}`, {
      headers: { Authorization: `Bearer ${API_KEY}`, Accept: "application/json" },
    });
    const data = await res.json();
    const tx = data.data?.transaction;
    if (tx?.state === "COMPLETE") return true;
    if (tx?.state === "FAILED") return false;
    await wait(5000);
  }
  return false;
}

async function setVault(tokenAddr, vaultAddr) {
  const entitySecretCiphertext = await getCiphertext();
  const res = await fetch(`${API}/developer/transactions/contractExecution`, {
    method: "POST",
    headers: { Authorization: `Bearer ${API_KEY}`, "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify({
      idempotencyKey: crypto.randomUUID(),
      walletId: WALLET_ID,
      contractAddress: tokenAddr,
      abiFunctionSignature: "setVault(address)",
      abiParameters: [vaultAddr],
      feeLevel: "MEDIUM",
      entitySecretCiphertext,
    }),
  });
  const d = await res.json();
  if (res.status >= 400) throw new Error(JSON.stringify(d));
  return d.data?.id;
}

async function main() {
  console.log("=== Set Vault for All Tokens ===\n");

  for (const tag of ASSETS) {
    const token = process.env[`${tag}_TOKEN`];
    const vault = process.env[`${tag}_VAULT`];
    if (!token || !vault) {
      console.log(`  ${tag}: SKIP (not deployed)`);
      continue;
    }

    try {
      process.stdout.write(`  ${tag}: setVault...`);
      const txId = await setVault(token, vault);
      const ok = await waitForTx(txId);
      console.log(ok ? ` ✅` : ` ⚠️ timeout (tx: ${txId})`);
    } catch (err) {
      console.log(` ❌ ${err.message}`);
    }
  }
}

main().catch((err) => { console.error("Failed:", err.message); process.exit(1); });
