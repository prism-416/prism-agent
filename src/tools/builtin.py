from __future__ import annotations

from src.tools.registry import ToolRegistry


async def format_citations(arguments: dict[str, object]) -> dict[str, object]:
    chunk_ids = arguments.get("chunk_ids", [])
    if not isinstance(chunk_ids, list):
        return {"citations": []}
    citations = [f"[{str(chunk_id)}]" for chunk_id in chunk_ids]
    return {"citations": citations}


async def expand_filters(arguments: dict[str, object]) -> dict[str, object]:
    query = str(arguments.get("query", "")).lower()
    inferred_filters: dict[str, str] = {}
    if "policy" in query:
        inferred_filters["category"] = "policy"
    if "runbook" in query:
        inferred_filters["category"] = "runbook"
    return {"filters": inferred_filters}


def register_builtin_tools(registry: ToolRegistry) -> None:
    registry.register(
        name="format_citations",
        description="Format retrieved chunk IDs into citation tags.",
        handler=format_citations,
    )
    registry.register(
        name="expand_filters",
        description="Infer useful metadata filters from user query.",
        handler=expand_filters,
    )

