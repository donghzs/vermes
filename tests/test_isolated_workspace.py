"""Tests for agent.isolated_workspace — staging area for safe modifications.

Run: python -m pytest tests/test_isolated_workspace.py -v
"""

import os
import tempfile
import textwrap
import pytest

from agent.isolated_workspace import (
    IsolatedWorkspace,
    StagingArea,
    _files_differ,
    _matches_glob,
    DEFAULT_EXCLUDES,
)


@pytest.fixture
def source_project():
    """Create a minimal source project for testing."""
    with tempfile.TemporaryDirectory() as src:
        # Create some files
        with open(os.path.join(src, "main.py"), "w") as f:
            f.write("def main():\n    return 'hello'\n")

        os.makedirs(os.path.join(src, "utils"))
        with open(os.path.join(src, "utils", "helper.py"), "w") as f:
            f.write("def help():\n    return 'help'\n")

        # Create __pycache__ (should be excluded)
        os.makedirs(os.path.join(src, "__pycache__"))
        with open(os.path.join(src, "__pycache__", "main.cpython.pyc"), "w") as f:
            f.write("binary junk")

        # Create .git (should be excluded)
        os.makedirs(os.path.join(src, ".git"))
        with open(os.path.join(src, ".git", "config"), "w") as f:
            f.write("[core]")

        yield src


# ---------------------------------------------------------------------------
# Helper function tests
# ---------------------------------------------------------------------------


class TestFilesDiffer:
    def test_identical_files(self, tmp_path):
        f1 = tmp_path / "a.txt"
        f2 = tmp_path / "b.txt"
        f1.write_text("same content")
        f2.write_text("same content")
        assert _files_differ(str(f1), str(f2)) is False

    def test_different_files(self, tmp_path):
        f1 = tmp_path / "a.txt"
        f2 = tmp_path / "b.txt"
        f1.write_text("content A")
        f2.write_text("content B")
        assert _files_differ(str(f1), str(f2)) is True

    def test_missing_file(self, tmp_path):
        f1 = tmp_path / "exists.txt"
        f1.write_text("content")
        assert _files_differ(str(f1), str(tmp_path / "missing.txt")) is True


class TestMatchesGlob:
    def test_exact_match(self):
        assert _matches_glob("__pycache__", frozenset({"__pycache__"})) is True

    def test_glob_match(self):
        assert _matches_glob("mypkg.egg-info", frozenset({"*.egg-info"})) is True

    def test_no_match(self):
        assert _matches_glob("main.py", frozenset({"__pycache__"})) is False


# ---------------------------------------------------------------------------
# StagingArea tests
# ---------------------------------------------------------------------------


class TestStagingArea:
    def test_path_property(self, tmp_path):
        staging = StagingArea(
            source_path="/source",
            staging_path=str(tmp_path),
        )
        assert staging.path == str(tmp_path)

    def test_get_file(self, tmp_path):
        staging = StagingArea(
            source_path="/source",
            staging_path=str(tmp_path),
        )
        assert staging.get_file("main.py") == os.path.join(str(tmp_path), "main.py")

    def test_cleanup(self, tmp_path):
        staging_dir = tempfile.mkdtemp(prefix="test_staging_")
        staging = StagingArea(source_path="/source", staging_path=staging_dir)
        staging.cleanup()
        assert not os.path.exists(staging_dir)

    def test_list_changed_files_no_changes(self, source_project):
        ws = IsolatedWorkspace(source_project)
        staging = ws.begin()
        changed = staging.list_changed_files()
        assert len(changed) == 0
        ws.rollback(staging)

    def test_list_changed_files_with_modification(self, source_project):
        ws = IsolatedWorkspace(source_project)
        staging = ws.begin()
        # Modify a file in staging
        with open(staging.get_file("main.py"), "w") as f:
            f.write("def main():\n    return 'modified'\n")
        changed = staging.list_changed_files()
        assert "main.py" in changed
        ws.rollback(staging)

    def test_list_changed_files_new_file(self, source_project):
        ws = IsolatedWorkspace(source_project)
        staging = ws.begin()
        # Add a new file in staging
        with open(staging.get_file("new_file.py"), "w") as f:
            f.write("# new file\n")
        changed = staging.list_changed_files()
        assert "new_file.py" in changed
        ws.rollback(staging)


# ---------------------------------------------------------------------------
# IsolatedWorkspace tests
# ---------------------------------------------------------------------------


