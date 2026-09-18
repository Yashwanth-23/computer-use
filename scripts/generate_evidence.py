import os
import json
import time
import threading
import requests
import uvicorn
from datetime import datetime, timezone

from mock_target.app import app
from mock_target import core_data
from src.agent.discovery_loop import DiscoveryAgent
from src.schemas.artifact import CapabilityArtifact
from src.schemas.execution import ReplayStatus
from src.engine.replay_executor import ReplayExecutor
from src.schemas.escalation import EscalationReason
from src.escalation.escalation_manager import EscalationManager
from playwright.sync_api import sync_playwright


def main():
    print("=== Generating Computer-Use Automation Evidence Suite ===")
    os.makedirs("evidence/screenshots", exist_ok=True)

    # 1. Start mock server in background thread
    core_data.reset_all()
    server_config = uvicorn.Config(app=app, host="127.0.0.1", port=8000, log_level="warning")
    server = uvicorn.Server(server_config)
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    time.sleep(1.5)

    # 2. Discovery Run
    print("\n[1/5] Running Goal-Driven LLM Discovery...")
    agent = DiscoveryAgent(headless=True, log_file="evidence/discovery_run.log")
    artifact = agent.discover(
        goal="Look up member 1001 and read savings and checking balances",
        target_url="http://127.0.0.1:8000/portal/member-lookup",
        capability_name="lookup_member_balance",
        max_steps=5,
    )

    artifact_path = "evidence/capability_member_lookup.json"
    with open(artifact_path, "w", encoding="utf-8") as f:
        f.write(artifact.model_dump_json(indent=2))
    print(f"Saved artifact to {artifact_path}")

    executor = ReplayExecutor(headless=True, evidence_dir="evidence")

    # 3. Deterministic Replay: Happy Path (Member 1001)
    print("\n[2/5] Running Replay: Happy Path (Member 1001)...")
    core_data.reset_all()
    res_success = executor.run(artifact, inputs={"member_id": "1001"})

    success_log = [
        f"=== REPLAY LOG: SUCCESS (0 TOKENS, DETERMINISTIC) ===",
        f"TIMESTAMP:   {datetime.now(timezone.utc).isoformat()}",
        f"RUN ID:      {res_success.run_id}",
        f"STATUS:      {res_success.status.value.upper()}",
        f"CAPABILITY:  {res_success.capability_id} (v{res_success.capability_version})",
        f"OUTPUTS:     {json.dumps(res_success.outputs, indent=2)}",
        "STEP TRACES:",
    ]
    for tr in res_success.step_traces:
        loc = f"[{tr.locator_resolution.strategy_used} candidate={tr.locator_resolution.candidate_index}]" if tr.locator_resolution else ""
        success_log.append(f"  * {tr.step_id:<25} ({tr.duration_ms:>5.1f}ms) {loc}")
    success_log.append("=====================================================")

    with open("evidence/replay_success.log", "w", encoding="utf-8") as f:
        f.write("\n".join(success_log) + "\n")
    print("Wrote evidence/replay_success.log")

    # 4. Deterministic Replay: Expected Business Outcome (Member 9999 Not Found)
    print("\n[3/5] Running Replay: Business Outcome (Member 9999)...")
    core_data.reset_all()
    res_not_found = executor.run(artifact, inputs={"member_id": "9999"})

    not_found_log = [
        f"=== REPLAY LOG: EXPECTED BUSINESS OUTCOME (NOT A CRASH) ===",
        f"TIMESTAMP:   {datetime.now(timezone.utc).isoformat()}",
        f"RUN ID:      {res_not_found.run_id}",
        f"STATUS:      {res_not_found.status.value.upper()}",
        f"OUTCOME:     [{res_not_found.business_outcome.outcome_code}]",
        f"RULE ID:     {res_not_found.business_outcome.matched_rule_id}",
        f"MESSAGE:     {res_not_found.business_outcome.message}",
        "STEP TRACES:",
    ]
    for tr in res_not_found.step_traces:
        loc = f"[{tr.locator_resolution.strategy_used} candidate={tr.locator_resolution.candidate_index}]" if tr.locator_resolution else ""
        detail = f" - {tr.detail}" if tr.detail else ""
        not_found_log.append(f"  * {tr.step_id:<25} ({tr.duration_ms:>5.1f}ms) {loc}{detail}")
    not_found_log.append("=====================================================")

    with open("evidence/replay_business_outcome_404.log", "w", encoding="utf-8") as f:
        f.write("\n".join(not_found_log) + "\n")
    print("Wrote evidence/replay_business_outcome_404.log")

    # 5. Deterministic Replay: Interstitial Recovery (Maintenance Alert)
    print("\n[4/5] Running Replay: Recoverable Interstitial...")
    core_data.reset_all()
    requests.post("http://127.0.0.1:8000/admin/maintenance/on")
    res_recovery = executor.run(artifact, inputs={"member_id": "1001"})

    recovery_log = [
        f"=== REPLAY LOG: RECOVERABLE CONDITION RECOVERY ===",
        f"TIMESTAMP:   {datetime.now(timezone.utc).isoformat()}",
        f"RUN ID:      {res_recovery.run_id}",
        f"STATUS:      {res_recovery.status.value.upper()}",
        f"OUTPUTS:     {json.dumps(res_recovery.outputs, indent=2)}",
        "STEP TRACES (Showing Dismissal Action):",
    ]
    for tr in res_recovery.step_traces:
        loc = f"[{tr.locator_resolution.strategy_used} candidate={tr.locator_resolution.candidate_index}]" if tr.locator_resolution else ""
        detail = f" - {tr.detail}" if tr.detail else ""
        recovery_log.append(f"  * {tr.step_id:<25} ({tr.duration_ms:>5.1f}ms) {loc}{detail}")
    recovery_log.append("=====================================================")

    with open("evidence/replay_interstitial_recovery.log", "w", encoding="utf-8") as f:
        f.write("\n".join(recovery_log) + "\n")
    print("Wrote evidence/replay_interstitial_recovery.log")
    requests.post("http://127.0.0.1:8000/admin/maintenance/off")

    # 6. Human Escalation Handoff Demonstration
    print("\n[5/5] Running Human Escalation Handoff on Live Session...")
    core_data.reset_all()
    esc_mgr = EscalationManager(
        run_id="run_evidence_handoff",
        session_id="sess_live_core",
        evidence_dir="evidence/screenshots"
    )

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto("http://127.0.0.1:8000/portal/sub-account/new?member_id=1001")

        req = esc_mgr.trigger_escalation(
            page=page,
            reason=EscalationReason.RISKY_STEP_APPROVAL,
            explanation="Policy Gate: Opening holiday club sub-account is classified as RISKY_IRREVERSIBLE.",
            goal="Open Holiday Club Sub-Account for Member 1001 with $50.00 deposit",
            capability_id="cap_open_sub_account",
            current_step_id="step_confirm_sub_account",
            proposed_action="Open HOLIDAY_CLUB sub-account for member 1001 ($50.00 deposit)",
        )

        def operator_takeover_action(intervention, live_page):
            # Operator operates the SAME live page
            live_page.select_option("#ctl00_MainContent_ddlProductType", value="HOLIDAY_CLUB")
            live_page.fill("#ctl00_MainContent_txtInitialDeposit", "50")
            live_page.click("#ctl00_MainContent_btnReview")
            return "Human operator configured sub-account product and navigated to review confirmation"

        esc_mgr.interactive_handler = operator_takeover_action
        final_state = esc_mgr.handle_operator_takeover(page=page, request=req)

        handoff_log = [
            f"=== HUMAN-IN-THE-LOOP ESCALATION & HANDOFF AUDIT TRAIL ===",
            f"TIMESTAMP:       {datetime.now(timezone.utc).isoformat()}",
            f"INCIDENT ID:     {req.id}",
            f"REASON:          {req.reason.value}",
            f"EXPLANATION:     {req.explanation}",
            f"PROPOSED ACTION: {req.proposed_action}",
            f"SCREENSHOT REF:  {req.screenshot_ref}",
            f"SESSION ID:      {final_state.session_id}",
            f"FINAL STATE:     {final_state.state.value}",
            "OPERATOR ACTIONS RECORDED ON LIVE SESSION:",
        ]
        for act in final_state.operator_actions:
            handoff_log.append(f"  * [{act.timestamp.isoformat()}] {act.description}")
        handoff_log.append("=========================================================")

        with open("evidence/replay_escalation_handoff.log", "w", encoding="utf-8") as f:
            f.write("\n".join(handoff_log) + "\n")
        print("Wrote evidence/replay_escalation_handoff.log")

        browser.close()

    print("\n[ALL EVIDENCE GENERATED SUCCESSFULLY IN /evidence/]")


if __name__ == "__main__":
    main()
