from urllib.parse import urlparse
from typing import Sequence
from src.schemas.artifact import CapabilityStep, RiskLevel, ActionType


class SecurityViolationError(Exception):
    """Raised when an action violates safety guardrails (domain allowlist, forbidden action)."""
    pass


class PolicyGuardrail:
    """Enforces safety guardrails: domain allowlists, action permissions, and risk gating."""

    def __init__(
        self,
        allowed_domains: Sequence[str],
        allowed_actions: Sequence[ActionType] | None = None,
        allow_unattended_risky: bool = False,
    ):
        self.allowed_domains = list(allowed_domains)
        self.allowed_actions = list(allowed_actions) if allowed_actions else list(ActionType)
        self.allow_unattended_risky = allow_unattended_risky

    def validate_url(self, url: str) -> None:
        """Ensure the target URL is strictly within the allowed domains."""
        if not url:
            raise SecurityViolationError("Target URL cannot be empty")

        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"}:
            raise SecurityViolationError(f"Prohibited URL scheme: '{parsed.scheme}'. Only http/https permitted.")

        host = parsed.netloc.split(":")[0]  # strip port
        full_host = parsed.netloc

        domain_matched = False
        for allowed in self.allowed_domains:
            allowed_clean = allowed.replace("http://", "").replace("https://", "").split("/")[0]
            allowed_host = allowed_clean.split(":")[0]
            if host == allowed_host or full_host == allowed_clean or host.endswith(f".{allowed_host}"):
                domain_matched = True
                break

        if not domain_matched:
            raise SecurityViolationError(
                f"URL '{url}' violates domain allowlist. Permitted domains: {self.allowed_domains}"
            )

    def validate_action(self, action: ActionType) -> None:
        """Ensure the action type is permitted by policy."""
        if action not in self.allowed_actions:
            raise SecurityViolationError(f"Action type '{action}' is not in permitted actions.")

    def check_step_risk(self, step: CapabilityStep) -> tuple[bool, str | None]:
        """Check if a step is risky and requires human escalation.

        Returns (requires_escalation, reason).
        """
        if step.is_risky == RiskLevel.RISKY_IRREVERSIBLE and not self.allow_unattended_risky:
            justification = step.risk_justification or "Irreversible state modification"
            reason = (
                f"Policy Gate: Step '{step.step_id}' ({step.action.value}) is classified as "
                f"RISKY_IRREVERSIBLE ({justification}). Unattended execution blocked by banking policy."
            )
            return True, reason
        return False, None
