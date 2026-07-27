"""Test: import validation in EmergentChangePipeline.

Verifies that:
1. A valid .py file passes import validation and is committed
2. A .py file with syntax error is rejected and rolled back
3. A .py file with import error (missing dependency) is rejected and rolled back
4. A non-.py file (e.g., .yaml) skips import validation
5. Rollback restores the previous version correctly
"""

import os
import sys
import tempfile
import pytest
from pathlib import Path
from unittest.mock import patch

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from agent.emergent_change import (
    EmergentChangePipeline,
    ChangeProposal,
    ChangeResult,
    get_pipeline,
)


@pytest.fixture
def tmp_pipeline(tmp_path):
    """Create a pipeline with a temporary HERMES_HOME."""
    old_home = os.environ.get("HERMES_HOME")
    os.environ["HERMES_HOME"] = str(tmp_path)
    pipeline = EmergentChangePipeline(hermes_home=str(tmp_path))
    yield pipeline, tmp_path
    if old_home:
        os.environ["HERMES_HOME"] = old_home
    else:
        os.environ.pop("HERMES_HOME", None)


class TestImportValidation:
    """Test the _validate_import method directly."""

    def test_valid_python_passes(self, tmp_pipeline):
        pipeline, tmp = tmp_pipeline
        target = tmp / "valid_module.py"
        target.write_text("x = 42\n\ndef hello():\n    return 'world'\n")
        result = pipeline._validate_import(target)
        assert result is None, f"Expected None, got: {result}"

    def test_syntax_error_caught(self, tmp_pipeline):
        pipeline, tmp = tmp_pipeline
        target = tmp / "bad_syntax.py"
        target.write_text("def broken(\n")  # syntax error
        result = pipeline._validate_import(target)
        assert result is not None
        assert "SyntaxError" in result

    def test_import_error_caught(self, tmp_pipeline):
        pipeline, tmp = tmp_pipeline
        target = tmp / "bad_import.py"
        target.write_text("import nonexistent_package_xyz_123\n")
        result = pipeline._validate_import(target)
        assert result is not None
        assert "ImportError" in result

    def test_runtime_error_caught(self, tmp_pipeline):
        pipeline, tmp = tmp_pipeline
        target = tmp / "bad_runtime.py"
        target.write_text("x = undefined_variable\n")
        result = pipeline._validate_import(target)
        assert result is not None
        assert "NameError" in result

    def test_non_py_file_skipped(self, tmp_pipeline):
        pipeline, tmp = tmp_pipeline
        target = tmp / "config.yaml"
        target.write_text("key: value\n")
        result = pipeline._validate_import(target)
        assert result is None  # non-.py always passes


class TestApplyChangeWithImportValidation:
    """Test that apply_change integrates import validation correctly."""

    def test_valid_py_committed(self, tmp_pipeline):
        pipeline, tmp = tmp_pipeline
        target = tmp / "good_module.py"
        target.write_text("# original\n")  # original content

        proposal = ChangeProposal(
            source="test",
            target_path=str(target),
            content="x = 1\n",
            initiator="user",
        )
        result = pipeline.apply_change(proposal, force=True)
        assert result.committed is True
        assert target.read_text() == "x = 1\n"

    def test_syntax_error_rolled_back(self, tmp_pipeline):
        pipeline, tmp = tmp_pipeline
        target = tmp / "module.py"
        original = "# original working code\n"
        target.write_text(original)

        # Use code that passes syntax check but fails at runtime (NameError)
        # Pure syntax errors are caught by _validate_file_format already,
        # so we test import validation with a runtime error instead.
        proposal = ChangeProposal(
            source="test",
            target_path=str(target),
            content="x = undefined_variable_name\n",
            initiator="user",
        )
        result = pipeline.apply_change(proposal, force=True)
        assert result.committed is False
        assert "import_validation_failed" in result.error or "Import validation" in result.error
        # Verify rollback: file should still have original content
        assert target.read_text() == original

    def test_import_error_rolled_back(self, tmp_pipeline):
        pipeline, tmp = tmp_pipeline
        target = tmp / "module.py"
        original = "# original\n"
        target.write_text(original)

        proposal = ChangeProposal(
            source="test",
            target_path=str(target),
            content="import nonexistent_xyz_123\n",
            initiator="user",
        )
        result = pipeline.apply_change(proposal, force=True)
        assert result.committed is False
        assert "ImportError" in result.error
        assert target.read_text() == original

    def test_new_file_creation_rolled_back(self, tmp_pipeline):
        """If a newly created .py file fails import, it should be deleted (no backup)."""
        pipeline, tmp = tmp_pipeline
        target = tmp / "new_module.py"
        # Don't create the file first — it's a new creation

        proposal = ChangeProposal(
            source="test",
            target_path=str(target),
            content="import nonexistent_xyz_456\n",
            initiator="user",
        )
        result = pipeline.apply_change(proposal, force=True)
        assert result.committed is False
        assert not target.exists(), "File should have been deleted on rollback (no backup)"

    def test_yaml_file_not_import_validated(self, tmp_pipeline):
        pipeline, tmp = tmp_pipeline
        target = tmp / "config.yaml"
        target.write_text("old: value\n")

        proposal = ChangeProposal(
            source="test",
            target_path=str(target),
            content="new: value\n",
            initiator="user",
        )
        result = pipeline.apply_change(proposal, force=True)
        assert result.committed is True
        assert target.read_text() == "new: value\n"


class TestRollbackMechanism:
    """Test the _do_rollback helper."""

    def test_rollback_restores_backup(self, tmp_pipeline):
        pipeline, tmp = tmp_pipeline
        target = tmp / "module.py"
        original = "# original\n"
        target.write_text(original)

        # Create a backup
        backup = str(target) + ".bak.test"
        import shutil
        shutil.copy2(str(target), backup)

        # Corrupt the file
        target.write_text("# corrupted\n")

        # Rollback
        pipeline._do_rollback(target, backup)
        assert target.read_text() == original

    def test_rollback_deletes_when_no_backup(self, tmp_pipeline):
        pipeline, tmp = tmp_pipeline
        target = tmp / "new_module.py"
        target.write_text("# should be deleted\n")

        pipeline._do_rollback(target, None)
        assert not target.exists()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
