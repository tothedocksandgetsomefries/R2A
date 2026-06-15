from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from r2a.agents.planner_agent import run_planner_agent
from r2a.core.paths import artifact_dir, report_path
from r2a.core.state import make_initial_state
from r2a.tools.planner_model_client import _template_planner_output
from r2a.tools.planner_renderer import render_experiment_contract, render_planner_json, render_task_spec
from r2a.tools.planner_transaction import (
    compile_canonical_planner_output,
    commit_planner_transaction,
    planner_allowed_outputs,
    planner_staging_dir,
    validate_planner_transaction,
    write_planner_transaction_metadata,
)
from r2a.core.planner_schema import PlannerOutput
from r2a.workflow.nodes import human_approval_node


def _write_valid_candidate(staging: Path, *, planner: bool = True, task: bool = True, contract: bool = True) -> None:
    staging.mkdir(parents=True, exist_ok=True)
    output = PlannerOutput.model_validate(_template_planner_output({"iteration": 1, "goal": "tx", "paper_bundle": {}}))
    if planner:
        (staging / "PLANNER_OUTPUT.json").write_text(render_planner_json(output), encoding="utf-8")
    if task:
        (staging / "TASK_SPEC.md").write_text(render_task_spec(output), encoding="utf-8")
    if contract:
        (staging / "EXPERIMENT_CONTRACT.md").write_text(render_experiment_contract(output), encoding="utf-8")


def _write_openclaw_file_write_candidate(staging: Path, data: dict) -> None:
    staging.mkdir(parents=True, exist_ok=True)
    (staging / "PLANNER_OUTPUT.json").write_text(json.dumps(data, indent=2), encoding="utf-8")
    (staging / "TASK_SPEC.md").write_text(
        "# TASK_SPEC\n\nSource of truth: PLANNER_OUTPUT.json\n",
        encoding="utf-8",
    )
    (staging / "EXPERIMENT_CONTRACT.md").write_text(
        "# EXPERIMENT_CONTRACT\n\nSource of truth: PLANNER_OUTPUT.json\n",
        encoding="utf-8",
    )


def _mutate_planner_output(staging: Path, mutator) -> None:
    path = staging / "PLANNER_OUTPUT.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    mutator(data)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _raw_planner_output(task: dict) -> dict:
    return {
        "schema_version": "2.0",
        "iteration": 1,
        "planning_mode": "initial",
        "iteration_strategy": "PROGRESS_ONLY",
        "objective": "raw planner candidate",
        "contract_mode": "verification_only",
        "tasks": [task],
    }


def test_planner_v2_commits_only_after_validation(tmp_path: Path) -> None:
    staging = planner_staging_dir(tmp_path, 1, 1)
    started = time.time()
    _write_valid_candidate(staging)

    metadata = validate_planner_transaction(
        tmp_path,
        staging,
        {"success": True, "planner_backend": "template"},
        iteration=1,
        attempt_started_at=started,
    )
    assert metadata["validation_status"] == "PASS"
    assert not report_path(tmp_path, "planner_output").exists()

    committed = commit_planner_transaction(tmp_path, staging, metadata)
    write_planner_transaction_metadata(tmp_path, committed)

    assert report_path(tmp_path, "planner_output").exists()
    assert report_path(tmp_path, "task").exists()
    assert report_path(tmp_path, "experiment_contract").exists()
    assert committed["committed_files"] == [
        ".r2a/PLANNER_OUTPUT.json",
        ".r2a/TASK_SPEC.md",
        ".r2a/EXPERIMENT_CONTRACT.md",
    ]


def test_planner_v2_rejects_missing_json(tmp_path: Path) -> None:
    staging = planner_staging_dir(tmp_path, 1, 1)
    _write_valid_candidate(staging, planner=False)

    metadata = validate_planner_transaction(
        tmp_path,
        staging,
        {"success": True, "planner_backend": "template"},
        iteration=1,
        attempt_started_at=time.time(),
    )

    assert metadata["validation_status"] == "FAIL"
    assert metadata["execution_status"] == "PLANNER_MISSING_REQUIRED_OUTPUT"
    assert not report_path(tmp_path, "planner_output").exists()


