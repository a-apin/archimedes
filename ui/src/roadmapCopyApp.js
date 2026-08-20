// Roadmap-only copy (#1354) for the AUTHENTICATED app bundle (Insights.jsx,
// Strategies.jsx, FusionResult.jsx — chunk `AuthenticatedApp-*.js`). See
// roadmapCopy.js for the full rationale, the JSX-in-.js constraint, and why
// this is a separate file from the public-pages copy rather than one shared
// module.

export const insights = {
	vaultDeployedLabel: "Deployed Vault",
};

export const strategies = {
	// Full sentence lives here so the surface file never carries the "on
	// Portfolio" clause literally; the core (flag-off) variant is inline in
	// Strategies.jsx since it's honest either way.
	emptyLibraryNoteRoadmap:
		"Generations in flight show in the agent activity feed on Portfolio " +
		"and Reasoning. They land in this table once the rigor gate clears.",
};

export const fusion = {
	// Both strings only ever render inside FusionResult.jsx's
	// ROADMAP_SURFACES_ENABLED-gated CTA block; kept here rather than inline
	// so the roadmap-copy claim-integrity guard's source scan (which the
	// PR's own convention says is where gated vault-deploy prose belongs)
	// actually covers them.
	deployAsVaultLabel: "Deploy as Vault — coming in Phase 4",
	deployAsVaultTitle:
		"Deploy as vault — coming in Phase 4 (time-bound vaults + on-chain agent)",
};
