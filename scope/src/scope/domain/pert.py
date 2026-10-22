"""Three-point (PERT) estimation and pricing. All arithmetic is done here, never by a model."""

from __future__ import annotations

import math
from collections.abc import Sequence

from scope.domain.models import EstimateSummary, Role, ScopeItem, Task

# Two-sided 90% interval for a normal approximation of the summed estimate.
Z_90 = 1.645


def order_triplet(o: float, m: float, p: float) -> tuple[float, float, float, list[str]]:
    """Return (o, m, p) with O <= M <= P and no negatives, plus flags describing any repair."""
    flags: list[str] = []
    values = [max(0.0, float(v)) for v in (o, m, p)]
    if values != [float(o), float(m), float(p)]:
        flags.append("negative_hours_clamped")
    ordered = sorted(values)
    if ordered != values:
        flags.append("hours_reordered")
    return ordered[0], ordered[1], ordered[2], flags


def expected(o: float, m: float, p: float) -> float:
    return (o + 4 * m + p) / 6


def sigma(o: float, p: float) -> float:
    return (p - o) / 6


def build_task(
    *, task_id: str, title: str, role: Role, o: float, m: float, p: float, rate_usd: float
) -> Task:
    oo, mm, pp, flags = order_triplet(o, m, p)
    e = expected(oo, mm, pp)
    return Task(
        id=task_id,
        title=title,
        role=role,
        optimistic_hours=oo,
        likely_hours=mm,
        pessimistic_hours=pp,
        expected_hours=round(e, 2),
        sigma_hours=round(sigma(oo, pp), 2),
        rate_usd=rate_usd,
        cost_usd=round(e * rate_usd, 2),
        flags=flags,
    )


def summarize(items: Sequence[ScopeItem], z: float = Z_90) -> EstimateSummary:
    """Sum expected hours; combine task sigmas as independent (root of summed variances)."""
    tasks = [t for item in items for t in item.tasks]
    exp_hours = sum(t.expected_hours for t in tasks)
    sd = math.sqrt(sum(t.sigma_hours**2 for t in tasks))
    price = sum(t.cost_usd for t in tasks)
    low = max(0.0, exp_hours - z * sd)
    high = exp_hours + z * sd
    blended = price / exp_hours if exp_hours else 0.0
    by_kind: dict[str, float] = {}
    for item in items:
        by_kind[item.kind] = round(by_kind.get(item.kind, 0.0) + item.expected_hours, 2)
    return EstimateSummary(
        expected_hours=round(exp_hours, 2),
        sigma_hours=round(sd, 2),
        low_hours=round(low, 2),
        high_hours=round(high, 2),
        price_usd=round(price, 2),
        price_low_usd=round(low * blended, 2),
        price_high_usd=round(high * blended, 2),
        confidence_level=0.9 if z == Z_90 else round(math.erf(z / math.sqrt(2)), 3),
        hours_by_kind=by_kind,
    )


def hours_by_requirement(items: Sequence[ScopeItem]) -> dict[str, tuple[float, float]]:
    """(expected hours, cost) attributed to each requirement; shared items split evenly."""
    out: dict[str, tuple[float, float]] = {}
    for item in items:
        if not item.requirement_ids:
            continue
        share = len(item.requirement_ids)
        for rid in item.requirement_ids:
            h, c = out.get(rid, (0.0, 0.0))
            out[rid] = (h + item.expected_hours / share, c + item.cost_usd / share)
    return out
