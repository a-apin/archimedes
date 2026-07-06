"""Unit tests for DynamoDBPaperIndex — uses mocked boto3 (no real AWS calls)."""

from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
from archimedes.services.dynamodb_paper_index import DynamoDBPaperIndex, _sanitize_for_dynamo


class TestSanitize:
    def test_float_to_decimal(self):
        result = _sanitize_for_dynamo({"sharpe": 1.23, "name": "test"})
        assert result == {"sharpe": Decimal("1.23"), "name": "test"}

    def test_strips_none(self):
        result = _sanitize_for_dynamo({"a": 1, "b": None, "c": "x"})
        assert result == {"a": 1, "c": "x"}

    def test_nested_dict(self):
        result = _sanitize_for_dynamo({"meta": {"score": 0.95}})
        assert result == {"meta": {"score": Decimal("0.95")}}

    def test_list_with_floats(self):
        result = _sanitize_for_dynamo({"vals": [1.1, 2.2]})
        assert result == {"vals": [Decimal("1.1"), Decimal("2.2")]}


@pytest.fixture
def mock_table():
    """Mock DynamoDB table resource."""
    with patch("archimedes.services.dynamodb_paper_index.boto3") as mock_boto3:
        resource = MagicMock()
        table = MagicMock()
        resource.Table.return_value = table
        mock_boto3.resource.return_value = resource
        yield table


class TestDynamoDBPaperIndex:
    def test_init_defaults(self):
        index = DynamoDBPaperIndex.__new__(DynamoDBPaperIndex)
        index.__init__()
        assert index.table_name == "archimedes-papers-index"
        assert index.region == "us-east-1"

    def test_init_from_env(self, monkeypatch):
        monkeypatch.setenv("AWS_DYNAMODB_PAPERS_TABLE", "custom-table")
        monkeypatch.setenv("AWS_REGION", "us-west-2")
        index = DynamoDBPaperIndex()
        assert index.table_name == "custom-table"
        assert index.region == "us-west-2"

    def test_get_paper_found(self, mock_table):
        mock_table.get_item.return_value = {"Item": {"arxiv_id": "2301.01234", "title": "Test Paper"}}
        index = DynamoDBPaperIndex()
        result = index.get_paper("2301.01234")
        assert result == {"arxiv_id": "2301.01234", "title": "Test Paper"}
        mock_table.get_item.assert_called_once_with(Key={"arxiv_id": "2301.01234"})

    def test_get_paper_not_found(self, mock_table):
        mock_table.get_item.return_value = {}
        index = DynamoDBPaperIndex()
        result = index.get_paper("9999.99999")
        assert result is None

    def test_get_paper_throttle_returns_none(self, mock_table):
        """A transient AWS throttle degrades to None, not a raised ClientError."""
        from botocore.exceptions import ClientError

        mock_table.get_item.side_effect = ClientError(
            {"Error": {"Code": "ProvisionedThroughputExceededException", "Message": "Rate exceeded"}},
            "GetItem",
        )
        index = DynamoDBPaperIndex()
        assert index.get_paper("2301.01234") is None

    def test_put_paper(self, mock_table):
        index = DynamoDBPaperIndex()
        index.put_paper({"arxiv_id": "2301.01234", "title": "Test", "sharpe": 1.5})
        mock_table.put_item.assert_called_once_with(
            Item={"arxiv_id": "2301.01234", "title": "Test", "sharpe": Decimal("1.5")}
        )

    def test_put_paper_missing_key(self, mock_table):
        index = DynamoDBPaperIndex()
        with pytest.raises(ValueError, match="arxiv_id"):
            index.put_paper({"title": "No ID"})

    def test_query_by_cluster(self, mock_table):
        mock_table.query.return_value = {"Items": [{"arxiv_id": "2301.01234", "cluster_id": "momentum"}]}
        index = DynamoDBPaperIndex()
        result = index.query_by_cluster("momentum")
        assert len(result) == 1
        assert result[0]["cluster_id"] == "momentum"

    def test_query_by_year(self, mock_table):
        mock_table.query.return_value = {"Items": [{"arxiv_id": "2301.01234", "year": 2023}]}
        index = DynamoDBPaperIndex()
        result = index.query_by_year(2023)
        assert len(result) == 1

    def test_batch_put_papers(self, mock_table):
        batch_writer = MagicMock()
        mock_table.batch_writer.return_value.__enter__ = MagicMock(return_value=batch_writer)
        mock_table.batch_writer.return_value.__exit__ = MagicMock(return_value=False)

        index = DynamoDBPaperIndex()
        count = index.batch_put_papers(
            [
                {"arxiv_id": "2301.00001", "title": "A"},
                {"arxiv_id": "2301.00002", "title": "B"},
                {"title": "No ID — skipped"},
            ]
        )
        assert count == 2
        assert batch_writer.put_item.call_count == 2

    def test_scan_all(self, mock_table):
        mock_table.scan.return_value = {"Items": [{"arxiv_id": f"2301.{i:05d}"} for i in range(5)]}
        index = DynamoDBPaperIndex()
        result = index.scan_all(limit=10)
        assert len(result) == 5
