# Computer-Use Automation System

**A robust, record-once / replay-many automation layer designed for legacy banking applications without APIs.**

---

## Core Philosophy

> **"The model discovers. The artifact becomes a reusable capability. Deterministic replay is how the AI agent invokes it in production."**

1. **Discovery (Model in the Loop)**: An LLM explores a live application surface using an **Observe -> Decide -> Act** loop, overcoming hostile legacy quirks (nested `<table>` tags, ASP.NET-style control IDs, absence of `data-testid`).
2. **Capability Artifact**: The successful execution is compiled into a typed, versioned, agent-invocable capability schema decoupled from the raw model transcript.
3. **Deterministic Replay (Zero Model in the Loop)**: In production, the capability replays at **0 token cost** and sub-second per DOM interaction (measured ~4.7–4.9 seconds across 5 steps, ~5–6 seconds total wall-clock time including Playwright Chromium browser startup, compared to 15–30+ seconds for multi-turn LLM exploration), resolving elements via multi-strategy locator chains and distinguishing **Expected Business Outcomes** from **Recoverable Interstitials** and **Hard Failures**.
4. **Human-in-the-Loop Escalation**: When unresolvable blockers or irreversible mutations occur, the system pauses on the **exact same live browser session**, transfers control to a human operator, records their actions, and safely resumes automation. Unattended execution strictly fails closed.

---

## Quickstart & Setup

### 1. Prerequisites
* Python 3.10+ (tested on Python 3.13)
* Playwright with Chromium installed

### 2. Installation
Clone the repository and install dependencies:
```bash
git clone https://github.com/Yashwanth-23/computer-use.git
cd computer-use

pip install -r requirements.txt
playwright install chromium
```

### 3. API Keys & Execution Modes
* **Running with an LLM Provider**:
  Set the corresponding environment variable for your target frontier provider:
  * **Anthropic**: `export ANTHROPIC_API_KEY="sk-ant-..."` (used for the canonical Claude Sonnet 5 discovery run)
  * **OpenAI**: `export OPENAI_API_KEY="sk-proj-..."`
  * **Google Gemini**: `export GEMINI_API_KEY="AIzaSy..."`
  * **Moonshot / Kimi**: `export KIMI_API_KEY="..."` or `export MOONSHOT_API_KEY="..."`
* **Running Standalone (Simulated Discovery Mode)**:
  * In simulated discovery mode, **no external model or banking service is required**; the built-in simulated client explores the live mock portal locally.
  * Deterministic replay requires **zero LLM tokens and no API keys** across all scenarios.

---

## Demo Path (Step-by-Step)

### Step 1: Start the Hostile Legacy Banking Portal
In a separate terminal, launch the *ApexCore* legacy banking mock server:
```bash
python -m src.cli serve-mock --port 8000
```
*(Server runs at `http://127.0.0.1:8000/`. Features server-rendered legacy ASP.NET controls, nested tables, member search, account balances, and sub-account opening).*

---

### Step 2: Run Goal-Driven Discovery
Execute the LLM discovery loop to navigate the portal, identify elements, and compile a reusable capability artifact:
```bash
python -m src.cli discover --goal "Look up member 1001 and read savings and checking balances" --target "http://127.0.0.1:8000/portal/member-lookup" --output evidence/capability_member_lookup.json
```
*Output*: A validated, typed capability artifact written to `evidence/capability_member_lookup.json` with execution telemetry logged to `evidence/discovery_run.log`.

---

### Step 3: Run Deterministic Replay (Happy Path)
Invoke the saved capability with input parameters (0 tokens, deterministic execution).

You can run the self-booting helper (boots mock server automatically in one command):
```bash
python -m scripts.run_replay --member 1001 --headless
```

Or run via the direct CLI (with mock server running):
```bash
python -m src.cli replay --artifact evidence/capability_member_lookup.json --input "{\"member_id\": \"1001\"}"
```
*Output*:
```
============================================================
REPLAY RESULT: SUCCESS
============================================================
Run ID:      run_1d2e6604e095
Capability:  cap_bfa2d82803e3 (v1.0.0)
Outputs:     {
  "savings_balance": 24500.0,
  "checking_balance": 4120.0
}
------------------------------------------------------------
Executed 5 steps:
  * step_1_navigate           ( 990.9ms) 
  * step_2_type               ( 969.3ms) [stable_id index=0]
  * step_3_click              ( 999.0ms) [stable_id index=0]
  * step_4_extract            ( 959.0ms) [stable_id index=0]
  * step_5_extract            ( 935.3ms) [stable_id index=0]
============================================================
```

---

