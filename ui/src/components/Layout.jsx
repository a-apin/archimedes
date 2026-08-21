import { useState, useEffect, useRef } from "react";
import WalletConnect from "./WalletConnect";
import Breadcrumbs from "./Breadcrumbs";
import { getStoredWalletName } from "../config";
import { deriveChainStatus } from "../chainStatus";
import { fetchHealth } from "../health";
import { getStoredTheme, applyTheme } from "../theme";
import { visibleNavigation } from "../routes";
import { lockBodyScroll, unlockBodyScroll } from "../utils/scrollLock";
import { getProofStages } from "../proofStages.js";

// Sidebar groups separate the marketing-site anchor (labelled "Marketing
// site", NOT "Home" — the breadcrumb's Home crumb already owns that label for
// the in-app anchor at Explore; two controls both reading "Home" ~40px apart
// with different destinations was #1370 item 3) from the product-state
// bands. Empty group label is intentional for that entry — it renders as a
// header-less section so it reads as the top-of-shell anchor, not a peer of
// the other groups. The five labelled groups split the remaining surfaces
// along the gating boundary:
//   DISCOVER — open to anonymous visitors (no wallet needed)
//   STRATEGY — wallet-gated: generate + your saved strategies
//   POSITION — wallet-gated: on-chain audit, post-hoc review (Portfolio and
//     Learnings are ROADMAP_PAGES, hidden by default (#1266); Quant Lab
//     defaults off separately via the backend `quant` feature flag — in the
//     shipped build this group renders as a single item, Reasoning)
//   MARKET — the strategy marketplace (ROADMAP_PAGES, hidden by default)
//   OPS — insights + account
// Item order inside DISCOVER (Explore → Corpus) follows the natural
// user-onboarding read: browse the seed strategies first, see the substrate
// they're drawn from second. Architecture is deliberately NOT a shell nav
// item (#1370) — `pageToPath('architecture')` resolves to the public
// `/architecture` route (routes.js PUBLIC_PATHS), so a click here rendered it
// inside PublicLayout instead: no sidebar, no breadcrumbs, sidebar destroyed.
// Reachable from PublicLayout's own nav (public marketing pages only) and by
// direct URL; see the anti-goal in #1370 against giving it a second, /app-side
// route as a fix.
const NAV = [
	{
		group: null,
		items: [
			{ id: "landing", label: "Marketing site", icon: "i-lucide-home" },
		],
	},
	{
		group: "Discover",
		items: [
			{ id: "explore", label: "Explore", icon: "i-lucide-compass" },
			{ id: "corpus", label: "Corpus", icon: "i-lucide-library" },
		],
	},
	{
		group: "Strategy",
		items: [
			{ id: "generate", label: "Generate", icon: "i-lucide-sparkles" },
			{ id: "library", label: "Library", icon: "i-lucide-line-chart" },
			// Paper Trading lives in STRATEGY: it is the act-on step of the MVP
			// spine (generate → verdict → paper) — simulated, account-owned, free.
			{ id: "paper", label: "Paper Trading", icon: "i-lucide-trending-up" },
			// Leaderboard lives in STRATEGY (#1077): it ranks the strategy library —
			// discovery-friendly but strategy-native. (Quant Lab moved to Position.)
			{ id: "leaderboard", label: "Leaderboard", icon: "i-lucide-trophy" },
		],
	},
	{
		group: "Position",
		items: [
			{
				id: "portfolio",
				label: "Portfolio",
				icon: "i-lucide-layout-dashboard",
			},
			// Re-added (#1060 AC#3, Dan's call 2026-07-14): the livestream-era hiding
			// (#1061) was for the synthetic-sample-data version; this PR wires the
			// panels to live library/vault/trace data with per-section disclaimers.
			// Lives in POSITION (Dan, 2026-07-14): its panels read the user's live
			// vault/trace data, so it belongs with the deployed-state surfaces and is
			// wallet-gated like them (see App.jsx).
			{ id: "quant", label: "Quant Lab", icon: "i-lucide-flask-conical" },
			{ id: "reasoning", label: "Reasoning", icon: "i-lucide-brain" },
			{ id: "learnings", label: "Learnings", icon: "i-lucide-graduation-cap" },
		],
	},
	{
		group: "Market",
		items: [
			{
				id: "marketplace",
				label: "Marketplace",
				icon: "i-lucide-shopping-bag",
			},
			{ id: "publish", label: "Publish", icon: "i-lucide-megaphone" },
			{ id: "subscriptions", label: "Subscriptions", icon: "i-lucide-bell" },
		],
	},
	{
		group: "Ops",
		items: [
			{ id: "insights", label: "Insights", icon: "i-lucide-bar-chart-3" },
			{ id: "account", label: "Account", icon: "i-lucide-user-round-cog" },
		],
	},
];

