const STORAGE_KEY = "archimedes.theme";

export function getStoredTheme() {
	try {
		const stored = localStorage.getItem(STORAGE_KEY);
		if (stored === "dark" || stored === "light") return stored;
	} catch {
		// Storage can be unavailable in private browsing or embedded contexts.
	}
	return window.matchMedia("(prefers-color-scheme: dark)").matches
		? "dark"
		: "light";
}

export function applyTheme(theme) {
	const next = theme === "dark" ? "dark" : "light";
	// Storage failure must not prevent current-page theme updates.
	document.documentElement.setAttribute("data-theme", next);
	try {
		localStorage.setItem(STORAGE_KEY, next);
	} catch {
		// Non-fatal: theme will not persist across reloads.
	}
}
