def test_web_app_import_is_side_effect_safe() -> None:
    import r2a_web.app as app

    assert callable(app.read_iteration_history)
    assert callable(app.read_report)
    assert app.DEFAULT_PAPER_BACKEND == "openclaw_reader"
    assert app.DEFAULT_PLANNER_BACKEND == "openclaw"
    assert app.DEFAULT_ENGINEER_EXECUTOR == "openclaw"
    assert app.DEFAULT_MANAGER_BACKEND == "openclaw_review"
    assert app.DEFAULT_REVIEWER_BACKEND == "openclaw"
    assert app.DEFAULT_FINAL_WRITER_BACKEND == "openclaw"
    report_keys = [key for _, key in app.REPORTS]
    assert "paper_reproduction_card" in report_keys
    assert "paper_figures_tables" in report_keys
    assert "paper_parse_quality" in report_keys


def test_web_network_scope_ui_copy_uses_friendly_label_and_advanced_raw_scope() -> None:
    import r2a_web.app as app

    copy = app._network_scope_ui_copy()

    assert copy["toggle_label"] == "允许有限网络获取算法依赖"
    assert copy["advanced_label"] == "Advanced network scope"
    assert copy["raw_label"] == "allowed_network_scope"
    assert copy["default_scope"] == app.DEFAULT_ALLOWED_NETWORK_SCOPE
    assert "does not authorize full datasets" in copy["toggle_help"]


def test_web_approval_diagnostics_model_reads_planner_transaction(tmp_path) -> None:
    import json
    import r2a_web.app as app

    logs = tmp_path / ".r2a" / "logs"
    logs.mkdir(parents=True)
    (tmp_path / ".r2a" / "TASK_SPEC.md").write_text("# TASK_SPEC\n", encoding="utf-8")
    (tmp_path / ".r2a" / "EXPERIMENT_CONTRACT.md").write_text("# EXPERIMENT_CONTRACT\n", encoding="utf-8")
    (logs / "planner_transaction.json").write_text(
        json.dumps(
            {
                "staging_dir": str(tmp_path / ".r2a" / "staging" / "planner" / "iter_001" / "attempt_001"),
                "committed": True,
                "validation_status": "PASS",
                "diagnostic": {
                    "staging_task_spec_written": True,
                    "staging_experiment_contract_written": True,
                    "planner_validation_passed": True,
                    "planner_committed": True,
                    "approval_passed": False,
                    "failure_category": "",
                    "failure_reason": "",
                    "is_claude_ccr_call_problem": False,
                },
            }
        ),
        encoding="utf-8",
    )

    diagnostics = app._approval_diagnostics_model(tmp_path, {"stopped": True, "stop_reason": "human_approval_rejected"})

    assert diagnostics["Planner validation passed"] == "yes"
    assert diagnostics["Planner committed"] == "yes"
    assert diagnostics["Approval passed"] == "no"
    assert diagnostics["Approval rejected reason"] == "human_approval_rejected"
    assert diagnostics["Engineer skipped reason"] == "Skipped due to approval gate"


def test_web_approval_diagnostics_model_reports_claude_problem(tmp_path) -> None:
    import json
    import r2a_web.app as app

    logs = tmp_path / ".r2a" / "logs"
    logs.mkdir(parents=True)
    (logs / "planner_transaction.json").write_text(
        json.dumps(
            {
                "committed": False,
                "validation_status": "FAIL",
                "failure_category": "TOOL_CALL_PARSE_FAILURE",
                "issues": ["tool call parse retry failed"],
                "diagnostic": {
                    "planner_validation_passed": False,
                    "planner_committed": False,
                    "approval_passed": False,
                    "failure_category": "TOOL_CALL_PARSE_FAILURE",
                    "is_claude_ccr_call_problem": True,
                },
            }
        ),
        encoding="utf-8",
    )

    diagnostics = app._approval_diagnostics_model(tmp_path, {"stopped": True, "stop_reason": "planner_stage_failed"})

    assert diagnostics["Planner validation passed"] == "no"
    assert diagnostics["Planner committed"] == "no"
    assert diagnostics["Is Claude/CCR call problem"] == "yes"
    assert diagnostics["Failure category"] == "TOOL_CALL_PARSE_FAILURE"


def test_web_preflight_allows_empty_repo_for_codex_discovery(tmp_path, monkeypatch) -> None:
    import r2a_web.app as app
    from r2a.tools.codex_cli import CodexCliCheckResult

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".r2a").mkdir()
    monkeypatch.setattr("r2a_web.app.check_codex_cli", lambda path: CodexCliCheckResult(True, path, path, "codex 1.0", "", "ok"))

    error = app._workflow_preflight(
        {"repo_path": str(repo)},
        paper_backend="preprocess",
        planner_backend="template",
        engineer_executor="codex",
        manager_backend="codex_review",
        reviewer_backend="codex",
        codex_executable_path="C:/Tools/codex.exe",
    )

    assert error == ""


def test_web_preflight_blocks_unrunnable_codex(tmp_path, monkeypatch) -> None:
    import r2a_web.app as app
    from r2a.tools.codex_cli import CodexCliCheckResult

    monkeypatch.setattr(
        "r2a_web.app.check_codex_cli",
        lambda path: CodexCliCheckResult(False, path, path, "", "PermissionError: Access is denied", "WindowsApps protected path; use codex.cmd."),
    )

    message = app._workflow_preflight(
        {"repo_path": str(tmp_path)},
        paper_backend="preprocess",
        planner_backend="template",
        engineer_executor="codex",
        manager_backend="codex_review",
        reviewer_backend="codex",
        codex_executable_path="C:/Program Files/WindowsApps/codex.exe",
    )

    assert "Access is denied" in message
    assert "codex.cmd" in message


def test_web_preflight_blocks_unconfigured_claude_planner(tmp_path, monkeypatch) -> None:
    import r2a_web.app as app

    monkeypatch.delenv("R2A_PLANNER_COMMAND", raising=False)

    message = app._workflow_preflight(
        {"repo_path": str(tmp_path)},
        paper_backend="preprocess",
        planner_backend="claude",
        engineer_executor="shell",
        manager_backend="rules",
        reviewer_backend="rules",
        codex_executable_path="codex",
        claude_executable_path="ccr",
    )

    assert "Planner backend ready = false" in message
    assert "R2A_PLANNER_COMMAND" in message


def test_web_preflight_blocks_gateway_not_running(tmp_path, monkeypatch) -> None:
    import r2a_web.app as app
    from r2a.tools.codex_cli import CodexCliCheckResult

    monkeypatch.setattr(
        "r2a_web.app.check_claude_code_cli",
        lambda path: CodexCliCheckResult(True, path, path, "claude-code-router version: 2.0.0", "", "ok"),
    )
    monkeypatch.setattr(
        "r2a_web.app.check_gateway_preflight",
        lambda *args, **kwargs: {
            "ok": False,
            "errors": ["GATEWAY_NOT_RUNNING"],
            "resolved_path": "C:/Tools/ccr.cmd",
            "gateway_type": "ccr",
            "gateway_running": False,
            "config_source": "C:/Users/example/.claude-code-router/config.json",
            "logs_dir": "C:/Users/example/.claude-code-router/logs",
        },
    )

    message = app._workflow_preflight(
        {"repo_path": str(tmp_path)},
        paper_backend="preprocess",
        planner_backend="template",
        engineer_executor="shell",
        manager_backend="claude_review",
        reviewer_backend="rules",
        codex_executable_path="codex",
        claude_executable_path="ccr",
    )

    assert "Gateway preflight failed" in message
    assert "GATEWAY_NOT_RUNNING" in message


