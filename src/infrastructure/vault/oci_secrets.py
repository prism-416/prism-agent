from __future__ import annotations

import base64
from typing import Any

from infrastructure.oci_auth import load_oci_config_and_signer


def fetch_secret_text(secret_ocid: str) -> str:
    client = build_secrets_client()
    response = client.get_secret_bundle(secret_ocid)
    content = _secret_bundle_content(response.data)
    return base64.b64decode(content).decode("utf-8")


def build_secrets_client() -> Any:
    try:
        import oci
    except ImportError as exc:
        raise RuntimeError("Install the prod extra to use OCI Vault secrets.") from exc

    config, signer = load_oci_config_and_signer("OCI Vault secrets")
    if signer is not None:
        return oci.secrets.SecretsClient(config=config, signer=signer)
    return oci.secrets.SecretsClient(config)


def _secret_bundle_content(secret_bundle: Any) -> str:
    bundle_content = getattr(secret_bundle, "secret_bundle_content", None)
    content = getattr(bundle_content, "content", None)
    if not isinstance(content, str) or not content:
        raise ValueError("OCI Vault secret bundle did not contain base64 text content.")
    return content
