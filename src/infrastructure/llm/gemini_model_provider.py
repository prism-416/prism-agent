from __future__ import annotations

import os

from capabilities.definitions import WorkflowPromptDefinition
from infrastructure.config.settings import Settings


class GeminiModelProvider:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def model_name_for(self, prompt: WorkflowPromptDefinition) -> str:
        return prompt.model.name or self.settings.default_gemini_model

    def pydantic_ai_model_ref(self, prompt: WorkflowPromptDefinition) -> str:
        return f"google:{self.model_name_for(prompt)}"

    def configure_environment(self) -> None:
        if self.settings.gemini_api_key:
            os.environ["GEMINI_API_KEY"] = self.settings.gemini_api_key
            os.environ.pop("GOOGLE_API_KEY", None)
        if not os.getenv("GEMINI_API_KEY") and not os.getenv("GOOGLE_API_KEY"):
            raise RuntimeError("GEMINI_API_KEY or GOOGLE_API_KEY is required for Gemini planning.")
