"""Paper / corpus browser endpoints — /api/papers/*."""

from __future__ import annotations

import json

from fastapi import APIRouter, Query

from archimedes.db import get_session
from archimedes.services.corpus_categories import label_for as _category_label

papers_router = APIRouter(prefix="/api/papers", tags=["papers"])


@papers_router.get("/")
async def list_papers(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    category: str | None = None,
    search: str | None = None,
):
    """Paginated corpus catalog. DB-backed with file fallback."""
    from archimedes.models.corpus_store import PaperRecord

    with get_session() as session:
        query = session.query(PaperRecord)

        if category:
            query = query.filter(PaperRecord.categories.contains(category))
        if search:
            pattern = f"%{search}%"
            query = query.filter((PaperRecord.title.ilike(pattern)) | (PaperRecord.abstract.ilike(pattern)))

        total = query.count()
        rows = query.order_by(PaperRecord.published.desc()).offset((page - 1) * page_size).limit(page_size).all()

        papers = [
            {
                "arxiv_id": r.arxiv_id,
                "title": r.title,
                "authors": json.loads(r.authors) if r.authors else [],
                "primary_category": r.primary_category,
                "category_label": _category_label(r.primary_category),
                "categories": json.loads(r.categories) if r.categories else [],
                "published": r.published,
                "abstract": r.abstract[:200] + "..." if len(r.abstract) > 200 else r.abstract,
            }
            for r in rows
        ]

    if total == 0 and not category and not search:
        from archimedes.agents.strategy_fusion import load_corpus

        corpus = load_corpus()
        all_papers = [
            {
                "arxiv_id": p.arxiv_id,
                "title": p.title,
                "authors": list(getattr(p, "authors", []) or []),
                "primary_category": p.primary_category,
                "category_label": _category_label(p.primary_category),
                "categories": list(p.categories),
                "published": p.published,
                "abstract": p.abstract[:200] + "..." if len(p.abstract) > 200 else p.abstract,
            }
            for p in corpus
        ]
        total = len(all_papers)
        start = (page - 1) * page_size
        papers = all_papers[start : start + page_size]

    return {"total": total, "page": page, "page_size": page_size, "papers": papers}


@papers_router.get("/{arxiv_id}")
async def get_paper(arxiv_id: str):
    """Single paper detail + citing strategies (bidirectional provenance)."""
    from fastapi import HTTPException

    from archimedes.models.corpus_store import PaperRecord
    from archimedes.models.strategy_store import strategies_by_paper

    with get_session() as session:
        record = session.query(PaperRecord).filter(PaperRecord.arxiv_id == arxiv_id).first()

    if record is not None:
        citing_strategies = []
        try:
            with get_session() as session:
                records = strategies_by_paper(session, arxiv_id)
                citing_strategies = [
                    {"id": r.id, "name": r.strategy_name, "status": r.status, "method": r.generation_method}
                    for r in records
                ]
        except Exception:
            pass

        return {
            "arxiv_id": record.arxiv_id,
            "title": record.title,
            "authors": json.loads(record.authors) if record.authors else [],
            "primary_category": record.primary_category,
            "category_label": _category_label(record.primary_category),
            "categories": json.loads(record.categories) if record.categories else [],
            "published": record.published,
            "abstract": record.abstract,
            "pdf_url": record.pdf_url,
            "source": record.source,
            "citing_strategies": citing_strategies,
        }

    from archimedes.agents.strategy_fusion import load_corpus

    corpus = load_corpus()
    paper = next((p for p in corpus if p.arxiv_id == arxiv_id), None)
    if paper is None:
        raise HTTPException(status_code=404, detail="Paper not found")

    citing_strategies = []
    try:
        with get_session() as session:
            records = strategies_by_paper(session, arxiv_id)
            citing_strategies = [
                {"id": r.id, "name": r.strategy_name, "status": r.status, "method": r.generation_method}
                for r in records
            ]
    except Exception:
        pass

    return {
        "arxiv_id": paper.arxiv_id,
        "title": paper.title,
        "primary_category": paper.primary_category,
        "category_label": _category_label(paper.primary_category),
        "categories": list(paper.categories),
        "published": paper.published,
        "abstract": paper.abstract,
        "citing_strategies": citing_strategies,
    }


# ── Corpus Overview ──────────────────────────────────────────────


