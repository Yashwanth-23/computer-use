import re
import time
from typing import Any, Tuple
from playwright.sync_api import Page, Locator

from src.schemas.artifact import MultiStrategyLocator, LocatorCandidate, LocatorStrategy
from src.schemas.execution import LocatorResolution


class LocatorResolutionError(Exception):
    """Raised when no candidate in a multi-strategy locator's chain resolves."""
    def __init__(self, locator: MultiStrategyLocator, attempts: list[dict]):
        self.locator = locator
        self.attempts = attempts
        super().__init__(f"Failed to resolve locator chain across {len(attempts)} candidates. Reasoning: {locator.reasoning}")


def resolve_candidate(page: Page, candidate: LocatorCandidate) -> Locator:
    """Map a LocatorCandidate to a concrete Playwright Locator."""
    val = candidate.value.strip()

    if candidate.strategy == LocatorStrategy.STABLE_ID:
        # Standard CSS or ID/name attribute
        return page.locator(val)

    elif candidate.strategy == LocatorStrategy.ACCESSIBLE_ROLE_NAME:
        # Matches patterns like role=textbox[name="Member ID"] or role=button, name="Search"
        m = re.match(r"role=([a-zA-Z]+)(?:\[name=[\"'](.*?)[\"']\]|,\s*name=[\"'](.*?)[\"'])?", val)
        if m:
            role = m.group(1).lower()
            name = m.group(2) or m.group(3)
            if name:
                return page.get_by_role(role, name=name)
            return page.get_by_role(role)
        # Fallback to standard selector
        return page.locator(val)

    elif candidate.strategy == LocatorStrategy.LABEL_PROXIMITY:
        # e.g. label="Member ID:" or text="Member ID"
        clean_val = re.sub(r"^label=[\"']?|[\"']$", "", val)
        return page.get_by_label(clean_val)

    elif candidate.strategy == LocatorStrategy.TEXT_MATCH:
        clean_text = re.sub(r"^text=[\"']?|[\"']$", "", val)
        return page.get_by_text(clean_text)

    elif candidate.strategy == LocatorStrategy.STRUCTURAL_PATH:
        # XPath or deep CSS
        return page.locator(val)

    elif candidate.strategy == LocatorStrategy.COORDINATES:
        # Used for click fallback
        return page.locator("body")

    # Default fallback
    return page.locator(val)


def resolve_locator(
    page: Page,
    multi_locator: MultiStrategyLocator,
    timeout_per_candidate_ms: int = 1500,
    check_visibility: bool = True,
) -> Tuple[Locator, LocatorResolution]:
    """Evaluate a multi-strategy locator chain sequentially until one resolves.

    Returns:
        (Playwright Locator, LocatorResolution metadata)
    """
    attempts = []
    start_total = time.perf_counter()

    for idx, candidate in enumerate(multi_locator.chain):
        step_start = time.perf_counter()
        try:
            loc = resolve_candidate(page, candidate)
            # Check attached/visible status
            if check_visibility:
                loc.first.wait_for(state="visible", timeout=timeout_per_candidate_ms)
            else:
                loc.first.wait_for(state="attached", timeout=timeout_per_candidate_ms)

            elapsed_ms = (time.perf_counter() - step_start) * 1000.0
            resolution = LocatorResolution(
                strategy_used=candidate.strategy.value,
                candidate_index=idx,
                resolution_time_ms=round(elapsed_ms, 2)
            )
            return loc.first, resolution

        except Exception as e:
            elapsed_ms = (time.perf_counter() - step_start) * 1000.0
            attempts.append({
                "index": idx,
                "strategy": candidate.strategy.value,
                "value": candidate.value,
                "duration_ms": round(elapsed_ms, 2),
                "error": str(e)
            })

    raise LocatorResolutionError(multi_locator, attempts)
