import { useEffect, useState } from "react";
import { apiGet } from "../api";
import { ROADMAP_SURFACES_ENABLED } from "../featureFlags.js";
import { landing as ROADMAP_COPY } from "../roadmapCopy.js";

// ConfigService returns these core singleton fields today. Arc-native USDC is
// excluded; per-asset oracles and user vaults are not fully represented, so the
// derived total is always shown as a floor rather than a complete inventory.
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

export default function Landing({ onNavigate }) {
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
	// contracts.pools is explicitly `null` (not just absent) when the fetch
	// succeeded (200) but the on-chain pool read itself failed (#1356's
	// ContractAddressesResponse contract: null means "not read", distinct
	// from {} which means "read, genuinely zero"). That's a live failure, not
	// a still-loading state — `contracts != null` means the fetch already
	// resolved, so this must render as census-state--error, never as
	// "Waiting for live deployment data…".
	const poolsUnread = contracts != null && contracts.pools == null;
	const totalLive =
		coreCount != null && synthCount != null && poolCount != null
			? coreCount + synthCount + poolCount
			: null;

	return (
		<main className="public-landing">
			<section className="public-hero" aria-labelledby="public-hero-title">
				<div className="public-shell public-hero__grid">
					<div className="public-hero__copy">
						<p className="public-kicker">
							Research <span>→</span> rigor{ROADMAP_SURFACES_ENABLED && (
								<> <span>→</span> {ROADMAP_COPY.kickerVault}</>
							)}
						</p>
						<h1 id="public-hero-title">
							Capital should follow
							<em>a thesis it can prove.</em>
						</h1>
						<p className="public-hero__lede">
							{ROADMAP_SURFACES_ENABLED
								? ROADMAP_COPY.heroLede
								: "Archimedes turns a plain-language brief into a paper-grounded strategy, then tests it for selection bias against a rigor gate it must pass before anything runs live."}
						</p>
						<div className="public-actions">
							<button
								type="button"
								className="public-cta public-cta--primary"
								onClick={() => onNavigate("generate")}
							>
								Generate a strategy <span aria-hidden="true">→</span>
							</button>
							<button
								type="button"
								className="public-cta public-cta--quiet"
								onClick={() => onNavigate("library", { tab: "examples" })}
							>
								Browse example library
							</button>
						</div>
						<p className="public-hero__note">
							Research prototype on Arc public testnet. Start with an account;
							your wallet appears only when you authorize an on-chain action.
						</p>
					</div>

					<ProofSpiral
						contractsError={contractsError}
						coreCount={coreCount}
						poolCount={poolCount}
						poolsUnread={poolsUnread}
						synthCount={synthCount}
						totalLive={totalLive}
					/>
				</div>
			</section>

			<EvidenceLedger />

			<section
				className="public-section public-rigor"
				aria-labelledby="rigor-title"
			>
				<div className="public-shell">
					<div className="public-section__intro">
						<div>
							<p className="public-kicker">Admission record</p>
							<h2 id="rigor-title">Rigor is not a badge.</h2>
						</div>
						<p>
							It is a rejection mechanism. Every criterion answers a different
							way a backtest can fool you; every value remains visible whether a
							candidate passes or fails.
						</p>
					</div>
					<RigorMatrix />
				</div>
			</section>

			{ROADMAP_SURFACES_ENABLED && <AuthorityBoundary />}

			<section
				className="public-section public-stack"
				aria-labelledby="stack-title"
			>
				<div className="public-shell public-stack__layout">
					<div>
						<p className="public-kicker">Working substrate</p>
						<h2 id="stack-title">Real rails, named plainly.</h2>
					</div>
					<ul aria-label="Platform integrations">
						<li>
							<strong>Arc</strong>
							<span>public testnet settlement</span>
						</li>
						<li>
							<strong>Circle</strong>
							<span>native USDC</span>
						</li>
						<li>
							<strong>AWS Bedrock</strong>
							<span>strategy reasoning</span>
						</li>
						<li>
							<strong>Foundry</strong>
							<span>contract suite</span>
						</li>
						<li>
							<strong>FastAPI + React</strong>
							<span>agent and interface</span>
						</li>
					</ul>
				</div>
			</section>

			<section className="public-final" aria-labelledby="final-title">
				<div className="public-shell public-final__layout">
					<div>
						<p className="public-kicker">Your next move</p>
						<h2 id="final-title">Give your capital a thesis to prove.</h2>
					</div>
					<div>
						<button
							type="button"
							className="public-cta public-cta--primary"
							onClick={() => onNavigate("generate")}
						>
							Generate a strategy <span aria-hidden="true">→</span>
						</button>
						<p>
							Past performance is not a promise. The gate reduces known sources
							of false confidence; it cannot remove market risk.
						</p>
					</div>
				</div>
			</section>
			{/* Footer moved to PublicLayout.jsx (shared shell chrome, also covers
			    Architecture + not-found) — see the .public-footer block there. */}
		</main>
	);
}

