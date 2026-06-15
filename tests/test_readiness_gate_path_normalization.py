"""Tests for PLACEHOLDER_TASK source path normalization fix.

This module tests the _missing_python_script function and its ability to
correctly handle repo-relative source artifact paths.

The bug was:
- source_root = C:\\...\\repo\\.r2a\\artifacts\\source
- script from action = .r2a/artifacts/source/benchmark.py
- old logic: source_root + script = wrong path (double prefix)
- fix: normalize script by stripping .r2a/artifacts/source/ prefix
"""

import tempfile
from pathlib import Path

import pytest

from r2a.tools.readiness_gate import _missing_python_script, _normalize_source_script_path


class TestNormalizeSourceScriptPath:
    """Tests for the _normalize_source_script_path helper."""

    def test_repo_relative_unix_path_is_stripped(self, tmp_path: Path) -> None:
        """Unix-style repo-relative source path should be normalized."""
        source_root = tmp_path / ".r2a" / "artifacts" / "source"
        script = ".r2a/artifacts/source/benchmark.py"

        result = _normalize_source_script_path(script, source_root)

        assert result == "benchmark.py"

    def test_repo_relative_windows_path_is_stripped(self, tmp_path: Path) -> None:
        """Windows-style repo-relative source path should be normalized."""
        source_root = tmp_path / ".r2a" / "artifacts" / "source"
        script = ".r2a\\artifacts\\source\\benchmark.py"

        result = _normalize_source_script_path(script, source_root)

        assert result == "benchmark.py"

    def test_repo_relative_with_dot_slash_prefix(self, tmp_path: Path) -> None:
        """Path with ./ prefix should also be normalized."""
        source_root = tmp_path / ".r2a" / "artifacts" / "source"
        script = "./.r2a/artifacts/source/benchmark.py"

        result = _normalize_source_script_path(script, source_root)

        assert result == "benchmark.py"

    def test_source_root_relative_path_unchanged(self, tmp_path: Path) -> None:
        """source_root-relative path should pass through unchanged."""
        source_root = tmp_path / ".r2a" / "artifacts" / "source"
        script = "benchmark.py"

        result = _normalize_source_script_path(script, source_root)

        assert result == "benchmark.py"

    def test_nested_script_path_stripped(self, tmp_path: Path) -> None:
        """Nested script paths should also be normalized."""
        source_root = tmp_path / ".r2a" / "artifacts" / "source"
        script = ".r2a/artifacts/source/subdir/script.py"

        result = _normalize_source_script_path(script, source_root)

        assert result == "subdir/script.py"

    def test_nested_source_root_relative_path_unchanged(self, tmp_path: Path) -> None:
        """Nested source_root-relative paths should pass through."""
        source_root = tmp_path / ".r2a" / "artifacts" / "source"
        script = "subdir/script.py"

        result = _normalize_source_script_path(script, source_root)

        assert result == "subdir/script.py"


