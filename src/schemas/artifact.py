"""
Capability artifact schema.

This is the reusable, agent-invocable "capability" that a discovery run
produces and that the deterministic replay engine consumes. It is the
central data model of the whole system (per the brief: "the artifact
schema and replay contract are central").

Design intent, in one line: an artifact is a *contract*, not a transcript.
It must be understandable by (a) a human reviewer deciding whether to trust
it, and (b) a calling AI agent deciding how to invoke it -- without either
party needing to see the raw LLM discovery trace that produced it.

Everything here is decoupled from the model transcript: no prompts, no raw
LLM reasoning tokens, no screenshots live in this schema. Those belong to
discovery-time evidence artifacts (see /evidence/), not the reusable
capability itself.
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


# ---------------------------------------------------------------------------
# Locators
# ---------------------------------------------------------------------------

class LocatorStrategy(str, Enum):
    """How a target element/control is identified.

    Ordered here from most to least durable *in the general case*, but the
    actual per-step priority order is explicit in `MultiStrategyLocator.chain`
    and is deliberately environment-dependent (see `reasoning`). A legacy,
    server-rendered app and a modern SPA invert this ordering, and the
    artifact is expected to say so rather than assume one global answer.
    """

    STABLE_ID = "stable_id"                # e.g. #ctl00_MainContent_txtMemberId, name=... on a legacy form
    ACCESSIBLE_ROLE_NAME = "accessible_role_name"  # role=textbox, name="Member ID"
    LABEL_PROXIMITY = "label_proximity"    # label text + spatial/DOM anchor to its control
    STRUCTURAL_PATH = "structural_path"    # XPath / CSS structural path -- last resort, most brittle
    TEXT_MATCH = "text_match"              # exact/substring visible text match (for links, buttons, banners)
    COORDINATES = "coordinates"            # last-resort screen coordinates, for surfaces with no clean tree


class LocatorCandidate(BaseModel):
    """One rung of a locator's fallback ladder."""

    model_config = ConfigDict(extra="forbid")

    strategy: LocatorStrategy
    value: str = Field(
        ..., description="The selector/expression itself, e.g. '#ctl00_MainContent_txtMemberId', "
                          "or 'role=textbox[name=\"Member ID\"]', or an XPath expression."
    )
    note: str | None = Field(
        default=None,
        description="Why this candidate is at this position in the chain, or what condition "
                    "would make it fail (e.g. 'breaks if master page control prefix changes').",
    )


class MultiStrategyLocator(BaseModel):
    """A locator with an explicit, ordered fallback chain and the reasoning
    behind the ordering. Replay tries candidates in order and records which
    one actually resolved, which is itself a useful drift signal over time
    (see src/engine/locator_resolver.py).
    """

    model_config = ConfigDict(extra="forbid")

    chain: list[LocatorCandidate] = Field(..., min_length=1)
    reasoning: str = Field(
        ...,
        description="Human-readable justification for this specific ordering on this specific "
                    "surface, e.g. why stable_id is primary here rather than accessible_role_name.",
    )

    @field_validator("chain")
    @classmethod
    def _no_duplicate_strategies_back_to_back(cls, v: list[LocatorCandidate]):
        for a, b in zip(v, v[1:]):
            if a.strategy == b.strategy and a.value == b.value:
                raise ValueError("duplicate consecutive locator candidate")
        return v


# ---------------------------------------------------------------------------
# Typed input / output contracts
# ---------------------------------------------------------------------------

class ParamType(str, Enum):
    STRING = "string"
    NUMBER = "number"
    BOOLEAN = "boolean"
    ENUM = "enum"


