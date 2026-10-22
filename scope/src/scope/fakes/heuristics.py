"""Rule-based responders for the fake LLM. Honest, transparent, and deliberately simple."""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

from scope.agents.extractor import ExtractedRequirement, ExtractionOutput
from scope.agents.feasibility import FeasibilityDecision, FeasibilityOutput
from scope.agents.proposal_writer import ProposalDraft, SectionDraft
from scope.agents.questioner import AssumptionDraft, QuestionDraft, QuestionsOutput
from scope.agents.reconciler import ReconcileOutput
from scope.agents.reviewer import ReviewOutput
from scope.agents.scoper import ScopeDraft, ScopeItemDraft, TaskDraft
from scope.domain.models import (
    CHECKLIST,
    ChecklistCategory,
    DiscardedMention,
    Evidence,
    Kind,
    Priority,
    ReconcileFinding,
    RequirementCategory,
    Role,
    ScopeStatus,
)
from scope.llm.types import LLMRequest

RENDERED = re.compile(
    r"^\[(\S+) (\d{2}:\d{2}:\d{2})\] ([A-Z_ ]+) \(([^)]+)\): (.*?)(  <<flagged.*)?$"
)
SENTENCE = re.compile(r"(?<=[.!?])\s+")
CUE = re.compile(
    r"\b(we need|we want|i want|i'd love|i would love|needs? to|has to|have to|should be able"
    r"|must|would be great|we'd like|i'd like|make search|they want|people want|we'll want)\b",
    re.IGNORECASE,
)
REVERSAL = re.compile(r"scratch that|forget it|never ?mind|not this year|let's not", re.IGNORECASE)
REMOVAL = re.compile(
    r"\b(drop it|drop the|remove|don't need|no longer|postpone|keep the default)\b", re.IGNORECASE
)
CHANGE_REQUEST = re.compile(
    r"change request|separate quote|quote (it|that) separately|extra", re.IGNORECASE
)
STOP = frozenset(
    {
        "the",
        "a",
        "an",
        "and",
        "or",
        "to",
        "of",
        "for",
        "in",
        "on",
        "at",
        "is",
        "are",
        "be",
        "it",
        "we",
        "i",
        "you",
        "they",
        "that",
        "this",
        "with",
        "so",
        "like",
        "just",
        "can",
        "our",
        "my",
        "your",
        "want",
        "need",
        "would",
        "should",
        "have",
        "has",
        "do",
    }
)


@dataclass(frozen=True)
class Line:
    meeting_id: str
    timestamp: str
    role: str
    speaker: str
    text: str


def lines(context: str) -> list[Line]:
    out = []
    for raw in context.splitlines():
        if m := RENDERED.match(raw):
            out.append(Line(m.group(1), m.group(2), m.group(3).strip(), m.group(4), m.group(5)))
    return out


def sentences(line: Line) -> Iterator[str]:
    yield from (s.strip() for s in SENTENCE.split(line.text) if len(s.split()) >= 4)


def words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in STOP and len(w) > 2}


def category_of(text: str) -> RequirementCategory:
    low = text.lower()
    if re.search(r"integrat|sync|hubspot|api|calendar|zapier|twilio", low):
        return "integration"
    if re.search(r"import|migrat|csv|spreadsheet|export", low):
        return "data"
    if re.search(r"second|fast|load|cookie|gdpr|accessib|wcag|secur", low):
        return "non_functional"
    return "functional"


def priority_of(text: str) -> Priority:
    return "must" if re.search(r"\b(need|must|has to|have to)\b", text, re.IGNORECASE) else "should"


def title_of(text: str) -> str:
    return " ".join(text.split()[:7]).rstrip(",.;:")


def _payload(req: LLMRequest) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(req.payload)
    return data


