from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    google_api_key: str = Field(alias="GOOGLE_API_KEY")
    gemini_model: str = Field(default="google-gla:gemini-2.0-flash", alias="GEMINI_MODEL")

    pg_dsn: str = Field(alias="PG_DSN")
    embedding_dimension: int = Field(default=768, alias="EMBEDDING_DIMENSION")

    rag_top_k: int = Field(default=6, alias="RAG_TOP_K")
    rag_score_threshold: float = Field(default=0.65, alias="RAG_SCORE_THRESHOLD")

    prompts_dir: Path = Field(default=Path("prompts"), alias="PROMPTS_DIR")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    max_workflow_retries: int = Field(default=1, alias="MAX_WORKFLOW_RETRIES")
    llm_timeout_seconds: float = Field(default=30.0, alias="LLM_TIMEOUT_SECONDS")


def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]