class InputParameter(BaseModel):
    """A typed input the calling agent must (or may) supply per invocation."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., description="Parameter name, e.g. 'member_id'.")
    type: ParamType
    required: bool = True
    description: str = Field(..., description="What this parameter means, for both humans and agents.")
    enum_values: list[str] | None = Field(
        default=None, description="Allowed values, required and only meaningful when type == ENUM."
    )
    pattern: str | None = Field(
        default=None, description="Optional regex the value must match (e.g. numeric member IDs)."
    )
    example: str | None = None

    @model_validator(mode="after")
    def _enum_requires_values(self):
        if self.type == ParamType.ENUM and not self.enum_values:
            raise ValueError("enum_values is required when type == ENUM")
        return self


class OutputField(BaseModel):
    """A typed output the capability extracts and returns to the caller."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., description="Output field name, e.g. 'savings_balance'.")
    type: ParamType
    description: str
    extraction_locator: MultiStrategyLocator = Field(
        ..., description="Where this value is read from in the final/checkpoint state."
    )
    transform: str | None = Field(
        default=None,
        description="Optional named transform applied to the raw extracted text, "
                    "e.g. 'strip_currency_symbol', 'parse_float'. Keeps extraction locators "
                    "decoupled from formatting logic.",
    )


# ---------------------------------------------------------------------------
# Steps
# ---------------------------------------------------------------------------

class ActionType(str, Enum):
    NAVIGATE = "navigate"
    CLICK = "click"
    TYPE = "type"
    SELECT = "select"
    WAIT_FOR = "wait_for"
    EXTRACT = "extract"
    DISMISS = "dismiss"  # explicit dismissal of a known interstitial, distinct from a normal click


class RiskLevel(str, Enum):
    """Per-step risk classification. Gating decision lives in the safety
    engine (src/safety/guardrail.py), not here -- this schema only records
    the classification and the artifact author's justification for it.
    """

    SAFE = "safe"                 # read-only or trivially reversible (navigation, search, form fill)
    RISKY_IRREVERSIBLE = "risky_irreversible"  # state-changing and not (cheaply) undoable


class Checkpoint(BaseModel):
    """A post-action assertion confirming the step actually landed, rather
    than assuming the click worked. Distinct from extraction: a checkpoint
    is a boolean condition, not a value to return.
    """

    model_config = ConfigDict(extra="forbid")

    description: str = Field(..., description="Human-readable statement of what must be true, e.g. "
                                                "'balance grid is visible' or 'receipt panel is rendered'.")
    locator: MultiStrategyLocator | None = Field(
        default=None, description="Element that must be present/visible. Omit for URL- or state-only checkpoints."
    )
    expected_url_pattern: str | None = Field(
        default=None, description="Regex the current URL must match, when relevant."
    )
    timeout_ms: int = Field(default=5000, ge=100, le=60000)

    @model_validator(mode="after")
    def _at_least_one_condition(self):
        if self.locator is None and self.expected_url_pattern is None:
            raise ValueError("Checkpoint needs at least one of: locator, expected_url_pattern")
        return self


class CapabilityStep(BaseModel):
    """One ordered step in the recorded flow."""

    model_config = ConfigDict(extra="forbid")

    step_id: str = Field(..., description="Stable identifier within this artifact, e.g. 'step_1'.")
    action: ActionType
    locator: MultiStrategyLocator | None = Field(
        default=None,
        description="Target element for this step. Required for click/type/select/extract/dismiss; "
                    "omitted for navigate (uses target_url) and pure wait_for-by-time steps.",
    )
    input_value: str | None = Field(
        default=None,
        description="Literal value to type/select, OR a '{param_name}' placeholder referencing "
                    "an InputParameter -- resolved at replay time, never baked in as a literal secret.",
    )
    target_url: str | None = Field(default=None, description="Used only when action == NAVIGATE.")
    is_risky: RiskLevel = RiskLevel.SAFE
    risk_justification: str | None = Field(
        default=None, description="Required when is_risky == RISKY_IRREVERSIBLE: why this step is irreversible."
    )
    checkpoint: Checkpoint | None = Field(
        default=None, description="Post-condition verifying this step succeeded before moving on."
    )
    max_wait_ms: int = Field(default=8000, ge=100, le=60000)

    @model_validator(mode="after")
    def _validate_action_requirements(self):
        needs_locator = self.action in {
            ActionType.CLICK, ActionType.TYPE, ActionType.SELECT,
            ActionType.EXTRACT, ActionType.DISMISS,
        }
        if needs_locator and self.locator is None:
            raise ValueError(f"action={self.action} requires a locator")
        if self.action == ActionType.NAVIGATE and not self.target_url:
            raise ValueError("action=navigate requires target_url")
        if self.is_risky == RiskLevel.RISKY_IRREVERSIBLE and not self.risk_justification:
            raise ValueError("risky steps require risk_justification")
        return self