class TestIsolatedWorkspace:
    def test_begin_creates_staging(self, source_project):
        ws = IsolatedWorkspace(source_project)
        staging = ws.begin()
        assert os.path.isdir(staging.path)
        # Check files are copied
        assert os.path.exists(staging.get_file("main.py"))
        assert os.path.exists(os.path.join(staging.path, "utils", "helper.py"))
        ws.rollback(staging)

    def test_begin_excludes_pycache(self, source_project):
        ws = IsolatedWorkspace(source_project)
        staging = ws.begin()
        assert not os.path.exists(staging.get_file("__pycache__"))
        ws.rollback(staging)

    def test_begin_excludes_git(self, source_project):
        ws = IsolatedWorkspace(source_project)
        staging = ws.begin()
        assert not os.path.exists(staging.get_file(".git"))
        ws.rollback(staging)

    def test_begin_nonexistent_source(self):
        ws = IsolatedWorkspace("/nonexistent/path")
        with pytest.raises(FileNotFoundError):
            ws.begin()

    def test_verify_passes(self, source_project):
        ws = IsolatedWorkspace(source_project)
        staging = ws.begin()
        result = ws.verify(staging, lambda path: True)
        assert result is True
        ws.rollback(staging)

    def test_verify_fails(self, source_project):
        ws = IsolatedWorkspace(source_project)
        staging = ws.begin()
        result = ws.verify(staging, lambda path: False)
        assert result is False
        ws.rollback(staging)

    def test_verify_exception_returns_false(self, source_project):
        ws = IsolatedWorkspace(source_project)
        staging = ws.begin()

        def bad_verify(path):
            raise RuntimeError("boom")

        result = ws.verify(staging, bad_verify)
        assert result is False
        ws.rollback(staging)

    def test_commit_applies_changes(self, source_project):
        ws = IsolatedWorkspace(source_project)
        staging = ws.begin()

        # Modify file in staging
        with open(staging.get_file("main.py"), "w") as f:
            f.write("def main():\n    return 'committed'\n")

        result = ws.commit(staging)
        assert result is True

        # Verify source was updated
        with open(os.path.join(source_project, "main.py")) as f:
            assert "committed" in f.read()

    def test_commit_no_changes(self, source_project):
        ws = IsolatedWorkspace(source_project)
        staging = ws.begin()
        result = ws.commit(staging)
        assert result is False

    def test_commit_new_file(self, source_project):
        ws = IsolatedWorkspace(source_project)
        staging = ws.begin()

        with open(staging.get_file("new_module.py"), "w") as f:
            f.write("# new module\n")

        result = ws.commit(staging)
        assert result is True
        assert os.path.exists(os.path.join(source_project, "new_module.py"))

    def test_rollback_discards_changes(self, source_project):
        ws = IsolatedWorkspace(source_project)
        staging = ws.begin()

        with open(staging.get_file("main.py"), "w") as f:
            f.write("def main():\n    return 'discarded'\n")

        ws.rollback(staging)

        # Source should be unchanged
        with open(os.path.join(source_project, "main.py")) as f:
            assert "hello" in f.read()

        # Staging directory should be cleaned up
        assert not os.path.exists(staging.path)

    def test_cleanup_all(self, source_project):
        ws = IsolatedWorkspace(source_project)
        staging1 = ws.begin()
        staging2 = ws.begin()
        ws.cleanup_all()
        assert not os.path.exists(staging1.path)
        assert not os.path.exists(staging2.path)

    def test_context_manager_success(self, source_project):
        ws = IsolatedWorkspace(source_project)
        with ws as staging:
            with open(staging.get_file("main.py"), "w") as f:
                f.write("def main():\n    return 'ctx_success'\n")

        # Changes should be committed
        with open(os.path.join(source_project, "main.py")) as f:
            assert "ctx_success" in f.read()

    def test_context_manager_exception(self, source_project):
        ws = IsolatedWorkspace(source_project)
        original_content = open(os.path.join(source_project, "main.py")).read()

        with pytest.raises(RuntimeError):
            with ws as staging:
                with open(staging.get_file("main.py"), "w") as f:
                    f.write("def main():\n    return 'ctx_fail'\n")
                raise RuntimeError("test error")

        # Changes should be rolled back
        with open(os.path.join(source_project, "main.py")) as f:
            assert f.read() == original_content

    def test_source_not_modified_during_staging(self, source_project):
        """Ensure modifications in staging don't affect source until commit."""
        ws = IsolatedWorkspace(source_project)
        staging = ws.begin()

        with open(staging.get_file("main.py"), "w") as f:
            f.write("MODIFIED IN STAGING\n")

        # Source should still have original content
        with open(os.path.join(source_project, "main.py")) as f:
            assert "hello" in f.read()

        ws.rollback(staging)
