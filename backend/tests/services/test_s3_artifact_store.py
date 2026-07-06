"""Unit tests for S3ArtifactStore — uses mocked boto3 (no real AWS calls)."""

from unittest.mock import MagicMock, patch

import pytest
from archimedes.services.s3_artifact_store import S3ArtifactStore, S3PdfStore


@pytest.fixture
def mock_s3_client():
    """Mock boto3 S3 client."""
    with patch("archimedes.services.s3_artifact_store.boto3") as mock_boto3:
        client = MagicMock()
        mock_boto3.client.return_value = client
        yield client


class TestS3ArtifactStore:
    def test_init_defaults(self):
        store = S3ArtifactStore.__new__(S3ArtifactStore)
        store.__init__()
        assert store.bucket == "archimedes-corpus-artifacts-prod"
        assert store.region == "us-east-1"

    def test_init_custom(self):
        store = S3ArtifactStore(bucket="my-bucket", region="us-east-1")
        assert store.bucket == "my-bucket"
        assert store.region == "us-east-1"

    def test_init_from_env(self, monkeypatch):
        monkeypatch.setenv("AWS_S3_ARTIFACTS_BUCKET", "env-bucket")
        monkeypatch.setenv("AWS_REGION", "ap-southeast-1")
        store = S3ArtifactStore()
        assert store.bucket == "env-bucket"
        assert store.region == "ap-southeast-1"

    def test_list_keys_empty(self, mock_s3_client):
        paginator = MagicMock()
        paginator.paginate.return_value = [{"Contents": []}]
        mock_s3_client.get_paginator.return_value = paginator

        store = S3ArtifactStore(bucket="test-bucket")
        store._client = mock_s3_client
        result = store.list_keys()
        assert result == []

    def test_list_keys_with_objects(self, mock_s3_client):
        paginator = MagicMock()
        paginator.paginate.return_value = [{"Contents": [{"Key": "embeddings.npy"}, {"Key": "clusters.json"}]}]
        mock_s3_client.get_paginator.return_value = paginator

        store = S3ArtifactStore(bucket="test-bucket")
        store._client = mock_s3_client
        result = store.list_keys()
        assert result == ["embeddings.npy", "clusters.json"]

    def test_list_keys_with_prefix(self, mock_s3_client):
        paginator = MagicMock()
        paginator.paginate.return_value = [{"Contents": [{"Key": "kb/embeddings.npy"}]}]
        mock_s3_client.get_paginator.return_value = paginator

        store = S3ArtifactStore(bucket="test-bucket")
        store._client = mock_s3_client
        result = store.list_keys(prefix="kb/")
        assert result == ["kb/embeddings.npy"]
        paginator.paginate.assert_called_once_with(Bucket="test-bucket", Prefix="kb/")

    def test_upload_bytes(self, mock_s3_client):
        store = S3ArtifactStore(bucket="test-bucket")
        store._client = mock_s3_client
        store.upload_bytes("test.bin", b"hello world")
        mock_s3_client.put_object.assert_called_once_with(Bucket="test-bucket", Key="test.bin", Body=b"hello world")

    def test_download_bytes(self, mock_s3_client):
        body_mock = MagicMock()
        body_mock.read.return_value = b"binary data"
        mock_s3_client.get_object.return_value = {"Body": body_mock}

        store = S3ArtifactStore(bucket="test-bucket")
        store._client = mock_s3_client
        result = store.download_bytes("test.bin")
        assert result == b"binary data"

    def test_download_bytes_throttle_returns_none(self, mock_s3_client):
        """A transient AWS throttle degrades to None, not a raised ClientError."""
        from botocore.exceptions import ClientError

        mock_s3_client.get_object.side_effect = ClientError(
            {"Error": {"Code": "ThrottlingException", "Message": "Rate exceeded"}}, "GetObject"
        )
        store = S3ArtifactStore(bucket="test-bucket")
        store._client = mock_s3_client
        assert store.download_bytes("test.bin") is None

    def test_exists_true(self, mock_s3_client):
        mock_s3_client.head_object.return_value = {}
        store = S3ArtifactStore(bucket="test-bucket")
        store._client = mock_s3_client
        assert store.exists("test.bin") is True

    def test_exists_false(self, mock_s3_client):
        from botocore.exceptions import ClientError

        mock_s3_client.head_object.side_effect = ClientError({"Error": {"Code": "404"}}, "HeadObject")
        store = S3ArtifactStore(bucket="test-bucket")
        store._client = mock_s3_client
        assert store.exists("missing.bin") is False

    def test_delete(self, mock_s3_client):
        store = S3ArtifactStore(bucket="test-bucket")
        store._client = mock_s3_client
        store.delete("test.bin")
        mock_s3_client.delete_object.assert_called_once_with(Bucket="test-bucket", Key="test.bin")


class TestS3PdfStore:
    def test_default_bucket(self):
        store = S3PdfStore()
        assert store.bucket == "archimedes-paper-pdfs-prod"

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("AWS_S3_PDFS_BUCKET", "my-pdfs")
        store = S3PdfStore()
        assert store.bucket == "my-pdfs"
