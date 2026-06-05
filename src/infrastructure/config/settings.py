from __future__ import annotations

import os
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from infrastructure.vault.oci_secrets import fetch_secret_text


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    app_env: Literal["local", "prod"] = "local"
    state_backend: Literal["memory", "object_storage"] = "memory"
    queue_backend: Literal["memory", "oci"] = "memory"
    llm_provider: Literal["gemini"] = "gemini"
    gemini_api_key: str | None = None
    gemini_api_key_secret_ocid: str | None = None
    default_gemini_model: str = "gemini-2.5-pro"
    max_recursion_depth: int = Field(default=10, ge=1)
    oci_queue_ocid: str | None = None
    oci_queue_messages_endpoint: str | None = None
    oci_bucket_name: str | None = None
    oci_namespace: str | None = None
    oci_payload_bucket_name: str | None = None
    oci_payload_namespace: str | None = None
    prism_api_base_url: str | None = None
    prism_api_token: str | None = None
    prism_api_token_secret_ocid: str | None = None

    @classmethod
    def from_env(cls) -> Settings:
        gemini_api_key_secret_ocid = os.getenv("GEMINI_API_KEY_SECRET_OCID") or None
        prism_api_token_secret_ocid = os.getenv("PRISM_API_TOKEN_SECRET_OCID") or None
        return cls(
            app_env=os.getenv("APP_ENV", "local"),
            state_backend=os.getenv("STATE_BACKEND", "memory"),
            queue_backend=os.getenv("QUEUE_BACKEND", "memory"),
            llm_provider=os.getenv("LLM_PROVIDER", "gemini"),
            gemini_api_key=os.getenv("GEMINI_API_KEY") or _secret_value(gemini_api_key_secret_ocid),
            gemini_api_key_secret_ocid=gemini_api_key_secret_ocid,
            default_gemini_model=os.getenv("DEFAULT_GEMINI_MODEL", "gemini-2.5-pro"),
            max_recursion_depth=int(os.getenv("MAX_RECURSION_DEPTH", "10")),
            oci_queue_ocid=os.getenv("OCI_QUEUE_OCID"),
            oci_queue_messages_endpoint=os.getenv("OCI_QUEUE_MESSAGES_ENDPOINT") or None,
            oci_bucket_name=os.getenv("OCI_BUCKET_NAME"),
            oci_namespace=os.getenv("OCI_NAMESPACE"),
            oci_payload_bucket_name=os.getenv("OCI_PAYLOAD_BUCKET_NAME")
            or os.getenv("OCI_BUCKET_NAME"),
            oci_payload_namespace=os.getenv("OCI_PAYLOAD_NAMESPACE") or os.getenv("OCI_NAMESPACE"),
            prism_api_base_url=os.getenv("PRISM_API_BASE_URL"),
            prism_api_token=os.getenv("PRISM_API_TOKEN")
            or _secret_value(prism_api_token_secret_ocid),
            prism_api_token_secret_ocid=prism_api_token_secret_ocid,
        )


def _secret_value(secret_ocid: str | None) -> str | None:
    if not secret_ocid:
        return None
    return fetch_secret_text(secret_ocid)
