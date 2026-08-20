#!/usr/bin/env node
// ui/scripts/check-orphans.mjs
//
// Fails (exit 1) if any file under src/components/ or src/data/ has zero
// importers anywhere in src/ (or index.html). This is the mechanical version
// of the manual "grep for a basename before deleting" check — it catches the
// next FusionResult.jsx / PortfolioAdvisor.jsx / promptLibrary.js before it
// sits dead in the tree.
//
// Static-analysis only: this is a regex-based import-graph walk, not a
// bundler. It mirrors the repo's existing structural-check idiom
// (ui/test/*.test.js reads source as text and asserts on it — see e.g.
// test/routes.test.js) rather than standing up vitest, per the sprint card's
// explicit "do NOT stand up vitest" instruction.
//
// Usage: node scripts/check-orphans.mjs   (run from ui/, or anywhere — all
// paths resolve relative to this file's own location, not cwd)

import { existsSync, readdirSync, readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const UI_ROOT = path.resolve(__dirname, "..");
const SRC_DIR = path.join(UI_ROOT, "src");
const INDEX_HTML = path.join(UI_ROOT, "index.html");

// Directories whose files must each have at least one importer.
const CHECK_DIRS = ["components", "data"].map((d) => path.join(SRC_DIR, d));

// Entry points that are legitimately unreferenced by a plain "from '...'"
// import scan — reached only via a mechanism this script can't see (e.g. a
// dynamic string built at runtime, or wired outside src/ entirely). Extend
// this with a comment explaining why; do NOT add an entry just to silence a
// finding you haven't verified is a real entry point (see CLAUDE.md's
// verify-before-acting rule). Paths are relative to SRC_DIR.
const ALLOWLIST = new Set([
	// KNOWN DEAD, deletion deferred — NOT a legitimate entry point. This
	// script found it with zero importers while WP-4 was scoped to delete
	// only FusionResult.jsx / PortfolioAdvisor.jsx / promptLibrary.js /
	// hero.png (the sprint card's named list). Flagged in the WP-4 PR body
	// for a human call on delete-vs-wire-up (it references a wallet-menu
	// "edit profile" entry point that doesn't exist yet, which reads more
	// like an unfinished feature than inert cruft). Remove this allowlist
	// entry the moment it's either wired up or deleted — do not let it sit
	// here silently past that decision.
	"components/WelcomeProfileModal.jsx",
]);

const SCAN_EXTS = new Set([".js", ".jsx", ".mjs"]);
const RESOLVE_EXTS = ["", ".js", ".jsx", ".mjs", ".json", ".css"];

function walk(dir) {
	const out = [];
	for (const entry of readdirSync(dir, { withFileTypes: true })) {
		if (entry.name === "node_modules" || entry.name.startsWith(".")) continue;
		const full = path.join(dir, entry.name);
		if (entry.isDirectory()) out.push(...walk(full));
		else out.push(full);
	}
	return out;
}

// Drop full-line `//` comments and `/* */` blocks before scanning for import
// statements, so prose that *mentions* a filename (e.g. "complement the
// existing read-only PortfolioAdvisor.jsx (NOT a rewrite)") can't be
// misread as a real import and hide a genuine orphan. Only whole-line `//`
// comments are stripped (not trailing inline ones) to avoid mangling a real
// code line that happens to contain "//" inside a string later on it.
function stripComments(text) {
	return text
		.replace(/\/\*[\s\S]*?\*\//g, "")
		.split("\n")
		.map((line) => (/^\s*\/\//.test(line) ? "" : line))
		.join("\n");
}

// Matches both static (`import X from '...'`, `import '...'`) and dynamic
// (`import('...')`) forms. Bare/package specifiers (no leading '.') are
// ignored on purpose — this script only tracks intra-src references.
const IMPORT_RE = /\bimport\s*\(?\s*(?:[^'"()]*?\bfrom\s+)?['"](\.[^'"]+)['"]/g;

function resolveSpecifier(specifier, fromFile) {
	const base = path.resolve(path.dirname(fromFile), specifier);
	for (const ext of RESOLVE_EXTS) {
		const candidate = base + ext;
		if (existsSync(candidate)) return candidate;
	}
	for (const ext of [".js", ".jsx"]) {
		const candidate = path.join(base, `index${ext}`);
		if (existsSync(candidate)) return candidate;
	}
	return null;
}

const allSrcFiles = walk(SRC_DIR);
const importedFiles = new Set();

function collectFrom(text, fromFile) {
	for (const match of stripComments(text).matchAll(IMPORT_RE)) {
		const resolved = resolveSpecifier(match[1], fromFile);
		if (resolved) importedFiles.add(resolved);
	}
}

for (const file of allSrcFiles) {
	if (!SCAN_EXTS.has(path.extname(file))) continue;
	collectFrom(readFileSync(file, "utf8"), file);
}

// index.html can reference a src file directly (the Vite entry script tag).
if (existsSync(INDEX_HTML)) {
	collectFrom(readFileSync(INDEX_HTML, "utf8"), path.join(UI_ROOT, "index.html"));
}

const orphans = [];
for (const dir of CHECK_DIRS) {
	if (!existsSync(dir)) continue;
	for (const file of walk(dir)) {
		const rel = path.relative(SRC_DIR, file);
		if (ALLOWLIST.has(rel)) continue;
		if (!importedFiles.has(file)) orphans.push(file);
	}
}

if (orphans.length > 0) {
	console.error(
		"check-orphans: found file(s) with zero importers under src/components/ or src/data/:",
	);
	for (const f of orphans) console.error(`  - ${path.relative(UI_ROOT, f)}`);
	console.error(
		"\nEither delete the dead file, or — if it's a genuine entry point this " +
			"script can't trace (verify first) — add it to ALLOWLIST in " +
			"scripts/check-orphans.mjs with a comment explaining why.",
	);
	process.exit(1);
}

console.log(
	`check-orphans: OK (${allSrcFiles.length} src files scanned, 0 orphans in components/ or data/)`,
);
