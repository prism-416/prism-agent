from __future__ import annotations

import json
from typing import Any

from application.context_provider import ContextProvider
from domain.actions import PlannedAction
from domain.events import DomainEvent, EventEnvelope
from infrastructure.config.settings import Settings
from infrastructure.llm.gemini_embeddings import GeminiQueryEmbedder
from infrastructure.prism_api.client import PrismApiClient
from infrastructure.registries.prompt_registry import PromptRegistry
from infrastructure.registries.tool_registry import ToolRegistry
from infrastructure.registries.workflow_registry import WorkflowRegistry

DIMENSIONS = 1536


class _FakeResponse:
    def __init__(self, body: bytes) -> None:
        self.body = body

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *args: Any) -> None:
        _ = args

    def read(self) -> bytes:
        return self.body


class _StubEmbedder:
    is_configured = True

    def __init__(self) -> None:
        self.queries: list[str] = []

    def embed_query(self, text: str) -> list[float] | None:
        self.queries.append(text)
        return [0.1] * DIMENSIONS


class _StubSimilarityClient:
    is_configured = True

    def __init__(self, similar: list[dict[str, Any]]) -> None:
        self.similar = similar
        self.calls: list[tuple[str, int]] = []

    def find_similar_work_items(self, project_id, embedding, *, limit=8):
        _ = embedding
        self.calls.append((project_id, limit))
        return self.similar


def _context(prompts_path, payload: dict[str, Any] | None = None):
    event = DomainEvent(
        event_type="story.created",
        workspace_id="w1",
        project_id="p1",
        payload=payload or {"entity_versions": {"story:s1": 1}},
    )
    prompt_registry = PromptRegistry(prompts_path)
    workflow = WorkflowRegistry.from_prompt_registry(prompt_registry).get("story.decompose")
    return (
        ContextProvider(PrismApiClient(), prompt_registry)
        .hydrate(EventEnvelope.wrap(event), workflow)
        .context
    )


def _action(input_data: dict[str, Any]) -> PlannedAction:
    return PlannedAction(
        plan_id="plan-1",
        action_type="analysis",
        tool_name="find_duplicate_workitems",
        instruction="Check for duplicates.",
        input=input_data,
        idempotency_key="k1",
    )


def test_embedder_returns_vector(monkeypatch) -> None:
    captured = {}

    def fake_urlopen(request, timeout):
        _ = timeout
        captured["url"] = request.full_url
        captured["body"] = json.loads(request.data.decode("utf-8"))
        return _FakeResponse(
            json.dumps({"embedding": {"values": [0.5] * DIMENSIONS}}).encode("utf-8")
        )

    monkeypatch.setattr("infrastructure.llm.gemini_embeddings.urlopen", fake_urlopen)
    embedder = GeminiQueryEmbedder(Settings(gemini_api_key="test-key"))

    vector = embedder.embed_query("saved views api")

    assert vector == [0.5] * DIMENSIONS
    assert "gemini-embedding-001:embedContent" in captured["url"]
    assert captured["body"]["taskType"] == "RETRIEVAL_QUERY"
    assert captured["body"]["outputDimensionality"] == DIMENSIONS


def test_embedder_degrades_to_none(monkeypatch) -> None:
    assert GeminiQueryEmbedder(Settings()).embed_query("text") is None

    def bad_shape(request, timeout):
        _ = (request, timeout)
        return _FakeResponse(json.dumps({"embedding": {"values": [0.5] * 3}}).encode("utf-8"))

    monkeypatch.setattr("infrastructure.llm.gemini_embeddings.urlopen", bad_shape)
    assert GeminiQueryEmbedder(Settings(gemini_api_key="k")).embed_query("text") is None


def test_client_posts_similar_search(monkeypatch) -> None:
    captured = {}

    def fake_urlopen(request, timeout):
        _ = timeout
        captured["url"] = request.full_url
        captured["body"] = json.loads(request.data.decode("utf-8"))
        return _FakeResponse(
            json.dumps({"data": [{"itemId": "i1", "title": "Existing", "similarity": 0.9}]}).encode(
                "utf-8"
            )
        )

    monkeypatch.setattr("infrastructure.prism_api.client.urlopen", fake_urlopen)
    client = PrismApiClient("https://api.example.test", "token")

    items = client.find_similar_work_items("p1", [0.1] * DIMENSIONS, limit=5)

    assert captured["url"] == "https://api.example.test/projects/p1/work-items/internal/similar"
    assert captured["body"]["limit"] == 5
    assert len(captured["body"]["embedding"]) == DIMENSIONS
    assert items == [{"itemId": "i1", "title": "Existing", "similarity": 0.9}]


def test_duplicate_tool_prefers_semantic_matches(prompts_path) -> None:
    client = _StubSimilarityClient(
        [
            {"itemId": "i1", "title": "Notification center", "similarity": 0.91},
            {"itemId": "i2", "title": "Vaguely related", "similarity": 0.41},
            {"itemId": "s1", "title": "Source story", "similarity": 0.99},
        ]
    )
    embedder = _StubEmbedder()
    registry = ToolRegistry.from_prompt_registry(
        PromptRegistry(prompts_path), prism_client=client, embedder=embedder
    )
    tool = registry.get("find_duplicate_workitems")
    context = _context(
        prompts_path,
        {"entity_versions": {"story:s1": 1}, "work_item": {"itemId": "s1", "title": "Source"}},
    )

    result = tool.execute(
        _action({"projectId": "p1", "title": "Notification center", "description": "Bell icon"}),
        context,
    )

    assert result.success is True
    assert result.output["method"] == "semantic"
    assert [d["itemId"] for d in result.output["duplicates"]] == ["i1"]
    assert embedder.queries == ["Notification center\nBell icon"]
    assert client.calls == [("p1", 8)]


def test_duplicate_tool_falls_back_to_lexical(prompts_path) -> None:
    registry = ToolRegistry.from_prompt_registry(PromptRegistry(prompts_path))
    tool = registry.get("find_duplicate_workitems")
    context = _context(
        prompts_path,
        {
            "entity_versions": {"story:s1": 1},
            "sibling_work_items": [{"itemId": "i9", "title": "Saved views board"}],
        },
    )

    result = tool.execute(_action({"projectId": "p1", "title": "saved views"}), context)

    assert result.success is True
    assert result.output["method"] == "lexical"
    assert [d["itemId"] for d in result.output["duplicates"]] == ["i9"]
