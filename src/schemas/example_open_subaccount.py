"""
Builds a real CapabilityArtifact for opening a sub-account against mock_target.
This flow includes a critical mutation step (step 5: confirm submit) explicitly
classified as RiskLevel.RISKY_IRREVERSIBLE with mandatory risk_justification.

Run: python -m src.schemas.example_open_subaccount
"""
import json
from src.schemas.artifact import (
    ActionType,
    CapabilityArtifact,
    CapabilityMetadata,
    CapabilityStep,
    Checkpoint,
    DetectionSignature,
    ExceptionalRule,
    InputParameter,
    LocatorCandidate,
    LocatorStrategy,
    MultiStrategyLocator,
    OutcomeClass,
    OutputField,
    ParamType,
    RiskLevel,
)


def product_type_locator() -> MultiStrategyLocator:
    return MultiStrategyLocator(
        chain=[
            LocatorCandidate(
                strategy=LocatorStrategy.STABLE_ID,
                value="#ctl00_MainContent_ddlProductType",
                note="Server-rendered ASP.NET dropdown control ID.",
            ),
            LocatorCandidate(
                strategy=LocatorStrategy.ACCESSIBLE_ROLE_NAME,
                value='role=combobox[name="Product Type"]',
                note="Accessible fallback for product dropdown.",
            ),
        ],
        reasoning="Legacy ASP.NET dropdown selector with accessible role fallback.",
    )


def initial_deposit_locator() -> MultiStrategyLocator:
    return MultiStrategyLocator(
        chain=[
            LocatorCandidate(
                strategy=LocatorStrategy.STABLE_ID,
                value="#ctl00_MainContent_txtInitialDeposit",
                note="Server-rendered ASP.NET text input control ID.",
            ),
            LocatorCandidate(
                strategy=LocatorStrategy.ACCESSIBLE_ROLE_NAME,
                value='role=textbox[name="Initial Deposit ($)"]',
                note="Accessible fallback for deposit amount.",
            ),
        ],
        reasoning="Legacy ASP.NET input control for monetary deposit.",
    )


def review_button_locator() -> MultiStrategyLocator:
    return MultiStrategyLocator(
        chain=[
            LocatorCandidate(
                strategy=LocatorStrategy.STABLE_ID,
                value="#ctl00_MainContent_btnReview",
                note="Server-rendered review submit button ID.",
            ),
            LocatorCandidate(
                strategy=LocatorStrategy.TEXT_MATCH,
                value="Review Request",
                note="Visible button text fallback.",
            ),
        ],
        reasoning="Review button transitions to the review confirmation stage.",
    )


def confirm_button_locator() -> MultiStrategyLocator:
    return MultiStrategyLocator(
        chain=[
            LocatorCandidate(
                strategy=LocatorStrategy.STABLE_ID,
                value="#ctl00_MainContent_btnConfirmSubmit",
                note="Server-rendered confirmation submit button ID.",
            ),
            LocatorCandidate(
                strategy=LocatorStrategy.TEXT_MATCH,
                value="Confirm and Submit",
                note="Visible button text fallback.",
            ),
        ],
        reasoning="Irreversible financial mutation button committing new sub-account to core ledger.",
    )


def receipt_panel_locator() -> MultiStrategyLocator:
    return MultiStrategyLocator(
        chain=[
            LocatorCandidate(
                strategy=LocatorStrategy.STABLE_ID,
                value="#ctl00_MainContent_pnlReceipt",
                note="Receipt container panel rendered upon successful account creation.",
            ),
        ],
        reasoning="Success receipt container indicating state commitment.",
    )


def receipt_id_locator() -> MultiStrategyLocator:
    return MultiStrategyLocator(
        chain=[
            LocatorCandidate(
                strategy=LocatorStrategy.STABLE_ID,
                value="#ctl00_MainContent_lblReceiptId",
                note="Stable label control ID containing unique sub-account receipt ID.",
            ),
            LocatorCandidate(
                strategy=LocatorStrategy.STRUCTURAL_PATH,
                value="//td[contains(text(),'Receipt ID')]/following-sibling::td",
                note="Structural fallback next to Receipt ID label.",
            ),
        ],
        reasoning="Receipt ID output field extraction locator.",
    )


def not_found_locator() -> MultiStrategyLocator:
    return MultiStrategyLocator(
        chain=[
            LocatorCandidate(
                strategy=LocatorStrategy.STABLE_ID,
                value="#ctl00_MainContent_lblResultMessage",
            ),
            LocatorCandidate(
                strategy=LocatorStrategy.TEXT_MATCH,
                value="Member Record Not Found",
            ),
        ],
        reasoning="Business outcome locator for missing member ID.",
    )


def frozen_locator() -> MultiStrategyLocator:
    return MultiStrategyLocator(
        chain=[
            LocatorCandidate(
                strategy=LocatorStrategy.STABLE_ID,
                value="#ctl00_MainContent_lblRestrictedMessage",
            ),
            LocatorCandidate(
                strategy=LocatorStrategy.TEXT_MATCH,
                value="FROZEN",
            ),
        ],
        reasoning="Business outcome locator for restricted/frozen accounts.",
    )


def validation_error_locator() -> MultiStrategyLocator:
    return MultiStrategyLocator(
        chain=[
            LocatorCandidate(
                strategy=LocatorStrategy.STABLE_ID,
                value="#ctl00_MainContent_lblValidationError",
            ),
        ],
        reasoning="Business outcome locator for input validation failures (e.g. deposit under $25).",
    )


