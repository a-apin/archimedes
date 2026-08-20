import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { PROVIDER_LABELS, providerLabel } from "../src/wallet-providers.js";

// ── provider provenance (#1293) ─────────────────────────────────────────────
// The browser can only ever SEND metamask | browser | circle, but it READS
// back whatever the account's other clients linked with — including
// "headless", which an API client sends because it has none of the three
// browser wallets. Account settings renders this string, so an unknown value
// must degrade to itself rather than to a familiar-but-wrong label.

test("every provider the API accepts has a label", () => {
	const card = JSON.parse(
		readFileSync(new URL("../public/.well-known/agent.json", import.meta.url)),
	);
	const accepted = card.authentication.walletLinkProviders;
	assert.deepEqual(accepted, ["metamask", "browser", "circle", "headless"]);
	for (const provider of accepted) {
		assert.ok(
			PROVIDER_LABELS[provider],
			`no display label for provider "${provider}"`,
		);
	}
});

test("headless renders as an API link, not as a browser wallet", () => {
	assert.equal(providerLabel("headless"), "Headless (API)");
	assert.notEqual(providerLabel("headless"), providerLabel("browser"));
});

test("known providers render their own label", () => {
	assert.equal(providerLabel("metamask"), "MetaMask");
	assert.equal(providerLabel("browser"), "Browser wallet");
	assert.equal(providerLabel("circle"), "Circle passkey");
});

// Guard demonstration: the inputs that must NOT be coerced to a wrong label.
test("an unrecognised provider is shown verbatim, never as a browser wallet", () => {
	for (const unknown of ["ledger", "walletconnect", "Headless", ""]) {
		assert.notEqual(
			providerLabel(unknown),
			"Browser wallet",
			`"${unknown}" was mislabelled as a browser wallet`,
		);
	}
	assert.equal(providerLabel("ledger"), "ledger");
});

test("a missing provider degrades to unknown rather than crashing", () => {
	assert.equal(providerLabel(undefined), "unknown");
	assert.equal(providerLabel(null), "unknown");
	assert.equal(providerLabel(""), "unknown");
});
