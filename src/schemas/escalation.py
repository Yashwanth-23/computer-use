"""
Human-in-the-loop escalation schema.

Two things live here:
  1. InterventionRequest -- the structured "please help" message raised when
     automation can't safely proceed (stuck, risky step, unrecognized state).
  2. HandoffState -- the state machine tracking who is in control of the
     live session, so both the automation and the operator tooling agree
     on whose turn it is to act. This is the seam described in brief 3.6:
     "automation must be able to pause, cede control, and resume on the
     same session, and there must be a way to know who is in control."

Neither of these describe *how* the operator's browser window is exposed
(that's the surface/session-sharing mechanism in src/escalation/
escalation_manager.py) -- these are the data contracts around it.
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator


class EscalationReason(str, Enum):
    STUCK_DURING_DISCOVERY = "stuck_during_discovery"    # LLM discovery loop can't find a way forward
    LOCATOR_UNRESOLVED = "locator_unresolved"             # replay: no candidate in the fallback chain resolved
    UNRECOGNIZED_STATE = "unrecognized_state"             # replay: page doesn't match any known checkpoint/rule
    RISKY_STEP_APPROVAL = "risky_step_approval"           # replay: reached an is_risky step, needs sign-off
    SESSION_AUTH_REQUIRED = "session_auth_required"       # e.g. re-login, MFA, security key prompt
    MAX_STEPS_EXCEEDED = "max_steps_exceeded"             # discovery loop stopping condition hit


class ControlState(str, Enum):
    AUTOMATION_RUNNING = "automation_running"
    ESCALATION_PENDING = "escalation_pending"   # paused, waiting for an operator to pick it up
    HUMAN_CONTROLLED = "human_controlled"       # operator is actively driving the live session
    RESUMING = "resuming"                       # operator signaled done; automation re-validating state
    COMPLETED = "completed"
    ABANDONED = "abandoned"                     # operator or timeout gave up; run will not resume


# Valid transitions, enforced by HandoffState.transition() rather than left
# implicit -- prevents e.g. jumping straight from ESCALATION_PENDING to
# COMPLETED without a human ever actually taking control.
_ALLOWED_TRANSITIONS: dict[ControlState, set[ControlState]] = {
    ControlState.AUTOMATION_RUNNING: {ControlState.ESCALATION_PENDING, ControlState.COMPLETED},
    ControlState.ESCALATION_PENDING: {ControlState.HUMAN_CONTROLLED, ControlState.ABANDONED},
    ControlState.HUMAN_CONTROLLED: {ControlState.RESUMING, ControlState.ABANDONED},
    ControlState.RESUMING: {ControlState.AUTOMATION_RUNNING, ControlState.ABANDONED},
    ControlState.COMPLETED: set(),
    ControlState.ABANDONED: set(),
}


class InterventionRequest(BaseModel):
    """Raised when the system cannot safely proceed on its own. Carries
    enough context for a human to act without needing to reconstruct the
    situation from logs (brief 3.6: "which capability/goal, the current
    step, the current state or screenshot, and why it stopped").
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=lambda: f"esc_{uuid4().hex[:12]}")
    run_id: str = Field(..., description="The ExecutionResult.run_id (or discovery run id) this belongs to.")
    capability_id: str | None = Field(
        default=None, description="None during discovery, before a capability exists yet."
    )
    goal: str = Field(..., description="The natural-language goal this run was pursuing.")
    reason: EscalationReason
    current_step_id: str | None = Field(default=None, description="Step in progress when escalation triggered.")
    explanation: str = Field(..., description="Plain-language reason, e.g. 'No locator candidate resolved for "
                                               "the Confirm button after 3 attempts.'")
    screenshot_ref: str = Field(..., description="Path to a live-state screenshot, relative to /evidence/.")
    proposed_action: str | None = Field(
        default=None,
        description="For RISKY_STEP_APPROVAL: plain description of the action awaiting authorization, "
                    "e.g. 'Open HOLIDAY_CLUB sub-account for member 1001 with $50.00 initial deposit.'",
    )
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class OperatorAction(BaseModel):
    """One recorded action the human operator took while in control.
    Captured so the run's evidence trail covers the human portion too,
    not just the automated portion.
    """

    model_config = ConfigDict(extra="forbid")

    description: str = Field(..., description="Plain description, e.g. 'Clicked Confirm and Submit button.'")
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class HandoffState(BaseModel):
    """Tracks who is in control of one live session, and the transition
    history. One HandoffState exists per session/run; it is the single
    source of truth both the automation loop and the operator console
    consult before acting.
    """

    model_config = ConfigDict(extra="forbid")

    run_id: str
    session_id: str = Field(..., description="Identifier of the underlying live browser/surface session.")
    state: ControlState = ControlState.AUTOMATION_RUNNING
    active_intervention: InterventionRequest | None = None
    operator_actions: list[OperatorAction] = Field(default_factory=list)
    resume_checkpoint_note: str | None = Field(
        default=None,
        description="What automation should re-verify before resuming, e.g. 'confirm review screen "
                    "still shows the same deposit amount the operator authorized.'",
    )
    last_updated: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def transition(self, new_state: ControlState) -> "HandoffState":
        """Returns a new HandoffState after validating the transition is
        legal. Raises ValueError otherwise. Immutable-update style keeps
        the state history reconstructable from a log of transitions.
        """
        if new_state not in _ALLOWED_TRANSITIONS[self.state]:
            raise ValueError(f"illegal transition: {self.state} -> {new_state}")
        return self.model_copy(update={"state": new_state, "last_updated": datetime.now(timezone.utc)})

    @model_validator(mode="after")
    def _intervention_presence_matches_state(self):
        if self.state in {ControlState.ESCALATION_PENDING, ControlState.HUMAN_CONTROLLED, ControlState.RESUMING}:
            if self.active_intervention is None:
                raise ValueError(f"state={self.state} requires an active_intervention")
        if self.state == ControlState.AUTOMATION_RUNNING and self.active_intervention is not None:
            raise ValueError("active_intervention must be cleared once back in AUTOMATION_RUNNING")
        return self
