"""Thin boto3 wrapper for S3 artifact storage (KB pipeline outputs + paper PDFs).

Usage:
    from archimedes.services.s3_artifact_store import S3ArtifactStore

    store = S3ArtifactStore()
    store.upload_bytes("embeddings.npy", data)
    data = store.download_bytes("embeddings.npy")
    keys = store.list_keys(prefix="kb/")
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

import boto3
from botocore.exceptions import ClientError

if TYPE_CHECKING:
    from mypy_boto3_s3 import S3Client

logger = logging.getLogger(__name__)

_DEFAULT_ARTIFACTS_BUCKET = "archimedes-corpus-artifacts-prod"
_DEFAULT_PDFS_BUCKET = "archimedes-paper-pdfs-prod"
_DEFAULT_REGION = "us-east-1"


class S3ArtifactStore:
    """S3-backed store for KB pipeline artifacts (embeddings, clusters, KG graphs)."""

    def __init__(
        self,
        bucket: str | None = None,
        region: str | None = None,
    ) -> None:
        self.bucket = bucket or os.environ.get("AWS_S3_ARTIFACTS_BUCKET", _DEFAULT_ARTIFACTS_BUCKET)
        self.region = region or os.environ.get("AWS_REGION", _DEFAULT_REGION)
        self._client: S3Client | None = None

    @property
    def client(self) -> S3Client:
        if self._client is None:
            self._client = boto3.client("s3", region_name=self.region)
        return self._client

    def list_keys(self, prefix: str = "") -> list[str]:
        """List object keys in the bucket, optionally filtered by prefix."""
        try:
            paginator = self.client.get_paginator("list_objects_v2")
            keys: list[str] = []
            for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
                for obj in page.get("Contents", []):
                    keys.append(obj["Key"])
            return keys
        except ClientError as exc:
            logger.error("S3 list_keys failed: %s", exc)
            raise

    def upload_bytes(self, key: str, data: bytes) -> None:
        """Upload raw bytes to S3."""
        self.client.put_object(Bucket=self.bucket, Key=key, Body=data)
        logger.info("Uploaded s3://%s/%s (%d bytes)", self.bucket, key, len(data))

    def download_bytes(self, key: str) -> bytes | None:
        """Download an object as bytes.

        Returns None on a transient AWS error (throttle, timeout, unavailable) so
        the caller can degrade gracefully (e.g. 503) instead of surfacing a 500.
        """
        try:
            resp = self.client.get_object(Bucket=self.bucket, Key=key)
            return resp["Body"].read()
        except ClientError as exc:
            logger.error("S3 download_bytes(s3://%s/%s) failed: %s", self.bucket, key, exc)
            return None

    def exists(self, key: str) -> bool:
        """Check if an object exists."""
        try:
            self.client.head_object(Bucket=self.bucket, Key=key)
            return True
        except ClientError as exc:
            if exc.response["Error"]["Code"] == "404":
                return False
            raise

    def delete(self, key: str) -> None:
        """Delete an object."""
        self.client.delete_object(Bucket=self.bucket, Key=key)
        logger.info("Deleted s3://%s/%s", self.bucket, key)


class S3PdfStore(S3ArtifactStore):
    """S3-backed store for raw paper PDFs."""

    def __init__(self, bucket: str | None = None, region: str | None = None) -> None:
        super().__init__(
            bucket=bucket or os.environ.get("AWS_S3_PDFS_BUCKET", _DEFAULT_PDFS_BUCKET),
            region=region,
        )