def test_web_preflight_checks_gateway_for_claude_reviewer(tmp_path, monkeypatch) -> None:
    import r2a_web.app as app
    from r2a.tools.codex_cli import CodexCliCheckResult

    captured: dict[str, object] = {}
    monkeypatch.setattr(
        "r2a_web.app.check_claude_code_cli",
        lambda path: CodexCliCheckResult(True, path, path, "claude-code-router version: 2.0.0", "", "ok"),
    )

    def fake_gateway(path, *, stages, preflight_required, auto_start):
        captured["path"] = path
        captured["stages"] = stages
        captured["preflight_required"] = preflight_required
        captured["auto_start"] = auto_start
        return {"ok": True, "errors": []}

    monkeypatch.setattr("r2a_web.app.check_gateway_preflight", fake_gateway)

    message = app._workflow_preflight(
        {"repo_path": str(tmp_path)},
        paper_backend="preprocess",
        planner_backend="template",
        engineer_executor="shell",
        manager_backend="rules",
        reviewer_backend="claude",
        codex_executable_path="codex",
        claude_executable_path="ccr",
    )

    assert message == ""
    assert captured["stages"] == ["reviewer"]
    assert captured["preflight_required"] is True
    assert captured["auto_start"] is False


def test_web_preflight_allows_openclaw_backends_with_wsl(tmp_path, monkeypatch) -> None:
    import r2a_web.app as app
    from r2a.tools.wsl import WslCheckResult

    monkeypatch.setattr("r2a_web.app.check_wsl", lambda distro: WslCheckResult(True, distro))
    monkeypatch.setattr("r2a_web.app.check_codex_cli", lambda path: (_ for _ in ()).throw(AssertionError("codex should not be checked")))
    monkeypatch.setattr("r2a_web.app.check_claude_code_cli", lambda path: (_ for _ in ()).throw(AssertionError("claude should not be checked")))

    message = app._workflow_preflight(
        {"repo_path": str(tmp_path)},
        paper_backend="openclaw_reader",
        planner_backend="openclaw",
        engineer_executor="shell",
        manager_backend="openclaw_review",
        reviewer_backend="openclaw",
        codex_executable_path="codex",
    )

    assert message == ""


def test_web_approval_diagnostics_reports_not_ready_before_approval(tmp_path) -> None:
    import json
    import r2a_web.app as app

    logs = tmp_path / ".r2a" / "logs"
    logs.mkdir(parents=True)
    (logs / "planner_transaction.json").write_text(
        json.dumps(
            {
                "committed": False,
                "validation_status": "FAIL",
                "failure_category": "STAGING_OUTPUT_MISSING",
                "issues": ["TASK_SPEC.md missing"],
                "diagnostic": {
                    "staging_task_spec_written": False,
                    "staging_experiment_contract_written": False,
                    "planner_validation_passed": False,
                    "planner_committed": False,
                    "approval_passed": False,
                    "failure_category": "STAGING_OUTPUT_MISSING",
                },
            }
        ),
        encoding="utf-8",
    )

    diagnostics = app._approval_diagnostics_model(tmp_path, {"stopped": True, "stop_reason": "planner_stage_failed"})

    assert diagnostics["Approval ready"] == "no"
    assert diagnostics["Failure category"] == "STAGING_OUTPUT_MISSING"
    assert "Planner not ready for approval" in diagnostics["Approval rejected reason"]


def test_failed_run_not_reported_as_completed_success() -> None:
    import r2a_web.app as app

    result = {
        "loop_status": "planner_failed",
        "stop_reason": "PLANNER_BACKEND_FAILURE",
        "planner_transaction": {
            "validation_status": "FAIL",
            "execution_status": "PLANNER_BACKEND_FAILURE",
        },
    }

    assert app._workflow_terminal_status(result) == "failed"
    assert app._failed_stage_from_result(result) == "planner"
    assert app._error_code_from_result(result) == "PLANNER_BACKEND_FAILURE"


def test_web_stage_status_uses_active_run_record() -> None:
    import r2a_web.app as app

    record = {"status": "running", "current_stage": "planner"}

    assert app._stage_status_from_run_record("paper", record) == "Done"
    assert app._stage_status_from_run_record("planner", record) == "Running"
    assert app._stage_status_from_run_record("approval", record) == "Pending"


def test_active_current_stage_model_prefers_live_registry_with_warning(tmp_path) -> None:
    import r2a_web.app as app

    record = {"status": "running", "current_stage": "engineer"}
    manifest = {"status": "RUNNING", "current_stage": "manager", "stages": {}}

    model = app._active_current_stage_model(tmp_path, record=record, manifest=manifest, iteration_state={})

    assert model["stage"] == "engineer"
    assert model["source"] == "registry"
    assert "differs from latest RUN_MANIFEST" in model["warning"]


def test_active_current_stage_model_uses_manifest_when_registry_stale(tmp_path) -> None:
    import r2a_web.app as app

    record = {"status": "failed", "current_stage": "engineer", "stale_active_run": True}
    manifest = {"status": "RUNNING", "current_stage": "planner", "stages": {}}

    model = app._active_current_stage_model(tmp_path, record=record, manifest=manifest, iteration_state={})

    assert model["stage"] == "planner"
    assert model["source"] == "latest_manifest"
    assert "stale Runtime registry" in model["warning"]


def test_manifest_stage_status_is_current_stage_aware_for_running_iteration() -> None:
    import r2a_web.app as app

    manifest = {
        "status": "RUNNING",
        "current_stage": "planner",
        "stages": {
            "paper": {"status": "PASS"},
            "planner": {"status": "RUNNING"},
            "manager": {"status": "PASS"},
            "reviewer": {"status": "PASS"},
        },
    }

    assert app._stage_status_from_manifest("paper", manifest) == "Done"
    assert app._stage_status_from_manifest("planner", manifest) == "Running"
    assert app._stage_status_from_manifest("manager", manifest) == "Pending"
    assert app._stage_status_from_manifest("reviewer", manifest) == "Pending"


def test_web_stage_status_shows_failed_terminal_run() -> None:
    import r2a_web.app as app

    record = {"status": "force_killed", "current_stage": "planner"}

    assert app._stage_status_from_run_record("paper", record) == "Done"
    assert app._stage_status_from_run_record("planner", record) == "Force Killed"
    assert app._stage_status_from_run_record("engineer", record) == "Skipped"


def test_web_stage_status_uses_manifest_over_final_stage_heuristic() -> None:
    import r2a_web.app as app

    manifest = {
        "status": "completed_with_failure",
        "current_stage": "final",
        "stages": {
            "paper": {"status": "PASS"},
            "planner": {"status": "FAIL"},
            "approval": {"status": "PENDING"},
            "engineer": {"status": "PENDING"},
            "manager": {"status": "PENDING"},
            "reviewer": {"status": "PENDING"},
            "final": {"status": "PASS"},
        },
    }

    assert app._stage_status_from_manifest("planner", manifest) == "Failed"
    assert app._stage_status_from_manifest("engineer", manifest) == "Skipped"
    assert app._stage_status_from_manifest("manager", manifest) == "Skipped"
    assert app._stage_status_from_manifest("final", manifest) == "Failure Report"


def test_web_workflow_failure_summary_keeps_runtime_and_artifact_failure_fields() -> None:
    import r2a_web.app as app

    record = {
        "status": "completed_with_failure",
        "current_stage": "final",
        "failed_stage": "planner",
        "error_code": "PLANNER_BACKEND_FAILURE",
        "termination_reason": "planner model timeout",
    }
    manifest = {
        "status": "completed_with_failure",
        "failed_stage": "planner",
        "stop_reason": "planner_backend_timeout",
    }

    summary = app._workflow_failure_summary_model(record, manifest)

    assert summary["workflow final_status"] == "completed_with_failure"
    assert summary["failed_stage"] == "planner"
    assert summary["stop_reason"] == "planner_backend_timeout"
    assert summary["failure_category"] == "PLANNER_BACKEND_FAILURE"


