from __future__ import annotations

from scope.domain.checks import (
    change_request_grouping_notes,
    coverage_notes,
    estimate_rule_notes,
    expected_item_kind,
)
from scope.domain.models import Feasibility, Kind
from scope.domain.pert import build_task
from tests.factories import item, req

R1, R2 = req("Radius search by zip."), req("Live booking availability.")


def feas(rid: str, kind: Kind) -> Feasibility:
    return Feasibility(requirement_id=rid, kind=kind, setting_path=None, rationale="",
                       confidence=1, citations=[])


def test_coverage_flags_missing_and_unknown_ids() -> None:
    notes = coverage_notes([R1, R2], [item("I1", [R1.id, "REQ-ghost"], 1)])
    kinds = {(n.kind, n.target) for n in notes}
    assert ("missed_requirement", R2.id) in kinds
    assert ("other", "REQ-ghost") in kinds
    assert all(n.severity == "must_fix" and n.source == "code" for n in notes)


def test_expected_item_kind_takes_the_most_expensive() -> None:
    f = {R1.id: feas(R1.id, "setting"), R2.id: feas(R2.id, "custom_dev")}
    assert expected_item_kind([R1.id, R2.id], f) == "custom_dev"
    assert expected_item_kind(["REQ-x"], f) == "unknown"


def test_settings_must_be_cheaper_than_custom() -> None:
    f = {R1.id: feas(R1.id, "setting"), R2.id: feas(R2.id, "custom_dev")}
    items = [item("I1", [R1.id], 10, "setting"), item("I2", [R2.id], 8, "custom_dev")]
    notes = estimate_rule_notes(items, f, setting_max_hours=6)
    msgs = " ".join(n.message for n in notes)
    assert "at most 6" in msgs and "not cheaper" in msgs
    ok = [item("I1", [R1.id], 2, "setting"), item("I2", [R2.id], 30, "custom_dev")]
    assert estimate_rule_notes(ok, f, setting_max_hours=6) == []


def test_kind_mismatch_and_repaired_triplets_are_flagged() -> None:
    f = {R1.id: feas(R1.id, "custom_dev")}
    bad = item("I1", [R1.id], 5, "setting")
    bad.tasks.append(build_task(task_id="T", title="x", role="qa", o=5, m=1, p=2, rate_usd=1))
    notes = estimate_rule_notes([bad], f, setting_max_hours=100)
    assert {n.kind for n in notes} == {"inconsistent_hours"}
    assert {n.severity for n in notes} == {"must_fix", "should_fix"}


def test_out_of_scope_requests_need_their_own_items() -> None:
    oos = req("SMS alerts.", flags=["out_of_scope"])
    assert change_request_grouping_notes([R1, oos], [item("I", [R1.id, oos.id], 1)])
    assert not change_request_grouping_notes([R1, oos], [item("I", [oos.id], 1)])
