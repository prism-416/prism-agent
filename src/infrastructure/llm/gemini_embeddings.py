from __future__ import annotations

import json
import logging
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from infrastructure.config.settings import Settings

logger = logging.getLogger(__name__)

GEMINI_EMBEDDING_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models"


class GeminiQueryEmbedder:
    """Embeds query text with the same model prism-vector uses for documents.

    Work item embeddings are computed by the vector workers with
    ``gemini-embedding-001`` at 1536 dimensions and RETRIEVAL_DOCUMENT task type;
    queries must use the matching model/dimensions with RETRIEVAL_QUERY. Returns
    None on any failure so semantic search degrades to lexical matching instead
    of failing the action.
    """

    def __init__(self, settings: Settings) -> None:
        self.api_key = settings.gemini_api_key
        self.model = settings.embedding_model
        self.dimensions = settings.embedding_dimensions

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key)

    def embed_query(self, text: str) -> list[float] | None:
        if not self.is_configured or not text.strip():
            return None
        url = f"{GEMINI_EMBEDDING_ENDPOINT}/{self.model}:embedContent?key={self.api_key}"
        body = {
            "model": f"models/{self.model}",
            "content": {"parts": [{"text": text}]},
            "taskType": "RETRIEVAL_QUERY",
            "outputDimensionality": self.dimensions,
        }
        request = Request(
            url,
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=30) as response:
                decoded = json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, TimeoutError, ValueError) as exc:
            logger.warning("Query embedding failed: %s", exc)
            return None
        values = decoded.get("embedding", {}).get("values")
        if not isinstance(values, list) or len(values) != self.dimensions:
            logger.warning(
                "Query embedding returned unexpected shape (len=%s, expected %s).",
                len(values) if isinstance(values, list) else None,
                self.dimensions,
            )
            return None
        return [float(value) for value in values]
