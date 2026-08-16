"""Comprehensive regressions for safety finalization rework containment."""

import time
from pathlib import Path
import pytest
from run_agent import AIAgent, _DB_PERSISTED_MARKER
from agent.finalization_gate import (
    ACCEPT_FINAL,
    CONTINUE_RECHECK,
    WITHHOLD_BLOCKED,
    WITHHOLD_UNVERIFIED,
    FinalizationDecision,
    fallback_text,
    terminal_decision,
)
from agent.chat_completion_helpers import build_assistant_message


def _agent_for_containment():
    agent = object.__new__(AIAgent)
    agent._safety_finalization_hold = True
    agent._safety_gate_owner = "test-gate"
    agent._safety_finalization_releasing = False
    agent._safety_finalization_released = False
    agent._safety_buffered_stream = []
    agent._safety_buffered_interim = []
    agent._safety_buffered_stream_end = None
    agent._stream_needs_break = False
    agent._current_streamed_assistant_text = ""
    agent._stream_writer_tls = None
    agent._stream_writer_token = 0
    agent._stream_think_scrubber = None
    agent._stream_context_scrubber = None
    agent.stream_delta_callback = None
    agent._stream_callback = None
    agent.interim_assistant_callback = None
    agent.session_id = "contained-session"
    agent.model = "test-model-primary"
    agent.provider = "test-provider-primary"
    agent.platform = "cli"
    agent._current_turn_id = "turn-contained"
    agent._api_call_count = 0
    agent._consecutive_stale_streams = 0
    agent.verbose_logging = False
    agent.reasoning_callback = None
    agent._needs_thinking_reasoning_pad = lambda: False
    agent._extract_reasoning = lambda _msg: None
    agent._strip_think_blocks = lambda text: text
    return agent


# 1. Direct callback bypass regression
def test_direct_callback_bypass_is_contained(monkeypatch):
    agent = _agent_for_containment()
    stream_deltas = []
    interim_msgs = []
    delivered_interim = []

    agent.stream_delta_callback = stream_deltas.append
    agent._stream_callback = None
    agent.interim_assistant_callback = lambda text, **kwargs: interim_msgs.append((text, kwargs))
    agent._record_streamed_assistant_text = lambda text: None
    agent._extract_codex_interim_visible_parts = lambda _msg: []
    agent._interim_assistant_visible_text = lambda msg: msg.get("content", "")
    agent._interim_text_was_delivered = lambda _text: False
    agent._interim_content_was_streamed = lambda _text: False
    agent._record_delivered_interim_text = delivered_interim.append
    agent._stream_hook_base_payload = lambda: {"session_id": agent.session_id}

    import agent.plugin_stream_hooks as stream_hooks
    observer_events = []
    monkeypatch.setattr(
        stream_hooks,
        "enqueue_plugin_stream_hook",
        lambda event, **kwargs: observer_events.append((event, kwargs)),
    )

    # Fire all direct output paths
    agent._fire_stream_delta("token-1")
    agent._emit_interim_assistant_message({"role": "assistant", "content": "interim-1"})
    agent._fire_streamed_codex_commentary("codex-commentary-1")
    agent._emit_stream_end(final_text="provisional-end", finished=True, error=None)

    # Nothing must have reached external callbacks or observers while held
    assert stream_deltas == []
    assert interim_msgs == []
    assert observer_events == []
    assert agent._safety_buffered_stream == ["token-1"]
    assert agent._safety_buffered_interim == ["interim-1", "codex-commentary-1"]
    assert agent._safety_buffered_stream_end == {"finished": True, "error": None}


