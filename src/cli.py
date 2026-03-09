from __future__ import annotations

import argparse
import asyncio
import json

from src.handlers.index_handler import handle_index
from src.handlers.query_handler import handle_query


async def _run_query(args: argparse.Namespace) -> None:
    event = {
        "query": args.query,
        "session_id": args.session_id,
        "metadata_filters": {},
    }
    result = await handle_query(event)
    print(json.dumps(result, indent=2))


async def _run_index(args: argparse.Namespace) -> None:
    event = {
        "doc_id": args.doc_id,
        "source": args.source,
        "text": args.text,
        "metadata": {},
    }
    result = await handle_index(event)
    print(json.dumps(result, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description="PydanticAI RAG template CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    query_parser = subparsers.add_parser("query")
    query_parser.add_argument("query")
    query_parser.add_argument("--session-id", default=None)

    index_parser = subparsers.add_parser("index")
    index_parser.add_argument("--doc-id", required=True)
    index_parser.add_argument("--source", default="manual")
    index_parser.add_argument("--text", required=True)

    args = parser.parse_args()
    if args.command == "query":
        asyncio.run(_run_query(args))
    else:
        asyncio.run(_run_index(args))


if __name__ == "__main__":
    main()

