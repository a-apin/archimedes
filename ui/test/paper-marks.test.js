import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import {
	markAnnouncement,
	markLabel,
	marksStalenessNote,
	marksUnavailableNote,
	noMarksNote,
} from "../src/paperCopy.js";

// Intraday marks (design §5.1). A mark re-PRICES the position the daily replay
// established; it never re-decides it. The card's job is to say exactly that
// much and no more, which is what every assertion below pins.

const MARK = { portfolio_value: 1.0042, ts: "2026-08-30T14:45:00Z", is_delayed: true };

// ── markLabel: never a bare number ──────────────────────────────────────────

test("markLabel: renders the value WITH its as-of time and the delayed flag", () => {
	assert.equal(markLabel(MARK), "+0.42% · as of 14:45 UTC · delayed");
});

test("markLabel: never renders a number without an as-of time", () => {
	// §2.4 rule 3. A percentage on its own is a claim about *now* that no mark
	// can support — the price behind it was observed at a stated past instant.
	for (const mark of [MARK, { ...MARK, is_delayed: false }, { ...MARK, portfolio_value: 0.9 }]) {
		const label = markLabel(mark);
		assert.match(label, /%/);
		assert.match(label, /as of \d{2}:\d{2} UTC/);
	}
});

test("markLabel: says 'delayed' only when the row says so — it is a stored fact, not a guess", () => {
	assert.match(markLabel(MARK), /delayed/);
	assert.doesNotMatch(markLabel({ ...MARK, is_delayed: false }), /delayed/);
});

test("markLabel: an absent mark is an em-dash, never a fabricated +0.00%", () => {
	// The exact bug formatTotalReturn was extracted to fix (#1362), in a new
	// place: an unmeasured value must not get a measured look.
	for (const absent of [null, undefined]) {
		assert.equal(markLabel(absent), "—");
	}
});

test("markLabel: a genuinely marked flat 0.00% still prints — the gate is existence, never the value", () => {
	// The mirror image of the test above, and the one that keeps the fix from
	// over-correcting: 0.00% observed at 14:45 is a measurement and a fact.
	const flat = { portfolio_value: 1.0, ts: "2026-08-30T14:45:00Z", is_delayed: false };
	assert.equal(markLabel(flat), "+0.00% · as of 14:45 UTC");
});

test("markLabel: a value with no usable timestamp is withheld entirely, not shown half-stated", () => {
	assert.equal(markLabel({ portfolio_value: 1.0042, ts: "not-a-timestamp", is_delayed: true }), "—");
	assert.equal(markLabel({ portfolio_value: 1.0042, is_delayed: true }), "—");
	assert.doesNotMatch(markLabel({ portfolio_value: 1.0042, ts: "nope" }), /Invalid Date/);
});

test("markLabel: never leaks the raw ISO machine timestamp into the label", () => {
	assert.doesNotMatch(markLabel(MARK), /\d{4}-\d{2}-\d{2}T/);
});

test("markLabel: negative moves carry their sign", () => {
	assert.match(markLabel({ ...MARK, portfolio_value: 0.9873 }), /^-1\.27%/);
});

// ── markAnnouncement: the number must reach a screen reader ─────────────────

test("markAnnouncement: names the value, the as-of time, and that it is unsettled", () => {
	const said = markAnnouncement(MARK);
	assert.match(said, /0\.42 percent/);
	assert.match(said, /14:45 UTC/);
	assert.match(said, /unsettled/i);
	assert.match(said, /daily ledger is the track record/i);
});

test("markAnnouncement: says nothing at all when there is nothing to say", () => {
	assert.equal(markAnnouncement(null), "");
	assert.equal(markAnnouncement({ portfolio_value: 1.0, ts: "nope" }), "");
});

// ── marksStalenessNote: a frozen number reads as frozen, not as broken ──────

test("marksStalenessNote: silent while the value is still moving", () => {
	const now = Date.parse("2026-08-30T14:50:00Z");
	assert.equal(marksStalenessNote({ ts: "2026-08-30T14:45:00Z" }, now), null);
});

test("marksStalenessNote: names the last observation once it stops moving", () => {
	// The #1378 shape — an equity deployment's value is GENUINELY frozen over a
	// weekend, and must read as "last marked Friday 16:00", not as a ticker that
	// broke.
	const now = Date.parse("2026-08-31T14:00:00Z"); // Monday
	const note = marksStalenessNote({ ts: "2026-08-28T20:00:00Z" }, now); // Friday
	assert.match(note, /Last marked Fri 20:00 UTC/);
});

test("marksStalenessNote: never asserts a market state the client cannot observe", () => {
	// #1378 is exactly the defect of labelling a gap with a window nobody
	// measured. The client knows the observation is old; it does not know why.
	const now = Date.parse("2026-08-31T14:00:00Z");
	const note = marksStalenessNote({ ts: "2026-08-28T20:00:00Z" }, now);
	assert.doesNotMatch(note, /market (is )?closed|halted|holiday|weekend/i);
	assert.match(note, /no newer price has been observed/i);
});

