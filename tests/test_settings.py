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


def test_settings_resolves_runtime_secrets_from_oci_vault_ocids(monkeypatch) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("PRISM_API_TOKEN", raising=False)
    monkeypatch.setenv("GEMINI_API_KEY_SECRET_OCID", "ocid1.vaultsecret.gemini")
    monkeypatch.setenv("PRISM_API_TOKEN_SECRET_OCID", "ocid1.vaultsecret.prism")

    def fake_fetch_secret_text(secret_ocid: str) -> str:
        return {
            "ocid1.vaultsecret.gemini": "gemini-secret",
            "ocid1.vaultsecret.prism": "prism-secret",
        }[secret_ocid]

    monkeypatch.setattr(
        "infrastructure.config.settings.fetch_secret_text",
        fake_fetch_secret_text,
    )

    settings = Settings.from_env()

    assert settings.gemini_api_key == "gemini-secret"
    assert settings.gemini_api_key_secret_ocid == "ocid1.vaultsecret.gemini"
    assert settings.prism_api_token == "prism-secret"
    assert settings.prism_api_token_secret_ocid == "ocid1.vaultsecret.prism"


def test_settings_prefers_direct_runtime_secret_env_values(monkeypatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "direct-gemini")
    monkeypatch.setenv("PRISM_API_TOKEN", "direct-prism")
    monkeypatch.setenv("GEMINI_API_KEY_SECRET_OCID", "ocid1.vaultsecret.gemini")
    monkeypatch.setenv("PRISM_API_TOKEN_SECRET_OCID", "ocid1.vaultsecret.prism")

    def fail_fetch_secret_text(secret_ocid: str) -> str:
        raise AssertionError(f"Unexpected OCI Vault fetch: {secret_ocid}")

    monkeypatch.setattr(
        "infrastructure.config.settings.fetch_secret_text",
        fail_fetch_secret_text,
    )

    settings = Settings.from_env()

    assert settings.gemini_api_key == "direct-gemini"
    assert settings.prism_api_token == "direct-prism"
