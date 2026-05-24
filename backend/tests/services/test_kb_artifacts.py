"""Tests for kb_artifacts service and corpus graph/kg endpoints (Issue #152).

Verifies:
  - S3 + local artifact loading with graceful fallback
  - In-memory cache with TTL
  - /api/corpus/graph returns SPECTER2-backed scatter when artifacts exist
  - /api/corpus/graph returns 503 when no artifacts
  - /api/papers/corpus/graph upgrades to SPECTER2 when available
  - /api/papers/corpus/kg reads kg_graph.json when available
  - Metadata-derived fallback when no S3 artifacts
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from archimedes.db import init_db
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _use_tmp_db(tmp_path, monkeypatch):
    """Point the DB at a temp SQLite so we don't pollute the real one."""
    db_path = tmp_path / "test_kb_artifacts.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    init_db()
    yield


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """TestClient with mocked chain client (no testnet calls)."""
    with (
        patch("archimedes.chain.client.chain_client") as mock_chain,
        patch("archimedes.chain.executor.chain_executor") as mock_executor,
    ):
        mock_chain.is_connected = AsyncMock(return_value=False)
        mock_chain.send_transaction = AsyncMock(return_value="0xmock_tx_hash")
        mock_chain.usdc_address = "0x3600000000000000000000000000000000000000"
        mock_chain.synthetic_factory_address = ""
        mock_chain.amm_router_address = "0xd5b829f9d364a8bbe1caf6c8b19cb05371b178f4"
        mock_chain.vault_factory_address = "0xca873414070844aeb98b0bf1051f81969c79cc32"
        mock_chain.reasoning_trace_registry_address = "0x42d8a23edb897cbee203e9fa197eb05ab5106ca6"
        mock_chain.asset_registry_address = "0x2d44550711137916df6175587d17886281a0fbc7"

        mock_executor.execute_swap = AsyncMock(return_value={"tx_hash": "0xmock_swap"})
        mock_executor.get_balance = AsyncMock(return_value=1000000)
        mock_executor.get_portfolio = AsyncMock(return_value={})

        from archimedes.main import app

        tc = TestClient(app)
        yield tc


# ---------------------------------------------------------------------------
# kb_artifacts service tests
# ---------------------------------------------------------------------------


class TestKbArtifactsCache:
    """In-memory cache tests."""

    def test_cache_miss_returns_none(self):
        from archimedes.services.kb_artifacts import _cache_get, invalidate_cache

        invalidate_cache()
        assert _cache_get("nonexistent") is None

    def test_cache_set_then_get(self):
        from archimedes.services.kb_artifacts import (
            _cache_get,
            _cache_set,
            invalidate_cache,
        )

        invalidate_cache()
        _cache_set("test_key", {"hello": "world"})
        assert _cache_get("test_key") == {"hello": "world"}

    def test_cache_expires_after_ttl(self):
        from archimedes.services.kb_artifacts import (
            _cache_get,
            _cache_set,
            invalidate_cache,
        )

        invalidate_cache()
        # Monkey-patch TTL to 0 for instant expiry
        import archimedes.services.kb_artifacts as mod

        original_ttl = mod._CACHE_TTL
        mod._CACHE_TTL = 0
        try:
            _cache_set("expiring", "value")
            # TTL=0 means instant expiry
            time.sleep(0.01)
            assert _cache_get("expiring") is None
        finally:
            mod._CACHE_TTL = original_ttl

    def test_invalidate_clears_all(self):
        from archimedes.services.kb_artifacts import (
            _cache_get,
            _cache_set,
            invalidate_cache,
        )

        _cache_set("a", 1)
        _cache_set("b", 2)
        cleared = invalidate_cache()
        assert cleared == 2
        assert _cache_get("a") is None
        assert _cache_get("b") is None


