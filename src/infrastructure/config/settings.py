from __future__ import annotations

import os
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    app_env: Literal["local", "prod"] = "local"
    state_backend: Literal["memory", "prism_api"] = "memory"
    queue_backend: Literal["memory", "oci"] = "memory"
    llm_provider: Literal["gemini"] = "gemini"
    gemini_api_key: str | None = None
    default_gemini_model: str = "gemini-3.1-pro-preview"
    gemini_flash_model: str = "gemini-2.5-flash"
    max_recursion_depth: int = Field(default=10, ge=1)
    oci_queue_ocid: str | None = None
    oci_queue_messages_endpoint: str | None = None
    object_storage_bucket_name: str | None = None
    object_storage_namespace: str | None = None
    prism_api_base_url: str | None = None
    prism_api_token: str | None = None
    discord_webhook_url: str | None = None
    log_level: str = "INFO"
    log_traces: bool = False
    # Persist plan/context-snapshot/task-graph state as agent memories so it survives
    # across function invocations (required for queue-separated processing). Needs the
    # API token to allow agent-memory writes.
    persist_agent_memories: bool = True

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            app_env=os.getenv("APP_ENV", "local"),
            state_backend=os.getenv("STATE_BACKEND", "memory"),
            queue_backend=os.getenv("QUEUE_BACKEND", "memory"),
            llm_provider=os.getenv("LLM_PROVIDER", "gemini"),
            gemini_api_key=os.getenv("GEMINI_API_KEY") or None,
            default_gemini_model=os.getenv("DEFAULT_GEMINI_MODEL", "gemini-3.1-pro-preview"),
            gemini_flash_model=os.getenv("GEMINI_FLASH_MODEL", "gemini-2.5-flash"),
            max_recursion_depth=int(os.getenv("MAX_RECURSION_DEPTH", "10")),
            oci_queue_ocid=os.getenv("OCI_QUEUE_OCID"),
            oci_queue_messages_endpoint=os.getenv("OCI_QUEUE_MESSAGES_ENDPOINT") or None,
            object_storage_bucket_name=os.getenv("OCI_OBJECT_STORAGE_BUCKET_NAME") or None,
            object_storage_namespace=os.getenv("OCI_OBJECT_STORAGE_NAMESPACE") or None,
            prism_api_base_url=os.getenv("PRISM_API_BASE_URL"),
            prism_api_token=os.getenv("PRISM_API_TOKEN") or None,
            discord_webhook_url=os.getenv("DISCORD_WEBHOOK_URL") or None,
            log_level=os.getenv("LOG_LEVEL", "INFO"),
            log_traces=os.getenv("LOG_TRACES", "true").lower() == "true",
            persist_agent_memories=os.getenv("PERSIST_AGENT_MEMORIES", "true").lower() == "true",
        )
