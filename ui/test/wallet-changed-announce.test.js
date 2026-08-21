import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

// Browser-payments split-brain (field report Dan, 2026-08-21, all three
// browsers): the top-bar chip showed a connected wallet while Generate's
// pay panel said "Connect your wallet to pay" forever. Root cause:
// Generate copies the connected address into local state (getAddress() at
// mount + the 'wallet-changed' event), but the ONLY dispatcher of that
// event was the EOA accountsChanged runtime listener — connectWallet(),
// reconnectWallet() (both branches), and disconnectWallet() all mutated
// _address silently. Page-load reconnect resolves AFTER Generate mounts,
// so the panel never engaged the one-click pay path in ANY browser flow.
// These pins reject any build where an _address transition doesn't
// announce.

const config = readFileSync(new URL("../src/config.js", import.meta.url), "utf8");
const generate = readFileSync(new URL("../src/components/Generate.jsx", import.meta.url), "utf8");

test("every wallet-state transition announces wallet-changed", () => {
	// The single announcer helper exists and carries the module address.
	assert.match(
		config,
		/function announceWalletChanged\(\) \{\s*window\.dispatchEvent\(new CustomEvent\('wallet-changed', \{ detail: \{ address: _address \} \}\)\)/,
	);
	// Circle passkey connect announces after persisting.
	assert.match(
		config,
		/saveWalletMeta\(CIRCLE_PROVIDER_ID, _address, result\.walletName\)\s*\n\s*announceWalletChanged\(\)/,
	);
	// EOA connect announces after persisting.
	assert.match(
		config,
		/saveWalletMeta\(providerId, _address\)\s*\n\s*announceWalletChanged\(\)/,
	);
	// Silent page-load reconnect announces on BOTH branches — this is the
	// exact transition Generate was blind to (chip connected, panel not).
	assert.match(
		config,
		/saveWalletMeta\(CIRCLE_PROVIDER_ID, _address\)\s*\n\s*announceWalletChanged\(\)/,
	);
	assert.match(
		config,
		/saveWalletMeta\(_providerId, _address\)\s*\n\s*announceWalletChanged\(\)/,
	);
	// Disconnect announces the cleared (null) address.
	assert.match(config, /clearWalletMeta\(\)\s*\n\s*announceWalletChanged\(\)\s*\n\}/);
});

test("accountsChanged path uses the shared announcer, no double dispatch", () => {
	// The empty-accounts branch relies on disconnectWallet()'s announce —
	// a second inline dispatch there would fire the event twice per revoke.
	assert.doesNotMatch(
		config,
		/disconnectWallet\(\).*\n\s*window\.dispatchEvent\(new CustomEvent\('wallet-changed'/,
	);
	// And no raw dispatches remain outside the helper.
	const rawDispatches = config.match(/dispatchEvent\(new CustomEvent\('wallet-changed'/g) ?? [];
	assert.equal(rawDispatches.length, 1, "wallet-changed must only be dispatched by announceWalletChanged()");
});

test("Generate resyncs from the announcement (consumer side of the pin)", () => {
	assert.match(generate, /useState\(\(\) => getAddress\(\)\)/);
	assert.match(generate, /addEventListener\("wallet-changed"/);
});
