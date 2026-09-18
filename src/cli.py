import sys
import json
import argparse
import uvicorn
from typing import Optional

from src.schemas.artifact import CapabilityArtifact
from src.schemas.execution import ReplayStatus
from src.engine.replay_executor import ReplayExecutor
from src.agent.discovery_loop import DiscoveryAgent


def cmd_serve_mock(args):
    """Run the legacy mock banking application."""
    from mock_target.app import app
    print(f"Starting ApexCore Mock Banking Portal on http://127.0.0.1:{args.port}...")
    uvicorn.run(app, host="127.0.0.1", port=args.port)


def cmd_discover(args):
    """Run LLM-driven discovery against a live target surface."""
    print(f"=== Starting Goal-Driven Discovery Loop ===")
    print(f"Goal:       {args.goal}")
    print(f"Target URL: {args.target}")
    print(f"Provider:   {args.provider or 'auto-detect (Gemini/Anthropic/Simulated)'}")
    print(f"Headless:   {not args.headed}")

    agent = DiscoveryAgent(headless=(not args.headed), log_file=args.log)
    artifact = agent.discover(
        goal=args.goal,
        target_url=args.target,
        capability_name=args.name,
        max_steps=args.max_steps,
        provider=args.provider,
    )

    with open(args.output, "w", encoding="utf-8") as f:
        f.write(artifact.model_dump_json(indent=2))

    print(f"\n[SUCCESS] Discovered capability compiled and saved to: {args.output}")
    print(f"Artifact ID: {artifact.metadata.id} | Steps: {len(artifact.steps)} | Outputs: {len(artifact.output_parameters)}")


def cmd_replay(args):
    """Replay a saved CapabilityArtifact deterministically without LLM in the loop."""
    print(f"=== Starting Deterministic Replay Engine ===")
    print(f"Artifact:   {args.artifact}")
    print(f"Inputs:     {args.input}")
    print(f"Headless:   {not args.headed}")

    with open(args.artifact, "r", encoding="utf-8") as f:
        artifact = CapabilityArtifact.model_validate_json(f.read())

    inputs = json.loads(args.input) if args.input else {}
    executor = ReplayExecutor(
        headless=(not args.headed),
        allow_unattended_risky=args.allow_risky,
        evidence_dir=args.evidence_dir,
    )

    result = executor.run(
        artifact=artifact,
        inputs=inputs,
        interactive_escalation=args.interactive,
    )

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


def main():
    parser = argparse.ArgumentParser(description="interface.ai Computer-Use Automation System")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # serve-mock
    p_mock = subparsers.add_parser("serve-mock", help="Start the mock legacy banking portal")
    p_mock.add_argument("--port", type=int, default=8000, help="Port to serve on (default: 8000)")
    p_mock.set_defaults(func=cmd_serve_mock)

    # discover
    p_disc = subparsers.add_parser("discover", help="Run goal-driven LLM discovery loop against a live surface")
    p_disc.add_argument("--goal", type=str, default="Look up member 1001 and read savings and checking balances")
    p_disc.add_argument("--target", type=str, default="http://127.0.0.1:8000/portal/member-lookup")
    p_disc.add_argument("--name", type=str, default="lookup_member_balance")
    p_disc.add_argument("--output", type=str, default="evidence/capability_member_lookup.json")
    p_disc.add_argument("--log", type=str, default="evidence/discovery_run.log")
    p_disc.add_argument("--provider", type=str, choices=["gemini", "anthropic", "simulated"], default=None)
    p_disc.add_argument("--max-steps", type=int, default=8)
    p_disc.add_argument("--headed", action="store_true", help="Launch browser in headed mode")
    p_disc.set_defaults(func=cmd_discover)

    # replay
    p_rep = subparsers.add_parser("replay", help="Deterministically replay a saved capability artifact")
    p_rep.add_argument("--artifact", type=str, default="evidence/capability_member_lookup.json")
    p_rep.add_argument("--input", type=str, default='{"member_id": "1001"}')
    p_rep.add_argument("--evidence-dir", type=str, default="evidence")
    p_rep.add_argument("--allow-risky", action="store_true", help="Authorize unattended execution of risky steps")
    p_rep.add_argument("--interactive", action="store_true", help="Prompt operator interactively during escalation")
    p_rep.add_argument("--headed", action="store_true", help="Launch browser in headed mode")
    p_rep.set_defaults(func=cmd_replay)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
