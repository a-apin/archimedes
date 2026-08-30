import { useEffect, useState } from "react";

import { apiGet } from "../api";
import { ROADMAP_SURFACES_ENABLED } from "../featureFlags.js";
import { landing as ROADMAP_COPY } from "../roadmapCopy.js";

// ConfigService exposes these core singleton fields. Arc-native USDC,
// per-asset oracles, and user vaults are not fully represented, so the
// derived total remains a floor rather than a complete inventory.
const CORE_CONTRACT_FIELDS = [
	"synthetic_factory",
	"amm_router",
	"vault_factory",
	"reasoning_trace_registry",
	"asset_registry",
	"price_oracle",
];

const RIGOR_CRITERIA = [
	{
		code: "DSR",
		name: "Deflated Sharpe Ratio",
		question: "Could this Sharpe be luck after testing many ideas?",
		method: "Corrects for multiple testing and non-normal returns.",
	},
	{
		code: "PBO",
		name: "Probability of Backtest Overfitting",
		question: "Is the result likely to collapse outside its best sample?",
		method: "Compares many train and test splits, not one lucky cut.",
	},
	{
		code: "OOS",
		name: "Walk-forward out-of-sample",
		question: "Does the method survive data it did not fit on?",
		method: "Tested on a 30% chronological held-out window it never trained on.",
	},
	{
		code: "LEAK",
		name: "Look-ahead audit",
		question: "Did future information leak into any decision?",
		method: "Rejects strategy code that reads data before it existed.",
	},
];

const WORKFLOW = [
	{
		title: "Describe",
		body: "State the outcome, assets, risk appetite, and time horizon. The brief stays attached to named research and current market context.",
	},
	{
		title: "Debate",
		body: "Candidate methods are challenged, ranked, and kept beside the alternatives they beat.",
	},
	{
		title: "Gate",
		body: "The selected method faces DSR, PBO, out-of-sample, and look-ahead checks before sizing diagnostics expose its tradeoffs.",
	},
	{
		title: "Authorize",
		body: "You review the passport and sign any vault action with your linked wallet.",
	},
	{
		title: "Inspect",
		body: "Reasoning traces bind context, papers, actions, and an Arc transaction reference into one record.",
	},
];

const FAQS = [
	{
		question: "Does a passed rigor gate guarantee returns?",
		answer:
			"No. The gate reduces known sources of false confidence. It cannot remove market risk or guarantee future performance.",
	},
	{
		question: "What happens when a strategy fails?",
		answer:
			"Failure remains visible with the measured reason. A failed or pending strategy does not receive the verified badge and cannot bypass the server-side deployment gate.",
	},
	{
		question: "Do I need a wallet to explore Archimedes?",
		answer:
			"No. Create an account to generate and save strategies. Link a wallet only when you need proof of on-chain control or want to authorize a vault action.",
	},
	{
		question: "Can Archimedes withdraw from my vault?",
		answer:
			"No. The agent receives rebalance authority within contract rules. Vault ownership and withdrawals stay with the user wallet.",
		roadmapOnly: true,
	},
	{
		question: "Is this running with real money?",
		answer:
			"No. Archimedes currently runs on Arc public testnet with faucet USDC. It is a research prototype, not a production investment product.",
	},
];

