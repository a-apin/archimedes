// Shared /health fetch, TTL-cached (#1333 review follow-up). See
// ./healthCache.js for the caching logic and the full rationale; this is
// the thin production wrapper the real UI components call — it binds the
// real `apiGet` so Layout.jsx, Architecture.jsx, and ModelCostPanel.jsx can
// all call `fetchHealth()` with no arguments and share one response.
import { apiGet } from "./api";
import { getCachedHealth } from "./healthCache.js";

export function fetchHealth() {
	return getCachedHealth(apiGet);
}
