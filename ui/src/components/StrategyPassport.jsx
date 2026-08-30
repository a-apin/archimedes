import { useEffect, useState } from "react";
import CreateVaultModal from "./CreateVaultModal";
import { ROADMAP_SURFACES_ENABLED } from "../featureFlags.js";
import RigorStrictnessControl, { levelLabel } from "./RigorStrictnessControl";
import { apiGet, apiPostWithMeta } from "../api";
import { useRigorStrictness, BADGE_LEVEL } from "../hooks/useRigorStrictness";
import {
	NOT_MEASURED,
	NOT_MEASURED_HINT,
	deriveGenerationCostView,
	formatDuration,
	formatTokenCount,
	quoteLabel,
	quoteNote,
	stageLabel,
	tokensLabel,
	usageNote,
} from "../generationCost.js";
import {
	isUnknownRigorGateStatus,
	warnUnknownRigorGateStatus,
	UNKNOWN_RIGOR_LABEL,
	UNKNOWN_RIGOR_TITLE,
} from "../rigorGateStatus.js";

const API_BASE = import.meta.env.VITE_API_BASE ?? "";

// /strategy/:id deep-link route. Renders the full passport per
// docs/specs/strategy-passport-spec.md — bigger, scrollable version of the row
// expansion that lives on /library. Includes the Deploy CTA that opens
// CreateVaultModal (Phase 4 scaffold).
//
// Reachable from: Library row title click (deep-link), Reasoning trace
// "→ Strategy in Library" follow-back, FusionResult "Open in Library →" CTA.

function fmt(v, digits = 2) {
	if (v == null || !Number.isFinite(v)) return "—";
	return Number(v).toFixed(digits);
}

function fmtPct(v) {
	if (v == null || !Number.isFinite(v)) return "—";
	return `${(Number(v) * 100).toFixed(1)}%`;
}

function statusTag(status, passesRigor) {
	// A "live" admin status combined with a failed rigor verdict shouldn't
	// render green — the rigor verdict is the truthful signal. Match the
	// Strategies.jsx pill rule (Issue #387) so the passport doesn't
	// contradict the library page.
	if (status === "live" && passesRigor === false) return "tag-muted";
	if (status === "validated" || status === "live") return "tag-positive";
	if (status === "rejected" || status === "retired") return "tag-muted";
	return "tag-accent";
}

function statusLabel(status, passesRigor) {
	if (status === "live" && passesRigor === false)
		return "Reference only — gate failed";
	return (
		(status || "candidate").charAt(0).toUpperCase() +
		(status || "candidate").slice(1)
	);
}

