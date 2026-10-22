"""Approval gate state machines. Pure functions: callers persist the result and audit it."""

from __future__ import annotations

from typing import Literal

ScopeVersionStatus = Literal["draft", "approved"]
ProposalState = Literal["draft", "in_review", "approved", "sent"]
ProposalAction = Literal[
    "submit_for_review", "approve", "send", "edit_section", "regenerate_section", "approve_section"
]


class InvalidTransition(Exception):
    """The requested action is not allowed in the current state."""


def approve_scope(status: ScopeVersionStatus) -> ScopeVersionStatus:
    if status == "approved":
        raise InvalidTransition("scope version is already approved")
    return "approved"


def can_generate_proposal(status: ScopeVersionStatus) -> bool:
    """The proposal gate: nothing is written for the client before a human approves the scope."""
    return status == "approved"


def next_proposal_state(
    state: ProposalState, action: ProposalAction, *, all_sections_approved: bool
) -> ProposalState:
    """Return the state after ``action``. Raises InvalidTransition when not allowed.

    - Content changes (edit/regenerate) are allowed until sent; on an approved proposal they
      send it back to review, so a changed proposal is never sent without a fresh approval.
    - Approving the proposal requires every section to be approved first.
    - Only an approved proposal can be sent. Sent is terminal.
    """
    if state == "sent":
        raise InvalidTransition("proposal was already sent; start a new version to change it")
    match action:
        case "edit_section" | "regenerate_section":
            return "in_review" if state == "approved" else state
        case "approve_section":
            if state == "approved":
                raise InvalidTransition("proposal is already approved")
            return state
        case "submit_for_review":
            if state != "draft":
                raise InvalidTransition(f"cannot submit from {state}")
            return "in_review"
        case "approve":
            if state != "in_review":
                raise InvalidTransition(f"cannot approve from {state}; submit for review first")
            if not all_sections_approved:
                raise InvalidTransition("approve every section before approving the proposal")
            return "approved"
        case "send":
            if state != "approved":
                raise InvalidTransition("only an approved proposal can be sent")
            return "sent"
