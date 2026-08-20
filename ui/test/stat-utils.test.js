import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { median } from "../src/statUtils.js";

// ── #1322: group "24h change" headline must be robust to a single outlier ──
// A prior arithmetic-mean implementation let one bad tick (or one genuinely
// large single mover) drag a whole group's headline by tens of points. The
// median is robust to a single outlier of any magnitude — this is the
// property under test, not just "median math is correct".

test("median: odd-length array returns the middle value", () => {
	assert.equal(median([3, 1, 2]), 2);
});

test("median: even-length array averages the two middle values", () => {
	assert.equal(median([1, 2, 3, 4]), 2.5);
});

test("median: empty array returns null, not NaN or 0", () => {
	assert.equal(median([]), null);
});

test("median: single value returns that value", () => {
	assert.equal(median([42]), 42);
});

test("median: does not mutate its input array", () => {
	const input = [5, 3, 1, 4, 2];
	const copy = [...input];
	median(input);
	assert.deepEqual(input, copy);
});

test("median is robust to one outlier of any magnitude (the actual #1322 bug)", () => {
	// The reported live defect: sJUP's corrupted +1483.08% sat among a crypto
	// group whose real median 24h move was ~6.70%, but an arithmetic mean
	// reported "+29.82% avg 24h" — the outlier moved the headline by ~23 points.
	const realistic = [2.1, -0.8, 1.4, 3.2, -1.1, 0.6, 6.7, 4.4, -2.3, 0.2];
	const withOutlier = [...realistic, 1483.08];

	const meanOf = (nums) => nums.reduce((a, b) => a + b, 0) / nums.length;

	const medianBefore = median(realistic);
	const medianAfter = median(withOutlier);
	const meanBefore = meanOf(realistic);
	const meanAfter = meanOf(withOutlier);

	// The median barely moves for one outlier among 11 values...
	assert.ok(Math.abs(medianAfter - medianBefore) < 1, `median moved by ${medianAfter - medianBefore}`);
	// ...while the mean is dragged by well over 100 points — proving *why*
	// the median was the correct fix, not merely that it computes correctly.
	assert.ok(meanAfter - meanBefore > 100, `mean only moved by ${meanAfter - meanBefore}`);
});

// ── Wiring: both group-headline call sites use the shared median, and the
// arithmetic-mean pattern the issue names is fully gone from both ──────────

const explorePage = readFileSync(new URL("../src/components/Explore.jsx", import.meta.url), "utf8");
const groupModal = readFileSync(new URL("../src/components/AssetGroupModal.jsx", import.meta.url), "utf8");

test("Explore.jsx group headline imports and calls the shared median, not a local mean", () => {
	assert.match(explorePage, /import \{ median \} from '\.\.\/statUtils'/);
	assert.match(explorePage, /median\(vals\)/);
	assert.doesNotMatch(explorePage, /reduce\(\(a, b\) => a \+ b, 0\) \/ vals\.length/);
});

test("AssetGroupModal.jsx aggregate stat imports and calls the shared median, not a local mean", () => {
	assert.match(groupModal, /import \{ median \} from '\.\.\/statUtils'/);
	assert.match(groupModal, /median\(vals\)/);
	assert.doesNotMatch(groupModal, /reduce\(\(a, b\) => a \+ b, 0\) \/ vals\.length/);
});

test("the group headline labels no longer claim 'avg' when the value is a median", () => {
	assert.doesNotMatch(explorePage, />\s*avg 24h\s*</);
	assert.doesNotMatch(groupModal, /Avg 24h change/);
	assert.match(explorePage, /median 24h/);
	assert.match(groupModal, /Median 24h change/);
});
