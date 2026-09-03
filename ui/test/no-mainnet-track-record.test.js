// Claim-integrity guard for the paper-trading track-record copy (#1807).
//
// Until this change, four surfaces told the reader that the settled paper
// ledger is "the track record that carries to mainnet":
//
//   PaperTrading.jsx    "This is the track record that carries to mainnet."
//   StrategyPassport.jsx "building the track record that carries to mainnet."
//   Leaderboard.jsx     "Build your track record now; it carries to mainnet."
//   paperCopy.js        the same sentence, in the comment the other three quote.
//
// The Arc mainnet cutover was CANCELLED by owner call on 2026-08-30
// (issue #1240): Archimedes stays a testnet product until legal/regulatory
// review and sustained traction justify charging real money, and no date is
// named. So the sentence is a promise about an event that is not scheduled —
// false on every surface that carried it, not merely optimistic.
//
// Same idiom as oracle-copy.test.js and roadmap-copy.test.js: a raw source-text
// scan (readFileSync, no JSX parsing) with anti-vacuity coverage — every
// pattern must still match the exact literal this change removed, so a guard
// that stops matching anything fails loudly instead of silently guarding
// nothing.
//
// Why the ban is the WORD and not just the sentence. Scrubbing one phrasing
// buys nothing: the next writer reaches for "ahead of mainnet", "when we go
// live on mainnet", "mainnet-ready". These four files are the paper-trading
// copy surfaces and none of them has any legitimate reason to name mainnet
// while the cutover is cancelled — unlike chain-config.js, Landing.jsx,
// PublicLayout.jsx, Security.jsx and Architecture.jsx, which say "NO mainnet
// money" and are the honest negations we want to keep. So the word is banned
// on these four, with an OWNER-DATED escape hatch: a line carrying
//
//     mainnet-claim-exemption: owner=<name> date=<YYYY-MM-DD> issue=#<n>
//
// is skipped. The marker must be well-formed to count — writing the bare word
// "exemption" in a comment does not silence the guard (proved below), so the
// escape hatch costs an owner, a date and an issue rather than a shrug.

