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
import { depositToGateway, getGatewayBalance, parseUsdcAmount, signGatewayPayment, walletSupportsPayment } from "../x402";
import {
	DEPTH_OPTIONS,
	PAYMENT_STATUS,
	deriveQuoteView,
	derivePaymentRequirements,
	derivePaymentState,
	describePayerMismatch,
	extractPaymentRequiredHeader,
	extractReceipt,
	paymentErrorMessage,
	startErrorMessage,
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
// in featureFlags.js — ON by default since Dan's 2026-08-19 directive):
// when on, a quote is fetched from the PUBLIC GET /api/generate/quote and
// shown before the submit button. /start's request body is unchanged — the
// ratified contract (docs/specs/generation-quote-contract.md, #1296) carries
// no quote_id or any other payment field on the body; the gate lives
// entirely in status codes: a 409 means "link + fund a wallet first" (CTA
// reuses the existing open-wallet-modal flow), a 402 means "sign and retry
// with Payment-Signature".
//
// The 402 path handling works REGARDLESS of GENERATION_QUOTE_ENABLED — a
// live paywall can 402 an unquoted submit just as easily as a quoted one, so
// the payment step below reads its own price/wallet info from the 402's
// body/headers rather than assuming the upfront quote ran.
//
// Real x402 signing IS wired (../x402.js): the 402's PAYMENT-REQUIRED
// header is parsed (../generateQuote.js's derivePaymentRequirements) into
// EIP-712 payment requirements; if the connected Gateway balance is short,
// a two-transaction approve+deposit flow funds it; then the connected
// wallet signs a TransferWithAuthorization and /start is retried with the
// resulting Payment-Signature header. A real signed header is ALSO accepted
// under the backend's PAYMENTS_DRY_RUN (unverified, unsettled — never
// dressed up as a real payment), so this one flow works whether the backend
// is live or still in dry-run. A settlement receipt (PAYMENT-RESPONSE
// header) is surfaced when present; when a retry succeeds WITHOUT one, the
// honest "accepted without settlement" notice renders instead — this UI
// never claims a payment succeeded without a receipt. State-transition
// logic and the x402 wire-format parsing/building live in
// ../generateQuote.js so they're unit-testable without a DOM or a wallet.

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
	// The account's linked wallets (GET /api/wallets) — fetched once a payment
	// step is actually active (a 409/402 landed), regardless of
	// GENERATION_QUOTE_ENABLED, so the 402 handling path works whether or not
	// the upfront quote ran. Used for the payer-binding transparency note
	// (describePayerMismatch) — real signing always uses the CONNECTED wallet
	// (walletAddr) as the payer, since that's the only address this UI can
	// actually produce a signature for; a linked wallet the user isn't
	// connected as can only be flagged, never silently substituted.
	const [linkedWallets, setLinkedWallets] = useState([]);
	// The payment-step banner. Set only from a genuine 409/402 on /start
	// (derivePaymentState); cleared on every fresh submit attempt so a stale
	// banner never survives a retry.
	const [paymentStatus, setPaymentStatus] = useState(PAYMENT_STATUS.NONE);
	const [paymentMessage, setPaymentMessage] = useState("");
	// The parsed PAYMENT-REQUIRED requirements from the active 402 (or an
	// error state if the header was missing/malformed/had no supported
	// option) — see ../generateQuote.js's derivePaymentRequirements. Null
	// until a 402 lands.
	const [paymentRequirements, setPaymentRequirements] = useState(null);
	// The 402 body's OWN quote ({price, asset, chain, dry_run, ...}) —
	// captured independently of the upfront GET /api/generate/quote fetch so
	// the payment step can show a price even when GENERATION_QUOTE_ENABLED
	// never fetched one.
	const [paywallQuoteRaw, setPaywallQuoteRaw] = useState(null);
	// Gateway balance (raw base-unit bigint) for the connected wallet against
	// the active requirement's asset. Null while unknown/checking.
	const [gatewayBalance, setGatewayBalance] = useState(null);
	const [checkingBalance, setCheckingBalance] = useState(false);
	// The approve+deposit funding flow — one faucet drip (20 USDC) funds
	// exactly 10 generations at the $2.00 default price, hence the default.
	const [depositAmount, setDepositAmount] = useState("20.00");
	const [depositStep, setDepositStep] = useState("idle"); // idle | approving | approved | depositing | deposited | error
	const [depositError, setDepositError] = useState("");
	const [paying, setPaying] = useState(false);
	// True only right after a /start retry succeeded WITHOUT a PAYMENT-RESPONSE
	// settlement receipt (PAYMENTS_DRY_RUN on the backend: a real signed
	// payment header is accepted UNVERIFIED and UNSETTLED). The honest label
	// the ratified contract requires — never dressed up as a real settlement.
	const [noSettlementNotice, setNoSettlementNotice] = useState(false);
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

	// Payment step active: a genuine 402 landed (dry-run or live — both need
	// the real payment flow now that signing is wired). Computed once here
	// so the linked-wallets fetch, the Gateway-balance fetch, and the render
	// branch below all agree on the same condition.
	const paymentActive =
		paymentStatus === PAYMENT_STATUS.DRY_RUN || paymentStatus === PAYMENT_STATUS.LIVE_UNAVAILABLE;

	// Linked wallets: needed once EITHER the upfront quote reports payments
	// required OR a 402 actually landed — the latter matters independently of
	// GENERATION_QUOTE_ENABLED, since a live paywall can 402 an unquoted
	// submit just as easily as a quoted one, and the payer-mismatch note must
	// still work in that path.
	useEffect(() => {
		if (!paymentActive && !(GENERATION_QUOTE_ENABLED && quote?.payment_required)) return;
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
	}, [paymentActive, quote?.payment_required]);

	const quoteView = useMemo(() => deriveQuoteView(quote), [quote]);
	// The price/asset/chain/dry_run to show in the payment step: the upfront
	// quote when it ran, else the 402 body's OWN quote — so the payment step
	// shows a real price regardless of GENERATION_QUOTE_ENABLED. Both
	// useMemo calls run unconditionally (Rules of Hooks) — only the
	// resulting VALUES are chosen with `??`.
	const paywallQuoteView = useMemo(() => deriveQuoteView(paywallQuoteRaw), [paywallQuoteRaw]);
	const effectiveQuoteView = quoteView ?? paywallQuoteView;
	const quoteReady = !GENERATION_QUOTE_ENABLED || (quoteStatus === "ready" && Boolean(quoteView));
	// The wallet actually active in the injected provider vs. the account's
	// LINKED wallet — real signing uses the CONNECTED wallet (it's the only
	// one this UI can produce a signature for), and the backend requires that
	// to equal the linked wallet, so a mismatch here blocks payment rather
	// than silently substituting. Null when there's nothing to flag (see
	// describePayerMismatch's own doc for the exact conditions).
	const payerMismatch = useMemo(
		() => describePayerMismatch(walletAddr, linkedWallets),
		[walletAddr, linkedWallets],
	);

	const requiredAmountRaw = useMemo(() => {
		const amount = paymentRequirements?.requirements?.amount;
		try {
			return amount != null ? BigInt(amount) : null;
		} catch {
			return null;
		}
	}, [paymentRequirements]);
	const needsDeposit =
		requiredAmountRaw != null && gatewayBalance != null && gatewayBalance < requiredAmountRaw;

	// Fetch the connected wallet's Gateway balance once a payable requirement
	// is active and the payer is unambiguous (connected + linked agree).
	useEffect(() => {
		const requirements = paymentRequirements?.requirements;
		if (!requirements || !walletAddr || payerMismatch) {
			setGatewayBalance(null);
			return;
		}
		let cancelled = false;
		setCheckingBalance(true);
		getGatewayBalance(requirements, walletAddr)
			.then((bal) => {
				if (!cancelled) setGatewayBalance(bal);
			})
			.finally(() => {
				if (!cancelled) setCheckingBalance(false);
			});
		return () => {
			cancelled = true;
		};
	}, [paymentRequirements, walletAddr, payerMismatch]);

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
	// Returns the receipt too (not just via the `receipt` state setter) so a
	// caller mid-async-flow (handlePay) can branch on it immediately rather
	// than reading stale state before the next render.
	const submitStart = async (extraHeaders) => {
		const { data, headers } = await apiPostWithMeta(
			"/api/generate/start",
			buildBrief(),
			extraHeaders,
		);
		setLastJobId(data.job_id);
		const settledReceipt = extractReceipt(headers);
		setReceipt(settledReceipt);
		return { data, receipt: settledReceipt };
	};

	// Reset every payment-step-local piece of state — shared by a fresh
	// submit attempt and a successful payment, so neither leaves stale
	// deposit/balance/error state behind for the next 402.
	const resetPaymentStepState = () => {
		setPaymentStatus(PAYMENT_STATUS.NONE);
		setPaymentMessage("");
		setPaymentRequirements(null);
		setPaywallQuoteRaw(null);
		setGatewayBalance(null);
		setDepositStep("idle");
		setDepositError("");
		setNoSettlementNotice(false);
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
		resetPaymentStepState();
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
				setPaywallQuoteRaw(e?.detail?.quote ?? null);
				if (state === PAYMENT_STATUS.DRY_RUN || state === PAYMENT_STATUS.LIVE_UNAVAILABLE) {
					setPaymentRequirements(derivePaymentRequirements(extractPaymentRequiredHeader(e.headers)));
				}
			} else {
				setStartError(startErrorMessage(e, "Failed to start generation"));
			}
		} finally {
			setStarting(false);
		}
	};

	// ── Real payment: fund the Gateway balance (approve + deposit) ──
	const handleDeposit = async () => {
		const requirements = paymentRequirements?.requirements;
		if (!requirements) return;
		setDepositError("");
		try {
			const amountRaw = parseUsdcAmount(depositAmount);
			await depositToGateway(requirements, amountRaw, setDepositStep);
			const bal = await getGatewayBalance(requirements, walletAddr);
			setGatewayBalance(bal);
		} catch (err) {
			setDepositStep("error");
			setDepositError(err.shortMessage || err.message || "Deposit failed — try again.");
		}
	};

	// ── Real payment: sign the EIP-712 authorization and retry /start ──
	// Reaching this point means: a 402 landed, requirements parsed, the
	// connected wallet equals the linked wallet (no payerMismatch), and the
	// Gateway balance already covers the amount. If the response carries a
	// PAYMENT-RESPONSE settlement receipt, submitStart's `receipt` state
	// surfaces it; if it doesn't (PAYMENTS_DRY_RUN), the honest
	// "accepted without settlement" notice renders instead — this UI never
	// claims a payment succeeded without a receipt.
	const handlePay = async () => {
		const requirements = paymentRequirements?.requirements;
		if (!requirements) return;
		setPaying(true);
		setPaymentMessage("");
		try {
			const header = await signGatewayPayment({
				requirements,
				resource: paymentRequirements.resource,
				x402Version: paymentRequirements.x402Version,
				payerAddress: walletAddr,
			});
			const { receipt: settledReceipt } = await submitStart({ "Payment-Signature": header });
			resetPaymentStepState();
			setNoSettlementNotice(!settledReceipt);
			if (GENERATION_QUOTE_ENABLED) fetchQuote();
		} catch (e) {
			setPaymentMessage(paymentErrorMessage(e, e?.message || "Payment failed — try again."));
		} finally {
			setPaying(false);
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
					{/* Real <label htmlFor> associations. These three fields — the
					    strategy name, the brief, and the asset search below — were
					    labelled by plain <div>s and placeholders only, so a screen
					    reader announced the product's single most important control
					    as an anonymous "edit, multiline" the moment the user typed
					    the first character (3.3.2 / 4.1.2). */}
					<div className="mb-3">
						<label className="label mb-1 block" htmlFor="generate-strategy-name">
							Strategy name (optional)
						</label>
						<input
							id="generate-strategy-name"
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

					<label className="label mb-1 block" htmlFor="generate-brief">
						Your brief
					</label>
					<p
						className="caption mb-2"
						id="generate-brief-help"
						style={{ color: "var(--text-3)" }}
					>
						A good brief names concrete assets or classes, a mechanism (momentum
						/ vol-managed / hedge / mean-reversion), and a goal.
					</p>

					<textarea
						id="generate-brief"
						aria-describedby="generate-brief-help"
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
											title="How many papers the engine considers"
										>
											{DEPTH_OPTIONS.map((n) => (
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
										aria-label="Search assets"
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
										{/* Each ticker is a real toggle button: as a click-only
										    <span> the universe picker had no tab stop and no
										    role, so a keyboard user could clear the selection
										    (the "Clear" affordance is a real button) but never
										    set one (2.1.1 / 4.1.2). */}
										{filteredAssetGroups.map((group) => (
											<div key={group.id} className="mb-2">
												<div
													className="caption mb-1"
													style={{ color: "var(--text-3)" }}
												>
													{group.label}
												</div>
												<div
													className="flex gap-1.5 flex-wrap"
													role="group"
													aria-label={`${group.label} assets`}
												>
													{group.assets.map((a) => (
														<button
															key={a}
															type="button"
															className={`tag ${selectedAssets.includes(a) ? "tag-accent" : "tag-muted"} cursor-pointer`}
															aria-pressed={selectedAssets.includes(a)}
															onClick={() => toggleAsset(a)}
														>
															{a}
														</button>
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
						{/* Every outcome of pressing Generate — start failure, quote not
						    ready, wallet-link required, test mode, payments unavailable,
						    and the success receipt — paints into this one slot. None of
						    it was announced: the user heard the button label revert from
						    "Starting…" and was never told whether the job started or why
						    it failed (4.1.3). The live container is mounted
						    unconditionally, because a live region added at the same
						    moment as its text is usually missed by assistive tech. */}
						<div
							className="generate-submit-status"
							role="status"
							aria-live="polite"
							aria-atomic="true"
							style={{ flex: 1, display: "flex" }}
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
						) : paymentStatus === PAYMENT_STATUS.DRY_RUN || paymentStatus === PAYMENT_STATUS.LIVE_UNAVAILABLE ? (
							<div className="info-box warning" style={{ flex: 1 }}>
								<strong>Payment required.</strong>{" "}
								{effectiveQuoteView && (
									<>
										Generating costs{" "}
										<strong>
											{effectiveQuoteView.price} {effectiveQuoteView.asset}
										</strong>{" "}
										on {effectiveQuoteView.chain}
										{effectiveQuoteView.recipient ? <> to {shortAddr(effectiveQuoteView.recipient)}</> : null}
										{walletAddr ? <> — paid from your connected wallet ({shortAddr(walletAddr)})</> : null}.{" "}
									</>
								)}
								{effectiveQuoteView?.dryRun && (
									<span className="tag tag-warning" style={{ marginRight: 6 }}>
										test mode — server accepts payments unverified
									</span>
								)}
								{paymentRequirements?.error ? (
									<p className="caption mb-0" style={{ marginTop: 6 }}>
										{paymentRequirements.error === "no_gateway_option"
											? "This backend didn't offer a supported payment method (Circle Gateway) for this request — contact the team."
											: "Could not read the payment requirements from the server response. Try again."}
									</p>
								) : !walletAddr ? (
									<p className="caption mb-0" style={{ marginTop: 6 }}>
										Connect your wallet to pay.{" "}
										<button
											type="button"
											onClick={() => window.dispatchEvent(new Event("open-wallet-modal"))}
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
									</p>
								) : payerMismatch ? (
									<p className="caption mb-0" style={{ marginTop: 6 }}>
										Payments must be signed by your linked wallet (
										{shortAddr(payerMismatch.linked)}) — switch your connected wallet (
										{shortAddr(payerMismatch.active)}) to that one, or link the connected
										one below.
									</p>
								) : !walletSupportsPayment() ? (
									<p className="caption mb-0" style={{ marginTop: 6 }}>
										Pay with a browser wallet (e.g. MetaMask) — this wallet type isn't
										supported for payments yet.
									</p>
								) : checkingBalance ? (
									<p className="caption mb-0" style={{ marginTop: 6 }}>
										Checking your Gateway balance…
									</p>
								) : needsDeposit ? (
									<div style={{ marginTop: 6 }}>
										<p className="caption mb-1">
											Your Circle Gateway balance is below the {effectiveQuoteView?.price ?? "generation"}{" "}
											price. Two on-chain steps fund it: approve USDC, then deposit into
											your Gateway balance. A default of 20.00 USDC funds exactly 10 ×
											$2.00 generations from one faucet drip.
										</p>
										<input
											type="text"
											value={depositAmount}
											onChange={(e) => setDepositAmount(e.target.value)}
											aria-label="Deposit amount (USDC)"
											className="chat-input"
											style={{ width: 110, marginRight: 8 }}
											disabled={depositStep === "approving" || depositStep === "depositing"}
										/>
										<button
											type="button"
											className="btn btn-outline btn-sm"
											onClick={handleDeposit}
											disabled={depositStep === "approving" || depositStep === "depositing"}
										>
											{depositStep === "approving"
												? "Approving USDC…"
												: depositStep === "depositing"
													? "Depositing…"
													: depositStep === "deposited"
														? "Deposited ✓"
														: "Approve & deposit →"}
										</button>
										{depositError && (
											<p
												className="caption"
												style={{ color: "var(--negative, #ef4444)", marginTop: 4 }}
											>
												{depositError}
											</p>
										)}
									</div>
								) : (
									<button
										type="button"
										className="btn btn-primary btn-sm"
										onClick={handlePay}
										disabled={paying}
										style={{ marginTop: 6 }}
									>
										{paying ? "Signing & paying…" : `Pay ${effectiveQuoteView?.price ?? ""} & generate →`}
									</button>
								)}
								{paymentMessage && (
									<p className="caption mb-0" style={{ marginTop: 6 }}>
										{paymentMessage}
									</p>
								)}
							</div>
						) : startError ? (
							<div className="info-box warning" style={{ flex: 1 }}>
								{startError}
							</div>
						) : noSettlementNotice ? (
							<div className="info-box" style={{ flex: 1 }}>
								<span className="tag tag-warning" style={{ marginRight: 6 }}>
									test mode
								</span>
								Accepted without settlement (server test mode) — no funds moved.
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
						</div>
						<button
							className="btn btn-primary"
							onClick={startJob}
							disabled={starting || !intent.trim() || !quoteReady}
							aria-describedby={
								!intent.trim() ? "generate-submit-hint" : undefined
							}
						>
							{starting
								? "Starting…"
								: GENERATION_QUOTE_ENABLED
									? "Approve & Generate →"
									: "Generate →"}
						</button>
					</div>
					{/* The submit button is disabled until a brief exists; state the
					    cause rather than leaving the form looking stuck (3.3.1). */}
					{!intent.trim() && (
						<p
							id="generate-submit-hint"
							className="caption mt-2 mb-0"
							style={{ color: "var(--text-3)" }}
						>
							Enter a brief above to enable generation.
						</p>
					)}
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
