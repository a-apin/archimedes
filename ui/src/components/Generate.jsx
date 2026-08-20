import { useCallback, useEffect, useMemo, useState } from "react";
import GenerationStream from "./GenerationStream";
import GenerationStatus from "./GenerationStatus";
import ModelCostPanel from "./ModelCostPanel";
import { EXAMPLE_BRIEFS } from "../data/exampleBriefs";
import { ASSET_GROUPS, SUPPORTED_ASSETS } from "../data/assetUniverse";
import { apiGet, apiPostWithMeta } from "../api";
import { getAddress } from "../config";
import { listLinkedWallets } from "../linked-wallets";
import { GENERATION_QUOTE_ENABLED } from "../featureFlags";
import {
	PAYMENT_STATUS,
	buildDryRunPaymentHeader,
	deriveQuoteView,
	derivePaymentState,
	describePayerMismatch,
	extractReceipt,
	paymentErrorMessage,
	resolveDryRunPayer,
} from "../generateQuote";

const shortAddr = (addr) => (addr ? `${addr.slice(0, 6)}…${addr.slice(-4)}` : "");

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
// Upfront cost quote + x402 paywall (feature-flagged, GENERATION_QUOTE_ENABLED
// in featureFlags.js — off by default): when on, a quote is fetched from the
// PUBLIC GET /api/generate/quote and shown before the submit button.
// /start's request body is unchanged — the ratified contract
// (docs/specs/generation-quote-contract.md, #1296) carries no quote_id or
// any other payment field on the body; the gate lives entirely in status
// codes: a 409 means "link + fund a wallet first" (CTA reuses the existing
// open-wallet-modal flow), a 402 means "sign and retry with
// Payment-Signature" — while the backend runs PAYMENTS_DRY_RUN (its
// default), any non-empty header is accepted UNVERIFIED, so this build
// offers a real, honestly-labeled "continue in test mode" retry instead of
// a non-functional pay button (real x402 signing isn't wired here). A
// settlement receipt (PAYMENT-RESPONSE header) is surfaced when present.
// State-transition logic lives in ../generateQuote.js so it's unit-testable
// without a DOM.

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

	// ── Upfront cost quote + x402 paywall (feature-flagged) ──
	const [quote, setQuote] = useState(null);
	const [quoteStatus, setQuoteStatus] = useState(
		GENERATION_QUOTE_ENABLED ? "loading" : "idle",
	);
	const [quoteError, setQuoteError] = useState("");
	const [walletAddr, setWalletAddr] = useState(() => getAddress());
	// The account's linked wallets (GET /api/wallets) — fetched only once the
	// quote says payments are actually required, so this call costs nothing
	// in the common (flag-off) case. Used purely for the payer-binding
	// transparency note (describePayerMismatch); never used to *select* a
	// signer, since real signing isn't wired in this build.
	const [linkedWallets, setLinkedWallets] = useState([]);
	// The payment-step banner. Set only from a genuine 409/402 on /start
	// (derivePaymentState); cleared on every fresh submit attempt so a stale
	// banner never survives a retry.
	const [paymentStatus, setPaymentStatus] = useState(PAYMENT_STATUS.NONE);
	const [paymentMessage, setPaymentMessage] = useState("");
	// True only right after a successful "continue in test mode" submit —
	// the honest label the ratified contract requires (PAYMENTS_DRY_RUN:
	// the payment header was accepted WITHOUT verification or settlement).
	const [testModeNotice, setTestModeNotice] = useState(false);
	const [continuingTestMode, setContinuingTestMode] = useState(false);
	// The PAYMENT-RESPONSE settlement receipt from a successful /start, if
	// the response carried one (only true payments settled — live mode).
	const [receipt, setReceipt] = useState(null);

	const fetchQuote = useCallback(() => {
		if (!GENERATION_QUOTE_ENABLED) return;
		setQuoteStatus("loading");
		setQuoteError("");
		apiGet("/api/generate/quote")
			.then((data) => {
				setQuote(data);
				setQuoteStatus("ready");
			})
			.catch((e) => {
				setQuote(null);
				setQuoteError(e.message || "Cost quote unavailable");
				setQuoteStatus("error");
			});
	}, []);

	useEffect(() => {
		fetchQuote();
	}, [fetchQuote]);

	// Wallet connect/disconnect is a global event (config.js), not React
	// state — re-derive our copy on change so the payer-mismatch note and
	// payment-step banner stay live.
	useEffect(() => {
		const onWalletChanged = () => setWalletAddr(getAddress());
		window.addEventListener("wallet-changed", onWalletChanged);
		return () => window.removeEventListener("wallet-changed", onWalletChanged);
	}, []);

	// Linked wallets: only needed once the quote reports payments are
	// actually required (flag on server-side) — the common case (flag off)
	// never makes this call.
	useEffect(() => {
		if (!GENERATION_QUOTE_ENABLED || !quote?.payment_required) return;
		let cancelled = false;
		listLinkedWallets()
			.then((wallets) => {
				if (!cancelled) setLinkedWallets(Array.isArray(wallets) ? wallets : []);
			})
			.catch(() => {
				if (!cancelled) setLinkedWallets([]);
			});
		return () => {
			cancelled = true;
		};
	}, [quote?.payment_required]);

	const quoteView = useMemo(() => deriveQuoteView(quote), [quote]);
	const quoteReady = !GENERATION_QUOTE_ENABLED || (quoteStatus === "ready" && Boolean(quoteView));
	// The wallet actually active in the injected provider vs. the account's
	// LINKED wallet — signing must use the linked one, not whatever's merely
	// selected right now. Null when there's nothing to flag (see
	// describePayerMismatch's own doc for the exact conditions).
	const payerMismatch = useMemo(
		() => describePayerMismatch(walletAddr, linkedWallets),
		[walletAddr, linkedWallets],
	);

	// ── Build the /start request body. No payment field on it anywhere —
	// the ratified contract (#1296) gates entirely via status codes/headers,
	// not a request-body field (the PROPOSED contract's quote_id is dead). ──
	const buildBrief = () => ({
		brief: {
			intent,
			risk_appetite: riskAppetite,
			asset_classes: selectedAssets.length > 0 ? selectedAssets : undefined,
			max_papers: depth,
			...(strategyName.trim() ? { name: strategyName.trim() } : {}),
		},
		...(selectedModel ? { model: selectedModel } : {}),
	});

	// POST /start, optionally with a Payment-Signature header, and surface
	// whatever the response carries (job id + PAYMENT-RESPONSE if present).
	const submitStart = async (extraHeaders) => {
		const { data, headers } = await apiPostWithMeta(
			"/api/generate/start",
			buildBrief(),
			extraHeaders,
		);
		setLastJobId(data.job_id);
		setReceipt(extractReceipt(headers));
		return data;
	};

	// ── Submit a generation job ──
	const startJob = async () => {
		setStartError("");
		if (!intent.trim()) {
			setStartError("Describe what you want in a sentence or two.");
			return;
		}
		if (GENERATION_QUOTE_ENABLED && !quoteReady) {
			setStartError("Cost quote isn't ready yet.");
			return;
		}
		setStarting(true);
		setPaymentStatus(PAYMENT_STATUS.NONE);
		setPaymentMessage("");
		setTestModeNotice(false);
		setReceipt(null);
		try {
			await submitStart();
			// Re-quote for the next generation — flat pricing has nothing to
			// consume, but the flag/dry_run/recipient could change underneath.
			if (GENERATION_QUOTE_ENABLED) fetchQuote();
			// Stay on the page — the job table will show the new row.
		} catch (e) {
			const dryRun = e?.detail?.quote?.dry_run ?? quote?.dry_run;
			const state = derivePaymentState(e, dryRun);
			if (state !== PAYMENT_STATUS.NONE) {
				setPaymentStatus(state);
				setPaymentMessage(paymentErrorMessage(e));
			} else {
				setStartError(e.message || "Failed to start generation");
			}
		} finally {
			setStarting(false);
		}
	};

	// The PAYMENTS_DRY_RUN continuation: any non-empty Payment-Signature is
	// accepted UNVERIFIED, so this actually completes the job — honestly
	// labeled, never dressed up as a real settlement. The payer named is the
	// LINKED wallet (server truth — what #1296's payer binding will require
	// when signing becomes real, #1298), falling back to the active browser
	// wallet. Reaching a 402 proves the SERVER-side link exists (the 409 gate
	// ran first), but NOT that the browser wallet is connected or that the
	// linked-wallets fetch succeeded — so the none-available case is handled
	// here as UX, never shipped as an empty payer.
	const continueInTestMode = async () => {
		const payer = resolveDryRunPayer(linkedWallets, walletAddr);
		if (!payer) {
			setPaymentMessage(
				"Could not resolve your linked wallet (reconnect your wallet or reload). No payment was attempted.",
			);
			return;
		}
		setContinuingTestMode(true);
		try {
			await submitStart({ "Payment-Signature": buildDryRunPaymentHeader(payer) });
			setPaymentStatus(PAYMENT_STATUS.NONE);
			setPaymentMessage("");
			setTestModeNotice(true);
			if (GENERATION_QUOTE_ENABLED) fetchQuote();
		} catch (e) {
			setPaymentMessage(paymentErrorMessage(e, "Test-mode continue failed — try again."));
		} finally {
			setContinuingTestMode(false);
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

					{/* ── Upfront cost quote (feature-flagged, ratified #1296 shape) ── */}
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
										<span className="tag tag-accent">
											{quoteView.price} {quoteView.asset}
										</span>
										{quoteView.paymentRequired ? (
											<span className="tag tag-info">testnet USDC</span>
										) : (
											<span className="tag tag-muted">payment not required yet</span>
										)}
										{quoteView.paymentRequired && quoteView.dryRun && (
											<span className="tag tag-warning">
												test mode — payment accepted unverified
											</span>
										)}
									</div>
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
									{quoteView.paymentRequired && payerMismatch && (
										<p
											className="caption mb-0"
											style={{ marginTop: 6, color: "var(--text-3)" }}
										>
											Payments must be signed by your linked wallet (
											{shortAddr(payerMismatch.linked)}) — your connected wallet
											({shortAddr(payerMismatch.active)}) isn't linked yet.
										</p>
									)}
								</div>
							)}
						</div>
					)}

					{/* Submit row */}
					<div
						className="flex items-center justify-between flex-wrap gap-2"
						style={{ marginTop: 2 }}
					>
						{paymentStatus === PAYMENT_STATUS.WALLET_LINK_REQUIRED ? (
							<div className="info-box warning" style={{ flex: 1 }}>
								<strong>Wallet link required.</strong> {paymentMessage}
								{payerMismatch && (
									<p
										className="caption mb-0"
										style={{ marginTop: 6, color: "var(--text-3)" }}
									>
										Your connected wallet ({shortAddr(payerMismatch.active)})
										isn't linked to your account — your linked wallet is{" "}
										{shortAddr(payerMismatch.linked)}. Switch your wallet's
										active account to that one, or link the connected one below.
									</p>
								)}{" "}
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
									Connect / link wallet →
								</button>
							</div>
						) : paymentStatus === PAYMENT_STATUS.DRY_RUN ? (
							<div className="info-box warning" style={{ flex: 1 }}>
								<strong>Test mode.</strong> {paymentMessage} This backend
								accepts payments unverified right now — nothing real is
								charged.{" "}
								<button
									type="button"
									onClick={continueInTestMode}
									disabled={continuingTestMode}
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
									{continuingTestMode ? "Continuing…" : "Continue in test mode →"}
								</button>
							</div>
						) : paymentStatus === PAYMENT_STATUS.LIVE_UNAVAILABLE ? (
							<div className="info-box warning" style={{ flex: 1 }}>
								<strong>Payments aren't enabled in this build yet.</strong>{" "}
								{paymentMessage}
							</div>
						) : startError ? (
							<div className="info-box warning" style={{ flex: 1 }}>
								{startError}
							</div>
						) : testModeNotice ? (
							<div className="info-box" style={{ flex: 1 }}>
								<span className="tag tag-warning" style={{ marginRight: 6 }}>
									test mode
								</span>
								Payment accepted unverified — no real charge.
							</div>
						) : receipt ? (
							<div className="info-box" style={{ flex: 1 }}>
								<span className="tag tag-positive" style={{ marginRight: 6 }}>
									paid
								</span>
								Settlement receipt: <code>{receipt}</code>
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
