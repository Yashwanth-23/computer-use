"""
Builds one real CapabilityArtifact for the member-lookup-and-balance flow,
using the exact control IDs from mock_target/templates/. This proves the
schema isn't just internally consistent -- it fits the actual hostile
surface we built, not an idealized one.

Run: python3 -m src.schemas.example_member_lookup
"""
import json

from src.schemas.artifact import (
    ActionType, CapabilityArtifact, CapabilityMetadata, CapabilityStep,
    Checkpoint, DetectionSignature, ExceptionalRule, InputParameter,
    LocatorCandidate, LocatorStrategy, MultiStrategyLocator, OutcomeClass,
    OutputField, ParamType, RiskLevel,
)


def member_id_locator() -> MultiStrategyLocator:
    return MultiStrategyLocator(
        chain=[
            LocatorCandidate(
                strategy=LocatorStrategy.STABLE_ID,
                value="#ctl00_MainContent_txtMemberId",
                note="Server-generated WebForms control ID; stable within a release.",
            ),
            LocatorCandidate(
                strategy=LocatorStrategy.ACCESSIBLE_ROLE_NAME,
                value='role=textbox[name="Member ID:"]',
                note="Falls back here if a vendor version bump changes the master-page control prefix "
                     "(e.g. ctl00_ -> ctl01_) but the visible label text is unchanged.",
            ),
            LocatorCandidate(
                strategy=LocatorStrategy.LABEL_PROXIMITY,
                value='label:has-text("Member ID:") >> xpath=following::input[1]',
                note="Falls back here if even the accessible name changes but the form's visual layout "
                     "(label immediately preceding its input) is preserved.",
            ),
        ],
        reasoning=(
            "This is a legacy server-rendered ASP.NET-style form. Control IDs are compiler-generated "
            "from the page's control tree and are the most durable identifier available on this class "
            "of surface -- they survive copy/styling changes untouched. Accessible role+name is the "
            "first fallback because it survives ID-prefix drift across vendor versions. On a modern "
            "SPA built with semantic ARIA attributes, this priority would invert: accessible role/name "
            "would be primary, since IDs there are often build-hash-generated and change on every deploy."
        ),
    )


def search_button_locator() -> MultiStrategyLocator:
    return MultiStrategyLocator(
        chain=[
            LocatorCandidate(strategy=LocatorStrategy.STABLE_ID, value="#ctl00_MainContent_btnSearch"),
            LocatorCandidate(
                strategy=LocatorStrategy.TEXT_MATCH,
                value="Search Member Record",
                note="Visible button text; stable unless copy is rebranded per-tenant.",
            ),
        ],
        reasoning="Same rationale as the member ID field: structural ID primary on this legacy surface.",
    )


def not_found_locator() -> MultiStrategyLocator:
    return MultiStrategyLocator(
        chain=[
            LocatorCandidate(strategy=LocatorStrategy.STABLE_ID, value="#ctl00_MainContent_lblResultMessage"),
            LocatorCandidate(strategy=LocatorStrategy.TEXT_MATCH, value="Member Record Not Found"),
        ],
        reasoning="Business-outcome banner has a stable server-rendered ID; text match as a loose fallback "
                  "since the exact wording may vary slightly by tenant branding.",
    )


def balance_grid_locator() -> MultiStrategyLocator:
    return MultiStrategyLocator(
        chain=[
            LocatorCandidate(strategy=LocatorStrategy.STABLE_ID, value="#ctl00_MainContent_gvBalances"),
        ],
        reasoning="Checkpoint only needs presence of the balances grid container; no fallback needed for "
                  "a container-level existence check.",
    )


def savings_output_locator() -> MultiStrategyLocator:
    return MultiStrategyLocator(
        chain=[
            LocatorCandidate(
                strategy=LocatorStrategy.STABLE_ID,
                value="#ctl00_MainContent_gvBalances_ctl02_lblSavingsBalance",
            ),
            LocatorCandidate(
                strategy=LocatorStrategy.STRUCTURAL_PATH,
                value="//td[contains(text(),'Savings')]/following-sibling::td//span",
                note="Last-resort structural fallback if grid-row control IDs shift due to a row being "
                     "inserted/removed by a vendor update.",
            ),
        ],
        reasoning="Nested-grid control IDs in this ASP.NET-style repeater pattern encode row index "
                  "(ctl02), which is the most common source of drift when row order changes -- hence "
                  "the structural (label-relative) fallback rather than another ID-based one.",
    )


def checking_output_locator() -> MultiStrategyLocator:
    return MultiStrategyLocator(
        chain=[
            LocatorCandidate(
                strategy=LocatorStrategy.STABLE_ID,
                value="#ctl00_MainContent_gvBalances_ctl03_lblCheckingBalance",
            ),
            LocatorCandidate(
                strategy=LocatorStrategy.STRUCTURAL_PATH,
                value="//td[contains(text(),'Checking')]/following-sibling::td//span",
            ),
        ],
        reasoning="Same row-index drift concern as savings_balance.",
    )


