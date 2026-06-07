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


def test_settings_accept_prism_api_state_backend() -> None:
    settings = Settings(state_backend="prism_api")

    assert settings.state_backend == "prism_api"


def test_settings_rejects_object_storage_state_backend() -> None:
    with pytest.raises(ValidationError):
        Settings(state_backend="object_storage")


def test_settings_reads_runtime_credentials_from_application_config(monkeypatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "direct-gemini")
    monkeypatch.setenv("PRISM_API_TOKEN", "direct-prism")

    settings = Settings.from_env()

    assert settings.gemini_api_key == "direct-gemini"
    assert settings.prism_api_token == "direct-prism"


def test_settings_do_not_expose_oci_vault_secret_ocids() -> None:
    assert "gemini_api_key_secret_ocid" not in Settings.model_fields
    assert "prism_api_token_secret_ocid" not in Settings.model_fields
    with pytest.raises(ValidationError):
        Settings(gemini_api_key_secret_ocid="ocid1.vaultsecret.gemini")


def test_settings_loads_object_storage_payload_bucket(monkeypatch) -> None:
    monkeypatch.setenv("OCI_OBJECT_STORAGE_NAMESPACE", "api-namespace")
    monkeypatch.setenv("OCI_OBJECT_STORAGE_BUCKET_NAME", "api-bucket")

    settings = Settings.from_env()

    assert settings.object_storage_namespace == "api-namespace"
    assert settings.object_storage_bucket_name == "api-bucket"
