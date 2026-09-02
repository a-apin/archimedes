/**
 * Issue #1750 — the landing hero rendered as a solid black void.
 *
 * The hero's children carry no opacity of their own; the only opacity the
 * stylesheet stated was `from { opacity: 0 }` in the `public-arrive` keyframe,
 * and both rules that used it asked for `animation-fill-mode: both`. `both`
 * means "apply the first frame before the animation starts AND the last frame
 * after it ends" — so opacity 0 was the hero's stylesheet-supplied resting
 * value, and whether anyone ever saw the headline depended on the animation
 * running and on a `to` frame the UA had to synthesise because none was
 * written. Over `.public-hero__stage`'s near-black `--public-stage` (#0c0c11),
 * a hero that loses that bet is a black rectangle.
 *
 * These are source-text checks against ui/src/App.css. They cannot prove what
 * a browser paints; what they pin is the invariant that made the void
 * possible — that the hero must not depend on an animation for its resting
 * appearance:
 *   1. the keyframe states where it ends, and it ends visible;
 *   2. any rule that keeps a forwards fill (`forwards`/`both`) may only do so
 *      while that explicit, visible end frame exists;
 *   3. reduced-motion visitors get `animation: none`, not a shortened fade;
 *   4. no rule that uses the animation writes an at-rest `opacity: 0` back
 *      into the cascade.
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const raw = readFileSync(new URL("../src/App.css", import.meta.url), "utf8");

// Blank comments out (preserving length and newlines, so offsets and line
// numbers stay true) — prose about `animation` must never read as a rule.
const css = raw.replace(/\/\*[\s\S]*?\*\//g, (match) =>
	match.replace(/[^\n]/g, " "),
);

const NAME = "public-arrive";
const FILL_KEYWORDS = new Set(["none", "forwards", "backwards", "both"]);

const lineOf = (index) => css.slice(0, index).split("\n").length;

/** Contents of the block whose opening brace is at `openIndex`. */
function blockAt(openIndex) {
	let depth = 0;
	for (let i = openIndex; i < css.length; i += 1) {
		if (css[i] === "{") depth += 1;
		else if (css[i] === "}") {
			depth -= 1;
			if (depth === 0) return css.slice(openIndex + 1, i);
		}
	}
	throw new Error(`src/App.css: unbalanced braces from line ${lineOf(openIndex)}`);
}

/** Flat `selector { declarations }` pairs inside a block that does not nest. */
function flatRules(body) {
	return [...body.matchAll(/([^{}]+)\{([^{}]*)\}/g)].map((match) => ({
		selector: match[1].replace(/\s+/g, " ").trim(),
		declarations: match[2],
	}));
}

function keyframesBody(name) {
	const at = css.search(new RegExp(`@keyframes\\s+${name}\\s*\\{`));
	assert.notEqual(
		at,
		-1,
		`src/App.css: @keyframes ${name} is gone. If the hero's entrance was ` +
			`retired, delete this guard in the same change; do not leave it ` +
			`passing on an animation that no longer exists.`,
	);
	return blockAt(css.indexOf("{", at));
}

/** Every rule whose own declarations name the animation. */
function rulesUsing(name) {
	const declaration = new RegExp(
		`animation(?:-name)?\\s*:[^;{}]*\\b${name}\\b[^;{}]*`,
		"g",
	);
	return [...css.matchAll(declaration)].map((match) => {
		const open = css.lastIndexOf("{", match.index);
		const start =
			Math.max(css.lastIndexOf("}", open), css.lastIndexOf("{", open - 1)) + 1;
		return {
			selector: css.slice(start, open).replace(/\s+/g, " ").trim(),
			declarations: blockAt(open),
			shorthand: match[0].replace(/\s+/g, " ").trim(),
			line: lineOf(match.index),
		};
	});
}

/** The fill mode a rule ends up with: longhand wins, else the shorthand. */
function fillModeOf(rule) {
	const longhand = rule.declarations.match(
		/animation-fill-mode\s*:\s*([a-zA-Z-]+)/,
	);
	if (longhand) return longhand[1].toLowerCase();
	const fromShorthand = rule.shorthand
		.split(/[\s:]+/)
		.map((token) => token.toLowerCase())
		.filter((token) => token !== NAME && FILL_KEYWORDS.has(token));
	// A CSS shorthand takes the fill mode from its last such keyword.
	return fromShorthand.at(-1) ?? "none";
}

