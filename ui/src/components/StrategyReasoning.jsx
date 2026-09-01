import { useEffect, useState } from "react";
import { apiGet } from "../api";
import { anchorState } from "../trace-binding";

// The reasoning panel on a strategy's passport.
//
// Two DIFFERENT things get called "the reasoning" for a strategy, and merging
// them would be the dishonest shortcut:
//
//   Generation debate — GET /api/strategies/{id}/debate (#1542). One bull/bear
//     adversarial round, argued ONCE over the whole proposed pool BEFORE C-null
//     picked this candidate. It is an argument about whether the strategy was
//     worth keeping. It moved no money and is anchored nowhere.
//   Trading decisions — GET /api/traces/?strategy_id={id}. Reasoning traces the
//     autonomous agent emitted while running a vault that references this
//     strategy. Each one is hashed and (when it traded) anchored on Arc.
//
// What the second section can show is bounded by what the filter can honestly
// match, and the copy says so rather than implying more. `strategies_referenced`
// holds real strategy ids only on the agent's DECISION traces; the two
// construction writers in api/strategies_routes.py put arXiv ids and paper
// anchors in the same field, so the backend scopes ?strategy_id= to decision
// types (services/redis_state.py STRATEGY_REFERENCE_DECISION_TYPES). A
// construction trace therefore never appears here — not because it is hidden,
// but because it references papers, not this strategy. Saying "no anchored
// decisions" while silently dropping a whole class of trace would be the
// dishonest version of the same empty state.
//
// They are rendered as two separately-headed sections, never interleaved into
// one "reasoning" timeline: a debate turn carries no anchor and must never sit
// in a list whose rows imply one.
//
// Every trace row's anchoring claim comes from `anchorState` (src/trace-binding.js)
// rather than an inline ternary here, so this panel cannot drift from the
// Reasoning page and the Portfolio feed the way those two drifted from each
// other. See that helper for why "not anchored (no trade to bind)" is a
// distinct state from "anchor pending".

const TRACE_LIMIT = 20;
const REASONING_EXCERPT = 240;

// A debate claim arrives in one of TWO shapes and both are real data (#1636):
//
//   - a plain string — every row persisted before the debate carried paper
//     attribution. Rendering these as `[object Object]`-proof text is not
//     enough; they must keep rendering exactly as they always did.
//   - `{claim, candidate_id, arxiv_ids}` — the current shape. `arxiv_ids` has
//     already been filtered server-side against the papers the proposers
//     actually read, so an id here is real. An EMPTY array is meaningful and
//     is shown as such: the claim was made without grounding it in a listed
//     paper, and saying so is the whole point of keeping it.
function claimText(claim) {
	if (typeof claim === "string") return claim;
	if (claim && typeof claim === "object") return String(claim.claim ?? "");
	return "";
}

function claimArxivIds(claim) {
	if (!claim || typeof claim !== "object" || Array.isArray(claim)) return null;
	return Array.isArray(claim.arxiv_ids) ? claim.arxiv_ids : null;
}

function formatWhen(ts) {
	if (!ts) return "—";
	const asDate = new Date(ts);
	const epochMs = !Number.isNaN(asDate.getTime())
		? asDate.getTime()
		: Number(ts) * 1000;
	// A missing timestamp often serializes as epoch 0 rather than as an empty
	// value. That is an absent timestamp, not a 1970 event.
	if (!Number.isFinite(epochMs) || epochMs <= 0) return "—";
	return new Date(epochMs).toLocaleString(undefined, {
		year: "numeric",
		month: "short",
		day: "numeric",
		hour: "2-digit",
		minute: "2-digit",
	});
}

function decisionTag(type) {
	if (type === "rebalance") return "tag-positive";
	if (type === "construction") return "tag-accent";
	if (type === "skip") return "tag-warning";
	return "tag-muted";
}

function AnchorBadge({ trace }) {
	const a = anchorState(trace);
	const cls =
		a.tone === "verified" ? "text-[var(--positive)]" : "text-[var(--text-3)]";
	return (
		<span
			className={`flex items-center gap-1 text-xs ${cls}`}
			title={a.title}
			data-anchor-state={a.state}
		>
			<span className={`${a.icon} w-3.5 h-3.5`} /> {a.label}
		</span>
	);
}

// ── Generation debate ───────────────────────────────────────────────────

