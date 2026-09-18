import sys
import json
import time
import socket
import argparse
import threading
from typing import Optional
import uvicorn

from mock_target.app import app
from mock_target import core_data
from src.schemas.artifact import CapabilityArtifact
from src.schemas.execution import ReplayStatus
from src.engine.replay_executor import ReplayExecutor


def is_port_open(host: str = "127.0.0.1", port: int = 8000) -> bool:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(0.5)
    try:
        res = s.connect_ex((host, port))
        return res == 0
    finally:
        s.close()


def ensure_mock_server(port: int = 8000):
    if not is_port_open("127.0.0.1", port):
        server_config = uvicorn.Config(app=app, host="127.0.0.1", port=port, log_level="error")
        server = uvicorn.Server(server_config)
        t = threading.Thread(target=server.run, daemon=True)
        t.start()
        for _ in range(30):
            if is_port_open("127.0.0.1", port):
                break
            time.sleep(0.1)


def main():
    parser = argparse.ArgumentParser(description="Run deterministic replay for a bank member.")
    parser.add_argument("--member", type=str, default="1001", help="Member ID to query (e.g. 1001 or 9999)")
    parser.add_argument("--artifact", type=str, default="evidence/capability_member_lookup.json", help="Path to capability artifact")
    parser.add_argument("--headless", action="store_true", default=True, help="Run browser headlessly")
    parser.add_argument("--headed", action="store_false", dest="headless", help="Run browser in visible window")
    args = parser.parse_args()

    ensure_mock_server()

    with open(args.artifact, "r", encoding="utf-8") as f:
        artifact = CapabilityArtifact.model_validate_json(f.read())

    executor = ReplayExecutor(headless=args.headless, evidence_dir="evidence")
    result = executor.run(artifact, inputs={"member_id": args.member})

    print("\n" + "=" * 60)
    print(f"REPLAY RESULT: {result.status.value.upper()}")
    print("=" * 60)
    print(f"Run ID:      {result.run_id}")
    print(f"Capability:  {result.capability_id} (v{result.capability_version})")

    if result.status in {ReplayStatus.SUCCESS, ReplayStatus.RECOVERED}:
        print(f"Outputs:     {json.dumps(result.outputs, indent=2)}")
    elif result.status == ReplayStatus.BUSINESS_OUTCOME:
        print(f"Outcome:     [{result.business_outcome.outcome_code}]")
        print(f"Message:     {result.business_outcome.message}")
    elif result.status == ReplayStatus.HARD_FAILURE:
        print(f"Failed Step: {result.debug.failed_step_id}")
        print(f"Expected:    {result.debug.expected}")
        print(f"Observed:    {result.debug.observed}")
        print(f"Evidence:    {result.debug.evidence_ref}")

    print("-" * 60)
    print(f"Executed {len(result.step_traces)} steps:")
    for trace in result.step_traces:
        loc_str = f"[{trace.locator_resolution.strategy_used} index={trace.locator_resolution.candidate_index}]" if trace.locator_resolution else ""
        detail = f" - {trace.detail}" if trace.detail else ""
        print(f"  * {trace.step_id:<25} ({trace.duration_ms:>6.1f}ms) {loc_str}{detail}")
    print("=" * 60)


if __name__ == "__main__":
    main()
