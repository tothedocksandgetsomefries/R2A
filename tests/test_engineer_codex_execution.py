from pathlib import Path

from r2a.agents.engineer_agent import run_engineer_agent
from r2a.core.paths import report_path
from r2a.core.state import make_initial_state
from r2a.tools.codex_runner import CodexRunResult


def test_engineer_codex_runs_purpose_based_task_spec(tmp_path: Path, monkeypatch) -> None:
    task_spec = report_path(tmp_path, "task")
    task_spec.parent.mkdir(parents=True)
    task_spec.write_text(
        "# TASK_SPEC\n\n"
        "## Purpose\n\nWrite a blocked status CSV.\n\n"
        "## Allowed Files\n\n- results/reproduction_status.csv\n\n"
        "## Forbidden Files\n\n- .r2a/TASK_SPEC.md\n\n"
        "## Acceptance Criteria\n\n- CSV exists.\n\n"
        "## Stop Conditions\n\n- Stop on unsafe changes.\n",
        encoding="utf-8",
    )
    captured = {}

    def fake_run(repo_path, task_spec_path, **kwargs):
        captured["task_spec_path"] = task_spec_path
        return CodexRunResult(
            command=["codex", "exec", "-"],
            returncode=0,
            stdout="done",
            stderr="",
            stdout_log_path=str(tmp_path / ".r2a" / "logs" / "codex_stdout.log"),
            stderr_log_path=str(tmp_path / ".r2a" / "logs" / "codex_stderr.log"),
            stdout_tail="done",
            stderr_tail="",
            attempted_executable="codex",
            skipped=False,
        )

    monkeypatch.setattr("r2a.agents.engineer_agent.run_codex_exec", fake_run)
    state = make_initial_state(tmp_path, executor="codex", engineer_executor="codex", auto_approve=True)

    result = run_engineer_agent(state)

    assert captured["task_spec_path"] == str(task_spec)
    text = Path(result["execution_report_path"]).read_text(encoding="utf-8")
    assert "codex exec -" in text
    assert "- skipped: False" in text


def test_engineer_codex_prefers_codex_stage_timeout(tmp_path: Path, monkeypatch) -> None:
    task_spec = report_path(tmp_path, "task")
    task_spec.parent.mkdir(parents=True)
    task_spec.write_text(
        "# TASK_SPEC\n\n"
        "## Goal\n\nRun test\n\n"
        "## Allowed Files\n\n- results/result.csv\n\n"
        "## Forbidden Files\n\n- .git/\n\n"
        "## Acceptance Criteria\n\n- CSV exists.\n\n"
        "## Stop Conditions\n\n- Stop on unsafe changes.\n",
        encoding="utf-8",
    )
    captured = {}

    def fake_run(repo_path, task_spec_path, **kwargs):
        captured.update(kwargs)
        return CodexRunResult(
            command=["codex", "exec", "-"],
            returncode=0,
            stdout="done",
            stderr="",
            stdout_log_path=str(tmp_path / ".r2a" / "logs" / "codex_stdout.log"),
            stderr_log_path=str(tmp_path / ".r2a" / "logs" / "codex_stderr.log"),
            stdout_tail="done",
            stderr_tail="",
            attempted_executable="codex",
        )

    monkeypatch.setattr("r2a.agents.engineer_agent.run_codex_exec", fake_run)
    state = make_initial_state(
        tmp_path,
        executor="codex",
        engineer_executor="codex",
        timeout=999,
        codex_stage_timeout=123,
        auto_approve=True,
    )

    run_engineer_agent(state)

    assert captured["timeout"] == 123


