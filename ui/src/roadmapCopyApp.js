// Roadmap-only copy (#1354) for the AUTHENTICATED app bundle (Insights.jsx,
// Strategies.jsx — chunk `AuthenticatedApp-*.js`). See roadmapCopy.js for the
// full rationale, the JSX-in-.js constraint, and why this is a separate file
// from the public-pages copy rather than one shared module.

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
;
