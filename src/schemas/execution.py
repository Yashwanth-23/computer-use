"""
Execution result schema -- the contract returned by the deterministic
replay engine (src/engine/replay_executor.py) to whatever invoked it
(an AI agent, a test harness, a human operator via CLI).

This is deliberately a *separate* schema from the artifact itself: the
artifact describes a capability once; an ExecutionResult describes one
specific run of it. Keeping these apart means the artifact never
accumulates per-run noise, and the result schema is free to be as detailed
as debugging requires without bloating the reusable capability.

Three-way outcome split (per brief 3.3), made structural rather than
advisory: `ReplayStatus` forces every result into exactly one of these
buckets, and `ExecutionResult` shapes its own fields around which bucket
was hit (e.g. `outputs` is only meaningful on SUCCESS).
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ReplayStatus(str, Enum):
    SUCCESS = "success"                  # goal met, checkpoints verified, outputs returned
    BUSINESS_OUTCOME = "business_outcome"  # legitimate domain result, not a failure
    RECOVERED = "recovered"              # hit recoverable condition(s) but completed successfully
    ESCALATED = "escalated"              # handed off to a human; run is paused, not finished
    HARD_FAILURE = "hard_failure"        # unrecoverable; stopped with debug detail


class StepOutcome(str, Enum):
    OK = "ok"
    RECOVERED = "recovered"          # step needed a recovery action first, then succeeded
    SKIPPED_ESCALATED = "skipped_escalated"  # step didn't run; run was escalated before it
    FAILED = "failed"


class LocatorResolution(BaseModel):
    """Records which candidate in a locator's fallback chain actually
    resolved. Accumulating these across replays is the raw signal for
    detecting per-tenant/version drift (see REPORT.md heterogeneity section):
    if a capability that used to resolve on candidate[0] starts resolving
    on candidate[2] across many replays, that's an early warning the
    underlying UI has drifted before it fails outright.
    """

    model_config = ConfigDict(extra="forbid")

    strategy_used: str
    candidate_index: int = Field(..., ge=0, description="Position in the chain that resolved, 0 = primary.")
    resolution_time_ms: float


class StepTrace(BaseModel):
    """What happened for one step during one replay."""

    model_config = ConfigDict(extra="forbid")

    step_id: str
    outcome: StepOutcome
    locator_resolution: LocatorResolution | None = None
    started_at: datetime
    duration_ms: float
    detail: str | None = Field(
        default=None, description="Short human-readable note, e.g. 'dismissed maintenance interstitial'."
    )
    # Deliberately no raw DOM/page content here -- see redaction policy.
    # A pointer to richer evidence (screenshot path) is fine; the content itself is not.
    evidence_ref: str | None = Field(
        default=None, description="Path to a screenshot/DOM-snapshot on failure, relative to /evidence/."
    )


class DebugContext(BaseModel):
    """Populated only on HARD_FAILURE. Enough to diagnose without needing
    to reproduce: what step, what was expected, what was actually observed.
    """

    model_config = ConfigDict(extra="forbid")

    failed_step_id: str
    expected: str = Field(..., description="What the artifact asserted should be true (checkpoint/locator).")
    observed: str = Field(..., description="What was actually found, redacted of any sensitive values.")
    evidence_ref: str | None = Field(default=None, description="Screenshot/snapshot path for this failure.")
    exception_type: str | None = None


class BusinessOutcomeDetail(BaseModel):
    """Populated on BUSINESS_OUTCOME. This is a legitimate answer the
    caller needs, structured the same way every time -- not a free-text
    error message the caller has to string-match.
    """

    model_config = ConfigDict(extra="forbid")

    outcome_code: str = Field(..., description="Matches ExceptionalRule.outcome_code, e.g. 'MEMBER_NOT_FOUND'.")
    matched_rule_id: str
    message: str = Field(..., description="Human-readable rendering, safe to show a caller/operator.")


class ExecutionResult(BaseModel):
    """The complete, structured result of one replay run."""

    model_config = ConfigDict(extra="forbid")

    run_id: str
    capability_id: str
    capability_version: str
    status: ReplayStatus
    started_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    finished_at: datetime | None = None
    step_traces: list[StepTrace] = Field(default_factory=list)

    # Exactly one of these is populated, depending on `status`.
    outputs: dict[str, Any] | None = Field(
        default=None, description="Populated on SUCCESS/RECOVERED: the declared output_parameters, typed."
    )
    business_outcome: BusinessOutcomeDetail | None = None
    debug: DebugContext | None = None
    escalation_ref: str | None = Field(
        default=None, description="ID of the InterventionRequest, when status == ESCALATED."
    )

    @model_validator(mode="after")
    def _status_shape_consistency(self):
        checks = {
            ReplayStatus.SUCCESS: ("outputs",),
            ReplayStatus.RECOVERED: ("outputs",),
            ReplayStatus.BUSINESS_OUTCOME: ("business_outcome",),
            ReplayStatus.HARD_FAILURE: ("debug",),
            ReplayStatus.ESCALATED: ("escalation_ref",),
        }
        required_field = checks[self.status]
        for field_name in required_field:
            if getattr(self, field_name) is None:
                raise ValueError(f"status={self.status} requires '{field_name}' to be set")
        # And the converse: fields belonging to other statuses should be absent,
        # so callers can't accidentally read stale data from a differently-shaped result.
        all_status_fields = {"outputs", "business_outcome", "debug", "escalation_ref"}
        allowed = set(required_field)
        for field_name in all_status_fields - allowed:
            if getattr(self, field_name) is not None:
                raise ValueError(f"status={self.status} must not set '{field_name}'")
        return self
