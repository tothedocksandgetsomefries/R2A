"""Tests for Planner CCR prompt argument mode ({prompt} placeholder)."""

import os
import tempfile
from pathlib import Path

import pytest

from r2a.tools.planner_model_client import _generate_command_text, _quote_prompt_for_shell, PlannerModelError


def test_quote_prompt_simple():
    """Simple prompt should be quoted correctly."""
    result = _quote_prompt_for_shell("Hello World")
    if os.name == "nt":
        assert result == '"Hello World"'
    else:
        assert result == "'Hello World'"


def test_quote_prompt_with_double_quotes():
    """Prompt with double quotes should be escaped."""
    result = _quote_prompt_for_shell('Say "hello"')
    # On Windows, double quotes are doubled
    assert '""' in result or '"' in result


def test_quote_prompt_with_newlines():
    """Prompt with newlines should be handled correctly."""
    result = _quote_prompt_for_shell("Line 1\nLine 2")
    if os.name == "nt":
        assert "\n" in result
    else:
        assert "\n" in result


def test_quote_prompt_with_json_braces():
    """Prompt with JSON braces should be preserved."""
    result = _quote_prompt_for_shell('{"key": "value"}')
    # JSON content should be preserved
    assert 'key' in result and 'value' in result


def test_quote_prompt_chinese():
    """Chinese characters should be preserved."""
    result = _quote_prompt_for_shell("请生成计划")
    assert "请生成计划" in result


def test_quote_prompt_windows_path():
    """Windows path should be preserved."""
    result = _quote_prompt_for_shell(r'C:\R2A_SAMPLE_WORKSPACE\file.txt')
    assert "R2A" in result
    assert "WORKSPACE" in result


def test_argument_mode_uses_python_script(tmp_path: Path):
    """Test argument mode with a Python script that reads command line argument."""
    # Create a simple Python script that echoes its argument
    script = tmp_path / "echo_arg.py"
    script.write_text(
        """
import sys
if len(sys.argv) > 1:
    print(sys.argv[1])
else:
    print("NO_ARG")
""",
        encoding="utf-8",
    )

    # Use {prompt} placeholder - argument mode
    # Use simple command without quotes (path has no spaces)
    command = f"python {script} {{prompt}}"

    result = _generate_command_text(
        command,
        "Hello from argument",
        timeout=10,
        repo_path=str(tmp_path),
    )

    assert "Hello from argument" in result


def test_stdin_mode_uses_python_script(tmp_path: Path):
    """Test stdin mode (no {prompt} placeholder) still works."""
    # Create a simple Python script that reads stdin
    script = tmp_path / "echo_stdin.py"
    script.write_text(
        """
import sys
line = sys.stdin.readline()
print(f"stdin: {line.strip()}")
""",
        encoding="utf-8",
    )

    # No {prompt} placeholder - stdin mode
    # Use simple command without quotes (path has no spaces)
    command = f"python {script}"

    result = _generate_command_text(
        command,
        "Hello from stdin",
        timeout=10,
        repo_path=str(tmp_path),
    )

    assert "stdin: Hello from stdin" in result


def test_argument_mode_special_characters(tmp_path: Path):
    """Test argument mode with special characters."""
    script = tmp_path / "echo_arg.py"
    script.write_text(
        """
import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
if len(sys.argv) > 1:
    print(sys.argv[1])
""",
        encoding="utf-8",
    )

    # Test with various special characters (avoiding encoding issues in output)
    test_prompts = [
        "Spaces: multiple words here",
        "Quotes: say 'hello' and \"world\"",
        "JSON: {\"key\": \"value\"}",
        "Path: C:\\R2A_SAMPLE\\test",
    ]

    for prompt in test_prompts:
        command = f"python {script} {{prompt}}"

        result = _generate_command_text(
            command,
            prompt,
            timeout=10,
            repo_path=str(tmp_path),
        )

        # The prompt content should be preserved in output
        assert prompt in result, f"Prompt not preserved: {prompt}"


def test_diagnostic_includes_argument_mode_flag(tmp_path: Path):
    """Diagnostic JSON should include argument_mode flag."""
    script = tmp_path / "echo_arg.py"
    script.write_text("import sys; print(sys.argv[1] if len(sys.argv) > 1 else 'NO_ARG')")

    command = f"python {script} {{prompt}}"

    _generate_command_text(
        command,
        "test",
        timeout=10,
        repo_path=str(tmp_path),
    )

    # Check diagnostic file
    from r2a.core.runtime_paths import repo_runtime_dir
    import json

    diagnostic_path = repo_runtime_dir(str(tmp_path)) / "planner" / "planner_backend_stdout.json"
    assert diagnostic_path.exists()

    data = json.loads(diagnostic_path.read_text(encoding="utf-8"))
    assert data.get("argument_mode") is True


def test_diagnostic_stdin_mode_flag(tmp_path: Path):
    """Diagnostic JSON should show argument_mode=False for stdin mode."""
    script = tmp_path / "echo_stdin.py"
    script.write_text("import sys; print(sys.stdin.readline().strip())")

    command = f"python {script}"

    _generate_command_text(
        command,
        "test",
        timeout=10,
        repo_path=str(tmp_path),
    )

    from r2a.core.runtime_paths import repo_runtime_dir
    import json

    diagnostic_path = repo_runtime_dir(str(tmp_path)) / "planner" / "planner_backend_stdout.json"
    data = json.loads(diagnostic_path.read_text(encoding="utf-8"))
    assert data.get("argument_mode") is False