class TestKbArtifactsLoading:
    """Artifact loading with mocked S3 + local."""

    def test_load_manifest_local_file(self, tmp_path):
        from archimedes.services import kb_artifacts as mod
        from archimedes.services.kb_artifacts import invalidate_cache

        invalidate_cache()
        # Write a local manifest
        manifest = {"run_ts": "2026-05-24T00:00:00Z", "paper_count": 100}
        (tmp_path / "manifest.json").write_text(json.dumps(manifest))

        # Patch artifact dir to tmp_path and disable S3
        with patch.object(mod, "_ARTIFACT_DIR", tmp_path), patch.object(mod, "_S3_BUCKET", ""):
            result = mod.load_manifest()
            assert result["paper_count"] == 100

    def test_load_manifest_not_found_raises(self, tmp_path):
        from archimedes.services import kb_artifacts as mod
        from archimedes.services.kb_artifacts import ArtifactNotFound, invalidate_cache

        invalidate_cache()
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()

        with (
            patch.object(mod, "_ARTIFACT_DIR", empty_dir),
            patch.object(mod, "_S3_BUCKET", ""),
            pytest.raises(ArtifactNotFound, match=r"manifest\.json"),
        ):
            mod.load_manifest()

    def test_load_kg_graph_local_file(self, tmp_path):
        from archimedes.services import kb_artifacts as mod
        from archimedes.services.kb_artifacts import invalidate_cache

        invalidate_cache()
        kg_data = {
            "nodes": [
                {"id": 1, "canonical_name": "momentum", "entity_type": "strategy"},
                {"id": 2, "canonical_name": "volatility", "entity_type": "risk_factor"},
            ],
            "edges": [
                {"source": 1, "target": 2, "relation": "correlates_with"},
            ],
        }
        (tmp_path / "kg_graph.json").write_text(json.dumps(kg_data))

        with patch.object(mod, "_ARTIFACT_DIR", tmp_path), patch.object(mod, "_S3_BUCKET", ""):
            result = mod.load_kg_graph()
            assert len(result["nodes"]) == 2
            assert len(result["edges"]) == 1

    def test_load_embeddings_local_file(self, tmp_path):
        from archimedes.services import kb_artifacts as mod
        from archimedes.services.kb_artifacts import invalidate_cache

        invalidate_cache()
        ids = ["2605.12345", "2605.12346", "2605.12347"]
        (tmp_path / "ids.json").write_text(json.dumps(ids))

        # Write a small numpy array
        import io

        import numpy as np

        emb = np.random.randn(3, 768).astype(np.float32)
        buf = io.BytesIO()
        np.save(buf, emb)
        (tmp_path / "embeddings.npy").write_bytes(buf.getvalue())

        with patch.object(mod, "_ARTIFACT_DIR", tmp_path), patch.object(mod, "_S3_BUCKET", ""):
            loaded_ids, loaded_emb = mod.load_embeddings()
            assert loaded_ids == ids
            assert loaded_emb.shape == (3, 768)

    def test_load_clusters_local_file(self, tmp_path):
        from archimedes.services import kb_artifacts as mod
        from archimedes.services.kb_artifacts import invalidate_cache

        invalidate_cache()
        clusters = {"2605.12345": "cluster_0", "2605.12346": "cluster_1"}
        (tmp_path / "clusters.json").write_text(json.dumps(clusters))

        with patch.object(mod, "_ARTIFACT_DIR", tmp_path), patch.object(mod, "_S3_BUCKET", ""):
            result = mod.load_clusters()
            assert result["2605.12345"] == "cluster_0"

    def test_compute_and_cache_umap_projection(self, tmp_path):
        from archimedes.services.kb_artifacts import (
            compute_and_cache_umap_projection,
            invalidate_cache,
        )

        invalidate_cache()
        import numpy as np

        ids = [f"paper_{i}" for i in range(20)]
        embeddings = np.random.randn(20, 768).astype(np.float32)
        clusters = {f"paper_{i}": f"cluster_{i % 3}" for i in range(20)}

        # PCA fallback (umap-learn may not be installed)
        points = compute_and_cache_umap_projection(ids, embeddings, clusters)
        assert len(points) == 20
        assert "arxiv_id" in points[0]
        assert "x" in points[0]
        assert "y" in points[0]
        assert "cluster_id" in points[0]
        assert points[0]["cluster_id"] == "cluster_0"

    def test_s3_fallback_to_local(self, tmp_path):
        """When S3 is configured but unavailable, falls back to local."""
        from archimedes.services import kb_artifacts as mod
        from archimedes.services.kb_artifacts import invalidate_cache

        invalidate_cache()
        manifest = {"run_ts": "2026-05-24", "status": "ok"}
        (tmp_path / "manifest.json").write_text(json.dumps(manifest))

        # S3 bucket set but no credentials — _get_s3_client returns None
        with (
            patch.object(mod, "_ARTIFACT_DIR", tmp_path),
            patch.object(mod, "_S3_BUCKET", "nonexistent-bucket"),
            patch.object(mod, "_get_s3_client", return_value=None),
        ):
            result = mod.load_manifest()
            assert result["status"] == "ok"


