// deploy.mjs
//
// Deploys all 3 contracts to Arc Testnet via Circle Contracts API.
// Uses raw fetch() since the SDK had issues.
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
const INITIAL_TSLA_PRICE = 433_450_000;
const API = "https://api.circle.com/v1/w3s";

let cachedCiphertext = null;

async function getCiphertext() {
  if (cachedCiphertext) return cachedCiphertext;
  const pkRes = await fetch(`${API}/config/entity/publicKey`, {
    headers: { Authorization: `Bearer ${API_KEY}`, Accept: "application/json" },
  });
  const { data } = await pkRes.json();
  const publicKey = crypto.createPublicKey({ key: data.publicKey, format: "pem" });
  const ct = crypto.publicEncrypt(
    { key: publicKey, padding: crypto.constants.RSA_PKCS1_OAEP_PADDING, oaepHash: "sha256" },
    Buffer.from(ENTITY_SECRET, "hex")
  );
  cachedCiphertext = ct.toString("base64");
  return cachedCiphertext;
}

function loadArtifact(name) {
  const f = path.join(OUT_DIR, `${name}.sol`, `${name}.json`);
  const a = JSON.parse(fs.readFileSync(f, "utf8"));
  const bc = a.bytecode.object;
  return { abi: a.abi, bytecode: bc.startsWith("0x") ? bc : "0x" + bc };
}

function wait(ms) { return new Promise(r => setTimeout(r, ms)); }

async function circlePost(endpoint, body) {
  const res = await fetch(`${API}${endpoint}`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${API_KEY}`,
      "Content-Type": "application/json",
      Accept: "application/json",
    },
    body: JSON.stringify(body),
  });
  const data = await res.json();
  if (res.status >= 400) throw new Error(`API ${res.status}: ${JSON.stringify(data)}`);
  return data;
}

async function waitForTx(txId, timeoutMs = 120_000) {
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    const res = await fetch(`${API}/developer/transactions/${txId}`, {
      headers: { Authorization: `Bearer ${API_KEY}`, Accept: "application/json" },
    });
    const data = await res.json();
    const tx = data.data?.transaction;
    if (tx?.state === "COMPLETE") return tx;
    if (tx?.state === "FAILED") throw new Error(`TX failed: ${txId}`);
    process.stdout.write(".");
    await wait(3000);
  }
  throw new Error(`Timeout for tx ${txId}`);
}

async function getContractAddress(contractId, timeoutMs = 120_000) {
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    const res = await fetch(`${API}/contracts/${contractId}`, {
      headers: { Authorization: `Bearer ${API_KEY}`, Accept: "application/json" },
    });
    const data = await res.json();
    const c = data.data?.contract;
    if (c?.status === "COMPLETE" && c.contractAddress) return c.contractAddress;
    process.stdout.write(".");
    await wait(3000);
  }
  throw new Error(`Timeout for contract ${contractId}`);
}

async function deployContract(name, artifact, constructorParams) {
  // Need fresh ciphertext per request
  cachedCiphertext = null;
  const entitySecretCiphertext = await getCiphertext();

  const res = await circlePost("/contracts/deploy", {
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
  cachedCiphertext = null;
  const entitySecretCiphertext = await getCiphertext();

  const res = await circlePost("/developer/transactions/contractExecution", {
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

async function main() {
  console.log("=== Deploy to Arc Testnet ===\n");

  // 1. Oracle
  console.log("1/4 Deploying TSLAPriceOracle...");
  const oracleArt = loadArtifact("TSLAPriceOracle");
  const oracleDep = await deployContract("Archimedes TSLA Oracle", oracleArt, [INITIAL_TSLA_PRICE.toString()]);
  console.log(`   contractId: ${oracleDep.contractId}`);
  console.log("   Waiting", );
  const oracleAddr = await getContractAddress(oracleDep.contractId);
  console.log(`\n   ✅ Oracle: ${oracleAddr}\n`);

  // 2. sTSLA
  console.log("2/4 Deploying SyntheticTSLA...");
  const sTslaArt = loadArtifact("SyntheticTSLA");
  const sTslaDep = await deployContract("Archimedes Synthetic TSLA", sTslaArt, [WALLET_ADDRESS]);
  console.log(`   contractId: ${sTslaDep.contractId}`);
  console.log("   Waiting");
  const sTslaAddr = await getContractAddress(sTslaDep.contractId);
  console.log(`\n   ✅ sTSLA: ${sTslaAddr}\n`);

  // 3. Vault
  console.log("3/4 Deploying SyntheticVault...");
  const vaultArt = loadArtifact("SyntheticVault");
  const vaultDep = await deployContract("Archimedes Synthetic Vault", vaultArt, [USDC, sTslaAddr, oracleAddr, WALLET_ADDRESS]);
  console.log(`   contractId: ${vaultDep.contractId}`);
  console.log("   Waiting");
  const vaultAddr = await getContractAddress(vaultDep.contractId);
  console.log(`\n   ✅ Vault: ${vaultAddr}\n`);

  // 4. Set vault as sTSLA minter
  console.log("4/4 Setting vault as sTSLA minter...");
  const setVaultTx = await callContract(sTslaAddr, "setVault(address)", [vaultAddr]);
  console.log("   Waiting");
  await waitForTx(setVaultTx.transactionId);
  console.log("\n   ✅ Vault set as minter\n");

  // Save to .env
  const envPath = path.resolve(import.meta.dirname, "../.env");
  let envContent = fs.existsSync(envPath) ? fs.readFileSync(envPath, "utf8") : "";
  const additions = {
    ORACLE_ADDRESS: oracleAddr,
    ORACLE_CONTRACT_ID: oracleDep.contractId,
    STSLA_ADDRESS: sTslaAddr,
    STSLA_CONTRACT_ID: sTslaDep.contractId,
    VAULT_ADDRESS: vaultAddr,
    VAULT_CONTRACT_ID: vaultDep.contractId,
  };
  for (const [key, value] of Object.entries(additions)) {
    if (envContent.includes(`${key}=`)) {
      envContent = envContent.replace(new RegExp(`${key}=.*`), `${key}=${value}`);
    } else {
      envContent += `\n${key}=${value}`;
    }
  }
  fs.writeFileSync(envPath, envContent);

  console.log("=== Deployment Complete ===");
  console.log(`Oracle:  ${oracleAddr}`);
  console.log(`sTSLA:   ${sTslaAddr}`);
  console.log(`Vault:   ${vaultAddr}`);
  console.log(`\nSaved to .env → run 'make feed' to push TSLA price`);
}

main().catch((err) => {
  console.error("\nFailed:", err.message || err);
  process.exit(1);
});