def test_web_stage_status_run_record_failed_final_does_not_mark_all_done() -> None:
    import r2a_web.app as app

    record = {"status": "failed", "current_stage": "final", "failed_stage": "planner", "error_code": "PLANNER_MISSING_REQUIRED_OUTPUT"}

    assert app._stage_status_from_run_record("paper", record) == "Done"
    assert app._stage_status_from_run_record("planner", record) == "Failed"
    assert app._stage_status_from_run_record("engineer", record) == "Skipped"


def test_web_workflow_data_sources_model_lists_current_run_paths(tmp_path) -> None:
    import r2a_web.app as app

    r2a = tmp_path / ".r2a"
    (r2a / "latest").mkdir(parents=True)
    (r2a / "latest" / "RUN_MANIFEST.json").write_text("{}", encoding="utf-8")
    (r2a / "FINAL_REPORT.md").write_text("# FINAL_REPORT\n", encoding="utf-8")
    (r2a / "ITERATION_STATE.json").write_text("{}", encoding="utf-8")

    rows = app._workflow_data_sources_model(tmp_path)

    labels = {row["Source"]: row for row in rows}
    assert labels["Run path"]["Path"] == str(tmp_path)
    assert labels["Manifest path"]["Exists"] == "yes"
    assert labels["Final report path"]["Exists"] == "yes"
    assert labels["Iteration state path"]["Exists"] == "yes"


def test_next_background_stage_routes_planner_failure_to_final() -> None:
    import r2a_web.app as app

    assert app._next_background_stage("paper_node", {}) == "planner"
    assert app._next_background_stage("planner_node", {"loop_status": "planner_failed"}) == "final"
    assert app._next_background_stage("planner_node", {}) == "approval"
    assert app._next_background_stage("manager_node", {}) == "reviewer"
    assert app._next_background_stage("reviewer_node", {}) == "final"


def test_reviewer_blocking_verdict_marks_terminal_failure() -> None:
    import r2a_web.app as app

    result = {"manager_status": "PASS", "reviewer_verdict": "REJECT"}

    assert app._workflow_terminal_status(result) == "completed_with_failure"
    assert app._failed_stage_from_result(result) == "reviewer"


def test_claude_stage_names_include_reviewer() -> None:
    import r2a_web.app as app

    assert app._claude_stage_names("preprocess", "template", "mock", "rules", "claude") == ["reviewer"]


def test_web_preflight_checks_codex_for_paper_ai_reader(tmp_path, monkeypatch) -> None:
    import r2a_web.app as app

    monkeypatch.setattr("r2a_web.app.check_codex_cli", lambda path: (_ for _ in ()).throw(AssertionError("codex should not be checked")))

    message = app._workflow_preflight(
        {"repo_path": str(tmp_path)},
        paper_backend="ai_reader",
        planner_backend="template",
        engineer_executor="shell",
        manager_backend="rules",
        reviewer_backend="rules",
        codex_executable_path="codex",
    )

    assert message == ""


def test_web_preflight_checks_claude_for_paper_claude_reader(tmp_path, monkeypatch) -> None:
    """Test that paper_backend=claude_reader triggers Claude CLI check."""
    import r2a_web.app as app

    codex_checked = []
    claude_checked = []

    def fake_check_codex(path):
        codex_checked.append(path)
        from r2a.tools.codex_cli import CodexCliCheckResult
        return CodexCliCheckResult(available=False, executable=path, resolved_path="", version_output="", error="not checked", hint="")

    def fake_check_claude(path):
        claude_checked.append(path)
        from r2a.tools.codex_cli import CodexCliCheckResult
        return CodexCliCheckResult(available=True, executable=path, resolved_path=path, version_output="", error="", hint="")

    def fake_gateway_preflight(executable, stages, preflight_required, auto_start):
        return {"ok": True, "errors": []}

    monkeypatch.setattr("r2a_web.app.check_codex_cli", fake_check_codex)
    monkeypatch.setattr("r2a_web.app.check_claude_code_cli", fake_check_claude)
    monkeypatch.setattr("r2a_web.app.check_gateway_preflight", fake_gateway_preflight)

    message = app._workflow_preflight(
        {"repo_path": str(tmp_path)},
        paper_backend="claude_reader",
        planner_backend="template",
        engineer_executor="shell",
        manager_backend="rules",
        reviewer_backend="rules",
        codex_executable_path="codex",
        claude_executable_path="ccr",
    )

    # Verify Claude CLI was checked for claude_reader
    assert len(claude_checked) == 1
    assert claude_checked[0] == "ccr"
    # Codex should not be checked
    assert len(codex_checked) == 0
    # Preflight should pass
    assert message == ""


def test_web_build_initial_state_preserves_workspace_goal_and_all_controls() -> None:
    import r2a_web.app as app

    workspace = {
        "workspace_dir": "C:/R2A_WORKSPACES_SAMPLE/run_001",
        "repo_path": "C:/R2A_WORKSPACES_SAMPLE/run_001/repo",
        "paper_path": "C:/R2A_WORKSPACES_SAMPLE/run_001/paper/paper.pdf",
        "goal": "resolved goal from workspace creation",
    }

    state = app._build_initial_state(
        workspace,
        guidance="changed after workspace creation",
        paper_backend="preprocess",
        planner_backend="template",
        engineer_executor="codex",
        manager_backend="codex_review",
        reviewer_backend="rules",
        auto_approve=True,
        output_language="Chinese",
        auto_iterate=True,
        max_iterations=3,
        codex_executable_path="C:/Tools/codex.cmd",
        stage_api_keys={"planner": "planner-dummy-key"},
        stage_api_key_env_vars={"planner": "ANTHROPIC_API_KEY"},
        codex_stage_timeout=1800,
    )

    assert state["workspace_dir"] == workspace["workspace_dir"]
    assert state["repo_path"] == workspace["repo_path"]
    assert state["paper_path"] == workspace["paper_path"]
    assert state["goal"] == "resolved goal from workspace creation"
    assert state["resolved_goal"] == "resolved goal from workspace creation"
    assert state["guidance"] == "changed after workspace creation"
    assert state["language"] == "zh"
    assert state["output_language"] == "Chinese"
    assert state["auto_approve"] is True
    assert state["approved"] is True
    assert state["auto_iterate"] is True
    assert state["max_iterations"] == 3
    assert state["paper_backend"] == "preprocess"
    assert state["planner_backend"] == "template"
    assert state["engineer_executor"] == "codex"
    assert state["manager_backend"] == "codex_review"
    assert state["reviewer_backend"] == "rules"
    assert state["stage_codex_enabled"] is True
    assert state["codex_executable_path"] == "C:/Tools/codex.cmd"
    assert state["codex_stage_timeout"] == 1800
    assert state["stage_api_keys"] == {"planner": "planner-dummy-key"}
    assert state["stage_api_key_env_vars"] == {"planner": "ANTHROPIC_API_KEY"}


def test_web_build_initial_state_uses_default_network_scope_when_enabled_empty() -> None:
    import r2a_web.app as app

    workspace = {
        "workspace_dir": "C:/R2A_WORKSPACES_SAMPLE/run_001",
        "repo_path": "C:/R2A_WORKSPACES_SAMPLE/run_001/repo",
        "paper_path": "C:/R2A_WORKSPACES_SAMPLE/run_001/paper/paper.pdf",
        "goal": "bounded dependency acquisition",
    }

    state = app._build_initial_state(
        workspace,
        guidance="",
        paper_backend="preprocess",
        planner_backend="template",
        engineer_executor="mock",
        manager_backend="rules",
        reviewer_backend="rules",
        auto_approve=True,
        output_language="English",
        auto_iterate=False,
        max_iterations=1,
        allow_network=True,
        allowed_network_scope="",
    )

    assert state["allow_network"] is True
    assert state["network_authorized"] is True
    assert state["allowed_network_scope"] == [app.DEFAULT_ALLOWED_NETWORK_SCOPE]


