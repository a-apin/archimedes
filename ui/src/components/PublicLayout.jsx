export default function PublicLayout({ user, children }) {
	return (
		<div className="public-site">
			<a className="public-skip-link" href="#public-content">
				Skip to content
			</a>
			<header className="public-header">
				<div className="public-header__inner">
					<a href="/" className="public-brand" aria-label="Archimedes home">
						<svg
							className="public-brand__mark"
							viewBox="0 0 36 36"
							aria-hidden="true"
						>
							<path d="M18 18c-1.8 1.4-4.5.1-4.1-2.3.5-3.2 5.3-4.5 7.8-2.1 3.8 3.8.2 10.4-6 11.5-8.1 1.4-14.4-7.6-11.1-15.4 4-9.5 17-12.5 25.5-6" />
						</svg>
						<span className="public-brand__copy">
							<strong>Archimedes</strong>
							<small>research · rigor · custody</small>
						</span>
					</a>
					<nav className="public-nav" aria-label="Public navigation">
						<a href="/architecture" className="public-nav__link">
							Architecture
						</a>
						<a className="public-auth-link" href={user ? "/app" : "/sign-in"}>
							{user ? "Open app" : "Sign in"} <span aria-hidden="true">→</span>
						</a>
					</nav>
				</div>
			</header>
			<div id="public-content" tabIndex="-1">
				{children}
			</div>
			{/* Shell-level footer, moved here from Landing.jsx: it used to live on
			    one page, so Architecture (and not-found) carried no footer at all
			    and had nowhere to hang the policy links. Owning it at the shell
			    means every public page — Landing, Architecture, Privacy, Terms,
			    not-found — links to the policies, which is the point: Google's
			    OAuth consent review looks for a discoverable privacy link, not a
			    URL you only reach by typing it. */}
			<footer className="public-footer">
				<div className="public-shell">
					<span>Archimedes</span>
					<span>
						Research-grounded strategy generation · Arc public testnet
					</span>
					<nav className="policy-links" aria-label="Policies">
						<a href="/privacy">Privacy</a>
						<a href="/terms">Terms</a>
					</nav>
				</div>
			</footer>
		</div>
	);
}
