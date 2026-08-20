import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import {
	_resetAgentStatusCache,
	AGENT_STATUS_TTL_MS,
	getCachedAgentStatus,
} from "../src/agentStatusCache.js";

// ── getCachedAgentStatus: shared TTL cache backing fetchAgentStatus() ──────
// (PR #1382 review) — same shape as healthCache.js's getCachedHealth
// (#1333), for /api/agent/status: four Redis reads + an Arc RPC round-trip
// per call, unauthenticated, no endpoint-specific rate limit.

test("getCachedAgentStatus: a second call within the TTL window reuses the first call's promise instead of fetching again", async () => {
	_resetAgentStatusCache();
	let calls = 0;
	const fetcher = async () => {
		calls += 1;
		return { alive: true };
	};
	const p1 = getCachedAgentStatus(fetcher, 1_000);
	const p2 = getCachedAgentStatus(fetcher, 1_000 + AGENT_STATUS_TTL_MS - 1);
	assert.equal(p1, p2);
	await p1;
	assert.equal(calls, 1);
});

test("getCachedAgentStatus: a call at/after the TTL window fetches again", async () => {
	_resetAgentStatusCache();
	let calls = 0;
	const fetcher = async () => {
		calls += 1;
		return { alive: true };
	};
	await getCachedAgentStatus(fetcher, 1_000);
	await getCachedAgentStatus(fetcher, 1_000 + AGENT_STATUS_TTL_MS);
	assert.equal(calls, 2);
});

test("getCachedAgentStatus: a failed fetch is not cached — the very next call retries rather than reusing the rejection", async () => {
	_resetAgentStatusCache();
	let calls = 0;
	const failingFetcher = async () => {
		calls += 1;
		throw new Error("boom");
	};
	const okFetcher = async () => {
		calls += 1;
		return { alive: false };
	};
	await assert.rejects(getCachedAgentStatus(failingFetcher, 1_000));
	assert.equal(calls, 1);
	const result = await getCachedAgentStatus(okFetcher, 1_001);
	assert.equal(calls, 2);
	assert.deepEqual(result, { alive: false });
});

// ── Wiring: agentStatus.js delegates, Architecture.jsx uses the cache ──────

const agentStatus = readFileSync(
	new URL("../src/agentStatus.js", import.meta.url),
	"utf8",
);

test("agentStatus.js: fetchAgentStatus() delegates to the shared getCachedAgentStatus cache with the real apiGet, not its own logic", () => {
	assert.match(agentStatus, /from ["']\.\/api["']/);
	assert.match(agentStatus, /from ["']\.\/agentStatusCache\.js["']/);
	assert.match(agentStatus, /getCachedAgentStatus\(apiGet\)/);
});

const architecture = readFileSync(
	new URL("../src/components/Architecture.jsx", import.meta.url),
	"utf8",
);

test("Architecture.jsx: /api/agent/status is read through the shared fetchAgentStatus cache, not a direct apiGet('/api/agent/status') call — mutation-check target", () => {
	// Mutation-check (CLAUDE.md § "Before you approve a merge", rule 4):
	// reverting Architecture.jsx's import + call site back to
	// `apiGet("/api/agent/status")` (the pre-fix shape, confirmed against
	// this branch's own prior commit) makes this assertion fail — verified
	// by temporarily restoring that line and re-running this file alone,
	// which produced exactly the failure below before the fix was
	// re-applied:
	//   AssertionError [ERR_ASSERTION]: The input was expected to not match
	//   the regular expression /apiGet\(["']\/api\/agent\/status["']\)/
	assert.doesNotMatch(
		architecture,
		/apiGet\(["']\/api\/agent\/status["']\)/,
		"Architecture.jsx calls apiGet('/api/agent/status') directly instead of the shared fetchAgentStatus() — this re-fires the endpoint's 4 Redis reads + Arc RPC call on every mount within the TTL window",
	);
	assert.match(
		architecture,
		/from ["']\.\.\/agentStatus["']/,
		"Architecture.jsx does not import from ../agentStatus",
	);
	assert.match(
		architecture,
		/fetchAgentStatus\(\)/,
		"Architecture.jsx does not call the shared fetchAgentStatus()",
	);
});