def extractor(req: LLMRequest, schema: type[BaseModel]) -> BaseModel:
    candidates: list[tuple[Line, str]] = []
    discarded: list[DiscardedMention] = []
    for ln in lines(req.context):
        if ln.role != "CLIENT":
            continue
        for s in sentences(ln):
            if REVERSAL.search(s) and candidates and candidates[-1][0].speaker == ln.speaker:
                dropped = candidates.pop()
                discarded.append(DiscardedMention(quote=dropped[1], reason="reversed"))
            elif CUE.search(s):
                candidates.append((ln, s))
    reqs = [
        ExtractedRequirement(
            title=title_of(s),
            statement=s,
            category=category_of(s),
            priority=priority_of(s),
            evidence=[
                Evidence(
                    meeting_id=ln.meeting_id, timestamp=ln.timestamp, speaker=ln.speaker, quote=s
                )
            ],
        )
        for ln, s in candidates[:60]
    ]
    return ExtractionOutput(requirements=reqs, discarded=discarded)


CATEGORY_WORDS: dict[ChecklistCategory, tuple[str, ...]] = {
    "business_outcome": ("revenue", "goal", "grow", "signups", "success"),
    "integrations": ("integrat", "api", "sync", "hubspot", "calendar"),
    "data_migration": ("import", "migrat", "export", "spreadsheet", "csv"),
    "roles_permissions": ("permission", "role", "access level", "admin access"),
    "acceptance_criteria": ("acceptance", "sign off on each", "definition of done", "test plan"),
    "non_functional": ("second", "load", "performance", "secure", "accessib"),
    "timeline": ("launch", "deadline", "by march", "timeline", "season"),
    "budget": ("budget", "cost", "spend", "price range"),
    "decision_maker": ("decision", "sign off", "approve the budget", "partner"),
    "support": ("support", "maintenance", "training", "warranty"),
    "out_of_scope": ("out of scope", "not this year", "phase two", "later phase"),
}


def questioner(req: LLMRequest, schema: type[BaseModel]) -> BaseModel:
    low = req.context.lower()
    covered = [c for c in CHECKLIST if sum(low.count(w) for w in CATEGORY_WORDS[c]) >= 3]
    missing = [c for c in CHECKLIST if c not in covered]
    return QuestionsOutput(
        covered_categories=covered,
        questions=[
            QuestionDraft(
                category=c,
                question=f"What are your expectations for {c.replace('_', ' ')}?",
                why_it_matters="Not discussed in the meetings.",
                blocking=c in ("budget", "decision_maker", "acceptance_criteria"),
            )
            for c in missing
        ],
        assumptions=[
            AssumptionDraft(
                category=c,
                statement=f"Standard terms apply for "
                f"{c.replace('_', ' ')} until the client confirms otherwise.",
                risk_if_wrong="Estimate or contract terms may change.",
            )
            for c in missing
        ],
    )


def feasibility(req: LLMRequest, schema: type[BaseModel]) -> BaseModel:
    decisions = []
    for r in _payload(req)["requirements"]:
        ans = r["brain_answer"]
        decisions.append(
            FeasibilityDecision(
                requirement_id=r["requirement_id"],
                kind=ans["kind"],
                setting_path=ans["setting_path"],
                rationale=ans["rationale"],
                confidence=ans["confidence"],
                citation_article_ids=[c["article_id"] for c in ans["citations"]],
                needs_clarification=ans["confidence"] < 0.5,
            )
        )
    return FeasibilityOutput(decisions=decisions)


HOURS: dict[Kind, list[tuple[str, Role, float, float, float]]] = {
    "setting": [("Configure setting", "consultant", 0.5, 1.5, 3), ("Verify", "qa", 0.5, 1, 1.5)],
    "addon": [("Install and configure add-on", "developer", 2, 4, 8), ("Test", "qa", 1, 2, 3)],
    "widget_customization": [
        ("Design", "designer", 3, 6, 10),
        ("Customize widget", "developer", 4, 8, 14),
        ("Test", "qa", 1, 2, 4),
    ],
    "custom_dev": [("Build", "developer", 16, 28, 48), ("Test", "qa", 4, 8, 12)],
    "unknown": [("Discovery", "consultant", 2, 4, 8), ("Build", "developer", 8, 16, 32)],
}


