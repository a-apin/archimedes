// The "Core strategy journey" rail rendered by Layout.jsx (#1354). Plain
// .js, not defined inline in Layout.jsx: Layout.jsx is JSX and can only be
// imported through Vite, but ui/test/ runs under plain `node --test` (see
// routes.js / featureFlags.js / features.js for the same split). Keeping
// this logic here lets the hermetic test import and call it directly.
import { ROADMAP_SURFACES_ENABLED } from "./featureFlags.js";

// Core rail: reachable regardless of the flag. The vault/monitor stages
// describe pages (vault-detail, portfolio) that are themselves ROADMAP_PAGES
// (routes.js) — unreachable when the flag is off — so showing them as
// grayed-out future stops on a public rail would be a claim with no path to
// redeem it.
const CORE_PROOF_STAGES = [
	{ id: "brief", label: "Brief" },
	{ id: "debate", label: "Debate" },
	{ id: "gate", label: "Gate" },
];
const ROADMAP_PROOF_STAGES = [
	{ id: "vault", label: "Vault" },
	{ id: "monitor", label: "Monitor" },
];

// `roadmapOn` defaults to the build-time flag but accepts an explicit
// override so tests don't need to mutate `import.meta.env` — same pattern
// as routes.js's `featureEnabled(page, features)`.
export function getProofStages(roadmapOn = ROADMAP_SURFACES_ENABLED) {
	return roadmapOn
		? [...CORE_PROOF_STAGES, ...ROADMAP_PROOF_STAGES]
		: CORE_PROOF_STAGES;
}
