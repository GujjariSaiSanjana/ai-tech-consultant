"""Small builders for domain objects used across tests."""

from __future__ import annotations

from scope.domain.ids import requirement_id
from scope.domain.models import Evidence, ReconcileFinding, Requirement, ScopeItem
from scope.domain.pert import build_task


def req(statement: str, title: str | None = None, flags: list[str] | None = None) -> Requirement:
    return Requirement(
        id=requirement_id(statement), title=title or statement[:20], statement=statement,
        category="functional", priority="must",
        evidence=[Evidence(meeting_id="m1", timestamp="00:00:01", speaker="Ana", quote=statement)],
        flags=flags or [],
    )


def finding(matches: str, change: str, statement: str, scope_status: str = "within_agreed",
            evidence: bool = True) -> ReconcileFinding:
    return ReconcileFinding.model_validate({
        "matches": matches, "change": change, "title": statement[:20], "statement": statement,
        "category": "functional", "priority": "must", "scope_status": scope_status,
        "evidence": [{"meeting_id": "m3", "timestamp": "00:00:01", "speaker": "Ana",
                      "quote": statement}] if evidence else [],
        "rationale": "because",
    })


def item(iid: str, rids: list[str], hours: float, kind: str = "custom_dev") -> ScopeItem:
    return ScopeItem.model_validate({
        "id": iid, "title": iid, "description": "", "requirement_ids": rids, "kind": kind,
        "tasks": [build_task(task_id=f"T-{iid}", title="work", role="developer", o=hours,
                             m=hours, p=hours, rate_usd=100).model_dump()],
    })
