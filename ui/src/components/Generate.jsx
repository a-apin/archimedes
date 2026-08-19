import { useCallback, useEffect, useMemo, useState } from "react";
import GenerationStream from "./GenerationStream";
import GenerationStatus from "./GenerationStatus";
import ModelCostPanel from "./ModelCostPanel";
import { EXAMPLE_BRIEFS } from "../data/exampleBriefs";
import { ASSET_GROUPS, SUPPORTED_ASSETS } from "../data/assetUniverse";
import { apiGet, apiPost } from "../api";
import { getAddress } from "../config";
import { GENERATION_QUOTE_ENABLED } from "../featureFlags";
import {
	PAYMENT_STATUS,
	attachQuoteId,
	deriveQuoteView,
	derivePaymentState,
	isPaywallError,
	isQuoteExpired,
} from "../generateQuote";

// /generate spine page — redesigned per issue #872.
//
// Layout (top to bottom):
//   1. Model selector (ModelCostPanel)
//   2. Brief input + submit
//   3. Recent Generations job table
//
// Submitting queues a job and adds a row to the table; the user STAYS on
// the page. Clicking a row opens that job's live SSE stream as a drill-down
// view. The SSE endpoint honours Last-Event-ID for replay, so re-subscribing
// on drill-in shows the full history for a completed job too.
//
// Upfront cost quote (feature-flagged, GENERATION_QUOTE_ENABLED in
// featureFlags.js — off by default): when on, a quote is fetched from
// GET /api/generate/quote and shown before the submit button, which reads
// "Approve & Generate" and carries `quote_id` on /start. If /start 402s
// (backend paywall active — docs/specs/generation-quote-contract.md,
// PROPOSED), the submit row switches to a payment-step banner instead of
// the generic error: wallet-required if none is connected, otherwise an
// honest "payments aren't enabled yet" preview — this build does not sign
// or execute an on-chain payment. State-transition logic lives in
// ../generateQuote.js so it's unit-testable without a DOM.

const RISK_PROFILES = [
	{ id: "fixed_income", label: "Fixed income" },
	{ id: "conservative", label: "Conservative" },
	{ id: "moderate", label: "Moderate" },
	{ id: "aggressive", label: "Aggressive" },
	{ id: "hyper_risky", label: "Hyper-risky" },
];

// ─────────────────────────────────────────────────────────────

