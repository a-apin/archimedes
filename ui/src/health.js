// Shared /health fetch, TTL-cached (mirrors main's #1333 review follow-up,
// which this branch predates — see ./healthCache.js for the caching logic
// and the full rationale). This is the thin production wrapper real UI
// components call — it binds the real `apiGet` so any caller can invoke
// `fetchHealth()` with no arguments and share one response instead of
// firing its own independent /health request. CorpusKG.jsx (#1368) is the
// first caller on this branch; Layout.jsx, Architecture.jsx, and
// ModelCostPanel.jsx still call `apiGet("/health")` directly here and will
// pick this up when this branch reconciles with main's own #1333 line.
import { apiGet } from "./api";
import { getCachedHealth } from "./healthCache.js";

export function fetchHealth() {
	return getCachedHealth(apiGet);
}
