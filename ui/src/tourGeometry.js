// Pure geometry predicate for the onboarding tour's spotlight (#1364).
//
// `measure()` in OnboardingTour.jsx only rejected a *zero-sized* anchor
// rect. Below 1024px the sidebar is an off-canvas drawer positioned with
// `transform: translateX(-100%)`, not removed from layout — so a closed
// drawer's nav button reports a full-size rect translated to
// `left ≈ -260`, which is non-zero and was accepted. The spotlight ring
// and dim-panel cut-out then painted entirely off-screen.
//
// Kept as a plain, dependency-free module (no JSX, no DOM) so it is
// importable and unit-testable under bare `node --test` (see
// ui/package.json's `"test": "node --test"` — no jsdom, no
// @testing-library).
export function rectOnScreen(r, vw, vh) {
	const width = r.right - r.left
	const height = r.bottom - r.top
	if (width === 0 || height === 0) return false
	if (r.right <= 0 || r.bottom <= 0 || r.left >= vw || r.top >= vh) return false
	return true
}