def test_web_build_initial_state_does_not_mark_paper_ai_reader_as_codex_stage() -> None:
    import r2a_web.app as app

    workspace = {
        "workspace_dir": "C:/R2A_WORKSPACES_SAMPLE/run_001",
        "repo_path": "C:/R2A_WORKSPACES_SAMPLE/run_001/repo",
        "paper_path": "C:/R2A_WORKSPACES_SAMPLE/run_001/paper/paper.pdf",
        "goal": "read paper",
    }

    state = app._build_initial_state(
        workspace,
        guidance="",
        paper_backend="ai_reader",
        planner_backend="template",
        engineer_executor="shell",
        manager_backend="rules",
        reviewer_backend="rules",
        auto_approve=True,
        output_language="English",
        auto_iterate=False,
        max_iterations=1,
        codex_executable_path="codex",
        codex_stage_timeout=300,
        final_writer_backend="template",
    )

    assert state["paper_backend"] == "ai_reader"
    assert state["stage_codex_enabled"] is False


def test_web_build_initial_state_preserves_openclaw_backends() -> None:
    import r2a_web.app as app

    workspace = {
        "workspace_dir": "C:/R2A_WORKSPACES_SAMPLE/run_001",
        "repo_path": "C:/R2A_WORKSPACES_SAMPLE/run_001/repo",
        "paper_path": "C:/R2A_WORKSPACES_SAMPLE/run_001/paper/paper.txt",
        "goal": "openclaw run",
    }

    state = app._build_initial_state(
        workspace,
        guidance="",
        paper_backend="openclaw_reader",
        planner_backend="openclaw",
        engineer_executor="claude",
        manager_backend="openclaw_review",
        reviewer_backend="openclaw",
        auto_approve=True,
        output_language="English",
        auto_iterate=True,
        max_iterations=2,
        codex_executable_path="codex",
        claude_executable_path="claude",
        openclaw_executable_path="C:/Tools/openclaw.cmd",
        openclaw_config_path="C:/Users/example/.openclaw/openclaw.json",
        codex_stage_timeout=300,
    )

    assert state["paper_backend"] == "openclaw_reader"
    assert state["planner_backend"] == "openclaw"
    assert state["engineer_executor"] == "claude"
    assert state["manager_backend"] == "openclaw_review"
    assert state["reviewer_backend"] == "openclaw"
    assert state["stage_codex_enabled"] is True
    assert state["openclaw_agent"] == ""
    assert state["openclaw_provider"] == ""
    assert state["openclaw_model"] == ""
    assert state["openclaw_executable_path"] == "C:/Tools/openclaw.cmd"
    assert state["openclaw_config_path"] == "C:/Users/example/.openclaw/openclaw.json"


def test_web_openclaw_status_model_reports_config_and_stage_profiles(tmp_path, monkeypatch) -> None:
    import r2a_web.app as app

    executable = tmp_path / "openclaw.cmd"
    executable.write_text("@echo off\n", encoding="utf-8")
    monkeypatch.setenv("R2A_OPENCLAW_EXECUTABLE_PATH", str(executable))
    monkeypatch.setenv("R2A_OPENCLAW_PROVIDER", "test-provider")
    monkeypatch.setenv("R2A_OPENCLAW_MODEL", "test-model")
    monkeypatch.setenv("R2A_OPENCLAW_RUNNER", "embedded")
    monkeypatch.setenv("R2A_OPENCLAW_CONFIG_PATH", str(tmp_path / "openclaw.json"))

    status = app._openclaw_config_status_model()

    assert status["availability"] == "local_path_exists"
    assert status["openclaw_executable_path"] == str(executable)
    assert status["provider"] == "test-provider"
    assert status["model"] == "test-model"
    assert {row["Stage"] for row in status["stage_profiles"]} == {"Paper", "Planner", "Engineer", "Manager", "Reviewer", "Final Writer"}
    engineer = next(row for row in status["stage_profiles"] if row["Stage"] == "Engineer")
    assert engineer["Provider"] == "deepseek"
    assert engineer["Model"] == "deepseek-chat"
    assert status["model_options"] == []
    assert app._openclaw_model_select_options(status) == [("Not detected", {})]
    assert "Paper" in [label for _, label in app.OPENCLAW_MODEL_STAGE_LABELS]
    assert "Final Writer / Report Writer" in [label for _, label in app.OPENCLAW_MODEL_STAGE_LABELS]


def test_web_openclaw_path_guidance_covers_no_wsl_and_template_fallback() -> None:
    import r2a_web.app as app

    guidance = app._openclaw_path_guidance_markdown()

    assert "Windows + WSL OpenClaw" in guidance
    assert "Windows native OpenClaw" in guidance
    assert "Linux/macOS native OpenClaw" in guidance
    assert "No OpenClaw" in guidance
    assert "`template` Final Writer" in guidance
    assert "/home/r2auser/" not in guidance


def test_web_openclaw_example_user_path_warning_flags_doc_example() -> None:
    import r2a_web.app as app

    assert "真实 WSL 用户名" in app._openclaw_example_path_warning(
        "/home/r2auser/.openclaw/openclaw.json"
    )
    assert app._openclaw_example_path_warning("C:\\Tools\\openclaw.cmd", "/home/alice/.openclaw/openclaw.json") == ""


