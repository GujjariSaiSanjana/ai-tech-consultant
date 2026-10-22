from __future__ import annotations

import math

import pytest

from scope.domain.models import ScopeItem
from scope.domain.pert import (
    build_task,
    expected,
    hours_by_requirement,
    order_triplet,
    sigma,
    summarize,
)


def test_expected_and_sigma() -> None:
    assert expected(2, 4, 12) == pytest.approx((2 + 16 + 12) / 6)
    assert sigma(2, 12) == pytest.approx(10 / 6)
    assert expected(5, 5, 5) == 5
    assert sigma(5, 5) == 0


@pytest.mark.parametrize(
    ("given", "want", "flags"),
    [
        ((1, 2, 3), (1, 2, 3), []),
        ((3, 2, 1), (1, 2, 3), ["hours_reordered"]),
        ((2, 1, 3), (1, 2, 3), ["hours_reordered"]),
        ((-1, 2, 3), (0, 2, 3), ["negative_hours_clamped"]),
        ((0, 0, 0), (0, 0, 0), []),
    ],
)
def test_order_triplet(given: tuple[float, float, float], want: tuple[float, ...],
                       flags: list[str]) -> None:
    o, m, p, f = order_triplet(*given)
    assert (o, m, p) == want
    assert f == flags


def test_build_task_prices_expected_hours() -> None:
    t = build_task(task_id="T-1", title="Build", role="developer", o=6, m=3, p=12, rate_usd=100)
    assert (t.optimistic_hours, t.likely_hours, t.pessimistic_hours) == (3, 6, 12)
    assert t.expected_hours == pytest.approx(6.5)
    assert t.cost_usd == pytest.approx(650)
    assert "hours_reordered" in t.flags


def _item(iid: str, rids: list[str], tasks: list[tuple[float, float, float]],
          kind: str = "custom_dev") -> ScopeItem:
    return ScopeItem.model_validate({
        "id": iid, "title": iid, "description": "", "requirement_ids": rids, "kind": kind,
        "tasks": [build_task(task_id=f"{iid}-{i}", title="t", role="developer", o=o, m=m, p=p,
                             rate_usd=100).model_dump() for i, (o, m, p) in enumerate(tasks)],
    })


def test_summarize_combines_variance() -> None:
    items = [_item("A", ["R1"], [(1, 2, 7)]), _item("B", ["R2"], [(2, 4, 12)], kind="setting")]
    s = summarize(items)
    exp = expected(1, 2, 7) + expected(2, 4, 12)
    sd = math.sqrt(sigma(1, 7) ** 2 + sigma(2, 12) ** 2)
    assert s.expected_hours == pytest.approx(exp, abs=0.02)
    assert s.sigma_hours == pytest.approx(sd, abs=0.02)
    assert s.low_hours == pytest.approx(exp - 1.645 * sd, abs=0.05)
    assert s.price_usd == pytest.approx(exp * 100, abs=2)
    assert s.price_low_usd < s.price_usd < s.price_high_usd
    assert set(s.hours_by_kind) == {"custom_dev", "setting"}


def test_summarize_empty() -> None:
    s = summarize([])
    assert s.expected_hours == 0 and s.price_usd == 0 and s.low_hours == 0


def test_hours_by_requirement_splits_shared_items() -> None:
    items = [_item("A", ["R1", "R2"], [(6, 6, 6)])]
    split = hours_by_requirement(items)
    assert split["R1"][0] == pytest.approx(3)
    assert split["R2"][1] == pytest.approx(300)