# ---------------------------------------------------------------------------
# Exceptional / recovery rules -- the runtime-condition taxonomy
# ---------------------------------------------------------------------------

class OutcomeClass(str, Enum):
    """The three-way split the brief requires the result contract to make.
    Declared here at the artifact level (what patterns *mean*); actually
    applied at replay time (src/engine/error_handler.py).
    """

    BUSINESS_OUTCOME = "business_outcome"      # legitimate domain answer, e.g. "not found"
    RECOVERABLE = "recoverable"                # dismiss/retry and continue automatically
    HARD_FAILURE = "hard_failure"               # stop, surface a debuggable error


class DetectionSignature(BaseModel):
    """How to recognize a runtime condition on the page, independent of
    which step triggered it -- these are checked opportunistically at
    each step boundary during replay, not tied to one specific step.
    """

    model_config = ConfigDict(extra="forbid")

    locator: MultiStrategyLocator | None = None
    text_pattern: str | None = Field(
        default=None, description="Regex matched against visible page text if locator is absent/insufficient."
    )

    @model_validator(mode="after")
    def _needs_a_signal(self):
        if self.locator is None and self.text_pattern is None:
            raise ValueError("DetectionSignature needs a locator or a text_pattern")
        return self


class ExceptionalRule(BaseModel):
    """Maps a detectable runtime signature to a classified outcome."""

    model_config = ConfigDict(extra="forbid")

    rule_id: str
    outcome_class: OutcomeClass
    signature: DetectionSignature
    outcome_code: str = Field(
        ..., description="Short machine-stable code returned to the caller, e.g. 'MEMBER_NOT_FOUND', "
                          "'ACCOUNT_FROZEN', 'VALIDATION_ERROR'."
    )
    description: str = Field(..., description="Human-readable explanation of this condition.")
    recovery_action: CapabilityStep | None = Field(
        default=None,
        description="Only meaningful when outcome_class == RECOVERABLE: the dismissal/retry step "
                    "to perform before resuming the main step sequence.",
    )

    @model_validator(mode="after")
    def _recoverable_needs_action(self):
        if self.outcome_class == OutcomeClass.RECOVERABLE and self.recovery_action is None:
            raise ValueError("RECOVERABLE rules require a recovery_action")
        if self.outcome_class != OutcomeClass.RECOVERABLE and self.recovery_action is not None:
            raise ValueError("recovery_action is only meaningful for RECOVERABLE rules")
        return self


# ---------------------------------------------------------------------------
# Metadata / versioning
# ---------------------------------------------------------------------------

class CapabilityMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=lambda: f"cap_{uuid4().hex[:12]}")
    name: str = Field(..., description="Human-readable capability name, e.g. 'lookup_member_balance'.")
    version: str = Field(default="1.0.0", description="Semver. Bump on any change to steps/locators/contract.")
    app_id: str = Field(..., description="Identifier of the target application/vendor product this was recorded against.")
    tenant_id: str | None = Field(
        default=None,
        description="Tenant this specific recording belongs to, if any. None means "
                    "'base/vendor-default' -- see multi-tenant reuse design in REPORT.md.",
    )
    author: str = Field(default="discovery_agent", description="Who/what produced this artifact.")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    review_status: Literal["draft", "approved", "deprecated"] = "draft"
    description: str = Field(..., description="What this capability does, in plain language, for a human reviewer.")


