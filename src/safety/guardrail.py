from urllib.parse import urlparse
from typing import Sequence
from src.schemas.artifact import CapabilityStep, RiskLevel, ActionType


class SecurityViolationError(Exception):
    """Raised when an action violates safety guardrails (domain allowlist, forbidden action, route violation)."""
    pass


class PolicyGuardrail:
    """Enforces safety guardrails: domain allowlists, route policies, action permissions, and risk gating."""

    def __init__(
        self,
        allowed_domains: Sequence[str],
        allowed_actions: Sequence[ActionType] | None = None,
        allowed_routes: Sequence[str] | None = None,
    ):
        if not allowed_domains:
            raise SecurityViolationError("PolicyGuardrail requires a non-empty allowed_domains list.")
        self.allowed_domains = list(allowed_domains)
        if allowed_actions is not None:
            self.allowed_actions = list(allowed_actions)
        else:
            self.allowed_actions = list(ActionType)
        self.allowed_routes = list(allowed_routes) if allowed_routes else None

    def validate_url(self, url: str) -> None:
        """Ensure the target URL is strictly within permitted domains, schemes, and routes."""
        if not url:
            raise SecurityViolationError("Target URL cannot be empty")

        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"}:
            raise SecurityViolationError(f"Prohibited URL scheme: '{parsed.scheme}'. Only http/https permitted.")

        if parsed.username or parsed.password:
            raise SecurityViolationError("URLs containing embedded credentials are strictly prohibited.")

        host = (parsed.hostname or "").lower()
        port = parsed.port
        full_netloc = (parsed.netloc or "").lower()

        domain_matched = False
        for allowed in self.allowed_domains:
            allowed_clean = allowed.replace("http://", "").replace("https://", "").rstrip("/").lower()
            allowed_host = allowed_clean.split(":")[0]
            allowed_port = int(allowed_clean.split(":")[1]) if ":" in allowed_clean else None

            # Match exact host or legitimate subdomain (.domain.com)
            host_matches = (host == allowed_host) or host.endswith(f".{allowed_host}")
            port_matches = True if allowed_port is None else (port == allowed_port)

            if host_matches and port_matches:
                domain_matched = True
                break

        if not domain_matched:
            raise SecurityViolationError(
                f"URL '{url}' violates domain allowlist. Permitted domains: {self.allowed_domains}"
            )

        # Route validation if configured
        if self.allowed_routes:
            path = parsed.path or "/"
            route_matched = any(path.startswith(r) for r in self.allowed_routes)
            if not route_matched:
                raise SecurityViolationError(
                    f"URL path '{path}' violates route allowlist. Permitted routes: {self.allowed_routes}"
                )

    def validate_action(self, action: ActionType) -> None:
        """Ensure the action type is explicitly permitted by policy. Fails closed."""
        if not self.allowed_actions:
            raise SecurityViolationError("Action allowlist is empty. Execution blocked by policy.")
        if action not in self.allowed_actions:
            raise SecurityViolationError(
                f"Action type '{action.value}' is prohibited by policy. Permitted actions: {[a.value for a in self.allowed_actions]}"
            )

    def check_step_risk(self, step: CapabilityStep) -> tuple[bool, str | None]:
        """Check if a step is risky and requires human escalation.

        Banking Core Policy: Unattended execution of RISKY_IRREVERSIBLE actions is strictly prohibited.
        Returns (requires_escalation, reason).
        """
        if step.is_risky == RiskLevel.RISKY_IRREVERSIBLE:
            justification = step.risk_justification or "Irreversible state modification"
            reason = (
                f"Policy Gate: Step '{step.step_id}' ({step.action.value}) is classified as "
                f"RISKY_IRREVERSIBLE ({justification}). Unattended execution blocked by banking policy."
            )
            return True, reason
        return False, None

