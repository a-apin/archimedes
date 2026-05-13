// deploy.mjs
//
// Deploys Oracle + Token + Vault for every asset to Arc Testnet.
//
// Usage: node --env-file=../.env deploy.mjs

import crypto from "crypto";
import fs from "fs";
import path from "path";

const API_KEY = process.env.CIRCLE_API_KEY;
const ENTITY_SECRET = process.env.CIRCLE_ENTITY_SECRET;
const WALLET_ID = process.env.WALLET_ID;
const WALLET_ADDRESS = process.env.WALLET_ADDRESS;

if (!API_KEY || !ENTITY_SECRET || !WALLET_ID || !WALLET_ADDRESS) {
  console.error("ERROR: Need CIRCLE_API_KEY, CIRCLE_ENTITY_SECRET, WALLET_ID, WALLET_ADDRESS");
  process.exit(1);
}

const OUT_DIR = path.resolve(import.meta.dirname, "../contracts/out");
const USDC = "0x3600000000000000000000000000000000000000";
const API = "https://api.circle.com/v1/w3s";

// ─── Asset definitions ──────────────────────────────────────────────
// symbol, token name, token symbol, approximate price (6 dec), yahoo ticker
const ASSETS = [
  { id: "TSLA",    name: "Synthetic TSLA",           sym: "sTSLA",  price: 433450000,  yahoo: "TSLA"  },
  { id: "NVDA",    name: "Synthetic NVDA",           sym: "sNVDA",  price: 128000000,  yahoo: "NVDA"  },
  { id: "SPY",     name: "Synthetic SPY",            sym: "sSPY",   price: 590000000,  yahoo: "SPY"   },
  { id: "BTC",     name: "Synthetic Bitcoin",        sym: "sBTC",   price: 103500000000, yahoo: "BTC-USD" },
  { id: "GOLD",    name: "Synthetic Gold (GLD)",     sym: "sGOLD",  price: 310000000,  yahoo: "GLD"   },
  { id: "OIL",     name: "Synthetic Oil (USO)",      sym: "sOIL",   price: 58000000,   yahoo: "USO"   },
  { id: "NIKKEI",  name: "Synthetic Nikkei (EWJ)",   sym: "sNKY",   price: 136000000,  yahoo: "EWJ"   },
];

// ─── Helpers ────────────────────────────────────────────────────────

let ciphertextCache = null;

async function getCiphertext() {
  if (ciphertextCache) return ciphertextCache;
  const pkRes = await fetch(`${API}/config/entity/publicKey`, {
    headers: { Authorization: `Bearer ${API_KEY}`, Accept: "application/json" },
  });
  const { data } = await pkRes.json();
  const pub = crypto.createPublicKey({ key: data.publicKey, format: "pem" });
  ciphertextCache = crypto.publicEncrypt(
    { key: pub, padding: crypto.constants.RSA_PKCS1_OAEP_PADDING, oaepHash: "sha256" },
    Buffer.from(ENTITY_SECRET, "hex")
  ).toString("base64");
  return ciphertextCache;
}

function loadArtifact(name) {
  const f = path.join(OUT_DIR, `${name}.sol`, `${name}.json`);
  const a = JSON.parse(fs.readFileSync(f, "utf8"));
  const bc = a.bytecode.object;
  return { abi: a.abi, bytecode: bc.startsWith("0x") ? bc : "0x" + bc };
}

function wait(ms) { return new Promise(r => setTimeout(r, ms)); }

async function apiPost(endpoint, body) {
  const res = await fetch(`${API}${endpoint}`, {
    method: "POST",
    headers: { Authorization: `Bearer ${API_KEY}`, "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify(body),
  });
  const data = await res.json();
  if (res.status >= 400) throw new Error(`API ${res.status}: ${JSON.stringify(data)}`);
  return data;
}

async function waitForContract(contractId, timeoutMs = 180_000) {
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    const res = await fetch(`${API}/contracts/${contractId}`, {
      headers: { Authorization: `Bearer ${API_KEY}`, Accept: "application/json" },
    });
    const data = await res.json();
    const c = data.data?.contract;
    if (c?.status === "COMPLETE" && c.contractAddress) return c.contractAddress;
    if (c?.status === "FAILED") throw new Error(`Contract ${contractId} failed`);
    process.stdout.write(".");
    await wait(3000);
  }
  throw new Error(`Timeout for contract ${contractId}`);
}

async function waitForTx(txId, timeoutMs = 60_000) {
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    const res = await fetch(`${API}/transactions/${txId}`, {
      headers: { Authorization: `Bearer ${API_KEY}`, Accept: "application/json" },
    });
    const data = await res.json();
    const tx = data.data?.transaction;
    if (tx?.state === "COMPLETE") return;
    if (tx?.state === "FAILED") throw new Error(`TX ${txId} failed`);
    await wait(3000);
  }
  throw new Error(`Timeout for tx ${txId}`);
}

