"""Domain records shared by agents, storage and UI.

LLM output schemas live next to each agent; these are the validated, ID-bearing records that code
builds from them. IDs are always assigned in code (see ``scope.domain.ids``), never by a model.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Kind = Literal["setting", "addon", "widget_customization", "custom_dev", "unknown"]
KINDS: tuple[Kind, ...] = ("setting", "addon", "widget_customization", "custom_dev", "unknown")

Priority = Literal["must", "should", "could"]
RequirementCategory = Literal["functional", "non_functional", "integration", "data", "content"]
ChecklistCategory = Literal[
    "business_outcome",
    "integrations",
    "data_migration",
    "roles_permissions",
    "acceptance_criteria",
    "non_functional",
    "timeline",
    "budget",
    "decision_maker",
    "support",
    "out_of_scope",
]
CHECKLIST: tuple[ChecklistCategory, ...] = (
    "business_outcome",
    "integrations",
    "data_migration",
    "roles_permissions",
    "acceptance_criteria",
    "non_functional",
    "timeline",
    "budget",
    "decision_maker",
    "support",
    "out_of_scope",
)
Role = Literal["consultant", "developer", "designer", "qa", "project_manager"]


class Evidence(BaseModel):
    """Where in a meeting a requirement was said."""

    meeting_id: str
    timestamp: str
    speaker: str
    quote: str


class Requirement(BaseModel):
    id: str
    title: str
    statement: str
    category: RequirementCategory
    priority: Priority
    evidence: list[Evidence]
    flags: list[str] = Field(default_factory=list)


class DiscardedMention(BaseModel):
    """Something said in a meeting that is deliberately not a requirement (reversed, ruled out)."""

    quote: str
    reason: str


class OpenQuestion(BaseModel):
    id: str
    category: ChecklistCategory
    question: str
    why_it_matters: str
    blocking: bool
    requirement_ids: list[str] = Field(default_factory=list)


class Assumption(BaseModel):
    id: str
    category: ChecklistCategory
    statement: str
    risk_if_wrong: str


class Citation(BaseModel):
    """Same shape as the Knowledge Brain contract's Citation."""

    article_id: str
    title: str
    url: str
    quote: str
    image_url: str | None = None


class Feasibility(BaseModel):
    requirement_id: str
    kind: Kind
    setting_path: str | None
    rationale: str
    confidence: float
    citations: list[Citation]
    flags: list[str] = Field(default_factory=list)


class Task(BaseModel):
    id: str
    title: str
    role: Role
    optimistic_hours: float
    likely_hours: float
    pessimistic_hours: float
    expected_hours: float
    sigma_hours: float
    rate_usd: float
    cost_usd: float
    flags: list[str] = Field(default_factory=list)


class ScopeItem(BaseModel):
    id: str
    title: str
    description: str
    requirement_ids: list[str]
    kind: Kind
    tasks: list[Task]

    @property
    def expected_hours(self) -> float:
        return round(sum(t.expected_hours for t in self.tasks), 2)

    @property
    def cost_usd(self) -> float:
        return round(sum(t.cost_usd for t in self.tasks), 2)


class EstimateSummary(BaseModel):
    expected_hours: float
    sigma_hours: float
    low_hours: float
    high_hours: float
    price_usd: float
    price_low_usd: float
    price_high_usd: float
    confidence_level: float
    hours_by_kind: dict[str, float]


class ReviewNote(BaseModel):
    severity: Literal["must_fix", "should_fix", "info"]
    kind: Literal[
        "missed_requirement", "inconsistent_hours", "missing_assumption", "estimate_rule", "other"
    ]
    target: str | None
    message: str
    source: Literal["code", "llm"]


class Review(BaseModel):
    loops: int
    notes: list[ReviewNote]
    approved_by_reviewer: bool


ScopeStatus = Literal["within_agreed", "out_of_scope"]


class ReconcileFinding(BaseModel):
    """One statement from a follow-up meeting, related to the prior approved requirements.

    ``matches`` is the prior requirement ID it refers to, or ``"new"``. This is also the item type
    of the Reconciler's structured output.
    """

    matches: str
    change: Literal["unchanged", "modified", "removed", "added"]
    title: str
    statement: str
    category: RequirementCategory
    priority: Priority
    scope_status: ScopeStatus
    evidence: list[Evidence]
    rationale: str


class Modification(BaseModel):
    id: str
    before: Requirement
    after: Requirement
    rationale: str
    scope_status: ScopeStatus


class ChangeRequest(BaseModel):
    id: str
    requirement_ids: list[str]
    delta_hours: float
    price_usd: float
    rationale: str


class ChangeLog(BaseModel):
    from_version: int
    to_version: int
    added: list[Requirement] = Field(default_factory=list)
    modified: list[Modification] = Field(default_factory=list)
    removed: list[Requirement] = Field(default_factory=list)
    out_of_scope: list[Requirement] = Field(default_factory=list)
    unchanged_ids: list[str] = Field(default_factory=list)
    delta_hours_by_requirement: dict[str, float] = Field(default_factory=dict)
    change_requests: list[ChangeRequest] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class ScopeSnapshot(BaseModel):
    """Everything one pipeline run produces before the proposal. Persisted as a scope version."""

    requirements: list[Requirement]
    discarded: list[DiscardedMention] = Field(default_factory=list)
    questions: list[OpenQuestion] = Field(default_factory=list)
    assumptions: list[Assumption] = Field(default_factory=list)
    feasibility: list[Feasibility] = Field(default_factory=list)
    items: list[ScopeItem] = Field(default_factory=list)
    exclusions: list[str] = Field(default_factory=list)
    estimate: EstimateSummary | None = None
    review: Review | None = None
    changelog: ChangeLog | None = None


ProposalSectionName = Literal[
    "summary",
    "scope",
    "exclusions",
    "timeline",
    "price",
    "assumptions",
    "open_questions",
    "change_process",
    "change_requests",
]
PROPOSAL_SECTIONS: tuple[ProposalSectionName, ...] = (
    "summary",
    "scope",
    "exclusions",
    "timeline",
    "price",
    "assumptions",
    "open_questions",
    "change_process",
    "change_requests",
)
SECTION_TITLES: dict[ProposalSectionName, str] = {
    "summary": "Summary",
    "scope": "Scope of work",
    "exclusions": "Exclusions",
    "timeline": "Timeline",
    "price": "Price",
    "assumptions": "Assumptions",
    "open_questions": "Open questions",
    "change_process": "Change process",
    "change_requests": "Change requests since the agreed scope",
}


class ProposalSection(BaseModel):
    name: ProposalSectionName
    content: str
    approved: bool = False
    generated_by: Literal["llm", "code", "human"]


class Proposal(BaseModel):
    sections: list[ProposalSection]

    def section(self, name: ProposalSectionName) -> ProposalSection:
        for s in self.sections:
            if s.name == name:
                return s
        raise KeyError(name)

    @property
    def all_approved(self) -> bool:
        return all(s.approved for s in self.sections)
