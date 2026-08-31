import { lazy, Suspense, useCallback, useEffect, useState } from "react";

import { fetchAdminProbe } from "./adminProbe.js";
import { isInsightsPageBlocked, resolveInsightsAdminState } from "./insightsGate.js";
import { useAuth } from "./AuthContext";
import { defaultFeatures, fetchFeatures } from "./features";
import { pageToPath, resolveRoute } from "./routes";
import Architecture from "./components/Architecture";
import AuthPage from "./components/AuthPage";
import Landing from "./components/Landing";
import NotFound from "./components/NotFound";
import PublicLayout from "./components/PublicLayout";
import Privacy from "./components/Privacy";
import Security from "./components/Security";
import Terms from "./components/Terms";
import "./App.css";

const API_BASE = import.meta.env.VITE_API_BASE ?? "";
const AuthenticatedApp = lazy(() => import("./AuthenticatedApp"));

function currentRoute(features) {
	return resolveRoute(
		window.location.pathname,
		window.location.search,
		features,
	);
}

export default function App() {
	const { user, loading: authLoading } = useAuth();
	const [features, setFeatures] = useState(defaultFeatures);
	const [route, setRoute] = useState(() => currentRoute(defaultFeatures));
	// Admin-gate probe result for /app/insights (owner directive 2026-08-20,
	// supersedes #1028 D8): null while checking, true/false once the server
	// has answered. Server truth only — never derived from `user`/wallet
	// local state, which cannot know PLATFORM_ADMIN_WALLETS membership.
	const [insightsAdmin, setInsightsAdmin] = useState(null);
	// Bumped on every 'wallet-changed' event (config.js — fired on a raw
	// account swap in the injected/EIP-6963 wallet, independent of sign-in
	// state) so the insights-admin-probe effect below re-runs even while
	// already sitting on /app/insights. A [route.page]-only dependency left
	// a stale `insightsAdmin === true` (and the live dashboard it gates)
	// rendering after switching from an admin-linked wallet to a
	// non-admin one on the same account, because route.page never changes on
	// an in-place wallet swap (round-2 finding: the cache-reset in
	// AuthenticatedApp's wallet-changed handler only helps a FUTURE probe
	// caller — this effect is the one that has to actually become that
	// caller). App.jsx has no other reason to track the connected wallet
	// (that's AuthenticatedApp's walletAddr, the LINKED-and-verified
	// subset), so a plain counter is used rather than duplicating that state.
	const [walletChangeSeq, setWalletChangeSeq] = useState(0);

	useEffect(() => {
		const onWalletChanged = () => setWalletChangeSeq((n) => n + 1);
		window.addEventListener("wallet-changed", onWalletChanged);
		return () => window.removeEventListener("wallet-changed", onWalletChanged);
	}, []);

	useEffect(() => {
		fetchFeatures()
			.then((next) => {
				setFeatures(next);
				setRoute(currentRoute(next));
			})
			.catch(() => {});
	}, []);

	useEffect(() => {
		if (route.kind !== "redirect") return;
		window.history.replaceState({}, "", route.redirect);
		setRoute(currentRoute(features));
	}, [route, features]);

	useEffect(() => {
		const onPopState = () => setRoute(currentRoute(features));
		window.addEventListener("popstate", onPopState);
		return () => window.removeEventListener("popstate", onPopState);
	}, [features]);

	useEffect(() => {
		try {
			if (sessionStorage.getItem("archimedes_landed")) return;
			sessionStorage.setItem("archimedes_landed", "1");
		} catch {
			// Storage may be blocked; metric stays best-effort.
		}
		fetch(`${API_BASE}/api/metrics/funnel/event`, {
			method: "POST",
			credentials: "include",
			headers: { "Content-Type": "application/json" },
			body: JSON.stringify({ stage: "landed" }),
		}).catch(() => {});
	}, []);

	useEffect(() => {
		// Anonymous-OK app pages never bounce to sign-in; auth is required only
		// to generate, pay, or paper-deploy. Keep aligned with nginx carve-outs.
		//
		// `insights` is EXCLUDED here too (owner directive 2026-08-20): a
		// client-side navigation onto /app/insights must land on the identical
		// not-found treatment a truly unknown path gets, never a sign-in
		// prompt — bouncing to /sign-in?next=/app/insights would itself
		// advertise that the page exists and is worth signing in for. (nginx
		// gates the FIRST-LOAD request for that path exactly like every other
		// /app path, so a direct anonymous GET is indistinguishable from a GET
		// for any unknown /app URL; this branch covers the in-app navigation
		// case, which never reaches nginx at all.)
		if (route.kind !== "app" || route.anonymousOk || route.page === "insights" || authLoading || user)
			return;
		const next = `${window.location.pathname}${window.location.search}`;
		window.location.replace(`/sign-in?next=${encodeURIComponent(next)}`);
	}, [route.kind, route.anonymousOk, route.page, authLoading, user]);

	useEffect(() => {
		// Re-probe every time navigation LANDS on insights (not on every
		// render while already there) — reset to "checking" first so a stale
		// true/false from a previous visit this session can't flash before the
		// fresh answer arrives. Also re-probes on a wallet swap that happens
		// WHILE already sitting on insights (walletChangeSeq dep, see above) —
		// route.page alone would miss that transition entirely.
		if (route.page !== "insights") {
			setInsightsAdmin(null);
			return;
		}
		let cancelled = false;
		setInsightsAdmin(null);
		fetchAdminProbe().then((result) => {
			if (!cancelled) setInsightsAdmin(resolveInsightsAdminState(result));
		});
		return () => {
			cancelled = true;
		};
	}, [route.page, walletChangeSeq]);

	useEffect(() => {
		const titles = {
			landing: "Archimedes",
			explore: "Explore · Archimedes",
			leaderboard: "Leaderboard · Archimedes",
			generate: "Generate · Archimedes",
			architecture: "Architecture · Archimedes",
			security: "Security · Archimedes",
			privacy: "Privacy Policy · Archimedes",
			terms: "Terms of Service · Archimedes",
			library: "Library · Archimedes",
			corpus: "Corpus · Archimedes",
			quant: "Quant Lab · Archimedes",
			portfolio: "Portfolio · Archimedes",
			reasoning: "Reasoning · Archimedes",
			learnings: "Learnings · Archimedes",
			insights: "Insights · Archimedes",
			account: "Account · Archimedes",
			"vault-detail": "Vault · Archimedes",
			strategy: "Strategy · Archimedes",
			paper: "Paper Trading · Archimedes",
			"sign-in": "Sign in · Archimedes",
			"sign-up": "Create account · Archimedes",
			"reset-password": "Reset password · Archimedes",
			// resolveRoute() returns page === null for not-found, so this branch used
			// to fall through to the bare 'Archimedes' title — byte-identical to the
			// landing page, leaving a screen-reader or many-tabs user unable to tell
			// a failed deep link from home (2.4.2 Page Titled).
			"not-found": "Page not found · Archimedes",
		};
		// A denied OR still-resolving insights probe titles the tab identically
		// to a real 404 — "do not advertise existence" applies to the tab title
		// too, not just the rendered page (a many-tabs user should not be able
		// to tell "unknown route" from "gated route I'm not allowed on" — or
		// from "gate still resolving" — apart). isInsightsPageBlocked treats
		// `null` (unresolved) the same as `false` (denied) here (round 3 fix):
		// titling the tab "Insights · Archimedes" while the probe is still in
		// flight was itself a disclosure a genuinely unknown route never makes.
		const deniedInsights = isInsightsPageBlocked(route.page, insightsAdmin);
		const key = route.kind === "not-found" || deniedInsights ? "not-found" : route.page;
		document.title = titles[key] ?? "Archimedes";
	}, [route.kind, route.page, insightsAdmin]);

	useEffect(() => {
		const currentCanonical = document.querySelector('link[rel="canonical"]');
		if (route.kind !== "public") {
			currentCanonical?.remove();
			return;
		}

		const canonical = currentCanonical ?? document.createElement("link");
		canonical.rel = "canonical";
		const canonicalPaths = {
			architecture: "/architecture",
			security: "/security",
			privacy: "/privacy",
			terms: "/terms",
		};
		canonical.href = new URL(
			canonicalPaths[route.page] ?? "/",
			window.location.origin,
		).href;
		if (!currentCanonical) document.head.append(canonical);
	}, [route.kind, route.page]);

	const navigateToPage = useCallback(
		(page, options = {}) => {
			const path = pageToPath(page, options);
			if (`${window.location.pathname}${window.location.search}` !== path) {
				window.history[options.replace ? "replaceState" : "pushState"](
					{},
					"",
					path,
				);
			}
			setRoute(
				resolveRoute(
					window.location.pathname,
					window.location.search,
					features,
				),
			);
		},
		[features],
	);

	if (route.kind === "redirect") return null;
	if (route.kind === "auth") {
		return <AuthPage mode={route.page} oauthError={route.error} />;
	}

	if (route.kind === "public") {
		let content = <Landing onNavigate={navigateToPage} />;
		if (route.page === "security") content = <Security />;
		if (route.page === "privacy") content = <Privacy />;
		if (route.page === "terms") content = <Terms />;
		if (route.page === "architecture") {
			content = <Architecture onNavigate={navigateToPage} />;
		}
		return <PublicLayout user={user}>{content}</PublicLayout>;
	}

	// The not-found body lives in ./components/NotFound so the admin gate
	// below renders the byte-identical treatment (see that file). The rebrand
	// restyled this branch's markup in place; the markup moved wholesale into
	// NotFound.jsx rather than being duplicated back here — two copies is
	// exactly the drift this extraction exists to prevent.
	if (route.kind === "not-found") {
		return <NotFound user={user} />;
	}

	if (route.kind === "app" && route.page === "insights") {
		// Server-truth admin gate (owner directive 2026-08-20, supersedes
		// #1028 D8): a denied OR still-resolving probe renders EXACTLY the
		// not-found page — same component the true 404 above uses — never a
		// "you need admin access" message (would itself confirm the page
		// exists) and never an intermediate "Loading…" screen either (round 3
		// fix: a genuinely unknown route never shows a loading state on first
		// paint, so a chrome-free loader while the probe resolves was itself a
		// vector for telling "gated route" apart from "unknown route" — the
		// exact thing this gate exists to prevent). isInsightsPageBlocked
		// treats `null` (unresolved) the same as `false` (denied): both render
		// NotFound immediately.
		if (isInsightsPageBlocked(route.page, insightsAdmin)) return <NotFound user={user} />;
		// admin === true falls through to the normal authenticated render below.
	}

	// Anonymous-OK pages render immediately with user === null rather than
	// blocking on auth resolution. Signed-in chrome upgrades when auth resolves.
	if (!route.anonymousOk && (authLoading || !user)) {
		return (
			<main className="min-h-screen grid place-items-center">
				Loading account…
			</main>
		);
	}

	return (
		<Suspense
			fallback={
				<main className="min-h-screen grid place-items-center">
					Loading application…
				</main>
			}
		>
			<AuthenticatedApp
				route={route}
				features={features}
				navigateToPage={navigateToPage}
				user={user}
			/>
		</Suspense>
	);
}
