import json
import os
from pathlib import Path

from r2a.tools.openclaw_stage_runner import (
    DEFAULT_OPENCLAW_AGENT,
    DEFAULT_OPENCLAW_EXECUTABLE,
    _cleanup_new_unexpected_modifications,
    _openclaw_config_read_candidates,
    _openclaw_wsl_command,
    detect_openclaw_model_profiles,
    openclaw_stage_profile,
    preflight_openclaw_stage,
    resolve_openclaw_config,
    run_openclaw_stage,
    test_openclaw_configuration as run_openclaw_configuration_test,
)
from r2a.tools.process_manager import create_run_record, workflow_run_context
from r2a.tools.process_tree import ProcessResult


def test_resolve_openclaw_config_uses_defaults_and_env(monkeypatch) -> None:
    monkeypatch.delenv("R2A_OPENCLAW_EXECUTABLE_PATH", raising=False)
    monkeypatch.delenv("R2A_OPENCLAW_PROVIDER", raising=False)
    monkeypatch.delenv("R2A_OPENCLAW_MODEL", raising=False)
    monkeypatch.delenv("R2A_OPENCLAW_RUNNER", raising=False)
    monkeypatch.delenv("R2A_OPENCLAW_AGENT", raising=False)
    config = resolve_openclaw_config()
    assert config["openclaw_executable_path"] == DEFAULT_OPENCLAW_EXECUTABLE
    assert config["provider"] == "ai-coding-plan"
    assert config["model"] == "glm-5"
    assert config["runner"] == "embedded"
    assert config["agent"] == DEFAULT_OPENCLAW_AGENT
    assert openclaw_stage_profile("engineer")["provider"] == "deepseek"
    assert resolve_openclaw_config(stage="engineer")["model"] == "deepseek-chat"

    monkeypatch.setenv("R2A_OPENCLAW_EXECUTABLE_PATH", "/opt/openclaw-env")
    monkeypatch.setenv("R2A_OPENCLAW_PROVIDER", "glm")
    monkeypatch.setenv("R2A_OPENCLAW_MODEL", "glm-5")
    monkeypatch.setenv("R2A_OPENCLAW_RUNNER", "gateway")
    monkeypatch.setenv("R2A_OPENCLAW_AGENT", "paper-lab")

    env_config = resolve_openclaw_config(stage="engineer")
    assert env_config["openclaw_executable_path"] == "/opt/openclaw-env"
    assert env_config["provider"] == "glm"
    assert env_config["model"] == "glm-5"
    assert env_config["runner"] == "gateway"
    assert env_config["agent"] == "paper-lab"
    assert resolve_openclaw_config(provider="deepseek")["provider"] == "deepseek"
    assert resolve_openclaw_config(agent="r2a-override")["agent"] == "r2a-override"


def test_detect_openclaw_model_profiles_does_not_invent_static_fallback(tmp_path: Path) -> None:
    missing_config = tmp_path / "missing-openclaw.json"

    detected = detect_openclaw_model_profiles(openclaw_config_path=str(missing_config))

    assert detected["ok"] is False
    assert detected["source"] == "not_detected"
    assert detected["models"] == []
    assert detected["warnings"]
    assert "config path not found" in detected["warnings"][0]


