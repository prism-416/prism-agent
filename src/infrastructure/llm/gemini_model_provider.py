from __future__ import annotations

import os

from capabilities.definitions import WorkflowPromptDefinition
from infrastructure.config.settings import Settings


class GeminiModelProvider:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def model_name_for(self, prompt: WorkflowPromptDefinition) -> str:
        return prompt.model.name or self.settings.default_gemini_model

    def model_name_for_tier(self, tier: str | None) -> str | None:
        """Resolve a subagent's role tier to a concrete model name.

        ``pro`` maps to the default (strong) model, ``flash`` to the fast/cheap one.
        Unknown or absent tiers return None so the caller falls back to the workflow
        prompt's model — keeping the whole-workflow (inline) path unchanged.
        """
        tier_models = {
            "pro": self.settings.default_gemini_model,
            "flash": self.settings.gemini_flash_model,
        }
        if tier is None:
            return None
        return tier_models.get(tier)

    def pydantic_ai_model_ref(
        self, prompt: WorkflowPromptDefinition, model_tier: str | None = None
    ) -> str:
        name = self.model_name_for_tier(model_tier) or self.model_name_for(prompt)
        return f"google:{name}"

    def configure_environment(self) -> None:
        if self.settings.gemini_api_key:
            os.environ["GEMINI_API_KEY"] = self.settings.gemini_api_key
            os.environ.pop("GOOGLE_API_KEY", None)
        if not os.getenv("GEMINI_API_KEY") and not os.getenv("GOOGLE_API_KEY"):
            raise RuntimeError("GEMINI_API_KEY or GOOGLE_API_KEY is required for Gemini planning.")
