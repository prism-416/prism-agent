from __future__ import annotations

import json
import urllib.request
from collections.abc import Callable

# Discord caps message content at 2000 chars; stay safely under it.
_MAX_CONTENT = 1900

Transport = Callable[[str, bytes, float], None]


class DiscordNotifier:
    """Fire-and-forget Discord webhook alerts.

    Posts plain message content to a single webhook (one channel). It is a no-op
    when no webhook URL is configured, and it never raises: a Discord outage,
    timeout, or rate-limit must never break the agent function. Delivery errors are
    swallowed by design.
    """

    def __init__(
        self,
        webhook_url: str | None,
        *,
        timeout: float = 5.0,
        transport: Transport | None = None,
    ) -> None:
        self.webhook_url = webhook_url
        self.timeout = timeout
        self._transport = transport or _post

    @property
    def enabled(self) -> bool:
        return bool(self.webhook_url)

    def send(self, content: str) -> None:
        if not self.enabled or not content:
            return
        body = json.dumps({"content": content[:_MAX_CONTENT]}).encode("utf-8")
        try:
            self._transport(self.webhook_url, body, self.timeout)
        except Exception:
            # Observability must not affect control flow.
            return


def _post(url: str, body: bytes, timeout: float) -> None:
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    urllib.request.urlopen(request, timeout=timeout).close()  # noqa: S310 (trusted webhook URL)
