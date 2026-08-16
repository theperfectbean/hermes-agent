"""Unit tests for agent.finalization_gate validation and decision logic."""

import pytest
from agent.finalization_gate import (
    ACCEPT_FINAL,
    CONTINUE_RECHECK,
    CONTINUE_REVIEW,
    WITHHOLD_BLOCKED,
    WITHHOLD_UNVERIFIED,
    FinalizationDecision,
    evidence_metadata,
    fallback_text,
    start_hold_required,
    terminal_decision,
    validate_hook_output,
)


def test_start_hold_requires_explicit_directive_and_returns_owner():
    assert start_hold_required([]) == (False, None)
    assert start_hold_required([{"action": "accept"}]) == (False, None)
    assert start_hold_required([{"action": "hold", "owner": "test-gate"}]) == (True, "test-gate")
    assert start_hold_required([{"action": "hold"}]) == (True, "anonymous_gate")


def test_validate_hook_output_start_bounds():
    # Valid
    assert validate_hook_output({"action": "hold", "owner": "gate-1", "reason": "material"}, phase="start") == {
        "action": "hold",
        "owner": "gate-1",
        "reason": "material",
    }
    # Unexpected key
    assert validate_hook_output({"action": "hold", "extra": "payload"}, phase="start") is None
    # Invalid action
    assert validate_hook_output({"action": "destroy"}, phase="start") is None
    # Oversized reason
    assert validate_hook_output({"action": "hold", "reason": "x" * 241}, phase="start") is None
    # Non-mapping
    assert validate_hook_output("hold", phase="start") is None


def test_validate_hook_output_terminal_bounds():
    # Valid terminal
    valid = {
        "action": "accept",
        "owner": "gate-1",
        "reason": "observed evidence",
        "message": "continue message",
        "operational_status": "verified",
        "review_status": "passed",
        "evidence": [{"role": "tool", "tool_name": "terminal", "observed": True}],
    }
    assert validate_hook_output(valid, phase="terminal") == valid

    # Unexpected top-level key
    assert validate_hook_output({**valid, "unauthorized_key": True}, phase="terminal") is None

    # Oversized string fields
    assert validate_hook_output({**valid, "reason": "a" * 241}, phase="terminal") is None
    assert validate_hook_output({**valid, "message": "b" * 2001}, phase="terminal") is None
    assert validate_hook_output({**valid, "review_status": "c" * 81}, phase="terminal") is None
    assert validate_hook_output({**valid, "owner": "d" * 81}, phase="terminal") is None

    # Evidence bounds: max 16 items
    oversized_evidence = [{"tool_name": f"t{i}", "observed": True} for i in range(17)]
    assert validate_hook_output({**valid, "evidence": oversized_evidence}, phase="terminal") is None

    # Unexpected evidence key
    assert validate_hook_output({**valid, "evidence": [{"arbitrary_key": "val"}]}, phase="terminal") is None

    # Nested structure in evidence
    assert validate_hook_output({**valid, "evidence": [{"tool_name": "terminal", "nested": {"a": 1}}]}, phase="terminal") is None
    assert validate_hook_output({**valid, "evidence": [{"tool_name": "terminal", "nested_list": [1, 2]}]}, phase="terminal") is None


def test_terminal_invalid_or_missing_output_fails_closed():
    decision = terminal_decision([{"action": "unexpected"}], expected_owner="my-gate")
    assert decision.action == WITHHOLD_UNVERIFIED
    assert decision.operational_status == "unverified"
    assert "not been claimed" in fallback_text(decision)


def test_terminal_accept_requires_non_empty_evidence():
    # Accept with empty evidence must fail closed to withhold_unverified
    decision = terminal_decision([{
        "action": ACCEPT_FINAL,
        "owner": "my-gate",
        "reason": "claim without evidence",
        "evidence": [],
    }], expected_owner="my-gate")
    assert decision.action == WITHHOLD_UNVERIFIED
    assert decision.operational_status == "unverified"
    assert "minimum evidence contract" in decision.reason

    # Accept with valid evidence succeeds
    valid_decision = terminal_decision([{
        "action": ACCEPT_FINAL,
        "owner": "my-gate",
        "reason": "verified with evidence",
        "evidence": [{"kind": "read-back", "resource": "service", "observed": True}],
    }], expected_owner="my-gate")
    assert valid_decision.accepted is True
    assert valid_decision.operational_status == "verified"
    assert evidence_metadata(valid_decision)["evidence"] == [
        {"kind": "read-back", "resource": "service", "observed": True}
    ]


def test_competing_hook_cannot_release_another_gate():
    # Gate armed by "gate-alpha"
    # Unrelated hook "gate-beta" returns accept
    competing_results = [{
        "action": ACCEPT_FINAL,
        "owner": "gate-beta",
        "evidence": [{"tool_name": "terminal", "observed": True}],
    }]
    decision = terminal_decision(competing_results, expected_owner="gate-alpha")
    assert decision.action == WITHHOLD_UNVERIFIED
    assert decision.operational_status == "unverified"
    assert decision.accepted is False


def test_continue_and_blocked_directives_preserve_status():
    continue_decision = terminal_decision([{
        "action": CONTINUE_RECHECK,
        "owner": "my-gate",
        "message": "Run a fresh read-back.",
    }], expected_owner="my-gate")
    blocked_decision = terminal_decision([{
        "action": WITHHOLD_BLOCKED,
        "owner": "my-gate",
        "reason": "operator conflict",
    }], expected_owner="my-gate")

    assert continue_decision.continue_required is True
    assert continue_decision.operational_status == "unverified"
    assert continue_decision.message == "Run a fresh read-back."

    assert blocked_decision.operational_status == "blocked"
    assert "blocked pending an operator decision" in fallback_text(blocked_decision)


def test_finalizer_removes_held_candidate_scaffolding():
    from agent.turn_finalizer import _drop_safety_finalization_scaffolding

    messages = [
        {"role": "user", "content": "verify"},
        {"role": "assistant", "content": "provisional candidate", "_safety_finalization_candidate": True},
        {"role": "assistant", "content": "verified result"},
    ]
    _drop_safety_finalization_scaffolding(messages)
    assert messages == [
        {"role": "user", "content": "verify"},
        {"role": "assistant", "content": "verified result"},
    ]
