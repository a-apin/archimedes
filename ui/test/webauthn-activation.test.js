import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

// Field report (Dan, 2026-08-21, iPad + MacBook): passkey payments died with
// "Failed to request credential." on REAL taps. WebKit refuses to open a
// WebAuthn prompt once the tap's transient user activation has been consumed
// by earlier awaits — and the one-click flow awaited a fresh 402, a balance
// read, a deposit user-op, and its confirmation before requesting the
// payment signature. The deposit's own prompt (close to the tap) succeeded;
// the payment prompt never did. These pins hold the contract that fixed it.

const generate = readFileSync(new URL("../src/components/Generate.jsx", import.meta.url), "utf8");
const x402 = readFileSync(new URL("../src/x402.js", import.meta.url), "utf8");

test("funded tap signs FIRST — no await between the tap and the ceremony", () => {
	// The funded branch exists, keyed on held requirements + held balance…
	assert.match(generate, /heldSignableRequirements/);
	assert.match(generate, /gatewayBalance >= requiredAmountRaw/);
	// …and inside it the signature call uses the HELD requirements, with the
	// no-await contract stated where the next editor will see it.
	assert.match(generate, /NOTHING may be awaited between the tap and this call/);
	assert.match(generate, /requirements: held\.requirements/);
	// The old shape — unconditional click-time refetch before signing — is
	// gone: the refetch survives only behind the held ?? fallback.
	assert.doesNotMatch(generate, /const fresh = await refreshPaymentRequirements\(\)/);
});

test("a refused ceremony arms a fresh-tap confirm state, not a raw error", () => {
	// ox's refusal (and a user cancel) is recognised…
	assert.match(generate, /isActivationRefusal/);
	assert.match(generate, /Authentication\.SignFailedError/);
	assert.match(generate, /failed to request credential/i);
	// …and lands in payStep "confirm" whose button asks for one more tap.
	assert.match(generate, /setPayStep\("confirm"\)/);
	assert.match(generate, /Tap to approve the/);
	// The confirm state survives the finally-reset that returns to idle.
	assert.match(generate, /s === "confirm" \? s : "idle"/);
});

test("held requirements have a bounded age before a refetch is forced", () => {
	assert.match(generate, /REQUIREMENTS_FRESH_MS/);
	assert.match(generate, /paymentRequirementsAt/);
});

test("exactly one button: main submit hides when the pay panel is armed", () => {
	assert.match(generate, /payPanelReady/);
	assert.match(generate, /\{!payPanelReady && \(/);
});

test("the circle signing path involves no WebAuthn ceremony at all (#1467)", () => {
	// SUPERSESSION (#1467): payments from the passkey kind are signed by the
	// device payment key — a local EOA — so the activation-refusal class this
	// file guards cannot occur there. The branch must sign with the session
	// key and must NOT reach for the smart account's WebAuthn signer or an
	// ensureArcChain/getWalletClient hop.
	const circleBranch = x402.match(/if \(kind === "circle"\) \{([\s\S]*?)\} else \{/);
	assert.ok(circleBranch, "circle signing branch missing");
	assert.doesNotMatch(circleBranch[1], /ensureArcChain|getWalletClient/);
	assert.match(circleBranch[1], /session\.signTypedData/);
	assert.doesNotMatch(circleBranch[1], /smartAccount\.signTypedData\(/);
});
