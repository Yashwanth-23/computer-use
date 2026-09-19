# System Design Report: Computer-Use Automation System

**Author:** Yashwanth Reddy Vasireddy  
**Project:** Autonomous Computer-Use Automation System for Legacy Enterprise Surfaces  
**Target Domain:** Legacy Core Banking & Credit Union Servicing Platforms (Fiserv/Jack Henry/FIS-style)

---

## 1. Architecture

The system implements the core operational paradigm:  
> **"The model discovers. The artifact becomes a reusable capability. Deterministic replay is how the AI agent invokes it in production."**

```
┌────────────────────────────────────────────────────────────────────────┐
│ PHASE 1: DISCOVERY (Model-in-the-Loop: Executed Once)                  │
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
│ PHASE 2: DETERMINISTIC REPLAY (0 Tokens, Zero-LLM Production Engine)   │
│                                                ▼                       │
│ Runtime Inputs ────────► Replay Engine (No LLM in Decision Loop)       │
│                               │                                        │
│                               ├──► Policy Guardrail & PII Redactor     │
│                               ├──► Multi-Strategy Locator Resolver     │
│                               ├──► Recovery & Interstitial Manager     │
│                               └──► Human-in-the-Loop Escalation Seam   │
│                                                │                       │
│                                                ▼                       │
│                              Structured ExecutionResult Contract       │
│                              (SUCCESS | BUSINESS_OUTCOME | FAILURE)    │
└────────────────────────────────────────────────────────────────────────┘
```

### Implemented Vertical Slice vs. Architectural Extensions

To maintain rigorous technical transparency, we distinguish between what is **implemented and verified in code** versus **architectural designs for enterprise expansion**:

* **Implemented Vertical Slice**:
  * Standalone mock banking core (*ApexCore* v4.2) serving server-rendered ASP.NET WebForms with nested tables, dynamic modals, and multi-step sub-account opening workflows.
  * Autonomous discovery loop with wall-clock deadline enforcement, cycle/dead-end state fingerprinting, and semantic risk classification.
  * Deterministic Playwright-based zero-token execution engine with multi-strategy locator chains, continuous post-action route validation, and fail-closed action allowlisting.
  * Human-in-the-loop escalation state machine yielding live browser sessions and auditing operator interventions.
  * PII redaction and pre-screenshot visual DOM blurring.
* **Design-Only Architectural Extensions (V2 Roadmap)**:
  * Desktop OS Surface Adapters (Windows UI Automation / Citrix virtual display scraping).
  * Distributed multi-tenant overlay inheritance with delta patch compilation.
  * WebRTC real-time operator streaming console.

### Key Decisions & Trade-Offs

1. **Hostile Local Mock Target vs. Public Sandbox**:
   * *Decision*: Implemented `mock_target/` as a standalone, server-rendered ASP.NET WebForms-style portal (*ApexCore*). Features nested `<table>` structures, compiler-generated IDs (`#ctl00_MainContent_txtMemberId`), dynamic maintenance modals, and complete absence of `data-testid` attributes.
   * *Trade-Off*: Incurred upfront engineering time to simulate banking realities, but eliminated third-party rate limits, flaky public sandboxes, and authentication rot. Evaluators can clone and verify the repository with zero external dependencies.
2. **Decoupling Discovery from Replay Execution**:
   * *Decision*: The LLM operates *strictly* during Phase 1 discovery. Once an artifact is compiled, the replay engine contains **zero LLM calls**.
   * *Trade-Off*: If a surface experiences catastrophic structural rewrites, deterministic replay stops and escalates rather than attempting unconstrained "self-healing." In banking, predictable execution and explicit failure contracts are vastly superior to non-deterministic model hallucinations.
3. **Pluggable Discovery Interface with Frontier Model Telemetry**:
   * *Decision*: Abstracted `LLMClient` supporting Anthropic Claude (`claude-sonnet-5`), OpenAI (`gpt-4o`), Kimi (`moonshot-v1-8k`), Gemini (`gemini-2.5-flash`), and a local goal-directed explorer (`SimulatedDiscoveryClient`).
   * *Trade-Off*: Avoids vendor lock-in while guaranteeing evaluators can reproduce discovery runs without provisioning paid API keys.