def test_planner_v2_schema_failure_routes_to_final(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def bad_model(*args, **kwargs):
        return {"schema_version": "2.0", "iteration": 1}

    monkeypatch.setattr("r2a.agents.planner_agent.call_planner_model", bad_model)
    result = run_planner_agent(make_initial_state(tmp_path, auto_approve=True))
    approved = human_approval_node(result)

    assert result["stopped"] is True
    assert approved["stopped"] is True
    assert result["loop_status"] == "planner_failed"
    assert not report_path(tmp_path, "task").exists()


def test_planner_v2_forbidden_extra_staging_file_rejects_commit(tmp_path: Path) -> None:
    staging = planner_staging_dir(tmp_path, 1, 1)
    _write_valid_candidate(staging)
    bad = staging / "results" / "reduced_metrics.csv"
    bad.parent.mkdir(parents=True)
    bad.write_text("command_id,dataset\nC1,x\n", encoding="utf-8")

    metadata = validate_planner_transaction(
        tmp_path,
        staging,
        {"success": True, "planner_backend": "template"},
        iteration=1,
        attempt_started_at=time.time(),
    )

    assert metadata["validation_status"] == "FAIL"
    assert metadata["execution_status"] == "PLANNER_FORBIDDEN_WRITE"
    assert not report_path(tmp_path, "task").exists()


def test_planner_v2_allowed_outputs_are_three_staging_files(tmp_path: Path) -> None:
    staging = planner_staging_dir(tmp_path, 3, 1)
    allowed = planner_allowed_outputs(tmp_path, staging)

    assert ".r2a/staging/planner/iter_003/attempt_001/PLANNER_OUTPUT.json" in allowed
    assert ".r2a/staging/planner/iter_003/attempt_001/TASK_SPEC.md" in allowed
    assert ".r2a/staging/planner/iter_003/attempt_001/EXPERIMENT_CONTRACT.md" in allowed
    assert ".r2a/TASK_SPEC.md" not in allowed


def test_empty_stop_conditions_are_derived_for_semantic_source_task() -> None:
    output = compile_canonical_planner_output(
        _raw_planner_output(
            {
                "task_id": "task_001_source_verification",
                "task_kind": "source_verification",
                "title": "Source Integrity Verification",
                "objective": "Verify source code completeness and provenance",
                "actions": ["Record source provenance."],
                "expected_outputs": [".r2a/results/source_verification.csv"],
                "stop_conditions": [],
                "allowed_write_paths": [".r2a/results/**"],
            }
        )
    )

    assert output.tasks[0].stop_conditions
    assert "source_verification.csv" in output.tasks[0].stop_conditions[0]


def test_empty_stop_conditions_do_not_get_meaningless_default_fill() -> None:
    with pytest.raises(ValueError, match="Cannot derive canonical stop_conditions"):
        compile_canonical_planner_output(
            _raw_planner_output(
                {
                    "task_id": "task_unknown",
                    "task_kind": "unknown",
                    "title": "Do useful work",
                    "objective": "Run a vague task",
                    "actions": ["Run command"],
                    "expected_outputs": [".r2a/results/notes.txt"],
                    "stop_conditions": [],
                }
            )
        )


def test_canonical_compiler_normalizes_network_request_by_scope() -> None:
    raw = _raw_planner_output(
        {
            "task_id": "task_004_input_contract_verification",
            "task_kind": "input_contract",
            "title": "Input Contract Verification",
            "objective": "Verify official dataset, query, ground_truth, metric, and command.",
            "actions": ["Inspect official input metadata."],
            "expected_outputs": [".r2a/results/input_contract_verification.csv"],
            "stop_conditions": [],
            "allow_network": True,
            "requires_network": True,
            "requested_network_scope": "external_git_clone_for_algorithm_dependencies",
        }
    )

    unauthorised = compile_canonical_planner_output(raw, network_authorized=False)
    assert unauthorised.tasks[0].allow_network is False

    authorised = compile_canonical_planner_output(
        raw,
        network_authorized=True,
        allowed_network_scope=["external_git_clone_for_algorithm_dependencies"],
    )
    assert authorised.tasks[0].allow_network is True

    mismatched_scope = compile_canonical_planner_output(
        raw,
        network_authorized=True,
        allowed_network_scope=["official_dataset_metadata_only"],
    )
    assert mismatched_scope.tasks[0].allow_network is False


def test_planner_transaction_compiler_has_no_model_name_business_branch() -> None:
    source = Path("r2a/tools/planner_transaction.py").read_text(encoding="utf-8").lower()
    assert "glm" not in source
    assert "deepseek" not in source
    assert "openclaw" not in source


def test_network_authorization_rejects_allow_network_without_authorization(tmp_path: Path) -> None:
    staging = planner_staging_dir(tmp_path, 1, 1)
    _write_valid_candidate(staging)
    _mutate_planner_output(staging, lambda data: data["tasks"][0].update({"allow_network": True}))

    metadata = validate_planner_transaction(
        tmp_path,
        staging,
        {"success": True, "planner_backend": "template", "network_authorized": False},
        iteration=1,
        attempt_started_at=time.time(),
    )

    assert metadata["validation_status"] == "FAIL"
    assert any("allow_network=true without network authorization" in issue for issue in metadata["issues"])
    assert not report_path(tmp_path, "planner_output").exists()


def test_network_authorization_allows_network_when_explicitly_authorized(tmp_path: Path) -> None:
    staging = planner_staging_dir(tmp_path, 1, 1)
    _write_valid_candidate(staging)
    _mutate_planner_output(staging, lambda data: data["tasks"][0].update({"allow_network": True}))

    metadata = validate_planner_transaction(
        tmp_path,
        staging,
        {
            "success": True,
            "planner_backend": "template",
            "network_authorized": True,
            "allowed_network_scope": ["external_git_clone_for_algorithm_dependencies"],
        },
        iteration=1,
        attempt_started_at=time.time(),
    )

    assert metadata["validation_status"] == "PASS"
    assert metadata["diagnostic"]["network_authorized"] is True


def test_network_authorization_rejects_allow_network_without_scope(tmp_path: Path) -> None:
    staging = planner_staging_dir(tmp_path, 1, 1)
    _write_valid_candidate(staging)
    _mutate_planner_output(staging, lambda data: data["tasks"][0].update({"allow_network": True}))

    metadata = validate_planner_transaction(
        tmp_path,
        staging,
        {"success": True, "planner_backend": "template", "network_authorized": True},
        iteration=1,
        attempt_started_at=time.time(),
    )

    assert metadata["validation_status"] == "FAIL"
    assert any("without an allowed network scope" in issue for issue in metadata["issues"])


def test_network_authorization_rejects_resolved_claim_without_authorization(tmp_path: Path) -> None:
    staging = planner_staging_dir(tmp_path, 1, 1)
    _write_valid_candidate(staging)
    _mutate_planner_output(
        staging,
        lambda data: data.setdefault("completed_capabilities", []).append("network_authorization_resolved"),
    )

    metadata = validate_planner_transaction(
        tmp_path,
        staging,
        {"success": True, "planner_backend": "template", "network_authorized": False},
        iteration=1,
        attempt_started_at=time.time(),
    )

    assert metadata["validation_status"] == "FAIL"
    assert any("network_authorization_resolved" in issue for issue in metadata["issues"])


def test_template_backend_uses_same_renderer_and_transaction(tmp_path: Path) -> None:
    result = run_planner_agent(make_initial_state(tmp_path, planner_backend="template"))
    tx_path = artifact_dir(tmp_path) / "logs" / "planner_transaction.json"

    assert result["approval_ready"] is True
    assert tx_path.exists()
    assert report_path(tmp_path, "planner_output").exists()


def test_planner_backend_openclaw_uses_staging_transaction(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_run_openclaw_stage(
        repo_path,
        stage,
        input_path,
        allowed_outputs,
        *,
        session_key,
        iteration=None,
        **kwargs,
    ):
        captured["stage"] = stage
        captured["input_path"] = str(input_path)
        captured["allowed_outputs"] = list(allowed_outputs)
        captured["session_key"] = session_key
        input_text = Path(input_path).read_text(encoding="utf-8")
        assert "Allowed Writes" in input_text
        assert "PLANNER_OUTPUT.json" in input_text
        assert "TASK_SPEC.md" in input_text
        assert "EXPERIMENT_CONTRACT.md" in input_text
        assert "Do not write files." not in input_text
        assert "Do not call tools." not in input_text
        assert "Return JSON only." not in input_text
        assert "Missing Previous Result Artifacts" in input_text
        assert "do not call tools to read that path" in input_text
        assert "PlannerOutput JSON Schema" in input_text
        assert "Planner Input Bundle" in input_text
        staging = planner_staging_dir(repo_path, int(iteration or 1), 1)
        output = PlannerOutput.model_validate(_template_planner_output({"iteration": 1, "goal": "openclaw", "paper_bundle": {}}))
        (staging / "PLANNER_OUTPUT.json").write_text(render_planner_json(output), encoding="utf-8")
        (staging / "TASK_SPEC.md").write_text(render_task_spec(output), encoding="utf-8")
        (staging / "EXPERIMENT_CONTRACT.md").write_text(render_experiment_contract(output), encoding="utf-8")
        return {
            "success": True,
            "returncode": 0,
            "stdout_log_path": "stdout.log",
            "stderr_log_path": "stderr.log",
            "unexpected_modifications": [],
            "provider": "deepseek",
            "model": "deepseek-chat",
            "runner": "embedded",
            "fallbackUsed": False,
        }

    monkeypatch.setattr("r2a.agents.planner_agent.repo_runtime_dir", lambda repo: tmp_path / "runtime")
    monkeypatch.setattr("r2a.agents.planner_agent.openclaw_stage_runner.run_openclaw_stage", fake_run_openclaw_stage)

    result = run_planner_agent(make_initial_state(tmp_path, planner_backend="openclaw", goal="openclaw planner"))

    staging = planner_staging_dir(tmp_path, 1, 1)
    assert result["approval_ready"] is True
    assert result["planner_transaction"]["validation_status"] == "PASS"
    assert result["planner_transaction"]["diagnostic"]["planner_backend"] == "openclaw"
    assert result["planner_transaction"]["stdout_log_path"] == "stdout.log"
    committed = PlannerOutput.model_validate_json(report_path(tmp_path, "planner_output").read_text(encoding="utf-8"))
    assert committed.tasks[0].stop_conditions
    assert report_path(tmp_path, "planner_output").exists()
    assert report_path(tmp_path, "task").exists()
    assert report_path(tmp_path, "experiment_contract").exists()
    assert not (staging / "OPENCLAW_INPUT.md").exists()
    assert captured["allowed_outputs"] == [
        ".r2a/staging/planner/iter_001/attempt_001/PLANNER_OUTPUT.json",
        ".r2a/staging/planner/iter_001/attempt_001/TASK_SPEC.md",
        ".r2a/staging/planner/iter_001/attempt_001/EXPERIMENT_CONTRACT.md",
    ]
    assert str(captured["session_key"]).startswith("r2a-planner-")


def test_planner_backend_openclaw_canonicalizes_empty_stop_conditions(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run_openclaw_stage(repo_path, stage, input_path, allowed_outputs, *, session_key, iteration=None, **kwargs):
        staging = planner_staging_dir(repo_path, int(iteration or 1), 1)
        data = _template_planner_output({"iteration": 1, "goal": "openclaw", "paper_bundle": {}})
        data["tasks"][0]["stop_conditions"] = []
        data["tasks"][0]["task_kind"] = "source_verification"
        _write_openclaw_file_write_candidate(staging, data)
        return {
            "success": True,
            "returncode": 0,
            "stdout_log_path": "stdout.log",
            "stderr_log_path": "stderr.log",
            "unexpected_modifications": [],
            "provider": "deepseek",
            "model": "deepseek-chat",
            "runner": "embedded",
            "fallbackUsed": False,
        }

    monkeypatch.setattr("r2a.agents.planner_agent.repo_runtime_dir", lambda repo: tmp_path / "runtime")
    monkeypatch.setattr("r2a.agents.planner_agent.openclaw_stage_runner.run_openclaw_stage", fake_run_openclaw_stage)

    result = run_planner_agent(make_initial_state(tmp_path, planner_backend="openclaw", goal="openclaw planner"))

    assert result["approval_ready"] is True
    assert result["planner_transaction"]["validation_status"] == "PASS"
    committed = PlannerOutput.model_validate_json(report_path(tmp_path, "planner_output").read_text(encoding="utf-8"))
    assert committed.tasks[0].stop_conditions
    assert all(task.stop_conditions for task in committed.tasks)


def test_planner_backend_openclaw_rejects_uninferable_empty_stop_conditions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_run_openclaw_stage(repo_path, stage, input_path, allowed_outputs, *, session_key, iteration=None, **kwargs):
        staging = planner_staging_dir(repo_path, int(iteration or 1), 1)
        data = _raw_planner_output(
            {
                "task_id": "task_unknown",
                "title": "Do useful work",
                "objective": "Run an ambiguous task.",
                "actions": ["Run command"],
                "expected_outputs": [".r2a/results/notes.txt"],
                "stop_conditions": [],
            }
        )
        _write_openclaw_file_write_candidate(staging, data)
        return {
            "success": True,
            "returncode": 0,
            "stdout_log_path": "stdout.log",
            "stderr_log_path": "stderr.log",
            "unexpected_modifications": [],
            "provider": "deepseek",
            "model": "deepseek-chat",
            "runner": "embedded",
            "fallbackUsed": False,
        }

    monkeypatch.setattr("r2a.agents.planner_agent.repo_runtime_dir", lambda repo: tmp_path / "runtime")
    monkeypatch.setattr("r2a.agents.planner_agent.openclaw_stage_runner.run_openclaw_stage", fake_run_openclaw_stage)

    result = run_planner_agent(make_initial_state(tmp_path, planner_backend="openclaw", goal="openclaw planner"))

    assert result["stopped"] is True
    assert result["planner_status"] == "failed"
    assert result["planner_transaction"]["validation_status"] == "FAIL"
    assert result["planner_transaction"]["failure_category"] == "PLANNER_SCHEMA_VALIDATION_FAILED"
    assert "Cannot derive canonical stop_conditions" in result["planner_transaction"]["issues"][0]
    assert not report_path(tmp_path, "planner_output").exists()


def test_planner_backend_openclaw_failure_records_backend_diagnostics(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_run_openclaw_stage(repo_path, stage, input_path, allowed_outputs, *, session_key, iteration=None, **kwargs):
        return {
            "success": False,
            "returncode": 1,
            "failure_category": "PLANNER_BACKEND_FAILURE",
            "execution_status": "PLANNER_BACKEND_FAILURE",
            "stderr_tail": "read failed: ENOENT .r2a/results/reduced_metrics.csv",
            "stdout_tail": "",
            "stdout_log_path": "stdout.log",
            "stderr_log_path": "stderr.log",
            "invocation_manifest_path": str(tmp_path / ".r2a" / "logs" / "invocations" / "planner" / "invocation.json"),
            "invocation_log_dir": str(tmp_path / ".r2a" / "logs" / "invocations" / "planner"),
            "unexpected_modifications": [],
        }

    monkeypatch.setattr("r2a.agents.planner_agent.repo_runtime_dir", lambda repo: tmp_path / "runtime")
    monkeypatch.setattr("r2a.agents.planner_agent.openclaw_stage_runner.run_openclaw_stage", fake_run_openclaw_stage)

    result = run_planner_agent(make_initial_state(tmp_path, planner_backend="openclaw", goal="openclaw planner"))

    transaction = result["planner_transaction"]
    diagnostic = transaction["diagnostic"]
    assert result["stopped"] is True
    assert transaction["validation_status"] == "FAIL"
    assert transaction["failure_category"] == "PLANNER_MISSING_REQUIRED_OUTPUT"
    assert transaction["backend_failure_category"] == "PLANNER_BACKEND_FAILURE"
    assert transaction["backend_returncode"] == 1
    assert "reduced_metrics.csv" in transaction["backend_stderr_tail"]
    assert transaction["backend_invocation_manifest_path"].endswith("invocation.json")
    assert diagnostic["backend_failure_category"] == "PLANNER_BACKEND_FAILURE"
    assert diagnostic["backend_returncode"] == 1
    assert "reduced_metrics.csv" in diagnostic["backend_stderr_tail"]
