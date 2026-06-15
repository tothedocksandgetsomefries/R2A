"""Tests for Planner command integration with Run-level process termination."""
from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path

import pytest


def test_planner_command_normal_execution(tmp_path, monkeypatch) -> None:
    """Planner command backend can execute and return stdout."""
    from r2a.tools.planner_model_client import _generate_command_text

    # Use echo command (safe, fast)
    if os.name == "nt":
        command = "echo test_output"
    else:
        command = "echo test_output"

    result = _generate_command_text(command, "test_prompt", timeout=10, repo_path=str(tmp_path))

    assert "test_output" in result


def test_planner_command_timeout(tmp_path) -> None:
    """Planner command timeout terminates the process."""
    from r2a.tools.planner_model_client import _generate_command_text, PlannerModelError

    # Use a long sleep command
    if os.name == "nt":
        command = "ping -n 30 127.0.0.1"  # ~30 seconds
    else:
        command = "sleep 30"

    with pytest.raises(PlannerModelError) as exc_info:
        _generate_command_text(command, "", timeout=2, repo_path=str(tmp_path))

    # Should fail due to timeout (returncode != 0)
    assert "returned" in str(exc_info.value) or "TimeoutExpired" in str(exc_info.value)


def test_planner_command_cancel(tmp_path, monkeypatch) -> None:
    """Planner command can be cancelled via Run-level termination."""
    import subprocess
    from r2a.tools.process_manager import (
        create_run_record,
        terminate_run,
        read_run_record,
    )
    from r2a.tools.planner_model_client import _generate_command_text, PlannerModelError

    # Set up Run context
    run_id = "test_planner_cancel"
    repo_path = tmp_path

    create_run_record(repo_path, run_id, status="running")

    # Set environment for process registration
    monkeypatch.setenv("R2A_RUN_ID", run_id)
    monkeypatch.setenv("R2A_REPO_PATH", str(repo_path))

    # Start a long-running command in a thread
    import threading
    result_holder = {"exception": None, "completed": False}

    def run_planner_command():
        try:
            if os.name == "nt":
                command = "ping -n 30 127.0.0.1"
            else:
                command = "sleep 30"
            _generate_command_text(command, "", timeout=30, repo_path=str(repo_path))
        except PlannerModelError as e:
            result_holder["exception"] = e
        finally:
            result_holder["completed"] = True

    thread = threading.Thread(target=run_planner_command)
    thread.start()

    # Wait a moment for the process to start
    time.sleep(2)

    # Check if process was registered
    record = read_run_record(repo_path, run_id)
    processes = record.get("windows_processes", [])

    # Terminate the run
    terminate_run(repo_path, run_id, force=True, wait_seconds=3)

    # Wait for thread to complete
    thread.join(timeout=10)

    # Verify the command was interrupted
    assert result_holder["completed"]
    if result_holder["exception"]:
        # Should have a non-zero returncode (130 for cancel, or other)
        assert "returned" in str(result_holder["exception"])


def test_planner_template_backend_unaffected(tmp_path, monkeypatch) -> None:
    """Template backend is not affected by command changes."""
    from r2a.tools.planner_model_client import call_planner_model

    bundle = {
        "repo_path": str(tmp_path),
        "iteration": 1,
        "paper_bundle": {},
        "structured_review_feedback": {},
    }

    result = call_planner_model(bundle, backend="template", timeout=10)

    # Should return valid planner output
    assert isinstance(result, dict)
    assert "schema_version" in result


def test_planner_mock_backend_unaffected(tmp_path, monkeypatch) -> None:
    """Mock backend is not affected by command changes."""
    from r2a.tools.planner_model_client import call_planner_model

    bundle = {
        "repo_path": str(tmp_path),
        "iteration": 1,
        "paper_bundle": {},
        "structured_review_feedback": {},
    }

    result = call_planner_model(bundle, backend="mock", timeout=10)

    # Should return valid planner output
    assert isinstance(result, dict)
    assert "schema_version" in result
