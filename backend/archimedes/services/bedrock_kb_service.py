"""Optional AWS Bedrock Knowledge Base RAG retrieval bridge (#1093).

Provides a cloud RAG retrieval bridge to AWS Bedrock KB without breaking
local offline SPECTER2/Postgres parity.
"""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)


class BedrockKnowledgeBaseService:
    """Optional Bedrock Knowledge Base RAG retrieval client."""

    def __init__(self) -> None:
        self.enabled = os.getenv("BEDROCK_KB_ENABLED", "0").lower() in ("1", "true", "yes")
        self.kb_id = os.getenv("BEDROCK_KB_ID", "")
        self.region = os.getenv("AWS_REGION", "us-east-1")
        self._client: Any = None

    def _get_client(self) -> Any:
        if self._client is None and self.enabled:
            import boto3
            self._client = boto3.client("bedrock-agent-runtime", region_name=self.region)
        return self._client

    async def retrieve(self, query: str, top_k: int = 5) -> list[dict[str, Any]]:
        """Retrieve relevant paper chunks from Bedrock KB if enabled."""
        if not self.enabled or not self.kb_id:
            return []

        try:
            client = self._get_client()
            response = client.retrieve(
                knowledgeBaseId=self.kb_id,
                retrievalQuery={"text": query},
                retrievalConfiguration={
                    "vectorSearchConfiguration": {
                        "numberOfResults": top_k
                    }
                }
            )
            results = []
            for item in response.get("retrievalResults", []):
                results.append({
                    "content": item.get("content", {}).get("text", ""),
                    "score": item.get("score", 0.0),
                    "location": item.get("location", {}),
                    "metadata": item.get("metadata", {})
                })
            return results
        except Exception as exc:
            logger.warning("Bedrock KB retrieval failed: %s", exc)
            return []


bedrock_kb_service = BedrockKnowledgeBaseService()
