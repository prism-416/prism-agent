from __future__ import annotations

import os
from typing import Any


def build_object_storage_client() -> Any:
    try:
        import oci
    except ImportError as exc:
        raise RuntimeError("Install the prod extra to use OCI Object Storage.") from exc

    if os.getenv("OCI_RESOURCE_PRINCIPAL_VERSION"):
        signer = oci.auth.signers.get_resource_principals_signer()
        return oci.object_storage.ObjectStorageClient(config={}, signer=signer)
    return oci.object_storage.ObjectStorageClient(oci.config.from_file())
