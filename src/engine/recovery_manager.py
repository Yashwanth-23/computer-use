import re
import logging
from typing import Tuple
from playwright.sync_api import Page

from src.schemas.artifact import ExceptionalRule, OutcomeClass, ActionType
from src.engine.locator_resolver import resolve_locator, LocatorResolutionError

logger = logging.getLogger(__name__)


class RecoveryManager:
    """Detects and resolves runtime conditions (interstitials, business outcomes, failure alerts)."""

    def __init__(self, rules: list[ExceptionalRule]):
        self.rules = rules

    def check_and_handle_conditions(
        self, page: Page, timeout_per_candidate_ms: int = 150
    ) -> Tuple[bool, ExceptionalRule | None, str | None]:
        """Scans the page for any matching ExceptionalRule signatures.

        Args:
            page: Active Playwright page.
            timeout_per_candidate_ms: Per-candidate wait timeout (default: 150ms for routine
                fast-path checks, 1500ms for reactive recovery checks when action is blocked).

        Returns:
            (was_recovered: bool, matched_rule: ExceptionalRule | None, detail_message: str | None)
        """
        for rule in self.rules:
            matched = False
            extracted_text = None

            # 1. Check locator signature if defined
            if rule.signature.locator:
                try:
                    loc, _ = resolve_locator(page, rule.signature.locator, timeout_per_candidate_ms=timeout_per_candidate_ms)
                    if loc.is_visible():
                        matched = True
                        extracted_text = loc.inner_text().strip()
                except LocatorResolutionError:
                    pass

            # 2. Check text pattern if defined and not already matched
            if not matched and rule.signature.text_pattern:
                try:
                    body = page.locator("body")
                    if body.count() > 0:
                        page_content = body.first.inner_text()
                        if re.search(rule.signature.text_pattern, page_content, re.IGNORECASE):
                            matched = True
                            extracted_text = rule.signature.text_pattern
                except Exception:
                    pass

            # If matched, handle by outcome class
            if matched:
                logger.info(f"Matched runtime condition rule '{rule.rule_id}' (class: {rule.outcome_class.value})")

                if rule.outcome_class == OutcomeClass.RECOVERABLE:
                    # Execute recovery action (e.g. click Acknowledge / dismiss interstitial)
                    if rule.recovery_action:
                        self._execute_recovery_action(page, rule.recovery_action)
                        logger.info(f"Executed recovery action for rule '{rule.rule_id}'")
                        return True, rule, extracted_text or rule.description

                elif rule.outcome_class == OutcomeClass.BUSINESS_OUTCOME:
                    # Business outcome encountered (e.g. Member Not Found)
                    return False, rule, extracted_text or rule.description

                elif rule.outcome_class == OutcomeClass.HARD_FAILURE:
                    return False, rule, extracted_text or rule.description

        return False, None, None

    def _execute_recovery_action(self, page: Page, action_step) -> None:
        """Executes a single recovery step (such as clicking an acknowledge button)."""
        if action_step.action in {ActionType.CLICK, ActionType.DISMISS}:
            if action_step.locator:
                loc, _ = resolve_locator(page, action_step.locator, timeout_per_candidate_ms=2000)
                loc.click()
        elif action_step.action == ActionType.WAIT_FOR:
            page.wait_for_timeout(action_step.max_wait_ms)
