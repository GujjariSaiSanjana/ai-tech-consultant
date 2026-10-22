"""Stable, content-derived IDs. Models never mint IDs; code derives them from normalized text."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def normalize(text: str) -> str:
    """Lowercase, drop punctuation, collapse whitespace. Used for IDs and fuzzy matching."""
    return _NON_ALNUM.sub(" ", text.lower()).strip()


def _digest(*parts: str, length: int = 8) -> str:
    joined = "\x1f".join(normalize(p) for p in parts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:length]


def requirement_id(statement: str) -> str:
    return f"REQ-{_digest(statement)}"


def question_id(category: str, question: str) -> str:
    return f"Q-{_digest(category, question)}"


def assumption_id(statement: str) -> str:
    return f"A-{_digest(statement)}"


def scope_item_id(title: str, requirement_ids: Iterable[str]) -> str:
    return f"SI-{_digest(title, *sorted(requirement_ids))}"


def task_id(item_id: str, title: str, role: str) -> str:
    return f"T-{_digest(item_id, title, role)}"


def change_request_id(requirement_ids: Iterable[str]) -> str:
    return f"CR-{_digest(*sorted(requirement_ids))}"
