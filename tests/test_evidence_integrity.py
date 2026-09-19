"""
Unit test for evidence suite integrity, cryptographic manifest, and safety policy.
"""
from scripts.validate_evidence import (
    validate_manifest,
    validate_provenance,
    scan_for_secrets_and_pii,
    verify_no_bypass_flags,
)


def test_manifest_and_evidence_integrity():
    """Verify manifest exists and every file SHA-256 matches."""
    manifest = validate_manifest("evidence/manifest.json")
    assert manifest["manifest_version"] == "1.0"
    assert len(manifest["artifacts"]) == 2
    assert len(manifest["evidence_runs"]) >= 5


def test_capability_provenance():
    """Verify canonical IDs match logs across the evidence suite."""
    manifest = validate_manifest("evidence/manifest.json")
    validate_provenance(manifest)


def test_zero_bypass_flags_in_codebase():
    """Verify that no unattended risk bypass exists in src/ or scripts/."""
    verify_no_bypass_flags(["src", "scripts"])


def test_no_unredacted_secrets_in_evidence():
    """Scan evidence/ and src/ for exposed secrets/PII."""
    scan_for_secrets_and_pii(["evidence", "src"])