test("marksStalenessNote: the freshness window follows the configured cadence", () => {
	const now = Date.parse("2026-08-30T15:45:00Z");
	const mark = { ts: "2026-08-30T14:45:00Z" }; // 60 minutes old
	assert.equal(marksStalenessNote(mark, now, 60), null); // 2 x 60min cadence: fresh
	assert.notEqual(marksStalenessNote(mark, now, 15), null); // 2 x 15min cadence: stale
});

test("marksStalenessNote: degrades cleanly on a missing or malformed mark", () => {
	assert.equal(marksStalenessNote(null, Date.now()), null);
	assert.equal(marksStalenessNote({ ts: "not-a-timestamp" }, Date.now()), null);
});

// ── noMarksNote: an absence with a reason ───────────────────────────────────

test("noMarksNote: an active deployment is told when its first value arrives", () => {
	const note = noMarksNote("active");
	assert.match(note, /No live value yet/);
	assert.match(note, /next 15-minute tick/);
});

test("noMarksNote: a stopped deployment is never told to wait for a mark that will never come", () => {
	// The marks loop filters on STATUS_ACTIVE, so a stopped deployment will
	// never be marked. Same status-gating lesson as driftTooltip.
	const note = noMarksNote("stopped");
	assert.doesNotMatch(note, /yet|next 15-minute tick/);
	assert.match(note, /marks stop when a deployment is stopped/);
});

test("noMarksNote: no state ever renders a number", () => {
	for (const status of ["active", "stopped", undefined]) {
		assert.doesNotMatch(noMarksNote(status), /\d+\.\d+%/);
	}
});

// ── marksUnavailableNote: a partial failure, stated ─────────────────────────

test("marksUnavailableNote: never lets a raw 'Backend returned NNN' reach the user", () => {
	const err = { status: 503, message: "Backend returned 503" };
	const note = marksUnavailableNote(err);
	assert.doesNotMatch(note, /Backend returned/);
	assert.match(note, /Live value unavailable/);
});

test("marksUnavailableNote: a network failure keeps its own informative message", () => {
	assert.match(marksUnavailableNote({ message: "Failed to fetch" }), /Failed to fetch/);
});

test("marksUnavailableNote: states unavailability rather than showing a number", () => {
	// A stale number under a fresh-looking label is the same defect as writing
	// a duplicated stale row (§2.4 rule 4), moved into the UI.
	assert.doesNotMatch(marksUnavailableNote({ status: 500 }), /\d+\.\d+%|as of/);
});

// ── Wiring: PaperTrading.jsx renders these, and only these ──────────────────

const paperTrading = readFileSync(new URL("../src/components/PaperTrading.jsx", import.meta.url), "utf8");

test("PaperTrading.jsx renders the live value through LiveValue, not an inline number", () => {
	assert.match(paperTrading, /<LiveValue\s+dep=\{dep\}/);
	assert.match(paperTrading, /markLabel\(latest\)/);
});

test("PaperTrading.jsx polls the marks endpoint and does not open an SSE stream", () => {
	// A 15-minute cadence does not justify an SSE channel; the generation stream
	// already cost this repo one reproducible drop-under-load incident (#891).
	assert.match(paperTrading, /\/marks\?limit=/);
	assert.match(paperTrading, /setInterval\(loadMarks, MARKS_POLL_MS\)/);
	assert.match(paperTrading, /clearInterval\(timer\)/);
	assert.doesNotMatch(paperTrading, /EventSource|new WebSocket/);
});

test("PaperTrading.jsx never carries stale marks forward across a failed poll", () => {
	// The rebuilt map is assigned wholesale — there is no spread of the previous
	// marks state anywhere, so a deployment whose poll failed cannot keep
	// rendering the last value it happened to hold under a live-looking label.
	assert.doesNotMatch(paperTrading, /\.\.\.marks\b/);
	assert.match(paperTrading, /setMarks\(nextMarks\)/);
});

test("PaperTrading.jsx draws the intraday tail distinguishably from the settled line", () => {
	// Only the settled daily ledger carries to mainnet, so the visual break is
	// load-bearing, not decoration.
	assert.match(paperTrading, /<Sparkline series=\{dep\.series\} intraday=\{marks\[dep\.deployment_id\]\}/);
	assert.match(paperTrading, /strokeDasharray=/);
});

test("PaperTrading.jsx routes the marks failure through marksUnavailableNote, never a bare message", () => {
	assert.match(paperTrading, /marksUnavailableNote\(res\.reason\)/);
	assert.doesNotMatch(paperTrading, /nextErrors\[id\] = res\.reason/);
});
