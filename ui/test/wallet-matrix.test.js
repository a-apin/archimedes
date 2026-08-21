import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

// #1298 wallet-matrix unlock: the pay panel must never dead-end. Field
// report (Dan, 2026-08-21): with no in-session wallet connection the panel
// said "use a browser wallet" with NO connect affordance — MetaMask, Circle
// passkey, and Coinbase users were all stuck. These pins reject that build.

const x402 = readFileSync(new URL("../src/x402.js", import.meta.url), "utf8");
const generate = readFileSync(new URL("../src/components/Generate.jsx", import.meta.url), "utf8");
const config = readFileSync(new URL("../src/config.js", import.meta.url), "utf8");

test("no-wallet branch offers the connect flow, not a dead end (#1298)", () => {
	// The old copy told users to use a wallet without offering one — gone.
	assert.doesNotMatch(generate, /isn't\s*\n?\s*supported for payments yet/);
	// The replacement branch must dispatch the app's existing connect modal.
	const branch = generate.match(/!walletSupportsPayment\(\) \? \(([\s\S]*?)\) : \(/);
	assert.ok(branch, "no-wallet branch missing");
	assert.match(branch[1], /open-wallet-modal/);
	assert.match(branch[1], /Connect wallet/);
});

test("circle passkey wallets sign and deposit instead of being refused (#1298)", () => {
	// Kind-based branching exists and covers all three states.
	assert.match(x402, /paymentWalletKind/);
	// Passkey signing goes through the smart account's typed-data signer…
	assert.match(x402, /smartAccount\.signTypedData\(\{ domain, types, primaryType, message \}\)/);
	// …and passkey deposits go through the bundler executor as a batched op.
	assert.match(x402, /executeUserOp\(\{ smartAccount, client, calls/);
	// The signature must NOT be re-wrapped (the #870/#871 double-ERC-6492 class).
	assert.doesNotMatch(x402, /wrap6492|erc6492Wrap/i);
	// The old blanket refusal copy is gone from the signer.
	assert.doesNotMatch(x402, /can't sign payments yet/);
});

test("payment errors surface the wallet's own words (#1298)", () => {
	assert.match(generate, /e\?\.shortMessage \|\| e\?\.message/);
});

test("wallet_addEthereumChain declares a non-empty block explorer (#1298 MetaMask validation)", () => {
	// MetaMask/Brave reject blockExplorerUrls: [] outright — the add-chain
	// call must carry the real explorer or omit the key entirely.
	assert.doesNotMatch(config, /blockExplorerUrls:\s*\[\s*\]/);
	assert.match(config, /blockExplorerUrls:\s*\['https:\/\/testnet\.arcscan\.app'\]/);
});