function ProofSpiral({
	contractsError,
	coreCount,
	poolCount,
	poolsUnread,
	synthCount,
	totalLive,
}) {
	return (
		<figure className="proof-spiral">
			<div className="proof-spiral__header">
				<span>Proof instrument</span>
				<span>r = a + bθ</span>
			</div>
			<svg
				className="proof-spiral__plot"
				viewBox="0 0 560 410"
				role="img"
				aria-labelledby="proof-spiral-title proof-spiral-description"
			>
				<title id="proof-spiral-title">
					{ROADMAP_SURFACES_ENABLED
						? ROADMAP_COPY.spiralTitle
						: "Brief, debate, rigor proof flow"}
				</title>
				<desc id="proof-spiral-description">
					{ROADMAP_SURFACES_ENABLED
						? ROADMAP_COPY.spiralDesc
						: "An Archimedean spiral traces a strategy from the user brief through multi-agent debate to the rigor gate that decides whether it can run live."}
				</desc>
				<line
					className="proof-spiral__axis"
					x1="38"
					x2="520"
					y1="241"
					y2="241"
				/>
				<line
					className="proof-spiral__axis"
					x1="285"
					x2="285"
					y1="34"
					y2="374"
				/>
				<path
					className="proof-spiral__track"
					pathLength="100"
					d="M285 241 C271 246 255 235 258 217 C262 191 293 179 317 197 C346 219 342 268 303 287 C255 311 193 277 199 216 C205 149 284 113 347 139 C418 168 434 272 365 323 C274 391 124 370 102 257 C83 159 147 75 270 70 C368 66 450 118 450 208"
				/>
				<path
					className="proof-spiral__pulse"
					pathLength="100"
					d="M285 241 C271 246 255 235 258 217 C262 191 293 179 317 197 C346 219 342 268 303 287 C255 311 193 277 199 216 C205 149 284 113 347 139 C418 168 434 272 365 323 C274 391 124 370 102 257 C83 159 147 75 270 70 C368 66 450 118 450 208"
				/>
				<g className="proof-spiral__node proof-spiral__node--user">
					<circle cx="285" cy="241" r="7" />
					<text x="235" y="260">
						Brief
					</text>
				</g>
				<g className="proof-spiral__node proof-spiral__node--system">
					<circle cx="317" cy="197" r="7" />
					<text x="332" y="188">
						Debate
					</text>
				</g>
				<g className="proof-spiral__node proof-spiral__node--system">
					<circle cx="303" cy="287" r="7" />
					<text x="316" y="310">
						Rigor
					</text>
				</g>
				{ROADMAP_SURFACES_ENABLED && (
					<g className="proof-spiral__node proof-spiral__node--user">
						<circle cx="450" cy="208" r="7" />
						<text x="463" y="228">
							{ROADMAP_COPY.spiralVaultLabel}
						</text>
					</g>
				)}
			</svg>

			<ol className="proof-spiral__legend" aria-label="Strategy proof flow">
				<li className="is-user">Brief</li>
				<li>Debate</li>
				<li>Rigor</li>
				{ROADMAP_SURFACES_ENABLED && (
					<li className="is-user">{ROADMAP_COPY.spiralVaultLabel}</li>
				)}
			</ol>

			<div className="proof-spiral__census" aria-live="polite">
				{contractsError || poolsUnread ? (
					<>
						<span className="census-state census-state--error">
							Live census unavailable
						</span>
						<p>
							{contractsError
								? "Contract API did not respond. No cached count substituted."
								: "Contract API responded, but the on-chain pool count could not be read. No cached count substituted."}
						</p>
					</>
				) : totalLive == null ? (
					<>
						<span className="census-state">Reading Arc contract census</span>
						<p>Waiting for live deployment data…</p>
					</>
				) : (
					<>
						<span className="census-state census-state--live">
							Arc census live
						</span>
						<p>
							<strong>≥{totalLive}</strong> reported instances ·{" "}
							{coreCount}/{CORE_CONTRACT_FIELDS.length} core · {synthCount}{" "}
							synths · {poolCount} pools
						</p>
					</>
				)}
			</div>
		</figure>
	);
}

function EvidenceLedger() {
	return (
		<section className="evidence-ledger" aria-label="Evidence standards">
			<div className="public-shell">
				<ul>
					<li>
						<span>Paper-grounded</span>
						<p>Methods keep named source papers attached.</p>
					</li>
					<li>
						<span>Bias-corrected</span>
						<p>Gate values stay visible, including failures.</p>
					</li>
					<li>
						<span>Commit-before-trade</span>
						<p>A vault rebalance requires an earlier reasoning commitment.</p>
					</li>
				</ul>
			</div>
		</section>
	);
}

function RigorMatrix() {
	return (
		<div className="rigor-matrix">
			{RIGOR_CRITERIA.map((criterion) => (
				<article key={criterion.code}>
					<div className="rigor-matrix__label">
						<span>{criterion.code}</span>
						<span>candidate check</span>
					</div>
					<h3>{criterion.name}</h3>
					<p>{criterion.question}</p>
					<small>{criterion.method}</small>
				</article>
			))}
		</div>
	);
}

function AuthorityBoundary() {
	return (
		<section
			className="public-section authority-boundary"
			aria-labelledby="authority-title"
		>
			<div className="public-shell">
				<div className="public-section__intro">
					<div>
						<p className="public-kicker">Custody boundary</p>
						<h2 id="authority-title">Autonomy stops at ownership.</h2>
					</div>
					<p>
						Agent receives enough authority to operate a strategy, never enough
						to take custody. Contract rules keep those roles separate.
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
							<li>{ROADMAP_COPY.authorityWithdrawBullet}</li>
							<li>Choose whether a validated strategy receives capital</li>
						</ul>
					</div>
				</div>
				<p className="authority-boundary__footnote">
					Agent cannot withdraw to the platform or replace vault ownership.
				</p>
			</div>
		</section>
	);
}