### Step 4: Replay an Expected Business Outcome (Member Not Found)
Replay with non-existent member `9999`:
```bash
python -m src.cli replay --artifact evidence/capability_member_lookup.json --input "{\"member_id\": \"9999\"}"
```
*Result Contract*: Returns `status="BUSINESS_OUTCOME"` with structured outcome `[MEMBER_NOT_FOUND]`. **This is an expected business result, not an unhandled exception or crash.**

---

### Step 5: Test Recoverable Interstitials & Human Escalation

#### A. Recoverable Interstitial (Maintenance Alert)
Enable the simulated maintenance alert and replay:
```bash
# In another terminal or curl:
curl -X POST http://127.0.0.1:8000/admin/maintenance/on

python -m src.cli replay --artifact evidence/capability_member_lookup.json --input "{\"member_id\": \"1001\"}"
```
*(The replay engine detects `#pnlMaintenanceAlert`, executes the recovery step by clicking `#btnAckMaintenance`, and completes the lookup with `status=RECOVERED`).*

#### B. Fail-Closed Unattended Risky Replay
Attempt to execute an irreversible sub-account opening without an operator:
```bash
python -m src.cli replay --artifact evidence/capability_open_subaccount.json --input "{\"member_id\": \"1001\", \"product_type\": \"HOLIDAY_CLUB\", \"initial_deposit\": \"50.00\"}"
```
*Result*:
```
============================================================
REPLAY RESULT: ESCALATED
============================================================
Run ID:      run_7f8c12a04d2e
Capability:  cap_open_sub_account (v1.0.0)
Incident ID: esc_31c7f776e0e9
Policy Gate: Unattended execution halted on RISKY_IRREVERSIBLE action.
Rerun with --interactive (and optionally --headed) to provide human operator authorization.
```

#### C. Interactive Supervised Escalation (Headed Live Browser)
Re-run with human operator authorization on a live headed browser:
```bash
python -m src.cli replay --artifact evidence/capability_open_subaccount.json --input "{\"member_id\": \"1001\", \"product_type\": \"HOLIDAY_CLUB\", \"initial_deposit\": \"50.00\"}" --interactive --headed
```
1. Automation drives the browser to the review confirmation screen.
2. The engine detects Step 5 is `RISKY_IRREVERSIBLE`, pauses execution on the live session, captures a screenshot, and cedes control to the operator.
3. The CLI prompts the operator to inspect the live session and authorize (`resume`) or cancel (`abort`).
4. Upon confirmation, automation commits the financial mutation to the ledger, verifies the receipt checkpoint, and returns `receipt_id`.

---

## Automated Test Suite

Run the full automated test suite (52 tests covering schemas, guardrails, locator fallbacks, error taxonomy, dead-end detection, and escalation state machine):
```bash
pytest tests/ -v
```

To regenerate all audit logs, evidence artifacts, and cryptographically verified manifest in `/evidence/`:
```bash
python scripts/generate_evidence.py
```

---

## Repository Structure

```
├── README.md                           # Quickstart, architecture, demo commands
├── REPORT.md                           # Formal 7-section technical design report
├── requirements.txt                    # Project dependencies
├── evidence/                           # Proof of real execution runs
│   ├── capability_member_lookup.json   # Discovered canonical capability artifact (cap_bfa2d82803e3)
│   ├── capability_open_subaccount.json # Sub-account creation artifact with RISKY_IRREVERSIBLE gate
│   ├── discovery_run.log               # Live Claude Sonnet 5 discovery audit log
│   ├── replay_success.log              # Deterministic replay log (Happy path, Member 1001)
│   ├── replay_business_outcome_404.log # Expected business outcome log (Member 9999)
│   ├── replay_interstitial_recovery.log# Recoverable condition log (Maintenance banner dismissed)
│   ├── replay_escalation_handoff.log   # Simulated supervisor handoff log (mode: simulated_operator_supervised)
│   ├── replay_hard_failure.log         # Sanitized diagnostic failure log with masked screenshot
│   ├── manifest.json                   # Cryptographic manifest (hashes, run IDs, commit SHA)
│   └── screenshots/                    # Active masked failure and escalation screenshots
├── mock_target/                        # Legacy core banking application (ApexCore)
│   ├── app.py                          # FastAPI server with legacy routes & admin toggles
│   ├── core_data.py                    # In-memory core data records
│   └── templates/                      # Hostile nested tables, frames, ASP.NET controls
├── src/                                # Core Engine Source
│   ├── agent/                          # LLM discovery loop & artifact compiler
│   ├── engine/                         # Zero-LLM deterministic replay engine & error diagnostics
│   ├── escalation/                     # Live session human escalation & handoff (with DOM visual masking)
│   ├── safety/                         # Domain/route guardrails & PII/Secrets sanitizer
│   ├── schemas/                        # Pydantic v2 artifact & execution contracts
│   └── cli.py                          # Unified CLI entry point
└── tests/                              # Rigorous unit and integration test suite (52 tests)
```