def test_engineer_codex_falls_back_to_legacy_timeout(tmp_path: Path, monkeypatch) -> None:
    task_spec = report_path(tmp_path, "task")
    task_spec.parent.mkdir(parents=True)
    task_spec.write_text(
        "# TASK_SPEC\n\n"
        "## Goal\n\nRun test\n\n"
        "## Allowed Files\n\n- results/result.csv\n\n"
        "## Forbidden Files\n\n- .git/\n\n"
        "## Acceptance Criteria\n\n- CSV exists.\n\n"
        "## Stop Conditions\n\n- Stop on unsafe changes.\n",
        encoding="utf-8",
    )
    captured = {}

    def fake_run(repo_path, task_spec_path, **kwargs):
        captured.update(kwargs)
        return CodexRunResult(
            command=["codex", "exec", "-"],
            returncode=0,
            stdout="done",
            stderr="",
            stdout_log_path=str(tmp_path / ".r2a" / "logs" / "codex_stdout.log"),
            stderr_log_path=str(tmp_path / ".r2a" / "logs" / "codex_stderr.log"),
            stdout_tail="done",
            stderr_tail="",
            attempted_executable="codex",
        )

    monkeypatch.setattr("r2a.agents.engineer_agent.run_codex_exec", fake_run)
    state = make_initial_state(tmp_path, executor="codex", engineer_executor="codex", timeout=456, auto_approve=True)
    state.pop("codex_stage_timeout", None)

    run_engineer_agent(state)

    assert captured["timeout"] == 456


def test_engineer_claude_runs_task_spec(tmp_path: Path, monkeypatch) -> None:
    task_spec = report_path(tmp_path, "task")
    task_spec.parent.mkdir(parents=True)
    task_spec.write_text(
        "# TASK_SPEC\n\n"
        "## Goal\n\nRun source verification\n\n"
        "## Allowed Files\n\n- .r2a/results/source_verification.csv\n\n"
        "## Forbidden Files\n\n- .git/\n\n"
        "## Acceptance Criteria\n\n- CSV exists.\n\n"
        "## Stop Conditions\n\n- Stop on unsafe changes.\n",
        encoding="utf-8",
    )
    captured = {}

    def fake_run(repo_path, task_spec_path, **kwargs):
        captured["task_spec_path"] = task_spec_path
        captured.update(kwargs)
        results_dir = tmp_path / ".r2a" / "results"
        results_dir.mkdir(parents=True, exist_ok=True)
        (results_dir / "source_verification.csv").write_text(
            "status,artifact_url,source_path,branch,commit,tag,readme_found,build_docs_found,experiment_scripts_found,data_scripts_found,notes\n"
            "OK,https://example.test/repo,repo,main,abc,,true,true,false,false,checked\n",
            encoding="utf-8",
        )
        return CodexRunResult(
            command=["claude", "--print"],
            returncode=0,
            stdout="done",
            stderr="",
            stdout_log_path=str(tmp_path / ".r2a" / "logs" / "claude_stdout.log"),
            stderr_log_path=str(tmp_path / ".r2a" / "logs" / "claude_stderr.log"),
            stdout_tail="done",
            stderr_tail="",
            attempted_executable="claude",
        )

    monkeypatch.setattr("r2a.agents.engineer_agent.run_claude_code_exec", fake_run)
    state = make_initial_state(
        tmp_path,
        executor="claude",
        engineer_executor="claude",
        claude_executable_path="C:/Tools/claude.cmd",
        auto_approve=True,
    )

    result = run_engineer_agent(state)

    assert captured["task_spec_path"] == str(task_spec)
    assert captured["claude_executable_path"] == "C:/Tools/claude.cmd"
    text = Path(result["execution_report_path"]).read_text(encoding="utf-8")
    assert "claude --print" in text
    assert "source_verification.csv" in text


