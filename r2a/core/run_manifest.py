from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from r2a.core.config import REPORT_FILENAMES
from r2a.core.final_decision import UNASSESSED as FINAL_UNASSESSED, read_final_decision
from r2a.core.evidence_level_compat import (
    read_current_reproduction_level,
    read_current_level_iteration,
    is_reviewer_executed,
    UNASSESSED,
)
from r2a.core.paths import artifact_dir, latest_dir, latest_run_manifest_path, report_path, run_dir, run_manifest_path
from r2a.core.verdicts import PASS_LIKE_VERDICTS


STAGES = ("paper", "planner", "approval", "engineer", "manager", "reviewer", "final")
PASS_VERDICTS = PASS_LIKE_VERDICTS
RUNTIME_TERMINAL_STATUSES = {
    "cancelled",
    "completed",
    "completed_success",
    "completed_with_failure",
    "completed_with_limitations",
    "failed",
    "failed_to_kill",
    "force_killed",
    "stopped",
    "terminal_failed",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def ensure_run_id(state: dict[str, Any]) -> str:
    run_id = str(state.get("run_id", "") or "").strip()
    if run_id:
        return run_id
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-adhoc"


def mark_stage_started(state: dict[str, Any], stage: str) -> dict[str, Any]:
    updated = {**state, "run_id": ensure_run_id(state)}
    manifest = _load_manifest(updated)
    stage_record = dict(manifest.get("stages", {}).get(stage, {}) or {})
    stage_record.setdefault("started_at", utc_now())
    stage_record["status"] = "RUNNING"
    manifest.setdefault("stages", {})[stage] = stage_record
    manifest["current_stage"] = stage
    manifest["status"] = "RUNNING"
    _write_manifest_files(updated, manifest)
    return _attach_paths(updated)


def mark_stage_finished(
    state: dict[str, Any],
    stage: str,
    *,
    status: str,
    errors: list[str] | None = None,
    warnings: list[str] | None = None,
    artifacts: list[str] | None = None,
) -> dict[str, Any]:
    updated = _attach_paths({**state, "run_id": ensure_run_id(state)})
    manifest = _load_manifest(updated)
    stage_record = dict(manifest.get("stages", {}).get(stage, {}) or {})
    stage_record.setdefault("started_at", utc_now())
    stage_record["finished_at"] = utc_now()
    stage_record["status"] = status
    stage_record["errors"] = list(errors or [])[:20]
    stage_record["warnings"] = list(warnings or [])[:20]
    stage_record["artifacts"] = list(artifacts or _stage_artifacts(Path(str(updated["repo_path"])), stage))
    diagnostics = _stage_diagnostics(updated, stage)
    if diagnostics:
        stage_record["diagnostics"] = diagnostics
    manifest.setdefault("stages", {})[stage] = stage_record
    manifest["current_stage"] = stage
    manifest = _refresh_manifest(updated, manifest)
    _write_manifest_files(updated, manifest)
    return updated


def write_run_manifest(state: dict[str, Any]) -> Path:
    updated = _attach_paths({**state, "run_id": ensure_run_id(state)})
    manifest = _load_manifest(updated)
    manifest = _refresh_manifest(updated, manifest)
    path = _write_manifest_files(updated, manifest)
    _sync_latest_files(Path(str(updated["repo_path"])))
    return path


def read_latest_manifest(repo_path: str | Path) -> dict[str, Any]:
    path = latest_run_manifest_path(repo_path)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def sync_manifest_terminal_status_from_runtime(
    repo_path: str | Path,
    run_id: str,
    runtime_record: dict[str, Any],
) -> bool:
    """Mirror terminal runtime status into RUN_MANIFEST top-level fields.

    Runtime records are the live truth. RUN_MANIFEST remains an artifact
    summary, so this function only patches top-level lifecycle diagnostics and
    deliberately avoids reproduction-level or verdict fields.
    """
    status = str((runtime_record or {}).get("status", "") or "").lower()
    if status not in RUNTIME_TERMINAL_STATUSES:
        return False
    repo = Path(repo_path)
    latest = latest_run_manifest_path(repo)
    if not latest.exists():
        return False
    try:
        manifest = json.loads(latest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(manifest, dict):
        return False

    current_stage = str(runtime_record.get("current_stage", "") or manifest.get("current_stage", "") or "")
    stop_reason = str(
        runtime_record.get("termination_reason", "")
        or runtime_record.get("error_code", "")
        or manifest.get("stop_reason", "")
        or status
    )
    updated = {
        **manifest,
        "status": status,
        "current_stage": current_stage,
        "finished_at": manifest.get("finished_at") or utc_now(),
        "stop_reason": stop_reason,
        "termination_reason": str(runtime_record.get("termination_reason", "") or ""),
        "runtime_status_source": "runtime_record",
        "runtime_run_id": str(run_id),
        "runtime_record_status": status,
        "updated_at": utc_now(),
    }
    text = json.dumps(updated, indent=2, ensure_ascii=False)
    try:
        latest.parent.mkdir(parents=True, exist_ok=True)
        latest.write_text(text, encoding="utf-8")
        manifest_run_id = str(updated.get("run_id", "") or "")
        if manifest_run_id:
            primary = run_manifest_path(repo, manifest_run_id)
            primary.parent.mkdir(parents=True, exist_ok=True)
            primary.write_text(text, encoding="utf-8")
    except OSError:
        return False
    return True


def _attach_paths(state: dict[str, Any]) -> dict[str, Any]:
    repo = Path(str(state["repo_path"]))
    run_id = ensure_run_id(state)
    return {
        **state,
        "run_id": run_id,
        "run_manifest_path": str(run_manifest_path(repo, run_id)),
        "latest_run_manifest_path": str(latest_run_manifest_path(repo)),
    }


def _load_manifest(state: dict[str, Any]) -> dict[str, Any]:
    repo = Path(str(state["repo_path"]))
    run_id = ensure_run_id(state)
    for path in (run_manifest_path(repo, run_id), latest_run_manifest_path(repo)):
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if data.get("run_id") == run_id:
                return data
        except (OSError, json.JSONDecodeError):
            continue
    return _new_manifest(state)


def _new_manifest(state: dict[str, Any]) -> dict[str, Any]:
    repo = Path(str(state["repo_path"]))
    run_id = ensure_run_id(state)
    return {
        "schema_version": 1,
        "run_id": run_id,
        "repo_path": str(repo),
        "workspace_dir": str(state.get("workspace_dir", "") or ""),
        "paper_path": str(state.get("paper_path", "") or ""),
        "goal": str(state.get("goal", "") or ""),
        "target_level": str(state.get("target_reproduction_level", "L4_reduced_paper_aligned") or ""),
        # 新字段：从 state 读取 Reviewer 判断的等级
        "current_level": "",  # Reviewer 未执行时为空
        "current_level_iteration": 0,
        # 兼容字段
        "achieved_level": "",
        "status": "RUNNING",
        "current_stage": "not_started",
        "started_at": utc_now(),
        "finished_at": "",
        "stages": {stage: {"status": "PENDING", "errors": [], "warnings": [], "artifacts": []} for stage in STAGES},
        "outputs": {},
        "evidence": {},
        "openclaw": _openclaw_config(state),
        "blocking_reasons": [],
    }


def _refresh_manifest(state: dict[str, Any], manifest: dict[str, Any]) -> dict[str, Any]:
    """刷新 RUN_MANIFEST。

    只从 state 读取等级，不调用 evaluate_l0_l4 或 infer_evidence_level。
    """
    repo = Path(str(state["repo_path"]))
    loop_status = str(state.get("loop_status", "") or "")
    decision_status = dict(state.get("decision_status", {}) or {})
    typed_decision = str(decision_status.get("typed_decision", "") or "")

    # 使用兼容读取函数
    # 只读取已有等级，不进行文件推断
    reviewer_executed = is_reviewer_executed(state)
    current_level = read_current_reproduction_level(state, reviewer_executed=reviewer_executed)
    current_level_iteration = read_current_level_iteration(state, reviewer_executed=reviewer_executed)

    # 如果 Reviewer 未执行或等级为 None，使用空字符串
    if not current_level:
        current_level = ""  # Reviewer 未执行
    elif current_level == UNASSESSED:
        current_level = ""  # 未评估

    final_decision = read_final_decision(repo)
    final_decision_accepted = str(final_decision.get("accepted_level", "") or "") if final_decision else ""
    final_decision_observed = str(final_decision.get("observed_level", "") or "") if final_decision else ""
    if final_decision:
        current_level = "" if final_decision_accepted == FINAL_UNASSESSED else final_decision_accepted

    # 构建 evidence 用于兼容（不再依赖 evidence_ladder）
    target_level = str(final_decision.get("target_level", "") or state.get("target_reproduction_level", "L4_reduced_paper_aligned") or "")
    evidence = {
        "target_level": target_level,
        "target_label": "",
        "achieved_level": current_level,
        "achieved_label": "",
        "status": "PASS" if state.get("reviewer_verdict", "") in PASS_VERDICTS and current_level else ("UNASSESSED" if final_decision_accepted == FINAL_UNASSESSED else "FAIL"),
        # evidence_ladder 已废弃
        "levels": {},
        "blocking_reasons": list(state.get("evidence_blocking_reasons", []) or []),
        "summary": "",
        "reviewer_completed": reviewer_executed,
    }

    status = str(manifest.get("status") or "RUNNING")
    if _is_terminal_state(state, manifest):
        if final_decision.get("final_status"):
            status = str(final_decision.get("final_status"))
        elif typed_decision == "stop_success":
            status = "completed_success"
        elif typed_decision == "stop_evidence_cap":
            status = "completed_with_limitations"
        elif loop_status in {"failed", "completed_with_failure"} or _has_terminal_failure(state):
            status = "completed_with_failure"
        else:
            status = "completed_success" if evidence["status"] == "PASS" else evidence["status"]

    refreshed = {
        **manifest,
        "repo_path": str(repo),
        "workspace_dir": str(state.get("workspace_dir", manifest.get("workspace_dir", "")) or ""),
        "paper_path": str(state.get("paper_path", manifest.get("paper_path", "")) or ""),
        "goal": str(state.get("goal", manifest.get("goal", "")) or ""),
        "target_level": target_level,
        "target_label": evidence["target_label"],
        # 正式等级字段
        "current_reproduction_level": current_level,
        "current_level_iteration": current_level_iteration,
        "accepted_level": current_level or FINAL_UNASSESSED,
        "observed_level": final_decision_observed or current_level or "",
        "level_source": str(state.get("level_source", "unassessed") or "unassessed"),
        "level_reasoning": str(state.get("level_reasoning", "") or ""),
        "reviewer_executed": bool(state.get("reviewer_executed", False)),
        "reviewer_level_valid": bool(state.get("reviewer_level_valid", False)),
        "reviewer_backend": str(state.get("reviewer_backend", "rules") or "rules"),
        # 兼容字段
        "current_level": current_level,
        "achieved_level": current_level,
        "achieved_label": evidence["achieved_label"],
        "status": status,
        "final_verdict": str(final_decision.get("formal_verdict", "") or state.get("reviewer_verdict", "") or typed_decision or manifest.get("final_verdict", "")),
        "decision_status": decision_status,
        "final_decision": final_decision,
        "final_decision_path": str(report_path(repo, "final_decision")) if final_decision else "",
        "stop_reason": str(final_decision.get("stop_reason", "") or decision_status.get("reason_code", "") or state.get("stop_reason", "") or manifest.get("stop_reason", "")),
        "manager_status": _manager_status(repo, state, manifest),
        "manager_max_level_allowed": _manager_max_level_allowed(repo, state, manifest),
        "openclaw": _openclaw_config(state),
        "stage_models": _stage_models(state, repo),
        "evidence": evidence["levels"],
        "blocking_reasons": evidence["blocking_reasons"],
        "summary": evidence["summary"],
        "outputs": _output_index(repo, state),
        "updated_at": utc_now(),
    }
    if state.get("loop_status") == "completed" or state.get("final_report_path"):
        refreshed["finished_at"] = refreshed.get("finished_at") or utc_now()
    return refreshed


def _write_manifest_files(state: dict[str, Any], manifest: dict[str, Any]) -> Path:
    repo = Path(str(state["repo_path"]))
    run_id = ensure_run_id(state)
    primary = run_manifest_path(repo, run_id)
    latest = latest_run_manifest_path(repo)
    primary.parent.mkdir(parents=True, exist_ok=True)
    latest.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(manifest, indent=2, ensure_ascii=False)
    primary.write_text(text, encoding="utf-8")
    latest.write_text(text, encoding="utf-8")
    return primary


def _is_terminal_state(state: dict[str, Any], manifest: dict[str, Any]) -> bool:
    loop_status = str(state.get("loop_status", "") or "")
    if loop_status in {"completed", "completed_with_failure", "failed"}:
        return True
    if state.get("final_report_path"):
        return True
    final_stage = dict(manifest.get("stages", {}).get("final", {}) or {})
    return bool(final_stage.get("finished_at"))


def _has_terminal_failure(state: dict[str, Any]) -> bool:
    decision_status = state.get("decision_status")
    if isinstance(decision_status, dict) and decision_status:
        typed = str(decision_status.get("typed_decision", "") or "")
        return typed in {"terminal_failed", "request_paper", "request_source", "request_dataset", "request_approval"}
    reviewer_verdict = str(state.get("reviewer_verdict", "") or "").upper()
    if reviewer_verdict in {
        "REJECT",
        "NEEDS_FIX",
        "NEEDS_INPUT",
        "NEEDS_OFFICIAL_INPUT",
        "NEEDS_INPUT_OR_BUDGET",
        "BORDERLINE",
    }:
        return True
    if str(state.get("manager_status", "") or "").upper() == "FAIL":
        return True
    if str(state.get("engineer_status", "") or "").upper() == "FAIL":
        return True
    if state.get("planner_status") == "failed" or state.get("loop_status") == "planner_failed":
        return True
    return False


def _manager_status(repo: Path, state: dict[str, Any], manifest: dict[str, Any]) -> str:
    explicit = str(state.get("manager_status", "") or "")
    decision_status = _manager_decision_value(repo, "status")
    return decision_status or explicit or str(manifest.get("manager_status", "") or "")


def _manager_max_level_allowed(repo: Path, state: dict[str, Any], manifest: dict[str, Any]) -> str:
    explicit = str(state.get("manager_max_level_allowed", "") or "")
    decision_level = _manager_decision_value(repo, "max_level_allowed")
    return decision_level or explicit or str(manifest.get("manager_max_level_allowed", "") or "")


def _manager_decision_value(repo: Path, key: str) -> str:
    path = report_path(repo, "manager_decision")
    if not path.exists():
        return ""
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError):
        return ""
    value = data.get(key, "") if isinstance(data, dict) else ""
    return str(value or "")


def _output_index(repo: Path, state: dict[str, Any]) -> dict[str, Any]:
    reports = {}
    for key, filename in REPORT_FILENAMES.items():
        path = artifact_dir(repo) / filename
        if path.exists():
            reports[key] = str(path)
    result_files = [str(path) for path in _existing_files(artifact_dir(repo) / "results")]
    repo_results = [str(path) for path in _existing_files(repo / "results")]
    logs = [str(path) for path in _existing_files(artifact_dir(repo) / "logs")]
    return {
        "reports": reports,
        "r2a_results": result_files,
        "repo_results": repo_results,
        "logs": logs[:100],
        "final_report": str(state.get("final_report_path", report_path(repo, "final"))),
    }


def _existing_files(root: Path) -> list[Path]:
    if not root.exists():
        return []
    return sorted(path for path in root.rglob("*") if path.is_file())


def _stage_artifacts(repo: Path, stage: str) -> list[str]:
    mapping = {
        "paper": ("paper", "paper_evidence", "paper_context", "paper_reproduction_card", "paper_parse_quality"),
        "planner": ("task", "experiment_contract"),
        "engineer": ("execution",),
        "manager": ("check", "manager_decision"),
        "reviewer": ("review", "review_verdict", "review_feedback", "evidence_decision"),
        "final": ("final_decision", "final_narrative", "final_writer_metadata", "final"),
    }
    artifacts: list[str] = []
    for key in mapping.get(stage, ()):
        path = report_path(repo, key)
        if path.exists():
            artifacts.append(str(path))
    return artifacts


def _stage_diagnostics(state: dict[str, Any], stage: str) -> dict[str, Any]:
    if stage == "planner":
        transaction = state.get("planner_transaction", {}) if isinstance(state.get("planner_transaction"), dict) else {}
        diagnostic = transaction.get("diagnostic", {}) if isinstance(transaction.get("diagnostic"), dict) else {}
        keys = (
            "planner_backend",
            "planner_status",
            "failure_category",
            "failure_reason",
            "provider",
            "model",
            "runner",
            "configured_provider",
            "configured_model",
            "configured_runner",
            "configured_agent",
            "backend_failure_category",
        )
        return {key: diagnostic.get(key, "") for key in keys if diagnostic.get(key, "")}
    if stage == "engineer":
        keys = (
            "engineer_status",
            "engineer_executor_failure_category",
            "engineer_executor_unavailable",
            "engineer_backend_provider",
            "engineer_backend_model",
            "engineer_backend_runner",
            "engineer_backend_agent",
        )
        return {key: state.get(key, "") for key in keys if state.get(key, "") not in {"", None}}
    if stage == "manager":
        keys = ("manager_status", "manager_max_level_allowed")
        return {key: state.get(key, "") for key in keys if state.get(key, "")}
    if stage == "reviewer":
        keys = ("reviewer_verdict", "achieved_reproduction_level")
        return {key: state.get(key, "") for key in keys if state.get(key, "")}
    if stage == "final":
        keys = ("loop_status", "stop_reason", "failure_category")
        diagnostics = {key: state.get(key, "") for key in keys if state.get(key, "")}
        decision = state.get("decision_status")
        if isinstance(decision, dict) and decision:
            diagnostics["typed_decision"] = decision.get("typed_decision", "")
            diagnostics["reason_code"] = decision.get("reason_code", "")
        final_writer = _final_writer_metadata(Path(str(state["repo_path"])))
        if final_writer:
            diagnostics["final_writer_backend"] = final_writer.get("backend", "")
            diagnostics["final_writer_model"] = final_writer.get("model", "")
            diagnostics["final_writer_output_path"] = final_writer.get("output_path", "")
        return diagnostics
    return {}


def _sync_latest_files(repo: Path) -> None:
    latest = latest_dir(repo)
    latest.mkdir(parents=True, exist_ok=True)
    for key in ("planner_output", "task", "experiment_contract", "execution", "check", "manager_decision", "review", "review_verdict", "evidence_decision", "final_decision", "final_narrative", "final_writer_metadata", "final"):
        path = report_path(repo, key)
        if path.exists():
            shutil.copy2(path, latest / path.name)


def _openclaw_config(state: dict[str, Any]) -> dict[str, Any]:
    from r2a.tools.openclaw_stage_runner import openclaw_config_from_state, openclaw_stage_profiles

    return {**openclaw_config_from_state(state), "stage_profiles": openclaw_stage_profiles()}


def _stage_models(state: dict[str, Any], repo: Path) -> dict[str, Any]:
    from r2a.tools.openclaw_stage_runner import openclaw_stage_model_config_from_state

    backends = {
        "planner": str(state.get("planner_backend", "template") or "template"),
        "engineer": str(state.get("engineer_executor", state.get("executor", "shell")) or "shell"),
        "manager": str(state.get("manager_backend", "rules") or "rules"),
        "reviewer": str(state.get("reviewer_backend", "rules") or "rules"),
    }
    models: dict[str, Any] = {}
    for stage, backend in backends.items():
        if backend in {"openclaw", "openclaw_review", "openclaw_reader"}:
            config = openclaw_stage_model_config_from_state(state, stage)
            models[stage] = {
                "backend": "openclaw",
                "provider": config.get("provider", ""),
                "model": config.get("model", ""),
                "profile": config.get("profile", stage),
                "runner": config.get("runner", ""),
            }
        else:
            models[stage] = {
                "backend": backend,
                "provider": "",
                "model": "none",
                "profile": "",
            }

    writer = _final_writer_metadata(repo)
    if writer:
        models["final_writer"] = {
            "backend": writer.get("backend", "template"),
            "provider": writer.get("provider", ""),
            "model": writer.get("model", "none"),
            "profile": writer.get("profile", ""),
            "runner": writer.get("runner", ""),
            "mode": writer.get("mode", "narrative_only"),
        }
    else:
        backend = str(state.get("final_writer_backend", "template") or "template")
        if backend == "openclaw":
            config = openclaw_stage_model_config_from_state(state, "final_writer")
            models["final_writer"] = {
                "backend": "openclaw",
                "provider": config.get("provider", ""),
                "model": config.get("model", ""),
                "profile": config.get("profile", "final_writer"),
                "runner": config.get("runner", ""),
                "mode": "narrative_only",
            }
        else:
            models["final_writer"] = {
                "backend": "template",
                "provider": "",
                "model": "none",
                "profile": "",
                "mode": "narrative_only",
            }
    return models


def _final_writer_metadata(repo: Path) -> dict[str, Any]:
    path = report_path(repo, "final_writer_metadata")
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}
