from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TypedDict
from uuid import uuid4

from r2a.core.config import DEFAULT_CLAUDE_EXECUTABLE, DEFAULT_CODEX_EXECUTABLE
from r2a.core.user_hints import build_user_hints, format_user_hints_markdown
from r2a.tools.wsl import DEFAULT_WSL_CACHE_DIR, DEFAULT_WSL_DISTRO

DEFAULT_MAX_ITERATIONS = 12


class R2AState(TypedDict, total=False):
    run_id: str
    run_manifest_path: str
    latest_run_manifest_path: str
    workspace_dir: str
    repo_path: str
    goal: str
    guidance: str
    resolved_goal: str
    paper_path: str
    extra_context: str
    user_hints: dict[str, Any]
    user_hints_path: str
    github_repo_url: str
    source_url: str
    dataset_urls: list[str]
    model_weight_urls: list[str]
    language: str
    output_language: str
    executor: str
    paper_backend: str
    planner_backend: str
    engineer_executor: str
    engineer_execution_environment: str
    wsl_distro: str
    wsl_cache_dir: str
    manager_backend: str
    reviewer_backend: str
    final_writer_backend: str
    stage_codex_enabled: bool
    codex_stage_timeout: int
    codex_executable_path: str
    claude_executable_path: str
    openclaw_executable_path: str
    openclaw_config_path: str
    openclaw_provider: str
    openclaw_model: str
    openclaw_runner: str
    openclaw_agent: str
    planner_provider: str
    planner_model: str
    engineer_provider: str
    engineer_model: str
    manager_provider: str
    manager_model: str
    reviewer_provider: str
    reviewer_model: str
    final_writer_provider: str
    final_writer_model: str
    final_writer_profile: str
    stage_model_selection: dict[str, Any]
    stage_api_keys: dict[str, str]
    stage_api_key_env_vars: dict[str, str]
    timeout: int
    strict: bool
    auto_approve: bool
    auto_iterate: bool
    approved: bool
    approval_ready: bool
    stopped: bool
    errors: list[str]
    warnings: list[str]
    paper_brief_path: str
    paper_evidence_path: str
    paper_text_path: str
    paper_context_path: str
    paper_reproduction_card_path: str
    paper_figures_tables_path: str
    paper_parse_quality_path: str
    paper_analysis_path: str
    paper_text_excerpt: str
    paper_context_excerpt: str
    paper_reproduction_card_excerpt: str
    paper_figures_tables_excerpt: str
    paper_parse_quality_excerpt: str
    paper_analysis_excerpt: str
    paper_extraction_status: str
    paper_text_length: int
    paper_queries_path: str
    paper_readiness: dict[str, Any]
    planner_readiness: dict[str, Any]
    engineer_readiness: dict[str, Any]
    source_acquisition: dict[str, Any]
    source_acquisition_path: str
    source_inspection: dict[str, Any]
    source_inspection_path: str
    next_planner_context_path: str
    evidence_used: list[str]
    clarification_needed: bool
    task_spec_path: str
    planner_output_path: str
    latest_planner_output_path: str
    planner_status: str
    planner_schema_version: str
    planning_mode: str
    iteration_strategy: str
    contract_mode: str
    planner_transaction: dict[str, Any]
    failed_stage: str
    execution_report_path: str
    check_report_path: str
    review_report_path: str
    review_feedback_path: str
    final_report_path: str
    final_report: str
    manager_passed: bool
    manager_max_level_allowed: str
    manager_decision_path: str
    iteration: int
    max_iterations: int
    loop_status: str
    stop_reason: str
    reviewer_verdict: str
    evidence_decision_path: str
    achieved_reproduction_level: str
    evidence_ladder: dict[str, Any]
    evidence_blocking_reasons: list[str]
    decision_status: dict[str, Any]
    workflow_blockers: list[dict[str, Any]]
    workflow_decision: dict[str, Any]
    manager_status: str
    manager_executed: bool
    reviewer_executed: bool
    engineer_backend_provider: str
    engineer_backend_model: str
    engineer_backend_runner: str
    engineer_backend_agent: str
    reproduction_level: str
    target_reproduction_level: str
    # 新增：正式业务等级字段（Reviewer 唯一写入）
    current_reproduction_level: str  # Reviewer 判断的当前等级，未评估时为 None 或 UNASSESSED
    current_level_iteration: int  # 该等级由哪一轮 Reviewer 产生
    # 兼容字段（compatibility-only，将由 Reviewer 同步）
    achieved_reproduction_level: str  # deprecated: use current_reproduction_level
    download_budget_gb: int
    prefer_minimal_subset: bool
    allow_official_dataset_download: bool
    allow_full_benchmark: bool
    allow_external_baselines: bool
    allow_network: bool
    network_authorized: bool
    allowed_network_scope: list[str]
    network_authorization_reason: str
    network_authorization: dict[str, Any]
    need_replan: bool
    iteration_dir: str
    runs_dir: str
    iteration_history: list[dict[str, Any]]
    latest_review_report_path: str
    latest_review_feedback_path: str
    latest_check_report_path: str
    latest_task_spec_path: str
    latest_execution_report_path: str
    latest_manager_codex_review_path: str
    metadata: dict[str, Any]


