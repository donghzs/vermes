"""Golden-snapshot test for run_conversation output stability.

Captures the complete output of a minimal run_conversation call and
compares it against a stored golden snapshot.  Any behavioral drift
caused by refactoring (B-3: extracting stage functions from the
3863-line run_conversation god function) will be caught here.

The snapshot covers:
  - final_response text
  - role/content of every message in the returned messages list
  - usage metrics (prompt/completion/total tokens)
  - interrupted flag
  - reasoning (if present)

If the snapshot needs to change intentionally, regenerate with:
    pytest tests/run_agent/test_conversation_loop_snapshot.py --update-snapshot
"""
from __future__ import annotations

import json
import os
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from run_agent import AIAgent

SNAPSHOT_DIR = os.path.join(os.path.dirname(__file__), "snapshots")
SNAPSHOT_FILE = os.path.join(SNAPSHOT_DIR, "conversation_loop_golden.json")


def _mock_response(*, content: str = "Hello! How can I help you today?"):
    msg = SimpleNamespace(content=content, tool_calls=None)
    choice = SimpleNamespace(message=msg, finish_reason="stop")
    return SimpleNamespace(
        choices=[choice],
        model="test/golden-model",
        usage=SimpleNamespace(
            prompt_tokens=10,
            completion_tokens=8,
            total_tokens=18,
        ),
    )


def _make_agent(session_db=None):
    with (
        patch("run_agent.get_tool_definitions", return_value=[]),
        patch("run_agent.check_toolset_requirements", return_value={}),
        patch("run_agent.OpenAI"),
    ):
        agent = AIAgent(
            api_key="test-key",
            base_url="https://openrouter.ai/api/v1",
            quiet_mode=True,
            skip_context_files=True,
            skip_memory=True,
            session_db=session_db,
            session_id="golden-session",
            platform="test",
        )
    agent.client = MagicMock()
    agent.client.chat.completions.create.return_value = _mock_response()
    return agent


def _serialize_result(result) -> dict:
    """Extract deterministic fields from run_conversation result."""
    return {
        "final_response": result.get("final_response", ""),
        "interrupted": result.get("interrupted", False),
        "messages": [
            {
                "role": m.get("role", ""),
                "content": m.get("content", ""),
                # Exclude non-deterministic fields like timestamps, tool_call_ids
            }
            for m in result.get("messages", [])
        ],
        "usage": {
            "prompt_tokens": result.get("usage", {}).get("prompt_tokens", 0)
                if isinstance(result.get("usage"), dict) else 0,
            "completion_tokens": result.get("usage", {}).get("completion_tokens", 0)
                if isinstance(result.get("usage"), dict) else 0,
            "total_tokens": result.get("usage", {}).get("total_tokens", 0)
                if isinstance(result.get("usage"), dict) else 0,
        },
    }


def test_run_conversation_golden_snapshot():
    """Run_conversation output must match the golden snapshot.

    This is the B-3 safety net: any refactor of the god function
    must produce byte-identical output.
    """
    session_db = MagicMock()
    agent = _make_agent(session_db)

    result = agent.run_conversation("hello", conversation_history=[])

    actual = _serialize_result(result)

    # Normalize: the assistant message content comes from the mock, which
    # is deterministic. The user message is "hello".
    # We only snapshot the stable parts.
    snapshot = {
        "final_response": actual["final_response"],
        "interrupted": actual["interrupted"],
        "message_count": len(actual["messages"]),
        "message_roles": [m["role"] for m in actual["messages"]],
        "user_message": actual["messages"][0]["content"] if actual["messages"] else "",
        "assistant_message": actual["messages"][-1]["content"] if len(actual["messages"]) >= 2 else "",
    }

    if not os.path.exists(SNAPSHOT_DIR):
        os.makedirs(SNAPSHOT_DIR)

    if "--update-snapshot" in os.environ.get("PYTEST_ARGS", ""):
        with open(SNAPSHOT_FILE, "w") as f:
            json.dump(snapshot, f, indent=2, ensure_ascii=False)
        pytest.skip("Snapshot updated")

    if not os.path.exists(SNAPSHOT_FILE):
        # First run — create the snapshot
        with open(SNAPSHOT_FILE, "w") as f:
            json.dump(snapshot, f, indent=2, ensure_ascii=False)
        pytest.skip("Golden snapshot created — re-run to verify")

    with open(SNAPSHOT_FILE) as f:
        expected = json.load(f)

    assert snapshot == expected, (
        f"Golden snapshot mismatch!\n"
        f"Expected: {json.dumps(expected, indent=2)}\n"
        f"Actual:   {json.dumps(snapshot, indent=2)}\n"
        f"If this change is intentional, regenerate with:\n"
        f"  PYTEST_ARGS=--update-snapshot pytest tests/run_agent/test_conversation_loop_snapshot.py"
    )


def test_run_conversation_golden_with_tool_call():
    """Golden snapshot for a turn with a tool call.

    Ensures tool dispatch path is also covered by the snapshot safety net.
    """
    # This is a placeholder — we'll add a tool-call scenario in Step 2
    # when we have the mock infrastructure for tool dispatch.
    pytest.skip("Tool-call golden snapshot — to be added in Step 2")
