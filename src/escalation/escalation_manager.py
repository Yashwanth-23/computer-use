import os
import time
from datetime import datetime, timezone
from typing import Callable, Optional
from playwright.sync_api import Page

from src.schemas.escalation import (
    ControlState,
    EscalationReason,
    HandoffState,
    InterventionRequest,
    OperatorAction,
)


class EscalationManager:
    """Manages the human-in-the-loop control-transfer seam on a live Playwright session."""

    def __init__(
        self,
        run_id: str,
        session_id: str,
        evidence_dir: str = "evidence/screenshots",
        interactive_handler: Optional[Callable[[InterventionRequest, Page], str]] = None,
    ):
        self.run_id = run_id
        self.session_id = session_id
        self.evidence_dir = evidence_dir
        self.interactive_handler = interactive_handler
        os.makedirs(self.evidence_dir, exist_ok=True)

        self.handoff_state = HandoffState(
            run_id=run_id,
            session_id=session_id,
            state=ControlState.AUTOMATION_RUNNING,
        )

    def trigger_escalation(
        self,
        page: Page,
        reason: EscalationReason,
        explanation: str,
        goal: str,
        capability_id: Optional[str] = None,
        current_step_id: Optional[str] = None,
        proposed_action: Optional[str] = None,
    ) -> InterventionRequest:
        """Pauses automation, captures live session context, and creates an InterventionRequest."""
        timestamp_str = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        screenshot_filename = f"escalation_{self.run_id[:8]}_{current_step_id or 'step'}_{timestamp_str}.png"
        screenshot_path = os.path.join(self.evidence_dir, screenshot_filename).replace("\\", "/")

        try:
            # Visually mask sensitive selectors before escalation screenshot
            page.evaluate("""() => {
                document.querySelectorAll('.ssn, .balance, [data-sensitive], input[type="password"]').forEach(el => {
                    el.style.filter = 'blur(6px)';
                });
            }""")
        except Exception:
            pass

        try:
            page.screenshot(path=screenshot_path)
        except Exception:
            screenshot_path = "screenshot_capture_failed.png"

        request = InterventionRequest(
            run_id=self.run_id,
            capability_id=capability_id,
            goal=goal,
            reason=reason,
            current_step_id=current_step_id,
            explanation=explanation,
            screenshot_ref=screenshot_path,
            proposed_action=proposed_action,
        )

        # Transition: AUTOMATION_RUNNING -> ESCALATION_PENDING
        self.handoff_state = self.handoff_state.transition(ControlState.ESCALATION_PENDING)
        self.handoff_state.active_intervention = request
        return request

    def handle_operator_takeover(
        self,
        page: Page,
        request: InterventionRequest,
        operator_action_desc: Optional[str] = None,
    ) -> HandoffState:
        """Transfers control of the live session to the human operator and manages resumption."""
        # Transition: ESCALATION_PENDING -> HUMAN_CONTROLLED
        self.handoff_state = self.handoff_state.transition(ControlState.HUMAN_CONTROLLED)

        # If a custom interactive handler is provided, invoke it
        if self.interactive_handler:
            resolution = self.interactive_handler(request, page)
            action_desc = operator_action_desc or f"Operator resolved: {resolution}"
        else:
            # Default CLI interactive prompt
            print("\n" + "=" * 72)
            print("🚨 HUMAN INTERVENTION REQUIRED (Automation Paused on Live Surface)")
            print("=" * 72)
            print(f"Run ID:          {request.run_id}")
            print(f"Capability:      {request.capability_id or 'N/A'}")
            print(f"Current Step:    {request.current_step_id or 'N/A'}")
            print(f"Reason:          {request.reason.value}")
            print(f"Explanation:     {request.explanation}")
            if request.proposed_action:
                print(f"Proposed Action: {request.proposed_action}")
            print(f"Live Screenshot: {request.screenshot_ref}")
            print(f"Current URL:     {page.url}")
            print("-" * 72)
            print("Controls: Human has control of the browser session.")
            print("You may interact with the live browser or verify state.")
            user_input = input("Enter 'resume' (or 'r') to return control to automation, or 'abort': ").strip().lower()
            if user_input in {"abort", "q", "quit"}:
                self.handoff_state = self.handoff_state.transition(ControlState.ABANDONED)
                raise RuntimeError(f"Replay aborted by human operator during intervention {request.id}")

            action_desc = f"Operator confirmed via CLI prompt: '{user_input}'"

        # Record human action in audit trail
        self.handoff_state.operator_actions.append(OperatorAction(description=action_desc))

        # Transition: HUMAN_CONTROLLED -> RESUMING -> AUTOMATION_RUNNING
        self.handoff_state = self.handoff_state.transition(ControlState.RESUMING)
        self.handoff_state = self.handoff_state.transition(ControlState.AUTOMATION_RUNNING)
        self.handoff_state.active_intervention = None
        return self.handoff_state
