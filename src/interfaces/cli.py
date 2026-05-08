from __future__ import annotations

import json
import sys
from pathlib import Path

from domain.events import EventEnvelope
from interfaces.local_entry import run_local


def load_envelope(path: str | Path) -> EventEnvelope:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if "event" in data:
        return EventEnvelope.model_validate(data)
    return EventEnvelope.model_validate({"event": data})


def main(argv: list[str] | None = None) -> int:
    argv = argv or sys.argv[1:]
    if not argv:
        print("Usage: python -m interfaces.cli <seed-event.json>")
        return 2
    envelope = load_envelope(argv[0])
    run_local(envelope)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