@papers_router.get("/corpus/overview")
async def get_corpus_overview():
    """High-level library breakdown: category mix, year distribution, totals."""
    from collections import Counter

    from sqlalchemy import func

    from archimedes.models.corpus_store import PaperRecord

    with get_session() as session:
        total = session.query(func.count(PaperRecord.arxiv_id)).scalar() or 0

        if total > 0:
            cat_rows = (
                session.query(PaperRecord.primary_category, func.count(PaperRecord.arxiv_id))
                .group_by(PaperRecord.primary_category)
                .order_by(func.count(PaperRecord.arxiv_id).desc())
                .all()
            )
            category_counts = Counter()
            for cat, cnt in cat_rows:
                category_counts[cat] = cnt

            year_rows = (
                session.query(
                    func.substr(PaperRecord.published, 1, 4).label("year"),
                    func.count(PaperRecord.arxiv_id),
                )
                .filter(PaperRecord.published != "")
                .group_by("year")
                .order_by("year")
                .all()
            )
            year_dist = [(yr, cnt) for yr, cnt in year_rows if yr and yr.isdigit()]

            return {
                "total_papers": total,
                "source": "database",
                "categories": [
                    {"name": cat, "label": _category_label(cat), "count": cnt}
                    for cat, cnt in category_counts.most_common(20)
                ],
                "year_distribution": [{"year": yr, "count": cnt} for yr, cnt in year_dist],
            }

    from archimedes.agents.strategy_fusion import load_corpus

    corpus = load_corpus()
    category_counts: Counter = Counter()
    year_counts: Counter = Counter()
    for p in corpus:
        category_counts[p.primary_category] += 1
        for c in p.categories:
            category_counts[c] += 1
        if p.published:
            year = p.published[:4]
            if year.isdigit():
                year_counts[year] += 1

    top_categories = category_counts.most_common(20)
    year_dist = sorted(year_counts.items())

    return {
        "total_papers": len(corpus),
        "source": "file",
        "categories": [{"name": cat, "label": _category_label(cat), "count": cnt} for cat, cnt in top_categories],
        "year_distribution": [{"year": yr, "count": cnt} for yr, cnt in year_dist],
    }


@papers_router.get("/corpus/graph")
async def get_corpus_graph(
    sample: int = 500,
    lod: int = 1,
):
    """SPECTER2-similarity 2D scatter backed by KB pipeline artifacts.

    Reads pre-computed UMAP projection or raw embeddings from S3 / local
    artifact dir. Falls back to metadata-derived graph only when no KB
    artifacts exist (503 with clear error).
    """
    from archimedes.services.kb_artifacts import (
        ArtifactNotFound,
        compute_and_cache_umap_projection,
        load_clusters,
        load_embeddings,
        load_topics,
        load_umap_projection,
    )

    # Try real SPECTER2-backed projection first
    try:
        points = load_umap_projection()
        if points is None:
            ids, embeddings = load_embeddings()
            try:
                clusters = load_clusters()
            except ArtifactNotFound:
                clusters = None
            points = compute_and_cache_umap_projection(ids, embeddings, clusters)

        # Load topics for cluster labels
        try:
            topics = load_topics()
        except ArtifactNotFound:
            topics = {}

        # Sample if requested
        if sample and len(points) > sample:
            import random

            points = random.sample(points, sample)

        cluster_ids = list({p.get("cluster_id") for p in points if p.get("cluster_id")})

        return {
            "status": "specter2",
            "total_papers": len(points),
            "sampled": len(points),
            "cluster_count": len(cluster_ids),
            "topics": topics,
            "points": points,
            "edges": [],  # Graph edges not needed for scatter; UMAP positions encode similarity
        }
    except ArtifactNotFound:
        # No KB artifacts — fall back to metadata-derived graph
        pass

    # --- Metadata-derived fallback ---
    from collections import defaultdict

    from sqlalchemy import func

    from archimedes.models.corpus_store import PaperRecord

    with get_session() as session:
        total = session.query(func.count(PaperRecord.arxiv_id)).scalar() or 0
        if total == 0:
            return {"status": "empty", "nodes": [], "edges": [], "total_papers": 0}

        papers = (
            session.query(
                PaperRecord.arxiv_id,
                PaperRecord.title,
                PaperRecord.categories,
                PaperRecord.cluster_id,
                PaperRecord.topic_label,
            )
            .limit(sample)
            .all()
        )

        nodes = []
        edges = []
        cat_papers = defaultdict(list)

        for p in papers:
            label = p.topic_label or p.primary_category if hasattr(p, "primary_category") else None
            try:
                cats = json.loads(p.categories) if p.categories else []
            except (json.JSONDecodeError, TypeError):
                cats = []
            if not cats:
                cats = ["uncategorized"]

            nodes.append(
                {
                    "id": p.arxiv_id,
                    "title": p.title[:80] if p.title else p.arxiv_id,
                    "cluster": p.cluster_id or cats[0],
                    "label": label,
                    "categories": cats[:3],
                }
            )

            for c in cats[:3]:
                cat_papers[c].append(p.arxiv_id)

        edge_set = set()
        for _cat, pids in cat_papers.items():
            for i in range(min(len(pids), 20)):
                for j in range(i + 1, min(len(pids), 20)):
                    pair = tuple(sorted([pids[i], pids[j]]))
                    if pair not in edge_set:
                        edge_set.add(pair)
                        edges.append(
                            {"source": pair[0], "target": pair[1], "weight": 1, "type": "category_cooccurrence"}
                        )

        return {
            "status": "metadata_derived",
            "note": "Category co-occurrence graph from metadata. Run KB pipeline for SPECTER2-backed similarity.",
            "total_papers": total,
            "sampled": len(nodes),
            "nodes": nodes,
            "edges": edges[:2000],
        }


