import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

// Static-source checks against component text (no DOM runner in this repo —
// pattern established by ui/test/generate-quote.test.js /
// ui/test/app-visuals.test.js). Cancel is fully implemented server-side
// (POST /api/generate/jobs/{id}/cancel) but had ZERO callers anywhere in
// ui/src (#1355): the one "Cancel" label in the app was rendered behind a
// prop (`hideReset`) its only mount always passed as true, and even when
// reachable its handler was `onReset` — a "back to the table" navigation
// that never called the endpoint. These assertions pin down both halves of
// the fix: the endpoint is actually called, AND the button that calls it is
// not gated behind the same dead prop that made the old one unreachable.

const stream = readFileSync(
	new URL("../src/components/GenerationStream.jsx", import.meta.url),
	"utf8",
);
const status = readFileSync(
	new URL("../src/components/GenerationStatus.jsx", import.meta.url),
	"utf8",
);

test("GenerationStream.jsx posts to the real cancel endpoint via apiPost", () => {
	assert.match(stream, /import\s*\{\s*apiPost\s*\}\s*from\s*["']\.\.\/api["']/);
	assert.match(
		stream,
		/apiPost\(\s*`\/api\/generate\/jobs\/\$\{encodeURIComponent\(jobId\)\}\/cancel`/,
	);
});

test("GenerationStream.jsx: the Cancel action is reachable for a running/queued job, not gated behind hideReset", () => {
	// The bug: the ONLY "Cancel" affordance was `{!hideReset && <button onClick={onReset}>...Cancel</button>}`,
	// and the only mount (Generate.jsx) always passes `hideReset`, so the
	// button never rendered at all. The real Cancel control must render off
	// `!terminal` (true for any running/queued job) independent of `hideReset`.
	assert.match(stream, /const handleCancel = async \(\) => \{/);
	assert.match(stream, /\{!terminal\s*&&\s*\(\s*<button[^>]*onClick=\{handleCancel\}/s);
	// The old broken wiring — a button labeled Cancel whose onClick was
	// onReset — must be gone, not merely supplemented.
	assert.doesNotMatch(stream, /\{terminal \? ['"]New generation['"] : ['"]Cancel['"]\}/);
});

test("GenerationStream.jsx: cancelling is best-effort and does not throw into the render tree", () => {
	// A failed cancel POST must surface as state, not an unhandled rejection —
	// the SSE stream (or a retry) remains the source of truth for whether the
	// job actually stopped.
	assert.match(stream, /const \[cancelling, setCancelling\] = useState\(false\)/);
	assert.match(stream, /handleCancel[\s\S]{0,400}try\s*\{[\s\S]{0,200}catch/);
});

test("GenerationStatus.jsx: STATE_TAGS carries a 'stalled' entry", () => {
	assert.match(status, /STATE_TAGS\s*=\s*\{[\s\S]*?\bstalled:\s*\{[^}]*\blabel:\s*['"]stalled['"]/);
});

test("GenerationStatus.jsx: a stalled row reads 'view →', never 'resume →'", () => {
	// resume/view is derived from `j.state`; 'stalled' must NOT join the
	// resume-eligible set (queued/running) — offering "resume" on a job the
	// server has already determined is dead repeats the exact false-liveness
	// claim #1355 exists to correct.
	const match = status.match(/\{j\.state === ['"]running['"] \|\| j\.state === ['"]queued['"] \? ['"]resume →['"] : ['"]view →['"]\}/);
	assert.ok(match, "expected the resume/view ternary to gate ONLY on running/queued");
});
