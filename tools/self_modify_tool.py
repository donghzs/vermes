"""Self-modification tool.

Lets the agent propose changes to Vermes's own source/config files through
``EmergentChangePipeline``. Every proposal is gated behind an explicit,
human-in-the-loop Gateway approval: the agent stages the change, computes a
unified diff, and blocks the turn while the user reviews it in the Gateway or
desktop UI and clicks /approve or /deny. Only after approval does the pipeline commit.

This is the production wiring of the previously-dormant self-modification
capability (R1 of the audit): observation-driven emergence is always on, but
the agent can never rewrite its own code without the user first seeing the
diff and confirming. No hardcoded risk rules — the only gate is the user.

Design notes
------------
* The agent must NEVER call the commit step on its own. ``apply_change`` is
  reached only after ``request_gateway_approval`` returns an approve choice,
  and even then it passes ``force=True`` (the user already confirmed, so the
  data-vacuum cold-start gate is bypassed — see emergent_change.py).
* If there is no active session (Gateway or desktop GUI), the request fails
  closed (denied), so a headless/CLI run can never silently self-modify.
"""

import difflib
import json
import os

from tools.registry import registry


SELF_MODIFY_SCHEMA = {
    "name": "self_modify",
    "description": (
        "Propose a change to Vermes's own source or config files. The change is "
        "staged and a diff preview is shown; it REQUIRES explicit user approval "
        "in the Gateway before being applied. Use only for self-improvements the "
        "user would want to review. The agent must not confirm on its own."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "source": {
                "type": "string",
                "description": "Where the change idea came from, e.g. 'agent_emergence' or a short label.",
            },
            "target_path": {
                "type": "string",
                "description": "Absolute path of the file to create or modify.",
            },
            "content": {
                "type": "string",
                "description": "Full new file content (the complete file, not a patch).",
            },
            "description": {
                "type": "string",
                "description": "What this change does and why the user should approve it.",
            },
        },
        "required": ["source", "target_path", "content"],
    },
}


def _build_diff(target_path: str, content: str) -> str:
    """Return a unified diff of *content* against the current file at *target_path*."""
    current_lines: list = []
    if os.path.exists(target_path):
        try:
            with open(target_path, "r", encoding="utf-8") as fh:
                current_lines = fh.read().splitlines()
        except Exception:
            current_lines = []
    new_lines = content.splitlines()
    diff = "\n".join(
        difflib.unified_diff(
            current_lines,
            new_lines,
            fromfile=f"a/{os.path.basename(target_path)}",
            tofile=f"b/{os.path.basename(target_path)}",
            lineterm="",
        )
    )
    return diff or "(no content change — identical to the current file)"


def self_modify_tool(args) -> str:
    """Handler: stage a self-modification, request Gateway approval, commit on approve."""
    source = args.get("source", "agent_emergence")
    target_path = args.get("target_path", "")
    content = args.get("content", "")
    description = args.get("description", "")

    if not target_path or not content:
        return json.dumps({"ok": False, "error": "target_path and content are required"})

    # Local imports keep this module importable without pulling the whole agent
    # graph at tool-discovery time (mirrors terminal_tool.py's lazy imports).
    from agent.emergent_change import get_pipeline, ChangeProposal
    from tools.approval import get_current_session_key, approve_privileged_action

    pipeline = get_pipeline()
    proposal = ChangeProposal(
        source=source,
        target_path=target_path,
        content=content,
        description=description,
        initiator="agent",
    )

    # Step 1: validate format + record as "proposed" (no commit, no write).
    proposed = pipeline.propose_change(proposal)
    if not proposed.pending_confirmation:
        return json.dumps({
            "ok": False,
            "error": proposed.error or "format validation failed",
            "target_path": target_path,
        })

    diff = _build_diff(target_path, content)

    # Step 2: real user confirmation via the Gateway (blocks this turn).
    # YOLO-aware: under VERMES_YOLO_MODE / session /yolo / approvals.mode=off
    # this auto-approves without prompting, matching the dangerous-command
    # policy. Otherwise it pops the same desktop / Gateway approval dialog.
    session_key = get_current_session_key(default="")
    approval_data = {
        "command": f"self_modify {target_path}",
        "description": description or f"Proposed self-modification to {target_path}",
        "pattern_key": "self_modify",
        "pattern_keys": ["self_modify"],
        "diff": diff,
        "target_path": target_path,
        "surface": "gui",
    }
    approved = approve_privileged_action(session_key, approval_data)

    if not approved:
        # User denied / timed out / no active session — discard, never write.
        pipeline._record_change_event(
            proposal, committed=False,
            reason="denied_by_user", is_error=False,
        )
        return json.dumps({
            "ok": True,
            "applied": False,
            "reason": "denied_or_timeout",
            "target_path": target_path,
            "diff": diff,
        })

    # Step 3: user approved — commit (force bypasses the data-vacuum gate,
    # which is correct because the user just explicitly confirmed).
    result = pipeline.apply_change(proposal, force=True)
    return json.dumps({
        "ok": True,
        "applied": result.committed,
        "target_path": target_path,
        "diff": diff,
        "error": result.error or None,
    })


registry.register(
    name="self_modify",
    toolset="agent",
    schema=SELF_MODIFY_SCHEMA,
    handler=self_modify_tool,
    emoji="🛠️",
)
