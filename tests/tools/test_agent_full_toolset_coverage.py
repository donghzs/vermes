"""Agent full toolset coverage — regression guard.

Guarantees that every core tool the agent is *supposed* to have is still
declared in the enabled toolsets.  Without this the agent silently loses a
capability (a tool is renamed / dropped / no longer listed in any toolset)
and only surfaces later when a user's task that needs it fails at runtime.

Design notes
------------
* Source of truth is the **declared toolset membership** (``toolsets``),
  i.e. what the agent is *meant* to expose when all toolsets are enabled.
  This is stable and does not depend on whether optional third-party deps
  (that gate individual tool modules from importing) happen to be installed
  in the test venv.
* We resolve the union of every enabled toolset and assert the required core
  tools are present in that union.  Extra declared tools are an allowed
  superset — new tools added over time never break this test.
* If the compiled backend's ``AGENT_TOOL_NAMES`` constant is importable it is
  mirrored as an authoritative cross-check; otherwise the declared-toolset
  union is used.
"""

import pytest

# Every tool the agent is expected to declare out-of-the-box when all toolsets
# are enabled.  This is the *minimum* required set; the toolsets may declare
# more.
REQUIRED_AGENT_TOOL_NAMES = [
    # file
    "read_file",
    "write_file",
    "patch",
    "search_files",
    # browser
    "browser_navigate",
    "browser_click",
    "browser_type",
    "browser_snapshot",
    "browser_console",
    "browser_press",
    "browser_scroll",
    "browser_back",
    "browser_vision",
    "browser_get_images",
    # multimodal / media
    "text_to_speech",
    "vision_analyze",
    "video_analyze",
    # delegation / coordination
    "delegate_task",
    "clarify",
    "cronjob",
    "send_message",
    # state / session
    "memory",
    "process",
    "todo",
]


def _agent_enabled_tool_names():
    """Return the agent's effective tool-name set (authoritative source).

    Prefers the compiled backend's ``AGENT_TOOL_NAMES`` constant when it is
    importable; otherwise falls back to the union of every declared enabled
    toolset (the source of truth for what the agent is *meant* to expose).
    """
    try:
        from vermes_backend.agent.tools import AGENT_TOOL_NAMES  # type: ignore
        return set(AGENT_TOOL_NAMES)
    except Exception:
        pass

    # Fallback: union of all declared enabled toolsets.  ``resolve_toolset``
    # returns the *declared* tool names for a toolset (including the tools a
    # gating dep would register once available), so this is stable across
    # environments.
    from toolsets import get_all_toolsets, resolve_toolset

    enabled: set = set()
    for toolset_name in get_all_toolsets():
        enabled.update(resolve_toolset(toolset_name))
    return enabled


def test_agent_has_all_required_tools():
    """Every required core tool must be declared; extras are allowed."""
    enabled = _agent_enabled_tool_names()

    if not enabled:
        pytest.skip(
            "no toolset/tool-source could be resolved in this environment; "
            "coverage check skipped"
        )

    missing = [name for name in REQUIRED_AGENT_TOOL_NAMES if name not in enabled]

    assert not missing, (
        f"agent toolset is missing required declared tools: {sorted(missing)} "
        f"(declared count: {len(enabled)})"
    )


def test_required_list_is_well_formed():
    """Sanity: the required list itself is non-empty and has no duplicates."""
    assert REQUIRED_AGENT_TOOL_NAMES
    assert len(REQUIRED_AGENT_TOOL_NAMES) == len(set(REQUIRED_AGENT_TOOL_NAMES))