export const PAGE_LABELS = {
	landing: "Marketing site",
	explore: "Explore",
	leaderboard: "Leaderboard",
	generate: "Generate",
	architecture: "Architecture",
	library: "Library",
	strategy: "Strategy Passport",
	paper: "Paper Trading",
	corpus: "Corpus",
	quant: "Quant Lab",
	portfolio: "Portfolio",
	reasoning: "Reasoning",
	learnings: "Learnings",
	insights: "Insights",
	"vault-detail": "Vault Details",
	marketplace: "Marketplace",
	"market-strategy": "Strategy Detail",
	publish: "Publish",
	subscriptions: "Subscriptions",
	account: "Account",
};

// PROOF_STAGES logic lives in ../proofStages.js (#1354) — plain .js so the
// hermetic node test can import and call getProofStages() directly; see
// that file for the rail's 3-vs-5-stage rationale.
const PROOF_STAGES = getProofStages();

const CORE_PAGE_STAGE = {
	generate: "brief",
	strategy: "gate",
	"vault-detail": "vault",
	portfolio: "monitor",
};

export default function Layout({
	page,
	setPage,
	walletAddr,
	onConnect,
	onDisconnect,
	onOpenTour,
	user,
	features,
	journeyStage,
	children,
}) {
	const [menuOpen, setMenuOpen] = useState(false);
	const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
	const [theme, setTheme] = useState(getStoredTheme);
	const [health, setHealth] = useState(null);
	const [healthError, setHealthError] = useState(false);
	const hamburgerRef = useRef(null);
	const chainStatus = deriveChainStatus(health, healthError);
	const proofStage =
		(page === "generate" ? journeyStage : null) ?? CORE_PAGE_STAGE[page];

	// Chain-status pill (#1321): re-derives from /health on mount and on every
	// in-app navigation (dep: `page`) — not a polling loop, a bounded
	// re-derivation. Layout sits at a stable position in the tree
	// (AuthenticatedApp.jsx renders it with no `key`, so React reconciles
	// rather than remounts across route changes), so an empty dep array here
	// would mean an outage that begins mid-session stays invisible until the
	// tab is reloaded — the same fail-soft failure #1321 was filed for, just
	// time-bounded instead of permanent. The "unknown" tone (deriveChainStatus,
	// ../chainStatus.js) covers both the pre-resolution window and a failed
	// fetch, so a backend outage in progress at mount OR surfaced by the next
	// navigation never silently renders as "Arc · Testnet live". A tab left
	// idle on a single page for the whole session still won't repaint until
	// the user navigates — that gap is accepted, not solved, by design: it
	// stays bounded by the next click rather than growing unbounded, without
	// adding the polling loop this issue explicitly ruled out.
	//
	// The actual network call goes through fetchHealth() (../health.js), a
	// short-TTL-cached wrapper, not a direct call to the raw fetch helper
	// here: Layout isn't the only /health caller (Architecture.jsx,
	// ModelCostPanel.jsx), so re-fetching straight from this effect on every
	// navigation would fire a fresh Arc RPC round-trip + DB reads even on a
	// nav that lands on a page with its own /health read. fetchHealth() lets
	// those callers share one response instead (#1333 review).
	useEffect(() => {
		let cancelled = false;
		fetchHealth()
			.then((d) => {
				if (!cancelled) {
					setHealth(d);
					// Clear a prior failure so a fetch on a later navigation can
					// report recovery — without this, one failed /health call
					// pins the pill on "unknown" for the rest of the session even
					// after the backend comes back, defeating the re-fetch this
					// effect now does on every `page` change.
					setHealthError(false);
				}
			})
			.catch(() => {
				if (!cancelled) setHealthError(true);
			});
		return () => {
			cancelled = true;
		};
	}, [page]);

	// Lock body scroll while the mobile nav drawer is open — otherwise the
	// page content underneath can still scroll behind the fixed overlay/drawer,
	// which reads as janky rather than a clean modal-style drawer.
	//
	// Uses the shared ref-counted lock (utils/scrollLock.js) rather than
	// saving/restoring document.body.style.overflow directly: AssetModal.jsx
	// can be open at the same time as this drawer, and a naive save/restore
	// in either place can wrongly re-enable scroll while the other is still
	// open (whichever closes first "restores" a stale value). The counter
	// only clears overflow once every locker has released it. AssetModal.jsx
	// isn't touched here — it keeps its own independent lock for now — but
	// this same helper is available for it to adopt.
	useEffect(() => {
		if (!menuOpen) return;
		lockBodyScroll();
		return () => unlockBodyScroll();
	}, [menuOpen]);

	const closeMenu = () => {
		setMenuOpen(false);
		// Return focus to the hamburger button on close for keyboard/screen-reader
		// parity — otherwise focus is dropped when the drawer unmounts/hides.
		hamburgerRef.current?.focus();
	};

	const toggleTheme = () => {
		const next = theme === "light" ? "dark" : "light";
		applyTheme(next);
		setTheme(next);
	};

	// Circle wallet names describe wallet, never application identity.
	const displayName = walletAddr ? getStoredWalletName(walletAddr) : null;

	const handleNav = (id) => {
		setPage(id);
		setMenuOpen(false);
	};

	return (
		<div
			className={`shell app-site${sidebarCollapsed ? " shell-sidebar-collapsed" : ""}`}
		>
			{/* Bypass Blocks (2.4.1). The public shell has always shipped one; the
			    authenticated shell made every /app page start with the same ~18
			    stops (sidebar close, up to 12 nav buttons, collapse, hamburger,
			    theme, tour, account chip, wallet) before any content. */}
			<a className="app-skip-link" href="#app-content">
				Skip to content
			</a>

			{/* Mobile overlay — uses UnoCSS `fixed inset-0` + App.css `.sidebar-overlay` */}
			{menuOpen && (
				<div
					className="fixed inset-0 sidebar-overlay"
					onClick={closeMenu}
					aria-hidden="true"
				/>
			)}

			<aside
				className={`sidebar${menuOpen ? " sidebar-open" : ""}${sidebarCollapsed ? " sidebar-collapsed" : ""}`}
			>
				<div className="sidebar-brand">
					<div className="sidebar-brand-main">
						<div className="logo-mark">
							<svg viewBox="0 0 36 36" aria-hidden="true">
								<path d="M18 18c-1.8 1.4-4.5.1-4.1-2.3.5-3.2 5.3-4.5 7.8-2.1 3.8 3.8.2 10.4-6 11.5-8.1 1.4-14.4-7.6-11.1-15.4 4-9.5 17-12.5 25.5-6" />
							</svg>
						</div>
						<div className="logo-copy flex-1 min-w-0">
							<div className="logo-text">Archimedes</div>
							<div className="logo-sub">Evidence workspace</div>
						</div>
						<button
							className="sidebar-close-btn"
							onClick={closeMenu}
							aria-label="Close menu"
						>
							<span className="i-lucide-x" style={{ width: 16, height: 16 }} />
						</button>
					</div>
				</div>

				<nav aria-label="Main">
					{NAV.map((group) => ({
						...group,
						// Anonymous visitors see only the browsable pages plus
						// Generate (#1194 revision d) — visibleNavigation owns
						// the id list. Groups left empty by the filter are
						// skipped entirely below so a logged-out sidebar never
						// shows a bare "Position"/"Market" header over nothing.
						items: visibleNavigation(group.items, features, user),
					}))
						.filter((group) => group.items.length > 0)
						.map((group, gi) => (
						<div key={group.group || gi} className="nav-group">
							{group.group && (
								<div className="nav-group-label">{group.group}</div>
							)}
							{group.items.map((item) => {
								const isCurrent =
									page === item.id ||
									(item.id === "portfolio" && page === "vault-detail");
								return (
								<button
									key={item.id}
									type="button"
									data-tour={item.id}
									className={`nav-link${isCurrent ? " active" : ""}`}
									onClick={() => handleNav(item.id)}
									// This is a client-routed SPA, so the `.active` class was the
									// ONLY "you are here" signal — every item read identically to
									// a screen reader. aria-label is kept only for the collapsed
									// rail, where .nav-label is display:none (App.css:501) and the
									// button would otherwise have no accessible name; leaving it
									// on the expanded state silently overrides the visible label
									// if the two ever drift (2.5.3).
									aria-current={isCurrent ? "page" : undefined}
									aria-label={sidebarCollapsed ? item.label : undefined}
									title={sidebarCollapsed ? item.label : undefined}
								>
									<span
										className={`nav-icon ${item.icon}`}
										aria-hidden="true"
									/>
									<span className="nav-label">{item.label}</span>
								</button>
								);
							})}
						</div>
					))}
				</nav>

				<div className="sidebar-footer">
					<span
						className={`live-dot live-dot-${chainStatus.tone}`}
						aria-hidden="true"
					/>
					<span className="sidebar-footer-label">{chainStatus.label}</span>
					<button
						type="button"
						className="sidebar-collapse-btn"
						onClick={() => setSidebarCollapsed((v) => !v)}
						aria-label={
							sidebarCollapsed ? "Expand sidebar" : "Collapse sidebar"
						}
						aria-expanded={!sidebarCollapsed}
						title={sidebarCollapsed ? "Expand sidebar" : "Collapse sidebar"}
					>
						<span
							className={
								sidebarCollapsed
									? "i-lucide-panel-left-open"
									: "i-lucide-panel-left-close"
							}
							style={{ width: 18, height: 18 }}
						/>
					</button>
				</div>
			</aside>

			<div className="main-area">
				<div className="topbar">
					{/* Left: hamburger (mobile) + breadcrumbs */}
					<div className="flex items-center gap-3">
						<button
							ref={hamburgerRef}
							className={`hamburger-btn${menuOpen ? " open" : ""}`}
							onClick={() => setMenuOpen((v) => !v)}
							aria-label="Toggle navigation"
							aria-expanded={menuOpen}
						>
							<span className="hamburger-line" />
							<span className="hamburger-line" />
							<span className="hamburger-line" />
						</button>
						<Breadcrumbs page={page} setPage={setPage} />
					</div>
					<div className="flex items-center gap-2">
						{/* Personalized greeting moved into the WalletConnect dropdown
                header so the topbar stays compact + the greeting lives next
                to the wallet identity it belongs to. */}
						<button
							type="button"
							className="topbar-icon-btn"
							onClick={toggleTheme}
							aria-label={
								theme === "light"
									? "Switch to dark theme"
									: "Switch to light theme"
							}
							title={
								theme === "light"
									? "Switch to dark theme"
									: "Switch to light theme"
							}
						>
							<span
								className={theme === "light" ? "i-lucide-moon" : "i-lucide-sun"}
								style={{ width: 18, height: 18 }}
							/>
						</button>
						{onOpenTour && (
							<button
								type="button"
								className="topbar-icon-btn"
								onClick={onOpenTour}
								aria-label="Open onboarding tour"
								title="What is Archimedes? — open the tour"
							>
								<span
									className="i-lucide-help-circle"
									style={{ width: 18, height: 18 }}
								/>
							</button>
						)}
						{user ? (
							<>
								<button
									type="button"
									className="wallet-chip account-chip"
									onClick={() => handleNav("account")}
									title={user?.email}
								>
									<span
										className="i-lucide-user-round"
										style={{ width: 14, height: 14 }}
									/>
									<span>{user?.name || "Account"}</span>
								</button>
								<WalletConnect
									address={walletAddr}
									displayName={displayName}
									onConnect={onConnect}
									onDisconnect={onDisconnect}
									onEditProfile={() => handleNav("account")}
								/>
							</>
						) : (
							// Anonymous browse (#1194 revision d): the account
							// chip would navigate into an auth-gated page and
							// the wallet widget links wallets to ACCOUNTS, so
							// with no session both collapse into the one honest
							// affordance — sign in. Deep-link preserved via
							// ?next so the visitor returns to the page they
							// were reading.
							<a
								className="btn-primary"
								href={`/sign-in?next=${encodeURIComponent(
									`${window.location.pathname}${window.location.search}`,
								)}`}
							>
								Sign in
							</a>
						)}
					</div>
				</div>
				{/* tabIndex={-1} so the skip link above actually lands focus here
				    rather than only moving the scroll position. */}
				<main
					id="app-content"
					tabIndex={-1}
					className={`page-content page-${page}`}
				>
					{proofStage && (
						<ol className="app-proof-rail" aria-label="Core strategy journey">
							{PROOF_STAGES.map((stage) => {
								const isCurrent = stage.id === proofStage;
								return (
									<li
										key={stage.id}
										className={isCurrent ? "is-current" : undefined}
										aria-current={isCurrent ? "step" : undefined}
									>
										{stage.label}
									</li>
								);
							})}
						</ol>
					)}
					{children}
				</main>
			</div>
		</div>
	);
}
