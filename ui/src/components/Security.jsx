export default function Security() {
	return (
		<main className="security-page">
			<section
				className="security-hero"
				aria-labelledby="security-title"
			>
				<div className="public-shell security-hero__layout">
					<div>
						<p className="public-overline">Public security posture</p>
						<h1 id="security-title">
							Security is enforced boundaries, not a guarantee.
						</h1>
						<p className="security-hero__lede">
							Archimedes separates account identity, wallet proof, agent
							authority, and vault ownership. These controls describe current
							code—not a promise that failure is impossible.
						</p>
					</div>
					<ul className="security-status" aria-label="Current product status">
						<li>
							<span>Environment</span>
							<strong>Arc public testnet</strong>
						</li>
						<li>
							<span>Product status</span>
							<strong>Research prototype</strong>
						</li>
						<li>
							<span>Value at risk</span>
							<strong>No real funds</strong>
						</li>
					</ul>
				</div>
			</section>

			<section
				className="public-section security-authority"
				aria-labelledby="authority-model-title"
			>
				<div className="public-shell">
					<div className="security-section-heading">
						<p className="public-overline">Authority model</p>
						<h2 id="authority-model-title">Four boundaries. No role inflation.</h2>
						<p>
							Signing in, linking a wallet, running an agent, and owning vault
							shares are separate capabilities.
						</p>
					</div>
					<dl className="security-role-ledger">
						<div>
							<dt>
								<span>01</span>
								Better Auth account
							</dt>
							<dd>
								Canonical application identity. A connected wallet never creates
								or replaces the account session.
							</dd>
						</div>
						<div>
							<dt>
								<span>02</span>
								Proof-linked wallet
							</dt>
							<dd>
								A five-minute, single-use EIP-4361 challenge proves control before
								a wallet is linked. Wallet state is not an app credential.
							</dd>
						</div>
						<div>
							<dt>
								<span>03</span>
								Bounded agent
							</dt>
							<dd>
								The agent may set targets and rebalance within vault checks. Its
								role cannot withdraw, redeem, transfer ownership, or install an
								arbitrary oracle.
							</dd>
						</div>
						<div>
							<dt>
								<span>04</span>
								Vault share owner
							</dt>
							<dd>
								The share owner withdraws or redeems directly, or explicitly
								approves a spender under ERC-4626 allowance rules.
							</dd>
						</div>
					</dl>
				</div>
			</section>

			<section
				className="public-section security-controls"
				aria-labelledby="controls-title"
			>
				<div className="public-shell security-controls__layout">
					<div className="security-section-heading">
						<p className="public-overline">Verified controls</p>
						<h2 id="controls-title">Where enforcement happens.</h2>
						<p>
							Each control maps to an application, edge, database, or contract
							boundary in the current repository.
						</p>
					</div>
					<ol className="security-controls__list">
						<li>
							<span>Session</span>
							<div>
								<h3>Account access is checked at three layers.</h3>
								<p>
									Production cookies are HttpOnly and Secure. nginx, the UI route
									guard, and FastAPI independently protect private surfaces.
								</p>
							</div>
						</li>
						<li>
							<span>Scope</span>
							<div>
								<h3>Private records follow the canonical user ID.</h3>
								<p>
									Profile, strategy, job, and linked-wallet reads resolve through
									the authenticated Better Auth user—not a client-supplied address.
								</p>
							</div>
						</li>
						<li>
							<span>Integrity</span>
							<div>
								<h3>Agent-only writes require a service credential.</h3>
								<p>
									User sessions cannot forge internal reasoning traces, rebalance
									events, or other integrity-critical agent records.
								</p>
							</div>
						</li>
						<li>
							<span>Edge</span>
							<div>
								<h3>Browser and API ingress is constrained.</h3>
								<p>
									Same-origin rules, a hash-restricted script policy, HSTS,
									anti-framing headers, limited browser permissions, and separate
									read/write rate limits reduce common web attack paths.
								</p>
							</div>
						</li>
						<li>
							<span>Contract</span>
							<div>
								<h3>Trades must stay inside contract rules.</h3>
								<p>
									Rebalances require an earlier reasoning-trace commitment, bounded
									target movement, slippage checks, and owner-curated oracle paths.
								</p>
							</div>
						</li>
					</ol>
				</div>
			</section>

			<section
				id="known-limits"
				className="public-section security-limits"
				aria-labelledby="known-limits-title"
			>
				<div className="public-shell security-limits__layout">
					<div>
						<p className="public-overline">Known limits</p>
						<h2 id="known-limits-title">Controls reduce risk. They do not erase it.</h2>
					</div>
					<ul>
						<li>
							<strong>Testnet:</strong> Arc public testnet only. No real funds
							should be used.
						</li>
						<li>
							<strong>Agent risk:</strong> Agent may mis-rebalance within its
							constraints, but its role cannot withdraw user assets.
						</li>
						<li>
							<strong>Demo inputs:</strong> Some current oracle and risk inputs
							are mock data and must not support live financial decisions.
						</li>
						<li>
							<strong>Immutable contracts:</strong> A defect requires a new
							deployment and migration rather than an in-place upgrade.
						</li>
						<li>
							<strong>No assurance:</strong> No independent security audit,
							production-readiness, regulatory, or return guarantee is claimed
							by this page.
						</li>
					</ul>
				</div>
			</section>

			<section
				className="public-section security-evidence"
				aria-labelledby="evidence-title"
			>
				<div className="public-shell security-evidence__layout">
					<div className="security-section-heading">
						<p className="public-overline">Inspect the evidence</p>
						<h2 id="evidence-title">Read controls at source.</h2>
						<p>
							Security posture should be reviewable, not accepted from copy.
						</p>
					</div>
					<ul>
						<li>
							<a href="/architecture">System architecture</a>
							<span>Identity, services, and chain boundary</span>
						</li>
						<li>
							<a
								href="https://github.com/a-apin/archimedes/blob/main/docs/security/auth-model.md"
								target="_blank"
								rel="noreferrer"
							>
								Authentication model
							</a>
							<span>Session, wallet proof, and user scoping</span>
						</li>
						<li>
							<a
								href="https://github.com/a-apin/archimedes/blob/main/contracts/src/Vault.sol"
								target="_blank"
								rel="noreferrer"
							>
								Vault contract
							</a>
							<span>Role checks and execution constraints</span>
						</li>
						<li>
							<a
								href="https://github.com/a-apin/archimedes/blob/main/docs/adr/non-custodial-vault-owner-agent.md"
								target="_blank"
								rel="noreferrer"
							>
								Custody decision record
							</a>
							<span>Why owner and agent authority stay separate</span>
						</li>
					</ul>
				</div>
			</section>
		</main>
	);
}
