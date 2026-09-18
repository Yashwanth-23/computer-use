import pytest
from src.safety.guardrail import PolicyGuardrail, SecurityViolationError
from src.safety.redaction import redact_text, redact_data
from src.schemas.artifact import CapabilityStep, ActionType, RiskLevel, MultiStrategyLocator, LocatorCandidate, LocatorStrategy


def test_domain_allowlist_enforcement():
    guard = PolicyGuardrail(allowed_domains=["127.0.0.1:8000", "localhost"])
    
    # Valid URLs
    guard.validate_url("http://127.0.0.1:8000/portal/member-lookup")
    guard.validate_url("http://localhost:8000/admin")
    
    # Invalid domain
    with pytest.raises(SecurityViolationError, match="violates domain allowlist"):
        guard.validate_url("http://malicious-bank.com/steal-creds")
        
    # Invalid scheme
    with pytest.raises(SecurityViolationError, match="Prohibited URL scheme"):
        guard.validate_url("file:///etc/passwd")


def test_risky_action_gating():
    guard_strict = PolicyGuardrail(allowed_domains=["127.0.0.1"], allow_unattended_risky=False)
    guard_permissive = PolicyGuardrail(allowed_domains=["127.0.0.1"], allow_unattended_risky=True)
    
    dummy_locator = MultiStrategyLocator(
        chain=[LocatorCandidate(strategy=LocatorStrategy.STABLE_ID, value="#btnConfirm")],
        reasoning="Test locator"
    )
    
    safe_step = CapabilityStep(
        step_id="search",
        action=ActionType.CLICK,
        locator=dummy_locator,
        is_risky=RiskLevel.SAFE
    )
    
    risky_step = CapabilityStep(
        step_id="confirm_transfer",
        action=ActionType.CLICK,
        locator=dummy_locator,
        is_risky=RiskLevel.RISKY_IRREVERSIBLE,
        risk_justification="Commits money transfer to external core"
    )
    
    # Safe step needs no escalation
    needed, _ = guard_strict.check_step_risk(safe_step)
    assert not needed
    
    # Risky step under strict policy needs escalation
    needed, reason = guard_strict.check_step_risk(risky_step)
    assert needed
    assert "Policy Gate" in reason
    assert "Commits money transfer" in reason
    
    # Risky step under permissive policy bypasses escalation
    needed, _ = guard_permissive.check_step_risk(risky_step)
    assert not needed


def test_pii_redaction():
    text = "Member SSN is 123-45-6789 and card is 4111 1111 1111 1111, bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.xyz"
    redacted = redact_text(text)
    
    assert "123-45-6789" not in redacted
    assert "[REDACTED_SSN]" in redacted
    assert "4111 1111 1111 1111" not in redacted
    assert "[REDACTED_CARD]" in redacted
    assert "eyJhbGci" not in redacted


def test_dict_pii_redaction():
    data = {
        "member_id": "1001",
        "ssn": "987-65-4321",
        "password": "SuperSecretPassword123!",
        "balance": ",500.00",
        "details": {
            "credit_card": "4111111111111111",
            "api_key": "sk-1234567890"
        }
    }
    redacted = redact_data(data)
    
    assert redacted["member_id"] == "1001"
    assert redacted["ssn"] == "[REDACTED_SECRET]"
    assert redacted["password"] == "[REDACTED_SECRET]"
    assert redacted["balance"] == ",500.00"
    assert redacted["details"]["credit_card"] in ["[REDACTED_CARD]", "[REDACTED_SECRET]"]