def build_artifact() -> CapabilityArtifact:
    return CapabilityArtifact(
        metadata=CapabilityMetadata(
            id="cap_open_sub_account",
            name="open_sub_account",
            version="1.0.0",
            app_id="apexcore_banking_portal",
            description="Opens a new sub-account (e.g. Holiday Club) for an existing member with an initial deposit. "
                        "Step 5 commits financial ledger mutations and is strictly classified as RISKY_IRREVERSIBLE.",
        ),
        input_parameters=[
            InputParameter(
                name="member_id",
                type=ParamType.STRING,
                required=True,
                description="Target member ID in the core banking system.",
                pattern=r"^\d{1,10}$",
                example="1001",
            ),
            InputParameter(
                name="product_type",
                type=ParamType.STRING,
                required=True,
                description="Product type code (HOLIDAY_CLUB, CHRISTMAS_CLUB, MONEY_MARKET).",
                example="HOLIDAY_CLUB",
            ),
            InputParameter(
                name="initial_deposit",
                type=ParamType.STRING,
                required=True,
                description="Initial deposit amount in USD (minimum $25.00).",
                pattern=r"^\d+(\.\d{1,2})?$",
                example="50.00",
            ),
        ],
        output_parameters=[
            OutputField(
                name="receipt_id",
                type=ParamType.STRING,
                description="Unique ledger confirmation receipt identifier.",
                extraction_locator=receipt_id_locator(),
            ),
        ],
        steps=[
            CapabilityStep(
                step_id="step_1_navigate_form",
                action=ActionType.NAVIGATE,
                target_url="http://127.0.0.1:8000/portal/sub-account/new?member_id={member_id}",
                checkpoint=Checkpoint(
                    description="Sub-account opening form loaded.",
                    locator=product_type_locator(),
                    expected_url_pattern=r"/portal/sub-account/new.*",
                ),
            ),
            CapabilityStep(
                step_id="step_2_select_product",
                action=ActionType.SELECT,
                locator=product_type_locator(),
                input_value="{product_type}",
            ),
            CapabilityStep(
                step_id="step_3_enter_deposit",
                action=ActionType.TYPE,
                locator=initial_deposit_locator(),
                input_value="{initial_deposit}",
            ),
            CapabilityStep(
                step_id="step_4_click_review",
                action=ActionType.CLICK,
                locator=review_button_locator(),
                checkpoint=Checkpoint(
                    description="Review confirmation screen loaded.",
                    locator=confirm_button_locator(),
                    expected_url_pattern=r"/portal/sub-account/review.*",
                ),
            ),
            CapabilityStep(
                step_id="step_5_confirm_submit",
                action=ActionType.CLICK,
                locator=confirm_button_locator(),
                is_risky=RiskLevel.RISKY_IRREVERSIBLE,
                risk_justification="Opening a sub-account creates an active financial ledger record and allocates initial deposit funds. "
                                   "This operation cannot be automatically reversed without human supervisor authorization.",
                checkpoint=Checkpoint(
                    description="Sub-account receipt rendered upon ledger commitment.",
                    locator=receipt_panel_locator(),
                    expected_url_pattern=r"/portal/sub-account/confirm.*",
                ),
            ),
            CapabilityStep(
                step_id="step_6_extract_receipt",
                action=ActionType.EXTRACT,
                locator=receipt_id_locator(),
            ),
        ],
        success_checkpoint=Checkpoint(
            description="Sub-account confirmed and receipt panel visible.",
            locator=receipt_panel_locator(),
            expected_url_pattern=r"/portal/sub-account/confirm.*",
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
                description="The specified member ID does not exist in the banking core.",
            ),
            ExceptionalRule(
                rule_id="rule_account_frozen",
                outcome_class=OutcomeClass.BUSINESS_OUTCOME,
                signature=DetectionSignature(
                    locator=frozen_locator(),
                    text_pattern="FROZEN",
                ),
                outcome_code="ACCOUNT_FROZEN",
                description="The member's account is FROZEN; sub-accounts cannot be opened.",
            ),
            ExceptionalRule(
                rule_id="rule_validation_error",
                outcome_class=OutcomeClass.BUSINESS_OUTCOME,
                signature=DetectionSignature(
                    locator=validation_error_locator(),
                ),
                outcome_code="VALIDATION_ERROR",
                description="Input validation failed (e.g. deposit below minimum requirement).",
            ),
        ],
        allowed_domains=["127.0.0.1:8000", "localhost:8000"],
        allowed_actions=[
            ActionType.NAVIGATE,
            ActionType.SELECT,
            ActionType.TYPE,
            ActionType.CLICK,
            ActionType.EXTRACT,
        ],
        allowed_routes=["/portal/sub-account"],
    )


def main():
    artifact = build_artifact()
    print("Sub-account Capability Artifact built and validated successfully.")
    print(f"  ID: {artifact.metadata.id}")
    print(f"  Steps: {len(artifact.steps)}")
    print(f"  Risky Step: {[s.step_id for s in artifact.steps if s.is_risky == RiskLevel.RISKY_IRREVERSIBLE]}")
    as_json = artifact.model_dump_json(indent=2)
    with open("evidence/capability_open_subaccount.json", "w", encoding="utf-8") as f:
        f.write(as_json)
    print("  Saved to evidence/capability_open_subaccount.json")


if __name__ == "__main__":
    main()
