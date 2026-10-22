"""View models for templates. Pure functions so the Changes diff is unit-testable."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from scope.domain.models import ChangeLog, ChangeRequest, Feasibility, Requirement, ScopeSnapshot

RowStatus = Literal["unchanged", "modified", "removed", "added", "out_of_scope"]


@dataclass(frozen=True)
class DiffRow:
    status: RowStatus
    before: Requirement | None
    after: Requirement | None
    delta_hours: float | None
    change_request: ChangeRequest | None
    rationale: str | None


def diff_rows(prior: ScopeSnapshot, log: ChangeLog) -> list[DiffRow]:
    """Split view rows in prior order, then additions, then out-of-scope requests."""
    modified = {m.id: m for m in log.modified}
    removed = {r.id for r in log.removed}
    crs = {rid: cr for cr in log.change_requests for rid in cr.requirement_ids}
    rows: list[DiffRow] = []
    for r in prior.requirements:
        delta = log.delta_hours_by_requirement.get(r.id)
        if r.id in modified:
            m = modified[r.id]
            rows.append(DiffRow("modified", r, m.after, delta, None, m.rationale))
        elif r.id in removed:
            rows.append(DiffRow("removed", r, None, delta, None, None))
        else:
            rows.append(DiffRow("unchanged", r, r, None, None, None))
    rows += [
        DiffRow("added", None, r, log.delta_hours_by_requirement.get(r.id), None, None)
        for r in log.added
    ]
    rows += [
        DiffRow(
            "out_of_scope", None, r, log.delta_hours_by_requirement.get(r.id), crs.get(r.id), None
        )
        for r in log.out_of_scope
    ]
    return rows


KIND_LABELS = {
    "setting": "Existing setting",
    "addon": "Add-on",
    "widget_customization": "Widget customization",
    "custom_dev": "Custom development",
    "unknown": "Unknown - needs checking",
}


def feasibility_by_id(snap: ScopeSnapshot) -> dict[str, Feasibility]:
    return {f.requirement_id: f for f in snap.feasibility}
