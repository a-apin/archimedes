"""Thin boto3 wrapper for DynamoDB paper metadata index.

Usage:
    from archimedes.services.dynamodb_paper_index import DynamoDBPaperIndex

    index = DynamoDBPaperIndex()
    index.put_paper({"arxiv_id": "2301.01234", "title": "...", "year": 2023, ...})
    paper = index.get_paper("2301.01234")
    papers = index.query_by_cluster("regime-switching")
"""

from __future__ import annotations

import logging
import os
from decimal import Decimal
from typing import TYPE_CHECKING, Any

import boto3
from botocore.exceptions import ClientError

if TYPE_CHECKING:
    from mypy_boto3_dynamodb import DynamoDBServiceResource

logger = logging.getLogger(__name__)

_DEFAULT_TABLE = "archimedes-papers-index"
_DEFAULT_REGION = "us-east-1"


def _sanitize_for_dynamo(item: dict[str, Any]) -> dict[str, Any]:
    """Convert floats to Decimal (DynamoDB requirement) and strip None values."""
    sanitized = {}
    for k, v in item.items():
        if v is None:
            continue
        if isinstance(v, float):
            sanitized[k] = Decimal(str(v))
        elif isinstance(v, dict):
            sanitized[k] = _sanitize_for_dynamo(v)
        elif isinstance(v, list):
            sanitized[k] = [Decimal(str(x)) if isinstance(x, float) else x for x in v]
        else:
            sanitized[k] = v
    return sanitized


class DynamoDBPaperIndex:
    """DynamoDB-backed paper metadata index (PK: arxiv_id)."""

    def __init__(
        self,
        table_name: str | None = None,
        region: str | None = None,
    ) -> None:
        self.table_name = table_name or os.environ.get("AWS_DYNAMODB_PAPERS_TABLE", _DEFAULT_TABLE)
        self.region = region or os.environ.get("AWS_REGION", _DEFAULT_REGION)
        self._resource: DynamoDBServiceResource | None = None
        self._table = None

    @property
    def table(self):
        if self._table is None:
            if self._resource is None:
                self._resource = boto3.resource("dynamodb", region_name=self.region)
            self._table = self._resource.Table(self.table_name)
        return self._table

    def get_paper(self, arxiv_id: str) -> dict[str, Any] | None:
        """Get a single paper by arxiv_id.

        Returns None if the paper is not found, and also returns None on a
        transient AWS error (throttle, timeout, unavailable) so the caller can
        degrade gracefully (e.g. 503) instead of surfacing a 500.
        """
        try:
            resp = self.table.get_item(Key={"arxiv_id": arxiv_id})
            return resp.get("Item")
        except ClientError as exc:
            logger.error("DynamoDB get_paper(%s) failed: %s", arxiv_id, exc)
            return None

    def put_paper(self, item: dict[str, Any]) -> None:
        """Upsert a paper record. Must contain 'arxiv_id'."""
        if "arxiv_id" not in item:
            raise ValueError("item must contain 'arxiv_id'")
        self.table.put_item(Item=_sanitize_for_dynamo(item))
        logger.debug("Put paper %s", item["arxiv_id"])

    def query_by_cluster(self, cluster_id: str, limit: int = 100) -> list[dict[str, Any]]:
        """Query papers by cluster_id via GSI."""
        try:
            resp = self.table.query(
                IndexName="cluster_id-index",
                KeyConditionExpression="cluster_id = :cid",
                ExpressionAttributeValues={":cid": cluster_id},
                Limit=limit,
            )
            return resp.get("Items", [])
        except ClientError as exc:
            logger.error("DynamoDB query_by_cluster(%s) failed: %s", cluster_id, exc)
            raise

    def query_by_year(self, year: int, limit: int = 100) -> list[dict[str, Any]]:
        """Query papers by year via GSI."""
        try:
            resp = self.table.query(
                IndexName="year-index",
                KeyConditionExpression="#yr = :y",
                ExpressionAttributeNames={"#yr": "year"},
                ExpressionAttributeValues={":y": year},
                Limit=limit,
            )
            return resp.get("Items", [])
        except ClientError as exc:
            logger.error("DynamoDB query_by_year(%d) failed: %s", year, exc)
            raise

    def batch_put_papers(self, items: list[dict[str, Any]]) -> int:
        """Batch-write papers (max 25 per batch per DynamoDB limits)."""
        count = 0
        with self.table.batch_writer() as batch:
            for item in items:
                if "arxiv_id" not in item:
                    continue
                batch.put_item(Item=_sanitize_for_dynamo(item))
                count += 1
        logger.info("Batch-put %d papers to %s", count, self.table_name)
        return count

    def scan_all(self, limit: int = 1000) -> list[dict[str, Any]]:
        """Scan all papers (use sparingly — prefer queries)."""
        items: list[dict[str, Any]] = []
        kwargs: dict[str, Any] = {"Limit": limit}
        while True:
            resp = self.table.scan(**kwargs)
            items.extend(resp.get("Items", []))
            if len(items) >= limit or "LastEvaluatedKey" not in resp:
                break
            kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
        return items[:limit]
