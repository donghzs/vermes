"""Tests for agent/emergent_change.py — emergent self-modification pipeline.

Tests cover:
1. Format validation (YAML/JSON/Python)
2. Change proposal → staging → commit flow
3. Rollback (with and without backup)
4. raw_event recording on commit/rollback
5. Unknown file extensions (no validation, passes through)
6. Error handling (invalid content, missing target dir)
"""

import json
import os
import tempfile
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock
from agent.emergent_change import (
    ChangeProposal,
    ChangeResult,
    EmergentChangePipeline,
    apply_change,
    _validate_file_format,
    _validate_yaml,
    _validate_json,
    _validate_python_syntax,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_hermes_home(tmp_path):
    """Create a temporary HERMES_HOME."""
    home = tmp_path / "hermes"
    home.mkdir()
    return str(home)


@pytest.fixture
def pipeline(tmp_hermes_home):
    """Create a pipeline with temp HERMES_HOME."""
    return EmergentChangePipeline(hermes_home=tmp_hermes_home)


# ---------------------------------------------------------------------------
# Format validators
# ---------------------------------------------------------------------------

class TestFormatValidators:
    def test_valid_yaml(self):
        assert _validate_yaml("key: value\nlist:\n  - a\n  - b\n")

    def test_invalid_yaml(self):
        assert not _validate_yaml("key: value: extra: colon\n  - broken")

    def test_valid_json(self):
        assert _validate_json('{"key": "value", "num": 42}')

    def test_invalid_json(self):
        assert not _validate_json('{key: "missing quotes"}')

    def test_valid_python(self):
        assert _validate_python_syntax("x = 1\nprint(x)\n")

    def test_invalid_python(self):
        assert not _validate_python_syntax("def broken(\n")

    def test_file_format_yaml(self, tmp_path):
        ok, err = _validate_file_format(str(tmp_path / "config.yaml"), "key: value")
        assert ok and err == ""

    def test_file_format_json(self, tmp_path):
        ok, err = _validate_file_format(str(tmp_path / "data.json"), '{"a": 1}')
        assert ok and err == ""

    def test_file_format_python(self, tmp_path):
        ok, err = _validate_file_format(str(tmp_path / "script.py"), "x = 1")
        assert ok and err == ""

    def test_file_format_invalid_yaml(self, tmp_path):
        ok, err = _validate_file_format(str(tmp_path / "bad.yaml"), "key: value: broken")
        assert not ok and "YAML" in err

    def test_file_format_unknown_ext(self, tmp_path):
        ok, err = _validate_file_format(str(tmp_path / "notes.txt"), "anything goes")
        assert ok and err == ""

    def test_file_format_no_ext(self, tmp_path):
        ok, err = _validate_file_format(str(tmp_path / "Makefile"), "all: build")
        assert ok and err == ""


# ---------------------------------------------------------------------------
# Change proposal → commit flow
# ---------------------------------------------------------------------------

class TestApplyChange:
    def test_commit_new_yaml_file(self, pipeline, tmp_path):
        target = tmp_path / "domains" / "translation.yaml"
        result = pipeline.apply_change(ChangeProposal(
            source="domain_modules",
            target_path=str(target),
            content="name: translation\ndescription: Translation helper\n",
            description="New domain module for translation tasks",
        ))
        assert result.committed
        assert target.exists()
        assert "translation" in target.read_text()

    def test_commit_new_json_file(self, pipeline, tmp_path):
        target = tmp_path / "config" / "skills.json"
        result = pipeline.apply_change(ChangeProposal(
            source="skill_extractor",
            target_path=str(target),
            content=json.dumps({"skill": "translate", "active": True}),
            description="New skill definition",
        ))
        assert result.committed
        assert target.exists()

    def test_commit_new_python_file(self, pipeline, tmp_path):
        target = tmp_path / "plugins" / "helper.py"
        result = pipeline.apply_change(ChangeProposal(
            source="capability_evolver",
            target_path=str(target),
            content="def helper():\n    return 'hello'\n",
            description="New plugin helper",
        ))
        assert result.committed
        assert target.exists()

    def test_commit_invalid_yaml_rejected(self, pipeline, tmp_path):
        target = tmp_path / "bad.yaml"
        result = pipeline.apply_change(ChangeProposal(
            source="agent",
            target_path=str(target),
            content="key: value: broken: colon\n",
            description="Bad YAML",
        ))
        assert not result.committed
        assert "YAML" in result.error
        assert not target.exists()

    def test_commit_invalid_python_rejected(self, pipeline, tmp_path):
        target = tmp_path / "broken.py"
        result = pipeline.apply_change(ChangeProposal(
            source="agent",
            target_path=str(target),
            content="def broken(\n",
            description="Bad Python",
        ))
        assert not result.committed
        assert "Python" in result.error
        assert not target.exists()

    def test_commit_overwrites_existing_with_backup(self, pipeline, tmp_path):
        target = tmp_path / "config.yaml"
        target.write_text("old: config\n")
        original_content = target.read_text()

        result = pipeline.apply_change(ChangeProposal(
            source="agent",
            target_path=str(target),
            content="new: config\n",
            description="Update config",
        ))
        assert result.committed
        assert target.read_text() == "new: config\n"

        # Check backup exists
        backups = list(tmp_path.glob("config.yaml.bak.*"))
        assert len(backups) == 1
        assert backups[0].read_text() == original_content

    def test_commit_unknown_ext_passes(self, pipeline, tmp_path):
        target = tmp_path / "notes.md"
        result = pipeline.apply_change(ChangeProposal(
            source="agent",
            target_path=str(target),
            content="# Notes\nSome content\n",
            description="New notes file",
        ))
        assert result.committed
        assert target.exists()

    def test_commit_creates_parent_dirs(self, pipeline, tmp_path):
        target = tmp_path / "deep" / "nested" / "path" / "config.yaml"
        result = pipeline.apply_change(ChangeProposal(
            source="agent",
            target_path=str(target),
            content="key: value\n",
            description="Deep nested file",
        ))
        assert result.committed
        assert target.exists()

    def test_staging_cleaned_up_after_commit(self, pipeline, tmp_path):
        target = tmp_path / "test.yaml"
        pipeline.apply_change(ChangeProposal(
            source="agent",
            target_path=str(target),
            content="key: value\n",
        ))
        # Staging dir should have no leftover .yaml files
        staging_files = list(pipeline.staging_dir.glob("change_*"))
        assert len(staging_files) == 0


# ---------------------------------------------------------------------------
# Rollback
# ---------------------------------------------------------------------------

class TestRollback:
    def test_rollback_with_backup(self, pipeline, tmp_path):
        target = tmp_path / "config.yaml"
        target.write_text("original: true\n")

        # Apply change (creates backup)
        result = pipeline.apply_change(ChangeProposal(
            source="agent",
            target_path=str(target),
            content="modified: true\n",
        ))
        assert result.committed

        # Find backup
        backups = list(tmp_path.glob("config.yaml.bak.*"))
        assert len(backups) == 1

        # Rollback
        assert pipeline.rollback_change(str(target), str(backups[0]))
        assert target.read_text() == "original: true\n"
        # Backup should be consumed
        assert not backups[0].exists()

    def test_rollback_without_backup_deletes_file(self, pipeline, tmp_path):
        target = tmp_path / "created.yaml"
        target.write_text("new: file\n")

        assert pipeline.rollback_change(str(target))
        assert not target.exists()

    def test_rollback_nonexistent_file(self, pipeline, tmp_path):
        # Should not raise
        assert pipeline.rollback_change(str(tmp_path / "nonexistent.yaml"))


# ---------------------------------------------------------------------------
# raw_event recording
# ---------------------------------------------------------------------------

class TestRawEventRecording:
    @patch("agent.raw_event.record_raw_event")
    def test_commit_records_raw_event(self, mock_record, pipeline, tmp_path):
        mock_record.return_value = 42
        target = tmp_path / "test.yaml"

        result = pipeline.apply_change(ChangeProposal(
            source="skill_extractor",
            target_path=str(target),
            content="key: value\n",
            description="Test skill",
            metadata={"cluster_id": 5},
        ))

        assert result.committed
        assert result.raw_event_id == 42
        assert mock_record.called
        call_args = mock_record.call_args
        assert call_args[1]["tool_name"] == "self_modify"
        assert "committed" in call_args[1]["result"]

    @patch("agent.raw_event.record_raw_event")
    def test_rejection_records_raw_event(self, mock_record, pipeline, tmp_path):
        mock_record.return_value = 99
        target = tmp_path / "bad.yaml"

        result = pipeline.apply_change(ChangeProposal(
            source="agent",
            target_path=str(target),
            content="key: value: broken\n",
            description="Bad YAML",
        ))

        assert not result.committed
        assert result.raw_event_id == 99
        assert mock_record.called
        call_args = mock_record.call_args
        assert call_args[1]["tool_name"] == "self_modify"
        assert "rejected" in call_args[1]["result"]
        assert mock_record.call_args[1]["is_error"] is True

    @patch("agent.raw_event.record_raw_event")
    def test_rollback_records_raw_event(self, mock_record, pipeline, tmp_path):
        mock_record.return_value = 77
        target = tmp_path / "test.yaml"
        target.write_text("content\n")

        pipeline.rollback_change(str(target))

        assert mock_record.called
        call_args = mock_record.call_args
        assert call_args[1]["tool_name"] == "self_modify_rollback"
        assert "rolled back" in call_args[1]["result"]


# ---------------------------------------------------------------------------
# Convenience function
# ---------------------------------------------------------------------------

class TestApplyChangeConvenience:
    def test_apply_change_function(self, tmp_path, monkeypatch):
        # Patch the singleton
        mock_pipeline = MagicMock()
        mock_pipeline.apply_change.return_value = ChangeResult(
            committed=True,
            target_path=str(tmp_path / "test.yaml"),
        )
        monkeypatch.setattr("agent.emergent_change._pipeline", mock_pipeline)

        result = apply_change(
            source="agent",
            target_path=str(tmp_path / "test.yaml"),
            content="key: value\n",
            description="Test",
        )

        assert result.committed
        assert mock_pipeline.apply_change.called
        proposal = mock_pipeline.apply_change.call_args[0][0]
        assert proposal.source == "agent"
        assert proposal.description == "Test"


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_empty_content(self, pipeline, tmp_path):
        target = tmp_path / "empty.yaml"
        result = pipeline.apply_change(ChangeProposal(
            source="agent",
            target_path=str(target),
            content="",
            description="Empty file",
        ))
        # Empty string is valid YAML
        assert result.committed

    def test_unicode_content(self, pipeline, tmp_path):
        target = tmp_path / "unicode.yaml"
        result = pipeline.apply_change(ChangeProposal(
            source="agent",
            target_path=str(target),
            content="name: 翻译模块\ndescription: 用户文档翻译\n",
            description="Unicode YAML",
        ))
        assert result.committed
        assert "翻译" in target.read_text()

    def test_concurrent_changes_different_files(self, pipeline, tmp_path):
        target1 = tmp_path / "a.yaml"
        target2 = tmp_path / "b.yaml"

        r1 = pipeline.apply_change(ChangeProposal(
            source="agent", target_path=str(target1), content="a: 1\n"
        ))
        r2 = pipeline.apply_change(ChangeProposal(
            source="agent", target_path=str(target2), content="b: 2\n"
        ))

        assert r1.committed and r2.committed
        assert target1.exists() and target2.exists()

    def test_raw_event_failure_does_not_block_commit(self, pipeline, tmp_path):
        """If raw_event recording fails, the change should still be committed."""
        target = tmp_path / "test.yaml"

        with patch("agent.raw_event.record_raw_event", side_effect=Exception("DB error")):
            result = pipeline.apply_change(ChangeProposal(
                source="agent",
                target_path=str(target),
                content="key: value\n",
            ))

        # Change is committed even though raw_event failed
        assert result.committed
        assert target.exists()
        # But raw_event_id is None
        assert result.raw_event_id is None
