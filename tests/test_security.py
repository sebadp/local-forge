import hashlib
import hmac
import json

from app.security.audit import AuditTrail
from app.security.models import PolicyAction
from app.security.policy_engine import PolicyEngine


def test_policy_engine_regex(tmp_path):
    policy_file = tmp_path / "policies.yaml"
    policy_file.write_text(r"""
version: "1.0"
default_action: "allow"
rules:
  - id: "block_rm_rf"
    target_tool: "run_command"
    argument_match:
      CommandLine: '(?i).*rm\s+-r.*\s+/(?!Users|tmp|data).*'
    action: "block"
    reason: "Blocked"
  - id: "flag_sudo"
    target_tool: "run_command"
    argument_match:
      CommandLine: '(?i).*sudo\s+.*'
    action: "flag"
    reason: "Flagged"
""")

    engine = PolicyEngine(policy_file)

    # Test 1: Destructive rm -rf / outside allowed dirs
    decision1 = engine.evaluate("run_command", {"CommandLine": "rm -rf /etc/hosts"})
    assert decision1.action == PolicyAction.BLOCK

    # Test 2: Destructive rm -rf but inside allowed /tmp and /data
    decision2 = engine.evaluate("run_command", {"CommandLine": "rm -rf /tmp/test"})
    assert decision2.action == PolicyAction.ALLOW

    decision3 = engine.evaluate("run_command", {"CommandLine": "rm -rf /data/memory/snapshots"})
    assert decision3.action == PolicyAction.ALLOW

    # Test 4: Sudo usage
    decision4 = engine.evaluate("run_command", {"CommandLine": "sudo apt update"})
    assert decision4.action == PolicyAction.FLAG


def test_audit_trail_hashing(tmp_path):
    log_file = tmp_path / "audit.jsonl"
    audit = AuditTrail(log_file)

    entry1 = audit.record("test_tool", {"arg": 1}, "allow", "test reason", "success")
    assert (
        entry1.previous_hash == "0000000000000000000000000000000000000000000000000000000000000000"
    )

    entry2 = audit.record("test_tool", {"arg": 2}, "block", "denied", "failure")
    assert entry2.previous_hash == entry1.entry_hash

    # Verify file contents
    content = log_file.read_text().strip().splitlines()
    assert len(content) == 2
    data2 = json.loads(content[1])
    assert data2["previous_hash"] == entry1.entry_hash


def test_policy_engine_missing_file_defaults_to_block(tmp_path):
    """PolicyEngine must fail-secure (BLOCK) when the policy file does not exist."""
    engine = PolicyEngine(tmp_path / "nonexistent.yaml")
    decision = engine.evaluate("run_command", {"command": "pytest tests/"})
    assert decision.action == PolicyAction.BLOCK


def test_policy_engine_existing_file_default_allow_unchanged(tmp_path):
    """A policy file with default_action=allow still works as before."""
    policy_file = tmp_path / "policies.yaml"
    policy_file.write_text('version: "1.0"\ndefault_action: "allow"\nrules: []\n')
    engine = PolicyEngine(policy_file)
    decision = engine.evaluate("some_tool", {})
    assert decision.action == PolicyAction.ALLOW


def test_audit_trail_hmac(tmp_path):
    """When hmac_key is set, entry_hash uses HMAC-SHA256."""
    key = "test-secret-key"
    log_file = tmp_path / "audit_hmac.jsonl"
    audit = AuditTrail(log_file, hmac_key=key)

    entry = audit.record("tool", {"x": 1}, "allow", "reason", "ok")

    payload = {
        "timestamp": entry.timestamp,
        "tool_name": "tool",
        "arguments": {"x": 1},
        "decision": "allow",
        "decision_reason": "reason",
        "execution_result": "ok",
        "previous_hash": entry.previous_hash,
    }
    expected = hmac.new(
        key.encode(), json.dumps(payload, sort_keys=True).encode(), hashlib.sha256
    ).hexdigest()
    assert entry.entry_hash == expected


def test_audit_trail_no_key_backward_compat(tmp_path):
    """Without hmac_key, AuditTrail behaves identically to before (plain SHA-256 chain)."""
    log_file = tmp_path / "audit_plain.jsonl"
    audit = AuditTrail(log_file)
    entry1 = audit.record("t", {}, "allow", None, None)
    entry2 = audit.record("t", {}, "allow", None, None)
    assert entry2.previous_hash == entry1.entry_hash
    assert (
        entry1.previous_hash == "0000000000000000000000000000000000000000000000000000000000000000"
    )


def test_audit_trail_hmac_chain_links(tmp_path):
    """HMAC chain: each entry's previous_hash matches prior entry's entry_hash."""
    log_file = tmp_path / "audit_chain.jsonl"
    audit = AuditTrail(log_file, hmac_key="chain-key")
    e1 = audit.record("t1", {}, "allow", None, None)
    e2 = audit.record("t2", {}, "block", None, None)
    assert e2.previous_hash == e1.entry_hash
