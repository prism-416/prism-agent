from __future__ import annotations

from src.adapters.gemini import GeminiPydanticAIClient
from src.adapters.pgvector_store import PgvectorStore
from src.config import Settings
from src.prompts.loader import YamlPromptLoader
from src.tools.builtin import register_builtin_tools
from src.tools.registry import ToolRegistry
from src.workflows.rag_agent import RagAgentWorkflow


def build_workflow(settings: Settings) -> tuple[RagAgentWorkflow, PgvectorStore]:
    model = GeminiPydanticAIClient(model_name=settings.gemini_model)
    vector_store = PgvectorStore(
        dsn=settings.pg_dsn,
        embedding_dimension=settings.embedding_dimension,
    )
    prompt_loader = YamlPromptLoader(prompts_dir=settings.prompts_dir)
    tool_registry = ToolRegistry()
    register_builtin_tools(tool_registry)

    workflow = RagAgentWorkflow(
        settings=settings,
        llm=model,
        embedder=model,
        vector_store=vector_store,
        prompt_loader=prompt_loader,
        tool_registry=tool_registry,
    )
    return workflow, vector_store

