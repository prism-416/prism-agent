from __future__ import annotations

import io
import json
from typing import Any

from fdk import response

from interfaces.oci_function_entry import handler as runtime_handler


def handler(ctx: Any, data: io.BytesIO | None = None) -> response.Response:
    payload = data.read() if data is not None else b"{}"
    result = runtime_handler(ctx, payload)
    return response.Response(
        ctx,
        response_data=json.dumps(result),
        headers={"Content-Type": "application/json"},
    )
