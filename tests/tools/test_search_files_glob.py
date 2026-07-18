"""Bug 3 fix: `search_files` with a filename glob (e.g. `*.txt`) passed as
`pattern` should be auto-routed to file-name search instead of being treated
as a content regex (which fails with "unexpected end of pattern").
"""
import json

import pytest


@pytest.fixture
def sandbox(tmp_path):
    (tmp_path / "a.txt").write_text("hello")
    (tmp_path / "b.md").write_text("world")
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "c.txt").write_text("nested")
    return tmp_path


def test_glob_pattern_routed_to_files(sandbox):
    import tools.file_tools as ft

    result = ft.search_tool("*.txt", target="content", path=str(sandbox), limit=50)
    data = json.loads(result)

    # Auto-routed to file-name search.
    assert data.get("_info"), "glob pattern was not routed to file search"
    found = " ".join(str(m) for m in (data.get("matches") or data.get("files") or []))
    assert "a.txt" in found, "expected a.txt in results"
    assert "c.txt" in found, "expected nested c.txt in results"
    assert "b.md" not in found, "non-matching .md should be excluded"


def test_content_regex_still_works(sandbox):
    import tools.file_tools as ft

    # A real content regex should NOT be routed and should grep contents.
    result = ft.search_tool("hello", target="content", path=str(sandbox), limit=50)
    data = json.loads(result)
    assert data.get("_info") is None, "plain content regex must not be routed"
    assert data.get("matches"), "expected content matches for 'hello'"