4. **Token-Efficient Interactive Observation vs. Raw DOM Dumps**:
   * *Decision*: Rather than flooding LLM context with the raw 50KB HTML tree each turn, `SurfaceObserver` parses the accessibility tree and extracts interactive controls (inputs, buttons, select, links, and balance grids) into a structured compact summary.
   * *Trade-Off*: Keeps input token growth lean (~600-1000 tokens/turn) and discovery latency low (~1.2-2.9s) while providing 100% of required visual/functional affordances.
5. **Cycle Detection & Discovery Dead-End Containment**:
   * *Decision*: Implemented a 60-second wall-clock deadline alongside SHA-256 state fingerprinting in `DiscoveryAgent`. If an exploration exceeds the wall-clock deadline, it raises `TimeoutError`. If it hits the same state signature 3 times consecutively without progress, the loop halts immediately with `RuntimeError` ("detected 3 duplicate cycles"). Incomplete or aborted explorations fail cleanly without writing corrupted artifacts.

---

## 2. Artifact Schema

The capability artifact (`src/schemas/artifact.py`) is designed as an **agent-invocable contract**, completely decoupled from discovery transcripts, prompt tokens, or raw vision coordinates.

### Key Structural Invariants

* **Multi-Strategy Locators with Robustness Reasoning**:  
  Elements are never bound to a single brittle selector. Each `CapabilityStep` and `OutputField` defines a `MultiStrategyLocator` with an ordered `chain` of candidates:
  ```
  Stable ID ──► Accessible Role + Name ──► Spatial Label Proximity ──► Structural XPath
  ```
  Each chain requires a mandatory `reasoning` field documenting *why* that priority was chosen. For example:
  > *"Structural ID is primary here because this simulates a legacy server-rendered app where IDs are compiler-generated and stable; on a modern SPA, the priority would invert toward accessible role/name as primary."*
* **Semantic Extraction Fallbacks & Label Alignment Caveat**:  
  Extraction fallbacks anchor to semantic table row headers (e.g. `tr:has(td:has-text("Savings")) >> span`) rather than dynamic data values, guaranteeing parameterized reusability across any member record. *Production Caveat*: The compiler tokenizes element IDs and descriptions to derive anchor text. In enterprise multi-tenant deployments where UI headers vary significantly across credit unions (e.g. "Share Savings" vs. "Savings Account"), a tenant label normalization mapping or discovery-time visual OCR anchor dictionary is recommended to maintain alignment.
* **Strict Type Coercion & Placeholders**:  
  Inputs (`InputParameter`) and outputs (`OutputField`) enforce strict primitive typing (`string`, `number`, `boolean`, `enum`). Parameter placeholders (`{member_id}`) in both `input_value` and `target_url` are statically validated against declared inputs at authoring time and resolved dynamically during replay, preventing hardcoded credentials or test values from polluting the capability.
* **Explicit Policy Allowlisting**:  
  Every artifact explicitly declares `allowed_domains`, `allowed_actions`, and optional `allowed_routes`. If a malicious or buggy step attempts an undeclared action (e.g. script injection or keyboard shortcut) or an off-route URL, the guardrail aborts execution before the browser executes the command.
* **Structural Result Guarantees (`ExecutionResult`)**:  
  The result contract enforces mutually exclusive fields at schema validation time:
  * `SUCCESS` / `RECOVERED`: requires `outputs`, forbids `debug` and `business_outcome`.
  * `BUSINESS_OUTCOME`: requires `business_outcome`, forbids `outputs` and `debug`.
  * `HARD_FAILURE`: requires `debug` (failed step, expected vs observed, failure screenshot), forbids `outputs`.
  * `ESCALATED`: requires `escalation_ref`, forbids `outputs`.

---

## 3. Determinism & Error Handling

Deterministic replay in enterprise banking must accommodate runtime variance without crashing. Our system enforces a three-way outcome taxonomy.

