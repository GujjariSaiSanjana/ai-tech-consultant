"""Plain-text summary of a scope version (CLI output)."""

from __future__ import annotations

from scope.domain.models import ScopeSnapshot


def scope_summary(snap: ScopeSnapshot, *, version: int, status: str) -> str:
    feas = {f.requirement_id: f for f in snap.feasibility}
    out = [f"Scope v{version} ({status})", ""]
    out.append(
        f"Requirements: {len(snap.requirements)}  (discarded mentions: {len(snap.discarded)})"
    )
    for r in snap.requirements:
        f = feas.get(r.id)
        kind = f.kind if f else "-"
        flags = (
            f" [{', '.join(r.flags + (f.flags if f else []))}]"
            if r.flags or (f and f.flags)
            else ""
        )
        out.append(f"  {r.id}  {kind:<20} {r.title}{flags}")
    blocking = sum(q.blocking for q in snap.questions)
    out += ["", f"Open questions: {len(snap.questions)} ({blocking} blocking)"]
    out += [f"  [{q.category}] {q.question}" for q in snap.questions[:10]]
    if len(snap.questions) > 10:
        out.append(f"  ... and {len(snap.questions) - 10} more")
    if snap.estimate:
        e = snap.estimate
        out += [
            "",
            f"Scope items: {len(snap.items)}",
            f"Estimate: {e.expected_hours:.1f} h expected ({e.low_hours:.0f}-{e.high_hours:.0f} h,"
            f" {int(e.confidence_level * 100)}%)",
            f"Price: ${e.price_usd:,.0f} (${e.price_low_usd:,.0f}-${e.price_high_usd:,.0f})",
        ]
    if snap.review:
        must = [n for n in snap.review.notes if n.severity == "must_fix"]
        out.append(f"Review: {snap.review.loops} loop(s), {len(must)} unresolved must-fix note(s)")
    if c := snap.changelog:
        out += [
            "",
            f"Changes v{c.from_version} -> v{c.to_version}: +{len(c.added)} added, "
            f"~{len(c.modified)} modified, -{len(c.removed)} removed, "
            f"{len(c.out_of_scope)} out of scope, {len(c.change_requests)} change request(s)",
        ]
        out += [
            f"  CR {cr.id}: {cr.delta_hours:.1f} h, ${cr.price_usd:,.0f}  {cr.rationale}"
            for cr in c.change_requests
        ]
    return "\n".join(out)