def make_initial_state(
    repo_path: str | Path,
    goal: str = "",
    workspace_dir: str | Path | None = None,
    paper_path: str | Path | None = None,
    guidance: str = "",
    resolved_goal: str | None = None,
    extra_context: str = "",
    language: str = "en",
    output_language: str = "",
    executor: str = "shell",
    paper_backend: str = "preprocess",
    planner_backend: str = "template",
    engineer_executor: str | None = None,
    engineer_execution_environment: str = "windows",
    wsl_distro: str = DEFAULT_WSL_DISTRO,
    wsl_cache_dir: str = DEFAULT_WSL_CACHE_DIR,
    manager_backend: str = "rules",
    reviewer_backend: str = "rules",
    final_writer_backend: str = "template",
    codex_stage_timeout: int = 10800,
    codex_executable_path: str = DEFAULT_CODEX_EXECUTABLE,
    claude_executable_path: str = DEFAULT_CLAUDE_EXECUTABLE,
    openclaw_executable_path: str = "",
    openclaw_config_path: str = "",
    openclaw_provider: str = "",
    openclaw_model: str = "",
    openclaw_runner: str = "",
    openclaw_agent: str = "",
    planner_provider: str = "",
    planner_model: str = "",
    engineer_provider: str = "",
    engineer_model: str = "",
    manager_provider: str = "",
    manager_model: str = "",
    reviewer_provider: str = "",
    reviewer_model: str = "",
    final_writer_provider: str = "",
    final_writer_model: str = "",
    final_writer_profile: str = "",
    stage_model_selection: dict[str, Any] | None = None,
    stage_api_keys: dict[str, str] | None = None,
    stage_api_key_env_vars: dict[str, str] | None = None,
    timeout: int = 10800,
    strict: bool = False,
    auto_approve: bool = False,
    auto_iterate: bool = False,
    max_iterations: int = DEFAULT_MAX_ITERATIONS,
    approved: bool = False,
    target_reproduction_level: str = "L4_reduced_paper_aligned",
    download_budget_gb: int = 20,
    prefer_minimal_subset: bool = True,
    allow_official_dataset_download: bool = False,
    allow_full_benchmark: bool = False,
    allow_external_baselines: bool = False,
    allow_network: bool = False,
    allowed_network_scope: list[str] | str | None = None,
    user_hints: dict[str, Any] | None = None,
    github_repo_url: str = "",
    source_url: str = "",
    dataset_urls: list[str] | str | None = None,
    model_weight_urls: list[str] | str | None = None,
) -> R2AState:
    max_iterations = max(1, int(max_iterations))
    final_goal = resolved_goal if resolved_goal is not None else goal
    resolved_engineer_executor = engineer_executor or executor
    run_id = _new_run_id()
    network_scope = _scope_list(allowed_network_scope)
    if allow_network and not network_scope:
        network_scope = ["external_git_clone_for_algorithm_dependencies"]
    if not allow_network:
        network_scope = []
    network_reason = "explicit_user_allowed_network" if allow_network else "network_not_authorized"
    raw_user_hints = dict(user_hints or {})
    structured_user_hints = build_user_hints(
        text=str(raw_user_hints.get("text") or guidance or goal or ""),
        source_urls=[github_repo_url, source_url, *_scope_list(raw_user_hints.get("source_urls"))],
        dataset_urls=[*_scope_list(dataset_urls), *_scope_list(raw_user_hints.get("dataset_urls"))],
        model_weight_urls=[*_scope_list(model_weight_urls), *_scope_list(raw_user_hints.get("model_weight_urls"))],
        other_urls=raw_user_hints.get("other_urls"),
        origin=str(raw_user_hints.get("origin") or "user_provided_hint"),
    )
    user_hint_context = format_user_hints_markdown(structured_user_hints)
    merged_extra_context = "\n\n".join(part for part in (extra_context.strip(), user_hint_context) if part)
    stage_codex_enabled = any(
        value in {"codex", "codex_review", "claude", "claude_review", "openclaw", "openclaw_review", "openclaw_reader"}
        for value in (paper_backend, planner_backend, resolved_engineer_executor, manager_backend, reviewer_backend, final_writer_backend)
    )
    return {
        "run_id": run_id,
        "workspace_dir": str(Path(workspace_dir)) if workspace_dir else "",
        "repo_path": str(Path(repo_path)),
        "goal": final_goal,
        "guidance": guidance,
        "resolved_goal": final_goal,
        "paper_path": str(Path(paper_path)) if paper_path else "",
        "extra_context": merged_extra_context,
        "user_hints": structured_user_hints,
        "user_hints_path": "",
        "github_repo_url": github_repo_url,
        "source_url": source_url,
        "dataset_urls": structured_user_hints["dataset_urls"],
        "model_weight_urls": structured_user_hints["model_weight_urls"],
        "language": language,
        "output_language": output_language or ("Chinese" if language == "zh" else "English"),
        "executor": resolved_engineer_executor,
        "paper_backend": paper_backend,
        "planner_backend": planner_backend,
        "engineer_executor": resolved_engineer_executor,
        "engineer_execution_environment": engineer_execution_environment,
        "wsl_distro": wsl_distro,
        "wsl_cache_dir": wsl_cache_dir,
        "manager_backend": manager_backend,
        "reviewer_backend": reviewer_backend,
        "final_writer_backend": final_writer_backend,
        "stage_codex_enabled": stage_codex_enabled,
        "codex_stage_timeout": codex_stage_timeout,
        "codex_executable_path": codex_executable_path or DEFAULT_CODEX_EXECUTABLE,
        "claude_executable_path": claude_executable_path or DEFAULT_CLAUDE_EXECUTABLE,
        "openclaw_executable_path": openclaw_executable_path,
        "openclaw_config_path": openclaw_config_path,
        "openclaw_provider": openclaw_provider,
        "openclaw_model": openclaw_model,
        "openclaw_runner": openclaw_runner,
        "openclaw_agent": openclaw_agent,
        "planner_provider": planner_provider,
        "planner_model": planner_model,
        "engineer_provider": engineer_provider,
        "engineer_model": engineer_model,
        "manager_provider": manager_provider,
        "manager_model": manager_model,
        "reviewer_provider": reviewer_provider,
        "reviewer_model": reviewer_model,
        "final_writer_provider": final_writer_provider,
        "final_writer_model": final_writer_model,
        "final_writer_profile": final_writer_profile,
        "stage_model_selection": dict(stage_model_selection or {}),
        "stage_api_keys": dict(stage_api_keys or {}),
        "stage_api_key_env_vars": dict(stage_api_key_env_vars or {}),
        "timeout": timeout,
        "strict": strict,
        "auto_approve": auto_approve,
        "auto_iterate": auto_iterate,
        "max_iterations": max_iterations,
        "approved": approved,
        "approval_ready": False,
        "stopped": False,
        "errors": [],
        "warnings": [],
        "evidence_used": [],
        "clarification_needed": False,
        "iteration": 1,
        "loop_status": "not_started",
        "stop_reason": "",
        "reviewer_verdict": "",
        "manager_status": "",
        "manager_executed": False,
        "reviewer_executed": False,
        "reproduction_level": "",  # 空，等待 Reviewer 判断。不再初始化为 L0_project_health。
        "target_reproduction_level": target_reproduction_level,
        # 正式等级快照（只有 AI backend 合法输出才能更新）
        "current_reproduction_level": None,  # 正式等级：None=未评估，L0-L6=AI 判断
        "current_level_iteration": 0,  # 正式等级产生的迭代轮次
        "level_source": "unassessed",  # 等级来源：ai_backend / rules_backend_no_level / invalid_* / legacy / unassessed
        "level_reasoning": "",  # AI 的等级推理说明
        "supporting_artifacts": [],  # 支撑证据产物
        "remaining_gaps": [],  # 剩余差距
        # 当前轮 Reviewer attempt 状态（每轮重置）
        "reviewer_backend": reviewer_backend,  # 当前轮使用的 backend
        "reviewer_level_valid": False,  # 当前轮 AI 输出是否有效
        "reviewer_level_rejection_reason": "",  # 当前轮无效原因
        "download_budget_gb": int(download_budget_gb),
        "prefer_minimal_subset": bool(prefer_minimal_subset),
        "allow_official_dataset_download": bool(allow_official_dataset_download),
        "allow_full_benchmark": bool(allow_full_benchmark),
        "allow_external_baselines": bool(allow_external_baselines),
        "allow_network": bool(allow_network),
        "network_authorized": bool(allow_network),
        "allowed_network_scope": network_scope,
        "network_authorization_reason": network_reason,
        "network_authorization": {
            "schema_version": 1,
            "network_authorized": bool(allow_network),
            "allowed_network_scope": network_scope,
            "network_authorization_reason": network_reason,
        },
        "need_replan": False,
        "iteration_history": [],
        "metadata": {"user_hints": structured_user_hints},
        "decision_status": {},
        "workflow_blockers": [],
        "workflow_decision": {},
        "paper_readiness": {},
        "planner_readiness": {},
        "engineer_readiness": {},
        "source_acquisition": {},
        "source_acquisition_path": "",
        "source_inspection": {},
        "source_inspection_path": "",
        "next_planner_context_path": "",
    }


def _scope_list(value: list[str] | str | None) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        return [item.strip() for item in value.replace(";", ",").replace("\n", ",").split(",") if item.strip()]
    return []


def _new_run_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{uuid4().hex[:8]}"