### 1. The Three-Way Outcome Split
| Outcome Class | Example Condition | System Behavior | Result Contract |
| :--- | :--- | :--- | :--- |
| **Expected Business Outcome** | Member `9999` -> *"Member Record Not Found in Fiserv Core"* | Halts execution gracefully; reports structured domain data. **Not a system error.** | `status="BUSINESS_OUTCOME"`, `code="MEMBER_NOT_FOUND"` |
| **Recoverable Condition** | *"Scheduled Maintenance Notice"* interstitial modal appears | Detects modal, invokes `recovery_action` (clicks *"Acknowledge"*), resumes sequence. | `status="RECOVERED"`, records dismissal in `step_traces` |
| **Hard Failure** | Network timeout, unresolvable element, or page crash | Captures live screenshot, extracts redacted DOM context, halts cleanly. | `status="HARD_FAILURE"`, `debug=DebugContext(...)` |

### 2. Locator Fallback & Drift Telemetry
When evaluating a locator, `resolve_locator()` steps down the candidate chain. The engine records `LocatorResolution.candidate_index` on every step trace:
* `candidate_index == 0`: Primary selector resolved.
* `candidate_index > 0`: Resolved on fallback rung.  
Tracking the mean candidate index across hundreds of runs provides an automated **early-warning drift signal** before an enterprise capability experiences hard failure.

### 3. Runtime Condition Precedence
When an exceptional condition triggers during execution, `RecoveryManager` evaluates candidate signatures under strict precedence:
1. **Recoverable Interstitials First**: If a modal overlay (e.g. `pnlMaintenanceAlert`) is visible, its dismissal action is executed immediately so the primary workflow can proceed without stall.
2. **Business Outcomes Second**: If a terminal business status signature is matched (e.g. `#ctl00_MainContent_lblResultMessage` containing "Record Not Found"), the run concludes immediately with status `BUSINESS_OUTCOME` and structured domain payloads.
3. **Hard Failure Last**: If a locator cannot be resolved across all fallback rungs and no exceptional rule matches, execution halts cleanly with captured screenshots and redacted DOM debug context.

### 4. Measured Execution Latency
Contrary to broad "sub-second end-to-end" claims, measured execution latency reflects real DOM rendering, server postback roundtrips, and browser initialization:
* **Per DOM Interaction**: ~25ms–50ms for local DOM queries and inputs; ~900ms–1000ms when handling server-rendered ASP.NET postbacks and page loads.
* **Full Multi-Step Replay**: ~4.7s–4.9s across the 5 replay steps (~950ms–980ms per step); ~5s–6s total wall-clock time including Playwright Chromium browser startup.
* **Speedup vs. Discovery**: Compared to multi-turn LLM discovery which takes 15–30+ seconds and consumes 3,000–5,000 tokens, deterministic replay delivers a **3x–6x latency improvement and infinite token efficiency at exactly zero model cost**.

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
*In the core V1 artifact schema (`src/schemas/artifact.py`), `CapabilityMetadata` defines `tenant_id: Optional[str] = None` and `app_id`. The overlay mechanism detailed below is the planned V2 specification for tenant-specific delta patching without duplicating base capabilities:*

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

```
[AUTOMATION_RUNNING] ──(trigger)──► [ESCALATION_PENDING] ──(takeover)──► [HUMAN_CONTROLLED] ──(resume)──► [RESUMING] ──(verify)──► [AUTOMATION_RUNNING]
```

### Fail-Closed Unattended Risk Policy
Banking compliance strictly forbids unattended AI agents from committing irreversible financial mutations.
* **Semantic Risk Classification**: Steps matching state-changing verbs (`confirm`, `submit`, `open-subaccount`, `transfer`) are classified as `RiskLevel.RISKY_IRREVERSIBLE` with required `risk_justification`.
* **Zero-Bypass Mandate**: Under unattended replay (headless without an operator session), any encounter with a `RISKY_IRREVERSIBLE` action **fails closed**. The engine halts immediately, leaves state completely untouched, sets `step_trace.outcome = StepOutcome.SKIPPED_ESCALATED`, and returns `ReplayStatus.ESCALATED`.
* **No Fabricated Live-Human Approval**: The system never auto-resumes or writes false audit statements. Live-human approval is only logged when an authentic operator interactively authorizes the action.