def scoper(req: LLMRequest, schema: type[BaseModel]) -> BaseModel:
    data = _payload(req)
    kinds = {f["requirement_id"]: f["kind"] for f in data["feasibility"]}
    items = []
    for r in data["requirements"]:
        kind: Kind = kinds.get(r["id"], "unknown")
        items.append(
            ScopeItemDraft(
                title=r["title"],
                description=r["statement"],
                requirement_ids=[r["id"]],
                kind=kind,
                tasks=[
                    TaskDraft(
                        title=t,
                        role=role,
                        optimistic_hours=o,
                        likely_hours=m,
                        pessimistic_hours=p,
                        rationale=f"Typical effort for {kind}",
                    )
                    for t, role, o, m, p in HOURS[kind]
                ],
            )
        )
    return ScopeDraft(items=items, exclusions=["Native mobile apps"], assumptions=[])


def reviewer(req: LLMRequest, schema: type[BaseModel]) -> BaseModel:
    return ReviewOutput(verdict="approve", notes=[])


def proposal_writer(req: LLMRequest, schema: type[BaseModel]) -> BaseModel:
    data = _payload(req)
    if schema is SectionDraft:
        return SectionDraft(
            content=f"{data['current_text']}\n\n(Revised: {data['reviewer_instruction']})"
        )
    f = data["facts"]
    bullets = "\n".join(f"- **{i['title']}**: {i['description']}" for i in f["scope_items"])
    return ProposalDraft(
        summary=f"{f['client']} wants to improve their directory. This proposal covers "
        f"{len(f['scope_items'])} scope items.",
        scope=bullets or "- No items.",
        exclusions="\n".join(f"- {e}" for e in f["exclusions"]) or "- None.",
        timeline="Delivery in phases: setup, build, test, launch.",
        assumptions="\n".join(f"- {a}" for a in f["assumptions"]) or "- None.",
        open_questions="\n".join(f"- {q}" for q in f["open_questions"]) or "- None.",
        change_process="Changes after approval are raised as written change requests, estimated, "
        "and approved before work starts.",
    )


def reconciler(req: LLMRequest, schema: type[BaseModel]) -> BaseModel:
    data = _payload(req)
    follow = set(data["follow_up_meeting_ids"])
    prior = data["approved_requirements"]
    all_lines = [ln for ln in lines(req.context) if ln.meeting_id in follow]
    findings: list[ReconcileFinding] = []
    for idx, ln in enumerate(all_lines):
        if ln.role != "CLIENT":
            continue
        for s in sentences(ln):
            removal = bool(REMOVAL.search(s))
            if not (removal or CUE.search(s)):
                continue
            ws = words(s)
            best, score = None, 0.0
            for p in prior:
                pw = words(p["statement"] + " " + p["title"])
                j = len(ws & pw) / max(1, len(ws | pw))
                if j > score:
                    best, score = p, j
            window = " ".join(x.text for x in all_lines[idx + 1 : idx + 4])
            oos: ScopeStatus = "out_of_scope" if CHANGE_REQUEST.search(window) else "within_agreed"
            ev = [
                Evidence(
                    meeting_id=ln.meeting_id, timestamp=ln.timestamp, speaker=ln.speaker, quote=s
                )
            ]
            if best is not None and score >= 0.15:
                findings.append(
                    ReconcileFinding(
                        matches=best["id"],
                        change="removed" if removal else "modified",
                        title=best["title"],
                        statement=s if not removal else best["statement"],
                        category=best["category"],
                        priority=best["priority"],
                        scope_status="within_agreed",
                        evidence=ev,
                        rationale="Discussed again.",
                    )
                )
            elif not removal:
                findings.append(
                    ReconcileFinding(
                        matches="new",
                        change="added",
                        title=title_of(s),
                        statement=s,
                        category=category_of(s),
                        priority=priority_of(s),
                        scope_status=oos,
                        evidence=ev,
                        rationale="New request.",
                    )
                )
    return ReconcileOutput(findings=findings)


DEFAULT_RESPONDERS = {
    "extractor": extractor,
    "questioner": questioner,
    "feasibility": feasibility,
    "scoper": scoper,
    "reviewer": reviewer,
    "proposal_writer": proposal_writer,
    "reconciler": reconciler,
}
