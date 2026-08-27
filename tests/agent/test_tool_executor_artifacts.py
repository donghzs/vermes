"""Regression tests for _build_tool_artifacts.

Ensures artifact extraction from tool results + args works correctly.
Root cause of the original bug: WriteResult.to_dict() has no 'path' field,
so data.get("path") always returned None. Fix: prioritize args["path"].
"""
import json
import pytest
from agent.tool_executor import _build_tool_artifacts


class TestBuildToolArtifacts:
    """Cover the 4 key scenarios for artifact path extraction."""

    def test_write_file_args_has_path(self):
        """Scenario 1: write_file with path in args (main path, no path in result JSON)."""
        result = json.dumps({"bytes_written": 42, "dirs_created": []})
        args = {"path": "/home/user/report.md", "content": "# Hello"}
        artifacts = _build_tool_artifacts("write_file", result, args)
        assert len(artifacts) == 1
        assert artifacts[0]["path"] == "/home/user/report.md"
        assert artifacts[0]["title"] == "report.md"
        assert artifacts[0]["source"] == "write_file"

    def test_patch_args_has_file_path(self):
        """Scenario 2: patch with file_path in args (alternative key name)."""
        result = json.dumps({"success": True, "diff": "@@ -1 +1 @@"})
        args = {"file_path": "/tmp/a.py", "patch": "..."}
        artifacts = _build_tool_artifacts("patch", result, args)
        assert len(artifacts) == 1
        assert artifacts[0]["path"] == "/tmp/a.py"
        assert artifacts[0]["title"] == "a.py"
        assert artifacts[0]["source"] == "patch"

    def test_fallback_result_json_has_path(self):
        """Scenario 3: no args, but result JSON contains path (fallback path)."""
        result = json.dumps({"path": "/legacy/x.txt", "bytes_written": 10})
        artifacts = _build_tool_artifacts("write_file", result, None)
        assert len(artifacts) == 1
        assert artifacts[0]["path"] == "/legacy/x.txt"

    def test_no_path_anywhere_returns_empty(self):
        """Scenario 4: no path in args or result → empty list, no crash."""
        result = json.dumps({"bytes_written": 0, "error": "disk full"})
        artifacts = _build_tool_artifacts("write_file", result, None)
        assert artifacts == []

    def test_result_artifacts_list_passthrough(self):
        """Non-write_file tools can declare artifacts in result dict."""
        result = {"artifacts": [{"path": "/output/plot.png", "title": "Chart", "source": "execute_code"}]}
        artifacts = _build_tool_artifacts("execute_code", result, None)
        assert len(artifacts) == 1
        assert artifacts[0]["path"] == "/output/plot.png"
        assert artifacts[0]["title"] == "Chart"
        assert artifacts[0]["source"] == "execute_code"

    def test_write_file_error_result_no_artifacts(self):
        """write_file result with error field should not produce artifacts from fallback."""
        result = json.dumps({"error": "permission denied", "bytes_written": 0})
        args = {"path": "/root/secret.txt", "content": "data"}
        # Main path (args) should still work even if result has error
        artifacts = _build_tool_artifacts("write_file", result, args)
        assert len(artifacts) == 1
        assert artifacts[0]["path"] == "/root/secret.txt"

    def test_fallback_skipped_when_result_has_error(self):
        """Fallback (result JSON) should skip when error key present."""
        result = json.dumps({"error": "failed", "path": "/should/not/extract.txt"})
        artifacts = _build_tool_artifacts("write_file", result, None)
        assert artifacts == []

    def test_filepath_key_in_args(self):
        """filepath (third alternative key) should also work."""
        result = json.dumps({"bytes_written": 100})
        args = {"filepath": "/data/config.yaml", "content": "key: value"}
        artifacts = _build_tool_artifacts("write_file", result, args)
        assert len(artifacts) == 1
        assert artifacts[0]["path"] == "/data/config.yaml"

    def test_artifact_includes_mime(self):
        """Bug #2: 每个产物应带 mime（按扩展名推断），供前端 rendererFor 准确选型。"""
        result = json.dumps({"bytes_written": 1})
        args = {"path": "/out/report.html"}
        arts = _build_tool_artifacts("write_file", result, args)
        assert arts[0]["mime"] == "text/html"
        # 默认/未知扩展名落空字符串而非缺失键
        arts2 = _build_tool_artifacts("write_file", result, {"path": "/out/x.unknown"})
        assert "mime" in arts2[0] and arts2[0]["mime"] == ""

    def test_present_files_result_dict_passthrough_with_mime(self):
        """Bug #3: present_files 返回 {'preview','artifacts'} 时，artifacts 应透传且补 mime。"""
        result = {
            "preview": "交付产物 1 个:",
            "artifacts": [{"path": "/out/plot.png", "title": "Chart"}],
        }
        arts = _build_tool_artifacts("present_files", result, None)
        assert len(arts) == 1
        assert arts[0]["path"] == "/out/plot.png"
        assert arts[0]["mime"] == "image/png"  # 未显式给 mime 时自动推断
        assert arts[0]["source"] == "present_files"
