// Extensioned import: this module is real-imported directly by
// test/company-links.test.js under plain Node ESM (no bundler), which
// requires an explicit specifier — unlike Vite, it won't resolve a bare
// "./companyLinks".
import { companyLinks } from "./companyLinks.js";

// Icon + accessible label per possible link. `icon` is a UnoCSS class drawn
// from a collection already registered in uno.config.js's presetIcons
// (simple-icons for brand marks, lucide for the generic company/globe mark);
// `label` becomes the aria-label / title on the icon-only anchor
// CompanyLinksFooter.jsx renders for it.
export const SOCIAL_LINK_META = {
	github: { icon: "i-simple-icons-github", label: "Archimedes on GitHub" },
	discord: {
		icon: "i-simple-icons-discord",
		label: "Join the Archimedes Discord",
	},
	x: { icon: "i-simple-icons-x", label: "Archimedes on X" },
	company: { icon: "i-lucide-globe", label: "APRIN Labs" },
};

// The one place that enforces companyLinks.js's rule — empty string means
// "does not render." CompanyLinksFooter.jsx calls this instead of
// re-implementing the check inline so the public and app shells can never
// drift apart on which links are live.
export function activeSocialLinks(links = companyLinks) {
	return Object.keys(SOCIAL_LINK_META)
		.filter((key) => Boolean(links[key]))
		.map((key) => ({ key, url: links[key], ...SOCIAL_LINK_META[key] }));
}