export default function Landing() {
	const [contracts, setContracts] = useState(null);
	const [contractsError, setContractsError] = useState(false);

	useEffect(() => {
		apiGet("/api/config/contracts")
			.then(setContracts)
			.catch(() => setContractsError(true));
	}, []);

	const coreCount = contracts
		? CORE_CONTRACT_FIELDS.filter((field) => contracts[field]).length
		: null;
	const synthCount = contracts?.synthetics
		? Object.keys(contracts.synthetics).length
		: null;
	const poolCount = contracts?.pools
		? Object.keys(contracts.pools).length
		: null;
	const poolsUnread = contracts != null && contracts.pools == null;
	const totalLive =
		coreCount != null && synthCount != null && poolCount != null
			? coreCount + synthCount + poolCount
			: null;
	const visibleWorkflow = ROADMAP_SURFACES_ENABLED
		? WORKFLOW
		: WORKFLOW.slice(0, 3);
	const visibleFaqs = ROADMAP_SURFACES_ENABLED
		? FAQS
		: FAQS.filter((item) => !item.roadmapOnly);

	return (
		<main className="public-landing">
			<section className="public-hero" aria-labelledby="public-hero-title">
				<div className="public-shell">
					<div className="public-hero__stage">
						<div className="public-hero__copy">
							<p className="public-hero__eyebrow">
								One cited candidate. Four ways to reject it.
							</p>
							<h1 id="public-hero-title">
								<span>Portfolio strategy,</span> <span>under scrutiny.</span>
							</h1>
							<p className="public-hero__lede">
								{ROADMAP_SURFACES_ENABLED
									? ROADMAP_COPY.heroLede
									: "Archimedes turns a plain-language brief into a paper-grounded strategy, then tests it for selection bias against a rigor gate it must pass before anything runs live."}
							</p>
							<div className="public-actions">
								<a
									className="public-cta public-cta--primary"
									href="/app/generate"
								>
									Generate a strategy
									<span aria-hidden="true">↗</span>
								</a>
								<a className="public-cta public-cta--quiet" href="#product">
									See the product
								</a>
							</div>
						</div>

						<ProductWorkspace
							contractsError={contractsError}
							coreCount={coreCount}
							poolCount={poolCount}
							poolsUnread={poolsUnread}
							synthCount={synthCount}
							totalLive={totalLive}
						/>
					</div>
				</div>
			</section>

			<EvidenceLedger />

			<section
				id="problem"
				className="public-section public-rigor-story"
				aria-labelledby="problem-title"
			>
				<div className="public-shell">
					<div className="public-rigor-story__intro">
						<h2 id="problem-title">A good-looking backtest is not enough.</h2>
						<div>
							<p>
								Trying enough ideas can manufacture a winner. Archimedes records
								the search, tests the selected method, and shows weak evidence
								instead of hiding it.
							</p>
							<ul>
								<li>Named papers stay attached.</li>
								<li>Failed values remain visible.</li>
								<li>Wallet authority stays separate.</li>
							</ul>
						</div>
					</div>

					<div className="public-rigor-story__proof">
						<div className="public-rigor-story__proof-copy">
							<h3 id="rigor-title">Most candidates should fail here.</h3>
							<p>
								Four independent checks look for luck, overfitting, weak
								out-of-sample behavior, and leaked future data.
							</p>
							<strong>Any failed check keeps the candidate unverified.</strong>
						</div>
						<RigorMatrix />
					</div>
				</div>
			</section>

			<section
				id="capabilities"
				className="public-section public-path"
				aria-labelledby="capabilities-title"
			>
				<div className="public-shell">
					<div className="public-path__intro">
						<p className="public-overline">
							One path. {visibleWorkflow.length} records.
						</p>
						<h2 id="capabilities-title">
							Built for inspection, not spectacle.
						</h2>
						<p>
							{ROADMAP_SURFACES_ENABLED
								? "Every surface answers one question: what was requested, what was rejected, what survived, who authorized it, and what happened."
								: "Every surface answers one question: what was requested, what was rejected, and what survived the rigor gate."}
						</p>
					</div>

					<section
						id="workflow"
						className="public-path__sequence"
						aria-labelledby="workflow-title"
					>
						<div className="public-path__sequence-header">
							<h3 id="workflow-title">From intent to accountable action.</h3>
							<a href="/architecture">
								Read system architecture
								<span aria-hidden="true">↗</span>
							</a>
						</div>
						<ol>
							{visibleWorkflow.map((item, index) => (
								<li key={item.title}>
									<span aria-hidden="true">
										{String(index + 1).padStart(2, "0")}
									</span>
									<div>
										<h4>{item.title}</h4>
										<p>{item.body}</p>
									</div>
								</li>
							))}
						</ol>
					</section>
				</div>
			</section>

			<section
				id="use-cases"
				className="public-section public-context"
				aria-labelledby="use-cases-title"
			>
				<div className="public-shell">
					<div className="public-context__intro">
						<h2 id="use-cases-title">Useful when trust needs evidence.</h2>
						<p>
							{ROADMAP_SURFACES_ENABLED
								? "Archimedes fits decisions where research, delegated action, and custody must stay separable."
								: "Archimedes fits research decisions where sources, rejected candidates, and measured limits must stay visible."}
						</p>
					</div>

					<div className="public-use-case-scenes">
						{ROADMAP_SURFACES_ENABLED && (
							<article className="is-custody">
								<span>Controlled autonomy</span>
								<h3>Test idle USDC without surrendering withdrawal authority.</h3>
								<p>
									Arc public testnet keeps the experiment honest and reversible.
								</p>
							</article>
						)}
						<article className="is-research">
							<span>Legible evidence</span>
							<h3>Run quant research without building a quant desk.</h3>
							<p>
								Start in plain language. Inspect papers, backtests, and gates.
							</p>
						</article>
						<article className="is-audit">
							<span>Traceable reasoning</span>
							<h3>Audit what the agent saw, cited, decided, and recorded.</h3>
							<p>
								Context and transaction evidence stay in one reviewable trail.
							</p>
						</article>
					</div>

					<section
						id="integrations"
						className="public-rail-stack"
						aria-labelledby="integrations-title"
					>
						<div className="public-rail-stack__header">
							<p>Working substrate</p>
							<h3 id="integrations-title">Built on rails you can name.</h3>
						</div>
						<ul aria-label="Platform integrations">
							<li>
								<strong>Arc</strong>
								<span>public testnet settlement</span>
							</li>
							<li>
								<strong>Circle</strong>
								<span>native testnet USDC and wallet tooling</span>
							</li>
							<li>
								<strong>AWS Bedrock</strong>
								<span>strategy reasoning</span>
							</li>
							<li>
								<strong>Foundry</strong>
								<span>contract testing and deployment</span>
							</li>
							<li>
								<strong>FastAPI + React</strong>
								<span>agent API and interface</span>
							</li>
						</ul>
					</section>
				</div>
			</section>

			{ROADMAP_SURFACES_ENABLED && <AuthorityBoundary />}

			<section
				id="faq"
				className="public-section public-faq"
				aria-labelledby="faq-title"
			>
				<div className="public-shell public-faq__grid">
					<div className="public-faq__intro">
						<h2 id="faq-title">Before you generate.</h2>
						<p>{visibleFaqs.length} direct answers. No return promises.</p>
					</div>
					<div className="public-faq__list">
						{visibleFaqs.map((item, index) => (
							<details key={item.question} open={index === 0}>
								<summary>{item.question}</summary>
								<p>{item.answer}</p>
							</details>
						))}
					</div>
				</div>
			</section>

			<section className="public-final" aria-labelledby="final-title">
				<div className="public-shell public-final__layout">
					<div>
						<p className="public-final__eyebrow">Start with a brief.</p>
						<h2 id="final-title">Describe the portfolio you want to test.</h2>
					</div>
					<div>
						<a className="public-cta public-cta--primary" href="/app/generate">
							Generate a strategy
							<span aria-hidden="true">↗</span>
						</a>
						<p>
							Arc public testnet only. Past performance is not a promise. A
							rigor gate can reject weak evidence; it cannot remove market risk.
						</p>
					</div>
				</div>
			</section>

			<PublicFooter />
		</main>
	);
}

