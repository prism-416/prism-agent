from __future__ import annotations

import base64
from types import SimpleNamespace

from infrastructure.vault import oci_secrets
from infrastructure.vault.oci_secrets import _secret_bundle_content


class FakeSecretsClient:
    def get_secret_bundle(self, secret_ocid: str):
        assert secret_ocid == "ocid1.vaultsecret.example"
        payload = base64.b64encode(b"secret-value").decode("utf-8")
        data = SimpleNamespace(secret_bundle_content=SimpleNamespace(content=payload))
        return SimpleNamespace(data=data)


def test_secret_bundle_content_reads_base64_payload() -> None:
    payload = base64.b64encode(b"secret-value").decode("utf-8")
    bundle = SimpleNamespace(secret_bundle_content=SimpleNamespace(content=payload))

    assert _secret_bundle_content(bundle) == payload


def test_fetch_secret_text_decodes_base64_payload(monkeypatch) -> None:
    monkeypatch.setattr(oci_secrets, "build_secrets_client", lambda: FakeSecretsClient())

    assert oci_secrets.fetch_secret_text("ocid1.vaultsecret.example") == "secret-value"