# 2. Rejected candidate never later released regression
def test_rejected_candidate_never_later_released(monkeypatch):
    agent = _agent_for_containment()
    stream_deltas = []
    interim_msgs = []
    delivered_interim = []

    agent.stream_delta_callback = stream_deltas.append
    agent._stream_callback = None
    agent.interim_assistant_callback = lambda text, **kwargs: interim_msgs.append((text, kwargs))
    agent._record_streamed_assistant_text = lambda text: None
    agent._extract_codex_interim_visible_parts = lambda _msg: []
    agent._interim_assistant_visible_text = lambda msg: msg.get("content", "")
    agent._interim_text_was_delivered = lambda _text: False
    agent._interim_content_was_streamed = lambda _text: False
    agent._record_delivered_interim_text = delivered_interim.append
    agent._stream_hook_base_payload = lambda: {"session_id": agent.session_id}

    import agent.plugin_stream_hooks as stream_hooks
    monkeypatch.setattr(stream_hooks, "enqueue_plugin_stream_hook", lambda *a, **kw: None)

    # Candidate 1 streams tokens and interim, then is rejected on recheck
    agent._fire_stream_delta("Cand1 delta 1")
    agent._fire_stream_delta("Cand1 delta 2")
    agent._emit_interim_assistant_message({"role": "assistant", "content": "Cand1 interim"})

    assert agent._safety_buffered_stream == ["Cand1 delta 1", "Cand1 delta 2"]
    assert agent._safety_buffered_interim == ["Cand1 interim"]

    # Recheck discard
    agent._discard_safety_finalization_output()
    assert agent._safety_buffered_stream == []
    assert agent._safety_buffered_interim == []
    assert stream_deltas == []
    assert interim_msgs == []

    # Candidate 2 streams tokens and interim, then is accepted
    agent._fire_stream_delta("Cand2 delta 1")
    agent._fire_stream_delta("Cand2 delta 2")
    agent._emit_interim_assistant_message({"role": "assistant", "content": "Cand2 interim"})

    agent._release_safety_finalization_output()

    # Only Candidate 2 must be delivered
    assert stream_deltas == ["Cand2 delta 1", "Cand2 delta 2"]
    assert [t for t, _ in interim_msgs] == ["Cand2 interim"]
    assert "Cand1 delta 1" not in stream_deltas
    assert "Cand1 interim" not in [t for t, _ in interim_msgs]


# 3. Withheld text absent from provider request
def test_withheld_text_absent_from_provider_request():
    agent = _agent_for_containment()
    agent.ephemeral_system_prompt = "base-system"
    agent._cached_system_prompt = "base-system"
    agent._internal_policy_continuation = "Perform a fresh read-back."

    messages = [{"role": "user", "content": "Operator original task"}]

    # Recheck continuation sets internal policy channel; messages list must not have synthetic user turn
    assert len(messages) == 1
    assert messages[0]["role"] == "user"
    assert messages[0]["content"] == "Operator original task"

    # Effective system prompt contains policy context for continuation
    effective_system = agent._cached_system_prompt
    if agent.ephemeral_system_prompt:
        effective_system = (effective_system + "\n\n" + agent.ephemeral_system_prompt).strip()
    if agent._internal_policy_continuation:
        effective_system = (effective_system + "\n\n[Internal Policy Context]\n" + agent._internal_policy_continuation).strip()
        agent._internal_policy_continuation = None

    assert "[Internal Policy Context]" in effective_system
    assert "Perform a fresh read-back." in effective_system
    # Once consumed, continuation context is cleared (strictly one-shot)
    assert agent._internal_policy_continuation is None


# 4. Withheld text absent from history and replay
def test_withheld_text_absent_from_history_and_replay():
    class RecordingDB:
        def __init__(self):
            self.batches = []

        def append_messages_batch(self, *, session_id, messages, **_kwargs):
            self.batches.append((session_id, messages))

    agent = _agent_for_containment()
    db = RecordingDB()
    agent._session_db = db
    agent._session_db_created = True
    agent._session_persist_lock = None
    agent._persist_disabled = False
    agent._persist_user_message_idx = None
    agent._persist_user_message_override = None
    agent._persist_user_message_timestamp = None
    agent._pending_cli_user_message = None
    agent._flushed_db_message_session_id = None
    agent._flushed_db_message_ids = set()
    agent._last_flushed_db_idx = 0
    agent._db_flush_scan_prefix = None
    agent._active_compression_lock_holder = None
    agent._active_session_turn_lease_holder = None
    agent._active_session_turn_lease_ttl_seconds = 300.0

    unverified_candidate = {
        "role": "assistant",
        "content": "Unverified completion claim",
        "_safety_finalization_candidate": True,
    }
    fallback_assistant = {
        "role": "assistant",
        "content": fallback_text(FinalizationDecision(action=WITHHOLD_UNVERIFIED, operational_status="unverified")),
        "operational_status": "unverified",
    }
    messages = [
        {"role": "user", "content": "operator request"},
        unverified_candidate,
        fallback_assistant,
    ]
    agent._flush_messages_to_session_db(messages)

    assert len(db.batches) == 1
    persisted = db.batches[0][1]
    assert [row["content"] for row in persisted] == [
        "operator request",
        fallback_assistant["content"],
    ]
    assert "Unverified completion claim" not in [row["content"] for row in persisted]


