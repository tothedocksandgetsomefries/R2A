"""Tests for readiness_gate._missing_python_script source_root resolution."""

import json
import tempfile
from pathlib import Path

import pytest

from r2a.tools.readiness_gate import _missing_python_script, _get_source_root


class TestGetSourceRoot:
    """Tests for _get_source_root path resolution."""

    def test_returns_local_path_from_source_acquisition(self, tmp_path: Path) -> None:
        """When SOURCE_ACQUISITION.json has local_path, use it."""
        # Setup
        source_dir = tmp_path / ".r2a" / "artifacts" / "source"
        source_dir.mkdir(parents=True)
        (source_dir / "test.py").write_text("# test")

        acquisition_file = tmp_path / ".r2a" / "SOURCE_ACQUISITION.json"
        acquisition_file.parent.mkdir(parents=True, exist_ok=True)
        acquisition_file.write_text(json.dumps({
            "schema_version": 2,
            "source_status": "available",
            "local_path": str(source_dir),
        }))

        # Execute
        result = _get_source_root({}, tmp_path)

        # Verify
        assert result == source_dir.resolve()

    def test_fallback_to_artifacts_source_when_no_local_path(self, tmp_path: Path) -> None:
        """When SOURCE_ACQUISITION.json missing local_path, fallback to repo/.r2a/artifacts/source."""
        # Setup
        source_dir = tmp_path / ".r2a" / "artifacts" / "source"
        source_dir.mkdir(parents=True)
        (source_dir / "test.py").write_text("# test")

        # No SOURCE_ACQUISITION.json or empty
        acquisition_file = tmp_path / ".r2a" / "SOURCE_ACQUISITION.json"
        acquisition_file.parent.mkdir(parents=True, exist_ok=True)
        acquisition_file.write_text(json.dumps({"schema_version": 2}))

        # Execute
        result = _get_source_root({}, tmp_path)

        # Verify
        assert result == source_dir.resolve()

    def test_fallback_to_repo_root_when_no_source_dir(self, tmp_path: Path) -> None:
        """When no source directory exists, fallback to repo root."""
        # Execute
        result = _get_source_root({}, tmp_path)

        # Verify
        assert result == tmp_path.resolve()


class TestMissingPythonScript:
    """Tests for _missing_python_script with source_root."""

    def test_finds_script_in_source_root(self, tmp_path: Path) -> None:
        """When script exists in source_root, return empty string."""
        # Setup
        source_dir = tmp_path / ".r2a" / "artifacts" / "source"
        source_dir.mkdir(parents=True)
        (source_dir / "benchmark.py").write_text("# benchmark")

        # Execute
        result = _missing_python_script(
            "python benchmark.py --help",
            repo=tmp_path,
            source_root=source_dir
        )

        # Verify - script exists, should return empty
        assert result == ""

    def test_reports_missing_script_not_in_source_root(self, tmp_path: Path) -> None:
        """When script does not exist in source_root, return script name."""
        # Setup
        source_dir = tmp_path / ".r2a" / "artifacts" / "source"
        source_dir.mkdir(parents=True)
        # benchmark.py does NOT exist here

        # Execute
        result = _missing_python_script(
            "python benchmark.py --help",
            repo=tmp_path,
            source_root=source_dir
        )

        # Verify - script missing, should return script name
        assert result == "benchmark.py"

    def test_does_not_check_repo_root_when_source_root_given(self, tmp_path: Path) -> None:
        """When source_root is provided, do NOT check repo root."""
        # Setup - script exists in repo root but NOT in source_root
        source_dir = tmp_path / ".r2a" / "artifacts" / "source"
        source_dir.mkdir(parents=True)
        # No benchmark.py in source_dir

        (tmp_path / "benchmark.py").write_text("# benchmark in wrong place")

        # Execute
        result = _missing_python_script(
            "python benchmark.py --help",
            repo=tmp_path,
            source_root=source_dir
        )

        # Verify - should report missing because we check source_root, not repo
        assert result == "benchmark.py"

    def test_backward_compatible_when_source_root_none(self, tmp_path: Path) -> None:
        """When source_root is None, check repo root (backward compatible)."""
        # Setup - script exists in repo root
        (tmp_path / "benchmark.py").write_text("# benchmark")

        # Execute
        result = _missing_python_script(
            "python benchmark.py --help",
            repo=tmp_path,
            source_root=None
        )

        # Verify
        assert result == ""

    def test_handles_subdirectory_scripts(self, tmp_path: Path) -> None:
        """Scripts in subdirectories relative to source_root are found."""
        # Setup
        source_dir = tmp_path / ".r2a" / "artifacts" / "source"
        scripts_dir = source_dir / "scripts"
        scripts_dir.mkdir(parents=True)
        (scripts_dir / "run.py").write_text("# run")

        # Execute
        result = _missing_python_script(
            "python scripts/run.py",
            repo=tmp_path,
            source_root=source_dir
        )

        # Verify
        assert result == ""

    def test_handles_path_traversal_escape(self, tmp_path: Path) -> None:
        """Path traversal attempts outside source_root are treated as missing."""
        # Setup
        source_dir = tmp_path / ".r2a" / "artifacts" / "source"
        source_dir.mkdir(parents=True)

        # Create a file outside source_root
        outside_file = tmp_path / "outside_fixture.py"
        outside_file.write_text("# outside fixture")

        # Execute - try to reference file outside source_root
        result = _missing_python_script(
            "python ../outside_fixture.py",
            repo=tmp_path,
            source_root=source_dir
        )

        # Verify - should report missing because path escapes source_root
        assert result == "../outside_fixture.py"

    def test_no_python_script_in_action(self, tmp_path: Path) -> None:
        """When action has no python command, return empty string."""
        result = _missing_python_script(
            "make all",
            repo=tmp_path,
            source_root=None
        )
        assert result == ""


class TestIntegration:
    """Integration tests with actual directory structure."""

    def test_real_world_fanns_scenario(self, tmp_path: Path) -> None:
        """Test the actual FANNS benchmark scenario that was failing."""
        # Setup - simulate the real structure
        repo = tmp_path
        source_dir = repo / ".r2a" / "artifacts" / "source"
        source_dir.mkdir(parents=True)

        # Create the actual files that exist
        (source_dir / "benchmark.py").write_text("# benchmark")
        (source_dir / "README.md").write_text("# FANNS Benchmark")

        # Create SOURCE_ACQUISITION.json
        acquisition = repo / ".r2a" / "SOURCE_ACQUISITION.json"
        acquisition.parent.mkdir(parents=True, exist_ok=True)
        acquisition.write_text(json.dumps({
            "schema_version": 2,
            "source_status": "available",
            "local_path": str(source_dir),
        }))

        # Execute - this is the action from TASK_SPEC that was failing
        source_root = _get_source_root({}, repo)
        result = _missing_python_script(
            "python benchmark.py --help",
            repo=repo,
            source_root=source_root
        )

        # Verify - should NOT report missing
        assert result == "", f"benchmark.py should be found in {source_dir}"