def test_engineer_openclaw_runs_task_spec(tmp_path: Path, monkeypatch) -> None:
    task_spec = report_path(tmp_path, "task")
    task_spec.parent.mkdir(parents=True)
    task_spec.write_text(
        "# TASK_SPEC\n\n"
        "## Goal\n\nRun source verification\n\n"
        "## Allowed Files\n\n- .r2a/results/source_verification.csv\n\n"
        "## Forbidden Files\n\n- .git/\n\n"
        "## Acceptance Criteria\n\n- CSV exists.\n\n"
        "## Stop Conditions\n\n- Stop on unsafe changes.\n",
        encoding="utf-8",
    )
    captured = {}

    def fake_run(repo_path, stage, input_path, allowed_outputs, **kwargs):
        captured["stage"] = stage
        captured["input_path"] = input_path
        captured["allowed_outputs"] = allowed_outputs
        captured.update(kwargs)
        results_dir = tmp_path / ".r2a" / "results"
        results_dir.mkdir(parents=True, exist_ok=True)
        (results_dir / "source_verification.csv").write_text(
            "status,artifact_url,source_path,branch,commit,tag,readme_found,build_docs_found,experiment_scripts_found,data_scripts_found,notes\n"
            "OK,https://example.test/repo,repo,main,abc,,true,true,false,false,checked\n",
            encoding="utf-8",
        )
        (results_dir / "ENGINEER_DONE.txt").write_text("PASS\n", encoding="utf-8")
        stdout_log = tmp_path / ".r2a" / "logs" / "engineer_stdout.log"
        stderr_log = tmp_path / ".r2a" / "logs" / "engineer_stderr.log"
        stdout_log.parent.mkdir(parents=True, exist_ok=True)
        stdout_log.write_text("ok\n", encoding="utf-8")
        stderr_log.write_text("", encoding="utf-8")
        return {
            "success": True,
            "returncode": 0,
            "payload": '{"status":"PASS"}',
            "stdout_tail": '{"status":"PASS"}',
            "stderr_tail": "",
            "stdout_log_path": str(stdout_log),
            "stderr_log_path": str(stderr_log),
            "attempted_executable": "/home/r2auser/.nvm/versions/node/v22.22.2/bin/openclaw",
            "command": ["wsl", "-d", "Ubuntu", "--", "bash", "-lc", "setsid --wait bash wrapper.sh"],
            "failure_category": "",
            "execution_status": "",
            "timed_out": False,
        }

    monkeypatch.setattr("r2a.agents.engineer_agent.openclaw_stage_runner.run_openclaw_stage", fake_run)
    state = make_initial_state(tmp_path, executor="openclaw", engineer_executor="openclaw", auto_approve=True)

    result = run_engineer_agent(state)

    assert captured["stage"] == "engineer"
    assert captured["allowed_outputs"] == ["*"]
    assert captured["session_key"].startswith("r2a-engineer-1-")
    prompt_text = Path(captured["input_path"]).read_text(encoding="utf-8")
    assert "TASK_SPEC.md content" in prompt_text
    assert "Run source verification" in prompt_text
    assert "OpenClaw Engineer mode" in prompt_text
    assert "L4 canonical artifact closure checklist" in prompt_text
    assert "Do not use `reduced_experiment.csv` as a substitute for `reduced_metrics.csv`" in prompt_text
    assert result["engineer_status"] == "PASS"
    text = Path(result["execution_report_path"]).read_text(encoding="utf-8")
    assert "## Executor\n\nopenclaw" in text
    assert "source_verification.csv" in text


def test_engineer_claude_recovers_when_results_are_written_before_parse_error(tmp_path: Path, monkeypatch) -> None:
    task_spec = report_path(tmp_path, "task")
    task_spec.parent.mkdir(parents=True)
    task_spec.write_text(
        "# TASK_SPEC\n\n"
        "## Goal\n\nRun source verification\n\n"
        "## Allowed Files\n\n- .r2a/results/source_verification.csv\n\n"
        "## Forbidden Files\n\n- .git/\n\n"
        "## Acceptance Criteria\n\n- CSV exists.\n\n"
        "## Stop Conditions\n\n- Stop on unsafe changes.\n",
        encoding="utf-8",
    )

    def fake_run(repo_path, task_spec_path, **kwargs):
        results_dir = tmp_path / ".r2a" / "results"
        results_dir.mkdir(parents=True, exist_ok=True)
        (results_dir / "source_verification.csv").write_text(
            "status,artifact_url,source_path,branch,commit,tag,readme_found,build_docs_found,experiment_scripts_found,data_scripts_found,notes\n"
            "OK,https://example.test/repo,repo,main,abc,,true,true,false,false,checked\n",
            encoding="utf-8",
        )
        return CodexRunResult(
            command=["ccr", "code"],
            returncode=1,
            stdout="The model's tool call could not be parsed (retry also failed).",
            stderr="",
            stdout_log_path=str(tmp_path / ".r2a" / "logs" / "claude_stdout.log"),
            stderr_log_path=str(tmp_path / ".r2a" / "logs" / "claude_stderr.log"),
            stdout_tail="The model's tool call could not be parsed (retry also failed).",
            stderr_tail="",
            attempted_executable="ccr",
        )

    monkeypatch.setattr("r2a.agents.engineer_agent.run_claude_code_exec", fake_run)
    state = make_initial_state(tmp_path, executor="claude", engineer_executor="claude", auto_approve=True)

    result = run_engineer_agent(state)

    assert not result["errors"]
    text = Path(result["execution_report_path"]).read_text(encoding="utf-8")
    assert "- status: passed_with_warnings" in text
    assert "- exit_code: 1" in text
    assert "source_verification.csv" in text


