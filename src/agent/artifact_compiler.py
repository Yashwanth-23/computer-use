from typing import List, Dict, Any
from src.schemas.artifact import (
    CapabilityArtifact,
    CapabilityMetadata,
    CapabilityStep,
    ActionType,
    MultiStrategyLocator,
    LocatorCandidate,
    LocatorStrategy,
    Checkpoint,
    InputParameter,
    OutputField,
    ParamType,
    RiskLevel,
    ExceptionalRule,
    DetectionSignature,
    OutcomeClass,
)


def compile_capability_from_trace(
    capability_name: str,
    description: str,
    target_url: str,
    allowed_domains: List[str],
    steps_record: List[Dict[str, Any]],
    inputs_meta: List[Dict[str, Any]],
    outputs_meta: List[Dict[str, Any]],
) -> CapabilityArtifact:
    """Compiles a discovered execution trace into a formal, typed CapabilityArtifact."""
    compiled_steps = []

    for idx, s in enumerate(steps_record):
        step_id = f"step_{idx + 1}_{s['action'].lower()}"
        action_type = ActionType(s["action"].lower())

        locator = None
        if s.get("element"):
            el = s["element"]
            candidates = []

            # 1. Primary: Stable Control ID
            if el.get("element_id"):
                candidates.append(LocatorCandidate(
                    strategy=LocatorStrategy.STABLE_ID,
                    value=f"#{el['element_id']}",
                    note="Server-generated ASP.NET control ID; durable within release."
                ))

            is_extract = action_type == ActionType.EXTRACT
            acc_name = (el.get("accessible_name") or "").strip()
            # If the accessible name looks like dynamic data (currency, numeric value, or extraction step),
            # never bake it into locator candidates.
            is_dynamic_value = (
                is_extract
                or acc_name.startswith("$")
                or any(char.isdigit() for char in acc_name)
            )

            # 2. Fallback: Accessible role and name (only if NOT dynamic data)
            if el.get("role") and acc_name and not is_dynamic_value:
                candidates.append(LocatorCandidate(
                    strategy=LocatorStrategy.ACCESSIBLE_ROLE_NAME,
                    value=f"role={el['role']}[name=\"{acc_name}\"]",
                    note="Resilient across control-prefix shifts if label text remains constant."
                ))

            # 3. Fallback: Label proximity
            if is_extract:
                field_hint = "Savings" if "savings" in el.get("element_id", "").lower() else ("Checking" if "checking" in el.get("element_id", "").lower() else "")
                if field_hint:
                    candidates.append(LocatorCandidate(
                        strategy=LocatorStrategy.LABEL_PROXIMITY,
                        value=f"tr:has(td:has-text(\"{field_hint}\")) >> span",
                        note=f"Positioned relative to '{field_hint}' row header without value dependency."
                    ))
            elif acc_name and not is_dynamic_value:
                candidates.append(LocatorCandidate(
                    strategy=LocatorStrategy.LABEL_PROXIMITY,
                    value=f"label:has-text(\"{acc_name}\") >> xpath=following::input[1]",
                    note="Layout anchor if ID and name attributes change."
                ))

            # 4. Fallback: Structural XPath
            if el.get("xpath"):
                candidates.append(LocatorCandidate(
                    strategy=LocatorStrategy.STRUCTURAL_PATH,
                    value=el["xpath"],
                    note="Structural DOM position fallback."
                ))
            elif is_extract and el.get("element_id"):
                candidates.append(LocatorCandidate(
                    strategy=LocatorStrategy.STRUCTURAL_PATH,
                    value=f"//span[@id='{el['element_id']}']",
                    note="Structural XPath fallback."
                ))

            # Fallback if no specific candidates
            if not candidates:
                candidates.append(LocatorCandidate(
                    strategy=LocatorStrategy.STABLE_ID,
                    value=el.get("selector", "body"),
                    note="Default selector fallback."
                ))

            locator = MultiStrategyLocator(
                chain=candidates,
                reasoning=(
                    "Priority starts with server-generated control IDs durable in legacy banking apps. "
                    "Falls back to structural layout proximity relative to stable row headers without value dependency."
                )
            )

        # For NAVIGATE, assert URL reached. For interactive actions, keep checkpoint None unless explicit
        checkpoint = None
        if action_type == ActionType.NAVIGATE:
            checkpoint = Checkpoint(
                description="Navigation completed",
                expected_url_pattern=r"/portal/.*",
            )

        step_obj = CapabilityStep(
            step_id=step_id,
            action=action_type,
            locator=locator,
            input_value=s.get("input_value"),
            target_url=s.get("target_url"),
            is_risky=RiskLevel.SAFE,
            checkpoint=checkpoint,
        )
        compiled_steps.append(step_obj)

    # Compile input parameters
    input_params = []
    for inp in inputs_meta:
        input_params.append(InputParameter(
            name=inp["name"],
            type=ParamType(inp.get("type", "string")),
            required=inp.get("required", True),
            description=inp.get("description", f"Parameter {inp['name']}"),
            pattern=inp.get("pattern"),
        ))

    # Compile output parameters
    output_params = []
    for out in outputs_meta:
        out_el_id = out["element_id"]
        out_locator = MultiStrategyLocator(
            chain=[
                LocatorCandidate(strategy=LocatorStrategy.STABLE_ID, value=f"#{out_el_id}"),
                LocatorCandidate(strategy=LocatorStrategy.STRUCTURAL_PATH, value=f"//span[@id='{out_el_id}']"),
            ],
            reasoning="Target balance span element in legacy nested table grid."
        )
        output_params.append(OutputField(
            name=out["name"],
            type=ParamType(out.get("type", "number")),
            description=out.get("description", f"Extracted field {out['name']}"),
            extraction_locator=out_locator,
            transform=out.get("transform", "strip_currency_symbol"),
        ))

    # Success checkpoint: balance span or page element rendered
    success_chk = Checkpoint(
        description="Core records rendered with balance values present",
        locator=output_params[0].extraction_locator if output_params else None,
        expected_url_pattern=r"/portal/.*",
    )

    # Exceptional rules
    not_found_loc = MultiStrategyLocator(
        chain=[
            LocatorCandidate(strategy=LocatorStrategy.STABLE_ID, value="#ctl00_MainContent_lblResultMessage"),
            LocatorCandidate(strategy=LocatorStrategy.TEXT_MATCH, value="Member Record Not Found"),
        ],
        reasoning="Legacy result message span displays record not found notifications."
    )

    maintenance_loc = MultiStrategyLocator(
        chain=[LocatorCandidate(strategy=LocatorStrategy.STABLE_ID, value="#pnlMaintenanceAlert")],
        reasoning="Maintenance modal banner container."
    )

    ack_button_loc = MultiStrategyLocator(
        chain=[LocatorCandidate(strategy=LocatorStrategy.STABLE_ID, value="#btnAckMaintenance")],
        reasoning="Acknowledge button to dismiss maintenance notification."
    )

    exceptional_rules = [
        ExceptionalRule(
            rule_id="rule_member_not_found",
            outcome_class=OutcomeClass.BUSINESS_OUTCOME,
            signature=DetectionSignature(locator=not_found_loc, text_pattern="Member Record Not Found"),
            outcome_code="MEMBER_NOT_FOUND",
            description="Member ID was not found in the core banking system.",
        ),
        ExceptionalRule(
            rule_id="rule_maintenance_interstitial",
            outcome_class=OutcomeClass.RECOVERABLE,
            signature=DetectionSignature(locator=maintenance_loc),
            outcome_code="MAINTENANCE_INTERSTITIAL_DISMISSED",
            description="System maintenance popup appeared; dismiss to continue.",
            recovery_action=CapabilityStep(
                step_id="dismiss_maintenance",
                action=ActionType.DISMISS,
                locator=ack_button_loc,
            )
        )
    ]

    return CapabilityArtifact(
        schema_version="1.0",
        metadata=CapabilityMetadata(
            name=capability_name,
            version="1.0.0",
            app_id="apex_core_v4",
            description=description,
        ),
        input_parameters=input_params,
        output_parameters=output_params,
        steps=compiled_steps,
        success_checkpoint=success_chk,
        exceptional_rules=exceptional_rules,
        allowed_domains=list(dict.fromkeys(allowed_domains)),
    )