async function deployContract(name, artifact, constructorParams) {
  ciphertextCache = null;
  const entitySecretCiphertext = await getCiphertext();
  const res = await apiPost("/contracts/deploy", {
    idempotencyKey: crypto.randomUUID(),
    name,
    walletId: WALLET_ID,
    blockchain: "ARC-TESTNET",
    abiJson: JSON.stringify(artifact.abi),
    bytecode: artifact.bytecode,
    constructorParameters: constructorParams,
    entitySecretCiphertext,
    feeLevel: "MEDIUM",
  });
  return res.data;
}

async function callContract(contractAddress, signature, params) {
  ciphertextCache = null;
  const entitySecretCiphertext = await getCiphertext();
  const res = await apiPost("/developer/transactions/contractExecution", {
    idempotencyKey: crypto.randomUUID(),
    walletId: WALLET_ID,
    contractAddress,
    abiFunctionSignature: signature,
    abiParameters: params,
    feeLevel: "MEDIUM",
    entitySecretCiphertext,
  });
  return res.data;
}

// ─── Main ───────────────────────────────────────────────────────────

async function main() {
  console.log(`=== Deploying ${ASSETS.length} assets to Arc Testnet ===\n`);

  const envPath = path.resolve(import.meta.dirname, "../.env");
  const oracleArt = loadArtifact("PriceOracle");
  const tokenArt  = loadArtifact("SyntheticToken");
  const vaultArt  = loadArtifact("SyntheticVault");

  const deployed = {};
  const existingEnv = fs.existsSync(envPath) ? fs.readFileSync(envPath, "utf8") : "";

  function getEnv(key) {
    const m = existingEnv.match(new RegExp(`${key}=(.*)`, "m"));
    return m ? m[1].trim() : null;
  }

  for (const asset of ASSETS) {
    const tag = asset.id;
    const existingOracle = getEnv(`${tag}_ORACLE`);
    const existingToken  = getEnv(`${tag}_TOKEN`);
    const existingVault  = getEnv(`${tag}_VAULT`);

    if (existingOracle && existingToken && existingVault) {
      console.log(`── ${tag}: already deployed (skip) ──`);
      deployed[tag] = { oracle: existingOracle, token: existingToken, vault: existingVault };
      continue;
    }

    console.log(`── ${tag} ──`);

    process.stdout.write(`  Oracle...`);
    const oracleDep = await deployContract(`Archimedes ${tag} Oracle`, oracleArt, [tag, asset.price.toString(), WALLET_ADDRESS]);
    const oracleAddr = await waitForContract(oracleDep.contractId);
    console.log(` ${oracleAddr}`);

    process.stdout.write(`  Token...`);
    const tokenDep = await deployContract(`Archimedes ${tag} Token`, tokenArt, [asset.name, asset.sym, WALLET_ADDRESS]);
    const tokenAddr = await waitForContract(tokenDep.contractId);
    console.log(` ${tokenAddr}`);

    process.stdout.write(`  Vault...`);
    const vaultDep = await deployContract(`Archimedes ${tag} Vault`, vaultArt, [USDC, tokenAddr, oracleAddr, WALLET_ADDRESS]);
    const vaultAddr = await waitForContract(vaultDep.contractId);
    console.log(` ${vaultAddr}`);

    deployed[tag] = { oracle: oracleAddr, token: tokenAddr, vault: vaultAddr };

    // Fire-and-forget setVault — saved to env, run 'make setvault' to confirm
    process.stdout.write(`  setVault...`);
    try {
      const setVTx = await callContract(tokenAddr, "setVault(address)", [vaultAddr]);
      await waitForTx(setVTx.id);
      console.log(` ✅`);
    } catch {
      console.log(` ⚠️ timed out — run 'make setvault' after`);
    }
  }

  // Save all to .env
  let envContent = fs.existsSync(envPath) ? fs.readFileSync(envPath, "utf8") : "";

  for (const [tag, addr] of Object.entries(deployed)) {
    const entries = {
      [`${tag}_ORACLE`]: addr.oracle,
      [`${tag}_TOKEN`]: addr.token,
      [`${tag}_VAULT`]: addr.vault,
    };
    for (const [key, value] of Object.entries(entries)) {
      if (envContent.includes(`${key}=`)) {
        envContent = envContent.replace(new RegExp(`${key}=.*`), `${key}=${value}`);
      } else {
        envContent += `\n${key}=${value}`;
      }
    }
  }
  fs.writeFileSync(envPath, envContent);

  console.log("\n=== All Deployed ===");
  for (const [tag, addr] of Object.entries(deployed)) {
    console.log(`${tag}: oracle=${addr.oracle} token=${addr.token} vault=${addr.vault}`);
  }
  console.log(`\nSaved to .env → run 'make feed' to push prices`);
}

main().catch((err) => {
  console.error("\nFailed:", err.message || err);
  process.exit(1);
});
