import os
import re
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Callable
from uuid import uuid4
from playwright.sync_api import sync_playwright, Page, Browser, BrowserContext

from src.schemas.artifact import (
    CapabilityArtifact,
    CapabilityStep,
    ActionType,
    OutcomeClass,
    ParamType,
)
from src.schemas.execution import (
    ExecutionResult,
    ReplayStatus,
    StepTrace,
    StepOutcome,
    BusinessOutcomeDetail,
    DebugContext,
)
from src.schemas.escalation import EscalationReason
from src.engine.locator_resolver import resolve_locator, LocatorResolutionError
from src.engine.recovery_manager import RecoveryManager
from src.engine.transforms import apply_transform
from src.engine.error_handler import ErrorDiagnostics
from src.escalation.escalation_manager import EscalationManager
from src.safety.guardrail import PolicyGuardrail, SecurityViolationError
from src.safety.redaction import redact_data


class ReplayExecutor:
    """Production execution engine: re-runs a CapabilityArtifact deterministically

    with zero LLM in the loop, sub-second latency, and explicit error taxonomy.
    """

    def __init__(
        self,
        headless: bool = True,
        evidence_dir: str = "evidence",
        interactive_handler: Optional[Callable[[Any, Page], str]] = None,
    ):
        self.headless = headless
        self.evidence_dir = evidence_dir
        self.interactive_handler = interactive_handler
        self.diagnostics = ErrorDiagnostics(os.path.join(evidence_dir, "screenshots"))

    def run(
        self,
        artifact: CapabilityArtifact,
        inputs: Dict[str, Any],
        interactive_escalation: bool = False,
    ) -> ExecutionResult:
        """Execute a capability artifact against live browser with input parameters."""
        run_id = f"run_{uuid4().hex[:12]}"
        session_id = f"sess_{uuid4().hex[:8]}"
        started_at = datetime.now(timezone.utc)
        traces: list[StepTrace] = []
        active_step_id = "step_init"

        # 1. Validate inputs
        self._validate_inputs(artifact, inputs)

        # 2. Configure guardrail (fail-closed action and route policies)
        guardrail = PolicyGuardrail(
            allowed_domains=artifact.allowed_domains,
            allowed_actions=getattr(artifact, "allowed_actions", None),
            allowed_routes=getattr(artifact, "allowed_routes", None),
        )

        recovery_mgr = RecoveryManager(artifact.exceptional_rules)
        escalation_mgr = EscalationManager(
            run_id=run_id,
            session_id=session_id,
            evidence_dir=os.path.join(self.evidence_dir, "screenshots"),
        )
        if self.interactive_handler:
            escalation_mgr.interactive_handler = self.interactive_handler

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=self.headless)
            context = browser.new_context()
            page = context.new_page()

            try:
                # Execute each step sequentially
                for step_idx, step in enumerate(artifact.steps):
                    active_step_id = step.step_id
                    step_start = time.perf_counter()
                    trace_detail = None
                    locator_res = None

                    # 1. Action allowlist validation (enforced before reaching Playwright)
                    guardrail.validate_action(step.action)

                    # 2. Check for risky action gating (FAIL CLOSED on unattended execution)
                    needs_esc, esc_reason = guardrail.check_step_risk(step)
                    if needs_esc:
                        req = escalation_mgr.trigger_escalation(
                            page=page,
                            reason=EscalationReason.RISKY_STEP_APPROVAL,
                            explanation=esc_reason or "Risky action requires human authorization",
                            goal=artifact.metadata.description,
                            capability_id=artifact.metadata.id,
                            current_step_id=step.step_id,
                            proposed_action=f"Execute {step.action.value} on {step.step_id}",
                        )
                        if not interactive_escalation and not self.interactive_handler:
                            # FAIL CLOSED: Unattended execution of RISKY_IRREVERSIBLE action is strictly refused!
                            step_elapsed = round((time.perf_counter() - step_start) * 1000, 2)
                            traces.append(StepTrace(
                                step_id=step.step_id,
                                outcome=StepOutcome.SKIPPED_ESCALATED,
                                started_at=datetime.now(timezone.utc),
                                duration_ms=step_elapsed,
                                detail=f"Unattended execution blocked by policy: {esc_reason}",
                            ))
                            return ExecutionResult(
                                run_id=run_id,
                                capability_id=artifact.metadata.id,
                                capability_version=artifact.metadata.version,
                                status=ReplayStatus.ESCALATED,
                                started_at=started_at,
                                finished_at=datetime.now(timezone.utc),
                                step_traces=traces,
                                escalation_ref=req.id,
                            )
                        else:
                            # Interactive operator takeover on the LIVE page
                            updated_state = escalation_mgr.handle_operator_takeover(page=page, request=req)
                            actions_desc = "; ".join(a.description for a in updated_state.operator_actions)
                            trace_detail = actions_desc or "Operator authorized live session resumption"

                    # Pre-step check for recoverable interstitials (e.g. maintenance banner)
                    recovered, rule, msg = recovery_mgr.check_and_handle_conditions(page)
                    if recovered:
                        trace_detail = f"Dismissed recoverable condition: {rule.description}"
                    elif rule and rule.outcome_class == OutcomeClass.BUSINESS_OUTCOME:
                        traces.append(StepTrace(
                            step_id=step.step_id,
                            outcome=StepOutcome.OK,
                            started_at=datetime.now(timezone.utc),
                            duration_ms=round((time.perf_counter() - step_start) * 1000, 2),
                            detail=f"Detected business outcome: {rule.outcome_code}",
                        ))
                        return ExecutionResult(
                            run_id=run_id,
                            capability_id=artifact.metadata.id,
                            capability_version=artifact.metadata.version,
                            status=ReplayStatus.BUSINESS_OUTCOME,
                            started_at=started_at,
                            finished_at=datetime.now(timezone.utc),
                            step_traces=traces,
                            business_outcome=BusinessOutcomeDetail(
                                outcome_code=rule.outcome_code,
                                matched_rule_id=rule.rule_id,
                                message=msg or rule.description,
                            )
                        )

                    # Perform action
                    resolved_value = self._resolve_placeholders(step.input_value, inputs)
                    loc_target = None
                    if step.locator and step.action not in {ActionType.NAVIGATE, ActionType.WAIT_FOR}:
                        try:
                            loc_target, locator_res = resolve_locator(page, step.locator, timeout_per_candidate_ms=2500)
                        except LocatorResolutionError:
                            # 1. Reactive check: check if a slow-loading interstitial blocked the target element
                            recovered, rec_rule, rec_msg = recovery_mgr.check_and_handle_conditions(page, timeout_per_candidate_ms=1500)
                            if recovered:
                                traces.append(StepTrace(
                                    step_id=step.step_id,
                                    outcome=StepOutcome.OK,
                                    started_at=datetime.now(timezone.utc),
                                    duration_ms=round((time.perf_counter() - step_start) * 1000, 2),
                                    detail=f"Dismissed late interstitial: {rec_rule.description}",
                                ))
                                # Retry resolving target locator after dismissal
                                loc_target, locator_res = resolve_locator(page, step.locator, timeout_per_candidate_ms=2500)
                            else:
                                # 2. Check if an exceptional business outcome rule explains why locator was not found
                                if rec_rule and rec_rule.outcome_class == OutcomeClass.BUSINESS_OUTCOME:
                                    return ExecutionResult(
                                        run_id=run_id,
                                        capability_id=artifact.metadata.id,
                                        capability_version=artifact.metadata.version,
                                        status=ReplayStatus.BUSINESS_OUTCOME,
                                        started_at=started_at,
                                        finished_at=datetime.now(timezone.utc),
                                        step_traces=traces,
                                        business_outcome=BusinessOutcomeDetail(
                                            outcome_code=rec_rule.outcome_code,
                                            matched_rule_id=rec_rule.rule_id,
                                            message=rec_msg or rec_rule.description,
                                        )
                                    )
                                raise

                    # Execute concrete action
                    if step.action == ActionType.NAVIGATE:
                        url = self._resolve_placeholders(step.target_url, inputs) or ""
                        if url.startswith("/"):
                            base_domain = artifact.allowed_domains[0]
                            if not base_domain.startswith("http"):
                                base_domain = f"http://{base_domain}"
                            url = f"{base_domain.rstrip('/')}{url}"
                        guardrail.validate_url(url)
                        page.goto(url)
                        guardrail.validate_url(page.url)
                    elif step.action == ActionType.CLICK:
                        loc_target.click()
                        guardrail.validate_url(page.url)
                    elif step.action == ActionType.TYPE:
                        loc_target.fill(resolved_value or "")
                    elif step.action == ActionType.SELECT:
                        loc_target.select_option(value=resolved_value)
                        guardrail.validate_url(page.url)
                    elif step.action == ActionType.DISMISS:
                        loc_target.click()
                        guardrail.validate_url(page.url)
                    elif step.action == ActionType.WAIT_FOR:
                        page.wait_for_timeout(step.max_wait_ms)
                    elif step.action == ActionType.EXTRACT:
                        pass

                    # Post-action check for exceptional business outcome
                    recovered, rule, msg = recovery_mgr.check_and_handle_conditions(page)
                    if recovered:
                        trace_detail = f"Dismissed interstitial: {rule.description}"
                    elif rule and rule.outcome_class == OutcomeClass.BUSINESS_OUTCOME:
                        traces.append(StepTrace(
                            step_id=step.step_id,
                            outcome=StepOutcome.OK,
                            locator_resolution=locator_res,
                            started_at=datetime.now(timezone.utc),
                            duration_ms=round((time.perf_counter() - step_start) * 1000, 2),
                            detail=f"Business outcome detected: {rule.outcome_code}",
                        ))
                        return ExecutionResult(
                            run_id=run_id,
                            capability_id=artifact.metadata.id,
                            capability_version=artifact.metadata.version,
                            status=ReplayStatus.BUSINESS_OUTCOME,
                            started_at=started_at,
                            finished_at=datetime.now(timezone.utc),
                            step_traces=traces,
                            business_outcome=BusinessOutcomeDetail(
                                outcome_code=rule.outcome_code,
                                matched_rule_id=rule.rule_id,
                                message=msg or rule.description,
                            )
                        )

                    # Post-action checkpoint assertion
                    if step.checkpoint:
                        self._verify_checkpoint(page, step.checkpoint)

                    step_elapsed = round((time.perf_counter() - step_start) * 1000, 2)
                    traces.append(StepTrace(
                        step_id=step.step_id,
                        outcome=StepOutcome.OK,
                        locator_resolution=locator_res,
                        started_at=datetime.now(timezone.utc),
                        duration_ms=step_elapsed,
                        detail=trace_detail,
                    ))

                # Check if business outcome occurred on final landing
                _, final_rule, final_msg = recovery_mgr.check_and_handle_conditions(page)
                if final_rule and final_rule.outcome_class == OutcomeClass.BUSINESS_OUTCOME:
                    return ExecutionResult(
                        run_id=run_id,
                        capability_id=artifact.metadata.id,
                        capability_version=artifact.metadata.version,
                        status=ReplayStatus.BUSINESS_OUTCOME,
                        started_at=started_at,
                        finished_at=datetime.now(timezone.utc),
                        step_traces=traces,
                        business_outcome=BusinessOutcomeDetail(
                            outcome_code=final_rule.outcome_code,
                            matched_rule_id=final_rule.rule_id,
                            message=final_msg or final_rule.description,
                        )
                    )

                # Verify overall success checkpoint
                self._verify_checkpoint(page, artifact.success_checkpoint)

                # Extract typed outputs
                extracted_outputs = {}
                for out_field in artifact.output_parameters:
                    loc, _ = resolve_locator(page, out_field.extraction_locator, timeout_per_candidate_ms=3000)
                    raw_text = loc.inner_text().strip()
                    transformed = apply_transform(raw_text, out_field.transform)
                    if out_field.type == ParamType.NUMBER and isinstance(transformed, str):
                        try:
                            transformed = float(transformed)
                        except ValueError:
                            pass
                    elif out_field.type == ParamType.BOOLEAN and isinstance(transformed, str):
                        transformed = transformed.lower() in ("true", "1", "yes")
                    extracted_outputs[out_field.name] = transformed

                sanitized_outputs = redact_data(extracted_outputs)
                has_recovery = any(t.detail and "Dismissed" in t.detail for t in traces)
                status = ReplayStatus.RECOVERED if has_recovery else ReplayStatus.SUCCESS

                return ExecutionResult(
                    run_id=run_id,
                    capability_id=artifact.metadata.id,
                    capability_version=artifact.metadata.version,
                    status=status,
                    started_at=started_at,
                    finished_at=datetime.now(timezone.utc),
                    step_traces=traces,
                    outputs=sanitized_outputs,
                )

            except Exception as e:
                try:
                    _, check_rule, check_msg = recovery_mgr.check_and_handle_conditions(page)
                    if check_rule and check_rule.outcome_class == OutcomeClass.BUSINESS_OUTCOME:
                        return ExecutionResult(
                            run_id=run_id,
                            capability_id=artifact.metadata.id,
                            capability_version=artifact.metadata.version,
                            status=ReplayStatus.BUSINESS_OUTCOME,
                            started_at=started_at,
                            finished_at=datetime.now(timezone.utc),
                            step_traces=traces,
                            business_outcome=BusinessOutcomeDetail(
                                outcome_code=check_rule.outcome_code,
                                matched_rule_id=check_rule.rule_id,
                                message=check_msg or check_rule.description,
                            )
                        )
                except Exception:
                    pass

                debug_ctx = self.diagnostics.capture_failure(
                    page=page,
                    run_id=run_id,
                    step_id=active_step_id,
                    expected="Successful deterministic execution of step",
                    exception=e,
                )
                return ExecutionResult(
                    run_id=run_id,
                    capability_id=artifact.metadata.id,
                    capability_version=artifact.metadata.version,
                    status=ReplayStatus.HARD_FAILURE,
                    started_at=started_at,
                    finished_at=datetime.now(timezone.utc),
                    step_traces=traces,
                    debug=debug_ctx,
                )
            finally:
                context.close()
                browser.close()

    def _validate_inputs(self, artifact: CapabilityArtifact, inputs: Dict[str, Any]) -> None:
        """Validate input parameters against schema."""
        for param in artifact.input_parameters:
            if param.required and param.name not in inputs:
                raise ValueError(f"Missing required input parameter: '{param.name}'")
            if param.name in inputs and param.pattern:
                val = str(inputs[param.name])
                if not re.match(param.pattern, val):
                    raise ValueError(f"Input '{param.name}' value '{val}' does not match pattern '{param.pattern}'")

    def _resolve_placeholders(self, text: Optional[str], inputs: Dict[str, Any]) -> Optional[str]:
        """Interpolate {param_name} placeholders with runtime inputs."""
        if not text:
            return text
        result = text
        for k, v in inputs.items():
            result = result.replace(f"{{{k}}}", str(v))
        return result

    def _verify_checkpoint(self, page: Page, checkpoint) -> None:
        """Assert checkpoint postcondition on page."""
        if checkpoint.locator:
            loc, _ = resolve_locator(page, checkpoint.locator, timeout_per_candidate_ms=checkpoint.timeout_ms)
            if not loc.is_visible():
                raise AssertionError(f"Checkpoint failed: Locator '{checkpoint.description}' not visible")
        if checkpoint.expected_url_pattern:
            if not re.search(checkpoint.expected_url_pattern, page.url):
                raise AssertionError(f"Checkpoint failed: URL '{page.url}' does not match '{checkpoint.expected_url_pattern}'")
