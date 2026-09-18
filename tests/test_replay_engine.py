import time
import pytest
import requests

from mock_target import core_data
from src.schemas.artifact import CapabilityArtifact
from src.schemas.execution import ReplayStatus
from src.schemas.example_member_lookup import build_artifact
from src.engine.replay_executor import ReplayExecutor


def test_deterministic_replay_happy_path():
    """Replay against existing member 1001 -> success with typed extracted outputs."""
    core_data.reset_all()
    artifact = build_artifact()
    executor = ReplayExecutor(headless=True)

    result = executor.run(artifact, inputs={"member_id": "1001"})

    assert result.status == ReplayStatus.SUCCESS
    assert result.outputs is not None
    assert result.outputs["savings_balance"] == 24500.0
    assert result.outputs["checking_balance"] == 4120.0
    assert len(result.step_traces) > 0


def test_deterministic_replay_business_outcome_not_found():
    """Replay against non-existent member 9999 -> structured BUSINESS_OUTCOME result, not a crash."""
    core_data.reset_all()
    artifact = build_artifact()
    executor = ReplayExecutor(headless=True)

    result = executor.run(artifact, inputs={"member_id": "9999"})

    assert result.status == ReplayStatus.BUSINESS_OUTCOME
    assert result.business_outcome is not None
    assert result.business_outcome.outcome_code == "MEMBER_NOT_FOUND"
    assert "Not Found" in result.business_outcome.message
    assert result.outputs is None


def test_deterministic_replay_interstitial_recovery():
    """Replay with maintenance interstitial active -> recovers by dismissing popup and completes."""
    core_data.reset_all()
    requests.post("http://127.0.0.1:8000/admin/maintenance/on")
    artifact = build_artifact()
    executor = ReplayExecutor(headless=True)

    result = executor.run(artifact, inputs={"member_id": "1001"})
    assert result.status == ReplayStatus.RECOVERED
    assert result.outputs["savings_balance"] == 24500.0
    # Confirm trace shows interstitial was dismissed
    assert any(trace.detail and "Dismissed" in trace.detail for trace in result.step_traces)
    requests.post("http://127.0.0.1:8000/admin/maintenance/off")
