// Shared /api/agent/status fetch, TTL-cached (PR #1382 review follow-up,
// same shape as ./health.js, #1333). See ./agentStatusCache.js for the
// caching logic and the full rationale; this is the thin production
// wrapper the real UI calls — it binds the real `apiGet` so a component
// can call `fetchAgentStatus()` with no arguments.
import { apiGet } from "./api";
import { getCachedAgentStatus } from "./agentStatusCache.js";

export function fetchAgentStatus() {
	return getCachedAgentStatus(apiGet);
}
