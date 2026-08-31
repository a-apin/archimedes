"""Paper trading: forward-run the GRADED engine, one appended bar per day.

Why replay-then-append rather than a persisted position state machine: the
backtest engine is a position FSM, so "today's" paper bar depends on every
bar since deploy. Re-running the full replay daily is cheap (one strategy,
daily bars), needs no serialized broker state, and is deterministic given the
data. The LEDGER is still append-only — the replay only ever contributes
dates the ledger has not seen. When a replay DISAGREES with rows already
written, the ledger is NOT rewritten: the drift is counted, logged loudly,
and stamped on the deployment. A track record that silently rewrites itself
is the exact failure this product exists to oppose.

Replay = the same calls the grading path uses (``fetch_real_panel`` →
``feed_factory`` → per-sleeve ``run_dsl_backtest`` with fusion's
dollar-sleeve aggregation), so paper semantics track graded semantics by
construction — including F1's momentum convention and whatever lands next,
with the interpreter-parity harness holding the other side. That "by
construction" coupling cuts both ways: this module has NO independent
opinion on cost model, commission, or slippage — a real historical
restatement (yfinance revising a bar) and a change to the GRADED path's own
cost model between two replays (e.g. wiring a previously-inert slippage
parameter, #1242/#1379) produce the identical drift signature. Don't assume
the cause is upstream data — see ``advance_deployment``'s log message, which
names both candidates rather than asserting the one that happens to be more
common.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime

from archimedes.models.paper_store import STATUS_ACTIVE, PaperDailyReturn, PaperDeployment
from archimedes.services.strategy_dsl import DSLError, validate_strategy_spec

logger = logging.getLogger(__name__)

# |replayed - ledgered| beyond this is a restatement, not float noise.
_DRIFT_EPS = 1e-9


class PaperReplayError(RuntimeError):
    """Replay could not produce a trustworthy dated series (fail closed)."""


class PaperTraceCoverageError(RuntimeError):
    """A decision left the trace pipeline without being accounted for.

    Deliberately NOT caught alongside ``PaperReplayError`` in ``advance_all``:
    a replay failure is one deployment's bad data, but a broken coverage
    identity means the counts a user reads about their own provenance are
    wrong. That is a bug in this module, and it must be loud.
    """


#: The literal that names a broken coverage identity in the logs. Shared, so
#: the scheduler's per-deployment isolation and the create-deployment route
#: report the SAME distinctive string, and so a test can assert on it rather
#: than on prose someone may reword.
COVERAGE_BROKEN_LOG = "paper: TRACE COVERAGE IDENTITY BROKEN"


@dataclass(frozen=True)
class PositionSet:
    """What the DAILY replay last established, for the marks loop to price.

    This is the read-only input to intraday mark-to-market (intraday design
    §4.1). It is written once per daily advance and never by the marks loop —
    the one-way arrow is the entire safety argument for marks: re-pricing a
    position more often is a display change; re-DECIDING it more often is a
    different strategy from the one the rigor gate graded (§1.3, divergence
    audit F3).

    ``weights`` are the dollar-sleeve weights ``replay_spec`` already computes
    on its way to a portfolio return — each symbol runs as an independently
    capitalized sleeve, so a sleeve's share of total equity IS its weight.
    They sum to 1.0.

    ``ref_prices`` is the close each weight was struck at, on ``as_of``. A
    mark is ``Σ wᵢ · (Pᵢ_now / Pᵢ_ref)``, so both halves have to come from the
    same bar or the ratio is measuring the wrong interval.

    **The disclosed approximation.** v1 values every sleeve at its own asset's
    move since ``as_of``. A sleeve the strategy currently holds in CASH is
    still valued that way, because ``replay_spec`` returns dated portfolio
    returns and not a per-sleeve invested/flat vector, and inferring one from
    the return series would be a guess dressed as a measurement. The
    consequence is bounded and one-directional: an out-of-market sleeve's
    intraday contribution is overstated in magnitude (in either direction)
    until the next daily advance re-settles it against the ledger, which is
    the authoritative number. This is why marks are labelled an *unsettled*
    view and why ``paper_daily_returns`` — never a mark — is the track record.
    Closing the gap needs a position vector out of the graded engine; that is
    scoped work, not a v1 constant.
    """

    as_of: date
    weights: dict[str, float]
    ref_prices: dict[str, float]

    def to_json(self, equity_index: float) -> str:
        return json.dumps(
            {
                "as_of": self.as_of.isoformat(),
                "equity_index": equity_index,
                "weights": self.weights,
                "ref_prices": self.ref_prices,
            },
            sort_keys=True,
        )


class ReplayResult(dict):
    """``dict[date, float]`` of portfolio returns, with ``.positions`` attached.

    Subclasses ``dict`` deliberately. ``advance_deployment``'s ``replay=``
    parameter is a seam many callers and tests satisfy with a plain dict, and
    the ledger append only ever needs the mapping — so widening the return
    type would break every one of them, and adding a SECOND replay call to
    fetch positions would double the most expensive thing the daily advance
    does (§4.1: the position set is cached once per day precisely because
    re-running a replay every 15 minutes would be slow and pointless).

    Attaching the position set as an attribute means one replay produces both
    outputs, while a plain-dict replay stub stays valid and simply refreshes
    no cache — ``getattr(result, "positions", None)`` is None and the advance
    proceeds untouched.
    """

    positions: PositionSet | None = None


def _sleeve_initial_cash() -> float:
    """Opening capital for ONE sleeve, read from the graded engine.

    Read rather than re-declared: ``_sleeve_dated_returns`` passes it straight
    back into ``run_dsl_backtest``, so the value the deployment-scoped
    portfolio snapshot attributes to a sleeve that never traded is the value
    the run actually used. Re-typing the number here would let the two drift
    and quietly misstate a hashed field.
    """
    from archimedes.services.fusion_evaluator import _DEFAULT_CASH

    return float(_DEFAULT_CASH)


def _closes_by_date(frame) -> dict[date, float]:
    """``{bar date: close}`` for one sleeve, used to mark UNTRADED sleeves.

    Column case follows ``fusion_market_data.feed_factory``, which lowercases
    before handing the frame to backtrader; the raw yfinance frame is
    ``Close``. Missing entirely → an empty dict, and the mark falls back to the
    sleeve's last fill price (see ``paper_trace._mark_price``) rather than to a
    fabricated zero.
    """
    try:
        lowered = {str(col).lower(): col for col in frame.columns}
        column = lowered.get("close")
        if column is None:
            return {}
        series = frame[column]
        return {(d.date() if hasattr(d, "date") else d): float(v) for d, v in zip(series.index, series, strict=True)}
    except (AttributeError, TypeError, ValueError):
        logger.warning("paper: could not read closes off a sleeve frame — untraded sleeves fall back to fill prices")
        return {}


def _sleeve_dated_returns(
    spec, sym: str, factory, frame, *, decisions: bool = False, initial_cash: float | None = None
) -> tuple[dict[date, float], list[dict]]:
    """Dated per-bar returns for one sleeve of the deployment's universe, and
    (when asked) the dated orders that sleeve placed.

    backtrader starts ``next()`` (and therefore the TimeReturn series feeding
    ``equity_curve``) only after the largest indicator warmup, so the returns
    list is END-aligned with the feed's bar index. The tail-alignment below is
    guarded by a hard length check — misalignment must fail loudly, because a
    misdated ledger row is worse than no row.

    ``decisions`` binds the observer-only decision journal (#1575). It rides
    the SAME ``run_dsl_backtest`` call as the returns rather than a second
    pass: a separate replay would double the settle-path cost, and — worse —
    could disagree with the run whose numbers were ledgered, which is exactly
    the two-implementations-of-one-cadence shape this module exists to avoid.
    """
    from archimedes.services.fusion_evaluator import run_dsl_backtest

    metrics = run_dsl_backtest(
        spec,
        data_feed_factory=factory,
        data_source_label=f"paper:{sym}",
        decision_journal=decisions,
        # Explicit, and the SAME number ``run_dsl_backtest`` defaults to, so no
        # graded value moves. Passing it makes the sleeve's opening capital a
        # fact this module knows rather than one it guesses when it has to
        # value a sleeve that never traded.
        initial_cash=_sleeve_initial_cash() if initial_cash is None else initial_cash,
    )
    curve = list(metrics.equity_curve or [])
    if len(curve) < 2:
        raise PaperReplayError(f"sleeve {sym}: replay produced no equity path")
    rets = [(curve[i] - curve[i - 1]) / curve[i - 1] for i in range(1, len(curve)) if curve[i - 1] > 0]
    idx = [d.date() if hasattr(d, "date") else d for d in frame.index]
    if len(rets) > len(idx):
        raise PaperReplayError(f"sleeve {sym}: {len(rets)} returns for {len(idx)} bars — alignment broken")

    legs: list[dict] = []
    if decisions:
        if metrics.decision_journal is None:
            # Fail closed: the flag was on and the journal came back absent,
            # which means the run produced no strategy. Publishing zero traces
            # for that is indistinguishable from "the strategy never traded".
            raise PaperReplayError(f"sleeve {sym}: decision journal requested but the run produced none")
        legs = [{**event, "symbol": sym} for event in metrics.decision_journal]

    return dict(zip(idx[-len(rets) :], rets, strict=True)), legs


def _dated_closes(frame) -> dict[date, float]:
    """``{bar date: close}`` for one sleeve's frame — the reference prices the
    dollar-sleeve weights were struck at."""
    idx = [d.date() if hasattr(d, "date") else d for d in frame.index]
    return {d: float(v) for d, v in zip(idx, frame["Close"], strict=True)}


def _replay(spec_dict: dict, deployed_at: date, *, decisions: bool) -> tuple[dict[date, float], dict[date, dict]]:
    """Shared body of :func:`replay_spec` / :func:`replay_decisions`.

    One pass over the sleeves produces both the dated returns and (optionally)
    the dated decisions, so the trace and the ledger row for a date can never
    come from two different runs of the same spec.

    A decision is ``{"legs": [...], "portfolio_before": {...},
    "portfolio_after": {...}}``. The two snapshots are DEPLOYMENT-scoped —
    every sleeve's cash and position, not just the sleeve that traded — which
    is why they are built here, the only place that has all the sleeves, and
    not inside the trace builder, which only ever sees one date's legs.
    """
    from archimedes.services import fusion_market_data
    from archimedes.services.paper_trace import deployment_portfolio

    spec = validate_strategy_spec(spec_dict)
    panel = fusion_market_data.fetch_real_panel(spec.asset_universe)
    if panel is None:
        raise PaperReplayError(f"real data unavailable for universe {spec.asset_universe}")

    sleeve_cash = _sleeve_initial_cash()
    sleeves: dict[str, dict[date, float]] = {}
    #: EVERY leg each sleeve placed, pre-deploy dates included: a sleeve's
    #: state on a decision date is the sum of every fill before it, and the
    #: replay starts at the feed's first bar, not at `deployed_at`.
    sleeve_legs: dict[str, list[dict]] = {}
    sleeve_closes: dict[str, dict[date, float]] = {}
    dated_legs: dict[date, list[dict]] = {}
    for sym, frame in panel.frames.items():
        factory = fusion_market_data.feed_factory(frame)
        sleeves[sym], legs = _sleeve_dated_returns(
            spec, sym, factory, frame, decisions=decisions, initial_cash=sleeve_cash
        )
        if decisions:
            sleeve_legs[sym] = list(legs)
            sleeve_closes[sym] = _closes_by_date(frame)
        for leg in legs:
            if leg["decided_on"] >= deployed_at:
                dated_legs.setdefault(leg["decided_on"], []).append(leg)
    if not sleeves:
        raise PaperReplayError("no sleeves replayed")
    # Deterministic leg order: the legs are hashed inside the trace body, so a
    # dict-iteration-order change must not move the hash of an unchanged
    # decision.
    dated_decisions: dict[date, dict] = {}
    for decision_date, legs in dated_legs.items():
        legs.sort(key=lambda leg: (leg["symbol"], leg["side"], leg["size"]))
        dated_decisions[decision_date] = {
            "legs": legs,
            "portfolio_before": deployment_portfolio(
                decision_date=decision_date,
                side="before",
                sleeve_legs=sleeve_legs,
                sleeve_initial_cash=sleeve_cash,
                sleeve_closes=sleeve_closes,
            ),
            "portfolio_after": deployment_portfolio(
                decision_date=decision_date,
                side="after",
                sleeve_legs=sleeve_legs,
                sleeve_initial_cash=sleeve_cash,
                sleeve_closes=sleeve_closes,
            ),
        }

    common = sorted(set.intersection(*(set(s.keys()) for s in sleeves.values())))
    if not common:
        raise PaperReplayError("sleeves share no dates")

    # Equity-weighted dollar sleeves: each sleeve compounds independently from
    # equal initial capital; the portfolio return is total-equity ratio.
    equities = dict.fromkeys(sleeves, 1.0)
    out = ReplayResult()
    for d in common:
        total_before = sum(equities.values())
        for sym in sleeves:
            equities[sym] *= 1.0 + sleeves[sym][d]
        total_after = sum(equities.values())
        if d >= deployed_at and total_before > 0:
            out[d] = total_after / total_before - 1.0
    out.positions = _position_set(panel, equities, common[-1])
    return out, dated_decisions


def replay_spec(spec_dict: dict, deployed_at: date) -> ReplayResult:
    """Full-history replay of a deployment's spec; returns {date: portfolio_return}
    for dates >= ``deployed_at``.

    Dollar-sleeve aggregation, faithful to the graded path: each symbol runs
    as an independently-capitalized sleeve; the portfolio return on a date is
    the equity-weighted combination of that date's sleeve returns.

    The return is a ``ReplayResult`` — a ``dict`` in every way that matters
    to the ledger append, plus ``.positions`` for the marks loop (§4.1);
    ``positions`` is None rather than an error when reference closes cannot
    be read, so a missing cache costs marks, never a ledger row.
    """
    return _replay(spec_dict, deployed_at, decisions=False)[0]


def replay_decisions(spec_dict: dict, deployed_at: date) -> dict[date, dict]:
    """``{decision date: decision}`` for dates >= ``deployed_at`` (#1575).

    A decision is ``{"legs": [...], "portfolio_before": {...},
    "portfolio_after": {...}}``; the two snapshots cover the whole deployment,
    every sleeve, not only the sleeve whose legs are in ``legs``.

    A paper decision is born at a rebalance-eligible bar on which the entry or
    exit condition fired — the only place in the paper system where the
    position set changes. This surfaces those bars from the observer-only
    journal, keyed by the DECIDED-on bar rather than the fill bar.

    Acted decisions only. A rebalance-eligible bar where the condition did not
    fire is also a decision (``DecisionType.SKIP``) but produces no order, so
    an order observer cannot see it — stated at the API surface as
    ``decision_kinds: ["rebalance"]`` rather than papered over.
    """
    return _replay(spec_dict, deployed_at, decisions=True)[1]


def replay_spec_with_decisions(spec_dict: dict, deployed_at: date) -> tuple[dict[date, float], dict[date, dict]]:
    """Both halves of one replay — what the settle path calls."""
    return _replay(spec_dict, deployed_at, decisions=True)


def _position_set(panel, equities: dict[str, float], as_of: date) -> PositionSet | None:
    """The sleeve weights + reference closes on ``as_of``, or None.

    Best-effort by design (see ``replay_spec``): a sleeve whose frame has no
    bar on the shared last date, or a non-positive total equity, yields no
    cache rather than a cache that is quietly wrong about what is held. The
    marks loop reads "no cache" as "nothing to mark yet", which renders as the
    honest no-marks-yet em-dash — never as a fabricated flat line.
    """
    total = sum(equities.values())
    if total <= 0:
        return None
    weights: dict[str, float] = {}
    ref_prices: dict[str, float] = {}
    for sym, equity in equities.items():
        frame = panel.frames.get(sym)
        if frame is None:
            return None
        close = _dated_closes(frame).get(as_of)
        if close is None or close <= 0:
            return None
        weights[sym] = equity / total
        ref_prices[sym] = close
    return PositionSet(as_of=as_of, weights=weights, ref_prices=ref_prices)


def create_deployment(
    session,
    *,
    strategy_id: str,
    spec_dict: dict,
    owner_wallet: str | None,
    owner_user_id: str | None = None,
    deployed_at: date | None = None,
) -> PaperDeployment:
    """Snapshot the spec and open a deployment. The snapshot is the contract:
    later regeneration of the strategy must not change what this ledger grades."""
    spec = validate_strategy_spec(spec_dict)  # raises DSLError on junk
    dep = PaperDeployment(
        strategy_id=strategy_id,
        owner_wallet=owner_wallet.lower() if owner_wallet else None,
        owner_user_id=owner_user_id,
        spec_json=json.dumps(spec_dict, sort_keys=True),
        deployed_at=deployed_at or datetime.now(UTC).date(),
        status=STATUS_ACTIVE,
    )
    session.add(dep)
    session.flush()
    logger.info("paper: deployed %s for strategy %s (spec=%s)", dep.id, strategy_id, spec.name)
    return dep


def _decision_parts(dep_id: str, decision_date: date, decision) -> tuple[list[dict], dict, dict]:
    """Unpack one replayed decision, or fail with a message that names it.

    The payload gained its two deployment-scoped portfolio snapshots when the
    per-leg ones were found to under-report a multi-sleeve deployment's cash
    and to omit its untraded symbols. Both are hashed fields, so a caller that
    supplies only legs must be rejected here rather than silently producing a
    trace with an empty or half-formed portfolio.
    """
    if not isinstance(decision, dict):
        raise PaperReplayError(
            f"deployment {dep_id} decision {decision_date}: expected "
            f"{{'legs', 'portfolio_before', 'portfolio_after'}}, got {type(decision).__name__}"
        )
    missing = [key for key in ("legs", "portfolio_before", "portfolio_after") if key not in decision]
    if missing:
        raise PaperReplayError(f"deployment {dep_id} decision {decision_date}: decision payload is missing {missing}")
    return decision["legs"], decision["portfolio_before"], decision["portfolio_after"]


def _publish_decision_traces(
    session,
    dep: PaperDeployment,
    spec_dict: dict,
    decisions: dict[date, dict],
    already_ledgered: set[date],
) -> dict:
    """Record a trace for every paper decision, and a durable row for every
    decision that did NOT get one (#1575 §3, §7).

    Called BEFORE the ledger append, deliberately. Paper has no broadcast, but
    it has an equivalent irreversible moment: the ledger row becoming part of
    the user's track record. The ledger is append-only and is never rewritten,
    so recording the reasoning first is the strongest temporal claim paper can
    honestly make — off-chain ordering inside one process, not a block-number
    proof, and the UI copy must say so.

    Never raises on a publish failure. A Redis outage must not freeze every
    user's paper ledger; the ledger is the honest number of record and it keeps
    advancing. The gap is loud instead: a durable row, a stamp on the
    deployment, a log line, and a count on the API. The one thing that DOES
    raise is the coverage accounting identity below — a decision that falls out
    of the pipeline uncounted is the failure mode that produces a silent zero.
    """
    from archimedes.models.paper_store import (
        TRACE_PUBLISHED,
        TRACE_RETRYABLE,
        PaperDecisionTrace,
    )
    from archimedes.services import paper_trace as pt
    from archimedes.services.strategy_dsl import validate_strategy_spec

    counts = dict.fromkeys(("published", "failed", "unowned", "disabled"), 0)
    detected = len(decisions)
    if not detected:
        return {"decisions": 0, "trace_drift": 0, **counts}

    spec = validate_strategy_spec(spec_dict)
    rows = {
        row.decision_date: row
        for row in session.query(PaperDecisionTrace).filter(PaperDecisionTrace.deployment_id == dep.id)
    }
    paper_hashes = pt.resolve_paper_hashes(list(spec.source_arxiv_ids))

    budget = pt.backfill_max()
    attempted = 0
    trace_drift = 0
    now = datetime.now(UTC)

    for decision_date in sorted(decisions):
        legs, portfolio_before, portfolio_after = _decision_parts(dep.id, decision_date, decisions[decision_date])
        row = rows.get(decision_date)

        if row is not None and row.status == TRACE_PUBLISHED:
            # Already traced. Re-derive the hash with the provenance that was
            # hashed into the stored trace: a mismatch means the replay now
            # decides differently for a date whose reasoning is already
            # published. The trace is NOT rewritten — the hash is the point —
            # so this is counted and stamped, exactly like the ledger's drift.
            rebuilt = pt.build_paper_trace(
                deployment_id=dep.id,
                strategy_id=dep.strategy_id,
                spec=spec,
                spec_dict=spec_dict,
                decision_date=decision_date,
                legs=legs,
                portfolio_before=portfolio_before,
                portfolio_after=portfolio_after,
                provenance=row.provenance or pt.PROVENANCE_SETTLE,
                paper_hashes=paper_hashes,
            )
            if row.trace_hash and rebuilt.trace_hash != row.trace_hash:
                trace_drift += 1
            counts["published"] += 1
            continue

        if row is not None and row.status not in TRACE_RETRYABLE:
            # `unowned` — a data problem, not a transient one. Retrying it
            # every settle would turn a loud ERROR into a recurring one that
            # operators learn to ignore.
            #
            # Same explicit-membership bucketing as the publish branch below,
            # and for the same reason: `counts[row.status] += 1` on a row
            # carrying a status outside the four buckets dies on a bare
            # KeyError deep in a loop that says nothing about which decision
            # was lost. Leaving it UNCOUNTED routes it to the accounting
            # identity, which raises with the decision key and the full bucket
            # breakdown. A stored row can hold anything — a hand-edited row, a
            # half-run migration, an older writer's vocabulary — so this is a
            # read of untrusted data, not of a local variable.
            if row.status in counts:
                counts[row.status] += 1
            else:
                logger.error(
                    "paper: deployment %s decision %s carries unrecognised trace status %r on a stored row — "
                    "deliberately left uncounted so the coverage identity catches it.",
                    dep.id,
                    decision_date,
                    row.status,
                )
            continue

        if attempted >= budget:
            # Bounded backfill. The remainder is recorded as a durable,
            # retryable gap rather than silently dropped: an untraced decision
            # with no row would be invisible to trace_coverage, which is the
            # silent zero this whole section exists to prevent.
            status, error = "failed", f"deferred — {pt.BACKFILL_MAX_ENV}={budget} reached this settle"
            trace_id = trace_hash = provenance = None
        else:
            attempted += 1
            # "settle" only when the reasoning genuinely precedes the ledger
            # row it explains. A date the ledger already carries is a backfill,
            # and says so INSIDE the hash — a backfilled trace can never be
            # laundered into a real-time one, because stripping the label
            # breaks /verify.
            provenance = pt.PROVENANCE_BACKFILL if decision_date in already_ledgered else pt.PROVENANCE_SETTLE
            trace = pt.build_paper_trace(
                deployment_id=dep.id,
                strategy_id=dep.strategy_id,
                spec=spec,
                spec_dict=spec_dict,
                decision_date=decision_date,
                legs=legs,
                portfolio_before=portfolio_before,
                portfolio_after=portfolio_after,
                provenance=provenance,
                paper_hashes=paper_hashes,
            )
            status, error = pt.publish_paper_trace(dep, trace)
            trace_id = trace.id if status == TRACE_PUBLISHED else None
            trace_hash = trace.trace_hash if status == TRACE_PUBLISHED else None
            if status != TRACE_PUBLISHED:
                provenance = None

        # Deliberately NOT `counts[status] += 1`. An unrecognised status must
        # fall THROUGH to the accounting identity below and raise there, with
        # the decision key and the full bucket breakdown in the message —
        # rather than dying on a KeyError deep in a loop that says nothing
        # about which decision was lost. The identity is the guard; this line
        # is what lets it do its job.
        if status in counts:
            counts[status] += 1
        else:
            logger.error(
                "paper: deployment %s decision %s produced unrecognised publish status %r — "
                "it is deliberately left uncounted so the coverage identity catches it.",
                dep.id,
                decision_date,
                status,
            )
        if row is None:
            session.add(
                PaperDecisionTrace(
                    deployment_id=dep.id,
                    decision_date=decision_date,
                    trace_id=trace_id,
                    trace_hash=trace_hash,
                    status=status,
                    provenance=provenance,
                    error=error,
                )
            )
        else:
            row.trace_id, row.trace_hash, row.status, row.provenance, row.error = (
                trace_id,
                trace_hash,
                status,
                provenance,
                error,
            )
            row.updated_at = now

    # THE ACCOUNTING IDENTITY. Every detected decision must land in exactly one
    # bucket. A mismatch means a decision fell out of the pipeline without
    # being counted — the shape that produces "0 traces" on a page that claims
    # full coverage — so it raises rather than logging.
    accounted = sum(counts.values())
    if accounted != detected:
        raise PaperTraceCoverageError(
            f"deployment {dep.id}: {detected} decisions detected but {accounted} accounted for "
            f"({counts}) — a decision left the pipeline uncounted"
        )

    if counts["published"] < detected:
        dep.trace_gap_at = now
    if trace_drift:
        dep.trace_drift_at = now
        logger.warning(
            "paper: deployment %s — a fresh replay decides differently on %d date(s) whose trace is "
            "already published. The trace is NOT rewritten (the hash is the point) and the deployment "
            "is stamped trace_drift_at. Same two causes as ledger drift: an upstream data restatement "
            "or a change to the GRADED path's own replay behavior between runs.",
            dep.id,
            trace_drift,
        )
    if counts["failed"] or counts["unowned"] or counts["disabled"]:
        logger.warning(
            "paper: deployment %s trace coverage GAP — %d of %d decisions have no published trace "
            "(failed=%d unowned=%d disabled=%d). Surfaced on GET /api/paper/deployments/%s as "
            "trace_coverage; retryable states are re-attempted next settle.",
            dep.id,
            detected - counts["published"],
            detected,
            counts["failed"],
            counts["unowned"],
            counts["disabled"],
            dep.id,
        )

    return {"decisions": detected, "trace_drift": trace_drift, **counts}


def advance_deployment(session, dep: PaperDeployment, *, replay=None, decision_replay=None) -> dict:
    """Append the replay's NEW dates to the ledger; detect (never repair) drift;
    publish a reasoning trace for every decision the replay made (#1575).

    Returns the append/drift counts plus ``decisions``, ``published``,
    ``failed``, ``unowned``, ``disabled`` and ``trace_drift``.

    ``replay`` resolves at CALL time (module attribute), not in the signature:
    a def-time default freezes the original function and silently defeats any
    monkeypatch — which in a hermetic test means the "stub" quietly hits the
    real network path. Found by this module's own isolation test.

    ``replay`` / ``decision_replay`` inject the two halves separately for
    tests; with neither, ONE ``replay_spec_with_decisions`` pass produces both,
    so the trace and the ledger row for a date can never come from different
    runs of the same spec.
    """
    spec_dict = json.loads(dep.spec_json)
    if replay is None and decision_replay is None:
        replayed, decisions = replay_spec_with_decisions(spec_dict, dep.deployed_at)
    else:
        replayed = (replay or replay_spec)(spec_dict, dep.deployed_at)
        decisions = (decision_replay or (lambda *_: {}))(spec_dict, dep.deployed_at)

    existing = {
        row.date: row.daily_return
        for row in session.query(PaperDailyReturn).filter(PaperDailyReturn.deployment_id == dep.id)
    }

    # Reasoning first, ledger second (§3): the return a trace explains must not
    # become part of the user's track record before the reasoning is recorded.
    trace_result = _publish_decision_traces(session, dep, spec_dict, decisions, set(existing))

    drift = 0
    appended = 0
    for d, r in sorted(replayed.items()):
        if d in existing:
            if abs(existing[d] - r) > _DRIFT_EPS:
                drift += 1
        else:
            session.add(PaperDailyReturn(deployment_id=dep.id, date=d, daily_return=r))
            appended += 1

    if drift:
        dep.drift_detected_at = datetime.now(UTC)
        logger.warning(
            "paper: deployment %s — fresh replay disagrees with %d already-written ledger row(s). "
            "Two known causes produce this identical signature: (1) upstream data restatement "
            "(yfinance revised a historical bar) or (2) a change to the GRADED path's own replay "
            "behavior between runs (cost model, commission, slippage, or interpreter semantics) — "
            "replay_spec calls the same run_dsl_backtest the grader uses, by design, so a grading-side "
            "change moves every open deployment's history along with it. This log cannot distinguish "
            "the two; do not assume upstream data without checking whether the graded path changed. "
            "The ledger is append-only and was NOT rewritten; the deployment is stamped "
            "drift_detected_at so the discrepancy is surfaced, not hidden.",
            dep.id,
            drift,
        )
    session.flush()
    _refresh_position_cache(session, dep, getattr(replayed, "positions", None))
    return {"appended": appended, "drift": drift, **trace_result}


def _refresh_position_cache(session, dep: PaperDeployment, positions: PositionSet | None) -> None:
    """Stamp the position set the marks loop will price (intraday design §4.1).

    Called at the end of every advance, with whatever the replay produced.
    A ``None`` (a plain-dict replay stub, or a replay that could not read
    reference closes) leaves ANY EXISTING CACHE IN PLACE rather than clearing
    it: a stale-by-one-day cache still prices the position the ledger last
    settled, whereas a cleared one makes a working deployment's live value
    vanish. Neither can corrupt the ledger — this function only ever writes
    the two cache columns.

    The equity index is computed from the ledger, not from the replay, so the
    intraday value is anchored to the SAME number ``deployment_summary``
    renders as the settled total return. A mark is that anchor times the
    weighted price move; if the two disagreed, the intraday line would appear
    to jump at every daily advance.
    """
    if positions is None:
        return
    equity = 1.0
    for row in (
        session.query(PaperDailyReturn)
        .filter(PaperDailyReturn.deployment_id == dep.id, PaperDailyReturn.date <= positions.as_of)
        .order_by(PaperDailyReturn.date.asc())
    ):
        equity *= 1.0 + row.daily_return
    dep.position_cache_json = positions.to_json(equity)
    dep.position_cache_at = datetime.now(UTC)
    session.flush()


def advance_all(session) -> dict:
    """Advance every active deployment. Per-deployment failures are isolated
    and counted — one bad universe must not stall everyone else's ledger."""
    deps = session.query(PaperDeployment).filter(PaperDeployment.status == STATUS_ACTIVE).all()
    ok = failed = appended = coverage_broken = 0
    traces = dict.fromkeys(("decisions", "published", "failed", "unowned", "disabled"), 0)
    for dep in deps:
        try:
            result = advance_deployment(session, dep)
            appended += result["appended"]
            for key in traces:
                traces[key] += result.get(key, 0)
            ok += 1
        except (PaperReplayError, DSLError) as exc:
            failed += 1
            logger.warning("paper: advance failed for %s: %s", dep.id, exc)
        except PaperTraceCoverageError:
            # Ahead of the bare `except Exception` on purpose. Swept in with
            # everything else this logged "advance crashed", which reads as one
            # deployment's bad data — the ordinary case below. It is not: a
            # broken identity means the coverage numbers this product publishes
            # about its own provenance are wrong, and that is a bug in this
            # module. Distinct literal, ERROR, and its own counter on the cycle
            # summary. The loop still continues: one deployment's broken
            # accounting must not stall everyone else's ledger, which is the
            # same isolation rule the other two handlers follow.
            failed += 1
            coverage_broken += 1
            logger.error("%s for deployment %s", COVERAGE_BROKEN_LOG, dep.id, exc_info=True)
        except Exception:
            failed += 1
            logger.exception("paper: advance crashed for %s", dep.id)
    return {
        "deployments": len(deps),
        "ok": ok,
        "failed": failed,
        "appended": appended,
        "decisions": traces["decisions"],
        "traces_published": traces["published"],
        # Named separately from the deployment-level "failed" above, which
        # counts advances that blew up. A cycle summary that conflated the two
        # would report a healthy advance as a trace gap and vice versa.
        "trace_failed": traces["failed"],
        "trace_unowned": traces["unowned"],
        "trace_disabled": traces["disabled"],
        # Deployments whose coverage identity broke. Non-zero is a code bug,
        # not an outage, and it is reported separately from `failed` so it
        # cannot be read as "a universe had bad data today".
        "coverage_broken": coverage_broken,
    }


def trace_coverage(session, dep: PaperDeployment) -> dict:
    """What fraction of this deployment's decisions actually got a trace.

    ``status`` is NEVER derived from a bare ``published > 0``: a deployment
    with any non-published decision reports ``"gap"``, so the UI renders
    "2 of 14 decisions have no published trace" rather than a hidden
    discrepancy or a blank panel. ``disabled`` outranks ``gap`` because the
    cause is an operator switch, not a transient failure, and naming it is
    what makes the switch findable.

    ``kinds`` states the v1 limit at the surface: acted decisions are traced;
    a rebalance-eligible bar where the condition did not fire produces no
    order, so an order observer cannot see it (#1575 §1.4).
    """
    from archimedes.models.paper_store import PaperDecisionTrace
    from archimedes.services import paper_trace

    rows = session.query(PaperDecisionTrace).filter(PaperDecisionTrace.deployment_id == dep.id).all()
    counts = dict.fromkeys(("published", "failed", "unowned", "disabled"), 0)
    # A stored status outside the four buckets was previously dropped on the
    # floor here: `decisions` counted it, no bucket did, and the payload came
    # out with totals that silently did not add up — the same silent-zero shape
    # the accounting identity exists to prevent, arriving on the read side.
    # This is a READ path feeding GET /api/paper/deployments, so it reports the
    # discrepancy instead of raising: a 500 would take the whole (correct)
    # ledger down with it. The count is on the payload and the log is an ERROR.
    unknown = [row.status for row in rows if row.status not in counts]
    for row in rows:
        if row.status in counts:
            counts[row.status] += 1
    if unknown:
        logger.error(
            "paper: deployment %s has %d decision-trace row(s) carrying unrecognised status(es) %s — "
            "reported as trace_coverage.unknown and counted as a gap, never dropped from the totals.",
            dep.id,
            len(unknown),
            sorted(set(unknown)),
        )
    gaps = sorted(row.decision_date for row in rows if row.status != "published")
    if counts["disabled"]:
        status = "disabled"
    elif gaps:
        status = "gap"
    else:
        status = "ok"
    return {
        "status": status,
        "decisions": len(rows),
        **counts,
        # `decisions == published + failed + unowned + disabled + unknown`
        # holds by construction. Without this key it did not, and nothing said so.
        "unknown": len(unknown),
        "first_gap_at": gaps[0].isoformat() if gaps else None,
        "kinds": list(paper_trace.DECISION_KINDS),
        "gap_at": dep.trace_gap_at.isoformat() if dep.trace_gap_at else None,
        "drift_at": dep.trace_drift_at.isoformat() if dep.trace_drift_at else None,
    }


def deployment_summary(session, dep: PaperDeployment) -> dict:
    rows = (
        session.query(PaperDailyReturn)
        .filter(PaperDailyReturn.deployment_id == dep.id)
        .order_by(PaperDailyReturn.date.asc())
        .all()
    )
    equity = 1.0
    series = []
    for row in rows:
        equity *= 1.0 + row.daily_return
        series.append({"date": row.date.isoformat(), "daily_return": row.daily_return, "equity_index": equity})
    # The latest intraday mark rides along so the ledger card can render a
    # live value without a second round trip per deployment. It is ALWAYS a
    # separate key from `total_return`, never folded into it: `total_return`
    # is the settled track record and a mark is an unsettled decoration with a
    # TTL. `None` when this deployment has no mark yet — a real state (a
    # deployment created between ticks, or one on SPY before the open), and
    # the UI must render it as an em-dash with a reason rather than +0.00%.
    from archimedes.services.paper_marks import latest_mark, mark_to_dict

    newest = latest_mark(session, dep.id)
    return {
        "deployment_id": dep.id,
        "strategy_id": dep.strategy_id,
        "deployed_at": dep.deployed_at.isoformat(),
        "status": dep.status,
        "days": len(rows),
        "total_return": equity - 1.0,
        "drift_detected_at": dep.drift_detected_at.isoformat() if dep.drift_detected_at else None,
        # The provenance claim, reported alongside the numbers it explains
        # (#1575 §7). A gap here is the honest degraded state; a page that
        # rendered performance without it would be asserting coverage it does
        # not have.
        "trace_coverage": trace_coverage(session, dep),
        "series": series,
        "latest_mark": mark_to_dict(newest) if newest is not None else None,
    }


async def paper_advance_loop() -> None:
    """Long-lived task: advance every active ledger once per interval.

    Same fail-soft contract as backtest_refresh_loop: a bad cycle logs and
    retries next tick; it must never take the app down. Interval defaults to
    daily — the ledger law makes extra runs harmless (idempotent appends).
    """
    import asyncio
    import os

    from archimedes.db import get_session, init_db

    delay = float(os.getenv("PAPER_ADVANCE_STARTUP_DELAY_S", "240"))
    interval_s = float(os.getenv("PAPER_ADVANCE_INTERVAL_HOURS", "24")) * 3600.0
    await asyncio.sleep(delay)
    while True:
        try:
            init_db()

            def _run() -> dict:
                with get_session() as session:
                    summary = advance_all(session)
                    session.commit()
                    return summary

            summary = await asyncio.to_thread(_run)
            logger.info("paper advance: %s", summary)
        except Exception as exc:
            logger.warning("paper advance: cycle failed (%s: %s) — will retry next tick", type(exc).__name__, exc)
        await asyncio.sleep(interval_s)
