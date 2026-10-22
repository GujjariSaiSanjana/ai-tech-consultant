from __future__ import annotations

from pathlib import Path

import pytest

from scope.config import Settings

DATA = Path(__file__).resolve().parents[1] / "data"
TRAILHEAD = DATA / "transcripts" / "trailhead"

SMALL = """# Meeting: Small test
# Date: 2026-01-01
# Participants: Sam (CONSULTANT); Ana (CLIENT)

[00:00:05] CONSULTANT (Sam Patel): Thanks for joining. What do you need?
[00:00:20] CLIENT (Ana Ruiz): We need members to be suspended when their card payment fails.
[00:01:10] CLIENT (Ana Ruiz): We want a forum. Actually, scratch that, no forum.
[00:02:00] CLIENT (Ana Ruiz): And we need a booking integration with FareHarbor on each profile.
[00:03:00] CONSULTANT (Sam Patel): Got it.
"""


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        llm="fake",
        brain="fake",
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'scope.db'}",
        export_dir=str(tmp_path / "exports"),
        anthropic_api_key=None,
    )


def read(name: str) -> str:
    return (TRAILHEAD / name).read_text(encoding="utf-8")
