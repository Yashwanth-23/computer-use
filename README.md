# Computer-Use Automation System

**A production-grade, record-once / replay-many automation layer designed for legacy banking applications without APIs.**

Built for the **interface.ai Applied AI Engineer: Hiring Automation (CEO's Office)** take-home assignment.

---

## Core Philosophy

> **"The model discovers. The artifact becomes a reusable capability. Deterministic replay is how the AI agent invokes it in production."**

1. **Discovery (Model in the Loop)**: An LLM explores a live application surface using an *Observe $\to$ Decide $\to$ Act* loop, overcoming hostile legacy quirks (nested `<table>` tags, ASP.NET-style control IDs, absence of `data-testid`).
2. **Capability Artifact**: The successful execution is compiled into a typed, versioned, agent-invocable capability schema decoupled from the raw model transcript.
3. **Deterministic Replay (Zero Model in the Loop)**: In production, the capability replays at **0 token cost** and sub-second latency, resolving elements via multi-strategy locator chains and distinguishing **Expected Business Outcomes** from **Recoverable Interstitials** and **Hard Failures**.
4. **Human-in-the-Loop Escalation**: When unresolvable blockers or irreversible actions occur, the system pauses on the **exact same live browser session**, transfers control to a human operator, records their actions, and safely resumes automation.

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

### 3. API Keys & Live Services
* **Running with an LLM Key**:
  * Set `GEMINI_API_KEY` (Free tier from [Google AI Studio](https://aistudio.google.com/)) or `ANTHROPIC_API_KEY` (Claude Sonnet 5).
* **Running Without Any Keys (100% Offline / Standalone)**:
  * The system ships with a built-in goal-directed explorer (`SimulatedDiscoveryClient`). If no API key is present, discovery runs locally against the live browser, compiling real capability artifacts right out of the box with zero external dependencies.

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
Invoke the saved capability with input parameters (0 tokens, sub-second execution).

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
Run ID:      run_147fa0d1a491
Capability:  lookup_member_balance (v1.0.0)
Outputs:     {
  "savings_balance": 24500.0,
  "checking_balance": 4120.0
}
------------------------------------------------------------
Executed 5 steps:
  * step_1_navigate           (2035.4ms) 
  * step_2_type               (2453.6ms) [stable_id index=0]
  * step_3_click              (2469.7ms) [stable_id index=0]
  * step_4_extract            (2444.8ms) [stable_id index=0]
  * step_5_extract            (2444.6ms) [stable_id index=0]
============================================================
```

---

### Step 4: Replay an Expected Business Outcome (Member Not Found)
Replay with non-existent member `9999`:
```bash
python -m scripts.run_replay --member 9999 --headless
```
*(Or via direct CLI: `python -m src.cli replay --artifact evidence/capability_member_lookup.json --input "{\"member_id\": \"9999\"}"`)*

*Result Contract*: Returns `status="BUSINESS_OUTCOME"` with structured outcome `[MEMBER_NOT_FOUND]`. **This is an expected business result, not an unhandled exception or crash.**

---

### Step 5: Test Recoverable Interstitials & Human Escalation
* **Recoverable Interstitial**: Enable the simulated maintenance alert:
  ```bash
  curl -X POST http://127.0.0.1:8000/admin/maintenance/on
  python -m scripts.run_replay --member 1001 --headless
  ```
  *(The replay engine automatically detects the maintenance banner, clicks "Acknowledge", and successfully completes the flow).*
* **Live Session Human Escalation**:
  ```bash
  python -m src.cli replay --artifact evidence/capability_member_lookup.json --interactive
  ```

---

## Automated Test Suite

Run the full automated test suite (39 tests covering schemas, guardrails, locator fallbacks, error taxonomy, and escalation state machine):
```bash
pytest tests/ -v
```

To re-generate all audit logs, evidence artifacts, and screenshots in `/evidence/`:
```bash
python -m scripts.generate_evidence
```

---

## Repository Structure

```
├── README.md                           # Quickstart, architecture, demo commands
├── REPORT.md                           # Formal 7-section technical write-up
├── requirements.txt                    # Project dependencies
├── evidence/                           # Proof of real execution runs
│   ├── capability_member_lookup.json   # Compiled capability artifact
│   ├── discovery_run.log               # Live LLM discovery audit log
│   ├── replay_success.log              # Deterministic replay log (Happy path)
│   ├── replay_business_outcome_404.log # Expected business outcome log (Member 9999)
│   ├── replay_interstitial_recovery.log# Recoverable condition log (Modal dismissed)
│   ├── replay_escalation_handoff.log   # Human handoff audit log on live session
│   └── screenshots/                    # Checkpoint and escalation screenshots
├── mock_target/                        # Legacy core banking application (ApexCore)
│   ├── app.py                          # FastAPI server with legacy routes & admin toggles
│   ├── core_data.py                    # In-memory core data records
│   └── templates/                      # Hostile nested tables, frames, ASP.NET controls
├── src/                                # Core Engine Source
│   ├── agent/                          # LLM discovery loop & artifact compiler
│   ├── engine/                         # Zero-LLM deterministic replay engine
│   ├── escalation/                     # Live session human escalation & handoff
│   ├── safety/                         # Domain allowlist & PII/Secrets sanitizer
│   ├── schemas/                        # Pydantic v2 artifact & execution contracts
│   └── cli.py                          # Unified CLI entry point
└── tests/                              # Rigorous unit and integration test suite
```
