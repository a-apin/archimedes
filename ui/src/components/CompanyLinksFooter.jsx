import { activeSocialLinks } from "../socialLinks";

// Shared footer link row for both shells (PublicLayout.jsx, Layout.jsx).
// activeSocialLinks() (../socialLinks.js) already filtered out every empty
// entry, so this component never has to re-check companyLinks.js itself —
// it renders exactly what it's handed, or nothing at all when nothing is
// configured yet (github is always non-empty today, so in practice this
// never returns null, but the guard stays honest for a config-only state).
export default function CompanyLinksFooter({ className = "footer-links" }) {
	const links = activeSocialLinks();
	if (links.length === 0) return null;
	return (
		<div className={className} aria-label="Archimedes elsewhere">
			{links.map(({ key, url, icon, label }) => (
				<a
					key={key}
					href={url}
					target="_blank"
					rel="noopener noreferrer"
					className="footer-icon-link"
					aria-label={label}
					title={label}
				>
					<span className={icon} aria-hidden="true" />
				</a>
			))}
		</div>
	);
}
