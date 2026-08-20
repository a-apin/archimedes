// Claim-integrity guard for the public/app surfaces that #1266 gated at the
// ROUTE level but left selling in COPY (#1354): with ROADMAP_SURFACES_ENABLED
// off (the default in every production build), the UI must not advertise the
// vault-deploy / marketplace journey it can no longer reach.
//
// Two guards, both hermetic (no network, no DB, no Redis, no `.env`, no
// import.meta.env mutation):
//
// 1. Prose guard — the seven surface files below must not contain any of the
//    phrase-shapes in OVERCLAIM_PATTERNS. This is a raw source-text scan
//    (readFileSync, no JSX parsing), so it does not distinguish "rendered
//    unconditionally" from "rendered inside a ROADMAP_SURFACES_ENABLED
//    branch" — the literal must not appear in these files AT ALL. Roadmap
//    copy that legitimately needs to exist for the flag-on build lives in
//    ui/src/roadmapCopy.js / roadmapCopyApp.js instead (deliberately not
//    scanned here) and is pulled in by reference. See those files' module
//    docstrings for why there are two of them, not one.
//
// 2. Proof-rail guard — Layout.jsx's "Core strategy journey" rail
//    (proofStages.js) renders 3 stages with the flag off and 5 with it on,
//    asserted through an explicit override argument rather than mutating
//    import.meta.env (mirrors routes.js's `featureEnabled(page, features)`).
//
// Each guard carries anti-vacuity coverage: every declared surface file must
// exist (a rename must fail loudly, not shrink the scan to nothing), and
// every pattern must reject its own canonical example (a pattern that stops
// matching anything is guarding nothing).
//
// Scope note, stated plainly (same discipline as
// backend/tests/test_corpus_claim_integrity.py): OVERCLAIM_PATTERNS is the
// floor named in issue #1354's acceptance criterion 1, not the ceiling. Other
// roadmap-flavored prose (e.g. Architecture.jsx's "Autonomous rebalance
// loop" ledger row, the flow-diagram.svg marketplace cluster) is gated in
// the same PR but isn't independently pattern-matched here; a human read is
// still worth doing on future roadmap-copy PRs.

import { existsSync, readFileSync } from "node:fs";
import assert from "node:assert/strict";
import test from "node:test";

import { getProofStages } from "../src/proofStages.js";

function repoFile(rel) {
	return new URL(`../${rel}`, import.meta.url);
}

