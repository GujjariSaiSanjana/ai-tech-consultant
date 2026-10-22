from __future__ import annotations

import pytest

from scope.domain.diff import (
    EXPANDED_FLAG,
    OUT_OF_SCOPE_FLAG,
    apply_findings,
    base_items,
    finalize_changes,
    split_items,
)
from tests.factories import finding, item, req

A = req("Members are suspended when payment fails.")
B = req("Profiles show live FareHarbor and Peek availability.")
C = req("Dana gets a weekly signup report.")
PRIOR = [A, B, C]


def test_unmentioned_requirements_carry_forward_unchanged() -> None:
    reqs, log = apply_findings(PRIOR, [], from_version=1, to_version=2)
    assert reqs == PRIOR
    assert log.unchanged_ids == [A.id, B.id, C.id]
    assert not (log.added or log.modified or log.removed or log.out_of_scope)


def test_modified_keeps_id_and_merges_evidence() -> None:
    f = finding(B.id, "modified", "A Book now button links to the guide's FareHarbor page.")
    reqs, log = apply_findings(PRIOR, [f], from_version=1, to_version=2)
    assert [m.id for m in log.modified] == [B.id]
    after = next(r for r in reqs if r.id == B.id)
    assert after.statement.startswith("A Book now")
    assert len(after.evidence) == 2
    assert log.modified[0].before == B


def test_removed_and_added_and_out_of_scope() -> None:
    fs = [
        finding(C.id, "removed", C.statement),
        finding("new", "added", "Admins export members to CSV."),
        finding("new", "added", "Guides get SMS alerts via Twilio.", scope_status="out_of_scope"),
    ]
    reqs, log = apply_findings(PRIOR, fs, from_version=1, to_version=2)
    assert [r.id for r in log.removed] == [C.id]
    assert C.id not in {r.id for r in reqs}
    assert [r.statement for r in log.added] == ["Admins export members to CSV."]
    assert [r.statement for r in log.out_of_scope] == ["Guides get SMS alerts via Twilio."]
    assert OUT_OF_SCOPE_FLAG in log.out_of_scope[0].flags
    assert all(r.id.startswith("REQ-") for r in reqs)


def test_unknown_id_becomes_new_with_warning() -> None:
    _, log = apply_findings(PRIOR, [finding("REQ-deadbeef", "modified", "Something else.")],
                            from_version=1, to_version=2)
    assert len(log.added) == 1
    assert any("unknown id" in w for w in log.warnings)


def test_removal_of_unknown_is_ignored() -> None:
    reqs, log = apply_findings(PRIOR, [finding("new", "removed", "Nope.")], from_version=1,
                               to_version=2)
    assert reqs == PRIOR and log.warnings


def test_later_finding_overrides_earlier_with_warning() -> None:
    fs = [finding(C.id, "removed", C.statement), finding(C.id, "unchanged", C.statement)]
    reqs, log = apply_findings(PRIOR, fs, from_version=1, to_version=2)
    assert C in reqs and not log.removed
    assert any("overridden" in w for w in log.warnings)


def test_same_text_modification_is_unchanged_and_new_with_same_text_is_match() -> None:
    fs = [finding(A.id, "modified", A.statement), finding("new", "added", B.statement)]
    reqs, log = apply_findings(PRIOR, fs, from_version=1, to_version=2)
    assert not log.modified and not log.added
    assert set(log.unchanged_ids) == {A.id, B.id, C.id}


def test_added_with_prior_id_is_treated_as_modified() -> None:
    _, log = apply_findings(PRIOR, [finding(A.id, "added", "Suspend after 14 days.")],
                            from_version=1, to_version=2)
    assert [m.id for m in log.modified] == [A.id]


def test_expanded_modification_is_flagged_not_excluded() -> None:
    f = finding(B.id, "modified", "Live availability plus Viator.", scope_status="out_of_scope")
    reqs, log = apply_findings(PRIOR, [f], from_version=1, to_version=2)
    assert EXPANDED_FLAG in log.modified[0].after.flags
    assert OUT_OF_SCOPE_FLAG not in log.modified[0].after.flags


def test_split_items_and_change_requests() -> None:
    prior_items = [item("I-A", [A.id], 2, "setting"), item("I-BC", [B.id, C.id], 40)]
    fs = [
        finding(C.id, "removed", C.statement),
        finding("new", "added", "Guides get SMS alerts via Twilio.", scope_status="out_of_scope"),
    ]
    reqs, log = apply_findings(PRIOR, fs, from_version=1, to_version=2)
    kept, rescope = split_items(prior_items, log)
    assert [i.id for i in kept] == ["I-A"]
    sms = log.out_of_scope[0].id
    assert rescope == {B.id, sms}
    next_items = [*kept, item("I-B", [B.id], 30), item("I-SMS", [sms], 12)]
    log = finalize_changes(log, prior_items, next_items)
    assert log.delta_hours_by_requirement[C.id] == pytest.approx(-20)
    assert log.delta_hours_by_requirement[sms] == pytest.approx(12)
    assert [cr.requirement_ids for cr in log.change_requests] == [[sms]]
    assert log.change_requests[0].price_usd == pytest.approx(1200)
    assert [i.id for i in base_items(next_items, reqs)] == ["I-A", "I-B"]
