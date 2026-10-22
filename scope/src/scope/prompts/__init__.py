"""Versioned prompt files. A prompt change is a new file (``<agent>.v2.md``), not an edit."""

from __future__ import annotations

from functools import cache
from importlib.resources import files


@cache
def load_prompt(name: str) -> str:
    """Load ``<name>.md`` from this package, e.g. ``load_prompt("extractor.v1")``."""
    return files(__package__).joinpath(f"{name}.md").read_text(encoding="utf-8").strip()