// Derive a brief-specific display title. The unified passport table doesn't
// persist `strategy_name`, but Pi's #336 fix ensures methodology_summary
// always starts with "For brief 'XXX': YYY" — extract XXX as the title so
// each generated strategy reads differently. Falls back to paper title for
// legacy strategies and curated ones (whose methodology_summary doesn't
// follow that template).
function deriveDisplayTitle(s) {
	const m = (s.methodology_summary || "").match(
		/^For brief ['"](.+?)['"]\s*:/i,
	);
	if (m && m[1]) return m[1].trim();
	return s.paper_title || s.id;
}

function regimeChip(tag) {
	if (tag === "bull") {
		return {
			label: (
				<>
					<span className="i-lucide-trending-up w-3.5 h-3.5" /> Bull regime
				</>
			),
			cls: "tag-positive",
		};
	}
	if (tag === "bear") {
		return {
			label: (
				<>
					<span className="i-lucide-trending-down w-3.5 h-3.5" /> Bear regime
				</>
			),
			cls: "tag-negative",
		};
	}
	return null;
}

// Return-source classification (T2.5) — the dominant economic source of the
// strategy's return. Maps the backend enum to a human label + tag colour. A
// durable, compensated source (risk_premium / productive_growth) reads neutral;
// mispricing reads accent (decays as it crowds); noise reads muted (no source).
const RETURN_SOURCE_META = {
	risk_premium: { label: "Risk premium", cls: "tag-accent" },
	mispricing: { label: "Mispricing", cls: "tag-accent" },
	productive_growth: { label: "Productive growth", cls: "tag-positive" },
	noise: { label: "Noise", cls: "tag-muted" },
};

function returnSourceChip(src) {
	return RETURN_SOURCE_META[src] || RETURN_SOURCE_META.noise;
}

export default function StrategyPassport({
	strategyId,
	onNavigate,
	walletAddr,
	user = null,
}) {
	const [strategy, setStrategy] = useState(null);
	const [loading, setLoading] = useState(true);
	const [error, setError] = useState("");
	const [deployOpen, setDeployOpen] = useState(false);
	// Per-user deploy strictness (localStorage-backed, tab-synced). Gates the
	// Deploy CTA and is passed to the deploy flow so the server enforces it too.
	const [level, setLevel] = useRigorStrictness();
	// The strategy's rigor-ladder verdict: min_passing_level + blocked_by_floor,
	// computed live over the whole-library cohort. Curated strategies resolve here;
	// generated strategies 404 → we fall back to the badge boolean.
	const [gate, setGate] = useState(null);

	useEffect(() => {
		let cancelled = false;
		if (!strategyId) {
			setError("No strategy id in URL.");
			setLoading(false);
			return;
		}
		fetch(`${API_BASE}/api/strategies/${encodeURIComponent(strategyId)}`)
			.then((r) =>
				r.ok
					? r.json()
					: r.text().then((t) => {
							throw new Error(t || r.statusText);
						}),
			)
			.then((data) => {
				if (!cancelled) setStrategy(data);
			})
			.catch((e) => {
				if (!cancelled) setError(e.message || "Failed to load strategy");
			})
			.finally(() => {
				if (!cancelled) setLoading(false);
			});
		return () => {
			cancelled = true;
		};
	}, [strategyId]);

	useEffect(() => {
		let cancelled = false;
		setGate(null);
		if (!strategyId) return;
		// min_passing_level is strictness-independent, so one call (default level)
		// gives the whole ladder. 404 (generated strategy not in the curated cohort)
		// is expected — we fall back to the badge boolean below.
		fetch(
			`${API_BASE}/api/selection-bias/gate/${encodeURIComponent(strategyId)}`,
		)
			.then((r) => (r.ok ? r.json() : null))
			.then((data) => {
				if (!cancelled && data) setGate(data);
			})
			.catch(() => {
				/* fall back to badge */
			});
		return () => {
			cancelled = true;
		};
	}, [strategyId]);

	// Exhaustiveness alarm for rigor_gate_status, shared with Strategies.jsx so
	// both surfaces answer a fifth state identically (#1358). Declared up here,
	// above the `loading` / `error` early returns below, because a hook after a
	// conditional return is a hook that sometimes does not run.
	const unknownRigor = isUnknownRigorGateStatus(strategy?.rigor_gate_status);
	useEffect(() => {
		if (unknownRigor)
			warnUnknownRigorGateStatus(
				strategy?.rigor_gate_status,
				"StrategyPassport",
			);
	}, [unknownRigor, strategy?.rigor_gate_status]);

	if (loading) return <div className="caption">Loading strategy passport…</div>;

	if (error || !strategy) {
		return (
			<div className="max-w-[640px]">
				<button
					className="btn btn-outline btn-sm mb-3"
					onClick={() => onNavigate("library")}
				>
					← Back to Library
				</button>
				<div className="info-box warning">
					Could not load strategy: {error || "unknown error"}
				</div>
			</div>
		);
	}

	const s = strategy;
	const passingRigor = s.passes_rigor_gate === true;
	// #1358: rigor_gate_status carries the honest four-state badge
	// ("pass"|"fail"|"pending"|"degenerate") the API has served since #1184 —
	// nothing under ui/src/ read it before this fix, so every strategy with
	// zero statistics computed (rigor_gate_status === "pending") rendered as
	// though it had FAILED the gate (passes_rigor_gate is false for pending
	// too, by fail-closed design). Checked before any passes_rigor_gate
	// branch below so "never evaluated" can't be mistaken for "evaluated and
	// lost".
	const rigorPending = s.rigor_gate_status === "pending";
	// #1358 round-3: the fourth state. Same NEUTRAL treatment as pending — never
	// the "not Verified" / "not passed" wording, because nothing was measurable
	// to fail — but its own sentence, since a degenerate row HAS persisted
	// returns and pending's "not yet evaluated" would be a fresh false claim.
	// Copy follows chat_service.py:395, which already tells the LLM exactly this.
	const rigorDegenerate = s.rigor_gate_status === "degenerate";
	// ── Deploy gating at the user's chosen strictness ──
	const badgePass = s.passes_rigor_gate === true;
	// Lowest level (1..5) at which this strategy is deployable — from the live
	// ladder when loaded, else the level-1 badge boolean (curated strategies load
	// the ladder; generated strategies fall back to the badge). null = deployable
	// at no level; undefined = unknown yet (pending backtest).
	const minLevel = gate
		? gate.min_passing_level
		: badgePass
			? BADGE_LEVEL
			: s.passes_rigor_gate === false
				? null
				: undefined;
	// #1358 round-3: a zero-variance series leaves dsr_p_value and oos_sharpe at
	// None, and RigorGateResult.blocked_by_floor treats a None on either as a
	// floor failure — so the gate returns blocked_by_floor=true for a strategy
	// no floor ever measured. Every consumer of blockedByFloor below says "fails
	// an always-on correctness floor", which is an assertion about a measurement
	// that did not happen. Suppress that claim here (once, so all five sites
	// agree) and let the degenerate sentence beside it carry the honest reason.
	//
	// This loosens no permission: `deployable` additionally requires
	// `minLevel != null`, and min_passing_level is null for a degenerate row, so
	// the deploy button stays disabled either way. Only the EXPLANATION changes.
	const gateDegenerate = gate ? gate.degenerate === true : false;
	const blockedByFloor = gate
		? gate.blocked_by_floor === true && !gateDegenerate
		: false;
	const deployable = minLevel != null && minLevel <= level && !blockedByFloor;
	const needsHigherLevel =
		minLevel != null && minLevel > level && !blockedByFloor;
	const belowBadge = deployable && minLevel > BADGE_LEVEL;
	const paperCite = [
		s.paper_authors?.[0]?.split(" ").pop(),
		s.paper_year && `(${s.paper_year})`,
	]
		.filter(Boolean)
		.join(" ");
	const displayTitle = deriveDisplayTitle(s);
	const regime = regimeChip(s.regime_tag);
	// Show the anchor-paper title as a sub-line only when the derived title is
	// brief-specific (i.e. we did extract it from methodology_summary) AND a
	// paper title exists. Otherwise the sub-line would duplicate the heading.
	const paperAnchorLine =
		displayTitle !== s.paper_title && s.paper_title
			? `Anchored on: ${s.paper_title}`
			: null;

	return (
		<div className="passport-page">
			<button
				className="btn btn-outline btn-sm app-back-link"
				onClick={() => onNavigate("library")}
			>
				← Back to Library
			</button>

			{/* Header */}
			<header className="app-page-heading passport-heading fade-up fade-up-1">
				<p className="app-eyebrow">Strategy passport</p>
				<h1>{displayTitle}</h1>
				{paperAnchorLine && (
					<div className="caption mb-1" style={{ color: "var(--text-3)" }}>
						{paperAnchorLine}
					</div>
				)}
				<div className="caption mb-3">
					{paperCite || (s.paper_year ? `(${s.paper_year})` : "")}
					{s.paper_venue && <> · {s.paper_venue}</>}
				</div>
				<div className="flex gap-2 items-center flex-wrap">
					{regime && (
						<span className={`tag ${regime.cls}`}>{regime.label}</span>
					)}
					<span className={`tag ${statusTag(s.status, s.passes_rigor_gate)}`}>
						{statusLabel(s.status, s.passes_rigor_gate)}
					</span>
					{unknownRigor ? (
						<span className="tag tag-muted" title={UNKNOWN_RIGOR_TITLE}>
							rigor gate {UNKNOWN_RIGOR_LABEL}
						</span>
					) : rigorDegenerate ? (
						<span
							className="tag tag-muted"
							title="DEGENERATE — the persisted return series is zero-variance (broken data or a zero-trade backtest), not a real evaluation"
						>
							rigor gate unevaluable
						</span>
					) : rigorPending ? (
						<span className="tag tag-muted">rigor gate pending</span>
					) : s.passes_rigor_gate === true ? (
						<span className="tag tag-positive inline-flex items-center gap-1">
							<span className="i-lucide-check w-3.5 h-3.5" /> rigor gate passed
						</span>
					) : (
						s.passes_rigor_gate === false && (
							<span className="tag tag-muted">rigor gate not passed</span>
						)
					)}
					{(s.papers || []).length > 1 ? (
						<span className="tag tag-accent inline-flex items-center gap-1">
							<span className="i-lucide-layers w-3.5 h-3.5" />
							{s.papers.length} fused papers
						</span>
					) : (
						s.paper_arxiv_id && (
							<a
								href={`https://arxiv.org/abs/${s.paper_arxiv_id}`}
								target="_blank"
								rel="noreferrer"
								className="tag tag-muted"
								style={{
									fontFamily: "var(--mono, monospace)",
									fontSize: "0.75rem",
								}}
							>
								arxiv:{s.paper_arxiv_id} ↗
							</a>
						)
					)}
				</div>
			</header>

			<div className="passport-workspace">
				<aside className="passport-authority">
					{/* Deploy CTA — top, gated on rigor-at-your-level + wallet. The card
					    leads into the vault surface, so it hides with it (#1266). */}
					{ROADMAP_SURFACES_ENABLED && (
					<div className="card passport-deploy fade-up fade-up-2">
						<div className="flex-1 min-w-[240px]">
							<div className="label mb-1">Deploy as a vault</div>
							<p className="caption leading-relaxed">
								Time-bound, non-custodial execution. Funds stay in an ERC-4626
								vault you control; the agent has rebalance authority only, no
								withdraw.
								{!walletAddr && (
									<> Connect a wallet (top right) to enable deployment.</>
								)}
								{blockedByFloor && (
									<>
										{" "}
										This strategy fails an always-on correctness floor
										(look-ahead / positive OOS / DSR ≥ 0.50) — it cannot be
										deployed at any strictness level.
									</>
								)}
								{gateDegenerate && (
									<>
										{" "}
										This strategy's persisted return series is zero-variance
										(broken data or a zero-trade backtest), so the rigor gate had
										nothing to measure — it was not graded and lost, it was never
										gradeable. It cannot be deployed until a real backtest
										replaces that series.
									</>
								)}
								{needsHigherLevel && (
									<>
										{" "}
										This strategy passes only at{" "}
										<strong>{levelLabel(null, minLevel)}</strong> (level{" "}
										{minLevel}) or riskier. Raise your strictness to deploy it.
									</>
								)}
								{belowBadge && !needsHigherLevel && (
									<>
										{" "}
										Deploying below the Archimedes Verified bar, at{" "}
										<strong>{levelLabel(null, level)}</strong> risk.
									</>
								)}
							</p>
						</div>
						<div className="flex flex-col items-end gap-2">
							<button
								className="btn btn-primary"
								onClick={() => setDeployOpen(true)}
								disabled={!walletAddr || !deployable}
								style={
									!walletAddr || !deployable
										? {
												opacity: 0.45,
												cursor: "not-allowed",
												filter: "grayscale(0.6)",
											}
										: undefined
								}
								title={
									!walletAddr
										? "Connect wallet to deploy"
										: gateDegenerate
											? "Zero-variance persisted return series — nothing for the rigor gate to measure; not a graded failure"
											: blockedByFloor
												? "Fails an always-on rigor floor — cannot deploy at any level"
												: needsHigherLevel
													? `Raise strictness to level ${minLevel} to deploy`
													: "Open deploy modal"
								}
							>
								Deploy as Vault →
							</button>
							{needsHigherLevel && (
								<button
									className="btn btn-outline btn-sm"
									onClick={() => setLevel(minLevel)}
									title={`Set your strictness to ${levelLabel(null, minLevel)}`}
								>
									Raise to {levelLabel(null, minLevel)} →
								</button>
							)}
						</div>
					</div>
					)}

					{/* Per-user strictness slider — the deploy gate above reads from this. */}
					<div className="passport-strictness fade-up fade-up-2">
						<RigorStrictnessControl level={level} onChange={setLevel} />
					</div>

					{/* Paper trading — the MVP act-on step. Account-owned and simulated
					    (POST /api/paper/deployments): no wallet, no rigor precondition,
					    free by design. The server allows duplicate deployments, so the
					    already-running state below is client-side dedupe UX. */}
					<PaperDeployCard
						strategyId={strategyId}
						user={user}
						onNavigate={onNavigate}
					/>

					{/* What the generation run that produced this strategy actually
					    consumed (#1326). Durable — read from the DB, not from the
					    job record, which expires an hour after the run ends. */}
					<GenerationCostCard record={s.generation_cost} />
				</aside>

				<div className="passport-evidence">
					{/* Methodology + source paper(s) */}
					<div className="passport-sources fade-up fade-up-3">
						{/* The user's own free-text ask (v8 Lane 3.3) — distinct from
						    Methodology below, which is the DERIVED writeup. Only the
						    single-strategy detail fetch populates this field, only for
						    the row's OWNER (the server redacts it for everyone else,
						    published rows included), and only when the brief is known —
						    so this renders for neither someone else's strategy, nor a
						    curated one, nor a legacy generated one the backfill could
						    not resolve. No viewer-side ownership check is needed or
						    wanted here: the redaction is the server's, and this guard
						    only declines to draw an empty card. That is what keeps the
						    "Your brief" copy literally true for whoever is reading. */}
						{s.brief_intent && (
							<div className="card passport-panel">
								<div className="label mb-2">Your brief</div>
								<p
									className="body leading-relaxed"
									style={{ fontStyle: "italic" }}
								>
									"{s.brief_intent}"
								</p>
							</div>
						)}
						<div className="card passport-panel">
							<div className="label mb-3">Methodology</div>
							<p className="body leading-relaxed">
								{s.methodology_summary || "—"}
							</p>
							<div className="mt-4 grid grid-cols-2 gap-3">
								<div>
									<div className="caption text-[var(--text-4)]">
										Position sizing
									</div>
									<div className="body capitalize">
										{s.position_sizing || "—"}
									</div>
								</div>
								<div>
									<div className="caption text-[var(--text-4)]">Rebalance</div>
									<div className="body capitalize">
										{s.rebalance_frequency || "—"}
									</div>
								</div>
								<div>
									<div className="caption text-[var(--text-4)]">
										Asset universe
									</div>
									<div className="body">
										{(s.asset_universe || []).join(", ") || "—"}
									</div>
								</div>
								{s.kelly_fraction != null && (
									<div>
										<div className="caption text-[var(--text-4)]">
											Kelly fraction
										</div>
										<div className="body mono">{fmt(s.kelly_fraction)}</div>
									</div>
								)}
							</div>
						</div>

						{/* Multi-paper (fusion/debate) vs. single-paper (curated) layout */}
						{(s.papers || []).length > 1 ? (
							<div className="card passport-panel">
								<div className="label mb-1">
									Fused from {s.papers.length} papers
								</div>
								<p className="caption mb-3 text-[var(--text-3)]">
									This strategy synthesizes ideas from multiple research papers
									into a single investment methodology.
								</p>
								<div className="flex flex-col gap-3">
									{s.papers.map((p, idx) => (
										<div
											key={p.arxiv_id || idx}
											className="pb-3"
											style={
												idx < s.papers.length - 1
													? { borderBottom: "1px solid var(--glass-border)" }
													: undefined
											}
										>
											<p
												className="body leading-snug"
												style={{ fontStyle: "italic", fontWeight: 600 }}
											>
												"{p.title || p.arxiv_id || "—"}"
											</p>
											{p.arxiv_id && (
												<a
													href={`https://arxiv.org/abs/${p.arxiv_id}`}
													target="_blank"
													rel="noreferrer"
													className="tag tag-muted"
													style={{
														fontFamily: "var(--mono, monospace)",
														fontSize: "0.72rem",
														marginTop: 4,
														display: "inline-block",
													}}
												>
													arxiv:{p.arxiv_id} ↗
												</a>
											)}
											{p.contribution && (
												<p
													className="caption mt-1 leading-relaxed"
													style={{
														fontStyle: "italic",
														color: "var(--text-3)",
													}}
												>
													{p.contribution}
												</p>
											)}
										</div>
									))}
								</div>
								{s.methodology_hash && (
									<div className="caption mt-3 mono text-[var(--text-4)]">
										hash: {s.methodology_hash.slice(0, 24)}…
									</div>
								)}
							</div>
						) : (
							<div className="card passport-panel">
								<div className="label mb-3">Source paper</div>
								<p
									className="body leading-snug"
									style={{ fontStyle: "italic" }}
								>
									"{s.paper_title}"
								</p>
								<p className="caption mt-2 leading-relaxed">
									{s.paper_authors?.slice(0, 4).join(", ")}
									{s.paper_authors?.length > 4 ? " et al." : ""}
									{s.paper_year ? ` (${s.paper_year})` : ""}
									{s.paper_venue && <> · {s.paper_venue}</>}
								</p>
								{s.paper_doi && (
									<div className="caption mt-2">
										DOI: <span className="mono">{s.paper_doi}</span>
									</div>
								)}
								{s.paper_citation_count != null && (
									<div className="caption">
										Cited by {s.paper_citation_count} other works
									</div>
								)}
								{s.methodology_hash && (
									<div className="caption mt-3 mono text-[var(--text-4)]">
										hash: {s.methodology_hash.slice(0, 24)}…
									</div>
								)}
							</div>
						)}
					</div>

					{/* Backtest metrics */}
					<div className="card passport-panel passport-backtest fade-up fade-up-4">
						<div className="label mb-3">Backtest</div>
						{s.is_backtest_placeholder && (
							<div className="info-box mb-3" style={{ fontSize: "0.85rem" }}>
								Pre-backtest hypothesis — empirical metrics pending evaluation.
							</div>
						)}
						<div className="grid grid-cols-2 md:grid-cols-4 gap-3">
							<Metric
								label="Sharpe"
								value={fmt(s.sharpe_ratio)}
								hint={
									s.sharpe_ci_lower != null && s.sharpe_ci_upper != null
										? `[${fmt(s.sharpe_ci_lower)}, ${fmt(s.sharpe_ci_upper)}]`
										: null
								}
							/>
							<Metric label="CAGR" value={fmtPct(s.cagr)} />
							<Metric
								label="Max DD"
								value={
									s.max_drawdown != null ? `−${fmtPct(s.max_drawdown)}` : "—"
								}
							/>
							<Metric label="Calmar" value={fmt(s.calmar_ratio)} />
							<Metric label="Sortino" value={fmt(s.sortino_ratio)} />
							<Metric label="Win rate" value={fmtPct(s.win_rate)} />
							<Metric
								label="Trades"
								value={s.total_trades != null ? s.total_trades : "—"}
							/>
							<Metric label="ρ to SPY" value={fmt(s.correlation_to_spy)} />
						</div>
						{(s.backtest_start || s.backtest_end) && (
							<div className="caption mt-3 text-[var(--text-3)]">
								Window:{" "}
								<span className="mono">
									{(s.backtest_start || "").slice(0, 10)} →{" "}
									{(s.backtest_end || "").slice(0, 10)}
								</span>
							</div>
						)}
						{s.paper_claimed_sharpe != null && (
							<div className="caption mt-2">
								Paper-claimed Sharpe:{" "}
								<strong>{fmt(s.paper_claimed_sharpe)}</strong> · realized:{" "}
								<strong>{fmt(s.sharpe_ratio)}</strong>{" "}
								{s.sharpe_ratio != null &&
									(() => {
										const ratio =
											s.paper_claimed_sharpe > 0.01
												? s.sharpe_ratio / s.paper_claimed_sharpe
												: null;
										return (
											<span
												className={
													ratio != null && ratio >= 0.5
														? "positive"
														: "negative"
												}
											>
												(
												{ratio != null
													? `${(ratio * 100).toFixed(0)}% of paper claim`
													: "—"}
												)
											</span>
										);
									})()}
							</div>
						)}
					</div>

					{/* Rigor gate */}
					<div className="card passport-panel passport-rigor fade-up fade-up-5">
						<div className="flex items-center justify-between flex-wrap gap-2 mb-3">
							<div className="label">
								Rigor verdict — selection-bias controls
							</div>
							<div className="flex items-center gap-2 flex-wrap">
								<span
									className={`tag inline-flex items-center gap-1 ${passingRigor ? "tag-positive" : "tag-muted"}`}
									title={
										unknownRigor
											? UNKNOWN_RIGOR_TITLE
											: rigorDegenerate
												? "DEGENERATE — the persisted return series is zero-variance (broken data or a zero-trade backtest), not a real evaluation"
												: undefined
									}
								>
									{/* Unevaluable states are tested FIRST, ahead of passingRigor:
									    a verdict nothing could compute must never be able to reach
									    a "Verified" or a "not Verified" claim through a stale
									    boolean. (Both are already false for these rows today —
									    this ordering is what keeps that true if the boolean ever
									    drifts.) */}
									{unknownRigor ? (
										UNKNOWN_RIGOR_LABEL
									) : rigorDegenerate ? (
										"unevaluable — zero-variance return series"
									) : passingRigor ? (
										<>
											<span className="i-lucide-check w-3.5 h-3.5" /> Verified
											(Conservative)
										</>
									) : rigorPending ? (
										"pending — not yet evaluated"
									) : (
										"not Verified"
									)}
								</span>
								{blockedByFloor ? (
									<span
										className="tag tag-negative"
										title="Fails an always-on correctness floor — look-ahead audit, positive OOS Sharpe, and DSR ≥ 0.50 — independent of strictness level; cannot be deployed at any level"
									>
										blocked — correctness floor
									</span>
								) : minLevel != null && minLevel > BADGE_LEVEL ? (
									<span className="tag tag-accent">
										deployable at {levelLabel(null, minLevel)}+
									</span>
								) : null}
							</div>
						</div>
						<div className="grid grid-cols-2 md:grid-cols-4 gap-3">
							<Metric
								label="DSR"
								value={fmt(s.deflated_sharpe_ratio)}
								hint={
									// Despite the wire field's legacy name (dsr_p_value), higher
									// is better here — a confidence, not a classical significance
									// statistic where lower is better (leaderboard_schemas.py).
									// "p = 0.93" reads as catastrophic to a reader expecting the
									// classical convention; it is in fact the passing case (#1358).
									s.dsr_p_value != null
										? `confidence = ${fmt(s.dsr_p_value, 3)}`
										: null
								}
							/>
							{/* PBO of exactly 0 paired with missing DSR/OOS is almost always a
              placeholder, not a real measurement. Render "—" in that case so
              we don't show a fake 0.0% next to honest unknowns elsewhere. */}
							<Metric
								label="PBO"
								value={
									s.pbo_score == null ||
									(s.pbo_score === 0 &&
										s.deflated_sharpe_ratio == null &&
										s.out_of_sample_sharpe == null)
										? "—"
										: fmtPct(s.pbo_score)
								}
								hint="lower = less overfit"
							/>
							<Metric label="OOS Sharpe" value={fmt(s.out_of_sample_sharpe)} />
							<Metric
								label="Trades"
								value={s.total_trades != null ? s.total_trades : "—"}
								hint="executed in backtest"
							/>
						</div>
						<p className="caption mt-3 leading-relaxed text-[var(--text-3)]">
							{/* #1358 round-2 review: a strategy the live gate hasn't graded
							    yet (no num_trials_in_selection / an "unspecified" provenance
							    — see schemas.py, which covers BOTH "no persisted returns" and
							    a batch/DB-failure pending case, so this sentence must not
							    assert a specific data fact) must not claim EITHER a
							    self-contained N=1 grading OR a real multi-candidate
							    correction; both would assert a statistic nothing computed.
							    "generated_untracked_default" (schemas.py) is a DISTINCT
							    third case: the strategy WAS graded, but its generation
							    pipeline never proved it tracks its own selection-pool size,
							    so num_trials was forced to 1 and the scope says so
							    explicitly — that is not "ungraded" and must not reuse either
							    the "not graded yet" sentence or the true-self-contained N=1
							    sentence below. Four branches total, not a fallback chain. */}
							{s.num_trials_in_selection == null ||
							s.num_trials_scope === "unspecified" ? (
								<>This strategy has not been graded by the rigor gate yet.</>
							) : s.num_trials_scope === "generated_untracked_default" ? (
								<>
									This strategy's generation pipeline did not record its own
									selection-pool size, so it is graded at num_trials = 1 (no
									multiple-testing correction applied) — the same treatment as
									a self-contained strategy, but here because the pool size is
									unknown, not because there was only one candidate.
								</>
							) : s.num_trials_in_selection > 1 ? (
								<>
									The Deflated Sharpe Ratio corrects the realized Sharpe for
									multiple-testing inflation across the{" "}
									{s.num_trials_in_selection} candidates in this strategy's own
									selection pool (Bailey & López de Prado 2014).
								</>
							) : (
								<>
									This strategy is graded on its own Sharpe (num_trials = 1 — no
									multiple-testing correction applied); the Deflated Sharpe
									Ratio here still uses a standard error robust to non-normality
									and serial correlation (Newey–West HAC; Bailey & López de
									Prado 2014).
								</>
							)}{" "}
							PBO estimates how much of the in-sample Sharpe is overfit (Bailey
							et al. 2014). OOS Sharpe is the chronological out-of-sample
							number. A strategy passes the rigor gate only when all four
							signals — DSR, PBO, OOS Sharpe, and the look-ahead audit — align.
						</p>

						{/* Return source (T2.5) — the rigor gate says whether the edge survives;
            this says WHY it exists, and how durable that source is. */}
						{s.return_source && (
							<div className="mt-4 pt-4 border-t border-[var(--glass-border)]">
								<div className="flex items-center gap-2 flex-wrap mb-1">
									<span className="caption text-[var(--text-4)]">
										Dominant return source
									</span>
									<span
										className={`tag ${returnSourceChip(s.return_source).cls}`}
									>
										{returnSourceChip(s.return_source).label}
									</span>
								</div>
								{s.return_source_note && (
									<p className="caption leading-relaxed text-[var(--text-3)]">
										{s.return_source_note}
									</p>
								)}
							</div>
						)}
					</div>

					{/* Provenance */}
					{(s.curator_wallet ||
						s.on_chain_registration_tx ||
						s.extraction_llm) && (
						<div className="card passport-panel passport-provenance">
							<div className="label mb-3">Provenance</div>
							<div className="grid grid-cols-1 md:grid-cols-2 gap-3 text-[0.85rem]">
								{s.extraction_llm && (
									<div>
										<div className="caption text-[var(--text-4)]">
											Extracted by
										</div>
										<div className="mono">{s.extraction_llm}</div>
									</div>
								)}
								{s.curator_wallet && (
									<div>
										<div className="caption text-[var(--text-4)]">Curator</div>
										<div className="mono">
											{s.curator_wallet.slice(0, 12)}…
											{s.curator_wallet.slice(-6)}
										</div>
									</div>
								)}
								{s.on_chain_registration_tx && (
									<div>
										<div className="caption text-[var(--text-4)]">
											Registration tx
										</div>
										<a
											href={`https://testnet.arcscan.app/tx/${s.on_chain_registration_tx}`}
											target="_blank"
											rel="noopener noreferrer"
											className="mono underline decoration-dotted underline-offset-2 hover:text-[var(--accent)] transition-colors"
										>
											{s.on_chain_registration_tx.slice(0, 14)}… ↗
										</a>
									</div>
								)}
								{s.curator_note && (
									<div style={{ gridColumn: "1 / -1" }}>
										<div className="caption text-[var(--text-4)]">
											Curator note
										</div>
										<div className="body">{s.curator_note}</div>
									</div>
								)}
							</div>
						</div>
					)}
				</div>
			</div>

			{ROADMAP_SURFACES_ENABLED && deployOpen && (
				<CreateVaultModal
					strategy={s}
					walletAddr={walletAddr}
					strictnessLevel={level}
					onClose={() => setDeployOpen(false)}
					onDeployed={(vaultAddress) => {
						setDeployOpen(false);
						if (onNavigate) {
							onNavigate("portfolio", { vaultAddress });
						}
					}}
				/>
			)}
		</div>
	);
}

function Metric({ label, value, hint }) {
	return (
		<div className="passport-metric">
			<div className="caption text-[var(--text-4)]">{label}</div>
			<div className="text-[1.1rem] font-semibold tabular-nums">{value}</div>
			{hint && <div className="caption text-[var(--text-4)]">{hint}</div>}
		</div>
	);
}

// Generation cost (#1326) — the measurement of the run that produced this
// strategy, beside the price that was quoted for it.
//
// Two facts, two sources, never a conversion: the tokens/seconds come from the
// cost meter, the price comes from the recorded `generation_payment.quote()`
// payload. Nothing here turns one into the other — converting measured tokens
// into dollars is #1217's remaining pricing work and it happens off-server.
//
// Absence is the honest default. No record at all (every curated strategy, and
// every generated one from before the meter) renders as "not measured", never
// as zero; a run whose token usage was only partly readable renders its totals
// with a `≥` so a floor is never read as a total.
function GenerationCostCard({ record }) {
	const view = deriveGenerationCostView(record);
	return (
		<div className="card fade-up fade-up-2" style={{ marginTop: 12 }}>
			<div className="label mb-1">Generation cost</div>
			{!view ? (
				<p className="caption leading-relaxed">{NOT_MEASURED_HINT}</p>
			) : (
				<>
					<p className="caption leading-relaxed">
						What this generation run consumed — a raw measurement, recorded when
						the run finished. Token counts are <strong>not</strong> converted to
						dollars.
					</p>
					<div className="mt-3 grid grid-cols-2 gap-3">
						<Metric
							label="Tokens"
							value={tokensLabel(view)}
							hint={`in ${formatTokenCount(view.tokens.input)} / out ${formatTokenCount(view.tokens.output)}`}
						/>
						<Metric
							label="Wall time"
							value={formatDuration(view.wallSeconds)}
							hint={
								view.tokens.calls != null
									? `${view.tokens.calls} LLM calls`
									: "LLM calls not measured"
							}
						/>
					</div>
					<div className="mt-3">
						<div className="caption text-[var(--text-4)]">Dominant stage</div>
						<div className="body">
							{view.dominantStage
								? `${stageLabel(view.dominantStage.name)} · ${formatDuration(view.dominantStage.wallSeconds)}`
								: NOT_MEASURED}
						</div>
					</div>
					<p
						className="caption mt-2 leading-relaxed"
						style={{
							color: view.usageComplete ? "var(--text-3)" : "var(--warning)",
						}}
					>
						{usageNote(view)}
					</p>
					<div
						className="mt-3 pt-3"
						style={{ borderTop: "1px solid var(--glass-border)" }}
					>
						<div className="caption text-[var(--text-4)]">Quoted price</div>
						<div className="body">{quoteLabel(view) || NOT_MEASURED}</div>
						<p className="caption leading-relaxed text-[var(--text-3)]">
							{quoteNote(view)}
						</p>
					</div>
				</>
			)}
		</div>
	);
}

// Paper-trading CTA — the free, account-owned act-on step (MVP spine). Kept
// separate from the vault Deploy card above: no wallet, no strictness gate,
// nothing moves. The server permits duplicate deployments for one strategy,
// so the "already running" state is client-side dedupe UX only.
function PaperDeployCard({ strategyId, user, onNavigate }) {
	const [existing, setExisting] = useState(null); // active deployment for THIS strategy
	const [busy, setBusy] = useState(false);
	const [error, setError] = useState("");

	useEffect(() => {
		if (!user || !strategyId) return;
		let cancelled = false;
		apiGet("/api/paper/deployments")
			.then((res) => {
				if (cancelled) return;
				const hit = (res.deployments || []).find(
					(d) => d.strategy_id === strategyId && d.status === "active",
				);
				setExisting(hit || null);
			})
			.catch(() => {}); // decoration only — the CTA still works without it
		return () => {
			cancelled = true;
		};
	}, [user, strategyId]);

	const start = async () => {
		setBusy(true);
		setError("");
		try {
			await apiPostWithMeta("/api/paper/deployments", {
				strategy_id: strategyId,
			});
			onNavigate("paper");
		} catch (e) {
			if (e.status === 401) {
				setError("Your session expired — sign in again to paper trade.");
			} else if (e.detail?.reason === "no_strategy_spec") {
				setError(
					"This strategy has no machine-readable spec, so it cannot be paper-traded.",
				);
			} else if (e.detail?.reason === "invalid_strategy_spec") {
				setError(
					"This strategy's stored spec fails validation — it cannot be paper-traded.",
				);
			} else {
				setError(e.detail?.message || e.message || "Paper deploy failed.");
			}
		} finally {
			setBusy(false);
		}
	};

	return (
		<div className="card fade-up fade-up-2" style={{ marginTop: 12 }}>
			<div className="label mb-1">Paper trading</div>
			<p className="caption leading-relaxed">
				Simulated deployment — <strong>free, no funds move</strong>. Snapshots
				this strategy's spec and appends one real-data return per trading day,
				building the track record that carries to mainnet.
			</p>
			{error && (
				<p className="caption" role="alert" style={{ color: "var(--negative)", marginTop: 6 }}>
					{error}
				</p>
			)}
			<div style={{ marginTop: 10 }}>
				{!user ? (
					<a
						className="btn btn-outline btn-sm"
						href={`/sign-in?next=${encodeURIComponent(`/app/strategy/${strategyId}`)}`}
					>
						Sign in to paper trade →
					</a>
				) : existing ? (
					<button
						className="btn btn-outline btn-sm"
						onClick={() => onNavigate("paper")}
						title={`Active since ${existing.deployed_at}`}
					>
						Paper trading — day {existing.days} · view →
					</button>
				) : (
					<button
						className="btn btn-primary btn-sm"
						disabled={busy}
						onClick={start}
					>
						{busy ? "Deploying…" : "Start paper trading (free) →"}
					</button>
				)}
			</div>
		</div>
	);
}