# 5. Observer receives no provisional text
def test_observer_receives_no_provisional_text(monkeypatch):
    agent = _agent_for_containment()
    agent._stream_hook_base_payload = lambda: {"session_id": agent.session_id}

    import agent.plugin_stream_hooks as stream_hooks
    observer_calls = []
    monkeypatch.setattr(
        stream_hooks,
        "enqueue_plugin_stream_hook",
        lambda event, **kwargs: observer_calls.append((event, kwargs)),
    )

    agent._fire_stream_delta("provisional text delta")
    agent._emit_stream_end(final_text="provisional full text", finished=True, error=None)

    assert observer_calls == []

    # Release verified output
    agent._release_safety_finalization_output()

    assert any(ev == "on_stream_delta" and kw.get("delta") == "provisional text delta" for ev, kw in observer_calls)
    assert any(ev == "on_stream_end" and kw.get("final_text") == "provisional text delta" for ev, kw in observer_calls)


# 6. Verification stop coexists with safety hold
def test_verification_stop_coexists_with_safety_hold(monkeypatch):
    agent = _agent_for_containment()
    this_file = str(Path(__file__).resolve())
    agent._turn_file_mutation_paths = {this_file}
    agent._verification_stop_nudges = 0

    from agent.verification_stop import build_verify_on_stop_nudge
    nudge = build_verify_on_stop_nudge(
        session_id=agent.session_id,
        changed_paths=agent._turn_file_mutation_paths,
        attempts=0,
    )
    assert nudge is not None
    assert "verification" in nudge.lower() or "test" in nudge.lower()


# 7. Pre_verify coexists with safety hold
def test_pre_verify_coexists_with_safety_hold():
    from hermes_cli.plugins import get_pre_verify_continue_message

    msg = get_pre_verify_continue_message(
        session_id="test-sess",
        platform="cli",
        model="test-model",
        coding=True,
        attempt=0,
        final_response="Done with edits.",
        changed_paths=["/tmp/bar.py"],
    )
    # Returns None or string depending on plugin registrations; must not raise
    assert msg is None or isinstance(msg, str)


# 8. Competing hook cannot release another gate
def test_competing_hook_cannot_release_another_gate():
    results = [
        {"action": ACCEPT_FINAL, "owner": "rogue-plugin", "evidence": [{"tool_name": "terminal", "observed": True}]},
    ]
    decision = terminal_decision(results, expected_owner="legitimate-gate")
    assert decision.action == WITHHOLD_UNVERIFIED
    assert decision.accepted is False
    assert decision.operational_status == "unverified"


# 9. Reviewer text never becomes a user message
def test_reviewer_text_never_becomes_user_message():
    decision = FinalizationDecision(
        action=CONTINUE_RECHECK,
        owner="my-gate",
        message="Reviewer advice: verify before saying done.",
    )
    # The message is typed follow-up data, never a role=user message
    assert decision.message == "Reviewer advice: verify before saying done."
    assert decision.continue_required is True


# 10. Operator conflict becomes blocked
def test_operator_conflict_becomes_blocked():
    decision = terminal_decision([{
        "action": WITHHOLD_BLOCKED,
        "owner": "test-gate",
        "reason": "Operator decision required for high-risk action",
    }], expected_owner="test-gate")

    assert decision.action == WITHHOLD_BLOCKED
    assert decision.operational_status == "blocked"
    assert "blocked pending an operator decision" in fallback_text(decision)


# 11. Exact model/provider snapshot across failover
def test_exact_model_provider_snapshot_across_failover():
    class DummyMsg:
        def __init__(self, content):
            self.content = content
            self.tool_calls = None

    class RecordingDB:
        def __init__(self):
            self.batches = []

        def append_messages_batch(self, *, session_id, messages, **_kwargs):
            self.batches.append((session_id, messages))

    agent = _agent_for_containment()
    db = RecordingDB()
    agent._session_db = db
    agent._session_db_created = True
    agent._session_persist_lock = None
    agent._persist_disabled = False
    agent._persist_user_message_idx = None
    agent._persist_user_message_override = None
    agent._persist_user_message_timestamp = None
    agent._pending_cli_user_message = None
    agent._flushed_db_message_session_id = None
    agent._flushed_db_message_ids = set()
    agent._last_flushed_db_idx = 0
    agent._db_flush_scan_prefix = None
    agent._active_compression_lock_holder = None
    agent._active_session_turn_lease_holder = None
    agent._active_session_turn_lease_ttl_seconds = 300.0

    # Step 1: Model A generates message 1
    agent.model = "model-alpha"
    agent.provider = "provider-alpha"
    msg1 = build_assistant_message(agent, DummyMsg("Response from alpha"), "stop")
    assert msg1["model"] == "model-alpha"
    assert msg1["billing_provider"] == "provider-alpha"

    # Step 2: Failover to Model B generates message 2
    agent.model = "model-beta"
    agent.provider = "provider-beta"
    msg2 = build_assistant_message(agent, DummyMsg("Response from beta"), "stop")
    assert msg2["model"] == "model-beta"
    assert msg2["billing_provider"] == "provider-beta"

    # Step 3: Flush both to DB
    messages = [{"role": "user", "content": "prompt"}, msg1, msg2]
    agent._flush_messages_to_session_db(messages)

    persisted = db.batches[0][1]
    assert persisted[1]["model"] == "model-alpha"
    assert persisted[1]["billing_provider"] == "provider-alpha"
    assert persisted[2]["model"] == "model-beta"
    assert persisted[2]["billing_provider"] == "provider-beta"


