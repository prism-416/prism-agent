import pytest
from pydantic import ValidationError

from infrastructure.config.settings import Settings


def test_settings_do_not_expose_prompt_path_env_override(monkeypatch) -> None:
    monkeypatch.setenv("PROMPTS_PATH", "custom-prompts")

    settings = Settings.from_env()

    assert "prompts_path" not in Settings.model_fields
    assert not hasattr(settings, "prompts_path")
    with pytest.raises(ValidationError):
        Settings(prompts_path="custom-prompts")
