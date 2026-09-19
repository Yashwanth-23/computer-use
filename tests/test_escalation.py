import os
import pytest
from playwright.sync_api import sync_playwright

from src.schemas.escalation import ControlState, EscalationReason, HandoffState
from src.schemas.artifact import CapabilityStep, ActionType, RiskLevel, MultiStrategyLocator, LocatorCandidate, LocatorStrategy
from src.escalation.escalation_manager import EscalationManager
from src.safety.guardrail import PolicyGuardrail


def test_escalation_lifecycle_on_live_session(tmp_path):
    """Verify live browser session pauses, transfers control to operator, and resumes on same session."""
    run_id = "test_run_12345"
    session_id = "test_sess_999"
    escalation_mgr = EscalationManager(run_id=run_id, session_id=session_id, evidence_dir=str(tmp_path))

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
        def simulated_operator(req, live_page):
            # Verify operator is operating the EXACT SAME live page
            assert "member-lookup" in live_page.url
            return "Simulated operator verified member and authorized action"

        escalation_mgr.interactive_handler = simulated_operator
        updated_state = escalation_mgr.handle_operator_takeover(page=page, request=request)

        # 3. Verify control returned to automation
        assert updated_state.state == ControlState.AUTOMATION_RUNNING
        assert updated_state.active_intervention is None
        assert len(updated_state.operator_actions) == 1
        assert "Simulated operator verified" in updated_state.operator_actions[0].description

        browser.close()


def test_unattended_risky_replay_fails_closed(tmp_path):
    """Verify that an unattended/headless replay hitting a RISKY_IRREVERSIBLE step fails closed.
    
    It must halt immediately with ReplayStatus.ESCALATED, refuse execution, and never claim human approval.
    """
    from src.schemas.artifact import CapabilityArtifact, CapabilityMetadata, Checkpoint
    from src.schemas.execution import ReplayStatus, StepOutcome
    from src.engine.replay_executor import ReplayExecutor

    dummy_locator = MultiStrategyLocator(
        chain=[LocatorCandidate(strategy=LocatorStrategy.STABLE_ID, value="#btnSearch")],
        reasoning="Test locator"
    )

    risky_step = CapabilityStep(
        step_id="step_confirm_action",
        action=ActionType.CLICK,
        locator=dummy_locator,
        is_risky=RiskLevel.RISKY_IRREVERSIBLE,
        risk_justification="Commits financial transaction to external core ledger"
    )

    artifact = CapabilityArtifact(
        schema_version="1.0",
        metadata=CapabilityMetadata(
            name="test_risky_flow",
            version="1.0.0",
            app_id="apex_core_v4",
            description="Test risky flow"
        ),
        steps=[
            CapabilityStep(
                step_id="step_nav",
                action=ActionType.NAVIGATE,
                target_url="http://127.0.0.1:8000/portal/member-lookup",
                is_risky=RiskLevel.SAFE,
            ),
            risky_step,
        ],
        success_checkpoint=Checkpoint(description="Done", expected_url_pattern=r".*"),
        allowed_domains=["127.0.0.1:8000"],
    )

    executor = ReplayExecutor(headless=True, evidence_dir=str(tmp_path))
    # Non-interactive execution (unattended default)
    res = executor.run(artifact, inputs={}, interactive_escalation=False)

    # Must FAIL CLOSED
    assert res.status == ReplayStatus.ESCALATED
    assert res.escalation_ref is not None
    assert res.outputs is None
    assert len(res.step_traces) == 2
    # The risky step was NOT executed
    risky_trace = res.step_traces[1]
    assert risky_trace.step_id == "step_confirm_action"
    assert risky_trace.outcome == StepOutcome.SKIPPED_ESCALATED
    assert "Unattended execution blocked by policy" in risky_trace.detail
    # Proves no fake approval was fabricated
    assert "Human operator approved" not in risky_trace.detail


def test_subaccount_artifact_unattended_blocks_and_supervised_succeeds(tmp_path):
    """End-to-end verification of capability_open_subaccount.json:
    - Unattended execution must block at step_5_confirm_submit and return ESCALATED.
    - Supervised execution with human operator confirmation must succeed and return receipt.
    """
    from src.schemas.artifact import CapabilityArtifact
    from src.schemas.execution import ReplayStatus, StepOutcome
    from src.engine.replay_executor import ReplayExecutor
    from mock_target import core_data

    core_data.reset_all()
    with open("evidence/capability_open_subaccount.json", "r", encoding="utf-8") as f:
        artifact = CapabilityArtifact.model_validate_json(f.read())

    executor = ReplayExecutor(headless=True, evidence_dir=str(tmp_path))

    # 1. Unattended replay -> FAIL CLOSED
    res_unattended = executor.run(
        artifact,
        inputs={"member_id": "1001", "product_type": "HOLIDAY_CLUB", "initial_deposit": "50.00"},
        interactive_escalation=False,
    )
    assert res_unattended.status == ReplayStatus.ESCALATED
    assert res_unattended.escalation_ref is not None
    assert res_unattended.outputs is None
    # Step 5 was skipped
    assert any(
        t.step_id == "step_5_confirm_submit" and t.outcome == StepOutcome.SKIPPED_ESCALATED
        for t in res_unattended.step_traces
    )
    # Step 6 was not reached
    assert not any(t.step_id == "step_6_extract_receipt" for t in res_unattended.step_traces)

    # 2. Supervised replay with operator sign-off -> SUCCESS
    core_data.reset_all()

    def operator_approver(intervention, live_page):
        assert "sub-account/review" in live_page.url
        return "Supervisor verified member identity and authorized $50.00 holiday club creation"

    executor.interactive_handler = operator_approver
    res_supervised = executor.run(
        artifact,
        inputs={"member_id": "1001", "product_type": "HOLIDAY_CLUB", "initial_deposit": "50.00"},
        interactive_escalation=True,
    )
    assert res_supervised.status == ReplayStatus.SUCCESS
    assert res_supervised.outputs is not None
    assert "receipt_id" in res_supervised.outputs
    assert res_supervised.outputs["receipt_id"].startswith("APX-")