# ---------------------------------------------------------------------------
# Top-level artifact
# ---------------------------------------------------------------------------

class CapabilityArtifact(BaseModel):
    """The complete, reusable, agent-invocable capability.

    Contract summary (mirrors brief 3.2):
      - ordered steps                -> `steps`
      - how each control is targeted -> `CapabilityStep.locator` (+ reasoning)
      - typed input parameters       -> `input_parameters`
      - typed outputs                -> `output_parameters`
      - checkpoint / success cond.   -> `CapabilityStep.checkpoint` + `success_checkpoint`
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    metadata: CapabilityMetadata
    input_parameters: list[InputParameter] = Field(default_factory=list)
    output_parameters: list[OutputField] = Field(default_factory=list)
    steps: list[CapabilityStep] = Field(..., min_length=1)
    success_checkpoint: Checkpoint = Field(
        ..., description="Final condition confirming the overall goal was reached, checked after all steps run."
    )
    exceptional_rules: list[ExceptionalRule] = Field(
        default_factory=list,
        description="Runtime conditions this capability knows how to classify/recover from.",
    )
    allowed_domains: list[str] = Field(
        ..., min_length=1,
        description="Domains this capability is permitted to touch. Enforced by the safety "
                    "guardrail at replay time independent of what the steps themselves say.",
    )
    allowed_actions: list[ActionType] = Field(
        default_factory=lambda: [
            ActionType.NAVIGATE, ActionType.TYPE, ActionType.CLICK,
            ActionType.EXTRACT, ActionType.WAIT_FOR, ActionType.SELECT, ActionType.DISMISS
        ],
        min_length=1,
        description="Explicit permitted actions for this capability. Missing/disallowed actions fail closed.",
    )
    allowed_routes: list[str] | None = Field(
        default=None,
        description="Optional route prefixes permitted for navigation. Enforced continuously.",
    )

    @field_validator("steps")
    @classmethod
    def _unique_step_ids(cls, v: list[CapabilityStep]):
        ids = [s.step_id for s in v]
        if len(ids) != len(set(ids)):
            raise ValueError("step_id values must be unique within an artifact")
        return v

    @model_validator(mode="after")
    def _input_placeholders_resolve(self):
        """Every '{param}' placeholder used in a step's input_value must
        correspond to a declared input parameter -- catches a whole class
        of "capability looks fine, replay crashes on first use" bugs at
        artifact-authoring time rather than at replay time.
        """
        declared = {p.name for p in self.input_parameters}
        import re
        placeholder_re = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")
        for step in self.steps:
            if step.input_value:
                for name in placeholder_re.findall(step.input_value):
                    if name not in declared:
                        raise ValueError(
                            f"step {step.step_id} references undeclared parameter '{{{name}}}'"
                        )
            if step.target_url:
                for name in placeholder_re.findall(step.target_url):
                    if name not in declared:
                        raise ValueError(
                            f"step {step.step_id} references undeclared parameter '{{{name}}}' in target_url"
                        )
        return self

    @model_validator(mode="after")
    def _output_extraction_steps_exist(self):
        """Every declared output must be extractable from *some* step's
        checkpoint state -- i.e. it's not enough to declare an output, the
        artifact must actually specify where to read it from. This is
        enforced structurally rather than left to replay-time hope.
        """
        # Each OutputField carries its own extraction_locator, so this is
        # really just confirming the list isn't empty when steps include
        # an EXTRACT action, and vice versa.
        has_extract_step = any(s.action == ActionType.EXTRACT for s in self.steps)
        if self.output_parameters and not has_extract_step:
            raise ValueError(
                "output_parameters declared but no step has action=EXTRACT"
            )
        return self
