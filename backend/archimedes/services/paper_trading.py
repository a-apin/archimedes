"""Paper trading: forward-run the GRADED engine, one appended bar per day.

Why replay-then-append rather than a persisted position state machine: the
backtest engine is a position FSM, so "today's" paper bar depends on every
bar since deploy. Re-running the full replay daily is cheap (one strategy,
daily bars), needs no serialized broker state, and is deterministic given the
data. The LEDGER is still append-only — the replay only ever contributes
dates the ledger has not seen. When a replay DISAGREES with rows already
written (an upstream data restatement — yfinance revises history), the ledger
is NOT rewritten: the drift is counted, logged loudly, and stamped on the
deployment. A track record that silently rewrites itself is the exact failure
this product exists to oppose.

Replay = the same calls the grading path uses (``fetch_real_panel`` →
``feed_factory`` → per-sleeve ``run_dsl_backtest`` with fusion's
dollar-sleeve aggregation), so paper semantics track graded semantics by
construction — including F1's momentum convention and whatever lands next,
with the interpreter-parity harness holding the other side.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, date, datetime

from archimedes.models.paper_store import STATUS_ACTIVE, PaperDailyReturn, PaperDeployment
from archimedes.services.strategy_dsl import DSLError, validate_strategy_spec

logger = logging.getLogger(__name__)

# |replayed - ledgered| beyond this is a restatement, not float noise.
_DRIFT_EPS = 1e-9


class PaperReplayError(RuntimeError):
    """Replay could not produce a trustworthy dated series (fail closed)."""


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


def replay_spec(spec_dict: dict, deployed_at: date) -> dict[date, float]:
    """Full-history replay of a deployment's spec; returns {date: portfolio_return}
    for dates >= ``deployed_at``.

    Dollar-sleeve aggregation, faithful to the graded path: each symbol runs
    as an independently-capitalized sleeve; the portfolio return on a date is
    the equity-weighted combination of that date's sleeve returns.
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
    out: dict[date, float] = {}
    for d in common:
        total_before = sum(equities.values())
        for sym in sleeves:
            equities[sym] *= 1.0 + sleeves[sym][d]
        total_after = sum(equities.values())
        if d >= deployed_at and total_before > 0:
            out[d] = total_after / total_before - 1.0
    return out


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
            "paper: deployment %s — fresh replay disagrees with %d already-written ledger row(s) "
            "(upstream data restatement). The ledger is append-only and was NOT rewritten; the "
            "deployment is stamped drift_detected_at so the discrepancy is surfaced, not hidden.",
            dep.id,
            drift,
        )
    session.flush()
    return {"appended": appended, "drift": drift}


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
    return {
        "deployment_id": dep.id,
        "strategy_id": dep.strategy_id,
        "deployed_at": dep.deployed_at.isoformat(),
        "status": dep.status,
        "days": len(rows),
        "total_return": equity - 1.0,
        "drift_detected_at": dep.drift_detected_at.isoformat() if dep.drift_detected_at else None,
        "series": series,
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
