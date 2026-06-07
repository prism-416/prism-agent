from __future__ import annotations

import base64
import os
from typing import Any


def load_oci_config_and_signer(usage: str) -> tuple[dict[str, Any], Any | None]:
    try:
        import oci
    except ImportError as exc:
        raise RuntimeError(f"Install the prod extra to use {usage}.") from exc

    auth_mode = os.getenv("OCI_AUTH_MODE", "").strip().lower().replace("-", "_")
    if os.getenv("OCI_RESOURCE_PRINCIPAL_VERSION") or auth_mode in {
        "resource_principal",
        "resource_principals",
    }:
        return {}, oci.auth.signers.get_resource_principals_signer()

    if auth_mode in {"instance_principal", "instance_principals"}:
        return {}, oci.auth.signers.InstancePrincipalsSecurityTokenSigner()

    env_config = _api_key_config_from_env()
    if env_config and auth_mode not in {"config", "config_file"}:
        return env_config, None

    if auth_mode in {"api_key", "user_principal"}:
        raise RuntimeError(
            "OCI_AUTH_MODE requires API key environment credentials: "
            "OCI_TENANCY_OCID, OCI_USER_OCID, OCI_FINGERPRINT, OCI_REGION, "
            "and OCI_PRIVATE_KEY or OCI_PRIVATE_KEY_FILE."
        )

    return oci.config.from_file(), None


def _api_key_config_from_env() -> dict[str, Any] | None:
    key_file = os.getenv("OCI_PRIVATE_KEY_FILE") or None
    key_content = _private_key_content(os.getenv("OCI_PRIVATE_KEY") or "")
    config = {
        "tenancy": os.getenv("OCI_TENANCY_OCID") or None,
        "user": os.getenv("OCI_USER_OCID") or None,
        "fingerprint": os.getenv("OCI_FINGERPRINT") or None,
        "region": os.getenv("OCI_REGION") or None,
        "key_file": key_file,
        "key_content": key_content,
    }
    required = ("tenancy", "user", "fingerprint", "region")
    if not all(config[name] for name in required) or not (key_file or key_content):
        return None

    return {key: value for key, value in config.items() if value}


def _private_key_content(value: str) -> str | None:
    normalized = value.strip().strip('"').strip("'")
    if not normalized:
        return None
    normalized = normalized.replace("\\n", "\n")
    if "-----BEGIN" in normalized:
        return normalized
    try:
        decoded = base64.b64decode(normalized).decode("utf-8").strip()
    except Exception:
        return normalized
    return decoded if "-----BEGIN" in decoded else normalized
