from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def prompts_path() -> Path:
    return Path(__file__).resolve().parents[1] / "prompts"
