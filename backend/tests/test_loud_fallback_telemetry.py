"""Hermetic coverage for T0.5 loud-fallback telemetry.

Two silent-degradation gaps closed here, each asserted to (a) flip a ``/health``
flag and (b) emit a structured WARN at the fallback site:

1. GMM-unfit — ``GmmRegimeDetector`` with no fitted artifact delegates to the
   rule-based ``VixRegimeDetector``. The detector must report ``degraded`` and
   warn, and ``gmm_regime_health()`` (the ``/health`` probe) must agree.
2. Risk-mock — the Risk Analysis surface renders client-side ``mockReturns``
   when no persisted backtest carries an equity curve. ``risk_data_health()``
   must report ``mock`` and warn; ``live`` when real equity data exists.

No DB / Redis / network / .env — boundaries are mocked (``load_gmm_model`` and
the strategy provider), so the file runs under the hermetic gate:

    env -i HOME=$HOME PATH=$PATH PYTHONPATH=backend \\
        python -m pytest backend/tests/test_loud_fallback_telemetry.py -q
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import archimedes.api.risk_routes as risk_routes
import archimedes.services.gmm_regime_detector as gmm_mod
import numpy as np
import pytest
from archimedes.models.asset import MarketSnapshot
from archimedes.services.gmm_regime_detector import (
    GmmRegimeDetector,
    fit_gmm_model,
    gmm_regime_health,
)
from httpx import ASGITransport, AsyncClient

# A path guaranteed not to exist, so the detector's load returns None and the
# fallback path is taken (no real artifact is ever committed — see module docs).
_ABSENT_MODEL = Path("/nonexistent/gmm_model_does_not_exist.pkl")


def _snapshot(*, vix: float | None = 20.0, spy: float | None = 5000.0) -> MarketSnapshot:
    prices: dict[str, float] = {}
    if spy is not None:
        prices["sSPY"] = spy
    return MarketSnapshot(timestamp=datetime.now(UTC), prices=prices, vix=vix)


def _fitted_model() -> object:
    """A real ``FittedGmm`` from synthetic features — pure, no I/O."""
    rng = np.random.default_rng(0)
    # Four loose clusters in the 4-feature space so labelling has something to do.
    feats = np.vstack(
        [
            rng.normal([15, 0.0, 0.10, 0.05], 1.0, size=(60, 4)),
            rng.normal([22, 0.2, 0.18, -0.02], 1.0, size=(60, 4)),
            rng.normal([30, 0.4, 0.30, -0.08], 1.0, size=(60, 4)),
            rng.normal([45, 0.8, 0.50, -0.20], 1.0, size=(60, 4)),
        ]
    )
    return fit_gmm_model(feats, random_state=42)


# ── Gap 1: GMM-unfit telemetry ───────────────────────────────────────


class TestGmmUnfitTelemetry:
    def test_unfit_detector_reports_degraded(self) -> None:
        det = GmmRegimeDetector(model_path=_ABSENT_MODEL)
        assert det.is_degraded is True
        assert det.health().status == "degraded"
        assert "fallback" in det.health().reason.lower()

    def test_unfit_construction_emits_structured_warn(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.WARNING, logger=gmm_mod.__name__):
            GmmRegimeDetector(model_path=_ABSENT_MODEL)
        recs = [r for r in caplog.records if getattr(r, "event", None) == "gmm_regime_fallback"]
        assert recs, "expected a structured gmm_regime_fallback WARN at construction"
        assert recs[0].levelno == logging.WARNING
        assert recs[0].fallback_reason == "no_fitted_model"

    def test_classify_fallback_warns_once_not_per_tick(self, caplog: pytest.LogCaptureFixture) -> None:
        det = GmmRegimeDetector(model_path=_ABSENT_MODEL)
        with caplog.at_level(logging.WARNING, logger=gmm_mod.__name__):
            for _ in range(5):
                det.classify(_snapshot())
        # Construction already consumed "no_fitted_model"; the classify path
        # dedupes on the same reason, so no *additional* duplicate WARNs spam.
        reasons = [getattr(r, "fallback_reason", None) for r in caplog.records]
        assert reasons.count("no_fitted_model") <= 1

    def test_module_health_probe_reports_degraded_when_unfit(self) -> None:
        # Hold the detector ref so the health probe inspects THIS unfit instance.
        # A dropped ref GCs the weakref, and the probe then falls back to the
        # on-disk DEFAULT_GMM_MODEL_PATH check — non-hermetic when a real
        # gmm_model.pkl exists in the dev tree (CI-green / local-red).
        det = GmmRegimeDetector(model_path=_ABSENT_MODEL)
        assert det.is_degraded is True
        diag = gmm_regime_health()
        assert diag.status == "degraded"

    def test_fitted_detector_reports_live(self) -> None:
        with patch.object(gmm_mod, "load_gmm_model", return_value=_fitted_model()):
            det = GmmRegimeDetector(model_path=_ABSENT_MODEL)
        assert det.is_degraded is False
        assert det.health().status == "live"
        assert gmm_regime_health().status == "live"


# ── Gap 2: risk-mock telemetry ───────────────────────────────────────


def _provider_with(curves: list[list[float]]) -> MagicMock:
    """A fake strategy provider whose backtests carry the given equity curves."""
    provider = MagicMock()
    strategies = []
    by_id: dict[str, MagicMock] = {}
    for i, curve in enumerate(curves):
        sid = f"s{i}"
        strat = MagicMock()
        strat.id = sid
        strategies.append(strat)
        bt = MagicMock()
        bt.equity_curve = curve
        by_id[sid] = bt
    provider.list_strategies.return_value = strategies
    provider.get_backtest_result.side_effect = lambda sid: by_id.get(sid)
    return provider


class TestRiskMockTelemetry:
    def test_no_equity_curves_reports_mock(self, caplog: pytest.LogCaptureFixture) -> None:
        provider = _provider_with([[], [1.0]])  # both too short (< 2 points)
        with (
            # _strategy_provider is now a lazily-cached accessor (_strategy_provider());
            # patch it with a callable returning the pre-configured mock instance.
            patch.object(risk_routes, "_strategy_provider", lambda: provider),
            caplog.at_level(logging.WARNING, logger=risk_routes.__name__),
        ):
            diag = risk_routes.risk_data_health()
        assert diag.status == "mock"
        recs = [r for r in caplog.records if getattr(r, "event", None) == "risk_data_mock"]
        assert recs, "expected a structured risk_data_mock WARN"
        assert recs[0].reason == "no_equity_curves"

    def test_live_equity_curves_report_live_no_warn(self, caplog: pytest.LogCaptureFixture) -> None:
        provider = _provider_with([[1.0, 1.1, 1.2]])  # a real curve (>= 2 points)
        with (
            # _strategy_provider is now a lazily-cached accessor (_strategy_provider());
            # patch it with a callable returning the pre-configured mock instance.
            patch.object(risk_routes, "_strategy_provider", lambda: provider),
            caplog.at_level(logging.WARNING, logger=risk_routes.__name__),
        ):
            diag = risk_routes.risk_data_health()
        assert diag.status == "live"
        assert not [r for r in caplog.records if getattr(r, "event", None) == "risk_data_mock"]

    def test_provider_failure_degrades_to_mock(self, caplog: pytest.LogCaptureFixture) -> None:
        provider = MagicMock()
        provider.list_strategies.side_effect = RuntimeError("db down")
        with (
            # _strategy_provider is now a lazily-cached accessor (_strategy_provider());
            # patch it with a callable returning the pre-configured mock instance.
            patch.object(risk_routes, "_strategy_provider", lambda: provider),
            caplog.at_level(logging.WARNING, logger=risk_routes.__name__),
        ):
            diag = risk_routes.risk_data_health()
        assert diag.status == "mock"
        recs = [r for r in caplog.records if getattr(r, "event", None) == "risk_data_mock"]
        assert recs and recs[0].reason == "probe_failed"


# ── /health surfaces both flags ──────────────────────────────────────


@pytest.mark.asyncio
async def test_health_surfaces_regime_and_risk_flags() -> None:
    """/health exposes regime_detector + risk_data flags alongside paper_rag."""
    from archimedes.main import app

    # Force both subsystems into their degraded states at the boundary.
    degraded_risk = risk_routes.RiskDataHealth(status="mock", reason="test mock")
    # Pin the GMM probe to "unfit" hermetically: no live detector instance AND a
    # guaranteed-absent default artifact path. (Patching load_gmm_model alone is
    # insufficient — the probe's cold path checks DEFAULT_GMM_MODEL_PATH on disk,
    # so a real gmm_model.pkl in the dev tree reports "live": CI-green/local-red.)
    with (
        patch.object(gmm_mod, "_LAST_DETECTOR", None),
        patch.object(gmm_mod, "DEFAULT_GMM_MODEL_PATH", _ABSENT_MODEL),
        patch.object(risk_routes, "risk_data_health", return_value=degraded_risk),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/health")

    assert resp.status_code == 200
    body = resp.json()
    # Existing flags stay intact.
    assert "paper_rag" in body
    # New T0.5 flags present and degraded.
    assert body["regime_detector"] == "degraded"
    assert "regime_detector_reason" in body
    assert body["risk_data"] == "mock"
    assert body["risk_data_reason"] == "test mock"


# ── /health corpus honesty fields (claim-integrity, issue #778) ──────


@pytest.mark.asyncio
async def test_health_surfaces_corpus_honesty_fields() -> None:
    """/health exposes machine-readable corpus honesty fields (issue #778).

    The ``corpus_papers`` / ``corpus_db_count`` counts are manifest-seeded
    *metadata records* — they must not be read as "embedded / graphed". These
    boolean + count fields make the real state explicit. Hermetic: no DB/KG, so
    every field reflects the un-built default (no embeddings, no KG, no artifact).
    """
    from archimedes.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/health")

    assert resp.status_code == 200
    body = resp.json()

    # The honesty fields are present and machine-readable booleans / ints.
    for key in ("paper_rerank_model_live", "corpus_embedded_at_rest", "corpus_kg_built", "corpus_artifact_present"):
        assert key in body, f"missing honesty field {key!r}"
        assert isinstance(body[key], bool)
    for key in ("corpus_kg_entities", "corpus_kg_relations"):
        assert key in body
        assert isinstance(body[key], int)

    # With no KB pipeline run / KG store, every honesty field reads "not built".
    assert body["corpus_kg_built"] is False
    assert body["corpus_kg_entities"] == 0
    assert body["corpus_kg_relations"] == 0
    assert body["corpus_artifact_present"] is False
    # paper_rerank_model_live tracks paper_rag: is a model object loaded in THIS
    # process. It is deliberately no longer named corpus_embedded (#1488) — that
    # name claimed a property of the corpus while carrying this process-local value.
    assert body["paper_rerank_model_live"] == (body.get("paper_rag") == "live")
    # The corpus itself is not embedded at rest, and that does not move with the
    # reranker's liveness.
    assert body["corpus_embedded_at_rest"] is False
    assert body["corpus_embedded_at_rest_reason"]
    assert body["rerank_candidate_cap"] > 0

    # The legacy count keys are untouched (no consumer breakage).
    assert "corpus_papers" in body
    assert "corpus_db_count" in body