const frames = flatRules(keyframesBody(NAME)).map((frame) => ({
	offsets: frame.selector
		.split(",")
		.map((offset) => offset.trim().toLowerCase())
		.filter(Boolean),
	declarations: frame.declarations,
}));
const endFrame = frames.find(
	(frame) => frame.offsets.includes("to") || frame.offsets.includes("100%"),
);
const endsVisible = Boolean(endFrame) && /opacity\s*:\s*1\b/.test(endFrame.declarations);
const users = rulesUsing(NAME);

test(`@keyframes ${NAME} states an end frame, and it ends visible`, () => {
	assert.ok(
		endFrame,
		`src/App.css: @keyframes ${NAME} has no \`to\` (or \`100%\`) frame. With ` +
			`only a \`from { opacity: 0 }\` frame the end state is implicit — the ` +
			`browser has to synthesise it from the element's underlying style — so ` +
			`nothing in the stylesheet ever says the hero is visible. Write the ` +
			`end state: \`to { opacity: 1; transform: none; }\`. (#1750)`,
	);
	assert.ok(
		endsVisible,
		`src/App.css: the \`to\` frame of @keyframes ${NAME} does not set ` +
			`\`opacity: 1\`. The frame the hero finishes on has to be a visible ` +
			`one. (#1750)`,
	);
	assert.match(
		endFrame.declarations,
		/transform\s*:\s*none\b/,
		`src/App.css: the \`to\` frame of @keyframes ${NAME} does not reset ` +
			`\`transform\`, so a forwards fill would leave the hero holding the ` +
			`10px entrance offset. (#1750)`,
	);
});

test("no public-arrive rule keeps a forwards fill without a visible end frame", () => {
	assert.ok(
		users.length >= 2,
		`src/App.css: expected the hero copy and the product frame to use ` +
			`${NAME}; found ${users.length} rule(s). Update this guard along with ` +
			`the hero if that changed on purpose.`,
	);
	const covered = users.map((rule) => rule.selector).join(" ");
	assert.match(covered, /public-hero__copy/, "hero copy no longer animates");
	assert.match(covered, /public-product-frame/, "product frame no longer animates");

	for (const rule of users) {
		const fill = fillModeOf(rule);
		if (fill !== "forwards" && fill !== "both") continue;
		assert.ok(
			endsVisible,
			`src/App.css:${rule.line}: \`${rule.selector}\` uses ` +
				`\`${rule.shorthand}\` — a \`${fill}\` fill holds the animation's ` +
				`last frame after it ends — but @keyframes ${NAME} has no \`to\` ` +
				`frame ending at \`opacity: 1\`. That is exactly #1750: the hero's ` +
				`resting appearance is supplied by an animation whose end state ` +
				`nothing states. Either write the end frame, or drop to ` +
				`\`backwards\` so the element rests on its own cascade.`,
		);
	}
});

test("reduced motion turns the hero entrance off rather than shortening it", () => {
	const reduceBodies = [...css.matchAll(/@media[^{]*\{/g)]
		.filter((match) => /prefers-reduced-motion\s*:\s*reduce/.test(match[0]))
		.map((match) => blockAt(match.index + match[0].length - 1));
	const silenced = reduceBodies
		.flatMap(flatRules)
		.filter((rule) => /animation(?:-name)?\s*:\s*none\b/.test(rule.declarations))
		.map((rule) => rule.selector)
		.join(" ");
	const explain =
		"src/App.css: under `prefers-reduced-motion: reduce` the hero must get " +
		"`animation: none`. A global `animation-duration: 0.01ms !important` is " +
		"not the same thing — it still runs an animation, and an animation that " +
		"has not started yet is an animation holding opacity 0. (#1750)";
	assert.match(silenced, /public-hero__copy/, explain);
	assert.match(silenced, /public-product-frame/, explain);
});

test("no public-arrive rule writes an at-rest opacity: 0 into the cascade", () => {
	for (const rule of users) {
		assert.doesNotMatch(
			rule.declarations,
			/opacity\s*:\s*0(?![.\d%])/,
			`src/App.css:${rule.line}: \`${rule.selector}\` declares ` +
				`\`opacity: 0\` outside the keyframe, which makes invisible the ` +
				`hero's resting state again no matter what the animation does. (#1750)`,
		);
	}
});
