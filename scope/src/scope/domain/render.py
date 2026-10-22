"""Code-rendered proposal parts (numbers) and Markdown/HTML export. Pure functions."""

from __future__ import annotations

import html
import re

from scope.domain.models import SECTION_TITLES, ChangeLog, EstimateSummary, Proposal, ScopeItem


def money(v: float) -> str:
    return f"${v:,.0f}"


def price_section(items: list[ScopeItem], est: EstimateSummary, *, excluded_ids: set[str]) -> str:
    rows = [i for i in items if i.id not in excluded_ids]
    lines = [
        "| Item | Expected hours | Price |",
        "|---|---:|---:|",
        *(f"| {i.title} | {i.expected_hours:.1f} | {money(i.cost_usd)} |" for i in rows),
        f"| **Total** | **{est.expected_hours:.1f}** | **{money(est.price_usd)}** |",
        "",
        f"Estimated price: **{money(est.price_usd)}**, with a {int(est.confidence_level * 100)}% "
        f"range of {money(est.price_low_usd)} to {money(est.price_high_usd)} "
        f"({est.low_hours:.0f} to {est.high_hours:.0f} hours). Hours are three-point estimates "
        "(optimistic, most likely, pessimistic); the range reflects their uncertainty. Work is "
        "billed against the estimate; any change beyond it follows the change process.",
    ]
    return "\n".join(lines)


def change_requests_section(log: ChangeLog | None) -> str:
    if log is None or not (log.change_requests or log.removed or log.modified or log.added):
        return "No changes since the agreed scope."
    out: list[str] = [f"Changes from scope version {log.from_version} to {log.to_version}."]
    if log.change_requests:
        out += [
            "",
            "Quoted separately (not included in the agreed price):",
            "",
            "| Change request | Extra hours | Price |",
            "|---|---:|---:|",
        ]
        out += [
            f"| {cr.rationale} | {cr.delta_hours:.1f} | {money(cr.price_usd)} |"
            for cr in log.change_requests
        ]
    if log.modified:
        out += ["", "Updated within the agreed scope:", ""]
        out += [
            f"- {m.after.title}: {m.after.statement}"
            for m in log.modified
            if m.scope_status == "within_agreed"
        ]
    if log.added:
        out += ["", "Added within the agreed scope:", ""]
        out += [f"- {r.title}: {r.statement}" for r in log.added]
    if log.removed:
        out += ["", "Removed at the client's request:", ""]
        out += [f"- {r.title}" for r in log.removed]
    return "\n".join(out)


def proposal_markdown(p: Proposal, *, client: str, version: int, revision: int) -> str:
    parts = [
        f"# Proposal for {client}",
        "",
        f"Scope version {version}, proposal revision {revision}.",
        "",
    ]
    for s in p.sections:
        parts += [f"## {SECTION_TITLES[s.name]}", "", s.content.strip(), ""]
    return "\n".join(parts)


_BOLD = re.compile(r"\*\*(.+?)\*\*")


def _inline(text: str) -> str:
    return _BOLD.sub(r"<strong>\1</strong>", html.escape(text))


def markdown_to_html(md: str, title: str) -> str:
    """Small, safe Markdown subset (headings, bullets, tables, bold) for the exported file."""
    out: list[str] = []
    in_list = in_table = False
    for raw in md.splitlines():
        line = raw.rstrip()
        if in_list and not line.startswith(("- ", "* ")):
            out.append("</ul>")
            in_list = False
        if in_table and not line.startswith("|"):
            out.append("</table>")
            in_table = False
        if line.startswith("#"):
            level = min(len(line) - len(line.lstrip("#")), 4)
            out.append(f"<h{level}>{_inline(line.lstrip('#').strip())}</h{level}>")
        elif line.startswith(("- ", "* ")):
            if not in_list:
                out.append("<ul>")
                in_list = True
            out.append(f"<li>{_inline(line[2:])}</li>")
        elif line.startswith("|"):
            cells = [c.strip() for c in line.strip("|").split("|")]
            if all(set(c) <= set("-: ") for c in cells):
                continue
            if not in_table:
                out.append("<table>")
                in_table = True
            out.append("<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in cells) + "</tr>")
        elif line:
            out.append(f"<p>{_inline(line)}</p>")
    if in_list:
        out.append("</ul>")
    if in_table:
        out.append("</table>")
    body = "\n".join(out)
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        f"<title>{html.escape(title)}</title><style>body{{font-family:system-ui,sans-serif;"
        "max-width:800px;margin:2rem auto;padding:0 1rem;line-height:1.5}table{border-collapse:"
        "collapse}td{border:1px solid #ccc;padding:4px 8px}</style></head><body>"
        f"{body}</body></html>"
    )
