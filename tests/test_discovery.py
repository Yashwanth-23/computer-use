import os
import json
import pytest
import requests
from src.agent.discovery_loop import DiscoveryAgent
from src.schemas.artifact import CapabilityArtifact
from src.schemas.execution import ReplayStatus
from src.engine.replay_executor import ReplayExecutor


def test_discovery_agent_end_to_end(tmp_path):
    """Verify that DiscoveryAgent runs the observe->decide->act loop and outputs a valid CapabilityArtifact.
    
    CRITICAL: Writes strictly to tmp_path to protect canonical evidence from being overwritten.
    """
    test_log = str(tmp_path / "test_discovery.log")
    test_artifact_path = str(tmp_path / "test_capability.json")
    
    agent = DiscoveryAgent(headless=True, log_file=test_log)
    
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
    assert os.path.exists(test_log)
    
    # Save the discovered artifact into tmp_path
    with open(test_artifact_path, "w", encoding="utf-8") as f:
        f.write(artifact.model_dump_json(indent=2))
        
    assert os.path.exists(test_artifact_path)

    # Replay discovered artifact directly from tmp_path
    executor = ReplayExecutor(headless=True, evidence_dir=str(tmp_path))
    
    # Replay 1001
    res1 = executor.run(artifact, inputs={"member_id": "1001"})
    assert res1.status == ReplayStatus.SUCCESS
    assert res1.outputs["savings_balance"] == 24500.0
    assert res1.outputs["checking_balance"] == 4120.0
    
    # Replay 9999 (Business Outcome: Member Not Found)
    res2 = executor.run(artifact, inputs={"member_id": "9999"})
    assert res2.status == ReplayStatus.BUSINESS_OUTCOME
    assert res2.business_outcome.outcome_code == "MEMBER_NOT_FOUND"


def test_discovery_timeout_detection(tmp_path):
    """Verify that DiscoveryAgent halts when wall-clock deadline is exceeded."""
    test_log = str(tmp_path / "test_timeout.log")
    agent = DiscoveryAgent(headless=True, log_file=test_log)
    
    with pytest.raises(TimeoutError, match="Discovery exceeded wall-clock deadline"):
        agent.discover(
            goal="Look up member 1001",
            target_url="http://127.0.0.1:8000/portal/member-lookup",
            max_duration_seconds=0.0001,
        )


def test_discovery_risk_classification():
    """Verify that semantic risk classification marks state commits as RISKY_IRREVERSIBLE."""
    from src.agent.artifact_compiler import classify_step_risk
    from src.schemas.artifact import ActionType, RiskLevel

    # 1. Search button click is SAFE
    risk1, reason1 = classify_step_risk(
        action_type=ActionType.CLICK,
        element={"element_id": "ctl00_MainContent_btnSearch", "text_content": "Search Record"}
    )
    assert risk1 == RiskLevel.SAFE
    assert reason1 is None

    # 2. Confirm sub-account button click is RISKY_IRREVERSIBLE
    risk2, reason2 = classify_step_risk(
        action_type=ActionType.CLICK,
        element={"element_id": "ctl00_MainContent_btnConfirm", "text_content": "Confirm Sub-Account Opening"},
        target_url="http://127.0.0.1:8000/portal/sub-account/confirm"
    )
    assert risk2 == RiskLevel.RISKY_IRREVERSIBLE
    assert reason2 is not None
    assert "Irreversible state modification" in reason2



def test_openai_compatible_client_structure(monkeypatch):
    """Verify OpenAICompatibleClient parses responses and factory detects keys."""
    from unittest.mock import MagicMock
    from src.agent.llm_client import OpenAICompatibleClient, get_llm_client

    client = OpenAICompatibleClient(api_key="mock-key", base_url="https://mock.api/v1", model="gpt-4o")
    assert client.base_url == "https://mock.api/v1"
    assert client.model == "gpt-4o"

    # Mock response
    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "choices": [{"message": {"content": '{"thought": "test", "action": "CLICK", "element_index": 2}'}}],
        "usage": {"prompt_tokens": 100, "completion_tokens": 20}
    }
    mock_resp.raise_for_status = MagicMock()

    mock_requests = MagicMock()
    mock_requests.post.return_value = mock_resp
    client._requests = mock_requests

    decision = client.decide_next_action("sys", "user")
    assert decision["action"] == "CLICK"
    assert decision["element_index"] == 2
    assert decision["_usage"]["input_tokens"] == 100

    # Test factory detection for Kimi/OpenAI
    monkeypatch.setenv("OPENAI_API_KEY", "sk-mock-openai")
    inst = get_llm_client(provider="openai")
    assert isinstance(inst, OpenAICompatibleClient)

