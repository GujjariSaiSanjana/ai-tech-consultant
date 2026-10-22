from __future__ import annotations

from scope.domain.ids import (
    change_request_id,
    normalize,
    question_id,
    requirement_id,
    scope_item_id,
    task_id,
)


def test_normalize() -> None:
    assert normalize("  Hello,   World!! ") == "hello world"
    assert normalize("Elite: $79/mo") == "elite 79 mo"


def test_requirement_id_is_stable_and_ignores_case_and_punctuation() -> None:
    a = requirement_id("Customers can search within 50 miles.")
    assert a == requirement_id("customers can search within 50 miles")
    assert a.startswith("REQ-") and len(a) == 12
    assert a != requirement_id("Customers can search within 60 miles.")


def test_other_ids_are_order_insensitive_where_it_matters() -> None:
    assert scope_item_id("Search", ["R2", "R1"]) == scope_item_id("Search", ["R1", "R2"])
    assert change_request_id(["B", "A"]) == change_request_id(["A", "B"])
    assert task_id("SI-1", "Build", "developer") != task_id("SI-1", "Build", "qa")
    assert question_id("budget", "What is the budget?").startswith("Q-")
