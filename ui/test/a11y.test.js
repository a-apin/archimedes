import assert from "node:assert/strict";
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";

// WCAG 2.2 AA regression guards.
//
// Same shape as app-visuals.test.js / public-visuals.test.js: readFileSync +
// regex pins on the source, no DOM. These pin the specific accessibility
// properties that a later refactor is most likely to delete by accident —
// a token value, a live-region attribute, a real <button> where a click-only
// <span> used to be. Every assertion here was confirmed to FAIL against the
// pre-fix tree.

const src = (p) => readFileSync(new URL(`../src/${p}`, import.meta.url), "utf8");

const css = src("App.css");
const app = src("App.jsx");
const authPage = src("components/AuthPage.jsx");
const corpusExplorer = src("components/CorpusExplorer.jsx");
const corpusKg = src("components/CorpusKG.jsx");
const customSelect = src("components/CustomSelect.jsx");
const explore = src("components/Explore.jsx");
const generate = src("components/Generate.jsx");
const generationStatus = src("components/GenerationStatus.jsx");
const generationStream = src("components/GenerationStream.jsx");
const layout = src("components/Layout.jsx");
const onboardingTour = src("components/OnboardingTour.jsx");
const paperTrading = src("components/PaperTrading.jsx");
const strategies = src("components/Strategies.jsx");
const walletConnect = src("components/WalletConnect.jsx");

// ── 1.4.3 Contrast (Minimum) ──────────────────────────────────────────────
//
// The auth screen renders outside both .app-site and .public-site, so it is
// the only surface left on the base palette — these three token values are
// what make its labels, its password rules and its links readable. The public
// --text-4 carries Architecture's error/loading copy. Ratios are against
// --surface-2 in the matching theme.

