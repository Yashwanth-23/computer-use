import os
import json
import pytest
import requests
from src.agent.discovery_loop import DiscoveryAgent
from src.schemas.artifact import CapabilityArtifact
from src.schemas.execution import ReplayStatus
from src.engine.replay_executor import ReplayExecutor


def test_discovery_agent_end_to_end():
    """Verify that DiscoveryAgent runs the observe->decide->act loop and outputs a valid CapabilityArtifact."""
    agent = DiscoveryAgent(headless=True, log_file="evidence/discovery_run.log")
    
    artifact = agent.discover(
        goal="Look up member 1001 and read savings and checking balances",
        target_url="http://127.0.0.1:8000/portal/member-lookup",
        capability_name="discovered_member_lookup",
        max_steps=5,
    )
    
    assert isinstance(artifact, CapabilityArtifact)
    assert artifact.metadata.name == "discovered_member_lookup"
    assert len(artifact.steps) >= 3
    assert len(artifact.output_parameters) >= 2
    assert os.path.exists("evidence/discovery_run.log")
    
    # Save the discovered artifact into evidence/
    with open("evidence/capability_member_lookup.json", "w", encoding="utf-8") as f:
        f.write(artifact.model_dump_json(indent=2))
        
    assert os.path.exists("evidence/capability_member_lookup.json")


def test_replay_discovered_artifact():
    """Verify that the artifact produced by DiscoveryAgent can be replayed deterministically."""
    with open("evidence/capability_member_lookup.json", "r", encoding="utf-8") as f:
        artifact = CapabilityArtifact.model_validate_json(f.read())
        
    executor = ReplayExecutor(headless=True)
    
    # Replay 1001
    res1 = executor.run(artifact, inputs={"member_id": "1001"})
    assert res1.status == ReplayStatus.SUCCESS
    assert res1.outputs["savings_balance"] == 24500.0
    assert res1.outputs["checking_balance"] == 4120.0
    
    # Replay 9999 (Business Outcome: Member Not Found)
    res2 = executor.run(artifact, inputs={"member_id": "9999"})
    assert res2.status == ReplayStatus.BUSINESS_OUTCOME
    assert res2.business_outcome.outcome_code == "MEMBER_NOT_FOUND"