function ProductWorkspace({
	contractsError,
	coreCount,
	poolCount,
	poolsUnread,
	synthCount,
	totalLive,
}) {
	return (
		<figure id="product" className="public-product-frame">
			<div className="public-product-frame__bar">
				<span>Strategy workspace</span>
				<span>
					{ROADMAP_SURFACES_ENABLED
						? "Brief → gate → authority"
						: "Brief → debate → gate"}
				</span>
			</div>
			<img
				src="/product-workspace.png"
				width={1600}
				height={1000}
				fetchPriority="high"
				alt="Archimedes Generate workspace with a strategy brief, model context, and visible path from brief to rigor gate."
			/>
			<figcaption aria-live="polite">
				{contractsError || poolsUnread ? (
					<>
						<strong className="census-state census-state--error">Live census unavailable</strong>
						<span>
							{contractsError
								? "Contract API did not respond. No cached count substituted."
								: "Contract API responded, but the on-chain pool count could not be read. No cached count substituted."}
						</span>
					</>
				) : totalLive == null ? (
					<>
						<strong className="census-state">Reading Arc contract census</strong>
						<span>Waiting for live deployment data…</span>
					</>
				) : (
					<>
						<strong className="census-state census-state--live">Arc census live</strong>
						<span>{`≥${totalLive} reported instances · ${coreCount}/${CORE_CONTRACT_FIELDS.length} core · ${synthCount} synths · ${poolCount} pools`}</span>
					</>
				)}
			</figcaption>
		</figure>
	);
}

