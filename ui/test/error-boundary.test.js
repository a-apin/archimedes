import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

// Same shape as a11y.test.js / routes.test.js: readFileSync + regex pins on
// the source, no DOM — node --test has no JSX loader, so ErrorBoundary.jsx,
// main.jsx and AuthenticatedApp.jsx (all .jsx) can't be imported directly.
//
// Every assertion here was confirmed to FAIL against the pre-fix tree (no
// ErrorBoundary.jsx existed; main.jsx mounted <App /> bare; AuthenticatedApp
// rendered {renderPage()} unwrapped) — see the PR body for the transcript
// (CLAUDE.md § "A guard must be shown to reject something").

const src = (p) => readFileSync(new URL(`../src/${p}`, import.meta.url), "utf8");

const boundary = src("components/ErrorBoundary.jsx");
const main = src("main.jsx");
const authenticatedApp = src("AuthenticatedApp.jsx");

test("ErrorBoundary.jsx is a real class-component error boundary", () => {
	assert.match(boundary, /class ErrorBoundary extends Component/);
	assert.match(boundary, /static getDerivedStateFromError\s*\(/);
	assert.match(boundary, /componentDidCatch\s*\(/);
	// componentDidCatch must actually report, not swallow silently.
	assert.match(boundary, /componentDidCatch[\s\S]*?console\.error\(/);
});

test("the fallback names the failure and offers a real reload control, not a blank card", () => {
	assert.match(boundary, /role="alert"/);
	assert.match(boundary, /<button[^>]*onClick=\{?this\.handleReload\}?[^>]*>/);
	// "names the failure": the actual error message/detail is interpolated
	// into the rendered copy, not a fixed generic string only.
	assert.match(boundary, /\{detail\}/);
	assert.match(boundary, /window\.location\.reload\(\)/);
});

test("main.jsx wraps <App in <ErrorBoundary", () => {
	assert.match(main, /import ErrorBoundary from ['"]\.\/components\/ErrorBoundary(\.jsx)?['"]/);
	assert.match(main, /<ErrorBoundary>[\s\S]*?<App\s*\/>[\s\S]*?<\/ErrorBoundary>/);
});

test("AuthenticatedApp.jsx wraps {renderPage()} in an <ErrorBoundary carrying key={route.page}", () => {
	assert.match(
		authenticatedApp,
		/import ErrorBoundary from ["']\.\/components\/ErrorBoundary["']/,
	);
	assert.match(
		authenticatedApp,
		/<ErrorBoundary key=\{route\.page\}>\s*\{renderPage\(\)\}\s*<\/ErrorBoundary>/,
	);
});

test("the two boundaries are distinct wrappers, not the same one reused", () => {
	// The per-page boundary is keyed (resets on navigation); the root
	// boundary in main.jsx is not. A single shared boundary would turn a
	// per-page crash into a full-page takeover (anti-goal).
	assert.doesNotMatch(main, /key=\{route\.page\}/);
	assert.match(authenticatedApp, /key=\{route\.page\}/);
});