export default function Generate({ onNavigate, onStageChange }) {
	// ── Brief form state ──
	const [intent, setIntent] = useState("");
	const [starting, setStarting] = useState(false);
	const [startError, setStartError] = useState("");

	// ── Model selector (lifted; passed to ModelCostPanel) ──
	const [selectedModel, setSelectedModel] = useState(null);

	// ── Advanced options (hidden by default) ──
	const [advancedOpen, setAdvancedOpen] = useState(false);
	const [riskAppetite, setRiskAppetite] = useState("moderate");
	const [depth, setDepth] = useState(5);
	const [selectedAssets, setSelectedAssets] = useState([]);
	const [assetQuery, setAssetQuery] = useState("");
	const [strategyName, setStrategyName] = useState("");

	// ── Drill-down: which job's stream to show (null = table view) ──
	const [drillInJobId, setDrillInJobId] = useState(null);

	useEffect(() => {
		onStageChange?.(drillInJobId ? "debate" : "brief");
	}, [drillInJobId, onStageChange]);

	// ── Track the most-recently submitted job so the table can highlight it ──
	const [lastJobId, setLastJobId] = useState(null);

	// ── Upfront cost quote (feature-flagged) ──
	const [quote, setQuote] = useState(null);
	const [quoteStatus, setQuoteStatus] = useState(
		GENERATION_QUOTE_ENABLED ? "loading" : "idle",
	);
	const [quoteError, setQuoteError] = useState("");
	// Set true only by a genuine 402 from /start (isPaywallError). Cleared on
	// every fresh submit attempt so a stale banner never survives a retry.
	const [paywallActive, setPaywallActive] = useState(false);
	const [walletAddr, setWalletAddr] = useState(() => getAddress());

	const fetchQuote = useCallback(() => {
		if (!GENERATION_QUOTE_ENABLED) return;
		setQuoteStatus("loading");
		setQuoteError("");
		const qs = selectedModel ? `?model=${encodeURIComponent(selectedModel)}` : "";
		apiGet(`/api/generate/quote${qs}`)
			.then((data) => {
				setQuote(data);
				setQuoteStatus("ready");
			})
			.catch((e) => {
				setQuote(null);
				setQuoteError(e.message || "Cost quote unavailable");
				setQuoteStatus("error");
			});
	}, [selectedModel]);

	useEffect(() => {
		fetchQuote();
	}, [fetchQuote]);

	// Wallet connect/disconnect is a global event (config.js), not React
	// state — re-derive our copy on change so the payment-step banner can
	// flip from "connect a wallet" to the payment preview live.
	useEffect(() => {
		const onWalletChanged = () => setWalletAddr(getAddress());
		window.addEventListener("wallet-changed", onWalletChanged);
		return () => window.removeEventListener("wallet-changed", onWalletChanged);
	}, []);

	const quoteView = useMemo(() => deriveQuoteView(quote), [quote]);
	const quoteReady =
		!GENERATION_QUOTE_ENABLED ||
		(quoteStatus === "ready" && Boolean(quoteView) && !isQuoteExpired(quote));
	const paymentStatus = paywallActive
		? derivePaymentState(walletAddr)
		: PAYMENT_STATUS.NONE;

	// ── Submit a generation job ──
	const startJob = async () => {
		setStartError("");
		if (!intent.trim()) {
			setStartError("Describe what you want in a sentence or two.");
			return;
		}
		if (GENERATION_QUOTE_ENABLED && isQuoteExpired(quote)) {
			setStartError("Your quote expired — fetching a new one, try again in a moment.");
			fetchQuote();
			return;
		}
		if (GENERATION_QUOTE_ENABLED && !quoteReady) {
			setStartError("Cost quote isn't ready yet.");
			return;
		}
		setStarting(true);
		setPaywallActive(false);
		const payload = attachQuoteId(
			{
				brief: {
					intent,
					risk_appetite: riskAppetite,
					asset_classes: selectedAssets.length > 0 ? selectedAssets : undefined,
					max_papers: depth,
					...(strategyName.trim() ? { name: strategyName.trim() } : {}),
				},
				...(selectedModel ? { model: selectedModel } : {}),
			},
			{ quoteEnabled: GENERATION_QUOTE_ENABLED, approvedQuoteId: quote?.quote_id },
		);
		try {
			const data = await apiPost("/api/generate/start", payload);
			setLastJobId(data.job_id);
			// Quote is consumed by a successful /start — fetch a fresh one for
			// the next generation rather than letting a spent quote_id linger.
			if (GENERATION_QUOTE_ENABLED) fetchQuote();
			// Stay on the page — the job table will show the new row.
		} catch (e) {
			if (isPaywallError(e)) {
				setPaywallActive(true);
			} else {
				setStartError(e.message || "Failed to start generation");
			}
		} finally {
			setStarting(false);
		}
	};

	// ── Apply an example brief ──
	const applyExample = (ex) => {
		setIntent(ex.brief);
		if (Array.isArray(ex.suggestedAssets) && ex.suggestedAssets.length) {
			setSelectedAssets(
				ex.suggestedAssets.filter((a) => SUPPORTED_ASSETS.includes(a)),
			);
		}
	};

	// ── Asset picker helpers ──
	const toggleAsset = (a) =>
		setSelectedAssets((prev) =>
			prev.includes(a) ? prev.filter((x) => x !== a) : [...prev, a],
		);
	const clearAssets = () => setSelectedAssets([]);

	const filteredAssetGroups = useMemo(() => {
		const q = assetQuery.trim().toLowerCase();
		if (!q) return ASSET_GROUPS;
		return ASSET_GROUPS.map((g) => ({
			...g,
			assets: g.assets.filter((a) => a.toLowerCase().includes(q)),
		})).filter((g) => g.assets.length > 0);
	}, [assetQuery]);

	// ── Drill-down: open stream for a job ──
	const handleDrillIn = (jobId) => setDrillInJobId(jobId);
	const handleBackToTable = () => setDrillInJobId(null);

	// ─── Drill-down view (stream for a selected job) ───────────
	if (drillInJobId) {
		return (
			<div className="generate-page generate-page--stream">
				<div className="app-page-heading generate-page__heading">
					<button
						type="button"
						className="btn btn-outline btn-sm"
						onClick={handleBackToTable}
						style={{ marginBottom: 12 }}
					>
						← Back to generations
					</button>
					<h2 className="serif text-[1.6rem] mb-1">Generation stream</h2>
					<p className="caption" style={{ color: "var(--text-3)" }}>
						Job {drillInJobId.slice(0, 10)}… — full history replayed from the
						start. Navigate away and come back; the job continues running in the
						background.
					</p>
				</div>
				<GenerationStream
					jobId={drillInJobId}
					onDone={() => {}}
					onReset={handleBackToTable}
					onPipelineSelected={() => {}}
					onNavigate={onNavigate}
					hideReset
				/>
			</div>
		);
	}

	// ─── Main view (model → brief → job table) ─────────────────
	return (
		<div className="generate-page">
			<header className="app-page-heading generate-page__heading">
				<p className="app-eyebrow">Strategy synthesis</p>
				<h1>Generate a strategy</h1>
				<p>
					Describe the outcome you want. Archimedes retrieves relevant q-fin
					papers, debates candidate methods, sizes positions, and sends one
					winner to the rigor gate.
				</p>
			</header>

			<div className="generate-workbench">
				{/* ── 1. BRIEF INPUT + SUBMIT ── */}
				<section className="card generate-brief">
					{/* Strategy name — promoted from Advanced options so it's seen before the brief */}
					<div className="mb-3">
						<div className="label mb-1">Strategy name (optional)</div>
						<input
							type="text"
							value={strategyName}
							onChange={(e) => setStrategyName(e.target.value)}
							placeholder="Leave blank — backend auto-derives a name"
							maxLength={80}
							className="chat-input w-full px-2.5 py-1.5"
							disabled={starting}
						/>
						<p className="caption mt-1" style={{ color: "var(--text-3)" }}>
							Short and memorable — leave blank to auto-name.{" "}
							{strategyName.length}/80
						</p>
					</div>

					<div className="label mb-1">Your brief</div>
					<p className="caption mb-2" style={{ color: "var(--text-3)" }}>
						A good brief names concrete assets or classes, a mechanism (momentum
						/ vol-managed / hedge / mean-reversion), and a goal.
					</p>

					<textarea
						value={intent}
						onChange={(e) => setIntent(e.target.value)}
						placeholder="e.g. blend momentum, quality and a gold hedge across major ETFs with volatility-managed sizing for idle USDC"
						rows={3}
						className="chat-input w-full mb-3 p-2.5 leading-relaxed"
						disabled={starting}
					/>

					{/* Example briefs */}
					<div className="mb-3">
						<div className="caption mb-1.5" style={{ color: "var(--text-3)" }}>
							Examples — click to fill:
						</div>
						<div className="flex flex-col gap-1.5">
							{EXAMPLE_BRIEFS.map((ex) => (
								<button
									key={ex.id}
									type="button"
									onClick={() => applyExample(ex)}
									disabled={starting}
									className="generate-example"
								>
									<span style={{ color: "var(--accent)", marginRight: 6 }}>
										→
									</span>
									{ex.label}
								</button>
							))}
						</div>
					</div>

					{/* Advanced options — collapsed by default */}
					<div
						className="mb-3"
						style={{
							borderTop: "1px solid var(--glass-border)",
							paddingTop: 10,
						}}
					>
						<button
							type="button"
							className="generate-advanced-toggle"
							onClick={() => setAdvancedOpen((o) => !o)}
							aria-expanded={advancedOpen}
						>
							<span
								className={`${advancedOpen ? "i-lucide-chevron-down" : "i-lucide-chevron-right"} w-3.5 h-3.5`}
							/>
							Advanced options
							{(selectedAssets.length > 0 ||
								riskAppetite !== "moderate" ||
								depth !== 5) && (
								<span
									className="tag tag-accent"
									style={{ fontSize: "0.7rem", padding: "1px 6px" }}
								>
									active
								</span>
							)}
						</button>

						{advancedOpen && (
							<div style={{ marginTop: 12 }}>
								{/* Risk + Depth selects */}
								<div className="flex gap-4 flex-wrap mb-3">
									<label className="caption flex items-center gap-1.5">
										Risk
										<select
											value={riskAppetite}
											onChange={(e) => setRiskAppetite(e.target.value)}
											className="chat-input w-auto px-2 py-1"
											disabled={starting}
										>
											{RISK_PROFILES.map((r) => (
												<option key={r.id} value={r.id}>
													{r.label}
												</option>
											))}
										</select>
									</label>
									<label className="caption flex items-center gap-1.5">
										Depth
										<select
											value={depth}
											onChange={(e) => setDepth(Number(e.target.value))}
											className="chat-input w-auto px-2 py-1"
											disabled={starting}
											title="How many papers / strategies the engine considers"
										>
											{[2, 3, 4, 5, 6, 8, 10].map((n) => (
												<option key={n} value={n}>
													{n}
												</option>
											))}
										</select>
									</label>
								</div>

								{/* Asset picker */}
								<div>
									<div className="flex items-center justify-between mb-1">
										<div className="label" style={{ fontSize: "0.82rem" }}>
											Assets (optional)
											{selectedAssets.length > 0
												? ` · ${selectedAssets.length} selected`
												: ""}
										</div>
										{selectedAssets.length > 0 && (
											<button
												type="button"
												onClick={clearAssets}
												className="caption"
												style={{
													background: "none",
													border: "none",
													cursor: "pointer",
													color: "var(--accent)",
												}}
											>
												Clear
											</button>
										)}
									</div>
									<p
										className="caption mb-2"
										style={{ color: "var(--text-3)" }}
									>
										Steer the universe. Leave empty to use the full supported
										set.
									</p>
									<input
										type="text"
										value={assetQuery}
										onChange={(e) => setAssetQuery(e.target.value)}
										placeholder="Search assets (e.g. SPY, GOLD, BTC)…"
										className="chat-input w-full mb-2 px-2.5 py-1.5"
										disabled={starting}
									/>
									<div
										style={{
											maxHeight: 180,
											overflowY: "auto",
											paddingRight: 4,
										}}
									>
										{filteredAssetGroups.length === 0 && (
											<div
												className="caption"
												style={{ color: "var(--text-3)" }}
											>
												No assets match "{assetQuery}".
											</div>
										)}
										{filteredAssetGroups.map((group) => (
											<div key={group.id} className="mb-2">
												<div
													className="caption mb-1"
													style={{ color: "var(--text-3)" }}
												>
													{group.label}
												</div>
												<div className="flex gap-1.5 flex-wrap">
													{group.assets.map((a) => (
														<span
															key={a}
															className={`tag ${selectedAssets.includes(a) ? "tag-accent" : "tag-muted"} cursor-pointer`}
															onClick={() => toggleAsset(a)}
														>
															{a}
														</span>
													))}
												</div>
											</div>
										))}
									</div>
								</div>
							</div>
						)}
					</div>

					{/* ── Upfront cost quote (feature-flagged) ── */}
					{GENERATION_QUOTE_ENABLED && (
						<div className="generate-quote mb-3">
							{quoteStatus === "loading" && (
								<p className="caption" style={{ color: "var(--text-3)" }}>
									Fetching cost quote…
								</p>
							)}
							{quoteStatus === "error" && (
								<div className="info-box warning">
									Cost quote unavailable ({quoteError}).{" "}
									<button
										type="button"
										onClick={fetchQuote}
										className="caption"
										style={{
											background: "none",
											border: "none",
											cursor: "pointer",
											color: "var(--accent)",
											textDecoration: "underline",
											padding: 0,
										}}
									>
										Try again
									</button>
								</div>
							)}
							{quoteStatus === "ready" && quoteView && (
								<div className="info-box">
									<div className="flex items-center flex-wrap gap-2">
										<span className="label">Cost to generate</span>
										<span className="tag tag-accent">{quoteView.priceLabel}</span>
										<span className="tag tag-info">testnet USDC</span>
									</div>
									{quoteView.breakdown.length > 0 && (
										<ul style={{ margin: "8px 0 0", paddingLeft: 18 }}>
											{quoteView.breakdown.map((b) => (
												<li
													key={b.label}
													className="caption"
													style={{ color: "var(--text-3)" }}
												>
													{b.label}: {b.amount_usdc} USDC-testnet
												</li>
											))}
										</ul>
									)}
									<p
										className="caption mb-0"
										style={{ marginTop: 8, color: "var(--text-3)" }}
									>
										<span className="tag tag-positive" style={{ marginRight: 6 }}>
											free
										</span>
										Paper trading after generation costs nothing — this quote
										covers generation only.
									</p>
								</div>
							)}
						</div>
					)}

					{/* Submit row */}
					<div
						className="flex items-center justify-between flex-wrap gap-2"
						style={{ marginTop: 2 }}
					>
						{paymentStatus === PAYMENT_STATUS.WALLET_REQUIRED ? (
							<div className="info-box warning" style={{ flex: 1 }}>
								<strong>Payment required.</strong> Connect a wallet to pay for
								this generation in testnet USDC.{" "}
								<button
									type="button"
									onClick={() =>
										window.dispatchEvent(new Event("open-wallet-modal"))
									}
									className="caption"
									style={{
										background: "none",
										border: "none",
										cursor: "pointer",
										color: "var(--accent)",
										textDecoration: "underline",
										padding: 0,
									}}
								>
									Connect wallet →
								</button>
							</div>
						) : paymentStatus === PAYMENT_STATUS.PAYMENT_PREVIEW ? (
							<div className="info-box warning" style={{ flex: 1 }}>
								<strong>Payments aren't enabled yet.</strong> This generation
								would cost {quoteView?.priceLabel ?? "the quoted amount"} from
								your connected wallet once live. Nothing was charged.
							</div>
						) : startError ? (
							<div className="info-box warning" style={{ flex: 1 }}>
								{startError}
							</div>
						) : (
							<div />
						)}
						<button
							className="btn btn-primary"
							onClick={startJob}
							disabled={starting || !intent.trim() || !quoteReady}
						>
							{starting
								? "Starting…"
								: GENERATION_QUOTE_ENABLED
									? "Approve & Generate →"
									: "Generate →"}
						</button>
					</div>
				</section>

				<aside
					className="generate-context-rail"
					aria-label="Generation context"
				>
					<ModelCostPanel
						selectedModel={selectedModel}
						onSelectModel={setSelectedModel}
					/>
					<div className="generate-context-note">
						<p className="label">Pipeline inputs</p>
						<dl>
							<div>
								<dt>Your brief</dt>
								<dd>goal, risk, assets</dd>
							</div>
							<div>
								<dt>Market context</dt>
								<dd>current regime signals</dd>
							</div>
							<div>
								<dt>Research</dt>
								<dd>paper methods and evidence</dd>
							</div>
						</dl>
						<p>
							Output: one candidate, considered rejects, and a visible rigor
							verdict.
						</p>
					</div>
				</aside>
			</div>

			{/* ── 2. JOB REGISTER ── */}
			<section className="generate-register" aria-label="Generation register">
				<GenerationStatus activeJobId={lastJobId} onDrillIn={handleDrillIn} />
			</section>
		</div>
	);
}
