import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

// Field-proven 2026-08-21 (Dan's iPad, prod): a Circle passkey smart-account
// signature clears the WebAuthn ceremony and the validity window, reaches
// Circle's facilitator, and dies with `invalid_signature` — because the
// nanopayments rail validates burn intents as EOA (ERC-3009) signatures
// only. Circle's own docs are explicit: "Nanopayments require an EOA
// wallet. Smart contract account (SCA) wallets are not supported" (buyer
// quickstart), and ERC-1271 is excluded from nanopayments (ERC-1271
// reference). Offering the pay button to a passkey wallet is therefore a
// trap that can take a deposit and then fail every payment. These pins
// hold the honest guard until the delegate design ships.

const generate = readFileSync(new URL("../src/components/Generate.jsx", import.meta.url), "utf8");

test("passkey wallets get the honest no-rail notice, not the pay button", () => {
	// The circle-kind branch exists in the pay panel…
	assert.match(generate, /paymentWalletKind\(\) === "circle" \? \(/);
	// …says plainly why, in user words…
	assert.match(generate, /can't verify passkey signatures/);
	// …and offers the wallet modal as the way forward.
	const branch = generate.match(/paymentWalletKind\(\) === "circle" \? \(([\s\S]*?)\) : \(/);
	assert.ok(branch, "circle-kind branch missing");
	assert.match(branch[1], /open-wallet-modal/);
	assert.match(branch[1], /Connect a payment wallet/);
	// The guard must sit BEFORE the pay control so a passkey wallet can
	// never reach handlePayAndGenerate (which would deposit first, then
	// fail signature verification — money in, nothing out).
	assert.ok(
		generate.indexOf('paymentWalletKind() === "circle" ? (') <
			generate.indexOf("onClick={handlePayAndGenerate}"),
		"circle guard must precede the pay button",
	);
});