function GenerationDebate({ strategyId }) {
	const [payload, setPayload] = useState(null);
	const [loading, setLoading] = useState(true);
	const [error, setError] = useState("");

	useEffect(() => {
		let cancelled = false;
		setLoading(true);
		setPayload(null);
		setError("");
		apiGet(`/api/strategies/${encodeURIComponent(strategyId)}/debate`)
			.then((data) => {
				if (!cancelled) setPayload(data);
			})
			.catch((e) => {
				// 404 is the expected, honest "no transcript was ever persisted"
				// answer — every curated strategy (the debate society never ran
				// for those) and everything generated before the table existed.
				// It is an empty state, not an error.
				if (!cancelled && e.status !== 404)
					setError(e.message || "Failed to load debate transcript");
			})
			.finally(() => {
				if (!cancelled) setLoading(false);
			});
		return () => {
			cancelled = true;
		};
	}, [strategyId]);

	if (loading) {
		return (
			<div className="caption" role="status">
				Loading generation debate…
			</div>
		);
	}

	if (error) {
		return (
			<p className="caption" role="alert" style={{ color: "var(--negative)" }}>
				Could not load the debate transcript: {error}
			</p>
		);
	}

	const turns = payload?.transcript || [];
	if (turns.length === 0) {
		return (
			<p className="caption leading-relaxed">
				No debate transcript for this strategy. The bull/bear debate is
				recorded only for strategies produced by the generation pipeline —
				curated library strategies never ran one, and strategies generated
				before transcripts were persisted have none to show.
			</p>
		);
	}

	return (
		<>
			<p className="caption mb-3 leading-relaxed max-w-[640px]">
				One adversarial round, argued before this candidate was selected. It
				is an argument about the strategy, not a record of a trade — nothing
				here is anchored on-chain.
			</p>
			<div className="flex flex-col gap-2">
				{turns.map((turn, i) => (
					<div
						key={`${turn.role}-${turn.round}-${i}`}
						className="card"
						style={{ padding: 12 }}
					>
						<div className="flex gap-2 items-center flex-wrap mb-1.5">
							<span
								className={`tag ${turn.role === "bull" ? "tag-positive" : turn.role === "bear" ? "tag-negative" : "tag-muted"}`}
							>
								{turn.role || "turn"}
							</span>
							{turn.round != null && (
								<span className="caption">Round {turn.round}</span>
							)}
						</div>
						{turn.verdict && (
							<p className="body" style={{ fontSize: "0.85rem", lineHeight: 1.5 }}>
								{turn.verdict}
							</p>
						)}
						{Array.isArray(turn.claims) && turn.claims.length > 0 && (
							<ul className="caption mt-1.5 leading-relaxed pl-4 list-disc">
								{turn.claims.map((claim, j) => {
									const ids = claimArxivIds(claim);
									return (
										<li key={j}>
											{claimText(claim)}
											{ids !== null &&
												(ids.length > 0 ? (
													<span className="caption" style={{ opacity: 0.75 }}>
														{" "}
														— {ids.map((id) => `arXiv:${id}`).join(", ")}
													</span>
												) : (
													<span className="caption" style={{ opacity: 0.75 }}>
														{" "}
														— not attributed to a listed paper
													</span>
												))}
										</li>
									);
								})}
							</ul>
						)}
						{Array.isArray(turn.discard) && turn.discard.length > 0 && (
							<ul className="caption mt-1.5 leading-relaxed pl-4 list-disc">
								{turn.discard.map((d, j) => (
									<li key={j} style={{ opacity: 0.75 }}>
										Discarded arXiv:{d?.arxiv_id}
										{d?.reason ? ` — ${d.reason}` : ""}
									</li>
								))}
							</ul>
						)}
					</div>
				))}
			</div>
		</>
	);
}

// ── Trading decisions ───────────────────────────────────────────────────