class TestMissingPythonScript:
    """Tests for the _missing_python_script function."""

    def test_source_root_relative_script_exists(self, tmp_path: Path) -> None:
        """Source root-relative script that exists should not be reported missing."""
        source_root = tmp_path / ".r2a" / "artifacts" / "source"
        source_root.mkdir(parents=True, exist_ok=True)

        # Create the script
        script_path = source_root / "benchmark.py"
        script_path.write_text("# test script", encoding="utf-8")

        action = "python3 benchmark.py --help"

        result = _missing_python_script(action, tmp_path, source_root)

        assert result == ""

    def test_repo_relative_script_exists_is_normalized(self, tmp_path: Path) -> None:
        """Repo-relative source artifact path should be normalized and found."""
        source_root = tmp_path / ".r2a" / "artifacts" / "source"
        source_root.mkdir(parents=True, exist_ok=True)

        # Create the script at source_root
        script_path = source_root / "benchmark.py"
        script_path.write_text("# test script", encoding="utf-8")

        # Planner writes repo-relative path
        action = "python3 .r2a/artifacts/source/benchmark.py --help"

        result = _missing_python_script(action, tmp_path, source_root)

        # Should be normalized and found
        assert result == ""

    def test_windows_style_repo_relative_script_is_normalized(self, tmp_path: Path) -> None:
        """Windows-style repo-relative path should be normalized."""
        source_root = tmp_path / ".r2a" / "artifacts" / "source"
        source_root.mkdir(parents=True, exist_ok=True)

        # Create the script
        script_path = source_root / "benchmark.py"
        script_path.write_text("# test script", encoding="utf-8")

        # Planner writes Windows-style repo-relative path
        action = "python3 .r2a\\artifacts\\source\\benchmark.py --help"

        result = _missing_python_script(action, tmp_path, source_root)

        assert result == ""

    def test_nonexistent_script_still_reported_missing(self, tmp_path: Path) -> None:
        """Script that truly doesn't exist should still be reported missing."""
        source_root = tmp_path / ".r2a" / "artifacts" / "source"
        source_root.mkdir(parents=True, exist_ok=True)

        # No script created
        action = "python3 nonexistent.py --help"

        result = _missing_python_script(action, tmp_path, source_root)

        assert result == "nonexistent.py"

    def test_nonexistent_repo_relative_script_reported_missing(self, tmp_path: Path) -> None:
        """Repo-relative path to nonexistent script should be reported missing."""
        source_root = tmp_path / ".r2a" / "artifacts" / "source"
        source_root.mkdir(parents=True, exist_ok=True)

        # No script created
        action = "python3 .r2a/artifacts/source/nonexistent.py --help"

        result = _missing_python_script(action, tmp_path, source_root)

        # Should still be reported as missing (the script path, not empty)
        assert result != ""

    def test_nested_repo_relative_script_normalized(self, tmp_path: Path) -> None:
        """Nested repo-relative script path should be normalized correctly."""
        source_root = tmp_path / ".r2a" / "artifacts" / "source"
        source_root.mkdir(parents=True, exist_ok=True)

        # Create nested directory and script
        subdir = source_root / "subdir"
        subdir.mkdir(parents=True, exist_ok=True)
        script_path = subdir / "script.py"
        script_path.write_text("# test script", encoding="utf-8")

        action = "python3 .r2a/artifacts/source/subdir/script.py --help"

        result = _missing_python_script(action, tmp_path, source_root)

        assert result == ""

    def test_absolute_path_script_exists(self, tmp_path: Path) -> None:
        """Absolute path script should be checked directly."""
        source_root = tmp_path / ".r2a" / "artifacts" / "source"
        source_root.mkdir(parents=True, exist_ok=True)

        # Create script at a different location
        other_dir = tmp_path / "other"
        other_dir.mkdir(parents=True, exist_ok=True)
        script_path = other_dir / "script.py"
        script_path.write_text("# test script", encoding="utf-8")

        # Use absolute path
        action = f"python3 {script_path} --help"

        result = _missing_python_script(action, tmp_path, source_root)

        assert result == ""

    def test_absolute_path_script_missing(self, tmp_path: Path) -> None:
        """Absolute path to nonexistent script should be reported missing."""
        source_root = tmp_path / ".r2a" / "artifacts" / "source"
        source_root.mkdir(parents=True, exist_ok=True)

        nonexistent = tmp_path / "nonexistent.py"
        action = f"python3 {nonexistent} --help"

        result = _missing_python_script(action, tmp_path, source_root)

        assert result != ""

    def test_no_python_script_in_action(self, tmp_path: Path) -> None:
        """Action without python script should return empty string."""
        source_root = tmp_path / ".r2a" / "artifacts" / "source"
        source_root.mkdir(parents=True, exist_ok=True)

        action = "echo 'hello'"

        result = _missing_python_script(action, tmp_path, source_root)

        assert result == ""

    def test_source_root_none_uses_repo(self, tmp_path: Path) -> None:
        """When source_root is None, should use repo as effective root."""
        # Create script at repo root
        script_path = tmp_path / "script.py"
        script_path.write_text("# test script", encoding="utf-8")

        action = "python3 script.py --help"

        result = _missing_python_script(action, tmp_path, None)

        assert result == ""


class TestRun6Scenario:
    """Test the exact scenario from Run 6 that triggered the bug."""

    def test_fanns_benchmark_py_repo_relative_path_normalized(self, tmp_path: Path) -> None:
        """Reproduce Run 6 scenario: benchmark.py with repo-relative path."""
        # Simulate the actual directory structure
        source_root = tmp_path / ".r2a" / "artifacts" / "source"
        source_root.mkdir(parents=True, exist_ok=True)

        # Create benchmark.py at source_root (like in real Run)
        benchmark_path = source_root / "benchmark.py"
        benchmark_path.write_text("#!/usr/bin/env python3\n# FANNS benchmark\n", encoding="utf-8")

        # The action from TASK_SPEC that triggered the bug
        action = "python3 .r2a/artifacts/source/benchmark.py --help"

        result = _missing_python_script(action, tmp_path, source_root)

        # Should NOT be reported as missing after the fix
        assert result == "", f"Expected benchmark.py to be found, but got: {result}"

    def test_fanns_benchmark_py_source_root_relative_found(self, tmp_path: Path) -> None:
        """If Planner uses correct source_root-relative path, it should also work."""
        source_root = tmp_path / ".r2a" / "artifacts" / "source"
        source_root.mkdir(parents=True, exist_ok=True)

        benchmark_path = source_root / "benchmark.py"
        benchmark_path.write_text("#!/usr/bin/env python3\n# FANNS benchmark\n", encoding="utf-8")

        # Correct path (relative to source_root)
        action = "python3 benchmark.py --help"

        result = _missing_python_script(action, tmp_path, source_root)

        assert result == ""
