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
        return f"google-gla:{self.model_name_for(prompt)}"

    def configure_environment(self) -> None:
        if self.settings.gemini_api_key and not os.getenv("GOOGLE_API_KEY"):
            os.environ["GOOGLE_API_KEY"] = self.settings.gemini_api_key