import { readFileSync, readdirSync, statSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";
import assert from "node:assert/strict";
import test from "node:test";

function repoFile(rel) {
	return new URL(`../${rel}`, import.meta.url);
}

//: The paper-trading copy surfaces. Every one of these carried the retracted
//: sentence, and none of them has another reason to name mainnet today.
const PAPER_SURFACES = [
	"src/components/PaperTrading.jsx",
	"src/components/StrategyPassport.jsx",
	"src/components/Leaderboard.jsx",
	"src/paperCopy.js",
];

//: The exact literals this change removed, file by file. Used only as
//: anti-vacuity fixtures — if `mainnetMentions` stops flagging these, the
//: scanner is broken and every assertion below is worthless.
const RETRACTED_LITERALS = [
	["PaperTrading.jsx", "This is the\n          track record that carries to mainnet."],
	["StrategyPassport.jsx", "building the track record that carries to mainnet."],
	["Leaderboard.jsx", "Build your track record now;\n                  it carries to mainnet.</>"],
	["paperCopy.js", "// the track record that carries to mainnet; a mark is an unsettled decoration"],
	["a rephrasing the sentence-scrub would have missed", "Deploy now and your ledger is ready ahead of mainnet."],
];

const EXEMPTION_RE = /mainnet-claim-exemption:\s*owner=\S+\s+date=\d{4}-\d{2}-\d{2}\s+issue=#\d+/;

/** Lines that name mainnet without a well-formed owner-dated exemption. */
function mainnetMentions(source) {
	return source
		.split("\n")
		.map((text, i) => ({ line: i + 1, text }))
		.filter(({ text }) => /mainnet/i.test(text) && !EXEMPTION_RE.test(text));
}

test("mainnetMentions flags every literal this change removed", () => {
	for (const [where, literal] of RETRACTED_LITERALS) {
		assert.ok(
			mainnetMentions(literal).length > 0,
			`the scanner no longer flags the ${where} literal ${JSON.stringify(literal)} — it is guarding nothing`,
		);
	}
});

test("an owner-dated exemption is honoured, and a hand-waved one is not", () => {
	const exempt = "// mainnet-claim-exemption: owner=dbrowneup date=2026-09-03 issue=#1807 — mainnet named on purpose";
	assert.deepEqual(mainnetMentions(exempt), [], "a well-formed exemption must silence the line");

	for (const sloppy of [
		"// mainnet-claim-exemption: this one is fine, trust me — mainnet",
		"// exemption granted: mainnet",
		"// mainnet-claim-exemption: owner=dbrowneup issue=#1807 — mainnet",
		"// mainnet-claim-exemption: owner=dbrowneup date=soon issue=#1807 — mainnet",
	]) {
		assert.equal(
			mainnetMentions(sloppy).length,
			1,
			`a marker missing an owner, a date or an issue must NOT silence the line: ${sloppy}`,
		);
	}
});

test("no paper-trading surface names mainnet (#1807, cutover cancelled by #1240)", () => {
	for (const rel of PAPER_SURFACES) {
		const found = mainnetMentions(readFileSync(repoFile(rel), "utf8"));
		assert.deepEqual(
			found,
			[],
			`ui/${rel} names mainnet on: ` +
				found.map(({ line, text }) => `${line}: ${text.trim()}`).join(" | ") +
				". The Arc mainnet cutover is cancelled (#1240) — say what is true today (a paper " +
				"track record on Arc testnet, no real funds), or add an owner-dated " +
				"`mainnet-claim-exemption: owner=<name> date=<YYYY-MM-DD> issue=#<n>` on the line.",
		);
	}
});

test('no file under ui/src says the ledger "carries to mainnet"', () => {
	// The narrow phrase, repo-wide across the UI, so the sentence cannot simply
	// migrate to a fifth file that is not on the list above.
	const srcRoot = fileURLToPath(repoFile("src"));
	const offenders = [];
	const walk = (dir) => {
		for (const entry of readdirSync(dir)) {
			const full = path.join(dir, entry);
			if (statSync(full).isDirectory()) walk(full);
			else if (/\.(js|jsx)$/.test(entry) && /carries to mainnet/i.test(readFileSync(full, "utf8"))) {
				offenders.push(path.relative(srcRoot, full));
			}
		}
	};
	walk(srcRoot);
	assert.deepEqual(offenders, [], `these ui/src files still say "carries to mainnet": ${offenders.join(", ")}`);
});

test("the paper-trading copy says what is true instead: Arc testnet, no real funds", () => {
	// A retraction that leaves a hole is not a fix — the reader still has to be
	// told what the ledger IS. Checked over PaperTrading.jsx + paperCopy.js
	// together because the intro copy legitimately lives in either one (#1805
	// moves it into a pinned constant in paperCopy.js).
	//: `assert.ok` rather than `assert.match` on purpose — a failing `assert.match`
	//: prints the entire file it was handed, which buries the one sentence the
	//: reader needs to see.
	const intro =
		readFileSync(repoFile("src/components/PaperTrading.jsx"), "utf8") +
		readFileSync(repoFile("src/paperCopy.js"), "utf8");
	const passport = readFileSync(repoFile("src/components/StrategyPassport.jsx"), "utf8");

	for (const [where, source] of [
		["PaperTrading.jsx + paperCopy.js", intro],
		["StrategyPassport.jsx", passport],
	]) {
		for (const phrase of [/paper track record on Arc testnet/, /no real funds/]) {
			assert.ok(
				phrase.test(source),
				`${where} no longer says ${phrase} — the mainnet claim was scrubbed and nothing true ` +
					"replaced it. A retraction that leaves a hole still leaves the reader guessing what " +
					"the paper ledger is.",
			);
		}
	}
});
