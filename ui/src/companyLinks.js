// Company and social links rendered in both shells' footers — the public
// site (PublicLayout.jsx, wraps Landing + Architecture) and the
// authenticated app (Layout.jsx). Both consume this through
// socialLinks.js's activeSocialLinks(), which is the one place that reads
// these values; nothing else in the UI should import this object directly.
//
// THE RULE (claims-must-be-true, see CLAUDE.md's "hard constraint, above
// everything else"): an empty string means the corresponding link renders
// NOWHERE. A footer icon that opens a 404, an unissued Discord invite, or a
// handle nobody has claimed yet is a false claim about what exists — exactly
// the class of thing that rule exists to forbid. Do not "temporarily" point
// a key at a placeholder URL to make the icon appear before the destination
// is real.
//
// Drop-in instructions for the owner — fill each in only once it is real:
//   - discord: the invite URL once one is issued, e.g. "https://discord.gg/xxxxxxxx".
//   - x:       the profile URL once the handle exists, e.g. "https://x.com/<handle>".
//   - company: "https://aprin.ai" once APRIN Labs' site is actually live.
//              The domain is being registered as of 2026-08-20 — the eventual
//              value is already known, but do NOT fill it in before the site
//              resolves; a live-looking link to nothing is the exact failure
//              mode this file's rule exists to prevent.
export const companyLinks = {
	// Real today — the public repo this UI ships from.
	github: "https://github.com/a-apin/archimedes",
	discord: "",
	x: "",
	company: "",
};
