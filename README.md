# PydanticAI RAG Template

Production-grade, serverless-first template for async RAG with agentic workflows using:

- Gemini via PydanticAI
- PostgreSQL + pgvector
- YAML prompt files
- Typed tools and workflow interfaces

## Why this template

- Keeps interfaces explicit so components are swappable.
- Uses staged workflow state for predictable agentic behavior.
- Avoids framework lock-in for serverless deployment targets.

## Project layout

- `src/` core code (no nested root package)
- `prompts/` versionable YAML prompts
- `tests/` unit tests for core subsystems
- `docs/` architecture and extension guidance

## Workflow overview

The runtime flow is designed to be explicit and easy to extend:

1. `handle_query()` receives a serverless event.
2. `build_workflow()` wires adapters, prompts, tools, and settings.
3. `RagAgentWorkflow.run()` executes staged steps:
   - route intent (`router.yaml`)
   - optional tool execution (`expand_filters`)
   - vector retrieval (`pgvector`)
   - answer synthesis (`synthesis.yaml`)
   - optional critique and retry (`critic.yaml`)
4. Response returns answer + citations + workflow trace.

Indexing follows a separate path:

1. `handle_index()` receives a document event.
2. Document is chunked.
3. Chunks are embedded.
4. Embeddings are upserted into `rag_chunks`.

## Build your program with this structure

Use this template as a composition root and replace components by interface:

1. Keep orchestrator logic in `src/workflows/rag_agent.py`.
2. Add or replace providers in `src/adapters/` (LLM, embeddings, vector store).
3. Keep prompt behavior in `prompts/*.yaml` instead of hardcoding prompt text.
4. Register business tools in `src/tools/builtin.py` and route through `ToolRegistry`.
5. Expose deployment entrypoints in `src/handlers/` for each serverless action.

Recommended customization order:

1. Define your domain schema in `src/models.py`.
2. Add metadata strategy for retrieval filters.
3. Update prompt files for your policy/tone/citation style.
4. Add domain tools (search APIs, policy checks, calculators).
5. Add integration tests for your expected end-to-end behavior.

## Minimal implementation map

- `src/handlers/index_handler.py`: ingest + chunk + embed + upsert
- `src/handlers/query_handler.py`: query + retrieve + synthesize
- `src/workflows/rag_agent.py`: agentic control flow and retries
- `src/prompts/loader.py`: YAML prompt rendering
- `src/tools/registry.py`: typed tool execution
- `src/adapters/gemini.py`: generation and embedding adapter
- `src/adapters/pgvector_store.py`: retrieval store adapter

## Quickstart

1. Install dependencies:
   - `uv sync --extra dev`
2. Copy env file:
   - PowerShell: `Copy-Item .env.example .env`
   - Bash: `cp .env.example .env`
3. Ensure PostgreSQL has `vector` extension enabled.
   - Example: `psql "$PG_DSN" -f sql/001_init_pgvector.sql`
4. Run tests:
   - `uv run --extra dev pytest`
5. Run a query locally:
   - `uv run python -m src.cli query "What is this repository for?"`

## Serverless usage

Use:

- `src/handlers/query_handler.py` for query execution
- `src/handlers/index_handler.py` for document indexing

These handlers are plain async functions intended to be wrapped by your cloud runtime adapter.

## Environment variables

See `.env.example` for complete list. Core variables:

- `GOOGLE_API_KEY`
- `GEMINI_MODEL`
- `PG_DSN`
- `EMBEDDING_DIMENSION`
- `PROMPTS_DIR`
- `RAG_TOP_K`

## Notes

- This template favors reliability and clarity over hidden automation.
- You can add tracing backends (OpenTelemetry, vendor APM) without changing workflow contracts.
- CI is provided at `.github/workflows/ci.yml` with `ruff`, `mypy`, and `pytest`.