def test_web_openclaw_status_model_refresh_rereads_detected_config(tmp_path, monkeypatch) -> None:
    import json
    import r2a_web.app as app

    config_path = tmp_path / "openclaw.json"
    monkeypatch.setenv("R2A_OPENCLAW_CONFIG_PATH", str(config_path))

    first = app._openclaw_config_status_model()
    assert app._openclaw_model_select_options(first) == [("Not detected", {})]

    config_path.write_text(
        json.dumps(
            {
                "agents": {
                    "defaults": {
                        "models": {
                            "ai-coding-plan/glm-5": {"alias": "GLM-5"},
                        }
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    refreshed = app._openclaw_config_status_model()
    options = app._openclaw_model_select_options(refreshed)

    assert refreshed["model_detection_read_path"] == str(config_path)
    assert ("ai-coding-plan/glm-5 (default)", {
        "backend": "openclaw",
        "provider": "ai-coding-plan",
        "model": "glm-5",
        "profile": "default",
        "runner": "embedded",
        "agent": "",
    }) in options


def test_web_openclaw_saved_default_selects_only_detected_models() -> None:
    import r2a_web.app as app

    options = [
        ("Use OpenClaw runtime default", {}),
        (
            "ai-coding-plan/glm-5 (default)",
            {
                "backend": "openclaw",
                "provider": "ai-coding-plan",
                "model": "glm-5",
                "profile": "default",
                "runner": "embedded",
                "agent": "",
            },
        ),
    ]
    settings = {
        "stage_model_selection": {
            "planner": {"provider": "ai-coding-plan", "model": "glm-5", "profile": "default"},
            "engineer": {"provider": "deepseek", "model": "deepseek-chat", "profile": "default"},
        }
    }

    assert app._default_model_option_index(options, "planner", settings=settings) == 1
    assert app._default_model_option_index(options, "engineer", settings=settings) == 0
    assert app._saved_stage_model_detection_warnings(options, settings) == [
        "Engineer: deepseek/deepseek-chat (default)"
    ]
    assert all(value.get("provider") != "deepseek" for _, value in options)


def test_web_save_stage_model_defaults_sanitizes_api_keys(tmp_path, monkeypatch) -> None:
    import json
    import r2a_web.app as app

    settings_path = tmp_path / "web_settings.json"
    monkeypatch.setattr(app, "WEB_SETTINGS_PATH", settings_path)
    app._save_web_settings(
        {
            "settings_schema_version": app.WEB_SETTINGS_SCHEMA_VERSION,
            "planner_backend": "openclaw",
            "stage_api_keys": {"planner": "dummy-key-placeholder"},
            "stage_api_key_env_vars": {"planner": "OPENAI_API_KEY"},
        }
    )

    app._save_stage_model_defaults(
        {
            "planner": {
                "backend": "openclaw",
                "provider": "ai-coding-plan",
                "model": "glm-5",
                "profile": "default",
                "runner": "embedded",
                "api_key": "must-not-save",
            }
        },
        openclaw_config_path="/valid/config.json",
    )

    raw = json.loads(settings_path.read_text(encoding="utf-8"))
    loaded = app._load_web_settings()

    assert raw["stage_api_keys"] == {}
    assert "api_key" not in raw["stage_model_selection"]["planner"]
    assert loaded["stage_model_selection"]["planner"]["model"] == "glm-5"


def test_web_save_stage_model_defaults_saves_executable_and_config_paths(tmp_path, monkeypatch) -> None:
    """Test that save defaults button saves both paths and model selection."""
    import json
    import r2a_web.app as app

    settings_path = tmp_path / "web_settings.json"
    monkeypatch.setattr(app, "WEB_SETTINGS_PATH", settings_path)
    app._save_web_settings(
        {
            "settings_schema_version": app.WEB_SETTINGS_SCHEMA_VERSION,
            "openclaw_executable_path": "",
            "openclaw_config_path": "",
            "stage_model_selection": {},
        }
    )

    result = app._save_stage_model_defaults(
        {
            "planner": {
                "backend": "openclaw",
                "provider": "ai-coding-plan",
                "model": "glm-5",
                "profile": "default",
                "runner": "embedded",
            }
        },
        openclaw_executable_path="/home/user/.nvm/versions/node/v22.22.2/bin/openclaw",
        openclaw_config_path="/home/user/.openclaw/openclaw.json",
    )

    assert result["success"] is True
    loaded = app._load_web_settings()
    assert loaded["openclaw_executable_path"] == "/home/user/.nvm/versions/node/v22.22.2/bin/openclaw"
    assert loaded["openclaw_config_path"] == "/home/user/.openclaw/openclaw.json"
    assert loaded["stage_model_selection"]["planner"]["model"] == "glm-5"


def test_web_save_stage_model_defaults_warns_on_missing_config_path(tmp_path, monkeypatch) -> None:
    """Test warning when config path is not configured."""
    import r2a_web.app as app

    settings_path = tmp_path / "web_settings.json"
    monkeypatch.setattr(app, "WEB_SETTINGS_PATH", settings_path)
    app._save_web_settings({"settings_schema_version": app.WEB_SETTINGS_SCHEMA_VERSION})

    result = app._save_stage_model_defaults(
        {
            "planner": {
                "backend": "openclaw",
                "provider": "ai-coding-plan",
                "model": "glm-5",
            }
        },
        openclaw_executable_path="openclaw",
        openclaw_config_path="",
    )

    assert result["success"] is False
    assert "config path is not configured" in result.get("error", "").lower()


def test_web_save_stage_model_defaults_rejects_placeholder_config_path(tmp_path, monkeypatch) -> None:
    """Test that placeholder config paths are rejected."""
    import r2a_web.app as app

    settings_path = tmp_path / "web_settings.json"
    monkeypatch.setattr(app, "WEB_SETTINGS_PATH", settings_path)

    result = app._save_stage_model_defaults(
        {"planner": {"provider": "ai-coding-plan", "model": "glm-5"}},
        openclaw_config_path="C:\\Users\\<user>\\.openclaw\\openclaw.json",
    )

    assert result["success"] is False
    assert "placeholder" in result.get("error", "").lower()


def test_web_save_stage_model_defaults_skips_not_detected_entries(tmp_path, monkeypatch) -> None:
    """Test that 'Not detected' is not saved as valid model."""
    import r2a_web.app as app

    settings_path = tmp_path / "web_settings.json"
    monkeypatch.setattr(app, "WEB_SETTINGS_PATH", settings_path)

    result = app._save_stage_model_defaults(
        {
            "planner": {
                "provider": "not",
                "model": "detected",
            }
        },
        openclaw_config_path="/valid/config.json",
    )

    loaded = app._load_web_settings()
    assert "planner" not in loaded.get("stage_model_selection", {})


def test_web_is_placeholder_config_path_detects_common_patterns() -> None:
    """Test placeholder detection."""
    import r2a_web.app as app

    assert app._is_placeholder_config_path("C:\\Users\\<user>\\.openclaw\\openclaw.json") is True
    assert app._is_placeholder_config_path("/home/<user>/.openclaw/openclaw.json") is True
    # Should accept real paths with short usernames like 'x'
    assert app._is_placeholder_config_path("/home/x/.openclaw/openclaw.json") is False
    assert app._is_placeholder_config_path("/home/r2auser/.openclaw/openclaw.json") is True
    assert app._is_placeholder_config_path("") is True
    # Should reject quotes (copy-paste from example)
    assert app._is_placeholder_config_path('"/home/r2auser/.openclaw/openclaw.json"') is True


def test_web_load_settings_restores_paths_before_detection(tmp_path, monkeypatch) -> None:
    """Test that saved paths are loaded before model detection."""
    import json
    import r2a_web.app as app

    settings_path = tmp_path / "web_settings.json"
    monkeypatch.setattr(app, "WEB_SETTINGS_PATH", settings_path)

    settings = {
        "settings_schema_version": app.WEB_SETTINGS_SCHEMA_VERSION,
        "openclaw_executable_path": "/saved/openclaw",
        "openclaw_config_path": "/saved/config.json",
        "stage_model_selection": {
            "planner": {
                "provider": "saved-provider",
                "model": "saved-model",
                "profile": "saved-profile",
            }
        },
    }
    app._save_web_settings(settings)

    loaded = app._load_web_settings()
    assert loaded["openclaw_executable_path"] == "/saved/openclaw"
    assert loaded["openclaw_config_path"] == "/saved/config.json"
    assert loaded["stage_model_selection"]["planner"]["model"] == "saved-model"


def test_web_build_initial_state_uses_restored_stage_model_selection(tmp_path, monkeypatch) -> None:
    """Test that run payload includes restored stage_model_selection."""
    import r2a_web.app as app

    workspace = {
        "workspace_dir": "C:/R2A_WORKSPACES_SAMPLE/run_001",
        "repo_path": "C:/R2A_WORKSPACES_SAMPLE/run_001/repo",
        "paper_path": "C:/R2A_WORKSPACES_SAMPLE/run_001/paper/paper.txt",
        "goal": "test",
    }

    stage_model_selection = {
        "engineer": {
            "backend": "openclaw",
            "provider": "deepseek",
            "model": "deepseek-chat",
            "profile": "config",
        }
    }

    state = app._build_initial_state(
        workspace,
        stage_model_selection=stage_model_selection,
    )

    assert state["stage_model_selection"]["engineer"]["model"] == "deepseek-chat"


def test_web_save_stage_model_defaults_rejects_doc_example_even_with_detection(tmp_path, monkeypatch) -> None:
    """The documentation example user must not become a persisted default."""
    import r2a_web.app as app

    settings_path = tmp_path / "web_settings.json"
    monkeypatch.setattr(app, "WEB_SETTINGS_PATH", settings_path)
    app._save_web_settings(
        {
            "settings_schema_version": app.WEB_SETTINGS_SCHEMA_VERSION,
            "openclaw_executable_path": "/home/x/.nvm/versions/node/v22.22.2/bin/openclaw",
            "openclaw_config_path": "/home/x/.openclaw/openclaw.json",
            "stage_model_selection": {"planner": {"provider": "saved", "model": "saved-model"}},
        }
    )

    detection_result = {
        "source": "openclaw_config",
        "config_read_path": "/home/r2auser/.openclaw/openclaw.json",
        "models": [{"provider": "ai-coding-plan", "model": "glm-5"}],
    }

    result = app._save_stage_model_defaults(
        {"planner": {"provider": "ai-coding-plan", "model": "glm-5"}},
        openclaw_config_path="/home/r2auser/.openclaw/openclaw.json",
        detection_result=detection_result,
    )

    assert result["success"] is False
    assert "/home/r2auser" in result["error"]
    loaded = app._load_web_settings()
    assert loaded["openclaw_config_path"] == "/home/x/.openclaw/openclaw.json"
    assert loaded["stage_model_selection"]["planner"]["model"] == "saved-model"


def test_web_load_settings_ignores_doc_example_openclaw_paths(tmp_path, monkeypatch) -> None:
    import json
    import r2a_web.app as app

    settings_path = tmp_path / "web_settings.json"
    monkeypatch.setattr(app, "WEB_SETTINGS_PATH", settings_path)
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(
        json.dumps(
            {
                "settings_schema_version": app.WEB_SETTINGS_SCHEMA_VERSION,
                "openclaw_executable_path": "/home/r2auser/.nvm/versions/node/v22.22.2/bin/openclaw",
                "openclaw_config_path": "/home/r2auser/.openclaw/openclaw.json",
            }
        ),
        encoding="utf-8",
    )

    loaded = app._load_web_settings()

    assert loaded["openclaw_executable_path"] == ""
    assert loaded["openclaw_config_path"] == ""


def test_web_load_settings_preserves_real_short_wsl_user_paths(tmp_path, monkeypatch) -> None:
    import r2a_web.app as app

    settings_path = tmp_path / "web_settings.json"
    monkeypatch.setattr(app, "WEB_SETTINGS_PATH", settings_path)
    app._save_web_settings(
        {
            "settings_schema_version": app.WEB_SETTINGS_SCHEMA_VERSION,
            "openclaw_executable_path": "/home/x/.nvm/versions/node/v22.22.2/bin/openclaw",
            "openclaw_config_path": "/home/x/.openclaw/openclaw.json",
        }
    )

    loaded = app._load_web_settings()

    assert loaded["openclaw_executable_path"] == "/home/x/.nvm/versions/node/v22.22.2/bin/openclaw"
    assert loaded["openclaw_config_path"] == "/home/x/.openclaw/openclaw.json"


def test_web_save_stage_model_defaults_error_does_not_overwrite_existing_settings(tmp_path, monkeypatch) -> None:
    import r2a_web.app as app

    settings_path = tmp_path / "web_settings.json"
    monkeypatch.setattr(app, "WEB_SETTINGS_PATH", settings_path)
    app._save_web_settings(
        {
            "settings_schema_version": app.WEB_SETTINGS_SCHEMA_VERSION,
            "openclaw_executable_path": "/home/x/.nvm/versions/node/v22.22.2/bin/openclaw",
            "openclaw_config_path": "/home/x/.openclaw/openclaw.json",
            "stage_model_selection": {"planner": {"provider": "saved", "model": "saved-model"}},
        }
    )

    result = app._save_stage_model_defaults(
        {"planner": {"provider": "ai-coding-plan", "model": "glm-5"}},
        openclaw_executable_path="openclaw",
        openclaw_config_path="",
    )

    loaded = app._load_web_settings()

    assert result["success"] is False
    assert loaded["openclaw_executable_path"] == "/home/x/.nvm/versions/node/v22.22.2/bin/openclaw"
    assert loaded["openclaw_config_path"] == "/home/x/.openclaw/openclaw.json"
    assert loaded["stage_model_selection"]["planner"]["model"] == "saved-model"


def test_web_collect_settings_preserves_saved_openclaw_defaults_when_not_detected(monkeypatch) -> None:
    import r2a_web.app as app

    class FakeStreamlit:
        session_state = {
            "web_settings": {
                "openclaw_executable_path": "/home/x/.nvm/versions/node/v22.22.2/bin/openclaw",
                "openclaw_config_path": "/home/x/.openclaw/openclaw.json",
                "stage_model_selection": {
                    "planner": {"provider": "ai-coding-plan", "model": "glm-5"},
                    "engineer": {"provider": "deepseek", "model": "deepseek-chat"},
                },
            },
            "setting_workspace_base_dir": "E:/R2A_WORKSPACES",
            "setting_paper_backend": "openclaw_reader",
            "setting_planner_backend": "openclaw",
            "setting_engineer_executor": "openclaw",
            "setting_manager_backend": "openclaw_review",
            "setting_reviewer_backend": "openclaw",
            "setting_final_writer_backend": "openclaw",
        }

    monkeypatch.setattr(app, "st", FakeStreamlit)

    saved = app._collect_web_settings(
        {},
        {},
        "",
        "",
        "/usr/local/bin/openclaw",
        "/tmp/not-detected.json",
        10800,
    )

    assert saved["openclaw_executable_path"] == "/home/x/.nvm/versions/node/v22.22.2/bin/openclaw"
    assert saved["openclaw_config_path"] == "/home/x/.openclaw/openclaw.json"
    assert saved["stage_model_selection"]["planner"]["model"] == "glm-5"
    assert saved["stage_model_selection"]["engineer"]["model"] == "deepseek-chat"


def test_web_current_or_saved_stage_model_selection_preserves_saved_when_not_detected(monkeypatch) -> None:
    import r2a_web.app as app

    class FakeStreamlit:
        session_state = {
            "openclaw_stage_model_value_planner": {},
            "openclaw_stage_model_value_engineer": {},
        }

    monkeypatch.setattr(app, "st", FakeStreamlit)

    selection = app._current_or_saved_stage_model_selection(
        {
            "stage_model_selection": {
                "planner": {"provider": "ai-coding-plan", "model": "glm-5"},
                "engineer": {"provider": "deepseek", "model": "deepseek-chat"},
            }
        }
    )

    assert selection["planner"]["model"] == "glm-5"
    assert selection["engineer"]["model"] == "deepseek-chat"


def test_web_build_initial_state_serializes_final_writer_stage_model() -> None:
    import r2a_web.app as app

    workspace = {
        "workspace_dir": "C:/R2A_WORKSPACES_SAMPLE/run_001",
        "repo_path": "C:/R2A_WORKSPACES_SAMPLE/run_001/repo",
        "paper_path": "C:/R2A_WORKSPACES_SAMPLE/run_001/paper/paper.txt",
        "goal": "openclaw final writer",
    }

    state = app._build_initial_state(
        workspace,
        guidance="",
        paper_backend="openclaw_reader",
        planner_backend="openclaw",
        engineer_executor="openclaw",
        manager_backend="openclaw_review",
        reviewer_backend="openclaw",
        auto_approve=True,
        output_language="Chinese",
        auto_iterate=False,
        max_iterations=1,
        final_writer_backend="openclaw",
        stage_model_selection={
            "final_writer": {
                "backend": "openclaw",
                "provider": "deepseek",
                "model": "deepseek-chat",
                "profile": "report-writer",
            }
        },
    )

    assert state["final_writer_backend"] == "openclaw"
    assert state["stage_model_selection"]["final_writer"]["provider"] == "deepseek"


def test_make_initial_state_marks_paper_openclaw_reader_as_ai_stage(tmp_path) -> None:
    from r2a.core.state import make_initial_state

    state = make_initial_state(
        tmp_path,
        paper_backend="openclaw_reader",
        planner_backend="template",
        engineer_executor="shell",
        manager_backend="rules",
        reviewer_backend="rules",
    )

    assert state["stage_codex_enabled"] is True


def test_web_preflight_rejects_paper_codex_without_cli_check(tmp_path, monkeypatch) -> None:
    import r2a_web.app as app

    monkeypatch.setattr("r2a_web.app.check_codex_cli", lambda path: (_ for _ in ()).throw(AssertionError("paper codex should not check CLI")))

    message = app._workflow_preflight(
        {"repo_path": str(tmp_path)},
        paper_backend="codex",
        planner_backend="template",
        engineer_executor="shell",
        manager_backend="rules",
        reviewer_backend="rules",
        codex_executable_path="codex",
    )

    assert "Paper Codex backend is disabled" in message


def test_web_preflight_does_not_check_codex_for_paper_preprocess(tmp_path, monkeypatch) -> None:
    import r2a_web.app as app

    monkeypatch.setattr("r2a_web.app.check_codex_cli", lambda path: (_ for _ in ()).throw(AssertionError("codex should not be checked")))

    message = app._workflow_preflight(
        {"repo_path": str(tmp_path)},
        paper_backend="preprocess",
        planner_backend="template",
        engineer_executor="shell",
        manager_backend="rules",
        reviewer_backend="rules",
        codex_executable_path="codex",
    )

    assert message == ""


def test_web_collects_engineer_result_artifacts(tmp_path) -> None:
    import r2a_web.app as app

    results = tmp_path / ".r2a" / "results"
    results.mkdir(parents=True)
    (results / "reduced_metrics.csv").write_text("method,recall,latency_ms\nm,0.9,12\n", encoding="utf-8")
    (results / "plot.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (results / "ENGINEER_NOTES.md").write_text("# Notes\n", encoding="utf-8")
    (results / "dashboard.html").write_text("<html></html>", encoding="utf-8")

    artifacts = app._collect_result_artifacts(tmp_path)

    assert results / "reduced_metrics.csv" in artifacts["tables"]
    assert results / "plot.png" in artifacts["images"]
    assert results / "ENGINEER_NOTES.md" in artifacts["texts"]
    assert results / "dashboard.html" in artifacts["html"]


def test_web_engineer_results_excludes_archived_iterations_by_default(tmp_path) -> None:
    import r2a_web.app as app

    current = tmp_path / ".r2a" / "results"
    archived = tmp_path / ".r2a" / "runs" / "iter_001" / "results"
    current.mkdir(parents=True)
    archived.mkdir(parents=True)
    (current / "reduced_metrics.csv").write_text("method,recall\nm,0.9\n", encoding="utf-8")
    (archived / "old_metrics.csv").write_text("method,recall\nold,0.1\n", encoding="utf-8")

    current_only = app._collect_result_artifacts(tmp_path)
    with_history = app._collect_result_artifacts(tmp_path, include_history=True)

    assert current / "reduced_metrics.csv" in current_only["tables"]
    assert archived / "old_metrics.csv" not in current_only["tables"]
    assert archived / "old_metrics.csv" in with_history["tables"]


def test_web_settings_save_and_load(tmp_path, monkeypatch) -> None:
    import r2a_web.app as app

    settings_path = tmp_path / "web_settings.json"
    monkeypatch.setattr(app, "WEB_SETTINGS_PATH", settings_path)
    settings = {
        "paper_backend": "claude_reader",
        "engineer_execution_environment": "wsl",
        "wsl_distro": "Ubuntu-22.04",
        "wsl_cache_dir": "D:/R2A/cache",
        "claude_executable_path": "C:/Tools/ccr.cmd",
        "openclaw_executable_path": "C:/Tools/openclaw.cmd",
        "openclaw_config_path": "C:/Users/example/.openclaw/openclaw.json",
        "auto_refresh_interval_seconds": 10,
        "stage_api_keys": {"paper": "paper-dummy-key"},
        "stage_api_key_env_vars": {"paper": "ANTHROPIC_API_KEY"},
        "stage_model_selection": {
            "reviewer": {
                "backend": "openclaw",
                "provider": "detected-provider",
                "model": "detected-model",
                "profile": "default",
            }
        },
    }

    app._save_web_settings(settings)

    loaded = app._load_web_settings()

    assert loaded["settings_schema_version"] == app.WEB_SETTINGS_SCHEMA_VERSION
    assert loaded["paper_backend"] == "openclaw_reader"
    assert loaded["planner_backend"] == "openclaw"
    assert loaded["engineer_executor"] == "openclaw"
    assert loaded["engineer_execution_environment"] == "wsl"
    assert loaded["wsl_distro"] == "Ubuntu-22.04"
    assert loaded["wsl_cache_dir"] == "D:/R2A/cache"
    assert loaded["manager_backend"] == "openclaw_review"
    assert loaded["reviewer_backend"] == "openclaw"
    assert loaded["final_writer_backend"] == "openclaw"
    assert loaded["claude_executable_path"] == "C:/Tools/ccr.cmd"
    assert loaded["openclaw_executable_path"] == "C:/Tools/openclaw.cmd"
    assert loaded["openclaw_config_path"] == "C:/Users/example/.openclaw/openclaw.json"
    assert loaded["auto_refresh_interval_seconds"] == 0
    assert loaded["stage_model_selection"]["reviewer"]["model"] == "detected-model"
    assert loaded["stage_api_keys"] == {}
    assert loaded["stage_api_key_env_vars"] == {}


def test_web_settings_path_uses_env_override(tmp_path, monkeypatch) -> None:
    import r2a_web.app as app

    settings_path = tmp_path / "custom_web_settings.json"
    monkeypatch.setenv("R2A_WEB_SETTINGS_PATH", str(settings_path))

    assert app._default_web_settings_path() == settings_path


def test_persist_auto_refresh_interval_disables_legacy_values_and_preserves_existing_settings(tmp_path, monkeypatch) -> None:
    import r2a_web.app as app

    settings_path = tmp_path / "web_settings.json"
    monkeypatch.setattr(app, "WEB_SETTINGS_PATH", settings_path)
    settings = {
        "settings_schema_version": app.WEB_SETTINGS_SCHEMA_VERSION,
        "planner_backend": "openclaw",
        "engineer_executor": "openclaw",
        "openclaw_config_path": "/saved/config.json",
        "auto_refresh_interval_seconds": 0,
    }

    saved = app._persist_auto_refresh_interval(settings, 5)
    loaded = app._load_web_settings()

    assert saved["auto_refresh_interval_seconds"] == 0
    assert loaded["auto_refresh_interval_seconds"] == 0
    assert loaded["planner_backend"] == "openclaw"
    assert loaded["engineer_executor"] == "openclaw"
    assert loaded["openclaw_config_path"] == "/saved/config.json"


def test_maybe_autorefresh_does_not_inject_reload_js_for_legacy_interval(monkeypatch) -> None:
    import r2a_web.app as app

    class AttrDict(dict):
        def __getattr__(self, key):
            try:
                return self[key]
            except KeyError as exc:
                raise AttributeError(key) from exc

        def __setattr__(self, key, value):
            self[key] = value

    class FakeStreamlit:
        def __init__(self) -> None:
            self.session_state = AttrDict(
                {
                    "workspace": {"repo_path": "C:/R2A_WORKSPACES_SAMPLE/run_001/repo"},
                    "active_run_id": "run-1",
                    "auto_refresh_interval_seconds": 30,
                    "setting_auto_refresh_interval_label": "30s",
                    "workflow_running": True,
                }
            )

        def html(self, *args, **kwargs):
            raise AssertionError("auto-refresh must not inject reload JavaScript")

    fake_st = FakeStreamlit()
    monkeypatch.setattr(app, "st", fake_st)
    monkeypatch.setattr(app, "feature_enabled", lambda _flag: True)

    app._maybe_autorefresh(interval_ms=1)

    assert fake_st.session_state.auto_refresh_diagnostic["should_refresh"] is False
    assert fake_st.session_state.auto_refresh_diagnostic["interval_seconds"] == 0
    assert "last_refreshed_at" not in fake_st.session_state


def test_run_control_panel_keeps_manual_refresh_status_button() -> None:
    import inspect
    import r2a_web.app as app

    source = inspect.getsource(app._show_run_control_panel)

    assert "Refresh Status" in source
    assert "_sync_background_run_state()" in source
    assert "st.rerun()" in source


def test_web_settings_migrates_legacy_engineer_claude_to_openclaw(tmp_path, monkeypatch) -> None:
    import json
    import r2a_web.app as app

    settings_path = tmp_path / "web_settings.json"
    monkeypatch.setattr(app, "WEB_SETTINGS_PATH", settings_path)
    settings_path.write_text(
        json.dumps(
            {
                "paper_backend": "openclaw_reader",
                "planner_backend": "openclaw",
                "engineer_executor": "claude",
                "manager_backend": "rules",
                "reviewer_backend": "openclaw",
                "stage_api_key_env_vars": {"engineer": "DEEPSEEK_API_KEY"},
                "stage_api_key_providers": {"engineer": "deepseek"},
                "stage_api_key_sources": {"engineer": "env"},
            }
        ),
        encoding="utf-8",
    )

    loaded = app._load_web_settings()

    assert loaded["engineer_executor"] == "openclaw"
    assert loaded["manager_backend"] == "openclaw_review"
    assert loaded["stage_api_key_env_vars"] == {}
    assert loaded["stage_api_key_providers"] == {}
    assert loaded["stage_api_key_sources"] == {}


def test_web_latest_stage_activity_reports_engineer_running(tmp_path) -> None:
    import r2a_web.app as app

    logs = tmp_path / ".r2a" / "logs"
    logs.mkdir(parents=True)
    (logs / "claude_engineer_prompt.md").write_text("prompt", encoding="utf-8")

    activity = app._latest_stage_activity(tmp_path)

    assert activity["level"] == "running"
    assert activity["status_text"] == "Engineer running"


def test_web_final_summary_model_prioritizes_user_friendly_fields() -> None:
    import r2a_web.app as app

    final_report = """# FINAL_REPORT

## Executive Summary

- Current conclusion: Pass with limitations (`PASS_WITH_LIMITATIONS`).
- Next action: cleanup.

## Final Status

completed

## Total Iterations

2

## Stop Reason

max_iterations_reached_with_l4_limited_evidence

## Final Verdict

PASS_WITH_LIMITATIONS

## Detailed Status

PASS_REDUCED_ALIGNED_WITH_LIMITATIONS

## Reproduction Level

- Current: L4: Reduced paper-aligned evidence (L4_reduced_paper_aligned)
- Target: L4: Reduced paper-aligned evidence (L4_reduced_paper_aligned)
- Result Type: reduced paper-aligned evidence with limitations
- Full Reproduction Claim: No. This is not a full reproduction.
- Claim: official reduced paper-aligned; not full reproduction (official reduced paper-aligned)
- Download Budget: 20GB
- Next Action: cleanup warnings

## Provenance

- L4_ALIGNMENT_SUMMARY.md: C:/R2A_RUNS_SAMPLE/repo/.r2a/results/L4_ALIGNMENT_SUMMARY.md

## Raw Engineer Results

CSV details.
"""

    summary = app._final_summary_model(final_report)

    assert summary["final_verdict"] == "PASS_WITH_LIMITATIONS"
    assert "L4: Reduced paper-aligned evidence" in summary["current_level"]
    assert summary["result_type"] == "reduced paper-aligned evidence with limitations"
    assert "not a full reproduction" in summary["full_reproduction_claim"]
    assert summary["l4_alignment_summary"].endswith("L4_ALIGNMENT_SUMMARY.md")
    assert final_report.index("## Executive Summary") < final_report.index("## Raw Engineer Results")


def test_web_final_summary_model_uses_existing_l4_summary_path(tmp_path) -> None:
    import r2a_web.app as app

    summary_path = tmp_path / ".r2a" / "results" / "L4_ALIGNMENT_SUMMARY.md"
    summary_path.parent.mkdir(parents=True)
    summary_path.write_text("# L4\n", encoding="utf-8")
    final_report = """# FINAL_REPORT

## Final Verdict

PASS_WITH_LIMITATIONS

## Reproduction Level

- Current: L4_reduced_paper_aligned
"""

    summary = app._final_summary_model(final_report, repo_path=tmp_path)

    assert summary["l4_alignment_summary"] == str(summary_path)


def test_web_read_report_supports_l4_alignment_summary(tmp_path) -> None:
    import r2a_web.app as app

    summary_path = tmp_path / ".r2a" / "results" / "L4_ALIGNMENT_SUMMARY.md"
    summary_path.parent.mkdir(parents=True)
    summary_path.write_text("# L4_ALIGNMENT_SUMMARY\n\nok\n", encoding="utf-8")

    assert "ok" in app.read_report(tmp_path, "l4_alignment_summary")


def test_web_final_status_card_model_keeps_success_fields_visible() -> None:
    import r2a_web.app as app

    final_report = """# FINAL_REPORT

## Final Status

completed_success

## Stop Reason

MAX_ITERATIONS_REACHED

## Final Verdict

PASS_REDUCED_ALIGNED

## Reproduction Level

- Current: L4_reduced_paper_aligned
- Observed Evidence Level: L4_reduced_paper_aligned
- Accepted Level After Quality Gates: L4_reduced_paper_aligned
"""

    card = app._final_status_card_model(final_report, repo_path=None)

    assert card["final_status"] == "completed_success"
    assert card["accepted_level"] == "L4_reduced_paper_aligned"
    assert card["observed_level"] == "L4_reduced_paper_aligned"
    assert card["stop_reason"] == "MAX_ITERATIONS_REACHED"
    assert card["is_failure"] is False


def test_web_final_summary_model_distinguishes_accepted_observed_and_cap_reason() -> None:
    import r2a_web.app as app

    final_report = """# FINAL_REPORT

## Final Status

completed_with_failure

## Total Iterations

6

## Stop Reason

manager_checks_failed

## Final Verdict

NEEDS_FIX

## Detailed Status

INPUT_CONTRACT_READY

## Reproduction Level

- Current: L0: Project health (L0_project_health)
- Observed Evidence Level: L2: Input contract ready (L2_input_contract_ready)
- Accepted Level After Quality Gates: L0: Project health (L0_project_health)
- Quality Gate Level: L0: Project health (L0_project_health)
- Cap Reason: Manager status is FAIL; structural checks cap accepted level below observed artifact level.
- Target: L4: Reduced paper-aligned evidence (L4_reduced_paper_aligned)
- Result Type: verification-only/no-op smoke evidence, capped at L2
- Full Reproduction Claim: No.
- Claim: limited
- Next Action: fix blockers
"""

    summary = app._final_summary_model(final_report)

    assert summary["accepted_level"] == "L0: Project health (L0_project_health)"
    assert summary["observed_level"] == "L2: Input contract ready (L2_input_contract_ready)"
    assert "Manager status is FAIL" in summary["cap_reason"]
    assert summary["current_level"] == summary["accepted_level"]


def test_web_final_summary_displays_unassessed_explicitly() -> None:
    import r2a_web.app as app

    final_report = """
## Reproduction Level

- Current: UNASSESSED
- Observed Evidence Level: L4_reduced_paper_aligned
- Accepted Level After Quality Gates: UNASSESSED
"""

    summary = app._final_summary_model(final_report)

    assert summary["accepted_level"] == "未正式接受 (UNASSESSED)"
    assert summary["current_level"] == "未正式接受 (UNASSESSED)"
    assert summary["observed_level"] == "L4_reduced_paper_aligned"
