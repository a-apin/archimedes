"""Postgres backfill service for Knowledge Base pipeline artifacts (#1092).

Syncs clusters.json, topics.json, and kg_graph.json into Postgres tables
`papers.cluster_id`, `papers.topic_label`, `kg_entities`, and `kg_relations`.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from archimedes.db import get_session

logger = logging.getLogger(__name__)


def backfill_kb_artifacts(artifact_dir: Path) -> dict[str, int]:
    """Reads KB artifact files and backfills Postgres DB. Returns sync counts."""
    counts = {"papers_updated": 0, "entities": 0, "relations": 0}
    if not artifact_dir.exists():
        logger.warning("kb_backfill: artifact_dir %s does not exist", artifact_dir)
        return counts

    clusters_path = artifact_dir / "clusters.json"
    topics_path = artifact_dir / "topics.json"
    kg_path = artifact_dir / "kg_graph.json"

    # 1. Update PaperRecord clusters & topics
    if clusters_path.exists():
        try:
            cluster_map = json.loads(clusters_path.read_text())
            topic_map = json.loads(topics_path.read_text()) if topics_path.exists() else {}
            
            with get_session() as session:
                from archimedes.models.corpus_store import PaperRecord
                papers = session.query(PaperRecord).all()
                for p in papers:
                    arxiv_id = p.arxiv_id
                    if arxiv_id in cluster_map:
                        cid = int(cluster_map[arxiv_id])
                        p.cluster_id = cid
                        p.topic_label = topic_map.get(str(cid)) or topic_map.get(cid)
                        counts["papers_updated"] += 1
                session.commit()
        except Exception as exc:
            logger.error("kb_backfill: paper cluster update failed: %s", exc)

    # 2. Sync KG Entities & Relations
    if kg_path.exists():
        try:
            kg_data = json.loads(kg_path.read_text())
            nodes = kg_data.get("nodes", [])
            edges = kg_data.get("edges", [])

            with get_session() as session:
                from archimedes.models.kg import KGEntity, KGRelation

                # Upsert Entities
                entity_map = {}
                for node in nodes:
                    name = node.get("name") if isinstance(node, dict) else str(node)
                    if not name:
                        continue
                    existing = session.query(KGEntity).filter_by(canonical_name=name).first()
                    if not existing:
                        entity = KGEntity(canonical_name=name, entity_type=node.get("type", "concept"))
                        session.add(entity)
                        session.flush()
                        entity_map[name] = entity.id
                        counts["entities"] += 1
                    else:
                        entity_map[name] = existing.id

                # Upsert Relations
                for edge in edges:
                    if isinstance(edge, dict):
                        subj_name = edge.get("subject") or edge.get("source")
                        obj_name = edge.get("object") or edge.get("target")
                        subject_id = entity_map.get(subj_name)
                        object_id = entity_map.get(obj_name)
                        
                        if subject_id:
                            rel = KGRelation(
                                paper_arxiv_id=edge.get("arxiv_id", ""),
                                subject_id=subject_id,
                                object_id=object_id,
                                relation=edge.get("relation", "related_to"),
                                confidence=float(edge.get("confidence", 1.0)),
                            )
                            session.add(rel)
                            counts["relations"] += 1

                session.commit()
        except Exception as exc:
            logger.error("kb_backfill: kg graph update failed: %s", exc)

    logger.info("kb_backfill completed: %s", counts)
    return counts
