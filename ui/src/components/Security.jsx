export default function Security() {
	return (
		<main className="security-page">
			<section className="security-hero" aria-labelledby="security-title">
				<div className="public-shell security-hero__layout">
					<div>
						<p className="public-overline">Public security posture</p>
						<h1 id="security-title">
							Security is enforced boundaries, not a guarantee.
						</h1>
						<p className="security-hero__lede">
							Archimedes separates account identity, wallet proof, and the
							service credentials its own agents run under. These controls
							describe current code—not a promise that failure is impossible.
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
						<h2 id="authority-model-title">
							Three boundaries. No role inflation.
						</h2>
						<p>
							Signing in, proving a wallet, and running an internal agent are
							separate capabilities. None of them implies the others.
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
								A five-minute, single-use EIP-4361 challenge proves control
								before a wallet is linked. Wallet state is not an app
								credential.
							</dd>
						</div>
						<div>
							<dt>
								<span>03</span>
								Bounded internal agent
							</dt>
							<dd>
								Archimedes&apos; own generation and research agents authenticate
								with a service credential, not a user session. A user session
								cannot assume that role, and that role cannot read or write
								another account&apos;s private records.
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
									Production cookies are HttpOnly and Secure. nginx, the UI
									route guard, and FastAPI independently protect private
									surfaces.
								</p>
							</div>
						</li>
						<li>
							<span>Scope</span>
							<div>
								<h3>Private records follow the canonical user ID.</h3>
								<p>
									Profile, strategy, job, and linked-wallet reads resolve
									through the authenticated Better Auth user—not a
									client-supplied address.
								</p>
							</div>
						</li>
						<li>
							<span>Integrity</span>
							<div>
								<h3>Agent-only writes require a service credential.</h3>
								<p>
									User sessions cannot forge internal reasoning traces or any
									other integrity-critical record written by an agent role.
								</p>
							</div>
						</li>
						<li>
							<span>Edge</span>
							<div>
								<h3>Browser and API ingress is constrained.</h3>
								<p>
									Same-origin rules, a hash-restricted script policy, HSTS,
									anti-framing headers, limited browser permissions, and
									separate read/write rate limits reduce common web attack
									paths.
								</p>
							</div>
						</li>
						<li>
							<span>Verdict</span>
							<div>
								<h3>A rigor verdict is computed server-side, never asserted.</h3>
								<p>
									The gate runs outside the generator, on persisted returns, so
									the thing being graded cannot influence its own grade. A failed
									or pending result cannot be relabelled as verified from the
									client.
								</p>
							</div>
						</li>
						<li>
							<span>Provenance</span>
							<div>
								<h3>Reasoning records are content-hashed and anchored on Arc.</h3>
								<p>
									A published trace can be re-hashed and compared against its
									on-chain anchor. That proves the record was not rewritten
									afterwards—it does not prove the reasoning was good.
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
						<h2 id="known-limits-title">
							Controls reduce risk. They do not erase it.
						</h2>
					</div>
					<ul>
						<li>
							<strong>Testnet:</strong> Arc public testnet only. No real funds
							should be used.
						</li>
						<li>
							<strong>No execution:</strong> Archimedes does not trade with
							capital today. Generation, the rigor gate, paper trading, and trace
							anchoring are what run; nothing here should be read as a claim that
							funds are being managed.
						</li>
						<li>
							<strong>Model risk:</strong> Generation is LLM-driven and can be
							wrong in ways the gate does not measure. A passed verdict bounds
							selection bias, not judgement.
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
								href="https://github.com/a-apin/archimedes/blob/main/contracts/src/ReasoningTraceRegistry.sol"
								target="_blank"
								rel="noreferrer"
							>
								Trace registry contract
							</a>
							<span>How a reasoning record is anchored on Arc</span>
						</li>
						<li>
							<a
								href="https://github.com/a-apin/archimedes/blob/main/backend/archimedes/services/live_rigor_gate.py"
								target="_blank"
								rel="noreferrer"
							>
								Live rigor gate
							</a>
							<span>The admission checks, as they actually run</span>
						</li>
					</ul>
				</div>
			</section>
		</main>
	);
}
