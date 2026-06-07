from __future__ import annotations

import base64

from infrastructure.oci_auth import _api_key_config_from_env, _private_key_content


def test_private_key_content_expands_escaped_newlines() -> None:
    raw = "-----BEGIN PRIVATE KEY-----\\nabc\\n-----END PRIVATE KEY-----"

    assert (
        _private_key_content(raw) == "-----BEGIN PRIVATE KEY-----\nabc\n-----END PRIVATE KEY-----"
    )


def test_private_key_content_decodes_base64_pem() -> None:
    pem = "-----BEGIN PRIVATE KEY-----\nabc\n-----END PRIVATE KEY-----"
    raw = base64.b64encode(pem.encode("utf-8")).decode("utf-8")

    assert _private_key_content(raw) == pem


def test_api_key_config_from_api_server_env(monkeypatch) -> None:
    monkeypatch.setenv("OCI_TENANCY_OCID", "ocid1.tenancy.oc1..tenancy")
    monkeypatch.setenv("OCI_USER_OCID", "ocid1.user.oc1..user")
    monkeypatch.setenv("OCI_FINGERPRINT", "aa:bb:cc")
    monkeypatch.setenv("OCI_REGION", "ap-seoul-1")
    monkeypatch.setenv(
        "OCI_PRIVATE_KEY",
        "-----BEGIN PRIVATE KEY-----\\nabc\\n-----END PRIVATE KEY-----",
    )

    config = _api_key_config_from_env()

    assert config == {
        "tenancy": "ocid1.tenancy.oc1..tenancy",
        "user": "ocid1.user.oc1..user",
        "fingerprint": "aa:bb:cc",
        "region": "ap-seoul-1",
        "key_content": "-----BEGIN PRIVATE KEY-----\nabc\n-----END PRIVATE KEY-----",
    }
