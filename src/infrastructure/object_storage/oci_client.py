from __future__ import annotations

from typing import Any

from infrastructure.oci_auth import load_oci_config_and_signer


def build_object_storage_client() -> Any:
    try:
        import oci
    except ImportError as exc:
        raise RuntimeError("Install the prod extra to use OCI Object Storage.") from exc

    config, signer = load_oci_config_and_signer("OCI Object Storage")
    if signer is not None:
        return oci.object_storage.ObjectStorageClient(config=config, signer=signer)
    return oci.object_storage.ObjectStorageClient(config)