def build_artifact() -> CapabilityArtifact:
    return CapabilityArtifact(
        metadata=CapabilityMetadata(
            name="lookup_member_balance",
            app_id="apexcore_banking_portal",
            description="Looks up a member by ID on the ApexCore servicing portal and reads their "
                        "current savings and checking balances.",
        ),
        input_parameters=[
            InputParameter(
                name="member_id",
                type=ParamType.STRING,
                required=True,
                description="The member's numeric ID as known to the core banking system.",
                pattern=r"^\d{1,10}$",
                example="1001",
            ),
        ],
        output_parameters=[
            OutputField(
                name="savings_balance",
                type=ParamType.NUMBER,
                description="Current savings account balance in USD.",
                extraction_locator=savings_output_locator(),
                transform="strip_currency_symbol",
            ),
            OutputField(
                name="checking_balance",
                type=ParamType.NUMBER,
                description="Current checking account balance in USD.",
                extraction_locator=checking_output_locator(),
                transform="strip_currency_symbol",
            ),
        ],
        steps=[
            CapabilityStep(
                step_id="step_1_navigate",
                action=ActionType.NAVIGATE,
                target_url="/portal/member-lookup",
                checkpoint=Checkpoint(
                    description="Member lookup form is visible.",
                    locator=member_id_locator(),
                ),
            ),
            CapabilityStep(
                step_id="step_2_enter_member_id",
                action=ActionType.TYPE,
                locator=member_id_locator(),
                input_value="{member_id}",
            ),
            CapabilityStep(
                step_id="step_3_click_search",
                action=ActionType.CLICK,
                locator=search_button_locator(),
                is_risky=RiskLevel.SAFE,
            ),
            CapabilityStep(
                step_id="step_4_extract_savings",
                action=ActionType.EXTRACT,
                locator=savings_output_locator(),
                checkpoint=Checkpoint(
                    description="Balances grid is rendered (member found).",
                    locator=balance_grid_locator(),
                ),
            ),
            CapabilityStep(
                step_id="step_5_extract_checking",
                action=ActionType.EXTRACT,
                locator=checking_output_locator(),
            ),
        ],
        success_checkpoint=Checkpoint(
            description="Balances grid rendered with both savings and checking values present.",
            locator=balance_grid_locator(),
        ),
        exceptional_rules=[
            ExceptionalRule(
                rule_id="rule_member_not_found",
                outcome_class=OutcomeClass.BUSINESS_OUTCOME,
                signature=DetectionSignature(
                    locator=not_found_locator(),
                    text_pattern="Member Record Not Found",
                ),
                outcome_code="MEMBER_NOT_FOUND",
                description="The member ID does not exist in the core system. A legitimate result, "
                            "not a failure -- the caller needs to know this, not see a crash.",
            ),
            ExceptionalRule(
                rule_id="rule_maintenance_interstitial",
                outcome_class=OutcomeClass.RECOVERABLE,
                signature=DetectionSignature(
                    locator=MultiStrategyLocator(
                        chain=[LocatorCandidate(strategy=LocatorStrategy.STABLE_ID, value="#pnlMaintenanceAlert")],
                        reasoning="Interstitial has a stable container ID; presence alone is the signal.",
                    ),
                ),
                outcome_code="MAINTENANCE_INTERSTITIAL_DISMISSED",
                description="A scheduled-maintenance notice appeared. Dismiss it and continue -- this "
                            "is expected, intermittent runtime behavior, not a sign the flow is broken.",
                recovery_action=CapabilityStep(
                    step_id="recovery_dismiss_maintenance",
                    action=ActionType.DISMISS,
                    locator=MultiStrategyLocator(
                        chain=[LocatorCandidate(strategy=LocatorStrategy.STABLE_ID, value="#btnAckMaintenance")],
                        reasoning="Acknowledge button has a stable ID.",
                    ),
                ),
            ),
        ],
        allowed_domains=["127.0.0.1:8000", "localhost:8000"],
    )


def main():
    artifact = build_artifact()
    print("Artifact built and validated OK.")
    print(f"  id={artifact.metadata.id}  name={artifact.metadata.name}  version={artifact.metadata.version}")
    print(f"  steps={len(artifact.steps)}  inputs={len(artifact.input_parameters)}  "
          f"outputs={len(artifact.output_parameters)}  exceptional_rules={len(artifact.exceptional_rules)}")

    as_json = artifact.model_dump_json(indent=2)
    with open("evidence_capability_member_lookup.json", "w") as f:
        f.write(as_json)
    print(f"  Wrote {len(as_json)} bytes to evidence_capability_member_lookup.json")

    # Round-trip check
    reloaded = CapabilityArtifact.model_validate_json(as_json)
    assert reloaded == artifact, "round-trip mismatch!"
    print("Round-trip (dump -> reload) verified identical.")


if __name__ == "__main__":
    main()