@papers_router.get("/corpus/kg")
async def get_corpus_kg(
    entity: str | None = None,
    depth: int = 1,
):
    """Knowledge-graph subgraph from S3-backed kg_graph.json artifact.

    When the KB pipeline has run, this reads the real REBEL/SciSpacy KG
    (``kg_graph.json``) and filters by entity name. Falls back to
    metadata-derived author/category KG when no artifact exists.
    """
    from archimedes.services.kb_artifacts import ArtifactNotFound, load_kg_graph

    # Try real KG artifact first
    try:
        kg_data = load_kg_graph()
        all_nodes = kg_data.get("nodes", [])
        all_edges = kg_data.get("edges", [])

        if entity:
            # Filter to entity neighborhood
            entity_lower = entity.lower()
            matching_node_ids = set()
            for n in all_nodes:
                name = (n.get("canonical_name") or n.get("name") or "").lower()
                if entity_lower in name:
                    matching_node_ids.add(n.get("id"))

            # Expand to depth hops
            frontier = set(matching_node_ids)
            visited = set(matching_node_ids)
            for _ in range(depth):
                next_frontier = set()
                for e in all_edges:
                    src = e.get("source") or e.get("subject_id")
                    tgt = e.get("target") or e.get("object_id")
                    if src in frontier and tgt not in visited:
                        next_frontier.add(tgt)
                    if tgt in frontier and src not in visited:
                        next_frontier.add(src)
                visited |= next_frontier
                frontier = next_frontier
                if not frontier:
                    break

            # Filter nodes and edges to the visited subgraph
            filtered_nodes = [n for n in all_nodes if n.get("id") in visited]
            node_ids = {n.get("id") for n in filtered_nodes}
            filtered_edges = []
            for e in all_edges:
                src = e.get("source") or e.get("subject_id")
                tgt = e.get("target") or e.get("object_id")
                if src in node_ids and tgt in node_ids:
                    filtered_edges.append(e)

            return {
                "status": "specter2_kg",
                "total_nodes": len(all_nodes),
                "total_edges": len(all_edges),
                "nodes": filtered_nodes,
                "edges": filtered_edges[:2000],
                "filtered_by": entity,
            }

        return {
            "status": "specter2_kg",
            "total_nodes": len(all_nodes),
            "total_edges": len(all_edges),
            "nodes": all_nodes[:500],
            "edges": all_edges[:2000],
        }
    except ArtifactNotFound:
        # No KG artifact — fall back to metadata-derived KG
        pass

    # --- Metadata-derived fallback ---

    from sqlalchemy import func, or_

    from archimedes.models.corpus_store import PaperRecord

    with get_session() as session:
        total = session.query(func.count(PaperRecord.arxiv_id)).scalar() or 0
        if total == 0:
            return {"status": "empty", "entities": [], "relations": [], "total_papers": 0}

        q = session.query(PaperRecord)
        if entity:
            like = f"%{entity}%"
            q = q.filter(
                or_(
                    PaperRecord.title.ilike(like),
                    PaperRecord.abstract.ilike(like),
                    PaperRecord.authors.ilike(like),
                    PaperRecord.categories.ilike(like),
                )
            )
        papers = q.limit(200).all()

        entities = {}
        relations = []

        for p in papers:
            entities[f"paper:{p.arxiv_id}"] = {
                "type": "paper",
                "id": p.arxiv_id,
                "label": p.title[:100] if p.title else p.arxiv_id,
            }

            try:
                authors = json.loads(p.authors) if p.authors else []
            except (json.JSONDecodeError, TypeError):
                authors = []
            for a in authors[:5]:
                a_key = f"author:{a}"
                if a_key not in entities:
                    entities[a_key] = {"type": "author", "id": a, "label": a}
                relations.append({"source": f"paper:{p.arxiv_id}", "target": a_key, "type": "authored_by"})

            try:
                cats = json.loads(p.categories) if p.categories else []
            except (json.JSONDecodeError, TypeError):
                cats = []
            for c in cats[:3]:
                c_key = f"category:{c}"
                if c_key not in entities:
                    entities[c_key] = {"type": "category", "id": c, "label": _category_label(c) or c}
                relations.append({"source": f"paper:{p.arxiv_id}", "target": c_key, "type": "belongs_to"})

        return {
            "status": "metadata_derived",
            "note": "Author/category KG from metadata. Run KB pipeline for REBEL/SciSpacy KG.",
            "total_papers": total,
            "filtered": len(papers),
            "nodes": list(entities.values()),
            "edges": relations[:2000],
        }