def test_engineer_claude_timeout_recovers_when_runtime_made_progress(tmp_path: Path, monkeypatch) -> None:
    task_spec = report_path(tmp_path, "task")
    task_spec.parent.mkdir(parents=True)
    task_spec.write_text(
        "# TASK_SPEC\n\n"
        "## Goal\n\nRun build smoke\n\n"
        "## Allowed Files\n\n- .r2a/results/build_smoke.csv\n\n"
        "## Forbidden Files\n\n- .git/\n\n"
        "## Acceptance Criteria\n\n- CSV exists.\n\n"
        "## Stop Conditions\n\n- Stop on unsafe changes.\n",
        encoding="utf-8",
    )

    from r2a.tools.engineer_runtime import EngineerRuntimeResult, RuntimeCommand

    def fake_runtime(repo_path, **kwargs):
        results_dir = tmp_path / ".r2a" / "results"
        results_dir.mkdir(parents=True)
        build_csv = results_dir / "build_smoke.csv"
        build_csv.write_text(
            "status,command,exit_code,duration_sec,component,notes\n"
            "OK,cmake -S demo -B build,0,1.0,cmake_configure,configured\n",
            encoding="utf-8",
        )
        progress = results_dir / "engineer_progress.json"
        progress.write_text("{}", encoding="utf-8")
        return EngineerRuntimeResult(
            commands=[RuntimeCommand("cmake_configure", ["cmake", "-S", "demo"], 0, 1.0, "ok", "")],
            generated_files=[str(build_csv), str(progress)],
            successful_stages=["cmake_configure"],
            failed_stages=[],
        )

    def fake_run(repo_path, task_spec_path, **kwargs):
        return CodexRunResult(
            command=["ccr", "code"],
            returncode=124,
            stdout="",
            stderr="TimeoutExpired",
            stdout_log_path=str(tmp_path / ".r2a" / "logs" / "claude_stdout.log"),
            stderr_log_path=str(tmp_path / ".r2a" / "logs" / "claude_stderr.log"),
            stdout_tail="",
            stderr_tail="TimeoutExpired",
            attempted_executable="ccr",
            skipped=True,
        )

    monkeypatch.setattr("r2a.agents.engineer_agent.run_engineer_runtime", fake_runtime)
    monkeypatch.setattr("r2a.agents.engineer_agent.run_claude_code_exec", fake_run)
    state = make_initial_state(tmp_path, executor="claude", engineer_executor="claude", auto_approve=True)

    result = run_engineer_agent(state)

    assert not result["errors"]
    text = Path(result["execution_report_path"]).read_text(encoding="utf-8")
    assert "- status: passed_with_warnings" in text
    assert "- exit_code: 124" in text
    assert "runtime:cmake_configure" in text


