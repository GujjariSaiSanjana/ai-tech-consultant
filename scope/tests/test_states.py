from __future__ import annotations

import itertools

import pytest

from scope.domain.states import (
    InvalidTransition,
    ProposalAction,
    ProposalState,
    approve_scope,
    can_generate_proposal,
    next_proposal_state,
)

STATES: list[ProposalState] = ["draft", "in_review", "approved", "sent"]
ACTIONS: list[ProposalAction] = ["submit_for_review", "approve", "send", "edit_section",
                                 "regenerate_section", "approve_section"]

# (state, action, all_sections_approved) -> expected next state; anything missing must raise.
ALLOWED: dict[tuple[ProposalState, ProposalAction, bool], ProposalState] = {}
for ok in (True, False):
    ALLOWED[("draft", "submit_for_review", ok)] = "in_review"
    ALLOWED[("approved", "send", ok)] = "sent"
    for s in ("draft", "in_review"):
        ALLOWED[(s, "approve_section", ok)] = s
    for a in ("edit_section", "regenerate_section"):
        ALLOWED[("draft", a, ok)] = "draft"
        ALLOWED[("in_review", a, ok)] = "in_review"
        ALLOWED[("approved", a, ok)] = "in_review"
ALLOWED[("in_review", "approve", True)] = "approved"


@pytest.mark.parametrize(("state", "action", "ok"),
                         list(itertools.product(STATES, ACTIONS, (True, False))))
def test_transition_table_is_exhaustive(state: ProposalState, action: ProposalAction,
                                        ok: bool) -> None:
    key = (state, action, ok)
    if key in ALLOWED:
        assert next_proposal_state(state, action, all_sections_approved=ok) == ALLOWED[key]
    else:
        with pytest.raises(InvalidTransition):
            next_proposal_state(state, action, all_sections_approved=ok)


def test_scope_gate() -> None:
    assert not can_generate_proposal("draft")
    assert can_generate_proposal("approved")
    assert approve_scope("draft") == "approved"
    with pytest.raises(InvalidTransition):
        approve_scope("approved")