# ---------------------------------------------------------------------------
# API endpoint tests
# ---------------------------------------------------------------------------


class TestCorpusGraphEndpoint:
    """Tests for /api/corpus/graph."""

    @pytest.fixture(autouse=True)
    def _setup(self):
        from archimedes.services.kb_artifacts import invalidate_cache

        invalidate_cache()

    def test_graph_returns_503_when_no_artifacts(self, client):
        from archimedes.services import kb_artifacts as mod

        with patch.object(mod, "_S3_BUCKET", ""), patch.object(mod, "_ARTIFACT_DIR", Path("/nonexistent")):
            resp = client.get("/api/corpus/graph")
            assert resp.status_code == 503
            body = resp.json()
            assert "detail" in body
            detail = body["detail"]
            assert detail.get("error") == "kb_artifact_not_found" or "KB" in str(detail)

    def test_graph_returns_scatter_when_artifacts_exist(self, client, tmp_path):
        import io

        # Write minimal artifacts
        import numpy as np
        from archimedes.services import kb_artifacts as mod

        ids = [f"paper_{i}" for i in range(10)]
        (tmp_path / "ids.json").write_text(json.dumps(ids))
        emb = np.random.randn(10, 768).astype(np.float32)
        buf = io.BytesIO()
        np.save(buf, emb)
        (tmp_path / "embeddings.npy").write_bytes(buf.getvalue())
        (tmp_path / "clusters.json").write_text(json.dumps({f"paper_{i}": f"c{i % 2}" for i in range(10)}))
        (tmp_path / "topics.json").write_text(json.dumps({"c0": {"label": "momentum"}, "c1": {"label": "volatility"}}))

        with patch.object(mod, "_S3_BUCKET", ""), patch.object(mod, "_ARTIFACT_DIR", tmp_path):
            resp = client.get("/api/corpus/graph")
            assert resp.status_code == 200
            body = resp.json()
            assert "points" in body
            assert len(body["points"]) == 10
            assert "topics" in body
            point = body["points"][0]
            assert "arxiv_id" in point
            assert "x" in point
            assert "y" in point
            assert "cluster_id" in point


