import { lazy, Suspense, useCallback, useEffect, useState } from "react";

import { useAuth } from "./AuthContext";
import { defaultFeatures, fetchFeatures } from "./features";
import { pageToPath, resolveRoute } from "./routes";
import Architecture from "./components/Architecture";
import AuthPage from "./components/AuthPage";
import Landing from "./components/Landing";
import PublicLayout from "./components/PublicLayout";
import Security from "./components/Security";
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
		if (route.kind !== "app" || route.anonymousOk || authLoading || user) return;
		const next = `${window.location.pathname}${window.location.search}`;
		window.location.replace(`/sign-in?next=${encodeURIComponent(next)}`);
	}, [route.kind, route.anonymousOk, authLoading, user]);

	useEffect(() => {
		const titles = {
			landing: "Archimedes",
			explore: "Explore · Archimedes",
			leaderboard: "Leaderboard · Archimedes",
			generate: "Generate · Archimedes",
			architecture: "Architecture · Archimedes",
			security: "Security · Archimedes",
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
			"not-found": "Page not found · Archimedes",
		};
		const key = route.kind === "not-found" ? "not-found" : route.page;
		document.title = titles[key] ?? "Archimedes";
	}, [route.kind, route.page]);

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
		if (route.page === "architecture") {
			content = <Architecture onNavigate={navigateToPage} />;
		}
		return <PublicLayout user={user}>{content}</PublicLayout>;
	}

	if (route.kind === "not-found") {
		return (
			<PublicLayout user={user}>
				<main className="min-h-[70vh] flex flex-col items-center justify-center gap-4 px-4 text-center">
					<h1 className="serif text-[2rem]">Page not found</h1>
					<a className="btn-primary" href={user ? "/app" : "/"}>
						{user ? "Open app" : "Go home"}
					</a>
				</main>
			</PublicLayout>
		);
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