test("base and public palettes keep muted text above the 4.5:1 floor", () => {
	// #71717a was 3.73:1 on #16161a (dark) and 4.01:1 on #eceae2 (light).
	assert.match(css, /:root\s*\{[\s\S]*?--text-3:\s*#8a8a93;/);
	assert.match(
		css,
		/:root\[data-theme="light"\]\s*\{[\s\S]*?--text-3:\s*#62626b;/,
	);
	// #9c6b0b was 3.86:1 as link text on the light card.
	assert.match(
		css,
		/:root\[data-theme="light"\]\s*\{[\s\S]*?--accent:\s*#8a5f0a;/,
	);
	assert.match(
		css,
		/:root\[data-theme="light"\]\s*\{[\s\S]*?--accent-rgb:\s*138,\s*95,\s*10;/,
	);
	// Public --text-4 was #657a73 = 3.46:1 on the slate card, then #7d9189
	// (3.94:1 on the lighter --surface-3, a #1318 residual).
	assert.match(css, /\.public-site\s*\{[\s\S]*?--text-4:\s*#8b9f97;/);
	// --text-4 is a border/decoration token on the base palette (it is #3f3f46
	// there = 1.73:1) and must never be used as text on the auth screen.
	assert.doesNotMatch(authPage, /text-\[var\(--text-4\)\]/);
});

test("chart axis labels clear 4.5:1 on every card they are drawn on", () => {
	// Axis ticks are real 8-10px <text>. Neither shell overrides these tokens
	// and the asset modal portals outside both, so the BASE values are the
	// ones that must hold. 0.42 / 0.5 gave 3.88:1 and 3.74:1.
	assert.match(css, /--chart-label:\s*rgba\(255,\s*255,\s*255,\s*0\.52\);/);
	assert.match(css, /--chart-label:\s*rgba\(9,\s*9,\s*11,\s*0\.6\);/);
});

test("error text uses the defined --negative, never an undefined --danger", () => {
	// `--danger` is not defined in any stylesheet, so `var(--danger, #b91c1c)`
	// always resolved to the literal — 2.71:1 on the app card, for a
	// role="alert" message.
	const files = [];
	const walk = (dir) => {
		for (const entry of readdirSync(dir)) {
			const full = join(dir, entry);
			if (statSync(full).isDirectory()) walk(full);
			else if (/\.jsx?$/.test(entry)) files.push(full);
		}
	};
	walk(new URL("../src", import.meta.url).pathname);
	const offenders = files.filter((f) =>
		readFileSync(f, "utf8").includes("--danger"),
	);
	assert.deepEqual(offenders, []);
	assert.match(css, /--negative:\s*var\(--app-risk\);/);
});

// ── 1.4.11 Non-text Contrast / 2.4.7 Focus Visible ────────────────────────

test("form fields carry a 3:1 boundary token in both shells", () => {
	// Fields are filled with the canvas inside surface cards, so the fill gives
	// no cue (1.08:1) and --glass-border was 1.22-1.45:1.
	assert.match(css, /:root\s*\{[\s\S]*?--field-border:\s*rgba\(255,\s*255,\s*255,\s*0\.35\);/);
	assert.match(
		css,
		/:root\[data-theme="light"\]\s*\{[\s\S]*?--field-border:\s*rgba\(9,\s*9,\s*11,\s*0\.5\);/,
	);
	assert.match(
		css,
		/\.app-site\s*\{[\s\S]*?--field-border:\s*rgba\(225,\s*230,\s*222,\s*0\.45\);/,
	);
	assert.match(
		css,
		/:root\[data-theme="light"\] \.app-site\s*\{[\s\S]*?--field-border:\s*rgba\(8,\s*18,\s*24,\s*0\.5\);/,
	);
	assert.match(
		css,
		/^input,\nselect,\ntextarea \{[^}]*border: 1px solid var\(--field-border\);/m,
	);
	assert.match(
		css,
		/\.app-site \.chat-input,\n\.app-site input,\n\.app-site select,\n\.app-site textarea \{[^}]*border-color: var\(--field-border\);/,
	);
});

test("no rule strips the focus indicator from a control", () => {
	// `.app-site input:focus { outline: none }` out-specified
	// `.app-site :focus-visible` and deleted the 3px ring from every text
	// field in the shell; the base input rule did the same on the auth screen,
	// which has no :focus-visible rule of its own to fall back to.
	for (const block of [
		/^input,\nselect,\ntextarea \{[^}]*\}/m,
		/\.app-site \.chat-input:focus,[\s\S]*?\}/,
		/^\.cs-trigger \{[^}]*\}/m,
		/^\.catalog-search:focus,\n\.kg-search:focus \{[^}]*\}/m,
	]) {
		const match = css.match(block);
		assert.ok(match, `rule not found: ${block}`);
		assert.doesNotMatch(match[0], /outline:\s*none/);
	}
	assert.match(css, /\.app-site :focus-visible \{\n\toutline: 3px solid/);
});

// ── 2.4.11 Focus Not Obscured / 1.4.4 Resize Text ─────────────────────────

test("sticky topbar cannot cover a focused control", () => {
	// 56px bar + 3px outline + 3px offset.
	assert.match(css, /^html \{[^}]*scroll-padding-top: 64px;/m);
});

test("corpus category labels wrap instead of clipping under zoom", () => {
	const rule = css.match(/^\.bar-label,\n\.year-label \{[^}]*\}/m);
	assert.ok(rule);
	assert.doesNotMatch(rule[0], /width:\s*140px/);
	assert.doesNotMatch(rule[0], /white-space:\s*nowrap/);
	assert.match(rule[0], /flex: 0 1 140px/);
});

// ── 2.4.1 Bypass Blocks / 2.4.2 Page Titled ───────────────────────────────

test("the authenticated shell ships a skip link with a focusable target", () => {
	assert.match(layout, /className="app-skip-link" href="#app-content"/);
	assert.match(layout, /id="app-content"\s+tabIndex=\{-1\}/);
	// It cannot borrow .public-skip-link's colours — --public-paper /
	// --public-abyss are undefined inside .app-site.
	assert.match(css, /\.app-skip-link \{\n\tbackground: var\(--text-1\);/);
	// ...and it must out-stack the sidebar. `.sidebar` is position: fixed,
	// inset: 0 auto 0 0, opaque, z-index: 100, and comes LATER in the DOM than
	// the link, so at the shared block's z-index: 100 the sidebar won the paint
	// order and covered the link's entire focused box (top 10px / left 12px,
	// inside the 232px rail). Verified in a browser against the built CSS:
	// elementFromPoint at all three corners returned DIV.sidebar-brand.
	// `.app-skip-link` is declared twice — once in the shared geometry block
	// alongside `.public-skip-link`, once on its own — at equal specificity, so
	// the EFFECTIVE z-index is the last one declared. Read it that way rather
	// than matching the first rule that mentions the class.
	const zDecls = [
		...css.matchAll(/([^{}]*\.app-skip-link[^{}]*)\{([^}]*)\}/g),
	].flatMap((rule) =>
		[...rule[2].matchAll(/z-index:\s*(\d+);/g)].map((m) => Number(m[1])),
	);
	assert.ok(zDecls.length > 0, ".app-skip-link declares no z-index");
	const effectiveZ = zDecls[zDecls.length - 1];
	const sidebar = css.match(/^\.sidebar \{[^}]*\}/m);
	const sidebarZ = Number(sidebar[0].match(/z-index:\s*(\d+);/)[1]);
	assert.ok(
		effectiveZ > sidebarZ,
		`skip link z-index ${effectiveZ} must beat .sidebar's ${sidebarZ}`,
	);
});

test("a failed deep link does not share the landing page's title", () => {
	assert.match(app, /'not-found': 'Page not found · Archimedes'/);
	assert.match(app, /route\.kind === 'not-found' \? 'not-found' : route\.page/);
});

// ── 2.1.1 Keyboard / 4.1.2 Name, Role, Value ──────────────────────────────

test("filter and toggle pills are real buttons with a pressed state", () => {
	// Library tabs: activeTab defaults to 'generated', so click-only spans
	// pinned a keyboard user to that one view forever.
	assert.match(
		strategies,
		/<button\n\s+type="button"\n\s+className=\{`tag \$\{activeTab === 'generated'[\s\S]{0,120}aria-pressed=\{activeTab === 'generated'\}/,
	);
	assert.match(strategies, /aria-pressed=\{activeTab === 'examples'\}/);
	assert.match(strategies, /aria-pressed=\{activeTab === 'published'\}/);
	// Explore asset-class filter.
	assert.match(explore, /aria-pressed=\{filterClass === c\}/);
	// Generate asset universe picker.
	assert.match(generate, /aria-pressed=\{selectedAssets\.includes\(a\)\}/);
	// None of the three may regress to a click-only span.
	for (const [name, source] of [
		["Strategies", strategies],
		["Explore", explore],
		["Generate", generate],
	]) {
		assert.doesNotMatch(
			source,
			/<span[^>]*className=\{`tag[\s\S]{0,200}?onClick=/,
			`${name} still has a click-only tag span`,
		);
	}
	// The pill reset that lets a <button> render like the <span> it replaced.
	assert.match(css, /button:where\(\.tag\) \{/);
});

test("row-level click targets expose a real control", () => {
	// Library: App.css hides the keyboard-accessible card list above 768px, so
	// the desktop table row was the only disclosure and it had no role, no tab
	// stop and no aria-expanded.
	assert.match(strategies, /className="lib-row-toggle"[\s\S]{0,120}aria-expanded=\{open\}/);
	// aria-controls must be conditional: <tr id={detailId}> only exists in the
	// DOM while open, so an unconditional aria-controls pointed at a
	// nonexistent id on every collapsed row (4.1.2, #1318).
	assert.match(strategies, /aria-controls=\{open \? detailId : undefined\}/);
	assert.doesNotMatch(strategies, /aria-controls=\{detailId\}/);
	assert.match(strategies, /className="lib-row-detail" id=\{detailId\}/);
	// Corpus catalog: clicking the row was the only way into a paper.
	assert.match(
		corpusExplorer,
		/className="catalog-title-btn"[\s\S]{0,140}openPaper\(p\.arxiv_id\)/,
	);
	// Recent Generations: role="button" on a <tr> destroys the cells.
	assert.doesNotMatch(generationStatus, /<tr[\s\S]{0,200}role="button"/);
	assert.match(generationStatus, /<caption className="sr-only">Recent generations<\/caption>/);
	assert.match(generationStatus, /<th scope="col"/);
});

test("shared select and its call site expose an accessible name", () => {
	assert.match(customSelect, /aria-label=\{ariaLabel\}/);
	assert.match(customSelect, /aria-controls=\{open \? listboxId : undefined\}/);
	assert.match(customSelect, /aria-activedescendant=\{/);
	assert.match(corpusExplorer, /ariaLabel="Filter papers by category"/);
});

test("sidebar navigation states where the user currently is", () => {
	assert.match(layout, /<nav aria-label="Main">/);
	assert.match(layout, /aria-current=\{isCurrent \? "page" : undefined\}/);
	// aria-label survives only for the collapsed rail, where .nav-label is
	// display:none — on the expanded rail it silently overrode the visible
	// label (2.5.3).
	assert.match(layout, /aria-label=\{sidebarCollapsed \? item\.label : undefined\}/);
});

// ── 1.1.1 Non-text Content / 1.4.1 Use of Color ───────────────────────────

test("informative icons and charts carry a text alternative", () => {
	// UnoCSS icons are a CSS mask on an empty <span>: no role, no name, and
	// `title` on a bare span is not reliably exposed.
	assert.match(strategies, /role="img" aria-label="Passes rigor gate"/);
	assert.match(strategies, /role="img" aria-label="Does not pass rigor gate"/);
	assert.match(strategies, /role="img" aria-label="Drift detected"/);
	// The drift triangle was hardcoded to base-dark amber: 2.15:1 on a white
	// card in the light theme.
	assert.doesNotMatch(strategies, /#f59e0b/);
	// The knowledge graph is a 500px informative SVG that had no role at all.
	assert.match(
		corpusKg,
		/role="img"\n\s+aria-label=\{`Topic cluster graph: \$\{entities\.length\} entities, \$\{relations\.length\} relations`\}/,
	);
});

test("threshold verdicts do not rely on colour alone", () => {
	// "(43%)" and "(97%)" rendered identically apart from green/red.
	assert.match(strategies, /replicated \? '✓' : '✗'/);
	assert.match(strategies, /below the 50% replication threshold/);
	assert.match(strategies, /above the 0\.50 overfitting threshold/);
	assert.match(css, /^\.sr-only \{/m);
});

// ── 2.4.3 Focus Order / 2.5.8 Target Size / 2.5.7 Dragging ────────────────

test("portalled dialogs move, trap and restore focus", () => {
	for (const [name, source] of [
		["OnboardingTour", onboardingTour],
		["Strategies", strategies],
		["WalletConnect", walletConnect],
	]) {
		assert.match(
			source,
			/import useDialogFocus from '\.\.\/hooks\/useDialogFocus'/,
			`${name} does not use the shared dialog-focus hook`,
		);
	}
	// The two dialogs that had no dialog semantics at all.
	assert.match(
		walletConnect,
		/role="dialog"\n\s+aria-modal="true"\n\s+aria-labelledby="wallet-modal-title"/,
	);
	assert.match(walletConnect, /<h3 id="wallet-modal-title">Connect Wallet<\/h3>/);
	assert.match(
		strategies,
		/role="dialog"\n\s+aria-modal="true"\n\s+aria-labelledby="rigor-explainer-title"/,
	);
	// Escape had no handler on the rigor explainer.
	assert.match(strategies, /useDialogFocus\(rigorModalOpen, \{ onEscape: closeRigorExplainer \}\)/);
});

test("tour pagination dots meet the 24px minimum target", () => {
	// 8px dots with a 6px gap: the 24px targets centred on adjacent dots
	// overlapped, so the spacing exception did not apply either.
	assert.match(onboardingTour, /className="w-6 h-6 flex items-center justify-center border-none cursor-pointer"/);
	// role="tab" promised arrow-key navigation and tabpanels this component
	// never implemented.
	assert.doesNotMatch(onboardingTour, /role="tablist"/);
	assert.doesNotMatch(onboardingTour, /role="tab"/);
});

test("knowledge-graph pan and zoom have single-pointer alternatives", () => {
	// Pan was drag-only and zoom wheel-only; "Reset view" only ever returns to
	// the origin.
	assert.match(corpusKg, /aria-label="Graph view controls"/);
	for (const label of ["Zoom in", "Zoom out", "Pan left", "Pan right", "Pan up", "Pan down"]) {
		assert.match(corpusKg, new RegExp(`aria-label="${label}"`), `missing ${label}`);
	}
});

// ── 3.3.x Labels, Instructions, Error Identification ──────────────────────

test("the generate form's fields are programmatically labelled", () => {
	assert.match(generate, /<label className="label mb-1 block" htmlFor="generate-brief">/);
	assert.match(generate, /id="generate-brief"\s+aria-describedby="generate-brief-help"/);
	assert.match(generate, /htmlFor="generate-strategy-name"/);
	assert.match(generate, /aria-label="Search assets"/);
});

test("the corpus search field has a name that survives typing", () => {
	assert.match(corpusExplorer, /aria-label="Search papers"/);
});

test("sign-up states why the confirm field is invalid and the button disabled", () => {
	assert.match(authPage, /id="password-rules"/);
	assert.match(authPage, /id="confirm-mismatch"/);
	assert.match(
		authPage,
		/aria-describedby=\{showMismatch \? 'password-rules confirm-mismatch' : 'password-rules'\}/,
	);
});

test("unlinking a wallet is confirmed before it happens", () => {
	// Single unconfirmed click deleted the account↔wallet binding with no undo.
	assert.match(
		src("components/AccountSettings.jsx"),
		/window\.confirm\(\n\s+`Unlink \$\{label\}\?/,
	);
});

// ── 4.1.3 Status Messages ─────────────────────────────────────────────────

test("generation outcomes are announced, not just painted", () => {
	// One slot carried start failures, the quote-not-ready message, both
	// payment banners and the settlement receipt, with no role or aria-live.
	assert.match(
		generate,
		/className="generate-submit-status"\s+role="status"\s+aria-live="polite"\s+aria-atomic="true"/,
	);
	// The stream is a minutes-long append-only feed.
	assert.match(
		generationStream,
		/role="log"\n\s+aria-live="polite"\n\s+aria-relevant="additions text"\n\s+aria-label="Generation events"/,
	);
	// The live region must hold ONLY the terminal outcome. `events.length`
	// increments on every SSE frame, so while the running-state counter sat
	// inside the region a screen reader re-read the job id and the whole block
	// once per event for the length of the run.
	assert.match(
		generationStream,
		/role="status"\n\s+aria-live=\{terminal === 'error' \? 'assertive' : 'polite'\}/,
	);
	const liveRegion = generationStream.match(
		/<div\n\s+role="status"\n\s+aria-live=\{terminal[\s\S]*?\n {10}<\/div>/,
	);
	assert.ok(liveRegion, "GenerationStream live region not found");
	assert.doesNotMatch(liveRegion[0], /events\.length/);
});

test("stopping a paper deployment says so", () => {
	// On success the row's chip silently flipped and the Stop button unmounted.
	assert.match(paperTrading, /<div role="status" aria-live="polite"/);
	assert.match(paperTrading, /setNotice\(`Paper trading stopped for \$\{label\}\./);
	assert.match(corpusExplorer, /className="catalog-results-info" role="status"/);
});