function EvidenceLedger() {
	return (
		<section
			className="public-proof-strip"
			aria-label="Verified product evidence"
		>
			<div className="public-shell">
				<ul>
					<li>
						<strong>Cited methods</strong>
						<span>Source papers remain attached.</span>
					</li>
					<li>
						<strong>Four rejection checks</strong>
						<span>Failures remain part of the record.</span>
					</li>
					<li>
						{ROADMAP_SURFACES_ENABLED ? (
							<>
								<strong>You hold authority</strong>
								<span>Wallet proof appears only for on-chain control.</span>
							</>
						) : (
							<>
								<strong>Verdict stays visible</strong>
								<span>Measured failures remain part of the record.</span>
							</>
						)}
					</li>
				</ul>
				<nav className="public-proof-strip__links" aria-label="Evidence links">
					<a href="/architecture">System architecture</a>
					<a
						href="https://github.com/a-apin/archimedes"
						target="_blank"
						rel="noreferrer"
					>
						Source code
					</a>
					<a href="/llms.txt">Agent API entry point</a>
				</nav>
			</div>
		</section>
	);
}

function RigorMatrix() {
	return (
		<div className="public-proof-deck">
			{RIGOR_CRITERIA.map((criterion) => (
				<article key={criterion.code}>
					<header>
						<span>{criterion.code}</span>
						<span>Independent rejection test</span>
					</header>
					<h4>{criterion.name}</h4>
					<p>{criterion.question}</p>
					<small>{criterion.method}</small>
				</article>
			))}
			<div className="public-proof-deck__rule" role="note">
				<strong>Measured values stay in the record, pass or fail.</strong>
			</div>
		</div>
	);
}

function AuthorityBoundary() {
	return (
		<section
			id="security"
			className="public-section authority-boundary"
			aria-labelledby="authority-title"
		>
			<div className="public-shell">
				<div className="public-section__intro">
					<h2 id="authority-title">Agent cannot withdraw.</h2>
					<p>
						Autonomy stops at ownership. Account identity, wallet control, and
						agent authority remain separate by contract.
					</p>
				</div>
				<div className="authority-boundary__grid">
					<div className="authority-boundary__side authority-boundary__side--agent">
						<p className="authority-boundary__owner">Agent may</p>
						<ul>
							<li>Read market conditions and research evidence</li>
							<li>Propose allocations and rebalance within vault rules</li>
							<li>Commit its reasoning before an enforced trade</li>
						</ul>
					</div>
					<div className="authority-boundary__line" aria-hidden="true">
						<span>contract boundary</span>
					</div>
					<div className="authority-boundary__side authority-boundary__side--user">
						<p className="authority-boundary__owner">Only you may</p>
						<ul>
							<li>Authorize deposits with your wallet</li>
							<li>Retain withdrawal authority over your vault</li>
							<li>Choose whether a validated strategy receives capital</li>
						</ul>
					</div>
				</div>
				<div className="authority-boundary__verdict" role="note">
					<span>Ownership invariant</span>
					<strong>You retain vault ownership.</strong>
					<span>Withdrawals stay with your wallet.</span>
				</div>
				<a className="authority-boundary__link" href="/security">
					Read security posture
					<span aria-hidden="true">↗</span>
				</a>
			</div>
		</section>
	);
}

function PublicFooter() {
	return (
		<footer className="public-footer">
			<div className="public-shell public-footer__grid">
				<div className="public-footer__brand">
					<strong>Archimedes</strong>
					<p>Research-grounded strategy generation on Arc public testnet.</p>
				</div>
				<nav aria-label="Product links">
					<strong>Product</strong>
					<a href="/app/generate">Generate</a>
					<a href="/app/explore">Explore</a>
					<a href="/security">Security</a>
					<a href="/architecture">Architecture</a>
				</nav>
				<nav aria-label="Resource links">
					<strong>Resources</strong>
					<a href="/llms.txt">Agent API</a>
					<a href="/.well-known/agent.json">Agent manifest</a>
					<a
						href="https://github.com/a-apin/archimedes"
						target="_blank"
						rel="noreferrer"
					>
						GitHub
					</a>
				</nav>
				<nav aria-label="Project links">
					<strong>Project</strong>
					<a
						href="https://github.com/a-apin/archimedes/blob/main/LICENSE"
						target="_blank"
						rel="noreferrer"
					>
						Unlicense
					</a>
					<a href="https://faucet.circle.com/" target="_blank" rel="noreferrer">
						Arc faucet
					</a>
					<span>No privacy or terms page published</span>
				</nav>
			</div>
			<div className="public-shell public-footer__base">
				<span>Research prototype. No real funds.</span>
				<span>Past performance does not guarantee future results.</span>
			</div>
		</footer>
	);
}