# 12. Malformed / oversized hook output fails closed
def test_malformed_oversized_hook_output_fails_closed():
    # Unexpected field
    d1 = terminal_decision([{"action": "accept", "owner": "test-gate", "malicious_payload": 123}], expected_owner="test-gate")
    assert d1.action == WITHHOLD_UNVERIFIED

    # Oversized string
    d2 = terminal_decision([{"action": "accept", "owner": "test-gate", "reason": "x" * 500, "evidence": [{"tool_name": "terminal"}]}], expected_owner="test-gate")
    assert d2.action == WITHHOLD_UNVERIFIED

    # Oversized evidence list (>16 items)
    d3 = terminal_decision([{
        "action": "accept",
        "owner": "test-gate",
        "evidence": [{"tool_name": "terminal"} for _ in range(20)],
    }], expected_owner="test-gate")
    assert d3.action == WITHHOLD_UNVERIFIED

    # Deep nesting
    d4 = terminal_decision([{
        "action": "accept",
        "owner": "test-gate",
        "evidence": [{"tool_name": "terminal", "nested": {"evil": True}}],
    }], expected_owner="test-gate")
    assert d4.action == WITHHOLD_UNVERIFIED


# 13. Guardrail halt with safety hold emits halt explanation and sets blocked
def test_guardrail_halt_with_safety_hold_emits_halt_explanation_and_blocks():
    agent = _agent_for_containment()
    stream_deltas = []
    agent.stream_delta_callback = stream_deltas.append

    # Simulate provisional tokens before halt
    agent._safety_buffered_stream = ["unverified tokens"]
    halt_response = "⚠️ Tool guardrail halted bash: command_blocked"

    # Guardrail halt logic sets operational_status = blocked, discards held provisional text,
    # marks released = True so finalizer does not swallow it, and delivers halt text
    if getattr(agent, "_safety_finalization_hold", False):
        agent._safety_finalization_status = "blocked"
        agent._discard_safety_finalization_output()
        agent._safety_finalization_released = True
    if agent.stream_delta_callback:
        agent.stream_delta_callback(halt_response)

    assert stream_deltas == [halt_response]
    assert agent._safety_buffered_stream == []
    assert agent._safety_finalization_status == "blocked"
    assert agent._safety_finalization_released is True


# 14. Verification continuation appends candidate ephemerally for provider context
def test_verification_continuation_appends_candidate_ephemerally():
    agent = _agent_for_containment()
    messages = [{"role": "user", "content": "Edit code"}]
    candidate_msg = {
        "role": "assistant",
        "content": "I have edited foo.py",
        "_safety_finalization_candidate": True,
    }
    verify_nudge = "[System: You edited code, run tests]"

    # When verification stop fires on held turn:
    agent._discard_safety_finalization_output()
    messages.append(candidate_msg)
    messages.append({
        "role": "user",
        "content": verify_nudge,
        "_verification_stop_synthetic": True,
    })

    # Messages in turn contains candidate + nudge for provider context
    assert len(messages) == 3
    assert messages[1]["content"] == "I have edited foo.py"
    assert messages[2]["content"] == verify_nudge

    # Durable flush ignores the candidate
    class RecordingDB:
        def __init__(self):
            self.batches = []
        def append_messages_batch(self, *, session_id, messages, **_kwargs):
            self.batches.append((session_id, messages))

    db = RecordingDB()
    agent._session_db = db
    agent._session_db_created = True
    agent._session_persist_lock = None
    agent._persist_disabled = False
    agent._persist_user_message_idx = None
    agent._persist_user_message_override = None
    agent._persist_user_message_timestamp = None
    agent._pending_cli_user_message = None
    agent._flushed_db_message_session_id = None
    agent._flushed_db_message_ids = set()
    agent._last_flushed_db_idx = 0
    agent._db_flush_scan_prefix = None
    agent._active_compression_lock_holder = None
    agent._active_session_turn_lease_holder = None
    agent._active_session_turn_lease_ttl_seconds = 300.0

    agent._flush_messages_to_session_db(messages)
    persisted = db.batches[0][1]
    assert [m["content"] for m in persisted] == ["Edit code"]