function escapeRegExp(literal) {
	return literal.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

//: Renaming a file here without updating this list is caught by
//: test_every_declared_surface_exists — otherwise the scan would silently
//: shrink and the guard would pass vacuously.
const SURFACE_FILES = [
	"src/components/Landing.jsx",
	"src/components/Architecture.jsx",
	"src/components/Layout.jsx",
	"src/components/Insights.jsx",
	"src/components/Strategies.jsx",
	"src/components/OnboardingTour.jsx",
];

//: `(name, regex, canonical_example)` — the example is the shortest string
//: that must trip the pattern; test_every_pattern_rejects_its_canonical_example
//: runs it, so a pattern that stops matching anything fails loudly instead of
//: silently guarding nothing. The first eight mirror issue #1354's
//: acceptance criterion 1 dist-grep list verbatim; the next two are its
//: criterion 3 additions; the last (deploy_as_vault_cta) is a PR #1396
//: review fix — deploy_as_vault is case-sensitive on a lowercase "vault"
//: and does not reject FusionResult.jsx's actual literal,
//: roadmapCopyApp.js's `deployAsVaultLabel: "Deploy as Vault — coming in
//: Phase 4"` (capital V). Both patterns are kept: deploy_as_vault still
//: guards the lowercase phrasing used elsewhere.
const OVERCLAIM_PATTERNS = [
	["deploy_as_vault", new RegExp(escapeRegExp("Deploy as vault")), "Deploy as vault"],
	[
		"non_custodial_vault_on_arc",
		new RegExp(escapeRegExp("non-custodial vault on Arc")),
		"strategy runs live in a non-custodial vault on Arc.",
	],
	["deployed_vault", new RegExp(escapeRegExp("Deployed Vault")), "vault_deployed: 'Deployed Vault'"],
	[
		"pay_creators_not_the_house",
		new RegExp(escapeRegExp("Pay creators, not the house")),
		"Pay creators, not the house",
	],
	[
		"withdraw_assets_from_your_vault",
		new RegExp(escapeRegExp("Withdraw assets from your vault")),
		"Withdraw assets from your vault",
	],
	[
		"agent_activity_feed_on_portfolio",
		new RegExp(escapeRegExp("agent activity feed on Portfolio")),
		"show in the agent activity feed on Portfolio and Reasoning",
	],
	[
		"five_wallet_signatures",
		new RegExp(escapeRegExp("five wallet signatures")),
		"five wallet signatures, all yours",
	],
	[
		"non_custodial_vault_deploy",
		new RegExp(escapeRegExp("Non-custodial vault deploy")),
		"Non-custodial vault deploy + deposits",
	],
	[
		"kicker_rigor_arrow_vault",
		/rigor\s*<span>→<\/span>\s*vault/,
		"Research <span>→</span> rigor <span>→</span> vault",
	],
	["legend_is_user_vault", /is-user">Vault</, '<li className="is-user">Vault</li>'],
	[
		"deploy_as_vault_cta",
		/Deploy as [Vv]ault/,
		"Deploy as Vault",
	],
];

function findOverclaims(text) {
	const hits = [];
	for (const [name, pattern] of OVERCLAIM_PATTERNS) {
		if (pattern.test(text)) hits.push(name);
	}
	return hits;
}

test("every declared surface file exists", () => {
	const missing = SURFACE_FILES.filter((rel) => !existsSync(repoFile(rel)));
	assert.deepEqual(
		missing,
		[],
		`SURFACE_FILES names files that do not exist — a rename shrank the scan silently: ${missing}`,
	);
});

test("every overclaim pattern rejects its canonical example", () => {
	for (const [name, pattern, example] of OVERCLAIM_PATTERNS) {
		assert.match(
			example,
			pattern,
			`pattern ${name} no longer rejects its canonical example ${JSON.stringify(example)} — it is guarding nothing`,
		);
	}
});

for (const rel of SURFACE_FILES) {
	test(`${rel} carries no roadmap-copy overclaim (#1354)`, () => {
		const text = readFileSync(repoFile(rel), "utf8");
		const hits = findOverclaims(text);
		assert.deepEqual(
			hits,
			[],
			`${rel} contains gated roadmap copy literally: ${hits.join(", ")}. ` +
				"Move the phrase to ui/src/roadmapCopy.js (public pages) or " +
				"roadmapCopyApp.js (authenticated pages) and reference it from a " +
				"ROADMAP_SURFACES_ENABLED-gated branch instead of inlining it here.",
		);
	});
}

test("proof rail is flag-derived: 3 stages off, 5 on (explicit override, no import.meta.env mutation)", () => {
	assert.deepEqual(
		getProofStages(false).map((s) => s.id),
		["brief", "debate", "gate"],
	);
	assert.deepEqual(
		getProofStages(true).map((s) => s.id),
		["brief", "debate", "gate", "vault", "monitor"],
	);
	// Anti-vacuity: the default argument really does read the build-time
	// flag (ROADMAP_SURFACES_ENABLED, off under plain node), not a
	// hardcoded value independent of it.
	assert.deepEqual(
		getProofStages().map((s) => s.id),
		["brief", "debate", "gate"],
	);
});

test("onboarding tour's paper card has no nav anchor — anon-bounce guard (#1354)", () => {
	// No nav item carries data-tour="paper" (Layout.jsx's NAV has no 'paper'
	// entry), so an `anchor: 'paper'` here would make measure() always miss,
	// which falls through to OnboardingTour.jsx's "not mounted yet" effect
	// and calls setPage('paper') as a side effect. 'paper' is a page kind:
	// 'app' route outside ANON_APP_PAGES (routes.js), so for a signed-out
	// visitor App.jsx bounces straight to /sign-in — the exact anon-bounce
	// #1354's anti-goal forbids ("linking or navigating to [paper trading]
	// from an anon-reachable surface is not [fine]"). The manual help-icon
	// path that (re)opens the tour has no `user` gate (AuthenticatedApp.jsx
	// passes onOpenTour unconditionally), so an anonymous visitor really can
	// reach this card.
	const onboardingTour = readFileSync(
		repoFile("src/components/OnboardingTour.jsx"),
		"utf8",
	);
	const paperCard = onboardingTour.match(/id:\s*'paper',[\s\S]*?anchor:\s*(\S+),/);
	assert.ok(paperCard, "could not find the 'paper' tour card in OnboardingTour.jsx");
	assert.equal(
		paperCard[1],
		"null",
		"the 'paper' tour card must keep anchor: null (no reachable nav element to spotlight, " +
			"and a non-null anchor triggers an unconditional setPage() navigation side effect)",
	);
});
