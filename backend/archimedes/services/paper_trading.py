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


def _sleeve_dated_returns(spec, sym: str, factory, frame) -> dict[date, float]:
    """Dated per-bar returns for one sleeve of the deployment's universe.

    backtrader starts ``next()`` (and therefore the TimeReturn series feeding
    ``equity_curve``) only after the largest indicator warmup, so the returns
    list is END-aligned with the feed's bar index. The tail-alignment below is
    guarded by a hard length check — misalignment must fail loudly, because a
    misdated ledger row is worse than no row.
    """
    from archimedes.services.fusion_evaluator import run_dsl_backtest

    metrics = run_dsl_backtest(spec, data_feed_factory=factory, data_source_label=f"paper:{sym}")
    curve = list(metrics.equity_curve or [])
    if len(curve) < 2:
        raise PaperReplayError(f"sleeve {sym}: replay produced no equity path")
    rets = [(curve[i] - curve[i - 1]) / curve[i - 1] for i in range(1, len(curve)) if curve[i - 1] > 0]
    idx = [d.date() if hasattr(d, "date") else d for d in frame.index]
    if len(rets) > len(idx):
        raise PaperReplayError(f"sleeve {sym}: {len(rets)} returns for {len(idx)} bars — alignment broken")
    return dict(zip(idx[-len(rets) :], rets, strict=True))


def _dated_closes(frame) -> dict[date, float]:
    """``{bar date: close}`` for one sleeve's frame — the reference prices the
    dollar-sleeve weights were struck at."""
    idx = [d.date() if hasattr(d, "date") else d for d in frame.index]
    return {d: float(v) for d, v in zip(idx, frame["Close"], strict=True)}


def replay_spec(spec_dict: dict, deployed_at: date) -> ReplayResult:
    """Full-history replay of a deployment's spec; returns {date: portfolio_return}
    for dates >= ``deployed_at``.

    Dollar-sleeve aggregation, faithful to the graded path: each symbol runs
    as an independently-capitalized sleeve; the portfolio return on a date is
    the equity-weighted combination of that date's sleeve returns.

    The return is a ``ReplayResult`` — a ``dict`` in every way that matters to
    the ledger append, plus ``.positions``: the sleeve weights and reference
    closes on the last replayed bar, so the marks loop can price the position
    set without re-running this (§4.1). Falls back to ``positions=None``
    rather than raising if the reference closes cannot be read: a missing
    position cache costs marks, and marks are decoration — it must never cost
    a ledger row.
    """
    from archimedes.services import fusion_market_data

    spec = validate_strategy_spec(spec_dict)
    panel = fusion_market_data.fetch_real_panel(spec.asset_universe)
    if panel is None:
        raise PaperReplayError(f"real data unavailable for universe {spec.asset_universe}")

    sleeves: dict[str, dict[date, float]] = {}
    for sym, frame in panel.frames.items():
        factory = fusion_market_data.feed_factory(frame)
        sleeves[sym] = _sleeve_dated_returns(spec, sym, factory, frame)
    if not sleeves:
        raise PaperReplayError("no sleeves replayed")

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
    return out


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


def advance_deployment(session, dep: PaperDeployment, *, replay=None) -> dict:
    """Append the replay's NEW dates to the ledger; detect (never repair) drift.

    Returns {"appended": n, "drift": n} — drift is overlapping dates where the
    fresh replay disagrees with what the ledger already recorded.

    ``replay`` resolves at CALL time (module attribute), not in the signature:
    a def-time default freezes the original function and silently defeats any
    monkeypatch — which in a hermetic test means the "stub" quietly hits the
    real network path. Found by this module's own isolation test.
    """
    spec_dict = json.loads(dep.spec_json)
    replayed = (replay or replay_spec)(spec_dict, dep.deployed_at)

    existing = {
        row.date: row.daily_return
        for row in session.query(PaperDailyReturn).filter(PaperDailyReturn.deployment_id == dep.id)
    }

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
    return {"appended": appended, "drift": drift}


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
    ok = failed = appended = 0
    for dep in deps:
        try:
            result = advance_deployment(session, dep)
            appended += result["appended"]
            ok += 1
        except (PaperReplayError, DSLError) as exc:
            failed += 1
            logger.warning("paper: advance failed for %s: %s", dep.id, exc)
        except Exception:
            failed += 1
            logger.exception("paper: advance crashed for %s", dep.id)
    return {"deployments": len(deps), "ok": ok, "failed": failed, "appended": appended}


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
