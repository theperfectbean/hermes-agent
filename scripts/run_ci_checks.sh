#!/usr/bin/env bash
# run_ci_checks.sh — Run downstream CI test and static check suite
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${REPO_DIR}"

echo "=== 1. Git Diff Check (Whitespace & Formatting) ==="
git diff --check $(git merge-base HEAD origin/main 2>/dev/null || echo "HEAD~1")..HEAD || git diff --check

echo "=== 2. Static Analysis (Ruff) ==="
if command -v ruff >/dev/null 2>&1; then
    ruff check .
    ruff check agent/turn_finalizer.py agent/conversation_loop.py agent/model_metadata.py hermes_state.py --select E,F,W --ignore E501
else
    echo "ruff not in local PATH, skipping local ruff check"
fi

echo "=== 3. Targeted Hardening Test Suite ==="
TARGET_TESTS=(
    "tests/agent/test_finalization_gate.py"
    "tests/agent/test_message_metadata.py"
    "tests/hermes_state/test_message_finalization_metadata.py"
    "tests/hermes_state/test_isolation_marker_env.py"
    "tests/run_agent/test_safety_finalization_containment.py"
    "tests/tools/test_session_search.py"
)

if command -v pytest >/dev/null 2>&1; then
    pytest "${TARGET_TESTS[@]}" -v --tb=short
else
    echo "pytest not in local PATH (run inside Python 3.11 virtualenv)"
fi

echo "=== All CI Checks Complete ==="
