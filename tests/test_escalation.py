import os
import pytest
from playwright.sync_api import sync_playwright

from src.schemas.escalation import ControlState, EscalationReason, HandoffState
from src.schemas.artifact import CapabilityStep, ActionType, RiskLevel, MultiStrategyLocator, LocatorCandidate, LocatorStrategy
from src.escalation.escalation_manager import EscalationManager
from src.safety.guardrail import PolicyGuardrail


def test_escalation_lifecycle_on_live_session():
    """Verify live browser session pauses, transfers control to operator, and resumes on same session."""
    run_id = "test_run_12345"
    session_id = "test_sess_999"
    escalation_mgr = EscalationManager(run_id=run_id, session_id=session_id, evidence_dir="evidence/screenshots")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto("http://127.0.0.1:8000/portal/member-lookup")

        # 1. Trigger escalation
        request = escalation_mgr.trigger_escalation(
            page=page,
            reason=EscalationReason.RISKY_STEP_APPROVAL,
            explanation="Test risky step requiring signoff",
            goal="Open sub-account",
            capability_id="cap_test",
            current_step_id="step_confirm",
            proposed_action="Confirm sub-account opening with $50 deposit",
        )

        assert request.run_id == run_id
        assert request.reason == EscalationReason.RISKY_STEP_APPROVAL
        assert escalation_mgr.handoff_state.state == ControlState.ESCALATION_PENDING
        assert os.path.exists(request.screenshot_ref)

        # 2. Operator takes over live session and resolves
        def custom_operator(req, live_page):
            # Verify human is operating the EXACT SAME live page
            assert "member-lookup" in live_page.url
            return "Operator manually verified member and authorized action"

        escalation_mgr.interactive_handler = custom_operator
        updated_state = escalation_mgr.handle_operator_takeover(page=page, request=request)

        # 3. Verify control returned to automation
        assert updated_state.state == ControlState.AUTOMATION_RUNNING
        assert updated_state.active_intervention is None
        assert len(updated_state.operator_actions) == 1
        assert "Operator resolved via custom handler" in updated_state.operator_actions[0].description

        browser.close()