class TestCorpusKgEndpoint:
    """Tests for /api/papers/corpus/kg."""

    @pytest.fixture(autouse=True)
    def _setup(self):
        from archimedes.services.kb_artifacts import invalidate_cache

        invalidate_cache()

    def test_kg_reads_artifact_when_available(self, client, tmp_path):
        from archimedes.services import kb_artifacts as mod

        kg_data = {
            "nodes": [
                {"id": 1, "canonical_name": "momentum", "entity_type": "strategy"},
                {"id": 2, "canonical_name": "mean reversion", "entity_type": "strategy"},
                {"id": 3, "canonical_name": "volatility", "entity_type": "risk_factor"},
                {"id": 4, "canonical_name": "drawdown", "entity_type": "risk_metric"},
                {"id": 5, "canonical_name": "Sharpe ratio", "entity_type": "metric"},
                {"id": 6, "canonical_name": " Sortino ratio", "entity_type": "metric"},
            ],
            "edges": [
                {"source": 1, "target": 2, "relation": "negatively_correlated"},
                {"source": 1, "target": 3, "relation": "modulated_by"},
                {"source": 2, "target": 3, "relation": "inverse"},
                {"source": 3, "target": 4, "relation": "causes"},
                {"source": 4, "target": 5, "relation": "impacts"},
                {"source": 5, "target": 6, "relation": "related_to"},
            ],
        }
        (tmp_path / "kg_graph.json").write_text(json.dumps(kg_data))

        with patch.object(mod, "_S3_BUCKET", ""), patch.object(mod, "_ARTIFACT_DIR", tmp_path):
            resp = client.get("/api/papers/corpus/kg?entity=momentum")
            assert resp.status_code == 200
            body = resp.json()
            assert body["status"] == "specter2_kg"
            assert len(body["nodes"]) > 0
            assert len(body["edges"]) > 0

    def test_kg_entity_filter_returns_neighborhood(self, client, tmp_path):
        from archimedes.services import kb_artifacts as mod

        kg_data = {
            "nodes": [
                {"id": 1, "canonical_name": "momentum"},
                {"id": 2, "canonical_name": "volatility"},
                {"id": 3, "canonical_name": "Sharpe ratio"},
                {"id": 4, "canonical_name": "Kelly criterion"},
                {"id": 5, "canonical_name": "drawdown"},
                {"id": 6, "canonical_name": "unrelated topic"},
            ],
            "edges": [
                {"source": 1, "target": 2},
                {"source": 2, "target": 3},
                {"source": 3, "target": 4},
                {"source": 4, "target": 5},
                {"source": 1, "target": 6},
            ],
        }
        (tmp_path / "kg_graph.json").write_text(json.dumps(kg_data))

        with patch.object(mod, "_S3_BUCKET", ""), patch.object(mod, "_ARTIFACT_DIR", tmp_path):
            resp = client.get("/api/papers/corpus/kg?entity=momentum&depth=1")
            assert resp.status_code == 200
            body = resp.json()
            node_ids = {n["id"] for n in body["nodes"]}
            # momentum (1) + its direct neighbors (2, 6)
            assert 1 in node_ids
            assert 2 in node_ids
            assert 6 in node_ids

    def test_kg_falls_back_to_metadata_when_no_artifact(self, client, tmp_path):
        from archimedes.services import kb_artifacts as mod

        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()

        with patch.object(mod, "_S3_BUCKET", ""), patch.object(mod, "_ARTIFACT_DIR", empty_dir):
            resp = client.get("/api/papers/corpus/kg")
            # Should return metadata-derived fallback or empty
            assert resp.status_code == 200
            body = resp.json()
            assert body["status"] in ("metadata_derived", "empty")


class TestPapersGraphEndpoint:
    """Tests for /api/papers/corpus/graph."""

    @pytest.fixture(autouse=True)
    def _setup(self):
        from archimedes.services.kb_artifacts import invalidate_cache

        invalidate_cache()

    def test_papers_graph_uses_specter2_when_available(self, client, tmp_path):
        import io

        import numpy as np
        from archimedes.services import kb_artifacts as mod

        ids = [f"paper_{i}" for i in range(15)]
        (tmp_path / "ids.json").write_text(json.dumps(ids))
        emb = np.random.randn(15, 768).astype(np.float32)
        buf = io.BytesIO()
        np.save(buf, emb)
        (tmp_path / "embeddings.npy").write_bytes(buf.getvalue())
        (tmp_path / "clusters.json").write_text(json.dumps({}))
        (tmp_path / "topics.json").write_text(json.dumps({}))

        with patch.object(mod, "_S3_BUCKET", ""), patch.object(mod, "_ARTIFACT_DIR", tmp_path):
            resp = client.get("/api/papers/corpus/graph?sample=10")
            assert resp.status_code == 200
            body = resp.json()
            assert body["status"] == "specter2"
            assert "points" in body
            assert len(body["points"]) <= 10

    def test_papers_graph_falls_back_to_metadata(self, client, tmp_path):
        from archimedes.services import kb_artifacts as mod

        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()

        with patch.object(mod, "_S3_BUCKET", ""), patch.object(mod, "_ARTIFACT_DIR", empty_dir):
            resp = client.get("/api/papers/corpus/graph")
            assert resp.status_code == 200
            body = resp.json()
            # Either metadata_derived or empty (no papers in DB for tests)
            assert body["status"] in ("metadata_derived", "empty")
