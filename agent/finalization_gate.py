"""Provider-neutral finalization gate for material local safety overlays.

The core deliberately does not decide what is material, invoke a reviewer, or
interpret terminal output. A locally installed plugin arms the gate at the
start of a turn and returns a small structured decision at the terminal point.
While armed, visible assistant text is held until the terminal decision is
accepted. This keeps provisional completion claims out of streams and durable
history without changing ordinary Hermes turns.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

ACCEPT_FINAL = "accept"
CONTINUE_RECHECK = "continue_recheck"
CONTINUE_REVIEW = "continue_review"
WITHHOLD_UNVERIFIED = "withhold_unverified"
WITHHOLD_BLOCKED = "withhold_blocked"

_VALID_START_ACTIONS = frozenset({"hold", "pass", "skip"})
_VALID_TERMINAL_ACTIONS = frozenset({
    ACCEPT_FINAL,
    CONTINUE_RECHECK,
    CONTINUE_REVIEW,
    WITHHOLD_UNVERIFIED,
    WITHHOLD_BLOCKED,
})
_VALID_STATUSES = frozenset({"verified", "unverified", "blocked"})

_ALLOWED_START_KEYS = frozenset({"action", "owner", "reason"})
_ALLOWED_TERMINAL_KEYS = frozenset({
    "action",
    "owner",
    "reason",
    "message",
    "operational_status",
    "evidence",
    "review_status",
})
_ALLOWED_EVIDENCE_KEYS = frozenset({
    "role",
    "tool_name",
    "effect_disposition",
    "observed",
    "timestamp",
    "summary",
    "path",
    "command",
    "exit_code",
    "kind",
    "resource",
})

_OWNER_PATTERN = re.compile(r"^[a-zA-Z0-9_\-\.]{1,80}$")


@dataclass(frozen=True)
class FinalizationDecision:
    """Validated policy decision. message is follow-up data, never a user turn."""

    action: str
    owner: str = ""
    reason: str = ""
    message: str = ""
    operational_status: str = ""
    evidence: tuple[Mapping[str, Any], ...] = ()
    review_status: str = ""

    @property
    def accepted(self) -> bool:
        return self.action == ACCEPT_FINAL

    @property
    def continue_required(self) -> bool:
        return self.action in {CONTINUE_RECHECK, CONTINUE_REVIEW}


def validate_hook_output(result: Any, *, phase: str) -> dict[str, Any] | None:
    """Strictly validate and normalize hook output at the hook boundary.

    Enforces:
      - Mapping type;
      - Allowed fields only (no arbitrary payload passthrough);
      - Bounded string lengths (reason <= 240, message <= 2000, review_status <= 80, owner <= 80);
      - Bounded lists (evidence <= 16 items);
      - Bounded flat evidence items (allowed scalar keys only, no nested maps/lists, values <= 240 chars);
      - Fail-closed on any violation.
    """
    if not isinstance(result, Mapping):
        return None

    keys = set(result.keys())
    if phase == "start":
        if not keys.issubset(_ALLOWED_START_KEYS):
            return None
        action = str(result.get("action", "")).strip().lower()
        if action not in _VALID_START_ACTIONS:
            return None
        owner = result.get("owner")
        if owner is not None:
            if not isinstance(owner, str) or not _OWNER_PATTERN.match(owner):
                return None
        else:
            owner = ""
        reason = result.get("reason")
        if reason is not None:
            if not isinstance(reason, str) or len(reason) > 240:
                return None
        else:
            reason = ""
        return {
            "action": action,
            "owner": owner,
            "reason": reason,
        }

    if phase == "terminal":
        if not keys.issubset(_ALLOWED_TERMINAL_KEYS):
            return None
        action = str(result.get("action", "")).strip().lower()
        if action not in _VALID_TERMINAL_ACTIONS:
            return None

        owner = result.get("owner")
        if owner is not None:
            if not isinstance(owner, str) or not _OWNER_PATTERN.match(owner):
                return None
        else:
            owner = ""

        reason = result.get("reason")
        if reason is not None:
            if not isinstance(reason, str) or len(reason) > 240:
                return None
        else:
            reason = ""

        message = result.get("message")
        if message is not None:
            if not isinstance(message, str) or len(message) > 2000:
                return None
        else:
            message = ""

        review_status = result.get("review_status")
        if review_status is not None:
            if not isinstance(review_status, str) or len(review_status) > 80:
                return None
        else:
            review_status = ""

        raw_status = result.get("operational_status")
        if raw_status is not None:
            if not isinstance(raw_status, str) or raw_status.strip().lower() not in _VALID_STATUSES:
                return None
            status = raw_status.strip().lower()
        else:
            status = ""

        if action == ACCEPT_FINAL:
            status = "verified"
        elif action == WITHHOLD_BLOCKED:
            status = "blocked"
        elif not status:
            status = "unverified"

        raw_evidence = result.get("evidence")
        normalized_evidence: list[dict[str, Any]] = []
        if raw_evidence is not None:
            if not isinstance(raw_evidence, list) or len(raw_evidence) > 16:
                return None
            for item in raw_evidence:
                if not isinstance(item, Mapping):
                    return None
                if not set(item.keys()).issubset(_ALLOWED_EVIDENCE_KEYS):
                    return None
                norm_item = {}
                for k, v in item.items():
                    if isinstance(v, (dict, list, tuple, set)):
                        return None
                    if isinstance(v, str):
                        if len(v) > 240:
                            return None
                        norm_item[k] = v
                    elif isinstance(v, (int, float, bool)) or v is None:
                        norm_item[k] = v
                    else:
                        return None
                normalized_evidence.append(norm_item)

        return {
            "action": action,
            "owner": owner,
            "reason": reason,
            "message": message,
            "operational_status": status,
            "evidence": normalized_evidence,
            "review_status": review_status,
        }

    return None


def start_hold_required(results: Iterable[Any]) -> tuple[bool, str | None]:
    """Return (True, owner) if a valid local-policy start directive armed the gate."""
    for result in results:
        validated = validate_hook_output(result, phase="start")
        if validated and validated["action"] == "hold":
            owner = validated.get("owner") or "anonymous_gate"
            return True, owner
    return False, None


def terminal_decision(
    results: Iterable[Any],
    *,
    expected_owner: str | None = None,
) -> FinalizationDecision:
    """Evaluate terminal directives, binding to expected_owner and enforcing evidence."""
    for result in results:
        validated = validate_hook_output(result, phase="terminal")
        if not validated:
            continue

        res_owner = validated.get("owner", "")
        if expected_owner:
            if res_owner != expected_owner:
                continue

        action = validated["action"]
        evidence = tuple(validated.get("evidence", []))

        if action == ACCEPT_FINAL:
            if not evidence:
                return FinalizationDecision(
                    action=WITHHOLD_UNVERIFIED,
                    owner=expected_owner or res_owner,
                    reason="Acceptance rejected: minimum evidence contract not met (empty evidence).",
                    operational_status="unverified",
                )
            return FinalizationDecision(
                action=ACCEPT_FINAL,
                owner=expected_owner or res_owner,
                reason=validated["reason"],
                message=validated["message"],
                operational_status="verified",
                evidence=evidence,
                review_status=validated["review_status"],
            )

        return FinalizationDecision(
            action=action,
            owner=expected_owner or res_owner,
            reason=validated["reason"],
            message=validated["message"],
            operational_status=validated["operational_status"],
            evidence=evidence,
            review_status=validated["review_status"],
        )

    return FinalizationDecision(
        action=WITHHOLD_UNVERIFIED,
        owner=expected_owner or "",
        reason="No valid authorized finalization decision was supplied by the active policy.",
        operational_status="unverified",
    )


def fallback_text(decision: FinalizationDecision, candidate_response: str = "") -> str:
    """Non-destructive, transparent response used when a held candidate is unverified."""
    if decision.operational_status == "blocked":
        banner = (
            "> ⚠️ **[SYSTEM GUARD — BLOCKED ACTION]**\n"
            "> *The requested operational outcome is blocked pending an operator decision; it has not been claimed as complete.*\n\n"
        )
    else:
        banner = (
            "> ⚠️ **[SYSTEM GUARD — UNVERIFIED ACTION]**\n"
            "> *The following outcome lacks fresh observable tool evidence and has not been claimed as verified.*\n\n"
        )
    if candidate_response and candidate_response.strip():
        return banner + candidate_response.strip()
    return banner + "The requested outcome could not be verified from fresh observable evidence; it has not been claimed as complete."


def evidence_metadata(decision: FinalizationDecision) -> dict[str, Any]:
    """Return bounded display metadata; never store raw output or review text."""
    return {
        "policy_version": 1,
        "owner": decision.owner,
        "reason": decision.reason,
        "review_status": decision.review_status,
        "evidence": [dict(item) for item in decision.evidence[:16]],
    }