def test_engineer_claude_auth_failure_does_not_recover_from_runtime_progress(tmp_path: Path, monkeypatch) -> None:
    task_spec = report_path(tmp_path, "task")
    task_spec.parent.mkdir(parents=True)
    task_spec.write_text(
        "# TASK_SPEC\n\n"
        "## Goal\n\nRun build smoke\n\n"
        "## Allowed Files\n\n- .r2a/results/build_smoke.csv\n\n"
        "## Forbidden Files\n\n- .git/\n\n"
        "## Acceptance Criteria\n\n- CSV exists.\n\n"
        "## Stop Conditions\n\n- Stop on unsafe changes.\n",
        encoding="utf-8",
    )

    from r2a.tools.engineer_runtime import EngineerRuntimeResult, RuntimeCommand

    def fake_runtime(repo_path, **kwargs):
        results_dir = tmp_path / ".r2a" / "results"
        results_dir.mkdir(parents=True)
        build_csv = results_dir / "build_smoke.csv"
        build_csv.write_text(
            "status,command,exit_code,duration_sec,component,notes\n"
            "BLOCKED,cmake configure,NA,0,cmake_configure,No CMakeLists.txt found.\n",
            encoding="utf-8",
        )
        return EngineerRuntimeResult(
            commands=[RuntimeCommand("dependency_check", ["python", "--version"], 0, 0.1, "ok", "")],
            generated_files=[str(build_csv)],
            successful_stages=["dependency_check"],
            failed_stages=["cmake_configure"],
        )

    def fake_run(repo_path, task_spec_path, **kwargs):
        return CodexRunResult(
            command=["claude", "--print"],
            returncode=1,
            stdout="Not logged in · Please run /login",
            stderr="",
            stdout_log_path=str(tmp_path / ".r2a" / "logs" / "claude_stdout.log"),
            stderr_log_path=str(tmp_path / ".r2a" / "logs" / "claude_stderr.log"),
            stdout_tail="Not logged in · Please run /login",
            stderr_tail="",
            attempted_executable="claude",
            backend_error={"is_backend_failure": True},
            is_backend_failure=True,
            transient_backend_failure=False,
            backend_failure_category="AUTHENTICATION_FAILURE",
            backend_failure_scope="BACKEND_AUTH_FAILURE",
            backend_user_message="Backend authentication failure",
        )

    monkeypatch.setattr("r2a.agents.engineer_agent.run_engineer_runtime", fake_runtime)
    monkeypatch.setattr("r2a.agents.engineer_agent.run_claude_code_exec", fake_run)
    state = make_initial_state(tmp_path, executor="claude", engineer_executor="claude", auto_approve=True, auto_iterate=True)

    result = run_engineer_agent(state)

    assert result["errors"]
    assert result["engineer_status"] == "FAIL"
    assert result["engineer_executor_unavailable"] is True
    assert result["auto_iterate"] is False
    assert result["stop_reason"] == "engineer_executor_unavailable"
    text = Path(result["execution_report_path"]).read_text(encoding="utf-8")
    assert "- status: failed" in text
    assert "- exit_code: 1" in text
    assert "will not auto-iterate" in text


def test_engineer_claude_timeout_does_not_recover_from_reused_runtime_only(tmp_path: Path, monkeypatch) -> None:
    task_spec = report_path(tmp_path, "task")
    task_spec.parent.mkdir(parents=True)
    task_spec.write_text(
        "# TASK_SPEC\n\n"
        "## Goal\n\nRun minimal fix\n\n"
        "## Allowed Files\n\n- .r2a/results/reduced_metrics.csv\n\n"
        "## Forbidden Files\n\n- .git/\n\n"
        "## Acceptance Criteria\n\n- CSV exists.\n\n"
        "## Stop Conditions\n\n- Stop on unsafe changes.\n",
        encoding="utf-8",
    )

    from r2a.tools.engineer_runtime import EngineerRuntimeResult, RuntimeCommand

    def fake_runtime(repo_path, **kwargs):
        return EngineerRuntimeResult(
            commands=[RuntimeCommand("cmake_configure", ["reuse", "build_r2a"], 0, 0, "reused", "")],
            generated_files=[],
            successful_stages=["cmake_configure"],
            failed_stages=[],
        )

    def fake_run(repo_path, task_spec_path, **kwargs):
        return CodexRunResult(
            command=["ccr", "code"],
            returncode=124,
            stdout="",
            stderr="TimeoutExpired",
            stdout_log_path=str(tmp_path / ".r2a" / "logs" / "claude_stdout.log"),
            stderr_log_path=str(tmp_path / ".r2a" / "logs" / "claude_stderr.log"),
            stdout_tail="",
            stderr_tail="TimeoutExpired",
            attempted_executable="ccr",
            skipped=True,
        )

    monkeypatch.setattr("r2a.agents.engineer_agent.run_engineer_runtime", fake_runtime)
    monkeypatch.setattr("r2a.agents.engineer_agent.run_claude_code_exec", fake_run)
    state = make_initial_state(tmp_path, executor="claude", engineer_executor="claude", auto_approve=True)

    result = run_engineer_agent(state)

    assert result["errors"]
    text = Path(result["execution_report_path"]).read_text(encoding="utf-8")
    assert "- status: failed" in text
    assert "- exit_code: 124" in text
    assert "runtime:cmake_configure: reuse build_r2a" in text
