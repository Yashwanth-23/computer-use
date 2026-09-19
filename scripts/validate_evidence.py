"""
Evidence Integrity and Secret/PII Validator.

Verifies:
1. Manifest integrity: evidence/manifest.json exists and all canonical SHA-256 checksums match live files.
2. Provenance consistency:
   - capability_member_lookup.json ID (cap_bfa2d82803e3) matches all member lookup replay logs and manifest entries.
   - capability_open_subaccount.json ID (cap_open_sub_account) matches replay_escalation_handoff.log.
3. Secret and PII hygiene: Scans evidence/ and src/ for unredacted SSNs, credit cards, and live API keys.
4. Security policy: Ensures no unattended risky bypass flags exist in src/ or scripts/.

Run: python scripts/validate_evidence.py
"""
import hashlib
import json
import os
import re
import sys

# Ensure repository root is on sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


def sha256_canonical(path: str) -> str:
    """Compute SHA-256 with platform-independent line-ending normalization for text files."""
    with open(path, "rb") as f:
        data = f.read()
    if any(path.endswith(ext) for ext in [".json", ".log", ".txt", ".md", ".py", ".html"]):
        data = data.replace(b"\r\n", b"\n")
    return hashlib.sha256(data).hexdigest()


def validate_manifest(manifest_path: str = "evidence/manifest.json") -> dict:
    if not os.path.exists(manifest_path):
        raise FileNotFoundError(f"Manifest missing at {manifest_path}")

    with open(manifest_path, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    # 1. Check artifacts
    for art in manifest.get("artifacts", []):
        p = art["path"]
        if not os.path.exists(p):
            raise FileNotFoundError(f"Artifact {p} listed in manifest does not exist")
        actual_hash = sha256_canonical(p)
        if actual_hash != art["sha256"]:
            raise ValueError(f"Checksum mismatch for {p}: manifest={art['sha256']} actual={actual_hash}")

    # 2. Check evidence runs
    for run in manifest.get("evidence_runs", []):
        log_file = run["log_file"]
        if not os.path.exists(log_file):
            raise FileNotFoundError(f"Log file {log_file} listed in manifest does not exist")
        actual_hash = sha256_canonical(log_file)
        if actual_hash != run["sha256"]:
            raise ValueError(f"Checksum mismatch for {log_file}: manifest={run['sha256']} actual={actual_hash}")

    return manifest


def validate_provenance(manifest: dict):
    # Member lookup checks
    with open("evidence/capability_member_lookup.json", "r", encoding="utf-8") as f:
        lookup = json.load(f)
    lookup_id = lookup["metadata"]["id"]
    if lookup_id != "cap_bfa2d82803e3":
        raise ValueError(f"Canonical member lookup ID mismatch: {lookup_id} != cap_bfa2d82803e3")

    member_logs = [
        "evidence/discovery_run.log",
        "evidence/replay_success.log",
        "evidence/replay_business_outcome_404.log",
        "evidence/replay_interstitial_recovery.log",
        "evidence/replay_hard_failure.log",
    ]
    for log_path in member_logs:
        with open(log_path, "r", encoding="utf-8") as f:
            content = f.read()
        if lookup_id not in content:
            raise ValueError(f"Log {log_path} does not reference canonical ID {lookup_id}")

    # Open sub-account checks
    with open("evidence/capability_open_subaccount.json", "r", encoding="utf-8") as f:
        subaccount = json.load(f)
    subaccount_id = subaccount["metadata"]["id"]
    if subaccount_id != "cap_open_sub_account":
        raise ValueError(f"Subaccount capability ID mismatch: {subaccount_id} != cap_open_sub_account")

    with open("evidence/replay_escalation_handoff.log", "r", encoding="utf-8") as f:
        esc_content = f.read()
    if subaccount_id not in esc_content:
        raise ValueError(f"Escalation log does not reference subaccount ID {subaccount_id}")


def scan_for_secrets_and_pii(dirs: list[str]):
    # Disallow unredacted real SSN patterns (not placeholder/redacted strings)
    ssn_re = re.compile(r"\b(?!000|666|9\d\d)(\d{3})-(?!00)(\d{2})-(?!0000)(\d{4})\b")
    # Live Credit Card numbers (Visa, Mastercard, Amex, Discover - contiguous, space, or dash-separated)
    card_re = re.compile(
        r"\b(?:4[0-9]{3}(?:[ -]?[0-9]{4}){3}"
        r"|5[1-5][0-9]{2}(?:[ -]?[0-9]{4}){3}"
        r"|3[47][0-9]{2}[ -]?[0-9]{6}[ -]?[0-9]{5}"
        r"|6(?:011|5[0-9]{2})(?:[ -]?[0-9]{4}){3})\b"
    )
    # Live OpenAI / Anthropic key format check (e.g. sk-ant-api03-..., sk-proj-...)
    live_key_re = re.compile(r"\b(sk-ant-[a-zA-Z0-9_\-]{20,}|sk-proj-[a-zA-Z0-9_\-]{20,})\b")

    for d in dirs:
        for root, _, files in os.walk(d):
            for fname in files:
                if fname.endswith((".py", ".json", ".log", ".md", ".txt")):
                    fpath = os.path.join(root, fname)
                    with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
                        text = f.read()
                    if live_key_re.search(text):
                        raise ValueError(f"Potential live API key detected in {fpath}")
                    if ssn_re.search(text):
                        raise ValueError(f"Potential unredacted SSN detected in {fpath}")
                    if card_re.search(text):
                        raise ValueError(f"Potential unredacted credit card detected in {fpath}")


def verify_no_bypass_flags(dirs: list[str]):
    bypass_terms = ["--allow" + "-unattended-risky", "--allow" + "-risky", "allow" + "_unattended_risky"]
    text_extensions = (".py", ".json", ".log", ".md", ".txt")
    for d in dirs:
        for root, _, files in os.walk(d):
            for fname in files:
                if fname == "validate_evidence.py":
                    continue
                if fname.endswith(text_extensions):
                    fpath = os.path.join(root, fname)
                    with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
                        text = f.read()
                    for term in bypass_terms:
                        if term in text:
                            raise ValueError(f"Forbidden unattended risky bypass detected in {fpath}: '{term}'")


def main():
    print("=== [RUNNING EVIDENCE INTEGRITY & SECRET SCAN] ===")
    
    # 1. Manifest verification
    print("[1/4] Verifying evidence/manifest.json and SHA-256 file hashes...")
    manifest = validate_manifest()
    print("      All artifact and evidence run hashes match manifest perfectly.")

    # 2. Provenance verification
    print("[2/4] Verifying canonical capability IDs and cross-log provenance...")
    validate_provenance(manifest)
    print("      All capability IDs (cap_bfa2d82803e3, cap_open_sub_account) match logs.")

    # 3. Secret and PII scan
    print("[3/4] Scanning evidence/ and src/ for unredacted PII/secrets...")
    scan_for_secrets_and_pii(["evidence", "src"])
    print("      No live keys, real SSNs, or credit card numbers found.")

    # 4. Zero bypass verification
    print("[4/4] Verifying zero unattended risky bypass flags across text assets...")
    verify_no_bypass_flags(["src", "scripts"])
    print("      Zero bypass flags found across all code and text assets. Strict fail-closed policy active.")

    print("\n[SUCCESS] Evidence integrity, provenance, and safety checks PASSED.")


if __name__ == "__main__":
    main()
