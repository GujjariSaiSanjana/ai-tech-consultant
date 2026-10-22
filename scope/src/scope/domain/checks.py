"""Deterministic estimate checks. They feed the Reviewer loop as notes with source="code"."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from scope.domain.diff import OUT_OF_SCOPE_FLAG
from scope.domain.models import Feasibility, Kind, Requirement, ReviewNote, ScopeItem

# Higher rank = more expensive way to deliver. An item is as expensive as its costliest requirement.
KIND_RANK: dict[Kind, int] = {
    "setting": 0,
    "addon": 1,
    "widget_customization": 2,
    "unknown": 3,
    "custom_dev": 4,
}


def expected_item_kind(
    requirement_ids: Sequence[str], feasibility: Mapping[str, Feasibility]
) -> Kind:
    kinds = [feasibility[r].kind for r in requirement_ids if r in feasibility]
    return max(kinds, key=lambda k: KIND_RANK[k]) if kinds else "unknown"


def coverage_notes(
    requirements: Sequence[Requirement], items: Sequence[ScopeItem]
) -> list[ReviewNote]:
    known = {r.id for r in requirements}
    covered = {rid for item in items for rid in item.requirement_ids}
    notes = [
        ReviewNote(
            severity="must_fix",
            kind="missed_requirement",
            target=r.id,
            source="code",
            message=f"{r.id} ({r.title}) is not covered by any scope item",
        )
        for r in requirements
        if r.id not in covered
    ]
    notes += [
        ReviewNote(
            severity="must_fix",
            kind="other",
            target=rid,
            source="code",
            message=f"scope item references unknown requirement {rid}",
        )
        for rid in sorted(covered - known)
    ]
    return notes


def estimate_rule_notes(
    items: Sequence[ScopeItem],
    feasibility: Mapping[str, Feasibility],
    *,
    setting_max_hours: float,
) -> list[ReviewNote]:
    notes: list[ReviewNote] = []
    for item in items:
        for t in item.tasks:
            if "hours_reordered" in t.flags or "negative_hours_clamped" in t.flags:
                notes.append(
                    ReviewNote(
                        severity="must_fix",
                        kind="inconsistent_hours",
                        target=t.id,
                        source="code",
                        message=f"task '{t.title}' did not satisfy 0 <= O <= M <= P; it was "
                        "repaired, re-estimate it",
                    )
                )
        want = expected_item_kind(item.requirement_ids, feasibility)
        if item.kind != want:
            notes.append(
                ReviewNote(
                    severity="should_fix",
                    kind="inconsistent_hours",
                    target=item.id,
                    source="code",
                    message=f"item '{item.title}' is labelled {item.kind} but its requirements are "
                    f"{want} per the feasibility check",
                )
            )
        if item.kind == "setting" and item.expected_hours > setting_max_hours:
            notes.append(
                ReviewNote(
                    severity="must_fix",
                    kind="estimate_rule",
                    target=item.id,
                    source="code",
                    message=f"settings item '{item.title}' is {item.expected_hours}h; configuring "
                    f"an existing setting should take at most {setting_max_hours}h",
                )
            )
    settings = [i for i in items if i.kind == "setting"]
    custom = [i for i in items if i.kind == "custom_dev"]
    if settings and custom:
        dearest = max(settings, key=lambda i: i.expected_hours)
        cheapest = min(custom, key=lambda i: i.expected_hours)
        if dearest.expected_hours >= cheapest.expected_hours:
            notes.append(
                ReviewNote(
                    severity="must_fix",
                    kind="estimate_rule",
                    target=dearest.id,
                    source="code",
                    message=f"settings item '{dearest.title}' ({dearest.expected_hours}h) is not "
                    f"cheaper than custom item '{cheapest.title}' ({cheapest.expected_hours}h)",
                )
            )
    return notes


def change_request_grouping_notes(
    requirements: Sequence[Requirement], items: Sequence[ScopeItem]
) -> list[ReviewNote]:
    """Out-of-scope requests must sit in their own items so they can be quoted separately."""
    oos = {r.id for r in requirements if OUT_OF_SCOPE_FLAG in r.flags}
    return [
        ReviewNote(
            severity="must_fix",
            kind="other",
            target=i.id,
            source="code",
            message=f"item '{i.title}' mixes out-of-scope requests with agreed work; put each "
            "out-of-scope request in its own item",
        )
        for i in items
        if set(i.requirement_ids) & oos and not set(i.requirement_ids) <= oos
    ]
