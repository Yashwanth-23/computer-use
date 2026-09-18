"""
Negative-path tests for the schema layer: prove the validators reject
malformed artifacts with clear errors, not just that well-formed ones pass.
Run: python3 -m pytest tests/test_schemas.py -v
"""
import pytest
from pydantic import ValidationError

from src.schemas.artifact import (
    ActionType, CapabilityArtifact, CapabilityMetadata, CapabilityStep,
    Checkpoint, DetectionSignature, ExceptionalRule, InputParameter,
    LocatorCandidate, LocatorStrategy, MultiStrategyLocator, OutcomeClass,
    OutputField, ParamType, RiskLevel,
)
from src.schemas.execution import (
    BusinessOutcomeDetail, DebugContext, ExecutionResult, ReplayStatus,
)
from src.schemas.escalation import ControlState, HandoffState, InterventionRequest, EscalationReason


def _simple_locator(value="#foo") -> MultiStrategyLocator:
    return MultiStrategyLocator(
        chain=[LocatorCandidate(strategy=LocatorStrategy.STABLE_ID, value=value)],
        reasoning="test locator",
    )


def _minimal_artifact_kwargs(**overrides):
    base = dict(
        metadata=CapabilityMetadata(name="test_cap", app_id="test_app", description="test"),
        steps=[
            CapabilityStep(step_id="s1", action=ActionType.CLICK, locator=_simple_locator()),
        ],
        success_checkpoint=Checkpoint(description="done", locator=_simple_locator()),
        allowed_domains=["localhost"],
    )
    base.update(overrides)
    return base


class TestCapabilityStep:
    def test_click_requires_locator(self):
        with pytest.raises(ValidationError, match="requires a locator"):
            CapabilityStep(step_id="s1", action=ActionType.CLICK)

    def test_navigate_requires_target_url(self):
        with pytest.raises(ValidationError, match="requires target_url"):
            CapabilityStep(step_id="s1", action=ActionType.NAVIGATE)

    def test_navigate_does_not_require_locator(self):
        # should not raise
        CapabilityStep(step_id="s1", action=ActionType.NAVIGATE, target_url="/home")

    def test_risky_step_requires_justification(self):
        with pytest.raises(ValidationError, match="risk_justification"):
            CapabilityStep(
                step_id="s1", action=ActionType.CLICK, locator=_simple_locator(),
                is_risky=RiskLevel.RISKY_IRREVERSIBLE,
            )

    def test_risky_step_with_justification_ok(self):
        CapabilityStep(
            step_id="s1", action=ActionType.CLICK, locator=_simple_locator(),
            is_risky=RiskLevel.RISKY_IRREVERSIBLE, risk_justification="opens an account",
        )


class TestMultiStrategyLocator:
    def test_requires_at_least_one_candidate(self):
        with pytest.raises(ValidationError):
            MultiStrategyLocator(chain=[], reasoning="empty")

    def test_rejects_duplicate_consecutive_candidates(self):
        with pytest.raises(ValidationError, match="duplicate"):
            MultiStrategyLocator(
                chain=[
                    LocatorCandidate(strategy=LocatorStrategy.STABLE_ID, value="#foo"),
                    LocatorCandidate(strategy=LocatorStrategy.STABLE_ID, value="#foo"),
                ],
                reasoning="dup",
            )


class TestCheckpoint:
    def test_requires_locator_or_url_pattern(self):
        with pytest.raises(ValidationError, match="needs at least one"):
            Checkpoint(description="no condition given")

    def test_url_pattern_alone_is_valid(self):
        Checkpoint(description="ok", expected_url_pattern=r"/confirmation$")


class TestExceptionalRule:
    def test_recoverable_requires_recovery_action(self):
        with pytest.raises(ValidationError, match="RECOVERABLE rules require"):
            ExceptionalRule(
                rule_id="r1",
                outcome_class=OutcomeClass.RECOVERABLE,
                signature=DetectionSignature(text_pattern="maintenance"),
                outcome_code="MAINT",
                description="test",
            )

    def test_business_outcome_must_not_have_recovery_action(self):
        with pytest.raises(ValidationError, match="only meaningful for RECOVERABLE"):
            ExceptionalRule(
                rule_id="r1",
                outcome_class=OutcomeClass.BUSINESS_OUTCOME,
                signature=DetectionSignature(text_pattern="not found"),
                outcome_code="NF",
                description="test",
                recovery_action=CapabilityStep(step_id="x", action=ActionType.NAVIGATE, target_url="/"),
            )