### The Live Session Handoff Seam
* When escalation fires, `EscalationManager` creates a structured `InterventionRequest` carrying the goal, step index, live screenshot path, and current URL.
* Automation pauses. The live Playwright page is yielded to the operator console.
* The human operator interacts with the live browser (or CLI prompt), completes the challenge or signs off, and enters `resume`.
* Automation records `OperatorAction` in the audit log, re-verifies postconditions, and resumes deterministic execution.
* **Evidence Run Mode**: In automated regression suites and verifiable evidence generation (`scripts/generate_evidence.py`), this handoff is exercised via a simulated supervisor callback (`mode: simulated_operator_supervised`, with log title `=== SIMULATED OPERATOR SUPERVISED HANDOFF AUDIT TRAIL ===` and supervisor ID `SUPV-8821`), proving the control transfer seam end-to-end without fabricating live human presence. Live headed interaction is invoked via `--interactive --headed`.

---

## 6. Safety & Financial Data Guardrails

### 1. Domain, Scheme, and Route Allowlists
* `PolicyGuardrail` enforces host/port verification: `http://127.0.0.1:8000` is accepted, while unauthorized external hosts are rejected with `SecurityViolationError`.
* **Continuous Post-Action Route Validation**: Evaluated after every navigation, click, and form submit (`page.url`) to prevent malicious redirects or cross-site escapes.
* **Embedded Credentials Blocked**: URLs containing basic authentication credentials (`user:pass@host`) are rejected immediately.

### 2. PII & Secrets Redaction
`src/safety/redaction.py` enforces regex sanitization across all logs, step traces, exception strings, and output payloads:
* SSNs (`\b\d{3}-\d{2}-\d{4}\b`) -> `[REDACTED_SSN]`
* Credit/Debit Cards (`\b(?:\d{4}[ -]?){3}\d{4}\b`) -> `[REDACTED_CARD]`
* JWTs & Session Tokens -> `[REDACTED_JWT]`
* Passwords / Secrets -> `[REDACTED_SECRET]`

### 3. Screenshot Policy & Visual Masking
* **Pre-Screenshot DOM Blurring**: Before any failure or escalation screenshot is captured, both `ErrorDiagnostics` and `EscalationManager` inject visual CSS filters (`filter: blur(6px)`) onto sensitive DOM elements (`.ssn, .balance, [data-sensitive], input[type="password"]`). Synthetic demo review inputs and confirmation labels remain intentionally legible so the operator or supervisor can inspect and verify the transaction context before sign-off.
* **Ephemeral Storage & Retention**: Screenshots are stored strictly in `evidence/screenshots/`. In production, these directories are mounted with 7-day TTL policies and encrypted at rest.
* **Cryptographic Manifest Tracking**: Orphaned screenshots from aborted or test runs are actively pruned during evidence generation, ensuring only active, audited evidence artifacts are retained in `evidence/manifest.json`.

---

## 7. Cuts

### Deliberately Cut for Production Focus
1. **Premature Distributed Infrastructure**: Avoided Celery task queues, Redis brokers, and PostgreSQL database schemas to keep evaluation immediate, lightweight, and focused on core engine judgment.
2. **Full WebRTC Co-Browsing Console**: The operator interface uses a robust CLI and in-session Playwright pause mechanism rather than a heavy real-time video streaming web portal.
3. **Open-Ended Replay Self-Healing**: Did not permit arbitrary LLM re-prompting during replay failures. In financial servicing, silent model guessing on production core accounts creates severe compliance liabilities.
4. **Desktop Native Automation**: Left the `WindowsUIAutomationAdapter` as a typed architectural interface rather than implementing native Win32 C++ hooks, maintaining full cross-platform portability on Linux/macOS/Windows.

### What to Build Next
1. **Automated Cross-Tenant Validator**: Replay recorded base capabilities across an array of tenant test environments to generate automated drift and compatibility scorecards.
2. **Multi-Modal Visual Anchor Fallbacks**: Augment DOM text locators with localized visual embedding templates (using lightweight OpenCV or CLIP features) for legacy environments that do not expose an OS accessibility tree.
3. **Automated Sub-Account Rollback Playbooks**: Pair irreversible capability artifacts with inverse compensating transaction capabilities (e.g. `close_subaccount`) for automated recovery when permitted by supervisor policies.