def test_detect_openclaw_model_profiles_parses_real_openclaw_provider_models(tmp_path: Path) -> None:
    config_path = tmp_path / "openclaw.json"
    config_path.write_text(
        json.dumps(
            {
                "agents": {
                    "defaults": {
                        "models": {
                            "deepseek/deepseek-chat": {"alias": "DeepSeek"},
                            "ai-coding-plan/glm-5": {"alias": "GLM-5"},
                        },
                        "model": {"primary": "ai-coding-plan/glm-5"},
                    },
                    "list": [],
                },
                "models": {
                    "providers": {
                        "deepseek": {
                            "models": [
                                {"id": "deepseek-chat", "name": "DeepSeek Chat"},
                                {"id": "deepseek-reasoner", "name": "DeepSeek Reasoner"},
                            ]
                        },
                        "ai-coding-plan": {
                            "models": [
                                {"id": "glm-5", "name": "glm-5"},
                            ]
                        },
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    detected = detect_openclaw_model_profiles(openclaw_config_path=str(config_path))
    model_ids = {(item["provider"], item["model"], item["profile"]) for item in detected["models"]}

    assert detected["ok"] is True
    assert detected["source"] == "openclaw_config"
    assert detected["config_read_path"] == str(config_path)
    assert ("ai-coding-plan", "glm-5", "default") in model_ids
    assert ("deepseek", "deepseek-chat", "default") in model_ids
    assert ("deepseek", "deepseek-reasoner", "config") in model_ids
    assert not any(item["model"].startswith("{") for item in detected["models"])


def test_detect_openclaw_model_profiles_reports_no_entries(tmp_path: Path) -> None:
    config_path = tmp_path / "openclaw.json"
    config_path.write_text(json.dumps({"models": {"providers": {}}}), encoding="utf-8")

    detected = detect_openclaw_model_profiles(openclaw_config_path=str(config_path))

    assert detected["ok"] is False
    assert detected["models"] == []
    assert detected["warnings"] == ["OpenClaw config readable but no model entries found"]


def test_test_openclaw_configuration_detects_mock_executable_and_config(tmp_path: Path) -> None:
    executable = tmp_path / "openclaw.cmd"
    executable.write_text("@echo off\n", encoding="utf-8")
    config_path = tmp_path / "openclaw.json"
    config_path.write_text(
        json.dumps({"agents": {"defaults": {"models": {"mock-provider/mock-model": {}}}}}),
        encoding="utf-8",
    )

    result = run_openclaw_configuration_test(
        openclaw_executable_path=str(executable),
        openclaw_config_path=str(config_path),
        provider="mock-provider",
        model="mock-model",
        profile="default",
    )

    assert result["success"] is True
    assert result["executable_path"] == str(executable)
    assert result["config_read_path"] == str(config_path)
    assert result["provider"] == "mock-provider"
    assert result["model"] == "mock-model"


def test_test_openclaw_configuration_reports_missing_executable(tmp_path: Path) -> None:
    config_path = tmp_path / "openclaw.json"
    config_path.write_text(
        json.dumps({"agents": {"defaults": {"models": {"mock-provider/mock-model": {}}}}}),
        encoding="utf-8",
    )

    result = run_openclaw_configuration_test(
        openclaw_executable_path=str(tmp_path / "missing-openclaw.cmd"),
        openclaw_config_path=str(config_path),
    )

    assert result["success"] is False
    assert "OpenClaw executable not found" in result["error_message"]


def test_test_openclaw_configuration_reports_missing_config(tmp_path: Path) -> None:
    executable = tmp_path / "openclaw.cmd"
    executable.write_text("@echo off\n", encoding="utf-8")

    result = run_openclaw_configuration_test(
        openclaw_executable_path=str(executable),
        openclaw_config_path=str(tmp_path / "missing-openclaw.json"),
    )

    assert result["success"] is False
    assert "OpenClaw config path not found" in result["error_message"]


def test_test_openclaw_configuration_reports_unsupported_config_format(tmp_path: Path) -> None:
    executable = tmp_path / "openclaw.cmd"
    executable.write_text("@echo off\n", encoding="utf-8")
    config_path = tmp_path / "openclaw.json"
    config_path.write_text(json.dumps(["unsupported"]), encoding="utf-8")

    result = run_openclaw_configuration_test(
        openclaw_executable_path=str(executable),
        openclaw_config_path=str(config_path),
    )

    assert result["success"] is False
    assert "Detected config format unsupported" in result["error_message"]


def test_test_openclaw_configuration_warns_when_saved_default_not_detected(tmp_path: Path) -> None:
    executable = tmp_path / "openclaw.cmd"
    executable.write_text("@echo off\n", encoding="utf-8")
    config_path = tmp_path / "openclaw.json"
    config_path.write_text(
        json.dumps({"agents": {"defaults": {"models": {"mock-provider/mock-model": {}}}}}),
        encoding="utf-8",
    )

    result = run_openclaw_configuration_test(
        openclaw_executable_path=str(executable),
        openclaw_config_path=str(config_path),
        provider="missing-provider",
        model="missing-model",
        profile="default",
    )

    assert result["success"] is True
    assert "Saved default profile not detected" in result["warnings"]


def test_openclaw_config_read_candidates_translate_wsl_paths(monkeypatch) -> None:
    monkeypatch.setattr("r2a.tools.openclaw_stage_runner.os.name", "nt")
    monkeypatch.setenv("R2A_WSL_DISTRO", "Ubuntu")

    candidates = _openclaw_config_read_candidates("/home/r2auser/.openclaw/openclaw.json")

    assert candidates[0] == "/home/r2auser/.openclaw/openclaw.json"
    assert "\\\\wsl.localhost\\Ubuntu\\home\\r2auser\\.openclaw\\openclaw.json" in candidates
    assert "\\\\wsl$\\Ubuntu\\home\\r2auser\\.openclaw\\openclaw.json" in candidates


def test_cleanup_removes_only_new_unexpected_files(tmp_path: Path) -> None:
    results = tmp_path / ".r2a" / "results"
    results.mkdir(parents=True)
    new_file = results / "project_tests.csv"
    existing_file = results / "source_verification.csv"
    new_file.write_text("new unauthorized\n", encoding="utf-8")
    existing_file.write_text("existing content\n", encoding="utf-8")

    cleanup = _cleanup_new_unexpected_modifications(
        tmp_path,
        {
            "unexpected_modifications": [
                ".r2a/results/project_tests.csv",
                ".r2a/results/source_verification.csv",
            ],
            "new_dirty_files": [".r2a/results/project_tests.csv"],
        },
    )

    assert cleanup["cleaned"] == [".r2a/results/project_tests.csv"]
    assert cleanup["uncleaned"] == [".r2a/results/source_verification.csv"]
    assert not new_file.exists()
    assert existing_file.read_text(encoding="utf-8") == "existing content\n"


def test_preflight_reports_provider_model_errors(tmp_path: Path, monkeypatch) -> None:
    def fake_run(command, **kwargs):
        return ProcessResult(
            returncode=2,
            stdout=json.dumps(
                {
                    "ok": False,
                    "stage": "planner",
                    "agent": "default",
                    "provider": "missing-provider",
                    "model": "missing-model",
                    "runner": "embedded",
                    "failure_category": "OPENCLAW_PROVIDER_NOT_FOUND",
                    "errors": ["OPENCLAW_PROVIDER_NOT_FOUND", "OPENCLAW_MODEL_NOT_AVAILABLE"],
                    "available_providers": ["ai-coding-plan", "deepseek"],
                    "available_models": ["ai-coding-plan/glm-5", "deepseek/deepseek-chat"],
                }
            ),
            stderr="",
        )

    monkeypatch.setattr("r2a.tools.openclaw_stage_runner.run_command_with_timeout", fake_run)

    result = preflight_openclaw_stage(
        "planner",
        executable="/opt/openclaw",
        provider="missing-provider",
        model="missing-model",
        runner="embedded",
        agent="",
        wsl_distro="Ubuntu",
        openclaw_config_path="/home/r2auser/.openclaw/openclaw.json",
        repo_path=tmp_path,
    )

    assert result["ok"] is False
    assert result["failure_category"] == "OPENCLAW_PROVIDER_NOT_FOUND"
    assert "OPENCLAW_MODEL_NOT_AVAILABLE" in result["errors"]


def test_openclaw_stage_preflight_failure_is_structured(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    input_path = repo / "OPENCLAW_INPUT.md"
    input_path.write_text("Return JSON.\n", encoding="utf-8")
    monkeypatch.setattr(
        "r2a.tools.openclaw_stage_runner.preflight_openclaw_stage",
        lambda *args, **kwargs: {
            "ok": False,
            "stage": "planner",
            "agent": "default",
            "provider": "missing-provider",
            "model": "missing-model",
            "runner": "embedded",
            "executable": "/opt/openclaw",
            "config_path": "/home/r2auser/.openclaw/openclaw.json",
            "failure_category": "OPENCLAW_PROVIDER_NOT_FOUND",
            "errors": ["OPENCLAW_PROVIDER_NOT_FOUND"],
            "returncode": 2,
        },
    )

    result = run_openclaw_stage(
        repo,
        "planner",
        input_path,
        [".r2a/staging/planner/iter_001/attempt_001/PLANNER_OUTPUT.json"],
        session_key="r2a-test-key",
        provider="missing-provider",
        model="missing-model",
    )

    assert result["success"] is False
    assert result["failure_category"] == "OPENCLAW_PROVIDER_NOT_FOUND"
    assert "OPENCLAW_PROVIDER_NOT_FOUND" in result["stderr_tail"]


def test_openclaw_wsl_command_uses_isolated_wsl_process_group(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setenv("R2A_RUNTIME_ROOT", str(tmp_path / "runtime-root"))
    create_run_record(repo, "run-openclaw", status="running", wsl_distro="Ubuntu")

    with workflow_run_context(repo, "run-openclaw", wsl_distro="Ubuntu"):
        command = _openclaw_wsl_command(
            ["/opt/openclaw", "agent", "--json"],
            cwd=repo,
            distro="Ubuntu",
            stdout_path=repo / ".r2a" / "logs" / "stdout.json",
            stderr_path=repo / ".r2a" / "logs" / "stderr.log",
            wrapper_path=repo / ".r2a" / "logs" / "openclaw_reviewer_wrapper.sh",
        )
        pgid_file = os.environ.get("R2A_WSL_PGID_FILE", "")

    script = command[-1].replace("\\", "/")
    wrapper_text = (repo / ".r2a" / "logs" / "openclaw_reviewer_wrapper.sh").read_text(encoding="utf-8")
    wrapper_bytes = (repo / ".r2a" / "logs" / "openclaw_reviewer_wrapper.sh").read_bytes()
    assert command[:4] == ["wsl", "-d", "Ubuntu", "--"]
    assert "setsid --wait bash /mnt/" in script
    assert "ps -o pgid= -p \"$$\"" in wrapper_text
    assert "exec /opt/openclaw agent --json" in wrapper_text
    assert "run-openclaw.wsl.pgid" in wrapper_text
    assert pgid_file.endswith("run-openclaw.wsl.pgid")
    assert b"\r\n" not in wrapper_bytes


def test_openclaw_stage_preserves_runtime_env_for_wsl_pgid_registration(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.delenv("R2A_OPENCLAW_AGENT", raising=False)
    input_path = repo / "OPENCLAW_INPUT.md"
    input_path.write_text("Return JSON.\n", encoding="utf-8")
    monkeypatch.setenv("R2A_RUNTIME_ROOT", str(tmp_path / "runtime-root"))
    create_run_record(repo, "run-openclaw", status="running", wsl_distro="Ubuntu")

    monkeypatch.setattr("r2a.tools.openclaw_stage_runner.snapshot_stage_changes", lambda repo_path: set())
    monkeypatch.setattr(
        "r2a.tools.openclaw_stage_runner.check_stage_allowed_modifications",
        lambda repo_path, stage, allowed, baseline=None: {
            "stage": stage,
            "allowed_patterns": allowed,
            "changed_files": [],
            "stage_changed_files": [],
            "unexpected_modifications": [],
            "ok": True,
            "guard_available": True,
            "error": "",
            "warning": "",
        },
    )
    captured: dict[str, object] = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["env"] = kwargs.get("env")
        raw_stdout = repo / ".r2a" / "logs" / "openclaw_reviewer_raw_stdout.json"
        raw_stdout.parent.mkdir(parents=True, exist_ok=True)
        raw_stdout.write_text(
            json.dumps(
                {
                        "payloads": [{"text": '{"status":"PASS"}'}],
                        "meta": {
                            "agentMeta": {"provider": "ai-coding-plan", "model": "glm-5", "sessionId": "sid-1"},
                        "executionTrace": {"runner": "embedded", "transport": "local", "fallbackUsed": False},
                        "systemPromptReport": {"sessionKey": "r2a-test-key"},
                        "usage": {"prompt_tokens": 7, "completion_tokens": 5, "total_tokens": 12},
                    },
                }
            ),
            encoding="utf-8",
        )
        return ProcessResult(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("r2a.tools.openclaw_stage_runner.run_command_with_timeout", fake_run)
    monkeypatch.setattr(
        "r2a.tools.openclaw_stage_runner.preflight_openclaw_stage",
        lambda *args, **kwargs: {
            "ok": True,
            "stage": "reviewer",
            "agent": "default",
            "provider": "ai-coding-plan",
            "model": "glm-5",
            "runner": "embedded",
            "config_path": "/home/r2auser/.openclaw/openclaw.json",
            "config_path_runtime": "/home/r2auser/.openclaw/openclaw.json",
        },
    )
    cleanup_calls: list[dict[str, str] | None] = []
    monkeypatch.setattr("r2a.tools.openclaw_stage_runner._kill_registered_wsl_group", lambda env=None: cleanup_calls.append(env))

    with workflow_run_context(repo, "run-openclaw", wsl_distro="Ubuntu"):
        result = run_openclaw_stage(
            repo,
            "reviewer",
            input_path,
            [".r2a/staging/reviewer/iter_001/attempt_001/REVIEW_REPORT.md"],
            session_key="r2a-test-key",
            env={"OPENCLAW_TEST_ENV": "kept"},
        )

    runtime_env = captured["env"]
    assert isinstance(runtime_env, dict)
    assert runtime_env["OPENCLAW_TEST_ENV"] == "kept"
    assert runtime_env["R2A_RUN_ID"] == "run-openclaw"
    assert runtime_env["R2A_WSL_DISTRO"] == "Ubuntu"
    assert runtime_env["R2A_WSL_PGID_FILE"].endswith("run-openclaw.wsl.pgid")
    assert cleanup_calls == [runtime_env]
    wrapper_text = (repo / ".r2a" / "logs" / "openclaw_reviewer_wrapper.sh").read_text(encoding="utf-8")
    assert "--agent" not in wrapper_text
    assert result["success"] is True
    assert result["provider"] == "ai-coding-plan"
    assert result["model"] == "glm-5"
    assert result["runner"] == "embedded"
    assert result["configured_provider"] == "ai-coding-plan"
    assert result["configured_model"] == "glm-5"
    assert result["configured_runner"] == "embedded"
    assert result["configured_agent"] == ""
    assert result["openclaw_config"]["provider"] == "ai-coding-plan"
    assert result["openclaw_config"]["agent"] == ""
    assert result["token_usage"] == {"prompt_tokens": 7, "completion_tokens": 5, "total_tokens": 12}
    invocation_manifest = Path(str(result["invocation_manifest_path"]))
    assert invocation_manifest.exists()
    invocation = json.loads(invocation_manifest.read_text(encoding="utf-8"))
    assert invocation["invocation_id"] == result["invocation_id"]
    assert invocation["stage"] == "reviewer"
    assert invocation["session_key"] == "r2a-test-key"
    assert invocation["actual_session_key"] == "r2a-test-key"
    assert invocation["provider"] == "ai-coding-plan"
    assert invocation["token_usage"]["total_tokens"] == 12
    assert invocation["preflight_config_path"] == "/home/r2auser/.openclaw/openclaw.json"
    assert invocation["runtime_config_path"] == "/home/r2auser/.openclaw/openclaw.json"
    assert invocation["wrapper_passes_config_path"] is False
    assert invocation["uses_openclaw_default_config_discovery"] is True
    assert Path(invocation["copied_logs"]["raw_stdout"]).exists()
    assert Path(invocation["copied_logs"]["stdout_log"]).exists()


def test_openclaw_stage_detects_provider_error_payload(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    input_path = repo / "OPENCLAW_INPUT.md"
    input_path.write_text("Return JSON.\n", encoding="utf-8")
    monkeypatch.setattr("r2a.tools.openclaw_stage_runner.snapshot_stage_changes", lambda repo_path: set())
    monkeypatch.setattr(
        "r2a.tools.openclaw_stage_runner.check_stage_allowed_modifications",
        lambda repo_path, stage, allowed, baseline=None: {
            "stage": stage,
            "allowed_patterns": allowed,
            "changed_files": [],
            "stage_changed_files": [],
            "unexpected_modifications": [],
            "ok": True,
            "guard_available": True,
            "error": "",
            "warning": "",
        },
    )
    monkeypatch.setattr(
        "r2a.tools.openclaw_stage_runner.preflight_openclaw_stage",
        lambda *args, **kwargs: {
            "ok": True,
            "stage": "planner",
            "agent": "default",
            "provider": "ai-coding-plan",
            "model": "glm-5",
            "runner": "embedded",
        },
    )

    def fake_run(command, **kwargs):
        raw_stdout = repo / ".r2a" / "logs" / "openclaw_planner_raw_stdout.json"
        raw_stdout.parent.mkdir(parents=True, exist_ok=True)
        raw_stdout.write_text(
            json.dumps(
                {
                    "payloads": [
                        {
                            "text": "Xunfei request failed with Sid: abc code: 10050, msg: Unknown description",
                        }
                    ],
                    "meta": {
                        "agentMeta": {"provider": "ai-coding-plan", "model": "glm-5", "sessionId": "sid-1"},
                        "executionTrace": {"runner": "embedded", "transport": "local", "fallbackUsed": False},
                        "systemPromptReport": {"sessionKey": "r2a-test-key"},
                    },
                }
            ),
            encoding="utf-8",
        )
        return ProcessResult(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("r2a.tools.openclaw_stage_runner.run_command_with_timeout", fake_run)

    result = run_openclaw_stage(
        repo,
        "planner",
        input_path,
        [".r2a/staging/planner/iter_001/attempt_001/PLANNER_OUTPUT.json"],
        session_key="r2a-test-key",
    )

    assert result["success"] is False
    assert result["failure_category"] == "PLANNER_BACKEND_FAILURE"
    assert "code: 10050" in result["provider_error"]
