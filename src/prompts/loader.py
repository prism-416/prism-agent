from __future__ import annotations

from pathlib import Path
from string import Template
from typing import Any

import yaml

from src.errors import PromptNotFoundError


class YamlPromptLoader:
    def __init__(self, prompts_dir: Path) -> None:
        self.prompts_dir = prompts_dir

    def render(self, prompt_name: str, variables: dict[str, Any] | None = None) -> str:
        prompt_path = self.prompts_dir / f"{prompt_name}.yaml"
        if not prompt_path.exists():
            raise PromptNotFoundError(f"Prompt file not found: {prompt_path}")

        data = self._load_yaml(prompt_path)
        content = data.get("prompt")
        if not isinstance(content, str):
            raise PromptNotFoundError(f"Missing 'prompt' key in {prompt_path}")

        var_map = {k: str(v) for k, v in (variables or {}).items()}
        return Template(content).safe_substitute(var_map).strip()

    @staticmethod
    def _load_yaml(path: Path) -> dict[str, Any]:
        raw = path.read_text(encoding="utf-8")
        parsed = yaml.safe_load(raw)
        if not isinstance(parsed, dict):
            raise PromptNotFoundError(f"Invalid YAML prompt format: {path}")
        return parsed

