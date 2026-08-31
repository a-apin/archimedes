#!/usr/bin/env node
/**
 * Adversarial check for the `.openwikiignore` read boundary.
 *
 * `.openwikiignore` is an ALLOW-LIST: it excludes the whole repository and
 * re-includes one slice. That shape is easy to get subtly wrong — gitignore
 * semantics are last-match-wins, so a rule added in the wrong place silently
 * re-admits everything, and OpenWiki then reads (and can reproduce in generated
 * prose) source it was never meant to see.
 *
 * A boundary is only a boundary if something has been shown to be REJECTED by
 * it. This script builds the paths that SHOULD be excluded — path traversal out
 * of the slice, alternate case, backslash spellings, secrets nested inside the
 * allowed slice — and asserts the matcher excludes them, alongside the paths
 * that must stay readable.
 *
 * It runs against OpenWiki's own matcher, not a reimplementation, so it cannot
 * pass while the real boundary fails. That means OpenWiki must be installed:
 *
 *   OPENWIKI_DIR=/path/to/dir/containing/node_modules/openwiki \
 *     node scripts/check_openwiki_ignore.mjs
 *
 * Exit 0 = every case behaved as required. Exit 1 = the boundary is wrong.
 * Exit 2 = OpenWiki is not installed (the check did not run; not a pass).
 *
 * WHEN TO RUN IT: any time `.openwikiignore` changes — in particular when
 * widening the allow-list by one slice, which is the documented way to grow the
 * wiki (see docs/decisions/tooling-adoptions-2026-08.md). Add the new slice's
 * paths to ALLOWED and a neighbouring out-of-slice path to DENIED first, and
 * confirm the DENIED one fails before the rule is added.
 */
import { fileURLToPath } from "node:url";
import path from "node:path";

const REPO_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

const openWikiDir = process.env.OPENWIKI_DIR ?? path.join(REPO_ROOT, "..", "tools", "openwiki");
const matcherModule = path.join(
  openWikiDir,
  "node_modules",
  "openwiki",
  "dist",
  "agent",
  "openwiki-ignore.js",
);

let OpenWikiIgnore;
try {
  ({ OpenWikiIgnore } = await import(matcherModule));
} catch {
  console.error(
    `SKIPPED: could not load OpenWiki's ignore matcher from\n  ${matcherModule}\n` +
      `Install OpenWiki (npm install openwiki@0.4.3) and set OPENWIKI_DIR to the\n` +
      `directory holding its node_modules. This is a skip, not a pass.`,
  );
  process.exit(2);
}

/** Paths that MUST remain readable, as [path, isDirectory]. */
const ALLOWED = [
  ["docs", true],
  ["docs/quant", true],
  ["docs/quant/methodology.md", false],
  ["docs/quant/README.md", false],
  ["docs/quant/admission-criteria.md", false],
  // OpenWiki's own output tree and marker files, or the run cannot write.
  ["openwiki", true],
  ["openwiki/quickstart.md", false],
  ["openwiki/rigor/admission-gate.md", false],
  ["AGENTS.md", false],
  ["CLAUDE.md", false],
];

/** Paths that MUST be excluded, as [path, isDirectory]. */
const DENIED = [
  // Out of slice.
  ["backend/archimedes/main.py", false],
  ["contracts/src/Vault.sol", false],
  ["ui/src/App.jsx", false],
  ["README.md", false],
  ["docs/README.md", false],
  ["docs/api", true],
  ["docs/api/generation.md", false],
  ["docs/architecture.md", false],
  ["docs/adr/non-custodial-vault-owner-agent.md", false],
  ["docs/decisions/tooling-adoptions-2026-08.md", false],
  // Hard denies must survive even under a re-included ancestor.
  ["node_modules/foo/index.js", false],
  ["docs/quant/node_modules/x.js", false],
  ["docs/quant/secrets.env", false],
  ["docs/quant/.env", false],
  ["docs/quant/id_rsa.pem", false],
  ["docs/quant/deploy.key", false],
  ["openwiki/.env", false],
  ["openwiki/node_modules/x.js", false],
  // Alternate spellings must not dodge an anchored rule.
  ["./backend/archimedes/main.py", false],
  ["/backend/archimedes/main.py", false],
  ["docs/quant/../api/generation.md", false],
  ["docs/quant/../../backend/archimedes/main.py", false],
  ["../../../etc/passwd", false],
  ["BACKEND/ARCHIMEDES/MAIN.PY", false],
  ["docs\\api\\generation.md", false],
];

const ignore = await OpenWikiIgnore.load(REPO_ROOT);
if (!ignore.isActive) {
  console.error("FAIL: .openwikiignore parsed to zero usable rules — enforcement is a no-op.");
  process.exit(1);
}

let failures = 0;
for (const [cases, expected, label] of [
  [ALLOWED, false, "readable"],
  [DENIED, true, "excluded"],
]) {
  for (const [candidate, isDirectory] of cases) {
    const actual = ignore.ignores(candidate, isDirectory);
    if (actual !== expected) {
      failures += 1;
      console.error(`FAIL: ${candidate} should be ${label}, matcher says ignores=${actual}`);
    }
  }
}

const total = ALLOWED.length + DENIED.length;
console.log(
  `.openwikiignore: ${total - failures}/${total} cases correct ` +
    `(${ALLOWED.length} must stay readable, ${DENIED.length} must be excluded), ` +
    `${ignore.rules.length} rules active.`,
);
process.exit(failures === 0 ? 0 : 1);
