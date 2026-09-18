import os
import re
from datetime import datetime, timezone
from playwright.sync_api import Page

from src.schemas.execution import DebugContext
from src.safety.redaction import redact_text


class ErrorDiagnostics:
    """Builds structured, redacted debug artifacts and screenshots on failure."""

    def __init__(self, evidence_dir: str = "evidence/screenshots"):
        self.evidence_dir = evidence_dir
        os.makedirs(self.evidence_dir, exist_ok=True)

    def capture_failure(
        self,
        page: Page,
        run_id: str,
        step_id: str,
        expected: str,
        exception: Exception,
    ) -> DebugContext:
        """Captures page screenshot, extracts redacted DOM context, and returns DebugContext."""
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        screenshot_name = f"failure_{run_id[:8]}_{step_id}_{timestamp}.png"
        screenshot_path = os.path.join(self.evidence_dir, screenshot_name)

        try:
            page.screenshot(path=screenshot_path)
            ref_path = screenshot_path
        except Exception:
            ref_path = "screenshot_unavailable.png"

        # Capture observed page context
        try:
            raw_text = page.inner_text("body")
            # Take first 500 chars to avoid gigantic dumps
            condensed = " ".join(raw_text.split()[:80])
            observed_text = f"URL: {page.url} | Body preview: {condensed}"
        except Exception:
            observed_text = f"URL: {getattr(page, 'url', 'unknown')}"

        redacted_observed = redact_text(observed_text)

        return DebugContext(
            failed_step_id=step_id,
            expected=expected,
            observed=f"{redacted_observed} (Error: {str(exception)})",
            evidence_ref=ref_path,
            exception_type=type(exception).__name__,
        )