function TradingDecisions({ strategyId, onNavigate }) {
	const [traces, setTraces] = useState([]);
	const [total, setTotal] = useState(0);
	const [loading, setLoading] = useState(true);
	const [error, setError] = useState("");

	useEffect(() => {
		let cancelled = false;
		setLoading(true);
		setTraces([]);
		setError("");
		apiGet(
			`/api/traces/?strategy_id=${encodeURIComponent(strategyId)}&limit=${TRACE_LIMIT}`,
		)
			.then((data) => {
				if (cancelled) return;
				setTraces(Array.isArray(data?.traces) ? data.traces : []);
				setTotal(Number(data?.total) || 0);
			})
			.catch((e) => {
				// A 404 here means the strategy itself is not visible to this
				// caller — the same answer GET /api/strategies/{id} gives. The
				// passport above would not have rendered in that case, so treat
				// it as an empty state rather than inventing a second error.
				if (!cancelled && e.status !== 404)
					setError(e.message || "Failed to load trading decisions");
			})
			.finally(() => {
				if (!cancelled) setLoading(false);
			});
		return () => {
			cancelled = true;
		};
	}, [strategyId]);

	if (loading) {
		return (
			<div className="caption" role="status">
				Loading trading decisions…
			</div>
		);
	}

	if (error) {
		return (
			<p className="caption" role="alert" style={{ color: "var(--negative)" }}>
				Could not load trading decisions: {error}
			</p>
		);
	}

	if (traces.length === 0) {
		return (
			<p className="caption leading-relaxed">
				No trading decisions recorded yet for this strategy. They appear here
				once the autonomous agent runs a vault that holds it — a rebalance, a
				rotation, a regime change, or a skip. This list does not include the
				strategy's own construction: that trace cites the papers it was built
				from, not the strategy, so it is not a decision about holding it.
			</p>
		);
	}

	return (
		<>
			<p className="caption mb-3 leading-relaxed max-w-[640px]">
				Agent decisions — rebalances, rotations, regime changes and skips —
				that named this strategy, newest first
				{total > traces.length ? ` (showing ${traces.length} of ${total})` : ""}
				. Each row states its own anchoring status: a decision that traded is
				anchored on Arc, a skip has no trade for an anchor to bind. Open one
				on Reasoning to re-fetch the on-chain receipt and compare the hash.
			</p>
			<div className="flex flex-col gap-2">
				{traces.map((t) => (
					<div key={t.id} className="card" style={{ padding: 12 }}>
						<div className="flex justify-between items-start gap-3 flex-wrap mb-1.5">
							<div className="flex gap-2 items-center flex-wrap">
								<span className={`tag capitalize ${decisionTag(t.decision_type)}`}>
									{t.decision_type}
								</span>
								{t.trigger && (
									<strong style={{ fontSize: "0.85rem" }}>{t.trigger}</strong>
								)}
							</div>
							<span className="caption">{formatWhen(t.timestamp)}</span>
						</div>
						{t.reasoning && (
							<p className="body" style={{ fontSize: "0.85rem", lineHeight: 1.45 }}>
								{t.reasoning.slice(0, REASONING_EXCERPT)}
								{t.reasoning.length > REASONING_EXCERPT ? "…" : ""}
							</p>
						)}
						<div className="caption mt-1.5 flex gap-3 items-center flex-wrap text-[var(--text-3)]">
							<AnchorBadge trace={t} />
							{t.arc_tx_hash && (
								<a
									href={`https://testnet.arcscan.app/tx/${t.arc_tx_hash}`}
									target="_blank"
									rel="noopener noreferrer"
									className="mono underline decoration-dotted underline-offset-2 hover:text-[var(--accent)] transition-colors"
								>
									{t.arc_tx_hash.slice(0, 10)}… ↗
								</a>
							)}
							{onNavigate && (
								<button
									type="button"
									className="btn btn-outline btn-sm"
									onClick={() => onNavigate("reasoning", { traceId: t.id })}
								>
									Open in Reasoning →
								</button>
							)}
						</div>
					</div>
				))}
			</div>
		</>
	);
}

// ── Panel ───────────────────────────────────────────────────────────────

export default function StrategyReasoning({ strategyId, onNavigate }) {
	if (!strategyId) return null;
	return (
		<div className="card passport-panel passport-reasoning">
			<div className="label mb-3">Reasoning</div>
			<section className="mb-5">
				<div className="label mb-2" style={{ fontSize: "0.75rem" }}>
					Generation debate
				</div>
				<GenerationDebate strategyId={strategyId} />
			</section>
			<section>
				<div className="label mb-2" style={{ fontSize: "0.75rem" }}>
					Trading decisions
				</div>
				<TradingDecisions strategyId={strategyId} onNavigate={onNavigate} />
			</section>
		</div>
	);
}
