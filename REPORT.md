# System Design Report: Computer-Use Automation System

**Candidate:** Yashwanth Vasireddy  
**Role:** Applied AI Engineer — Hiring Automation (CEO's Office)  
**Target Domain:** Legacy Core Banking & Credit Union Servicing Platforms (Fiserv/Jack Henry/FIS-style)

---

## 1. Architecture

The system implements the core operational paradigm:  
> **"The model discovers. The artifact becomes a reusable capability. Deterministic replay is how the AI agent invokes it in production."**

```
┌────────────────────────────────────────────────────────────────────────┐
│ PHASE 1: DISCOVERY (Model-in-the-Loop — Executed Once)                 │
│ Goal + Target URL ──► Surface Observer ──► LLM Planner ──► Live Surface│
│                                                │                       │
│                                                ▼                       │
│                                   Capability Artifact Compiler         │
│                                                │                       │
└────────────────────────────────────────────────┼───────────────────────┘
                                                 ▼
                             ┌───────────────────────────────────────┐
                             │   TYPED CAPABILITY ARTIFACT (.json)   │
                             │ (Contract, Locators, Checkpoints,     │
                             │  Inputs/Outputs, Error Taxonomy)      │
                             └───────────────────┬───────────────────┘
                                                 │
┌────────────────────────────────────────────────┼───────────────────────┐
│ PHASE 2: DETERMINISTIC REPLAY (0 Tokens, Sub-Second, Production Engine)│
│                                                ▼                       │
│ Runtime Inputs ────────► Replay Engine (No LLM in Decision Loop)       │
│                               │                                        │
│                               ├──► Safety Guardrail & PII Redactor     │
│                               ├──► Multi-Strategy Locator Resolver     │
│                               ├──► Recovery & Interstitial Manager     │
│                               └──► Human-in-the-Loop Escalation Seam   │
│                                                │                       │
│                                                ▼                       │
│                              Structured ExecutionResult Contract       │
│                              (SUCCESS | BUSINESS_OUTCOME | FAILURE)    │
└────────────────────────────────────────────────────────────────────────┘
```

### Key Decisions & Trade-Offs

1. **Hostile Local Mock Target vs. Public Sandbox**:
   * *Decision*: Implemented `mock_target/` as a standalone, server-rendered ASP.NET WebForms-style portal (*ApexCore*). Features nested `<table>` structures, compiler-generated IDs (`#ctl00_MainContent_txtMemberId`), dynamic maintenance modals, and complete absence of `data-testid` attributes.
   * *Trade-Off*: Incurred upfront engineering time to simulate banking realities, but eliminated third-party rate limits, flaky public sandboxes, and authentication rot. Evaluators can clone and verify the repository with zero external dependencies.
2. **Decoupling Discovery from Replay Execution**:
   * *Decision*: The LLM operates *strictly* during Phase 1 discovery. Once an artifact is compiled, the replay engine contains **zero LLM calls**.
   * *Trade-Off*: If a surface experiences catastrophic structural rewrites, deterministic replay stops and escalates rather than attempting unconstrained "self-healing." In banking, predictable execution and explicit failure contracts are vastly superior to non-deterministic model hallucinations.
3. **Pluggable Discovery Interface with Zero-Key Fallback**:
   * *Decision*: Abstracted `LLMClient` supporting Claude (`claude-3-5-sonnet`), Gemini (`gemini-2.5-flash`), and a local goal-directed explorer (`SimulatedDiscoveryClient`).
   * *Trade-Off*: Avoids vendor lock-in while guaranteeing evaluators can reproduce discovery runs without provisioning paid API keys.

---

## 2. Artifact Schema

The capability artifact (`src/schemas/artifact.py`) is designed as an **agent-invocable contract**, completely decoupled from discovery transcripts, prompt tokens, or raw vision coordinates.

### Key Structural Invariants

* **Multi-Strategy Locators with Robustness Reasoning**:  
  Elements are never bound to a single brittle selector. Each `CapabilityStep` and `OutputField` defines a `MultiStrategyLocator` with an ordered `chain` of candidates:
  $$\text{Stable ID} \longrightarrow \text{Accessible Role + Name} \longrightarrow \text{Spatial Label Proximity} \longrightarrow \text{Structural XPath}$$
  Each chain requires a mandatory `reasoning` field documenting *why* that priority was chosen. For example:
  > *"Structural ID is primary here because this simulates a legacy server-rendered app where IDs are compiler-generated and stable; on a modern SPA, the priority would invert toward accessible role/name as primary."*
* **Strict Type Coercion & Placeholders**:  
  Inputs (`InputParameter`) and outputs (`OutputField`) enforce strict primitive typing (`string`, `number`, `boolean`, `enum`). Parameter placeholders (`{member_id}`) are resolved dynamically during replay, preventing hardcoded credentials or test values from polluting the capability.
* **Structural Result Guarantees (`ExecutionResult`)**:  
  The result contract enforces mutually exclusive fields at schema validation time:
  * `SUCCESS` / `RECOVERED` $\implies$ requires `outputs`, forbids `debug` and `business_outcome`.
  * `BUSINESS_OUTCOME` $\implies$ requires `business_outcome`, forbids `outputs` and `debug`.
  * `HARD_FAILURE` $\implies$ requires `debug` (failed step, expected vs observed, failure screenshot), forbids `outputs`.

---

## 3. Determinism & Error Handling

Deterministic replay in enterprise banking must accommodate runtime variance without crashing. Our system enforces a three-way outcome taxonomy.

### 1. The Three-Way Outcome Split
| Outcome Class | Example Condition | System Behavior | Result Contract |
| :--- | :--- | :--- | :--- |
| **Expected Business Outcome** | Member `9999` $\to$ *"Member Record Not Found in Fiserv Core"* | Halts execution gracefully; reports structured domain data. **Not a system error.** | `status="BUSINESS_OUTCOME"`, `code="MEMBER_NOT_FOUND"` |
| **Recoverable Condition** | *"Scheduled Maintenance Notice"* interstitial modal appears | Detects modal, invokes `recovery_action` (clicks *"Acknowledge"*), resumes sequence. | `status="RECOVERED"`, records dismissal in `step_traces` |
| **Hard Failure** | Network timeout, unresolvable element, or page crash | Captures live screenshot, extracts redacted DOM context, halts cleanly. | `status="HARD_FAILURE"`, `debug=DebugContext(...)` |

### 2. Locator Fallback & Drift Telemetry
When evaluating a locator, `resolve_locator()` steps down the candidate chain. The engine records `LocatorResolution.candidate_index` on every step trace:
* `candidate_index == 0`: Primary selector resolved.
* `candidate_index > 0`: Resolved on fallback rung.  
Tracking the mean candidate index across hundreds of runs provides an automated **early-warning drift signal** before an enterprise capability experiences hard failure.

---

## 4. Heterogeneity & Multi-Tenant Architecture

### 1. Surface Abstraction Seam
The system abstracts surface interactions via a unified protocol:
```
[CapabilityArtifact / Replay Engine]
                 │
                 ▼
        [SurfaceAdapter API]
        ├── evaluate_locator(chain) -> ElementHandle
        ├── execute_action(action, handle, value)
        ├── capture_state() -> AccessibilityTree / Screenshot
        └── pause_for_operator() -> LiveSessionHandoff
                 │
    ┌────────────┴────────────┐
    ▼                         ▼
[PlaywrightWebAdapter]   [WindowsUIAutomationAdapter]
(DOM / Chromium)         (Win32 / MSAA / UIA Desktop Screens)
```
Because the artifact schema stores semantic concepts (ActionType, Accessible Roles, Text Anchors) rather than browser-exclusive bindings, porting from Web to a legacy Citrix or Windows Core Console (e.g. Jack Henry SilverLake) requires only replacing the surface driver, leaving capability definitions intact.

### 2. Multi-Tenant Reuse at Scale
When 200 credit unions run the same core vendor application with divergent branding and custom fields:
1. **Base Capability Inheritance**: A vendor-level base artifact (`app_id="fiserv_dna_v4"`, `tenant_id=None`) defines the canonical flow.
2. **Tenant Specialization Overrides**: Tenant-specific configuration layers override only shifted selectors or custom fields:
   ```json
   {
     "tenant_id": "cu_golden_gate",
     "locator_overrides": {
       "step_2_type": { "primary": "#custom_txtMemberNum" }
     }
   }
   ```
3. **Route Canonicalization**: Parameterized route templates (`/member/{member_id}/detail`) decouple navigation from institution-specific base domains.

---

## 5. Escalation & Handoff

Escalation is not an uncaught exception; it is an architected state machine operating on the **exact same live browser session**:

$$\text{AUTOMATION\_RUNNING} \xrightarrow{\text{trigger}} \text{ESCALATION\_PENDING} \xrightarrow{\text{takeover}} \text{HUMAN\_CONTROLLED} \xrightarrow{\text{signal done}} \text{RESUMING} \xrightarrow{\text{verify}} \text{AUTOMATION\_RUNNING}$$

### Dual Escalation Triggers
1. **Reactive Escalation (Stuck/Unresolvable)**: Automation hits an unexpected interstitial or unresolved locator candidate.
2. **Proactive Policy Escalation (`is_risky: true`)**: The agent reaches an irreversible action (e.g., submitting an account opening or wire transfer). Under enterprise safety policy, unattended execution halts and requires an operator sign-off.

### The Live Session Handoff Seam
* When escalation fires, `EscalationManager` creates a structured `InterventionRequest` carrying the goal, step index, live screenshot path, and current URL.
* Automation pauses. The live Playwright page is yielded to the operator console.
* The human operator interacts with the live browser (or CLI prompt), completes the challenge or signs off, and enters `resume`.
* Automation records `OperatorAction` in the audit log, re-verifies postconditions, and resumes deterministic execution.

---

## 6. Safety & Financial Data Guardrails

* **Domain & Route Allowlists**: `PolicyGuardrail` validates every navigation against `artifact.allowed_domains`. Navigation to unlisted hosts raises `SecurityViolationError`.
* **Zero PII & Secrets Persistence**: `src/safety/redaction.py` enforces regex sanitization across all logs, step traces, and output payloads:
  * SSNs (`\b\d{3}-\d{2}-\d{4}\b`) $\to$ `[REDACTED_SSN]`
  * Credit/Debit Cards (`\b(?:\d{4}[ -]?){3}\d{4}\b`) $\to$ `[REDACTED_CARD]`
  * JWTs & Session Tokens $\to$ `[REDACTED_JWT]`
  * Passwords / API Keys $\to$ `[REDACTED_SECRET]`

---

## 7. Cuts & Future Work

### Deliberately Cut (In Accordance with Section 7 of the Brief)
1. **Premature Distributed Infrastructure**: Avoided Celery task queues, Redis brokers, and PostgreSQL database schemas to keep evaluation immediate, lightweight, and focused on core engine judgment.
2. **Full WebRTC Co-Browsing Console**: The operator interface uses a robust CLI and in-session Playwright pause mechanism rather than a heavy real-time video streaming web portal.
3. **Open-Ended Replay Self-Healing**: Did not permit arbitrary LLM re-prompting during replay failures. In financial servicing, silent model guessing on production core accounts creates severe compliance liabilities.

### What to Build Next
1. **Automated Cross-Tenant Validator**: Replay recorded base capabilities across an array of tenant test environments to generate automated drift and compatibility scorecards.
2. **Multi-Modal Visual Anchor Fallbacks**: Augment DOM text locators with localized visual embedding templates (using lightweight OpenCV or CLIP features) for legacy environments that do not expose an OS accessibility tree.