class TestCapabilityArtifact:
    def test_valid_minimal_artifact(self):
        CapabilityArtifact(**_minimal_artifact_kwargs())

    def test_duplicate_step_ids_rejected(self):
        with pytest.raises(ValidationError, match="unique"):
            CapabilityArtifact(**_minimal_artifact_kwargs(
                steps=[
                    CapabilityStep(step_id="s1", action=ActionType.CLICK, locator=_simple_locator()),
                    CapabilityStep(step_id="s1", action=ActionType.CLICK, locator=_simple_locator()),
                ]
            ))

    def test_undeclared_parameter_placeholder_rejected(self):
        with pytest.raises(ValidationError, match="undeclared parameter"):
            CapabilityArtifact(**_minimal_artifact_kwargs(
                steps=[
                    CapabilityStep(
                        step_id="s1", action=ActionType.TYPE, locator=_simple_locator(),
                        input_value="{member_id}",  # not declared in input_parameters
                    ),
                ],
            ))

    def test_declared_parameter_placeholder_accepted(self):
        CapabilityArtifact(**_minimal_artifact_kwargs(
            input_parameters=[
                InputParameter(name="member_id", type=ParamType.STRING, description="id"),
            ],
            steps=[
                CapabilityStep(
                    step_id="s1", action=ActionType.TYPE, locator=_simple_locator(),
                    input_value="{member_id}",
                ),
            ],
        ))

    def test_outputs_without_extract_step_rejected(self):
        with pytest.raises(ValidationError, match="no step has action=EXTRACT"):
            CapabilityArtifact(**_minimal_artifact_kwargs(
                output_parameters=[
                    OutputField(
                        name="balance", type=ParamType.NUMBER, description="x",
                        extraction_locator=_simple_locator(),
                    ),
                ],
                # steps still only has a CLICK step, no EXTRACT
            ))

    def test_empty_allowed_domains_rejected(self):
        with pytest.raises(ValidationError):
            CapabilityArtifact(**_minimal_artifact_kwargs(allowed_domains=[]))

    def test_enum_param_without_values_rejected(self):
        with pytest.raises(ValidationError, match="enum_values is required"):
            InputParameter(name="x", type=ParamType.ENUM, description="test")


class TestExecutionResult:
    def _base_kwargs(self, **overrides):
        base = dict(run_id="run_1", capability_id="cap_1", capability_version="1.0.0")
        base.update(overrides)
        return base

    def test_success_requires_outputs(self):
        with pytest.raises(ValidationError, match="requires 'outputs'"):
            ExecutionResult(**self._base_kwargs(status=ReplayStatus.SUCCESS))

    def test_success_with_outputs_ok(self):
        ExecutionResult(**self._base_kwargs(status=ReplayStatus.SUCCESS, outputs={"balance": 100.0}))

    def test_success_must_not_carry_debug(self):
        with pytest.raises(ValidationError, match="must not set 'debug'"):
            ExecutionResult(**self._base_kwargs(
                status=ReplayStatus.SUCCESS,
                outputs={"x": 1},
                debug=DebugContext(failed_step_id="s1", expected="a", observed="b"),
            ))

    def test_business_outcome_requires_detail(self):
        with pytest.raises(ValidationError, match="requires 'business_outcome'"):
            ExecutionResult(**self._base_kwargs(status=ReplayStatus.BUSINESS_OUTCOME))

    def test_hard_failure_requires_debug(self):
        with pytest.raises(ValidationError, match="requires 'debug'"):
            ExecutionResult(**self._base_kwargs(status=ReplayStatus.HARD_FAILURE))

    def test_business_outcome_shape_ok(self):
        ExecutionResult(**self._base_kwargs(
            status=ReplayStatus.BUSINESS_OUTCOME,
            business_outcome=BusinessOutcomeDetail(
                outcome_code="MEMBER_NOT_FOUND", matched_rule_id="r1", message="not found",
            ),
        ))


class TestHandoffState:
    def test_valid_forward_transition(self):
        state = HandoffState(run_id="r1", session_id="s1", state=ControlState.AUTOMATION_RUNNING)
        intervention = InterventionRequest(
            run_id="r1", goal="test goal", reason=EscalationReason.LOCATOR_UNRESOLVED,
            explanation="couldn't find button", screenshot_ref="shot.png",
        )
        pending = state.model_copy(update={
            "state": ControlState.ESCALATION_PENDING,
            "active_intervention": intervention,
        })
        human = pending.transition(ControlState.HUMAN_CONTROLLED)
        assert human.state == ControlState.HUMAN_CONTROLLED

    def test_illegal_transition_rejected(self):
        state = HandoffState(run_id="r1", session_id="s1", state=ControlState.AUTOMATION_RUNNING)
        with pytest.raises(ValueError, match="illegal transition"):
            state.transition(ControlState.HUMAN_CONTROLLED)  # can't skip ESCALATION_PENDING

    def test_pending_state_requires_active_intervention(self):
        with pytest.raises(ValidationError, match="requires an active_intervention"):
            HandoffState(run_id="r1", session_id="s1", state=ControlState.ESCALATION_PENDING)

    def test_automation_running_must_not_carry_intervention(self):
        intervention = InterventionRequest(
            run_id="r1", goal="test", reason=EscalationReason.MAX_STEPS_EXCEEDED,
            explanation="x", screenshot_ref="shot.png",
        )
        with pytest.raises(ValidationError, match="must be cleared"):
            HandoffState(
                run_id="r1", session_id="s1", state=ControlState.AUTOMATION_RUNNING,
                active_intervention=intervention,
            )
