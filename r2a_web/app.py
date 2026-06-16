from __future__ import annotations
import json
import os
from datetime import datetime
from pathlib import Path
import tempfile
import threading

import pandas as pd

try:
    import streamlit as st
except ModuleNotFoundError:  # pragma: no cover - exercised by import-safety tests
    st = None

from r2a.agents.manager_agent import run_manager_agent
from r2a.agents.reviewer_agent import run_reviewer_agent
from r2a.core.config import DEFAULT_CLAUDE_EXECUTABLE, DEFAULT_CODEX_EXECUTABLE
from r2a.core.paths import report_path
from r2a.core.run_manifest import read_latest_manifest
from r2a.core.user_hints import build_user_hints, format_user_hints_markdown, normalize_user_hints
from r2a.tools.claude_runner import check_claude_code_cli
from r2a.tools.codex_cli import check_codex_cli
from r2a.tools.csv_sanitizer import sanitized_csv_frame, sanitized_csv_rows
from r2a.tools.iteration import archive_current_iteration
from r2a.tools.openclaw_stage_runner import (
    detect_openclaw_model_profiles,
    openclaw_stage_profiles,
    resolve_openclaw_config,
    test_openclaw_configuration,
)
from r2a.tools.paper_structure import summarize_structure
from r2a.tools.gateway_preflight import check_gateway_preflight
from r2a.core.runtime_paths import runtime_runs_dir
from r2a.tools.process_manager import (
    create_run_record,
    latest_run_id,
    new_run_id,
    read_run_record,
    read_run_result,
    request_cancel,
    update_run_record,
    update_run_heartbeat,
    workflow_run_context,
    write_run_result,
)
from r2a.tools.reproduction_levels import REPRODUCTION_LEVELS
from r2a.tools.stage_env import DEFAULT_STAGE_API_KEY_ENV
from r2a.tools.wsl import DEFAULT_WSL_CACHE_DIR, DEFAULT_WSL_DISTRO, check_wsl
from r2a.tools.web_runtime_registry import check_registry, clear_web_registry, existing_server_status, web_registry_path
from r2a.workflow.graph import create_research_graph
from r2a.workflow.nodes import final_node
from r2a.workflow.router import route_after_planner, route_after_reviewer
from r2a.core.feature_flags import (
    FEATURE_AUTO_ITERATE,
    FEATURE_RESTORE_PREVIOUS_RUN,
    FEATURE_UI_POLLING,
    feature_enabled,
    minimal_workflow_defaults,
    minimal_workflow_mode,
)
from r2a.workspace.manager import DEFAULT_WORKSPACE_BASE, create_workspace
from r2a.workspace.manifest import build_workspace_manifest, read_workspace_manifest, write_workspace_manifest
from r2a.core.state import DEFAULT_MAX_ITERATIONS
from r2a_web.workspace_state import (
    apply_workspace_session,
    autorefresh_decision,
    planner_backend_ready as workspace_planner_backend_ready,
    restore_runtime_run_session,
    restore_runtime_run_session_by_scan,
    restore_workspace_session,
    run_workflow_button_disabled,
    sync_background_run_readonly,
)

REPORTS = [
    ("Final 最终报告", "final"),
    ("L4 对齐证据包", "l4_alignment_summary"),
    ("Reviewer 评审报告", "review"),
    ("Reviewer 机器判定", "review_verdict"),
    ("Manager 检查报告", "check"),
    ("Engineer 执行报告", "execution"),
    ("Planner 任务书", "task"),
    ("Planner V2 JSON", "planner_output"),
    ("Experiment Contract", "experiment_contract"),
    ("Manager Codex 评审", "manager_codex_review"),
    ("Paper 综合分析", "paper_analysis"),
    ("Paper 复现卡", "paper_reproduction_card"),
    ("Paper 图表信息", "paper_figures_tables"),
    ("Paper 表格解析质量", "paper_parse_quality"),
    ("Paper 上下文", "paper_context"),
    ("Paper 原文抽取", "paper_text"),
    ("Paper 分节文本", "paper_sections"),
    ("Paper 图表标题", "paper_captions"),
    ("Paper 分页文本", "paper_pages"),
    ("Paper 简报", "paper"),
    ("Paper 证据", "paper_evidence"),
]

_MINIMAL_DEFAULTS = minimal_workflow_defaults()
DEFAULT_PAPER_BACKEND = str(_MINIMAL_DEFAULTS["paper_backend"])
DEFAULT_PLANNER_BACKEND = str(_MINIMAL_DEFAULTS["planner_backend"])
DEFAULT_ENGINEER_EXECUTOR = str(_MINIMAL_DEFAULTS["engineer_executor"])
DEFAULT_MANAGER_BACKEND = str(_MINIMAL_DEFAULTS["manager_backend"])
DEFAULT_REVIEWER_BACKEND = str(_MINIMAL_DEFAULTS["reviewer_backend"])
DEFAULT_FINAL_WRITER_BACKEND = "openclaw"
DEFAULT_AUTO_ITERATE = bool(_MINIMAL_DEFAULTS["auto_iterate"])
DEFAULT_AUTO_APPROVE = bool(_MINIMAL_DEFAULTS["auto_approve"])
DEFAULT_MAX_ITERATIONS_MINIMAL = int(_MINIMAL_DEFAULTS["max_iterations"])
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg"}
TABLE_EXTENSIONS = {".csv", ".tsv"}
TEXT_RESULT_EXTENSIONS = {".md", ".txt", ".json", ".log"}
HTML_EXTENSIONS = {".html", ".htm"}

DEFAULT_GOAL = "Create a conservative, reduced, evidence-limited reproduction plan from the uploaded paper and available context."
DEFAULT_TARGET_REPRODUCTION_LEVEL = "L4_reduced_paper_aligned"
DEFAULT_ENGINEER_EXECUTION_ENVIRONMENT = "wsl"
DEFAULT_ALLOWED_NETWORK_SCOPE = "external_git_clone_for_algorithm_dependencies"
WEB_SETTINGS_SCHEMA_VERSION = 4


def _default_web_settings_path() -> Path:
    configured = os.environ.get("R2A_WEB_SETTINGS_PATH", "").strip()
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".r2a" / "web_settings.json"


WEB_SETTINGS_PATH = _default_web_settings_path()
STAGE_API_KEY_LABELS = [
    ("paper", "Paper"),
    ("planner", "Planner"),
    ("engineer", "Engineer"),
    ("manager", "Manager"),
    ("reviewer", "Reviewer"),
    ("final_writer", "Final Writer"),
]
OPENCLAW_MODEL_STAGE_LABELS = [
    ("paper", "Paper"),
    ("planner", "Planner"),
    ("engineer", "Engineer"),
    ("manager", "Manager"),
    ("reviewer", "Reviewer"),
    ("final_writer", "Final Writer / Report Writer"),
]
WORKFLOW_STAGE_ORDER = [
    ("paper", "Paper"),
    ("planner", "Planner"),
    ("approval", "Approval"),
    ("engineer", "Engineer"),
    ("manager", "Manager"),
    ("reviewer", "Reviewer"),
    ("final", "Final"),
]
NODE_TO_STAGE = {
    "paper_node": "paper",
    "planner_node": "planner",
    "human_approval_node": "approval",
    "engineer_node": "engineer",
    "manager_node": "manager",
    "reviewer_node": "reviewer",
    "final_node": "final",
}
BACKEND_DISPLAY_NAMES = {
    "preprocess": "Local preprocess",
    "ai_reader": "Codex AI Reader",
    "claude_reader": "Claude Code Reader",
    "openclaw_reader": "OpenClaw Reader",
    "template": "Template",
    "mock": "Mock",
    "ccr_text": "CCR Text JSON",
    "command": "Command Text JSON",
    "openai_compatible": "OpenAI-compatible Text JSON",
    "anthropic": "Anthropic Text JSON",
    "codex": "Codex",
    "claude": "Claude Code",
    "openclaw": "OpenClaw",
    "shell": "Shell",
    "rules": "Rules",
    "codex_review": "Codex Review",
    "claude_review": "Claude Code Review",
    "openclaw_review": "OpenClaw Review",
}


def _network_scope_ui_copy() -> dict[str, str]:
    return {
        "toggle_label": "允许有限网络获取算法依赖",
        "toggle_help": (
            "Allows bounded network only for algorithm dependency/source acquisition tasks that Planner explicitly requests. "
            "It does not authorize full datasets, full benchmarks, or arbitrary web search."
        ),
        "caption": "Network remains bounded to the raw scope in Advanced settings.",
        "advanced_label": "Advanced network scope",
        "raw_label": "allowed_network_scope",
        "default_scope": DEFAULT_ALLOWED_NETWORK_SCOPE,
    }
PROVIDER_DISPLAY_NAMES = {
    "auto": "Auto / CLI config",
    "anthropic": "Anthropic Claude",
    "deepseek": "DeepSeek",
    "glm": "GLM / Zhipu",
    "openai": "OpenAI",
    "openrouter": "OpenRouter",
    "custom": "Custom",
}
PROVIDER_ENV_DEFAULTS = {
    "anthropic": "ANTHROPIC_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "glm": "ZHIPUAI_API_KEY",
    "openai": "OPENAI_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
}
KEY_SOURCE_DISPLAY_NAMES = {
    "env": "Use saved/CLI env",
    "direct": "Paste API key",
}
KEYLESS_BACKENDS = {
    "openclaw",
    "openclaw_reader",
    "openclaw_review",
    "rules",
    "mock",
    "shell",
    "template",
    "preprocess",
    "ai_reader",
    "ccr",
    "ccr_text",
    "command",
}
OPENCLAW_BACKENDS = {"openclaw", "openclaw_reader", "openclaw_review"}
STAGE_BACKEND_SETTING_KEYS = {
    "paper": "paper_backend",
    "planner": "planner_backend",
    "engineer": "engineer_executor",
    "manager": "manager_backend",
    "reviewer": "reviewer_backend",
    "final_writer": "final_writer_backend",
}
OPENCLAW_STAGE_BACKEND_DEFAULTS = {
    "paper_backend": DEFAULT_PAPER_BACKEND,
    "planner_backend": DEFAULT_PLANNER_BACKEND,
    "engineer_executor": DEFAULT_ENGINEER_EXECUTOR,
    "manager_backend": DEFAULT_MANAGER_BACKEND,
    "reviewer_backend": DEFAULT_REVIEWER_BACKEND,
    "final_writer_backend": DEFAULT_FINAL_WRITER_BACKEND,
}


def read_report(repo_path: str | Path, name: str) -> str:
    if name == "l4_alignment_summary":
        path = Path(repo_path) / ".r2a" / "results" / "L4_ALIGNMENT_SUMMARY.md"
    else:
        path = report_path(repo_path, name)
    if not path.exists():
        return "Not generated yet."
    return path.read_text(encoding="utf-8", errors="replace")


def read_log(repo_path: str | Path, filename: str) -> str:
    path = Path(repo_path) / ".r2a" / "logs" / filename
    if not path.exists():
        return "Not available."
    return path.read_text(encoding="utf-8", errors="replace")


def read_iteration_history(repo_path: str | Path) -> dict:
    path = Path(repo_path) / ".r2a" / "ITERATION_STATE.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _read_json_file(path: str | Path) -> dict:
    candidate = Path(path)
    if not candidate.exists() or not candidate.is_file():
        return {}
    try:
        data = json.loads(candidate.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _list_existing_runs(base_dir: str | Path) -> list[dict]:
    base = Path(base_dir).expanduser()
    if not base.exists():
        return []
    runs: list[dict] = []
    for metadata_path in sorted(base.glob("run_*/metadata.json"), reverse=True):
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        repo_path = Path(str(metadata.get("repo_path", "")))
        if not repo_path.exists():
            continue
        runs.append(
            {
                "run_id": metadata.get("run_id", metadata_path.parent.name),
                "created_at": metadata.get("created_at", ""),
                "workspace_dir": metadata.get("workspace_dir", str(metadata_path.parent)),
                "paper_path": metadata.get("paper_path", ""),
                "repo_path": str(repo_path),
                "data_dir": metadata.get("data_dir", ""),
                "metadata_path": str(metadata_path),
                "repo_download": metadata.get("repo_download", {}),
                "dataset_downloads": metadata.get("dataset_downloads", []),
                "goal": metadata.get("goal", DEFAULT_GOAL),
                "copy_repo": metadata.get("copy_repo", True),
            }
        )
    return runs


def _run_label(run: dict) -> str:
    run_id = str(run.get("run_id", "run"))
    created = str(run.get("created_at", ""))
    goal = _compact_display(str(run.get("goal", "")), 80)
    return " | ".join(part for part in (run_id, created, goal) if part)


def main() -> None:
    if st is None:
        raise RuntimeError("Streamlit is required to run the R2A Web UI. Install with `pip install -e .[dev]`.")
    st.set_page_config(page_title="R2A", page_icon=None, layout="wide")
    _apply_css()
    _init_state()
    if not restore_workspace_session(st.session_state):
        restore_runtime_run_session(st.session_state)
    sync_background_run_readonly(st.session_state)
    _maybe_autorefresh()

    with st.sidebar:
        settings = st.session_state.web_settings
        st.header("Run Settings")
        base_dir = st.text_input("Workspace base dir", value=str(settings.get("workspace_base_dir", DEFAULT_WORKSPACE_BASE)), key="setting_workspace_base_dir")
        existing_runs = _list_existing_runs(base_dir) if feature_enabled(FEATURE_RESTORE_PREVIOUS_RUN) else []
        if existing_runs:
            labels = ["No restored run"] + [_run_label(run) for run in existing_runs]
            current_repo = str((st.session_state.workspace or {}).get("repo_path", ""))
            current_index = 0
            for index, run in enumerate(existing_runs, start=1):
                if str(run.get("repo_path", "")) == current_repo:
                    current_index = index
                    break
            selected_run = st.selectbox("Restore previous run", labels, index=current_index)
            if selected_run != labels[0] and st.button("Load selected run", use_container_width=True):
                _restore_run(existing_runs[labels.index(selected_run) - 1])
                st.success("Previous run loaded.")
        # P0: Manual recovery button (no auto-scan on startup)
        if not st.session_state.get("workspace"):
            if st.button("Recover from Runtime History", use_container_width=True, help="Scan runtime records to find active runs. May take 10-30 seconds."):
                with st.spinner("Scanning runtime records..."):
                    if restore_runtime_run_session_by_scan(st.session_state):
                        st.success("Recovered run from runtime records.")
                        st.rerun()
                    else:
                        recovery_info = st.session_state.get("runtime_recovery", {})
                        reason = recovery_info.get("reason", "no active runs found")
                        st.warning(f"No active run recovered: {reason}")
        with st.expander("Stage Backends", expanded=False):
            paper_backend_options = ["openclaw_reader", "preprocess", "ai_reader", "claude_reader"]
            saved_paper_backend = settings.get("paper_backend", DEFAULT_PAPER_BACKEND)
            if saved_paper_backend not in paper_backend_options:
                saved_paper_backend = DEFAULT_PAPER_BACKEND
            paper_backend = st.selectbox(
                "Paper processing",
                paper_backend_options,
                index=paper_backend_options.index(saved_paper_backend),
                key="setting_paper_backend",
                format_func=_backend_label,
            )
            if paper_backend == "ai_reader":
                st.caption("Paper processing: local text/metadata path; no Codex Tool Calls")
            elif paper_backend == "claude_reader":
                st.caption("Paper processing: local text/metadata path; no Claude Tool Calls")
            elif paper_backend == "openclaw_reader":
                st.caption("Paper processing: OpenClaw local embedded reader")
            else:
                st.caption("Paper processing: local preprocess")
            planner_backend = _select_with_saved(
                "Planner backend",
                ["openclaw", "codex", "claude", "ccr_text", "command", "template", "mock", "openai_compatible", "anthropic"],
                settings.get("planner_backend", DEFAULT_PLANNER_BACKEND),
                "setting_planner_backend",
            )
            engineer_executor = _select_with_saved(
                "Engineer executor",
                ["openclaw", "codex", "claude", "mock"],
                settings.get("engineer_executor", DEFAULT_ENGINEER_EXECUTOR),
                "setting_engineer_executor",
            )
            manager_backend = _select_with_saved(
                "Manager backend",
                ["openclaw_review", "rules", "codex_review", "claude_review"],
                settings.get("manager_backend", DEFAULT_MANAGER_BACKEND),
                "setting_manager_backend",
            )
            reviewer_backend = _select_with_saved("Reviewer backend", ["openclaw", "rules", "codex", "claude"], settings.get("reviewer_backend", DEFAULT_REVIEWER_BACKEND), "setting_reviewer_backend")
            final_writer_backend = _select_with_saved(
                "Final Writer / Report Writer backend",
                ["template", "openclaw"],
                settings.get("final_writer_backend", DEFAULT_FINAL_WRITER_BACKEND),
                "setting_final_writer_backend",
            )
            if any(
                value in {"claude", "claude_review"}
                for value in (planner_backend, manager_backend, reviewer_backend)
            ):
                st.warning(
                    "Full-stage Claude/CCR/DeepSeek operation is experimental. For stable L3/L4 evidence, prefer local/template/rules for Paper, Planner, and Manager, and use Claude Code mainly for Engineer."
            )
            _show_planner_backend_preflight(planner_backend)
        with st.expander("Run Behavior", expanded=True):
            output_language = st.selectbox("Output language", ["Chinese", "English"], index=0)
            engineer_environment_options = ["wsl", "windows"]
            saved_engineer_environment = str(settings.get("engineer_execution_environment", DEFAULT_ENGINEER_EXECUTION_ENVIRONMENT) or DEFAULT_ENGINEER_EXECUTION_ENVIRONMENT)
            if saved_engineer_environment not in engineer_environment_options:
                saved_engineer_environment = DEFAULT_ENGINEER_EXECUTION_ENVIRONMENT
            saved_wsl_distro = str(settings.get("wsl_distro", DEFAULT_WSL_DISTRO) or DEFAULT_WSL_DISTRO)
            saved_wsl_cache_dir = str(settings.get("wsl_cache_dir", DEFAULT_WSL_CACHE_DIR) or DEFAULT_WSL_CACHE_DIR)
            engineer_execution_environment = st.selectbox(
                "Engineer execution environment",
                engineer_environment_options,
                index=engineer_environment_options.index(saved_engineer_environment),
                help="Use WSL for Linux-style build/test/benchmark commands while keeping the Windows UI and run folders.",
                key="setting_engineer_execution_environment",
            )
            if engineer_execution_environment == "wsl":
                wsl_distro = st.text_input("WSL distro", value=saved_wsl_distro, key="setting_wsl_distro")
                wsl_cache_dir = st.text_input(
                    "WSL cache dir on Windows drive",
                    value=saved_wsl_cache_dir,
                    help="Caches are exported to WSL as XDG_CACHE_HOME/PIP_CACHE_DIR/HF_HOME/TORCH_HOME to avoid filling the WSL home/C drive.",
                    key="setting_wsl_cache_dir",
                )
            else:
                wsl_distro = saved_wsl_distro
                wsl_cache_dir = saved_wsl_cache_dir
            auto_approve = st.toggle("Skip manual approval", value=DEFAULT_AUTO_APPROVE)
            auto_iterate = st.toggle("Auto iterate", value=DEFAULT_AUTO_ITERATE, help="Enable automatic iteration on Manager FAIL.")
            st.session_state.setting_auto_refresh_interval_label = "Off"
            st.session_state.auto_refresh_interval_seconds = 0
            st.caption("Status refresh: Manual")
            if st.session_state.get("last_refreshed_at"):
                st.caption(f"Last refreshed: {st.session_state.last_refreshed_at}")
            max_iterations = st.slider("Max iterations", min_value=1, max_value=10, value=DEFAULT_MAX_ITERATIONS_MINIMAL, help="Maximum number of iterations when auto-iterate is enabled.")
            target_reproduction_level = st.selectbox(
                "Target reproduction level",
                list(REPRODUCTION_LEVELS),
                index=list(REPRODUCTION_LEVELS).index(DEFAULT_TARGET_REPRODUCTION_LEVEL),
            )
            download_budget_gb = st.number_input("Official data download budget (GB)", min_value=0, value=20, step=1)
            allow_official_dataset_download = st.toggle("Allow official dataset download", value=False)
            allow_full_benchmark = st.toggle("Allow full benchmark", value=False)
            allow_external_baselines = st.toggle("Allow external baselines", value=False)
            network_copy = _network_scope_ui_copy()
            allow_network = st.toggle(network_copy["toggle_label"], value=False, help=network_copy["toggle_help"])
            if allow_network:
                st.caption(network_copy["caption"])
            with st.expander(network_copy["advanced_label"], expanded=False):
                raw_allowed_network_scope = st.text_input(
                    network_copy["raw_label"],
                    value=network_copy["default_scope"],
                    disabled=not allow_network,
                    help="Comma-separated raw scope labels passed to the Planner/Engineer state.",
                )
            allowed_network_scope = raw_allowed_network_scope if allow_network else ""
        with st.expander("Settings", expanded=False):
            (
                stage_api_keys,
                stage_api_key_env_vars,
                codex_path_input,
                claude_path_input,
                openclaw_executable_path,
                openclaw_config_path,
                codex_stage_timeout,
            ) = _show_settings(
                {
                    "paper": paper_backend,
                    "planner": planner_backend,
                    "engineer": engineer_executor,
                    "manager": manager_backend,
                    "reviewer": reviewer_backend,
                    "final_writer": final_writer_backend,
                }
            )
            any_codex = any(
                value in {"codex", "codex_review"}
                for value in (paper_backend, planner_backend, engineer_executor, manager_backend, reviewer_backend, final_writer_backend)
            )
            any_claude = any(
                value in {"claude", "claude_review", "claude_reader"}
                for value in (paper_backend, planner_backend, engineer_executor, manager_backend, reviewer_backend, final_writer_backend)
            )
            openclaw_selected = any(
                value in OPENCLAW_BACKENDS
                for value in (paper_backend, planner_backend, engineer_executor, manager_backend, reviewer_backend, final_writer_backend)
            )
            if any_codex:
                st.info(
                    "Selected Codex stages call the local command-line Codex CLI from this Streamlit process. "
                    "Being able to chat with Codex here does not mean Streamlit can run `codex --version`."
                )
                st.caption(f"Candidate executable: {_resolve_codex_input(codex_path_input)}")
                if st.button("Check Codex CLI", use_container_width=True):
                    _show_codex_cli_check(_resolve_codex_input(codex_path_input))
            if engineer_executor == "codex":
                st.warning("Engineer Codex may modify the copied workspace repo.")
            if any_claude:
                st.info("Claude stages use Claude Code Router by default. Engineer may modify the copied workspace repo; report stages are guarded to their allowed .r2a outputs.")
                st.caption(f"Claude/Router candidate executable: {_resolve_claude_input(claude_path_input)}")
                if st.button("Check Claude Code / Router CLI", use_container_width=True):
                    _show_claude_cli_check(_resolve_claude_input(claude_path_input))
                if st.button("Check Gateway", use_container_width=True):
                    _show_gateway_check(
                        _resolve_claude_input(claude_path_input),
                        _claude_stage_names(paper_backend, planner_backend, engineer_executor, manager_backend, reviewer_backend),
                    )
                if st.button("Start CCR", use_container_width=True):
                    _show_gateway_check(
                        _resolve_claude_input(claude_path_input),
                        _claude_stage_names(paper_backend, planner_backend, engineer_executor, manager_backend, reviewer_backend),
                        auto_start=True,
                    )
            if paper_backend == "ai_reader":
                st.info("Paper AI Reader compatibility mode uses the local Paper V2 path and does not call Codex Tool Calls.")
            if paper_backend == "claude_reader":
                st.info("Paper Claude Reader compatibility mode uses the local Paper V2 path and does not call Claude Tool Calls.")
            if openclaw_selected:
                st.info("OpenClaw stages use OpenClaw --local embedded. Stage provider/model policy comes from R2A stage profiles and OpenClaw config, not UI API keys.")
            if engineer_execution_environment == "wsl" and (openclaw_selected or engineer_executor in {"codex", "claude", "claude_code"}):
                check = check_wsl(wsl_distro)
                if check.available:
                    st.success(f"WSL execution available: {wsl_distro}")
                    st.caption(f"WSL cache policy: {wsl_cache_dir}")
                else:
                    st.warning(f"WSL check failed for `{wsl_distro}`: {check.error}")
            elif engineer_execution_environment == "wsl":
                st.caption(f"WSL execution selected; availability is checked during workflow preflight for real WSL stages. Cache policy: {wsl_cache_dir}")
            if engineer_executor == "shell":
                st.caption("Shell mode is a safe MVP demo executor and does not run real experiments.")
            else:
                st.caption("Non-Engineer AI stages should only write their allowed .r2a reports.")
            if paper_backend == "ai_reader":
                st.caption("Paper AI Reader compatibility mode writes local Paper artifacts only.")
            elif paper_backend == "claude_reader":
                st.caption("Paper Claude Reader compatibility mode writes local Paper artifacts only.")
            else:
                st.caption("Paper is processed locally. It does not call Codex.")

    st.title("R2A")
    _show_web_runtime_header()
    _show_runtime_recovery_notice()
    st.caption("Upload a paper, optionally add guidance, and run a reproducible research workflow.")

    left, right = st.columns([1, 1], gap="large")
    with left:
        with st.container(border=True):
            st.subheader("1. Upload Paper")
            uploaded_paper = st.file_uploader("PDF paper", type=["pdf"])

        with st.expander("2. Optional Guidance", expanded=False):
            st.caption("Leave empty to let R2A create a conservative reproduction goal from available paper/context.")
            guidance = st.text_area(
                "Guidance",
                value="",
                height=150,
                placeholder="Optional: e.g. focus on HNSW oversampling and qps/recall metrics",
            )

    with right:
        with st.container(border=True):
            st.subheader("3. Workspace")
            st.caption("R2A creates a new run directory under the workspace base dir.")
            with st.expander("Advanced: optional source project", expanded=False):
                github_repo_url = st.text_input("GitHub repository URL", placeholder="https://github.com/user/repo.git")
                source_repo_path = st.text_input("Source project path", placeholder="E:\\path\\to\\existing_repo")
                copy_repo = st.toggle("Copy source repo into workspace", value=True)
                st.caption("Optional. If left empty, R2A creates an empty repo and Codex Engineer may try to discover the official project source from the paper/context.")
            with st.expander("Advanced: optional datasets", expanded=False):
                dataset_urls_text = st.text_area(
                    "Dataset file URLs",
                    value="",
                    height=90,
                    placeholder="One URL per line. Files larger than 10GB are skipped.",
                )
                max_dataset_download_gb = st.number_input("Max dataset download per file (GB)", min_value=1, max_value=10, value=10, step=1)
            if st.button("Create Workspace", type="primary", use_container_width=True):
                _create_workspace_clicked(
                    base_dir,
                    guidance,
                    uploaded_paper,
                    source_repo_path,
                    github_repo_url,
                    dataset_urls_text,
                    int(max_dataset_download_gb),
                    copy_repo,
                )
            _show_workspace_summary()

    run_payload = None
    with st.container(border=True):
        st.subheader("4. Run Workflow")
        run_status_slot = st.empty()
        run_disabled, run_disabled_reason = run_workflow_button_disabled(st.session_state, planner_backend)
        if run_disabled and run_disabled_reason:
            st.caption(f"Run Workflow disabled: {run_disabled_reason}")
        _show_run_control_panel()
        if st.button("Run Workflow", disabled=run_disabled, use_container_width=True):
            preflight_error = _workflow_preflight(
                st.session_state.workspace,
                paper_backend,
                planner_backend,
                engineer_executor,
                manager_backend,
                reviewer_backend,
                _resolve_codex_input(codex_path_input),
                _resolve_claude_input(claude_path_input),
                engineer_execution_environment,
                wsl_distro,
                wsl_cache_dir,
                final_writer_backend=final_writer_backend,
            )
            if preflight_error:
                st.error(preflight_error)
                st.stop()
            run_payload = {
                "guidance": guidance,
                "paper_backend": paper_backend,
                "planner_backend": planner_backend,
                "engineer_executor": engineer_executor,
                "manager_backend": manager_backend,
                "reviewer_backend": reviewer_backend,
                "final_writer_backend": final_writer_backend,
                "stage_model_selection": _current_or_saved_stage_model_selection(settings),
                "auto_approve": auto_approve,
                "output_language": output_language,
                "auto_iterate": auto_iterate,
                "max_iterations": max_iterations,
                "target_reproduction_level": target_reproduction_level,
                "download_budget_gb": int(download_budget_gb),
                "allow_official_dataset_download": allow_official_dataset_download,
                "allow_full_benchmark": allow_full_benchmark,
                "allow_external_baselines": allow_external_baselines,
                "allow_network": allow_network,
                "allowed_network_scope": allowed_network_scope,
                "codex_executable_path": _resolve_codex_input(codex_path_input),
                "claude_executable_path": _resolve_claude_input(claude_path_input),
                "openclaw_executable_path": openclaw_executable_path,
                "openclaw_config_path": openclaw_config_path,
                "codex_stage_timeout": int(codex_stage_timeout),
                "engineer_execution_environment": engineer_execution_environment,
                "wsl_distro": wsl_distro,
                "wsl_cache_dir": wsl_cache_dir,
                "stage_api_keys": stage_api_keys,
                "stage_api_key_env_vars": stage_api_key_env_vars,
            }

    with st.container(border=True):
        st.subheader("Workflow Review")
        workflow_review_slot = st.empty()
        with workflow_review_slot.container():
            _show_workflow_overview(auto_iterate, max_iterations)

    if run_payload:
        _start_workflow_background(**run_payload)
        st.info("Workflow started in the background. Use Stop or Force Stop for this run if it needs to be cancelled.")
        st.rerun()

    with st.container(border=True):
        st.subheader("Paper Processing Status")
        _show_paper_preprocess_status()

    with st.container(border=True):
        st.subheader("Iteration History")
        _show_iteration_history()

    with st.container(border=True):
        st.subheader("Engineer Results")
        _show_engineer_results()

    with st.container(border=True):
        st.subheader("Logs")
        _show_logs()

    with st.container(border=True):
        st.subheader("Web UI Control")
        _show_web_ui_control()



def _init_state() -> None:
    st.session_state.setdefault("workspace", None)
    st.session_state.setdefault("workspace_path", "")
    st.session_state.setdefault("workspace_id", "")
    st.session_state.setdefault("workspace_created", False)
    st.session_state.setdefault("workflow_result", None)
    st.session_state.setdefault("workflow_error", "")
    st.session_state.setdefault("codex_cli_check", None)
    st.session_state.setdefault("claude_cli_check", None)
    st.session_state.setdefault("workflow_running", False)
    st.session_state.setdefault("active_run_id", "")
    st.session_state.setdefault("run_created_this_session", False)  # Track if run was created this session
    st.session_state.setdefault("loaded_historical_run", False)  # Track if user explicitly loaded historical run
    st.session_state.setdefault("recovered_active_run", False)  # Track active runs recovered from runtime state
    st.session_state.setdefault("workflow_thread", None)
    if "web_settings" not in st.session_state:
        st.session_state.web_settings = _load_web_settings()
    else:
        should_reset_backend_widgets = _settings_need_openclaw_default_migration(st.session_state.web_settings)
        st.session_state.web_settings = _normalize_web_settings(st.session_state.web_settings)
        if should_reset_backend_widgets:
            _reset_stage_backend_widget_state(st.session_state.web_settings)


def _select_with_saved(label: str, options: list[str], saved: str, key: str) -> str:
    value = saved if saved in options else options[0]
    return st.selectbox(label, options, index=options.index(value), key=key, format_func=_backend_label)


def _planner_backend_ready(backend: str) -> tuple[bool, str]:
    return workspace_planner_backend_ready(backend)


def _show_planner_backend_preflight(planner_backend: str) -> None:
    ready, message = _planner_backend_ready(planner_backend)
    if ready:
        st.success(f"Planner backend ready = true ({message})")
    else:
        st.error(f"Planner backend ready = false ({message})")


def _openclaw_path_guidance_markdown() -> str:
    return "\n".join(
        [
            "路径填写说明：",
            "- Windows + WSL OpenClaw：填写真实 WSL 用户下的 POSIX executable/config path；Windows UI 会在检测配置时尝试对应 UNC read path。",
            "- Windows native OpenClaw：填写 Windows 可执行文件路径或 PATH 命令，例如 `openclaw.cmd` / npm global bin。",
            "- Linux/macOS native OpenClaw：填写 `which openclaw` 返回的 executable path，并填写本机 OpenClaw config path。",
            "- No OpenClaw：OpenClaw stages 不可用；`template` Final Writer 可继续生成报告，但 Planner/Engineer/Reviewer 仍需要可用 backend。",
        ]
    )


def _openclaw_example_path_warning(*paths: str) -> str:
    for path in paths:
        normalized = str(path or "").replace("\\", "/").lower()
        if "/home/r2auser/" in normalized:
            return "路径包含 `/home/r2auser/`，这可能是文档示例路径；请确认真实 WSL 用户名后再保存。"
    return ""


def _is_openclaw_doc_example_path(path: str) -> bool:
    normalized = str(path or "").strip().replace("\\", "/").lower()
    return "/home/r2auser/" in normalized


def _openclaw_persisted_path_value(path: object) -> str:
    text = str(path or "").strip()
    return "" if _is_openclaw_doc_example_path(text) else text


def _openclaw_saved_paths_for_ui(settings: dict | None) -> tuple[str, str, list[str]]:
    settings = settings if isinstance(settings, dict) else {}
    raw_executable = str(settings.get("openclaw_executable_path", "") or "").strip()
    raw_config = str(settings.get("openclaw_config_path", "") or "").strip()
    executable = _openclaw_persisted_path_value(raw_executable)
    config = _openclaw_persisted_path_value(raw_config)
    warnings: list[str] = []
    if raw_executable and not executable:
        warnings.append("Saved OpenClaw executable path looks like a documentation example and is ignored until a real path is saved.")
    if raw_config and not config:
        warnings.append("Saved OpenClaw config path looks like a documentation example and is ignored until a real path is saved.")
    return executable, config, warnings


def _current_or_saved_stage_model_selection(settings: dict | None) -> dict[str, dict[str, str]]:
    saved: dict[str, dict[str, str]] = {}
    if isinstance(settings, dict) and isinstance(settings.get("stage_model_selection"), dict):
        saved = {
            str(stage): {str(key): str(value) for key, value in entry.items() if value is not None}
            for stage, entry in settings.get("stage_model_selection", {}).items()
            if isinstance(entry, dict) and entry.get("provider") and entry.get("model")
        }
    current = _collect_stage_model_selection_from_session()
    merged = dict(saved)
    merged.update(current)
    return merged


def _show_openclaw_stage_profile_policy(settings: dict | None = None) -> tuple[str, str]:
    settings = settings if isinstance(settings, dict) else {}
    saved_executable, saved_config, saved_path_warnings = _openclaw_saved_paths_for_ui(settings)
    for widget_key, saved_value in (
        ("setting_openclaw_executable_path", saved_executable),
        ("setting_openclaw_config_path", saved_config),
    ):
        if _is_openclaw_doc_example_path(str(st.session_state.get(widget_key, "") or "")):
            st.session_state[widget_key] = saved_value

    st.caption("OpenClaw executable/config")
    st.info(_openclaw_path_guidance_markdown())
    for warning in saved_path_warnings:
        st.warning(warning)
    openclaw_executable_path = st.text_input(
        "OpenClaw executable path",
        value=saved_executable,
        placeholder="openclaw, C:\\Tools\\openclaw.cmd, or /usr/local/bin/openclaw",
        help="Used for OpenClaw stage runtime and lightweight config validation. Windows native, PATH commands, and POSIX paths are preserved as entered.",
        key="setting_openclaw_executable_path",
    )
    openclaw_config_path = st.text_input(
        "OpenClaw config path",
        value=saved_config,
        placeholder="C:\\Users\\Alice\\.openclaw\\openclaw.json or /home/alice/.openclaw/openclaw.json",
        help="Used to detect real OpenClaw model/profile entries. R2A also tries WSL UNC paths for POSIX config paths on Windows.",
        key="setting_openclaw_config_path",
    )
    # Use current input values for detection (not stale session state)
    current_executable = str(st.session_state.get("setting_openclaw_executable_path", openclaw_executable_path) or "").strip()
    current_config = str(st.session_state.get("setting_openclaw_config_path", openclaw_config_path) or "").strip()
    example_path_warning = _openclaw_example_path_warning(current_executable, current_config)
    if example_path_warning:
        st.warning(example_path_warning)

    status = _openclaw_config_status_model(
        openclaw_executable_path=current_executable or None,
        openclaw_config_path=current_config or None,
    )
    # Store detection result for save validation
    st.session_state["openclaw_config_status"] = status

    st.caption(
        f"Executable: `{status['openclaw_executable_path']}` | "
        f"Config: `{status['openclaw_config_path']}` | "
        f"Runner: `{status['runner']}`"
    )
    availability = str(status["availability"])
    if availability == "local_path_exists":
        st.success("OpenClaw executable path exists on this host.")
    elif availability == "wsl_or_posix_path_configured":
        st.info("OpenClaw executable is configured as a WSL/POSIX path; runtime preflight will verify it inside WSL.")
    elif availability == "path_lookup_configured":
        st.info("OpenClaw executable is configured as a PATH command; runtime preflight will resolve it in the target environment.")
    else:
        st.warning("OpenClaw executable path is configured but was not found as a local Windows path.")
    if status.get("model_detection_errors"):
        st.warning("OpenClaw model detection errors: " + "; ".join(status["model_detection_errors"]))
    elif status.get("model_detection_warnings"):
        st.info("OpenClaw model detection warning: " + "; ".join(status["model_detection_warnings"]))
    read_path = str(status.get("model_detection_read_path", "") or "")
    if read_path:
        st.caption(f"Detected models from: `{read_path}`")
    checked_paths = [str(item) for item in status.get("model_detection_checked_paths", []) or []]
    if checked_paths:
        st.caption("Detection checked: " + "; ".join(f"`{item}`" for item in checked_paths))
    refreshed_at = st.session_state.get("openclaw_models_refreshed_at", "")
    if refreshed_at:
        st.caption(f"Models refreshed at: {refreshed_at}")
    cols = st.columns(2)
    if cols[0].button("Refresh Models", use_container_width=True):
        # Use current input values for refresh
        st.session_state.openclaw_model_detection_refresh = True
        st.session_state.openclaw_models_refreshed_at = _openclaw_model_refresh_timestamp()
        # Save current paths to settings temporarily for detection to pick up
        st.session_state.web_settings_temp = {
            "openclaw_executable_path": current_executable,
            "openclaw_config_path": current_config,
        }
        st.rerun()
    if cols[1].button("Test OpenClaw", use_container_width=True):
        selection = _first_selected_stage_model_from_session()
        # Use current input values for test
        test_executable = str(st.session_state.get("setting_openclaw_executable_path", "") or "").strip()
        test_config = str(st.session_state.get("setting_openclaw_config_path", "") or "").strip()
        result = test_openclaw_configuration(
            openclaw_executable_path=test_executable or None,
            openclaw_config_path=test_config or None,
            provider=selection.get("provider"),
            model=selection.get("model"),
            profile=selection.get("profile"),
        )
        st.session_state.openclaw_test_result = result
    _show_openclaw_test_result(st.session_state.get("openclaw_test_result", {}))
    _show_openclaw_stage_model_selection(status, settings=settings)
    # Return current values from input widgets
    return str(st.session_state.get("setting_openclaw_executable_path", current_executable) or "").strip(), str(st.session_state.get("setting_openclaw_config_path", current_config) or "").strip()


def _openclaw_config_status_model(
    *,
    openclaw_executable_path: str | None = None,
    openclaw_config_path: str | None = None,
) -> dict[str, object]:
    config = resolve_openclaw_config(
        openclaw_executable_path=openclaw_executable_path,
        openclaw_config_path=openclaw_config_path,
    )
    executable = str(config.get("openclaw_executable_path", "") or "")
    if not executable:
        availability = "missing_executable_path"
    elif executable.startswith(("/", "~")):
        availability = "wsl_or_posix_path_configured"
    elif Path(executable).exists():
        availability = "local_path_exists"
    elif not any(separator in executable for separator in ("/", "\\")):
        availability = "path_lookup_configured"
    else:
        availability = "local_path_missing"
    profiles = openclaw_stage_profiles()
    detection = detect_openclaw_model_profiles(openclaw_config_path=str(config.get("openclaw_config_path", "") or ""))
    rows = []
    for stage in ("paper", "planner", "engineer", "manager", "reviewer", "final_writer"):
        profile = profiles.get(stage, {})
        rows.append(
            {
                "Stage": "Final Writer" if stage == "final_writer" else stage.title(),
                "Backend": "OpenClaw",
                "Provider": str(profile.get("provider", "")),
                "Model": str(profile.get("model", "")),
                "Runner": str(profile.get("runner", "")),
                "Agent": str(profile.get("agent", "") or "OpenClaw default"),
            }
        )
    return {
        "openclaw_executable_path": executable,
        "openclaw_config_path": str(config.get("openclaw_config_path", "") or ""),
        "provider": str(config.get("provider", "") or ""),
        "model": str(config.get("model", "") or ""),
        "runner": str(config.get("runner", "") or ""),
        "agent": str(config.get("agent", "") or ""),
        "availability": availability,
        "stage_profiles": rows,
        "model_options": list(detection.get("models", []) or []),
        "model_detection_source": str(detection.get("source", "") or ""),
        "model_detection_read_path": str(detection.get("config_read_path", "") or ""),
        "model_detection_checked_paths": list(detection.get("checked_paths", []) or []),
        "model_detection_warnings": list(detection.get("warnings", []) or []),
        "model_detection_errors": list(detection.get("errors", []) or []),
    }


def _show_openclaw_stage_model_selection(status: dict[str, object], *, settings: dict | None = None) -> None:
    options = _openclaw_model_select_options(status)
    labels = [label for label, _ in options]
    st.caption("OpenClaw stage-level model selection")

    # Check if config path is configured
    config_path = str(status.get("openclaw_config_path", "") or "").strip()
    if not config_path:
        st.info("OpenClaw config path is not configured; model detection cannot restore saved defaults.")

    missing_defaults = _saved_stage_model_detection_warnings(options, settings)
    if missing_defaults:
        st.warning("Saved default model not detected after reading current OpenClaw config; please refresh models or reselect. " + "; ".join(missing_defaults))
    elif isinstance(settings, dict) and settings.get("stage_model_selection"):
        st.caption("Default loaded.")
    for stage, label in OPENCLAW_MODEL_STAGE_LABELS:
        default_index = _default_model_option_index(options, stage, settings=settings)
        widget_key = f"openclaw_stage_model_{stage}"
        if st.session_state.get(widget_key) not in {None, *labels}:
            st.session_state.pop(widget_key, None)
        selected = st.selectbox(
            f"{label} model/profile",
            labels,
            index=default_index,
            key=widget_key,
        )
        st.session_state[f"openclaw_stage_model_value_{stage}"] = dict(options[labels.index(selected)][1])
    if st.button("保存当前 OpenClaw 路径、配置与阶段模型为默认配置", use_container_width=True):
        selection = _collect_stage_model_selection_from_session()
        if not selection:
            st.warning("No detected OpenClaw model selection to save.")
        else:
            # Get current detection result for validation
            current_status = st.session_state.get("openclaw_config_status", {})
            result = _save_stage_model_defaults(
                selection,
                openclaw_executable_path=str(st.session_state.get("setting_openclaw_executable_path", "") or ""),
                openclaw_config_path=str(st.session_state.get("setting_openclaw_config_path", "") or ""),
                detection_result=current_status if isinstance(current_status, dict) else None,
            )
            st.session_state.web_settings = _load_web_settings()
            if result.get("error"):
                st.error(result["error"])
                if result.get("warning"):
                    st.warning(result["warning"])
            elif result.get("warning"):
                st.warning(result["warning"])
            else:
                st.success("OpenClaw default configuration saved.")


def _openclaw_model_select_options(status: dict[str, object]) -> list[tuple[str, dict[str, str]]]:
    detected_models = list(status.get("model_options", []) or [])
    if not detected_models:
        return [("Not detected", {})]
    options: list[tuple[str, dict[str, str]]] = [("Use OpenClaw runtime default", {})]
    seen = {("", "", "")}
    for item in detected_models:
        if not isinstance(item, dict):
            continue
        provider = str(item.get("provider", "") or "")
        model = str(item.get("model", "") or "")
        profile = str(item.get("profile", "") or item.get("runner", "") or "")
        key = (provider, model, profile)
        if not provider or not model or key in seen:
            continue
        seen.add(key)
        label = str(item.get("display_name") or f"{provider}/{model} ({profile or 'profile'})")
        options.append(
            (
                label,
                {
                    "backend": "openclaw",
                    "provider": provider,
                    "model": model,
                    "profile": profile,
                    "runner": str(item.get("runner", "") or profile),
                    "agent": str(item.get("agent", "") or ""),
                },
            )
        )
    return options


def _default_model_option_index(
    options: list[tuple[str, dict[str, str]]],
    stage: str,
    *,
    settings: dict | None = None,
) -> int:
    saved_models = {}
    if isinstance(settings, dict):
        saved_models = dict(settings.get("stage_model_selection", {}) or {})
    saved = saved_models.get(stage, {}) if isinstance(saved_models.get(stage, {}), dict) else {}
    provider = str(saved.get("provider", "") or "")
    model = str(saved.get("model", "") or "")
    profile = str(saved.get("profile", "") or "")
    if not provider or not model:
        return 0
    for index, (_, value) in enumerate(options):
        value_profile = str(value.get("profile", "") or "")
        if value.get("provider") == provider and value.get("model") == model and (not profile or value_profile == profile):
            return index
    return 0


def _saved_stage_model_detection_warnings(
    options: list[tuple[str, dict[str, str]]],
    settings: dict | None,
) -> list[str]:
    if not isinstance(settings, dict):
        return []
    saved_models = settings.get("stage_model_selection", {})
    if not isinstance(saved_models, dict):
        return []
    warnings: list[str] = []
    labels = dict(OPENCLAW_MODEL_STAGE_LABELS)
    for stage, entry in saved_models.items():
        if not isinstance(entry, dict):
            continue
        provider = str(entry.get("provider", "") or "")
        model = str(entry.get("model", "") or "")
        profile = str(entry.get("profile", "") or "")
        if not provider or not model:
            continue
        if not _stage_model_entry_detected(options, provider=provider, model=model, profile=profile):
            label = labels.get(str(stage), str(stage))
            suffix = f" ({profile})" if profile else ""
            warnings.append(f"{label}: {provider}/{model}{suffix}")
    return warnings


def _stage_model_entry_detected(
    options: list[tuple[str, dict[str, str]]],
    *,
    provider: str,
    model: str,
    profile: str = "",
) -> bool:
    for _, value in options:
        value_profile = str(value.get("profile", "") or "")
        if value.get("provider") == provider and value.get("model") == model and (not profile or value_profile == profile):
            return True
    return False


def _collect_stage_model_selection_from_session() -> dict[str, dict[str, str]]:
    selection: dict[str, dict[str, str]] = {}
    if st is None:
        return selection
    for stage, _ in OPENCLAW_MODEL_STAGE_LABELS:
        value = st.session_state.get(f"openclaw_stage_model_value_{stage}", {})
        if isinstance(value, dict) and value.get("provider") and value.get("model"):
            selection[stage] = {str(key): str(item) for key, item in value.items() if item is not None}
    return selection


def _first_selected_stage_model_from_session() -> dict[str, str]:
    selection = _collect_stage_model_selection_from_session()
    for stage, _ in OPENCLAW_MODEL_STAGE_LABELS:
        entry = selection.get(stage, {})
        if entry.get("provider") and entry.get("model"):
            return entry
    return {}


def _show_openclaw_test_result(result: object) -> None:
    if not isinstance(result, dict) or not result:
        return
    success = bool(result.get("success"))
    if success:
        st.success("OpenClaw configuration is recognizable by R2A.")
    else:
        st.error("OpenClaw configuration test failed.")
    rows = {
        "tested_at": str(result.get("tested_at", "")),
        "executable_path": str(result.get("executable_path", "")),
        "config_path": str(result.get("config_path", "")),
        "config_read_path": str(result.get("config_read_path", "")),
        "provider": str(result.get("provider", "")),
        "model": str(result.get("model", "")),
        "profile": str(result.get("profile", "")),
        "detection_source": str(result.get("detection_source", "")),
    }
    st.json({key: value for key, value in rows.items() if value})
    if result.get("error_message"):
        st.warning(str(result.get("error_message")))
    warnings = [str(item) for item in result.get("warnings", []) or [] if str(item).strip()]
    if warnings:
        st.info("Warnings: " + "; ".join(warnings))


def _save_stage_model_defaults(
    selection: dict[str, dict[str, str]],
    *,
    openclaw_executable_path: str = "",
    openclaw_config_path: str = "",
    detection_result: dict[str, object] | None = None,
) -> dict[str, str]:
    """Save OpenClaw executable/config paths and stage model selection as defaults.

    Returns a dict with 'success' and optional 'warning' or 'error' keys.
    """
    warnings: list[str] = []
    errors: list[str] = []

    # Validate executable path
    executable = str(openclaw_executable_path or "").strip()
    if _is_openclaw_doc_example_path(executable):
        errors.append("OpenClaw executable path appears to be the documentation example `/home/r2auser/...`; please save the real WSL username path.")

    # Validate config path - prioritize detection success over placeholder check
    config = str(openclaw_config_path or "").strip()
    if _is_openclaw_doc_example_path(config):
        errors.append("OpenClaw config path appears to be the documentation example `/home/r2auser/...`; please save the real WSL username path.")

    # If detection was successful, trust that path even if it looks like placeholder
    detection_ok = False
    if isinstance(detection_result, dict):
        # Check model_detection_source (returned by _openclaw_config_status_model)
        source = str(detection_result.get("model_detection_source", "") or detection_result.get("source", "") or "")
        if source and source != "not_detected":
            detection_ok = True
            # DO NOT use read_path (UNC path) - keep original user path
            # The read_path is only for UI display, not for saving
        elif detection_result.get("config_read_path") and detection_result.get("models"):
            detection_ok = True

    if errors:
        return {"success": False, "error": " ".join(errors)}

    if not config:
        errors.append("OpenClaw config path is not configured; model detection cannot restore saved defaults.")
    elif not detection_ok and _is_placeholder_config_path(config):
        errors.append("OpenClaw config path appears to be a placeholder; please provide a real config path.")
    elif not detection_ok:
        # Detection failed but path doesn't look like placeholder - still warn
        detection_errors = list(detection_result.get("model_detection_errors", []) or []) if isinstance(detection_result, dict) else []
        if detection_errors:
            warnings.append("OpenClaw config path detection failed: " + "; ".join(detection_errors))
        else:
            warnings.append("OpenClaw config path was saved without a fresh model detection result; refresh models to verify it.")

    # Validate selection - don't save Not detected entries
    defaults: dict[str, dict[str, str]] = {}
    for stage, entry in (selection or {}).items():
        if not isinstance(entry, dict):
            continue
        provider = str(entry.get("provider", "") or "").strip()
        model = str(entry.get("model", "") or "").strip()
        if not provider or not model:
            continue
        # Don't save if it looks like "Not detected"
        if provider.lower() == "not" and model.lower() == "detected":
            continue
        defaults[str(stage)] = {
            str(key): str(value)
            for key, value in entry.items()
            if key in {"backend", "provider", "model", "profile", "runner", "agent", "mode"} and value is not None
        }

    if not defaults and not errors:
        warnings.append("No valid stage model selection to save; existing saved model selection was preserved.")

    if errors:
        return {"success": False, "error": " ".join(errors), **({"warning": " ".join(warnings)} if warnings else {})}

    # Load existing settings and update
    settings = _load_web_settings()
    settings["openclaw_executable_path"] = executable
    settings["openclaw_config_path"] = config
    if defaults:
        settings["stage_model_selection"] = defaults
    else:
        settings["stage_model_selection"] = dict(settings.get("stage_model_selection", {}) or {})
    # Preserve other non-sensitive settings
    if "auto_refresh_interval_seconds" in settings:
        pass  # Keep existing value
    settings["stage_api_keys"] = {}  # Clear API keys as per security requirement
    _save_web_settings(settings)

    result = {"success": not bool(errors)}
    if errors:
        result["error"] = " ".join(errors)
    if warnings:
        result["warning"] = " ".join(warnings)
    return result


def _is_placeholder_config_path(path: str) -> bool:
    """Check if config path looks like a placeholder example.

    Only rejects paths with explicit placeholder markers like <user> or example usernames.
    R2A documentation previously used /home/r2auser/... as an example path, so the
    Web UI treats that path as an example rather than a real persisted default.

    This function checks for explicit placeholder patterns that indicate
    the path is an example/template rather than a real user path.

    Important: WSL usernames can be short (x, a, u, dev, etc.) and should
    NOT be rejected.
    """
    text = str(path).strip()
    lowered = text.lower()
    if not text:
        return True
    if _is_openclaw_doc_example_path(text):
        return True

    # 1. Explicit placeholder markers (clear indicators of example text)
    explicit_placeholders = [
        "<user>",
        "<username>",
        "<YOUR_USER>",
        "<your_username>",
        "<path>",
        "<config>",
        "{user}",
        "{username}",
    ]
    if any(placeholder.lower() in lowered for placeholder in explicit_placeholders):
        return True

    # 2. Windows-style placeholder path with angle brackets
    # Only match if it still has angle brackets
    if "c:\\users\\<" in lowered:
        return True

    # 3. POSIX-style placeholder path with angle brackets
    # Only match if it still has angle brackets
    if "/home/<" in lowered or "/users/<" in lowered:
        return True

    # 4. Common example usernames in documentation
    # These are typically used in examples and tutorials
    # Only reject if the path pattern suggests it's an example
    example_usernames = ["example", "username", "your_user", "your_username"]
    for username in example_usernames:
        patterns = [
            f"/home/{username}/",
            f"/users/{username}/",
            f"c:\\users\\{username}\\",
            f"c:/users/{username}/",
        ]
        if any(pattern in lowered for pattern in patterns):
            return True

    # 5. Paths containing quotes (suggests copy-paste from example text)
    if '"' in text or "'" in text:
        return True

    # 6. Not detected or empty markers
    if lowered in {"not detected", "", "none", "null"}:
        return True

    # Do NOT reject paths with short usernames like /home/u/ or /home/a/
    # These are legitimate WSL usernames

    # Do NOT reject paths that are just .openclaw/openclaw.json
    # These might be relative paths that resolve correctly

    return False


def _openclaw_model_refresh_timestamp() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _backend_label(value: str) -> str:
    return BACKEND_DISPLAY_NAMES.get(value, value)


def _show_settings(stage_backends: dict[str, str]) -> tuple[dict[str, str], dict[str, str], str, str, str, str, int]:
    settings = st.session_state.web_settings
    openclaw_executable_path = str(settings.get("openclaw_executable_path", "") or "")
    openclaw_config_path = str(settings.get("openclaw_config_path", "") or "")
    if any(backend in OPENCLAW_BACKENDS for backend in stage_backends.values()):
        openclaw_executable_path, openclaw_config_path = _show_openclaw_stage_profile_policy(settings=settings)
        st.divider()

    stage_api_keys, stage_api_key_env_vars = _show_stage_api_key_settings(stage_backends)

    with st.expander("Advanced / Diagnostics", expanded=False):
        st.caption("CLI executables and checks")
        ccr_summary = _ccr_config_summary()
        if ccr_summary:
            st.caption(ccr_summary)
        codex_path_input = st.text_input(
            "Codex executable path",
            value=str(settings.get("codex_executable_path", "")),
            placeholder="codex or C:\\Users\\<user>\\AppData\\Roaming\\npm\\codex.cmd",
            help="Optional. Leave empty to use codex from PATH.",
            key="setting_codex_executable_path",
        )
        claude_path_input = st.text_input(
            "Claude Code / Router executable path",
            value=str(settings.get("claude_executable_path", "")),
            placeholder="ccr or E:\\ClaudeCode\\npm-global\\ccr.cmd",
            help="Optional. Use ccr/ccr.cmd for Claude Code Router.",
            key="setting_claude_executable_path",
        )
        codex_stage_timeout = int(
            st.number_input(
                "AI stage timeout (seconds)",
                min_value=60,
                max_value=10800,
                value=int(settings.get("codex_stage_timeout", 10800)),
                step=60,
                key="setting_codex_stage_timeout",
            )
        )
    cols = st.columns(2)
    if cols[0].button("Save settings", use_container_width=True):
        saved = _collect_web_settings(
            stage_api_keys,
            stage_api_key_env_vars,
            codex_path_input,
            claude_path_input,
            openclaw_executable_path,
            openclaw_config_path,
            codex_stage_timeout,
        )
        _save_web_settings(saved)
        st.session_state.web_settings = saved
        st.success("Settings saved.")
    if cols[1].button("Reload settings", use_container_width=True):
        st.session_state.web_settings = _load_web_settings()
        st.success("Settings reloaded. Refresh the page controls if values were already shown.")
    return stage_api_keys, stage_api_key_env_vars, codex_path_input, claude_path_input, openclaw_executable_path, openclaw_config_path, codex_stage_timeout


def _show_stage_api_key_settings(stage_backends: dict[str, str]) -> tuple[dict[str, str], dict[str, str]]:
    st.caption("Choose saved/CLI env to use existing system or router configuration. Choose paste API key only when R2A should inject a stage-specific key.")
    settings = st.session_state.web_settings
    saved_keys = dict(settings.get("stage_api_keys", {}))
    saved_env_vars = dict(settings.get("stage_api_key_env_vars", {}))
    saved_providers = dict(settings.get("stage_api_key_providers", {}))
    saved_key_sources = dict(settings.get("stage_api_key_sources", {}))
    stage_api_keys: dict[str, str] = {}
    stage_api_key_env_vars: dict[str, str] = {}
    for stage, label in STAGE_API_KEY_LABELS:
        backend = stage_backends.get(stage, "")
        if backend in OPENCLAW_BACKENDS:
            continue
        if backend in KEYLESS_BACKENDS:
            st.caption(f"{label}: `{_backend_label(backend)}` does not require an API key from the R2A UI.")
            continue
        backend_default_env = DEFAULT_STAGE_API_KEY_ENV.get(backend, "ANTHROPIC_API_KEY")
        provider_options = list(PROVIDER_DISPLAY_NAMES)
        saved_provider = str(saved_providers.get(stage, "auto"))
        if saved_provider not in provider_options:
            saved_provider = "auto"
        cols = st.columns([1.05, 1.15, 2])
        provider = cols[0].selectbox(
            f"{label} provider",
            provider_options,
            index=provider_options.index(saved_provider),
            key=f"stage_api_key_provider_{stage}",
            format_func=lambda value: PROVIDER_DISPLAY_NAMES.get(value, value),
        )
        default_env = PROVIDER_ENV_DEFAULTS.get(provider, backend_default_env)
        source_options = list(KEY_SOURCE_DISPLAY_NAMES)
        saved_source = str(saved_key_sources.get(stage, "env"))
        if saved_source not in source_options:
            saved_source = "env"
        key_source = cols[1].selectbox(
            f"{label} key source",
            source_options,
            index=source_options.index(saved_source),
            key=f"stage_api_key_source_{stage}",
            format_func=lambda value: KEY_SOURCE_DISPLAY_NAMES.get(value, value),
        )
        env_var = str(saved_env_vars.get(stage, default_env))
        if key_source == "direct":
            key = cols[2].text_input(
                f"{label} API key",
                value=str(saved_keys.get(stage, "")),
                type="password",
                key=f"stage_api_key_{stage}",
                placeholder=f"Injected as {default_env}",
            )
            env_var = default_env
            st.caption(f"{label}: pasted key will be passed as `{env_var}` for this stage only.")
            if key.strip():
                stage_api_keys[stage] = key.strip()
                stage_api_key_env_vars[stage] = env_var
        else:
            env_var = cols[2].text_input(
                f"{label} env var",
                value=env_var,
                key=f"stage_api_key_env_{stage}",
                help="R2A will not store a key for this stage; the subprocess must already be able to read this environment variable or CLI/router config.",
            )
            stage_api_key_env_vars[stage] = env_var.strip() or default_env
    return stage_api_keys, stage_api_key_env_vars


def _collect_web_settings(
    stage_api_keys: dict[str, str],
    stage_api_key_env_vars: dict[str, str],
    codex_executable_path: str,
    claude_executable_path: str,
    openclaw_executable_path: str,
    openclaw_config_path: str,
    codex_stage_timeout: int,
) -> dict:
    existing_settings = st.session_state.get("web_settings", {})
    if not isinstance(existing_settings, dict):
        existing_settings = {}
    settings = {
        "settings_schema_version": WEB_SETTINGS_SCHEMA_VERSION,
        "workspace_base_dir": st.session_state.get("setting_workspace_base_dir", str(DEFAULT_WORKSPACE_BASE)),
        "engineer_execution_environment": st.session_state.get(
            "setting_engineer_execution_environment",
            existing_settings.get("engineer_execution_environment", DEFAULT_ENGINEER_EXECUTION_ENVIRONMENT),
        ),
        "wsl_distro": st.session_state.get("setting_wsl_distro", existing_settings.get("wsl_distro", DEFAULT_WSL_DISTRO)),
        "wsl_cache_dir": st.session_state.get("setting_wsl_cache_dir", existing_settings.get("wsl_cache_dir", DEFAULT_WSL_CACHE_DIR)),
        "paper_backend": st.session_state.get("setting_paper_backend", DEFAULT_PAPER_BACKEND),
        "planner_backend": st.session_state.get("setting_planner_backend", DEFAULT_PLANNER_BACKEND),
        "engineer_executor": st.session_state.get("setting_engineer_executor", DEFAULT_ENGINEER_EXECUTOR),
        "manager_backend": st.session_state.get("setting_manager_backend", DEFAULT_MANAGER_BACKEND),
        "reviewer_backend": st.session_state.get("setting_reviewer_backend", DEFAULT_REVIEWER_BACKEND),
        "final_writer_backend": st.session_state.get("setting_final_writer_backend", DEFAULT_FINAL_WRITER_BACKEND),
        "codex_executable_path": codex_executable_path,
        "claude_executable_path": claude_executable_path,
        "openclaw_executable_path": existing_settings.get("openclaw_executable_path", ""),
        "openclaw_config_path": existing_settings.get("openclaw_config_path", ""),
        "codex_stage_timeout": int(codex_stage_timeout),
        "auto_refresh_interval_seconds": int(st.session_state.get("auto_refresh_interval_seconds", 0) or 0),
        "stage_model_selection": dict(existing_settings.get("stage_model_selection", {}) or {}),
        "stage_api_keys": stage_api_keys,
        "stage_api_key_env_vars": stage_api_key_env_vars,
    }
    stage_backends = _stage_backends_from_settings(settings)
    settings["stage_api_key_providers"] = {
        stage: st.session_state.get(f"stage_api_key_provider_{stage}", "auto")
        for stage, _ in STAGE_API_KEY_LABELS
        if stage_backends.get(stage, "") not in KEYLESS_BACKENDS
    }
    settings["stage_api_key_sources"] = {
        stage: st.session_state.get(f"stage_api_key_source_{stage}", "env")
        for stage, _ in STAGE_API_KEY_LABELS
        if stage_backends.get(stage, "") not in KEYLESS_BACKENDS
    }
    return _normalize_web_settings(settings)


def _settings_schema_version(settings: dict) -> int:
    try:
        return int(settings.get("settings_schema_version", 0))
    except (TypeError, ValueError):
        return 0


def _settings_need_openclaw_default_migration(settings: dict) -> bool:
    return isinstance(settings, dict) and _settings_schema_version(settings) < WEB_SETTINGS_SCHEMA_VERSION


def _stage_backends_from_settings(settings: dict) -> dict[str, str]:
    return {
        stage: str(settings.get(setting_key, OPENCLAW_STAGE_BACKEND_DEFAULTS[setting_key]))
        for stage, setting_key in STAGE_BACKEND_SETTING_KEYS.items()
    }


def _normalize_web_settings(settings: dict) -> dict:
    if not isinstance(settings, dict):
        return {}
    normalized = dict(settings)
    if _settings_need_openclaw_default_migration(normalized):
        for setting_key, default_backend in OPENCLAW_STAGE_BACKEND_DEFAULTS.items():
            if str(normalized.get(setting_key, "")) not in OPENCLAW_BACKENDS:
                normalized[setting_key] = default_backend
    normalized["settings_schema_version"] = WEB_SETTINGS_SCHEMA_VERSION
    normalized["workspace_base_dir"] = str(normalized.get("workspace_base_dir", str(DEFAULT_WORKSPACE_BASE)) or str(DEFAULT_WORKSPACE_BASE))
    engineer_environment = str(normalized.get("engineer_execution_environment", DEFAULT_ENGINEER_EXECUTION_ENVIRONMENT) or DEFAULT_ENGINEER_EXECUTION_ENVIRONMENT)
    if engineer_environment not in {"wsl", "windows"}:
        engineer_environment = DEFAULT_ENGINEER_EXECUTION_ENVIRONMENT
    normalized["engineer_execution_environment"] = engineer_environment
    normalized["wsl_distro"] = str(normalized.get("wsl_distro", DEFAULT_WSL_DISTRO) or DEFAULT_WSL_DISTRO)
    normalized["wsl_cache_dir"] = str(normalized.get("wsl_cache_dir", DEFAULT_WSL_CACHE_DIR) or DEFAULT_WSL_CACHE_DIR)
    normalized["openclaw_executable_path"] = _openclaw_persisted_path_value(normalized.get("openclaw_executable_path", ""))
    normalized["openclaw_config_path"] = _openclaw_persisted_path_value(normalized.get("openclaw_config_path", ""))
    normalized["auto_refresh_interval_seconds"] = 0
    stage_backends = _stage_backends_from_settings(normalized)
    stage_model_selection = normalized.get("stage_model_selection", {})
    if isinstance(stage_model_selection, dict):
        normalized["stage_model_selection"] = {
            str(stage): {str(key): str(value) for key, value in entry.items() if value is not None}
            for stage, entry in stage_model_selection.items()
            if isinstance(entry, dict) and entry.get("provider") and entry.get("model")
        }
    else:
        normalized["stage_model_selection"] = {}
    for bucket in ("stage_api_keys", "stage_api_key_env_vars", "stage_api_key_providers", "stage_api_key_sources"):
        values = dict(normalized.get(bucket, {}))
        for stage, backend in stage_backends.items():
            if backend in KEYLESS_BACKENDS:
                values.pop(stage, None)
        normalized[bucket] = values
    return normalized


def _persist_auto_refresh_interval(settings: dict, interval_seconds: int) -> dict:
    updated = dict(settings or {})
    updated["auto_refresh_interval_seconds"] = 0
    normalized = _normalize_web_settings(updated)
    _save_web_settings(normalized)
    return normalized


def _reset_stage_backend_widget_state(settings: dict) -> None:
    if st is None:
        return
    for setting_key in OPENCLAW_STAGE_BACKEND_DEFAULTS:
        widget_key = f"setting_{setting_key}"
        if widget_key in st.session_state:
            st.session_state[widget_key] = settings.get(setting_key, OPENCLAW_STAGE_BACKEND_DEFAULTS[setting_key])


def _load_web_settings() -> dict:
    if not WEB_SETTINGS_PATH.exists():
        return {}
    try:
        data = json.loads(WEB_SETTINGS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return _normalize_web_settings(data) if isinstance(data, dict) else {}


def _save_web_settings(settings: dict) -> None:
    WEB_SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    WEB_SETTINGS_PATH.write_text(json.dumps(_normalize_web_settings(settings), indent=2, ensure_ascii=False), encoding="utf-8")


def _ccr_config_summary() -> str:
    path = Path.home() / ".claude-code-router" / "config.json"
    if not path.exists():
        return "Claude Code Router config not detected. Use `ccr model` or `ccr ui` to configure provider/model routing."
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return f"Claude Code Router config exists but could not be parsed: `{path}`"
    providers = [str(item.get("name", "")) for item in data.get("Providers", []) if isinstance(item, dict) and item.get("name")]
    router = data.get("Router", {}) if isinstance(data.get("Router"), dict) else {}
    default_route = str(router.get("default", ""))
    provider_text = ", ".join(providers) if providers else "none"
    route_text = default_route or "not set"
    return f"Detected ccr config: providers `{provider_text}`, default route `{route_text}`."


def _restore_run(run: dict) -> None:
    apply_workspace_session(st.session_state, run)
    st.session_state.active_run_id = latest_run_id(run.get("repo_path", ""))
    st.session_state.workflow_result = {}
    st.session_state.workflow_error = ""
    st.session_state.loaded_historical_run = True  # User explicitly loaded historical run
    st.session_state.run_created_this_session = False
    st.session_state.recovered_active_run = False


def _create_workspace_clicked(
    base_dir: str,
    guidance: str,
    uploaded_paper,
    source_repo_path: str,
    github_repo_url: str,
    dataset_urls_text: str,
    max_dataset_download_gb: int,
    copy_repo: bool,
) -> None:
    paper_path = None
    goal = _resolve_goal(guidance)
    dataset_urls = _parse_dataset_urls(dataset_urls_text)
    user_hints = build_user_hints(
        text=guidance,
        source_urls=[github_repo_url],
        dataset_urls=dataset_urls,
    )
    with tempfile.TemporaryDirectory() as tmp:
        if uploaded_paper is not None:
            paper_path = Path(tmp) / uploaded_paper.name
            paper_path.write_bytes(uploaded_paper.getbuffer())
        workspace = create_workspace(
            base_dir=base_dir,
            goal=goal,
            paper_file_path=paper_path,
            source_repo_path=source_repo_path.strip() or None,
            github_repo_url=github_repo_url.strip() or None,
            dataset_urls=dataset_urls,
            max_dataset_download_gb=max_dataset_download_gb,
            copy_repo=copy_repo,
        )
    workspace["user_hints"] = user_hints
    apply_workspace_session(st.session_state, workspace)
    manifest = build_workspace_manifest(
        workspace_id=str(workspace.get("run_id", "")),
        workspace_path=str(workspace.get("workspace_dir", "")),
        paper_path=str(workspace.get("paper_path", "")),
        extra={
            "repo_path": str(workspace.get("repo_path", "")),
            "data_dir": str(workspace.get("data_dir", "")),
            "goal": str(workspace.get("goal", goal)),
            "repo_download": workspace.get("repo_download", {}),
            "dataset_downloads": workspace.get("dataset_downloads", []),
            "user_hints": user_hints,
        },
    )
    write_workspace_manifest(workspace["workspace_dir"], manifest)
    st.session_state.workflow_result = None
    st.session_state.workflow_error = ""
    st.session_state.active_run_id = ""
    st.session_state.workflow_running = False
    st.session_state.run_created_this_session = False  # New workspace, no run yet
    st.session_state.loaded_historical_run = False  # Clear any loaded historical run
    st.session_state.recovered_active_run = False
    st.success("Workspace created.")


def _show_workspace_summary() -> None:
    workspace = st.session_state.workspace
    if not workspace:
        st.caption("No workspace created yet.")
        return
    st.caption(f"Active run: {workspace.get('run_id', Path(str(workspace.get('workspace_dir', 'run'))).name)}")
    with st.expander("Workspace details", expanded=False):
        st.caption("Workspace directory")
        st.code(workspace["workspace_dir"])
        st.caption("Workflow repository")
        st.code(workspace["repo_path"])
        st.caption("Resolved goal")
        st.code(workspace.get("goal", DEFAULT_GOAL))
        if workspace.get("paper_path"):
            st.caption("Paper file")
            st.code(workspace["paper_path"])
        if workspace.get("repo_download"):
            st.caption("Repository download")
            st.json(workspace["repo_download"])
        if workspace.get("dataset_downloads"):
            st.caption("Dataset downloads")
            st.json(workspace["dataset_downloads"])


def _show_paper_preprocess_status() -> None:
    workspace = st.session_state.workspace
    if not workspace:
        st.caption("No workspace yet.")
        return
    repo_path = workspace["repo_path"]
    paper_path = workspace.get("paper_path", "")
    text_path = report_path(repo_path, "paper_text")
    pages_path = report_path(repo_path, "paper_pages")
    sections_path = report_path(repo_path, "paper_sections")
    captions_path = report_path(repo_path, "paper_captions")
    context_path = report_path(repo_path, "paper_context")
    card_path = report_path(repo_path, "paper_reproduction_card")
    figures_tables_path = report_path(repo_path, "paper_figures_tables")
    parse_quality_path = report_path(repo_path, "paper_parse_quality")
    analysis_path = report_path(repo_path, "paper_analysis")
    status = "generated" if context_path.exists() else "pending"
    text = ""
    if text_path.exists():
        text = text_path.read_text(encoding="utf-8", errors="replace")
    text_length = len(text)
    structure = summarize_structure(text)
    cols = st.columns(6)
    cols[0].metric("Paper", "uploaded" if paper_path else "not uploaded")
    cols[1].metric("Extraction", status)
    cols[2].metric("Text length", text_length)
    cols[3].metric("URLs", len(structure["source_or_artifact_urls"]))
    cols[4].metric("Figures", len(structure["figures"]))
    cols[5].metric("Tables", len(structure["tables"]))
    cols2 = st.columns(3)
    cols2[0].metric("Datasets", len(structure["datasets"]))
    cols2[1].metric("Baselines", len(structure["baselines"]))
    cols2[2].metric("Metrics", len(structure["metrics"]))
    st.caption("下面两项是 Paper 阶段抽取出的本地报告路径和疑似源码/数据链接，只是线索清单，不代表已经完成复现。")
    with st.expander("Paper artifact paths", expanded=False):
        st.caption("这些是 R2A 从论文解析阶段生成的中间报告文件路径，Planner/Reviewer 会读取它们。")
        st.code(f"{analysis_path}\n{context_path}\n{text_path}\n{sections_path}\n{captions_path}\n{pages_path}\n{card_path}\n{figures_tables_path}\n{parse_quality_path}")
    if structure["source_or_artifact_urls"]:
        with st.expander("Extracted source/artifact/data URLs", expanded=False):
            st.caption("这些 URL 是从论文文本中抽取的候选源码、artifact 或数据链接，需要后续 Engineer/Manager 验证。")
            st.json(structure["source_or_artifact_urls"])
    if structure["figures"] or structure["tables"]:
        st.warning("Figures/tables are extracted from captions and nearby text only; image content is not parsed.")


def _run_workflow(
    guidance: str,
    paper_backend: str,
    planner_backend: str,
    engineer_executor: str,
    manager_backend: str,
    reviewer_backend: str,
    final_writer_backend: str,
    auto_approve: bool,
    output_language: str,
    auto_iterate: bool,
    max_iterations: int,
    target_reproduction_level: str,
    download_budget_gb: int,
    allow_official_dataset_download: bool,
    allow_full_benchmark: bool,
    allow_external_baselines: bool,
    allow_network: bool = False,
    allowed_network_scope: str = "",
    codex_executable_path: str = DEFAULT_CODEX_EXECUTABLE,
    claude_executable_path: str = DEFAULT_CLAUDE_EXECUTABLE,
    openclaw_executable_path: str = "",
    openclaw_config_path: str = "",
    codex_stage_timeout: int = 10800,
    engineer_execution_environment: str = "windows",
    wsl_distro: str = DEFAULT_WSL_DISTRO,
    wsl_cache_dir: str = DEFAULT_WSL_CACHE_DIR,
    stage_api_keys: dict[str, str] | None = None,
    stage_api_key_env_vars: dict[str, str] | None = None,
    stage_model_selection: dict[str, dict[str, str]] | None = None,
    run_status_slot=None,
    workflow_review_slot=None,
) -> None:
    workspace = st.session_state.workspace
    if not workspace:
        return
    repo_path = workspace["repo_path"]
    _sync_workspace_manifest_backends(
        workspace,
        paper_backend=paper_backend,
        planner_backend=planner_backend,
        engineer_executor=engineer_executor,
        manager_backend=manager_backend,
        reviewer_backend=reviewer_backend,
        final_writer_backend=final_writer_backend,
    )
    graph = create_research_graph()
    initial_state = _build_initial_state(
        workspace,
        guidance,
        paper_backend,
        planner_backend,
        engineer_executor,
        manager_backend,
        reviewer_backend,
        auto_approve,
        output_language,
        auto_iterate,
        max_iterations,
        target_reproduction_level,
        download_budget_gb,
        allow_official_dataset_download,
        allow_full_benchmark,
        allow_external_baselines,
        allow_network,
        allowed_network_scope,
        codex_executable_path,
        claude_executable_path,
        openclaw_executable_path,
        openclaw_config_path,
        codex_stage_timeout,
        engineer_execution_environment,
        wsl_distro,
        wsl_cache_dir,
        stage_api_keys,
        stage_api_key_env_vars,
        stage_model_selection,
        final_writer_backend=final_writer_backend,
    )
    try:
        st.session_state.workflow_running = True
        result = _run_graph_with_progress(
            graph,
            initial_state,
            repo_path,
            run_status_slot=run_status_slot,
            workflow_review_slot=workflow_review_slot,
            auto_iterate=auto_iterate,
            max_iterations=max_iterations,
        )
    except Exception as exc:
        st.session_state.workflow_running = False
        st.session_state.workflow_error = str(exc)
        fallback_result = _failure_finalize_after_engineer(repo_path, initial_state, exc)
        if fallback_result:
            st.session_state.workflow_result = _redact_sensitive_state(fallback_result)
            st.session_state.workflow_running = False
            _render_workflow_review_slot(workflow_review_slot, auto_iterate, max_iterations)
            st.warning(
                "Workflow failed after Engineer wrote terminal artifacts. "
                "R2A attempted Manager/Reviewer/Final fallback finalization."
            )
            st.error(f"Original workflow failure: {exc}")
            return
        st.error(f"Workflow failed: {exc}")
        _render_workflow_review_slot(workflow_review_slot, auto_iterate, max_iterations)
        return
    st.session_state.workflow_running = False
    st.session_state.workflow_result = _redact_sensitive_state(result)
    st.session_state.workflow_error = ""
    _render_workflow_review_slot(workflow_review_slot, auto_iterate, max_iterations)
    st.success("Workflow finished. See Workflow Review for the structured summary and full report.")


def _start_workflow_background(**kwargs) -> None:
    workspace = st.session_state.workspace
    if not workspace:
        return
    repo_path = workspace["repo_path"]
    run_id = new_run_id()
    workflow_kwargs = {
        "reviewer_backend": DEFAULT_REVIEWER_BACKEND,
        "auto_iterate": DEFAULT_AUTO_ITERATE,
        "max_iterations": DEFAULT_MAX_ITERATIONS_MINIMAL,
        **{key: value for key, value in kwargs.items() if key not in {"run_status_slot", "workflow_review_slot"}},
    }
    initial_state = _build_initial_state(workspace, **workflow_kwargs)
    initial_state["runtime_run_id"] = run_id
    create_run_record(
        repo_path,
        run_id,
        status="running",
        workspace_dir=str(workspace.get("workspace_dir", "")),
        wsl_distro=str(initial_state.get("wsl_distro", DEFAULT_WSL_DISTRO)),
        current_stage="paper",
        stage_status="running",
        iteration=1,
        backend=str(initial_state.get("paper_backend", "")),
        fallback_used=False,
        warning=None,
        blocker=None,
    )
    thread = threading.Thread(
        target=_workflow_background_worker,
        args=(initial_state, repo_path, run_id, bool(initial_state.get("auto_iterate")), int(initial_state.get("max_iterations", DEFAULT_MAX_ITERATIONS))),
        daemon=True,
    )
    thread.start()
    st.session_state.active_run_id = run_id
    st.session_state.workflow_thread = thread
    st.session_state.workflow_running = True
    st.session_state.workflow_result = {}
    st.session_state.workflow_error = ""
    st.session_state.run_created_this_session = True  # Mark that this run was created this session
    st.session_state.loaded_historical_run = False  # Clear any loaded historical run
    st.session_state.recovered_active_run = False


def _workflow_background_worker(initial_state: dict, repo_path: str | Path, run_id: str, auto_iterate: bool, max_iterations: int) -> None:
    graph = create_research_graph()
    result = dict(initial_state)
    try:
        with workflow_run_context(repo_path, run_id, wsl_distro=str(initial_state.get("wsl_distro", DEFAULT_WSL_DISTRO))):
            for event in graph.stream(initial_state):
                record = read_run_record(repo_path, run_id)
                if record.get("cancel_requested"):
                    result = {
                        **result,
                        "stopped": True,
                        "stop_reason": "user_cancelled",
                        "loop_status": "stopped",
                    }
                    update_run_record(repo_path, run_id, status="stopped", current_stage="cancelled")
                    break
                if not isinstance(event, dict):
                    continue
                for node_name, node_state in event.items():
                    stage_name = NODE_TO_STAGE.get(node_name, node_name)
                    update_run_heartbeat(
                        repo_path,
                        run_id,
                        current_stage=stage_name,
                        stage_status="running",
                        iteration=int(result.get("iteration", 1) or 1),
                        backend=_stage_backend_label(stage_name, result),
                        fallback_used=bool(result.get("fallback_used") or result.get("paper_backend") == "local_preprocess_fallback"),
                        warning=_latest_warning(result),
                        blocker=_current_blocker(result),
                    )
                    if isinstance(node_state, dict):
                        result.update(node_state)
                    update_run_heartbeat(
                        repo_path,
                        run_id,
                        current_stage=_next_background_stage(node_name, result),
                        stage_status="running",
                        iteration=int(result.get("iteration", 1) or 1),
                        backend=_stage_backend_label(_next_background_stage(node_name, result), result),
                        fallback_used=bool(result.get("fallback_used") or result.get("paper_backend") == "local_preprocess_fallback"),
                        warning=_latest_warning(result),
                        blocker=_current_blocker(result),
                    )
        _write_background_result(repo_path, run_id, _redact_sensitive_state(result))
        final_status = _workflow_terminal_status(result)
        update_run_record(
            repo_path,
            run_id,
            status=final_status,
            current_stage=str(result.get("current_stage", "final")),
            failed_stage=_failed_stage_from_result(result),
            error_code=_error_code_from_result(result),
            approval_ready=_approval_ready_from_result(repo_path, result),
            manager_executed=bool(result.get("manager_status")) and not _planner_failed(result),
            reviewer_executed=bool(result.get("reviewer_verdict")) and not _planner_failed(result),
            stage_status="finished",
            backend="",
        )
    except Exception as exc:
        fallback = _failure_finalize_after_engineer(repo_path, result, exc)
        if fallback:
            result = fallback
        result = {**result, "workflow_error": str(exc), "errors": [*result.get("errors", []), f"{type(exc).__name__}: {exc}"]}
        _write_background_result(repo_path, run_id, _redact_sensitive_state(result))
        update_run_record(repo_path, run_id, status="failed", stage_status="failed", termination_reason=f"{type(exc).__name__}: {exc}")


def _next_background_stage(node_name: str, result: dict) -> str:
    stage = NODE_TO_STAGE.get(node_name, node_name)
    decision = result.get("decision_status") if isinstance(result.get("decision_status"), dict) else {}
    if decision and decision.get("typed_decision") not in {"", "continue_iteration"}:
        return "final"
    if stage == "paper":
        return "planner"
    if stage == "planner":
        return "final" if _planner_failed(result) else "approval"
    if stage == "approval":
        return "final" if result.get("stopped") else "engineer"
    if stage == "engineer":
        return "manager"
    if stage == "manager":
        return "reviewer"
    if stage == "reviewer":
        return "final"
    return stage


def _stage_backend_label(stage: str, state: dict) -> str:
    return {
        "paper": str(state.get("paper_backend", "")),
        "planner": str(state.get("planner_backend", "")),
        "engineer": str(state.get("engineer_executor", state.get("executor", ""))),
        "manager": str(state.get("manager_backend", "")),
        "reviewer": str(state.get("reviewer_backend", "")),
    }.get(stage, "")


def _latest_warning(state: dict) -> str | None:
    warnings = state.get("warnings", [])
    if isinstance(warnings, list) and warnings:
        return str(warnings[-1])
    return None


def _current_blocker(state: dict) -> str | None:
    decision = state.get("decision_status") if isinstance(state.get("decision_status"), dict) else {}
    if decision:
        reason = str(decision.get("reason_code", "") or decision.get("typed_decision", "") or "")
        blockers = decision.get("active_blockers", []) if isinstance(decision.get("active_blockers"), list) else []
        if blockers and isinstance(blockers[0], dict):
            return str(blockers[0].get("last_message") or blockers[0].get("reason_code") or reason)
        if reason:
            return reason
    if state.get("stop_reason"):
        return str(state.get("stop_reason"))
    transaction = state.get("planner_transaction", {}) if isinstance(state.get("planner_transaction"), dict) else {}
    if transaction.get("failure_category"):
        return str(transaction.get("failure_category"))
    return None


def _workflow_terminal_status(result: dict) -> str:
    decision = result.get("decision_status") if isinstance(result.get("decision_status"), dict) else {}
    typed = str(decision.get("typed_decision", "") or "")
    if typed == "stop_success":
        return "completed_success"
    if typed == "stop_evidence_cap":
        return "completed_with_limitations"
    if typed in {"request_paper", "request_source", "request_dataset", "request_approval", "terminal_failed", "retry_backend"}:
        return "completed_with_failure"
    if result.get("stopped") and result.get("stop_reason") == "user_cancelled":
        return "cancelled"
    if _planner_failed(result) or result.get("workflow_error"):
        return "failed"
    if result.get("stopped"):
        return "completed_with_failure"
    if str(result.get("manager_status", "")).upper() == "FAIL":
        return "completed_with_failure"
    if _reviewer_blocks_success(result):
        return "completed_with_failure"
    return "completed_success"


def _planner_failed(result: dict) -> bool:
    transaction = result.get("planner_transaction", {}) or {}
    metadata = result.get("metadata", {}) or {}
    diagnostic = transaction.get("diagnostic", {}) if isinstance(transaction.get("diagnostic"), dict) else {}
    planner_stop_reasons = {
        "PLANNER_FORBIDDEN_WRITE",
        "BACKEND_TRANSIENT_FAILURE",
        "planner_stage_failed",
        "PLANNER_BACKEND_FAILURE",
        "PLANNER_BACKEND_NOT_CONFIGURED",
        "PLANNER_MODEL_FAILURE",
        "PLANNER_SCHEMA_VALIDATION_FAILED",
        "PLANNER_TRANSACTION_FAILED",
        "PLANNER_MISSING_REQUIRED_OUTPUT",
        "PLANNER_STALE_OUTPUT",
        "PLANNER_CONTRACT_VALIDATION_FAILED",
    }
    return bool(
        result.get("loop_status") == "planner_failed"
        or result.get("stop_reason") in planner_stop_reasons
        or transaction.get("validation_status") == "FAIL"
        or transaction.get("committed") is False
        or diagnostic.get("planner_validation_passed") is False
        or diagnostic.get("planner_committed") is False
        or metadata.get("planner_stage_failure")
    )


def _reviewer_blocks_success(result: dict) -> bool:
    verdict = str(result.get("reviewer_verdict", "") or "").upper()
    return verdict in {
        "REJECT",
        "NEEDS_FIX",
        "NEEDS_INPUT",
        "NEEDS_OFFICIAL_INPUT",
        "NEEDS_INPUT_OR_BUDGET",
        "BORDERLINE",
    }


def _failed_stage_from_result(result: dict) -> str:
    decision = result.get("decision_status") if isinstance(result.get("decision_status"), dict) else {}
    typed = str(decision.get("typed_decision", "") or "")
    if typed == "request_paper":
        return "paper"
    if typed == "request_source":
        return "paper"
    if typed in {"terminal_failed", "retry_backend"}:
        reason = str(decision.get("reason_code", "") or "")
        if "PLANNER" in reason:
            return "planner"
        if "ENGINEER" in reason or "PLACEHOLDER" in reason:
            return "engineer"
    if _planner_failed(result):
        return "planner"
    if result.get("workflow_error"):
        return str(result.get("current_stage") or "")
    if _reviewer_blocks_success(result):
        return "reviewer"
    return ""


def _error_code_from_result(result: dict) -> str:
    decision = result.get("decision_status") if isinstance(result.get("decision_status"), dict) else {}
    if decision:
        return str(decision.get("reason_code", "") or decision.get("typed_decision", "") or "")
    transaction = result.get("planner_transaction", {}) or {}
    metadata = result.get("metadata", {}) or {}
    planner_failure = metadata.get("planner_stage_failure", {}) if isinstance(metadata.get("planner_stage_failure"), dict) else {}
    return str(
        transaction.get("execution_status")
        or transaction.get("failure_category")
        or planner_failure.get("execution_status")
        or planner_failure.get("failure_category")
        or result.get("stop_reason")
        or ""
    )


def _approval_ready_from_result(repo_path: str | Path, result: dict) -> bool:
    transaction = result.get("planner_transaction", {}) or {}
    if not transaction:
        tx_path = Path(repo_path) / ".r2a" / "logs" / "planner_transaction.json"
        if tx_path.exists():
            try:
                transaction = json.loads(tx_path.read_text(encoding="utf-8", errors="replace"))
            except (OSError, json.JSONDecodeError):
                transaction = {}
    return bool(
        transaction.get("validation_status") == "PASS"
        and transaction.get("committed")
        and (Path(repo_path) / ".r2a" / "TASK_SPEC.md").exists()
        and (Path(repo_path) / ".r2a" / "EXPERIMENT_CONTRACT.md").exists()
    )


def _write_background_result(repo_path: str | Path, run_id: str, result: dict) -> None:
    write_run_result(repo_path, run_id, result)


def _sync_background_run_state() -> None:
    sync_background_run_readonly(st.session_state)
    workspace = st.session_state.get("workspace")
    run_id = st.session_state.get("active_run_id", "")
    if not workspace or not run_id:
        return
    repo_path = workspace["repo_path"]
    record = read_run_record(repo_path, run_id)
    if not record:
        return
    status = str(record.get("status", ""))
    if status in {"completed_success", "completed_with_failure", "completed", "stopped", "force_killed", "failed", "failed_to_kill", "cancelled"}:
        result = read_run_result(repo_path, run_id)
        if result:
            st.session_state.workflow_result = result
            st.session_state.workflow_error = str(result.get("workflow_error", ""))


def _maybe_autorefresh(interval_ms: int | None = None) -> None:
    if st is None:
        return
    decision = autorefresh_decision(
        st.session_state,
        ui_polling_enabled=feature_enabled(FEATURE_UI_POLLING),
    )
    st.session_state.auto_refresh_diagnostic = decision


def _show_run_control_panel() -> None:
    workspace = st.session_state.get("workspace")
    run_id = st.session_state.get("active_run_id", "")

    # Don't auto-load latest run on fresh startup
    # Only show run control if run was created this session or explicitly loaded
    run_created_this_session = st.session_state.get("run_created_this_session", False)
    loaded_historical_run = st.session_state.get("loaded_historical_run", False)
    recovered_active_run = st.session_state.get("recovered_active_run", False)

    if not run_id or not (run_created_this_session or loaded_historical_run or recovered_active_run):
        # Show empty state for run control
        st.caption("当前没有运行中的 workflow / No active workflow run")
        return

    if not workspace:
        return
    repo_path = workspace["repo_path"]
    record = read_run_record(repo_path, run_id)
    if not record:
        return
    stage_model = _active_current_stage_model(repo_path, run_id=run_id, record=record)
    status_value = str(record.get("status", ""))
    can_stop = status_value in {"running", "stopping", "force_killing", "failed_to_kill"}
    st.session_state.workflow_running = can_stop
    cols = st.columns(5)
    cols[0].metric("Run status", status_value or "-")
    cols[1].metric("Run ID", run_id[-12:])
    cols[2].metric("Stage", str(stage_model.get("stage", "-")))
    cols[3].metric("Windows PIDs", len(record.get("windows_processes", []) or []))
    cols[4].metric("WSL PGIDs", len(record.get("wsl_process_groups", []) or []))
    st.caption(f"Run registry: `{runtime_runs_dir(repo_path)}`")
    st.caption(f"Stage source: `{stage_model.get('source', 'unknown')}`")
    if stage_model.get("warning"):
        st.warning(stage_model["warning"])
    stop_cols = st.columns([1, 1, 2])
    if stop_cols[0].button("Stop current run", disabled=not can_stop, use_container_width=True):
        request_cancel(repo_path, run_id, force=False)
        st.warning("Stop requested. Waiting for the current safe boundary.")
    confirm_force = stop_cols[1].checkbox("Confirm force", value=False)
    if stop_cols[2].button("Force stop current run", disabled=not (confirm_force and can_stop), use_container_width=True):
        request_cancel(repo_path, run_id, force=True)
        st.error("Force stop requested for the current run. Partial artifacts may remain.")
    if st.button("Refresh Status", use_container_width=True):
        _sync_background_run_state()
        st.session_state.last_refreshed_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        st.rerun()


def _redact_sensitive_state(state: dict) -> dict:
    cleaned = dict(state)
    if "stage_api_keys" in cleaned:
        cleaned["stage_api_keys"] = {stage: "***" for stage in cleaned.get("stage_api_keys", {})}
    return cleaned


def _failure_finalize_after_engineer(repo_path: str | Path, state: dict, exc: Exception) -> dict:
    repo = Path(repo_path)
    if not _engineer_terminal_artifacts_exist(repo):
        return {}
    errors = [
        *state.get("errors", []),
        f"Workflow failed after Engineer terminal artifacts existed: {type(exc).__name__}: {exc}",
    ]
    finalize_state = {
        **state,
        "repo_path": str(repo),
        "errors": errors,
        "manager_backend": "rules",
        "reviewer_backend": "rules",
        "auto_iterate": False,
        "strict": False,
        "workflow_error": str(exc),
    }
    fallback_errors: list[str] = []
    try:
        finalize_state = run_manager_agent(finalize_state, force=True)
    except Exception as manager_exc:
        fallback_errors.append(f"Fallback Manager failed: {type(manager_exc).__name__}: {manager_exc}")
    try:
        finalize_state = run_reviewer_agent({**finalize_state, "reviewer_backend": "rules"}, force=True)
    except Exception as reviewer_exc:
        fallback_errors.append(f"Fallback Reviewer failed: {type(reviewer_exc).__name__}: {reviewer_exc}")
        finalize_state = {
            **finalize_state,
            "reviewer_verdict": finalize_state.get("reviewer_verdict", "BORDERLINE") or "BORDERLINE",
            "suggested_next_action": "Fallback finalization could not complete Reviewer; inspect errors and Engineer artifacts.",
        }
    if fallback_errors:
        finalize_state["errors"] = [*finalize_state.get("errors", []), *fallback_errors]
    fallback_failed = bool(fallback_errors)
    try:
        finalize_state = archive_current_iteration(finalize_state)
    except Exception as archive_exc:
        fallback_failed = True
        finalize_state["errors"] = [
            *finalize_state.get("errors", []),
            f"Fallback archive failed: {type(archive_exc).__name__}: {archive_exc}",
        ]
    if fallback_failed:
        _write_minimal_fallback_final_report(repo, finalize_state)
        return finalize_state
    try:
        return final_node(finalize_state)
    except Exception as final_exc:
        finalize_state["errors"] = [
            *finalize_state.get("errors", []),
            f"Fallback FINAL_REPORT generation failed: {type(final_exc).__name__}: {final_exc}",
        ]
        _write_minimal_fallback_final_report(repo, finalize_state)
        return finalize_state


def _engineer_terminal_artifacts_exist(repo_path: str | Path) -> bool:
    results_dir = Path(repo_path) / ".r2a" / "results"
    return (results_dir / "ENGINEER_DONE.txt").exists() or (results_dir / "reproduction_status.csv").exists()


def _write_minimal_fallback_final_report(repo_path: str | Path, state: dict) -> Path:
    output = report_path(repo_path, "final")
    output.parent.mkdir(parents=True, exist_ok=True)
    errors = "\n".join(f"- {item}" for item in state.get("errors", [])) or "- No captured errors."
    text = (
        "# FINAL_REPORT\n\n"
        "## Final Status\n\n"
        "- fallback_finalize_failed\n\n"
        "## Summary\n\n"
        "Workflow failed after Engineer terminal artifacts were present. "
        "R2A attempted Manager/Reviewer/Final fallback finalization; at least one fallback step failed.\n\n"
        "## Errors\n\n"
        f"{errors}\n"
    )
    output.write_text(text, encoding="utf-8")
    state["final_report_path"] = str(output)
    state["loop_status"] = "fallback_finalize_failed"
    state["stop_reason"] = "fallback_finalize_failed"
    return output


def _sync_workspace_manifest_backends(
    workspace: dict,
    *,
    paper_backend: str,
    planner_backend: str,
    engineer_executor: str,
    manager_backend: str,
    reviewer_backend: str,
    final_writer_backend: str = DEFAULT_FINAL_WRITER_BACKEND,
) -> None:
    workspace_dir = str(workspace.get("workspace_dir", "") or "")
    if not workspace_dir:
        return
    manifest = read_workspace_manifest(workspace_dir) or {}
    backends = {
        "paper_backend": paper_backend,
        "planner_backend": planner_backend,
        "engineer_executor": engineer_executor,
        "manager_backend": manager_backend,
        "reviewer_backend": reviewer_backend,
        "final_writer_backend": final_writer_backend,
    }
    manifest.update(backends)
    manifest["last_run_backends"] = dict(backends)
    manifest["repo_path"] = str(workspace.get("repo_path", manifest.get("repo_path", "")) or "")
    manifest["paper_path"] = str(workspace.get("paper_path", manifest.get("paper_path", "")) or "")
    manifest["goal"] = str(workspace.get("goal", manifest.get("goal", "")) or "")
    if workspace.get("repo_download") is not None:
        manifest["repo_download"] = workspace.get("repo_download", {})
    write_workspace_manifest(workspace_dir, manifest)


def _build_initial_state(
    workspace: dict,
    guidance: str = "",
    paper_backend: str = DEFAULT_PAPER_BACKEND,
    planner_backend: str = DEFAULT_PLANNER_BACKEND,
    engineer_executor: str = DEFAULT_ENGINEER_EXECUTOR,
    manager_backend: str = DEFAULT_MANAGER_BACKEND,
    reviewer_backend: str = DEFAULT_REVIEWER_BACKEND,
    auto_approve: bool = DEFAULT_AUTO_APPROVE,
    output_language: str = "Chinese",
    auto_iterate: bool = DEFAULT_AUTO_ITERATE,
    max_iterations: int = DEFAULT_MAX_ITERATIONS_MINIMAL,
    target_reproduction_level: str = DEFAULT_TARGET_REPRODUCTION_LEVEL,
    download_budget_gb: int = 20,
    allow_official_dataset_download: bool = False,
    allow_full_benchmark: bool = False,
    allow_external_baselines: bool = False,
    allow_network: bool = False,
    allowed_network_scope: str = "",
    codex_executable_path: str = DEFAULT_CODEX_EXECUTABLE,
    claude_executable_path: str = DEFAULT_CLAUDE_EXECUTABLE,
    openclaw_executable_path: str = "",
    openclaw_config_path: str = "",
    codex_stage_timeout: int = 10800,
    engineer_execution_environment: str = "windows",
    wsl_distro: str = DEFAULT_WSL_DISTRO,
    wsl_cache_dir: str = DEFAULT_WSL_CACHE_DIR,
    stage_api_keys: dict[str, str] | None = None,
    stage_api_key_env_vars: dict[str, str] | None = None,
    stage_model_selection: dict[str, dict[str, str]] | None = None,
    final_writer_backend: str = DEFAULT_FINAL_WRITER_BACKEND,
) -> dict:
    repo_path = workspace["repo_path"]
    paper_path = workspace.get("paper_path", "")
    resolved_goal = workspace.get("goal") or _resolve_goal(guidance)
    language = "zh" if output_language == "Chinese" else "en"
    stage_codex_enabled = any(
        value in {"codex", "codex_review", "claude", "claude_review", "openclaw", "openclaw_review", "openclaw_reader"}
        for value in (planner_backend, engineer_executor, manager_backend, reviewer_backend, final_writer_backend)
    ) or paper_backend == "openclaw_reader"
    openclaw_config = resolve_openclaw_config(
        openclaw_executable_path=openclaw_executable_path or None,
        openclaw_config_path=openclaw_config_path or None,
    )
    network_scope = _network_scope_list(allowed_network_scope) if allow_network else []
    if allow_network and not network_scope:
        network_scope = [DEFAULT_ALLOWED_NETWORK_SCOPE]
    network_reason = "explicit_user_allowed_network" if allow_network else "network_not_authorized"
    dataset_hint_urls = list(workspace.get("dataset_urls", []) or [])
    for item in workspace.get("dataset_downloads", []) or []:
        if isinstance(item, dict) and item.get("url"):
            dataset_hint_urls.append(str(item.get("url")))
    raw_user_hints = normalize_user_hints(
        workspace.get("user_hints") if isinstance(workspace.get("user_hints"), dict) else {},
        fallback_text=guidance,
    )
    user_hints = build_user_hints(
        text=str(raw_user_hints.get("text") or guidance or ""),
        source_urls=[workspace.get("github_repo_url", ""), *raw_user_hints.get("source_urls", [])],
        dataset_urls=[*dataset_hint_urls, *raw_user_hints.get("dataset_urls", [])],
        model_weight_urls=raw_user_hints.get("model_weight_urls"),
        other_urls=raw_user_hints.get("other_urls"),
        origin=str(raw_user_hints.get("origin") or "user_provided_hint"),
    )
    return {
        "workspace_dir": str(workspace.get("workspace_dir", "")),
        "repo_path": str(repo_path),
        "paper_path": str(paper_path) if paper_path else "",
        "goal": resolved_goal,
        "guidance": guidance.strip(),
        "resolved_goal": resolved_goal,
        "extra_context": format_user_hints_markdown(user_hints),
        "user_hints": user_hints,
        "github_repo_url": str(workspace.get("github_repo_url", "") or ""),
        "source_repo_path": str(workspace.get("source_repo_path", "") or ""),
        "dataset_urls": user_hints.get("dataset_urls", []),
        "model_weight_urls": user_hints.get("model_weight_urls", []),
        "executor": engineer_executor,
        "paper_backend": paper_backend,
        "planner_backend": planner_backend,
        "engineer_executor": engineer_executor,
        "engineer_execution_environment": engineer_execution_environment,
        "wsl_distro": wsl_distro,
        "wsl_cache_dir": wsl_cache_dir,
        "manager_backend": manager_backend,
        "reviewer_backend": reviewer_backend,
        "final_writer_backend": final_writer_backend,
        "language": language,
        "output_language": output_language,
        "stage_codex_enabled": stage_codex_enabled,
        "auto_approve": auto_approve,
        "approved": auto_approve,
        "auto_iterate": auto_iterate,
        "iteration": 1,
        "max_iterations": max_iterations,
        "target_reproduction_level": target_reproduction_level,
        "download_budget_gb": int(download_budget_gb),
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
        "metadata": {
            "user_hints": user_hints,
            "github_repo_url": str(workspace.get("github_repo_url", "") or ""),
            "source_repo_path": str(workspace.get("source_repo_path", "") or ""),
            "dataset_downloads": workspace.get("dataset_downloads", []),
        },
        "stage_model_selection": dict(stage_model_selection or {}),
        "codex_stage_timeout": codex_stage_timeout,
        "codex_executable_path": codex_executable_path,
        "claude_executable_path": claude_executable_path,
        "openclaw_executable_path": openclaw_config["openclaw_executable_path"],
        "openclaw_config_path": openclaw_config["openclaw_config_path"],
        "openclaw_provider": "",
        "openclaw_model": "",
        "openclaw_runner": "",
        "openclaw_agent": "",
        "stage_api_keys": dict(stage_api_keys or {}),
        "stage_api_key_env_vars": dict(stage_api_key_env_vars or {}),
        "strict": False,
        "timeout": codex_stage_timeout,
    }


def _network_scope_list(value: str) -> list[str]:
    return [item.strip() for item in str(value or "").replace(";", ",").replace("\n", ",").split(",") if item.strip()]


def _run_graph_with_progress(
    graph,
    initial_state: dict,
    repo_path: str | Path,
    *,
    run_status_slot=None,
    workflow_review_slot=None,
    auto_iterate: bool = False,
    max_iterations: int = DEFAULT_MAX_ITERATIONS,
) -> dict:
    result = dict(initial_state)
    _init_stage_runtime(initial_state)
    _render_workflow_review_slot(workflow_review_slot, auto_iterate, max_iterations)
    status_parent = run_status_slot or st
    with status_parent.container():
        status = st.status("Workflow running", expanded=True)
        status.write(f"Workspace: `{repo_path}`")
        status.write("Stage logs are written under `.r2a/logs` while AI subprocesses run.")
        current = st.empty()
    try:
        for event in graph.stream(initial_state):
            if not isinstance(event, dict):
                continue
            for node_name, node_state in event.items():
                if isinstance(node_state, dict):
                    result.update(node_state)
                _mark_stage_done(node_name, result)
                _mark_next_stage_running(node_name, result)
                current.info(f"Current status: {_node_label(node_name)} finished.")
                status.write(f"{_node_label(node_name)} finished.")
                _render_workflow_review_slot(workflow_review_slot, auto_iterate, max_iterations)
        status.update(label="Workflow finished", state="complete", expanded=False)
        _render_workflow_review_slot(workflow_review_slot, auto_iterate, max_iterations)
        return result
    except Exception:
        _mark_running_stage_failed()
        _render_workflow_review_slot(workflow_review_slot, auto_iterate, max_iterations)
        status.update(label="Workflow failed", state="error", expanded=True)
        raise


def _init_stage_runtime(initial_state: dict) -> None:
    iteration = int(initial_state.get("iteration", 1))
    stages = {}
    for stage, label in WORKFLOW_STAGE_ORDER:
        stage_iteration = 1 if stage == "paper" else (None if stage == "final" else iteration)
        stages[stage] = {"label": label, "status": "pending", "iteration": stage_iteration}
    stages["paper"]["status"] = "running"
    st.session_state.stage_runtime = {"current_iteration": iteration, "stages": stages}


def _mark_stage_done(node_name: str, state: dict) -> None:
    runtime = st.session_state.get("stage_runtime")
    if not runtime:
        return
    if node_name == "prepare_next_iteration_node":
        iteration = int(state.get("iteration", runtime.get("current_iteration", 1)))
        runtime["current_iteration"] = iteration
        for stage in ("planner", "approval", "engineer", "manager", "reviewer"):
            runtime["stages"][stage] = {
                "label": dict(WORKFLOW_STAGE_ORDER)[stage],
                "status": "pending",
                "iteration": iteration,
            }
        runtime["stages"]["paper"]["status"] = "done"
        runtime["stages"]["final"]["status"] = "pending"
        return
    stage = NODE_TO_STAGE.get(node_name)
    if not stage:
        return
    runtime["stages"][stage]["status"] = "done"
    if stage not in {"paper", "final"}:
        runtime["stages"][stage]["iteration"] = int(state.get("iteration", runtime.get("current_iteration", 1)))


def _mark_next_stage_running(node_name: str, state: dict) -> None:
    runtime = st.session_state.get("stage_runtime")
    if not runtime:
        return
    next_stage = ""
    if node_name == "paper_node":
        next_stage = "planner"
    elif node_name == "planner_node":
        next_stage = "approval" if route_after_planner(state) == "approval" else "final"
    elif node_name == "human_approval_node":
        next_stage = "final" if state.get("stopped") else "engineer"
    elif node_name == "engineer_node":
        next_stage = "manager"
    elif node_name == "manager_node":
        next_stage = "reviewer"
    elif node_name == "reviewer_node":
        # Use route_after_reviewer to determine next stage
        # This ensures UI matches the real workflow routing
        route_result = route_after_reviewer(state)
        if route_result == "prepare_next_iteration":
            # prepare_next_iteration is an internal transition node, not shown in UI
            # _mark_stage_done will reset stages when it completes
            next_stage = ""
        else:
            next_stage = "final"
    elif node_name == "prepare_next_iteration_node":
        next_stage = "planner"
    if next_stage and runtime["stages"].get(next_stage, {}).get("status") == "pending":
        runtime["stages"][next_stage]["status"] = "running"
        if next_stage not in {"paper", "final"}:
            runtime["stages"][next_stage]["iteration"] = int(state.get("iteration", runtime.get("current_iteration", 1)))


def _mark_running_stage_failed() -> None:
    runtime = st.session_state.get("stage_runtime")
    if not runtime:
        return
    for item in runtime.get("stages", {}).values():
        if item.get("status") == "running":
            item["status"] = "failed"
            return


def _render_workflow_review_slot(slot, auto_iterate: bool, max_iterations: int) -> None:
    if slot is None:
        return
    with slot.container():
        _show_workflow_overview(auto_iterate, max_iterations)


def _node_label(node_name: str) -> str:
    labels = {
        "paper_node": "Paper",
        "planner_node": "Planner",
        "human_approval_node": "Approval",
        "engineer_node": "Engineer",
        "manager_node": "Manager",
        "reviewer_node": "Reviewer",
        "prepare_next_iteration_node": "Prepare next iteration",
        "final_node": "Final",
    }
    return labels.get(node_name, node_name)


def _workflow_preflight(
    workspace: dict,
    paper_backend: str,
    planner_backend: str,
    engineer_executor: str,
    manager_backend: str,
    reviewer_backend: str,
    codex_executable_path: str,
    claude_executable_path: str = DEFAULT_CLAUDE_EXECUTABLE,
    engineer_execution_environment: str = "windows",
    wsl_distro: str = DEFAULT_WSL_DISTRO,
    wsl_cache_dir: str = DEFAULT_WSL_CACHE_DIR,
    final_writer_backend: str = DEFAULT_FINAL_WRITER_BACKEND,
) -> str:
    if paper_backend == "codex":
        return "Paper Codex backend is disabled. Use local paper preprocess, ai_reader, or claude_reader instead."
    planner_ready, planner_message = _planner_backend_ready(planner_backend)
    if not planner_ready:
        return f"Planner backend ready = false: {planner_message}"
    backends = (paper_backend, planner_backend, engineer_executor, manager_backend, reviewer_backend, final_writer_backend)
    if any(value in OPENCLAW_BACKENDS for value in backends):
        check = check_wsl(wsl_distro)
        if not check.available:
            return f"WSL unavailable for OpenClaw local embedded stages: {check.error}\n{check.hint}"
    if any(value in {"codex", "codex_review"} for value in backends):
        check = check_codex_cli(codex_executable_path)
        if not check.available:
            return (
                f"Codex CLI is not runnable: {check.attempted_executable}\n\n"
                f"{check.error}\n\n"
                f"{check.hint}\n\n"
                "R2A needs the same command to work in PowerShell as `codex --version` or `<your path> --version`."
            )
    # Check Claude CLI for claude, claude_review, and claude_reader backends
    if any(value in {"claude", "claude_review", "claude_reader"} for value in backends):
        check = check_claude_code_cli(claude_executable_path)
        if not check.available:
            return (
                f"Claude Code / Router CLI is not runnable: {check.attempted_executable}\n\n"
                f"{check.error}\n\n"
                f"{check.hint}\n\n"
                "R2A needs the same command to work in PowerShell as `ccr version`, `claude --version`, or the matching absolute path."
            )
        gateway = check_gateway_preflight(
            claude_executable_path,
            stages=_claude_stage_names(paper_backend, planner_backend, engineer_executor, manager_backend, reviewer_backend),
            preflight_required=True,
            auto_start=False,
        )
        if not gateway.get("ok"):
            errors = ", ".join(str(item) for item in gateway.get("errors", [])) or "GATEWAY_PREFLIGHT_FAILED"
            return (
                f"Gateway preflight failed: {errors}\n\n"
                f"Executable: {gateway.get('resolved_path') or gateway.get('gateway_executable')}\n"
                f"Gateway type: {gateway.get('gateway_type')}\n"
                f"Running: {gateway.get('gateway_running')}\n"
                f"Config: {gateway.get('config_source')}\n"
                f"Logs: {gateway.get('logs_dir')}\n\n"
                "Start/check the gateway explicitly, then rerun. R2A does not auto-start CCR by default."
            )
    if engineer_execution_environment == "wsl":
        check = check_wsl(wsl_distro)
        if not check.available:
            return (
                f"WSL is not runnable for distro `{wsl_distro}`.\n\n"
                f"{check.error}\n\n"
                f"{check.hint}\n\n"
                f"When WSL is available, R2A will keep caches outside WSL home: {wsl_cache_dir}"
            )
    return ""


def _show_codex_cli_check(codex_executable_path: str) -> None:
    check = check_codex_cli(codex_executable_path)
    st.session_state.codex_cli_check = check.to_dict()
    if check.available:
        st.success(f"Codex CLI is available: {check.attempted_executable}")
        if check.version_output:
            st.code(check.version_output)
        return
    st.error(f"Codex CLI is not available: {check.attempted_executable}")
    if check.error:
        st.code(check.error)
    st.warning(check.hint)


def _show_claude_cli_check(claude_executable_path: str) -> None:
    check = check_claude_code_cli(claude_executable_path)
    st.session_state.claude_cli_check = check.to_dict()
    if check.available:
        st.success(f"Claude Code / Router CLI is available: {check.attempted_executable}")
        if check.version_output:
            st.code(check.version_output)
        return
    st.error(f"Claude Code / Router CLI is not available: {check.attempted_executable}")
    if check.error:
        st.code(check.error)
    st.warning(check.hint)


def _show_gateway_check(claude_executable_path: str, stages: list[str], *, auto_start: bool = False) -> None:
    result = check_gateway_preflight(
        claude_executable_path,
        stages=stages,
        preflight_required=True,
        auto_start=auto_start,
    )
    summary = {key: value for key, value in result.items() if key not in {"status_output", "stage_policy_checks"}}
    if result.get("ok"):
        st.success("Gateway preflight passed.")
    else:
        st.error(f"Gateway preflight failed: {', '.join(result.get('errors', []))}")
    st.json(summary)
    if result.get("stage_policy_checks"):
        with st.expander("Stage policy checks", expanded=False):
            st.json(result["stage_policy_checks"])


def _claude_stage_names(
    paper_backend: str,
    planner_backend: str,
    engineer_executor: str,
    manager_backend: str,
    reviewer_backend: str,
) -> list[str]:
    stages = []
    if paper_backend == "claude_reader":
        stages.append("paper")
    if planner_backend == "claude":
        stages.append("planner")
    if engineer_executor in {"claude", "claude_code"}:
        stages.append("engineer")
    if manager_backend == "claude_review":
        stages.append("manager")
    if reviewer_backend == "claude":
        stages.append("reviewer")
    return stages


def _resolve_codex_input(codex_path: str) -> str:
    cleaned = codex_path.strip().strip('"')
    return cleaned or DEFAULT_CODEX_EXECUTABLE


def _resolve_claude_input(claude_path: str) -> str:
    cleaned = claude_path.strip().strip('"')
    return cleaned or DEFAULT_CLAUDE_EXECUTABLE


def _resolve_goal(guidance: str) -> str:
    cleaned = guidance.strip()
    if cleaned:
        return cleaned
    return DEFAULT_GOAL


def _parse_dataset_urls(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip()]


def _show_workflow_overview(auto_iterate: bool = DEFAULT_AUTO_ITERATE, max_iterations: int = DEFAULT_MAX_ITERATIONS_MINIMAL) -> None:
    repo_path = _repo_path()
    result = st.session_state.workflow_result or {}
    if st.session_state.workflow_error:
        st.error(st.session_state.workflow_error)

    # Only read final_report if run was created this session or explicitly loaded
    run_created_this_session = st.session_state.get("run_created_this_session", False)
    loaded_historical_run = st.session_state.get("loaded_historical_run", False)
    recovered_active_run = st.session_state.get("recovered_active_run", False)

    if result.get("final_report") or (run_created_this_session or loaded_historical_run or recovered_active_run):
        final_report = result.get("final_report") or (read_report(repo_path, "final") if repo_path else "")
    else:
        final_report = ""  # Don't auto-load latest FINAL_REPORT on fresh startup

    _show_final_status_card(final_report, repo_path=repo_path, result=result)

    # Show concise stage status bar on main UI
    _show_concise_stage_bar(repo_path, result)

    # P1: Show latest historical run summary if present (but not as main status card)
    _show_latest_historical_run_status(repo_path)

    st.subheader("阶段报告")
    _show_reports(default_key="final", include_history=False)

    if final_report and final_report != "Not generated yet.":
        summary = _final_summary_model(final_report, repo_path=repo_path)
        _show_l4_alignment_summary_card(summary["l4_alignment_summary"])
        _show_workflow_summary(summary)

    with st.expander("Advanced diagnostics / 高级诊断", expanded=False):
        _show_live_stage_activity(repo_path)
        _show_stage_status()
        if repo_path:
            _show_workflow_data_sources(repo_path)
        _show_iteration_control(auto_iterate, max_iterations)
        if repo_path:
            _show_manifest_summary(repo_path)
            _show_approval_diagnostics(repo_path, result)
            quick = _quick_report_summary(repo_path)
            if quick:
                st.caption("关键结论")
                st.write(quick)


def _show_final_status_card(final_report: str, repo_path: str | Path | None, result: dict) -> None:
    """Display the appropriate status card based on run state.

    Priority:
    1. Active run (running/stopping/force_killing/failed_to_kill) -> runtime status card
    2. Historical run (terminal status) -> historical status card
    3. Completed run with final artifacts -> final status card
    4. No run -> empty state
    """
    # P2: Check for active runtime status first
    record = _active_run_record(repo_path) if repo_path else {}
    record_status = str((record or {}).get("status", "") or "").lower()

    # If run is actively running/stopping, show runtime status card
    if record_status in {"running", "stopping", "force_killing", "failed_to_kill"}:
        _show_runtime_status_card(record)
        return

    # Build final status card for completed/historical runs
    card = _final_status_card_model(final_report, repo_path=repo_path, result=result)
    if not card:
        st.info("创建 workspace 并运行 workflow 后，这里会显示最终状态。")
        return

    # P1: Check if this is a historical run vs active/current run
    is_historical = _is_historical_run_status(repo_path, result)
    if is_historical:
        _show_historical_status_card(card)
        return

    status_text = (
        f"final_status: {card['final_status']} | "
        f"verdict: {card['final_verdict']} | "
        f"accepted: {card['accepted_level']} | observed: {card['observed_level']}"
    )
    if card["is_failure"]:
        st.error(status_text)
    elif "PASS" in card["final_verdict"].upper() or card["final_status"] == "completed_success":
        st.success(status_text)
    else:
        st.info(status_text)

    cols = st.columns(4)
    cols[0].metric("Final status", card["final_status"])
    cols[1].metric("Accepted level", card["accepted_level"])
    cols[2].metric("Observed level", card["observed_level"])
    cols[3].metric("Stop reason", card["stop_reason"])

    failure_bits = []
    if card["failed_stage"] != "-":
        failure_bits.append(f"failed_stage={card['failed_stage']}")
    if card["failure_category"] != "-":
        failure_bits.append(f"failure_category={card['failure_category']}")
    if card["is_failure"] and card["stop_reason"] != "-":
        failure_bits.append(f"stop_reason={card['stop_reason']}")
    if failure_bits:
        st.warning("Failure details: " + " | ".join(failure_bits))


def _final_status_card_model(final_report: str, repo_path: str | Path | None, result: dict | None = None) -> dict[str, str | bool]:
    result = result or {}
    summary = _final_summary_model(final_report, repo_path=repo_path) if final_report and final_report != "Not generated yet." else {}
    manifest = read_latest_manifest(repo_path) if repo_path else {}
    record = _active_run_record(repo_path) if repo_path else {}
    failure_summary = _workflow_failure_summary_model(record, manifest)

    # P1: Determine if we have an active/current run vs historical run
    has_active_run = _has_active_or_current_run(repo_path, result, record)

    # P1: If no active run and result is empty, don't show historical force_killed as current status
    if not has_active_run and not result:
        # Check if manifest/record have meaningful data for current run
        manifest_status = str((manifest or {}).get("status", "") or "").strip()
        record_status = str((record or {}).get("status", "") or "").strip()

        # If both are terminal historical statuses, return empty to show neutral state
        if manifest_status in {"force_killed", "cancelled", "stopped"} or record_status in {"force_killed", "cancelled", "stopped"}:
            # Only show if there's actual final_report content with verdict
            if not final_report or final_report == "Not generated yet.":
                return {}

    final_status = (
        str(summary.get("final_status", "") or "").strip()
        or str((manifest or {}).get("status", "") or "").strip()
        or str((record or {}).get("status", "") or "").strip()
        or str(result.get("loop_status", "") or result.get("status", "") or "").strip()
        or "-"
    )
    final_verdict = str(summary.get("final_verdict", "") or result.get("reviewer_verdict", "") or "-").strip() or "-"
    accepted_level = (
        str(summary.get("accepted_level", "") or "").strip()
        or str((manifest or {}).get("accepted_level", "") or (manifest or {}).get("achieved_level", "") or "").strip()
        or str(result.get("current_reproduction_level", "") or "").strip()
        or "-"
    )
    observed_level = (
        str(summary.get("observed_level", "") or "").strip()
        or str((manifest or {}).get("observed_level", "") or "").strip()
        or str(result.get("current_reproduction_level", "") or "").strip()
        or "-"
    )
    stop_reason = (
        str(summary.get("stop_reason", "") or "").strip()
        or str((manifest or {}).get("stop_reason", "") or (manifest or {}).get("termination_reason", "") or "").strip()
        or str((record or {}).get("termination_reason", "") or (record or {}).get("error_code", "") or "").strip()
        or "-"
    )
    failed_stage = (
        str(failure_summary.get("failed_stage", "") or "").strip()
        or str((manifest or {}).get("failed_stage", "") or "").strip()
        or str((record or {}).get("failed_stage", "") or "").strip()
        or "-"
    )
    failure_category = (
        str(failure_summary.get("failure_category", "") or "").strip()
        or str((manifest or {}).get("failure_category", "") or (manifest or {}).get("backend_failure_category", "") or "").strip()
        or str((record or {}).get("error_code", "") or "").strip()
        or "-"
    )
    is_failure = final_status in {"completed_with_failure", "failed", "force_killed", "stopped", "cancelled", "failed_to_kill"} or bool(failure_summary)
    if not any(value and value != "-" for value in (final_status, final_verdict, accepted_level, observed_level, stop_reason, failed_stage, failure_category)):
        return {}

    # P1: Don't show card if verdict is empty and it's a historical run
    if final_verdict == "-" and accepted_level == "-" and observed_level == "-":
        return {}

    return {
        "final_status": _compact_display(final_status, 80),
        "final_verdict": _compact_display(final_verdict, 80),
        "accepted_level": _compact_display(accepted_level, 80),
        "observed_level": _compact_display(observed_level, 80),
        "stop_reason": _compact_display(stop_reason, 80),
        "failed_stage": _compact_display(failed_stage, 80),
        "failure_category": _compact_display(failure_category, 80),
        "is_failure": is_failure,
    }


def _show_workflow_summary(summary: dict[str, str]) -> None:
    rows = {
        "Final verdict": summary.get("final_verdict", "-"),
        "Detailed status": summary.get("detailed_status", "-"),
        "Result type": summary.get("result_type", "-"),
        "Full reproduction claim": summary.get("full_reproduction_claim", "-"),
        "Target level": summary.get("target_level", "-"),
        "Cap reason": summary.get("cap_reason", "-"),
        "Next action": summary.get("next_action", "-"),
    }
    st.subheader("Workflow summary")
    st.table(pd.DataFrame([rows]).T.rename(columns={0: "Summary"}))


def _has_active_or_current_run(repo_path: str | Path | None, result: dict | None, record: dict | None) -> bool:
    """Check if there's an active run or a current run in progress.

    Returns True if:
    - There's a non-empty result from current workflow execution
    - There's an active run (running/stopping/force_killing status)
    - The session has workflow_running=True
    """
    if not repo_path:
        return bool(result)

    # Check session state for active run
    if st.session_state.get("workflow_running"):
        return True

    # Check if there's a result from current execution
    if result and (result.get("status") or result.get("loop_status") or result.get("final_report")):
        return True

    # Check record status
    if record:
        record_status = str(record.get("status", "") or "").lower()
        if record_status in {"running", "stopping", "force_killing", "failed_to_kill"}:
            return True

    # Check if there's an active_run_id in session
    active_run_id = st.session_state.get("active_run_id", "")
    if active_run_id and record:
        return True

    return False


def _is_historical_run_status(repo_path: str | Path | None, result: dict | None) -> bool:
    """Check if the status being shown is from a historical run, not current active run.

    A status is considered historical if:
    - There's no active run (workflow_running=False)
    - No result from current execution
    - The data comes from manifest/record of a previous run
    """
    if not repo_path:
        return False

    # If there's a current result, it's not historical
    if result and (result.get("status") or result.get("loop_status") or result.get("final_report")):
        return False

    # If workflow is currently running, it's not historical
    if st.session_state.get("workflow_running"):
        return False

    # Check if we have an active run
    record = _active_run_record(repo_path) if repo_path else {}
    if record:
        record_status = str(record.get("status", "") or "").lower()
        # If record is in active state, it's not historical
        if record_status in {"running", "stopping", "force_killing", "failed_to_kill"}:
            return False
        # If record is terminal and no result, it's historical
        if record_status in {"force_killed", "cancelled", "stopped", "completed", "completed_success", "completed_with_failure", "failed"}:
            return True

    # Check manifest
    manifest = read_latest_manifest(repo_path) if repo_path else {}
    if manifest:
        manifest_status = str(manifest.get("status", "") or "").lower()
        if manifest_status in {"force_killed", "cancelled", "stopped", "completed", "completed_success", "completed_with_failure", "failed"}:
            return True

    return False


def _show_runtime_status_card(record: dict) -> None:
    """Display runtime status for actively running workflows.

    Shows current runtime status when the workflow is actively running,
    without displaying final_status/accepted_level/observed_level/stop_reason
    which are semantic for completed runs only.
    """
    status = str(record.get("status", "-") or "-").lower()
    stage = str(record.get("current_stage", "-") or "-")
    run_id = str(record.get("run_id", "-") or "-")
    iteration = int(record.get("iteration", 1) or 1)
    stage_status = str(record.get("stage_status", "") or "").lower()

    # Display active run status
    st.info(f"当前 workflow 正在运行 | status: {status} | stage: {stage} | run_id: {run_id[-12:] if len(run_id) > 12 else run_id}")

    cols = st.columns(4)
    cols[0].metric("Status", status)
    cols[1].metric("Stage", stage)
    cols[2].metric("Iteration", iteration)
    cols[3].metric("Stage status", stage_status or "running")

    # Show warning if any
    warning = str(record.get("warning", "") or "")
    if warning:
        st.warning(warning)

    # Inform user that final conclusion is pending
    st.caption("最终结论将在 Reviewer / Final 阶段完成后生成。")


def _show_historical_status_card(card: dict[str, str | bool]) -> None:
    """Display a historical run status with clear labeling.

    Shows historical run status in a neutral/secondary style,
    clearly labeled as "历史运行状态" not current status.
    """
    st.caption("历史运行状态 / Historical run status")

    status_text = (
        f"final_status: {card['final_status']} | "
        f"verdict: {card['final_verdict']} | "
        f"accepted: {card['accepted_level']} | observed: {card['observed_level']}"
    )

    # Use warning/info for historical runs, not error even if failed
    if card["is_failure"]:
        st.warning(status_text)
    else:
        st.info(status_text)

    cols = st.columns(4)
    cols[0].metric("Final status", card["final_status"])
    cols[1].metric("Accepted level", card["accepted_level"])
    cols[2].metric("Observed level", card["observed_level"])
    cols[3].metric("Stop reason", card["stop_reason"])

    failure_bits = []
    if card["failed_stage"] != "-":
        failure_bits.append(f"failed_stage={card['failed_stage']}")
    if card["failure_category"] != "-":
        failure_bits.append(f"failure_category={card['failure_category']}")
    if card["is_failure"] and card["stop_reason"] != "-":
        failure_bits.append(f"stop_reason={card['stop_reason']}")
    if failure_bits:
        st.caption("Failure details: " + " | ".join(failure_bits))


def _show_latest_historical_run_status(repo_path: str | Path | None) -> None:
    """Show a brief summary of the latest historical run if no active run exists.

    This is displayed in a secondary area, not the main status card.
    Provides a button to explicitly load the historical run for viewing.
    """
    if not repo_path:
        return

    # Don't show if there's an active run
    if st.session_state.get("workflow_running"):
        return

    # Don't show if run was created this session
    run_created_this_session = st.session_state.get("run_created_this_session", False)
    if run_created_this_session:
        return

    # Check for latest historical run
    manifest = read_latest_manifest(repo_path) if repo_path else {}

    manifest_status = str((manifest or {}).get("status", "") or "").lower()

    # Check if it's a terminal historical status
    if not manifest_status or manifest_status in {"running", "stopping", "force_killing", "failed_to_kill", ""}:
        return

    # Show brief historical run info
    run_id = str((manifest or {}).get("run_id", "") or "")
    stop_reason = str((manifest or {}).get("stop_reason", "") or "-")
    current_stage = str((manifest or {}).get("current_stage", "") or "-")

    # Show for all terminal statuses
    with st.expander("最近一次运行 / Latest historical run", expanded=False):
        st.caption(f"Run ID: {run_id[-12:] if len(run_id) > 12 else run_id}")
        st.caption(f"Status: {manifest_status}")
        st.caption(f"Stage: {current_stage}")
        st.caption(f"Stop reason: {stop_reason}")

        if manifest_status == "force_killed":
            st.info("最近一次运行被中断。这并非当前运行状态。")
        elif manifest_status == "cancelled":
            st.info("最近一次运行被取消。")
        elif manifest_status == "stopped":
            st.info("最近一次运行被停止。")
        elif manifest_status == "completed_with_failure":
            st.warning("最近一次运行失败。点击下方按钮可查看详细报告。")
        elif manifest_status == "completed_success":
            st.success("最近一次运行成功。点击下方按钮可查看详细报告。")

        # Add button to load historical run
        if st.button("加载最近一次运行 / Load latest run", key="load_latest_historical_run"):
            st.session_state.active_run_id = run_id
            st.session_state.loaded_historical_run = True
            st.session_state.run_created_this_session = False
            st.session_state.recovered_active_run = False
            st.rerun()


def _show_web_runtime_header() -> None:
    registry = existing_server_status(Path(__file__).resolve())
    run_id = str(st.session_state.get("active_run_id", "") or "-")
    web_pid = registry.get("listener_pid") or registry.get("pid", "-")
    cols = st.columns(6)
    values = [
        ("Web PID", str(web_pid)),
        ("Web Port", str(registry.get("port", "-"))),
        ("Started At", str(registry.get("started_at", "-"))),
        ("Build", str(registry.get("build_version", "local"))),
        ("Run ID", run_id),
        ("Registry", str(web_registry_path())),
    ]
    for col, (label, value) in zip(cols, values):
        col.metric(label, _compact_display(value, 42))
    workspace = st.session_state.get("workspace") or {}
    repo_path = workspace.get("repo_path")
    if repo_path and run_id != "-":
        record = read_run_record(repo_path, run_id)
        if record:
            stage_model = _active_current_stage_model(repo_path, run_id=run_id, record=record)
            cols2 = st.columns(6)
            details = [
                ("Stage", stage_model.get("stage", "-")),
                ("Stage Status", record.get("stage_status", record.get("status", "-"))),
                ("Heartbeat", record.get("heartbeat_at", "-")),
                ("Backend", record.get("backend", "-")),
                ("Fallback", record.get("fallback_used", "-")),
                ("Source", stage_model.get("source", "-")),
            ]
            for col, (label, value) in zip(cols2, details):
                col.metric(label, _compact_display(str(value), 42))
            if stage_model.get("warning"):
                st.warning(stage_model["warning"])


def _show_runtime_recovery_notice() -> None:
    recovery = st.session_state.get("runtime_recovery", {})
    if not isinstance(recovery, dict) or not recovery.get("recovered"):
        return
    message = str(recovery.get("message", "") or "Recovered run from runtime record.")
    run_id = str(recovery.get("selected_run_id", "") or "-")
    status = str(recovery.get("selected_status", "") or "-")
    count = int(recovery.get("active_candidate_count", 0) or 0)
    suffix = f" run_id={run_id}, status={status}"
    if count > 1:
        st.warning(f"{message}{suffix}. Multiple active runtime runs were found; the most recently updated run was selected.")
    else:
        st.info(f"{message}{suffix}")


def _show_live_stage_activity(repo_path: str | None) -> None:
    if not repo_path:
        st.info("实时阶段动态会在创建 workspace 后显示。")
        return
    activity = _latest_stage_activity(repo_path)
    status_text = activity.get("status_text", "Waiting")
    detail = activity.get("detail", "")
    if activity.get("level") == "running":
        st.info(f"当前阶段：{status_text}" + (f" | {detail}" if detail else ""))
    elif activity.get("level") == "done":
        st.success(f"当前阶段：{status_text}" + (f" | {detail}" if detail else ""))
    elif activity.get("level") == "warning":
        st.warning(f"当前阶段：{status_text}" + (f" | {detail}" if detail else ""))
    else:
        st.caption(f"当前阶段：{status_text}" + (f" | {detail}" if detail else ""))


def _latest_stage_activity(repo_path: str | Path) -> dict[str, str]:
    repo = Path(repo_path)
    r2a_dir = repo / ".r2a"
    run_activity = _active_run_activity(repo)
    if run_activity:
        return run_activity
    manifest = read_latest_manifest(repo)
    if manifest:
        stage = str(manifest.get("current_stage", "") or "workflow")
        stages = manifest.get("stages", {}) if isinstance(manifest.get("stages"), dict) else {}
        stage_status = ""
        if isinstance(stages.get(stage), dict):
            stage_status = str(stages[stage].get("status", "") or "")
        overall_status = str(manifest.get("status", "") or stage_status or "RUNNING")
        level = "done" if overall_status in {"PASS", "PASS_WITH_LIMITATIONS", "completed"} or stage == "final" else "running"
        if overall_status in {"FAIL", "REJECT"}:
            level = "warning"
        detail = str(manifest.get("summary", "") or manifest.get("achieved_label", "") or "")
        return {"level": level, "status_text": f"{stage.title()} {stage_status or overall_status}", "detail": detail}
    result_files = _latest_files(r2a_dir / "results")
    log_files = _latest_files(r2a_dir / "logs")
    report_stages = [
        ("Final", r2a_dir / "FINAL_REPORT.md"),
        ("Reviewer", r2a_dir / "REVIEW_REPORT.md"),
        ("Manager", r2a_dir / "CHECK_REPORT.md"),
        ("Engineer", r2a_dir / "EXECUTION_REPORT.md"),
        ("Planner", r2a_dir / "TASK_SPEC.md"),
        ("Paper", r2a_dir / "PAPER_CONTEXT.md"),
    ]
    latest_report = next(((label, path) for label, path in report_stages if path.exists()), None)
    latest_file = max([*result_files, *log_files], key=lambda path: path.stat().st_mtime, default=None)
    done_path = r2a_dir / "results" / "ENGINEER_DONE.txt"
    if not (r2a_dir / "FINAL_REPORT.md").exists() and _engineer_activity_running(repo):
        detail = _relative_label(latest_file, repo) if latest_file else "waiting for first Engineer artifact"
        return {"level": "running", "status_text": "Engineer running", "detail": detail}
    if latest_report:
        label, path = latest_report
        detail = _relative_label(latest_file, repo) if latest_file else _relative_label(path, repo)
        level = "done" if label == "Final" else "running"
        return {"level": level, "status_text": f"{label} updated", "detail": detail}
    if done_path.exists():
        return {"level": "done", "status_text": "Engineer finished", "detail": _relative_label(done_path, repo)}
    if latest_file:
        return {"level": "running", "status_text": "Artifact updating", "detail": _relative_label(latest_file, repo)}
    return {"level": "idle", "status_text": "Waiting", "detail": ""}


def _active_run_record(repo_path: str | Path) -> dict:
    """Get the active run record.

    Only returns a record if:
    1. There's an active_run_id in session state AND
    2. The run was created this session OR user explicitly loaded a historical run

    This prevents auto-loading old runs on fresh startup.
    """
    if st is None:
        return {}
    run_id = st.session_state.get("active_run_id", "")
    # Only return record if run was created this session or explicitly loaded
    run_created_this_session = st.session_state.get("run_created_this_session", False)
    loaded_historical_run = st.session_state.get("loaded_historical_run", False)
    recovered_active_run = st.session_state.get("recovered_active_run", False)

    if run_id and (run_created_this_session or loaded_historical_run or recovered_active_run):
        return read_run_record(repo_path, run_id)
    return {}


def _active_current_stage_model(
    repo_path: str | Path,
    *,
    run_id: str = "",
    record: dict | None = None,
    manifest: dict | None = None,
    iteration_state: dict | None = None,
) -> dict[str, str]:
    repo = Path(repo_path)
    selected_run_id = run_id or latest_run_id(repo)
    live_record = record if record is not None else (read_run_record(repo, selected_run_id) if selected_run_id else {})
    latest_manifest = manifest if manifest is not None else read_latest_manifest(repo)
    iter_state = iteration_state if iteration_state is not None else _read_json_file(repo / ".r2a" / "ITERATION_STATE.json")

    registry_stage = str((live_record or {}).get("current_stage", "") or "")
    registry_status = str((live_record or {}).get("status", "") or "").lower()
    manifest_stage = str((latest_manifest or {}).get("current_stage", "") or "")
    manifest_status = str((latest_manifest or {}).get("status", "") or "")
    iteration_stage = str((iter_state or {}).get("current_stage", "") or "")
    warning = ""

    registry_active = registry_status in {"running", "stopping", "force_killing", "failed_to_kill"} and not bool((live_record or {}).get("stale_active_run"))
    if registry_active and registry_stage:
        if manifest_stage and manifest_stage != registry_stage:
            warning = f"Runtime registry stage `{registry_stage}` differs from latest RUN_MANIFEST `{manifest_stage}`."
        return {
            "stage": registry_stage,
            "source": "registry",
            "status": registry_status,
            "warning": warning,
            "registry_stage": registry_stage,
            "manifest_stage": manifest_stage,
            "iteration_stage": iteration_stage,
        }

    if manifest_stage:
        if registry_stage and registry_stage != manifest_stage:
            stale_note = "stale " if (live_record or {}).get("stale_active_run") else ""
            warning = f"{stale_note}Runtime registry stage `{registry_stage}` differs from latest RUN_MANIFEST `{manifest_stage}`; manifest is shown."
        return {
            "stage": manifest_stage,
            "source": "latest_manifest",
            "status": manifest_status,
            "warning": warning,
            "registry_stage": registry_stage,
            "manifest_stage": manifest_stage,
            "iteration_stage": iteration_stage,
        }

    if iteration_stage:
        return {
            "stage": iteration_stage,
            "source": "iteration_state",
            "status": "",
            "warning": "",
            "registry_stage": registry_stage,
            "manifest_stage": manifest_stage,
            "iteration_stage": iteration_stage,
        }

    return {
        "stage": registry_stage or "-",
        "source": "registry" if registry_stage else "unknown",
        "status": registry_status,
        "warning": "",
        "registry_stage": registry_stage,
        "manifest_stage": manifest_stage,
        "iteration_stage": iteration_stage,
    }


def _active_run_activity(repo_path: str | Path) -> dict[str, str]:
    record = _active_run_record(repo_path)
    if not record:
        return {}
    manifest = read_latest_manifest(repo_path)
    stage_model = _active_current_stage_model(repo_path, record=record, manifest=manifest)
    status = str(record.get("status", "") or "")
    stage = str(stage_model.get("stage", "") or record.get("current_stage", "") or "workflow")
    detail = str(record.get("error_code", "") or record.get("termination_reason", "") or "")
    source = str(stage_model.get("source", "") or "")
    warning = str(stage_model.get("warning", "") or "")
    if source:
        detail = f"source={source}" + (f" | {detail}" if detail else "")
    if warning:
        detail = f"{detail} | {warning}" if detail else warning
    if status in {"running", "stopping", "force_killing", "failed_to_kill"}:
        return {"level": "running", "status_text": f"{stage.title()} {status}", "detail": detail}
    if status in {"failed", "completed_with_failure", "force_killed", "stopped", "cancelled"}:
        return {"level": "warning", "status_text": f"{stage.title()} {status}", "detail": detail}
    if status == "completed_success":
        return {"level": "done", "status_text": f"{stage.title()} {status}", "detail": detail}
    return {}


def _latest_files(directory: Path) -> list[Path]:
    if not directory.exists():
        return []
    return sorted((path for path in directory.rglob("*") if path.is_file()), key=lambda path: path.stat().st_mtime, reverse=True)[:8]


def _engineer_activity_running(repo: Path) -> bool:
    markers = (
        repo / ".r2a" / "logs" / "claude_engineer_prompt.md",
        repo / ".r2a" / "logs" / "claude_stdout.log",
        repo / ".r2a" / "logs" / "claude_stderr.log",
        repo / ".r2a" / "results" / "engineer_progress.json",
    )
    return any(path.exists() for path in markers) and not (repo / ".r2a" / "results" / "ENGINEER_DONE.txt").exists()


def _show_final_summary(final_report: str, repo_path: str | Path | None = None) -> None:
    summary = _final_summary_model(final_report, repo_path=repo_path)
    final_status = summary["final_status"]
    total_iterations = summary["total_iterations"]
    stop_reason = summary["stop_reason"]
    final_verdict = summary["final_verdict"]
    accepted_level = summary["accepted_level"]
    observed_level = summary["observed_level"]
    target_level = summary["target_level"]
    cap_reason = summary["cap_reason"]
    claim = summary["claim"]

    tone_text = f"{final_verdict} | accepted: {accepted_level} | observed: {observed_level}"
    verdict_upper = final_verdict.upper()
    if verdict_upper == "PASS":
        st.success(tone_text)
    elif "PASS" in verdict_upper:
        st.warning(tone_text)
    elif "FAIL" in verdict_upper or "REJECT" in verdict_upper:
        st.error(tone_text)
    else:
        st.info(tone_text)

    executive = re_search_heading(final_report, "Executive Summary")
    if executive:
        st.subheader("Executive Summary")
        st.markdown(executive)
    if cap_reason and cap_reason not in {"-", "None"}:
        cap_text = f"Accepted level capped. Cap reason: {cap_reason}"
        if "MANAGER" in cap_reason.upper() and "FAIL" in cap_reason.upper():
            st.error(f"Manager FAIL. {cap_text}")
        else:
            st.warning(cap_text)

    cols = st.columns(6)
    cols[0].metric("Status", final_status)
    cols[1].metric("Verdict", final_verdict)
    cols[2].metric("Iterations", total_iterations)
    cols[3].metric("Stop reason", stop_reason)
    cols[4].metric("Accepted level", accepted_level)
    cols[5].metric("Target level", target_level)

    level_rows = {
        "Accepted Level After Quality Gates": accepted_level,
        "Observed Evidence Level": observed_level,
        "Target Level": target_level,
        "Cap Reason": cap_reason,
        "Result Type": summary["result_type"],
        "Full Reproduction Claim": summary["full_reproduction_claim"],
        "L4 Alignment Summary": summary["l4_alignment_summary"],
        "Next Action": summary["next_action"],
    }
    st.table(pd.DataFrame([level_rows]).T.rename(columns={0: "Summary"}))
    _show_l4_alignment_summary_card(summary["l4_alignment_summary"])

    for heading, expanded in (
        ("Progress Cards", True),
        ("Experiment Summary", True),
        ("Paper Alignment Summary", True),
        ("Remaining Issues", True),
        ("What Was Actually Done", False),
        ("Provenance", False),
    ):
        body = re_search_heading(final_report, heading)
        if body:
            with st.expander(heading, expanded=expanded):
                st.markdown(body)

    evidence = re_search_heading(final_report, "Evidence Level Checks")
    if evidence:
        with st.expander("Evidence checks", expanded=False):
            st.markdown(evidence)
    with st.expander("Full final report", expanded=False):
        st.markdown(final_report)


def _show_manifest_summary(repo_path: str | Path) -> None:
    manifest = read_latest_manifest(repo_path)
    if not manifest:
        return
    target = manifest.get("target_label") or manifest.get("target_level", "-")
    accepted = manifest.get("achieved_label") or manifest.get("achieved_level", "-")
    status = str(manifest.get("status", "-"))
    summary = str(manifest.get("summary", ""))
    decision = manifest.get("decision_status") if isinstance(manifest.get("decision_status"), dict) else {}
    manager_status = str(manifest.get("manager_status", "") or "").upper()
    capped = manager_status == "FAIL" or bool(manifest.get("blocking_reasons"))
    if decision:
        typed = str(decision.get("typed_decision", "") or "-")
        reason = str(decision.get("reason_code", "") or "-")
        requires_input = "yes" if decision.get("requires_user_input") else "no"
        st.caption(f"Decision: {typed} | Reason: {reason} | Requires user input: {requires_input}")
        blockers = decision.get("active_blockers", []) if isinstance(decision.get("active_blockers"), list) else []
        if blockers:
            with st.expander("Active blockers", expanded=True):
                for blocker in blockers:
                    if isinstance(blocker, dict):
                        st.write(f"- {blocker.get('reason_code', '')}: {blocker.get('last_message', '')}")
    if status == "PASS":
        st.success(f"Accepted level：{accepted} / Target：{target}")
    elif "PASS" in status:
        st.warning(f"Accepted level：{accepted} / Target：{target}")
    elif status in {"FAIL", "REJECT"}:
        st.error(f"Accepted level：{accepted} / Target：{target}")
    else:
        st.info(f"Accepted level：{accepted} / Target：{target}")
    if capped:
        st.warning("Accepted level capped by quality gates. See blocking reasons below.")
    if manager_status == "FAIL":
        st.error("Manager FAIL")
    if summary:
        st.caption(summary)
    source_summary = _source_status_summary(repo_path)
    if source_summary:
        st.caption(source_summary)
    if _manifest_has_planner_failure(manifest):
        st.warning("Workflow stopped before evidence evaluation because Planner did not commit required outputs.")
        return
    evidence = manifest.get("evidence", {})
    if isinstance(evidence, dict) and evidence:
        rows = []
        for key, item in evidence.items():
            if isinstance(item, dict):
                rows.append(
                    {
                        "Level": item.get("label", key),
                        "Status": item.get("status", "-"),
                        "Reason": item.get("reason", ""),
                    }
                )
        if rows:
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    blockers = manifest.get("blocking_reasons", [])
    if blockers:
        with st.expander("阻断原因", expanded=True):
            for item in blockers:
                st.write(f"- {item}")


def _final_summary_model(final_report: str, repo_path: str | Path | None = None) -> dict[str, str]:
    current = _extract_list_value(final_report, "Reproduction Level", "Current") or "-"
    accepted = _extract_list_value(final_report, "Reproduction Level", "Accepted Level After Quality Gates") or current
    observed = _extract_list_value(final_report, "Reproduction Level", "Observed Evidence Level") or current
    l4_summary = _existing_l4_alignment_summary(repo_path) or _extract_provenance_value(final_report, "L4_ALIGNMENT_SUMMARY.md") or "L4 alignment package not available yet."
    return {
        "final_status": _extract_markdown_section_value(final_report, "Final Status") or "-",
        "total_iterations": _extract_markdown_section_value(final_report, "Total Iterations") or "-",
        "stop_reason": _extract_markdown_section_value(final_report, "Stop Reason") or "-",
        "final_verdict": _extract_markdown_section_value(final_report, "Final Verdict") or "-",
        "detailed_status": _extract_markdown_section_value(final_report, "Detailed Status") or "-",
        "current_level": _display_level_value(current),
        "accepted_level": _display_level_value(accepted),
        "observed_level": _display_level_value(observed),
        "cap_reason": _extract_list_value(final_report, "Reproduction Level", "Cap Reason") or "-",
        "target_level": _extract_list_value(final_report, "Reproduction Level", "Target") or "-",
        "result_type": _extract_list_value(final_report, "Reproduction Level", "Result Type") or "-",
        "full_reproduction_claim": _extract_list_value(final_report, "Reproduction Level", "Full Reproduction Claim") or "-",
        "claim": _extract_list_value(final_report, "Reproduction Level", "Claim") or "-",
        "next_action": _extract_list_value(final_report, "Reproduction Level", "Next Action") or "-",
        "l4_alignment_summary": l4_summary,
    }


def _display_level_value(value: str) -> str:
    text = str(value or "").strip()
    if text.upper() == "UNASSESSED":
        return "未正式接受 (UNASSESSED)"
    return text or "-"


def _existing_l4_alignment_summary(repo_path: str | Path | None) -> str:
    if not repo_path:
        return ""
    path = Path(repo_path) / ".r2a" / "results" / "L4_ALIGNMENT_SUMMARY.md"
    return str(path) if path.exists() else ""


def _show_approval_diagnostics(repo_path: str | Path, result: dict) -> None:
    diagnostics = _approval_diagnostics_model(repo_path, result)
    if not diagnostics:
        return
    st.caption("Planner / Approval diagnostics")
    st.table(pd.DataFrame([diagnostics]).T.rename(columns={0: "Value"}))
    if diagnostics.get("Planner validation passed") == "no" or diagnostics.get("Planner committed") == "no":
        st.warning("Planner outputs were not committed; inspect planner_transaction.json before treating this as an Engineer failure.")
    elif diagnostics.get("Approval passed") == "no":
        st.warning(f"Engineer skipped due to approval gate: {diagnostics.get('Approval rejected reason')}")
    elif diagnostics.get("Is Claude/CCR call problem") == "yes":
        st.warning("Planner failure is classified as a Claude/CCR backend call problem, not a paper reproduction result.")


def _approval_diagnostics_model(repo_path: str | Path, result: dict) -> dict[str, str]:
    repo = Path(repo_path)
    tx_path = repo / ".r2a" / "logs" / "planner_transaction.json"
    data: dict = {}
    diagnostic: dict = {}
    if tx_path.exists():
        try:
            data = json.loads(tx_path.read_text(encoding="utf-8", errors="replace"))
            diagnostic = dict(data.get("diagnostic", {}) or {})
        except (OSError, json.JSONDecodeError):
            diagnostic = {"failure_category": "PLANNER_TRANSACTION_METADATA_PARSE_FAILED", "failure_reason": str(tx_path)}
    has_v2_outputs = (
        (repo / ".r2a" / "PLANNER_OUTPUT.json").exists()
        and (repo / ".r2a" / "TASK_SPEC.md").exists()
        and (repo / ".r2a" / "EXPERIMENT_CONTRACT.md").exists()
    )
    has_legacy_outputs = (repo / ".r2a" / "TASK_SPEC.md").exists() and (repo / ".r2a" / "EXPERIMENT_CONTRACT.md").exists()
    has_committed_outputs = has_v2_outputs or has_legacy_outputs
    approved = bool(result.get("approved") or diagnostic.get("approval_passed"))
    stopped = bool(result.get("stopped"))
    stop_reason = str(result.get("stop_reason", "") or "")
    planner_validation = diagnostic.get("planner_validation_passed")
    if planner_validation is None and data:
        planner_validation = data.get("validation_status") == "PASS"
    planner_committed = diagnostic.get("planner_committed")
    if planner_committed is None:
        planner_committed = data.get("committed") if data else has_committed_outputs
    staging_created = bool(data.get("staging_dir")) if data else False
    candidate_files_present = (
        bool(diagnostic.get("staging_planner_output_written") or diagnostic.get("staging_task_spec_written"))
        and bool(diagnostic.get("staging_task_spec_written"))
        and bool(diagnostic.get("staging_experiment_contract_written"))
    )
    approval_ready = bool(staging_created and candidate_files_present and planner_validation and planner_committed and has_committed_outputs)
    failure_category = str(diagnostic.get("failure_category", data.get("failure_category", "")) or "none")
    failure_reason = str(diagnostic.get("failure_reason", "") or _planner_transaction_issue_text(data) or "none")
    approval_reason = ""
    if approved:
        approval_reason = ""
    elif not approval_ready:
        approval_reason = f"Planner not ready for approval: {failure_category}; {failure_reason}"
    else:
        approval_reason = stop_reason or "waiting for manual approval or approval rejected"
    return {
        "Approval ready": _web_yes_no(approval_ready),
        "Planner backend": str(diagnostic.get("planner_backend", data.get("planner_backend", "")) or "unknown"),
        "Planner status": str(diagnostic.get("planner_status", "success" if planner_validation else "failed")),
        "Planner schema version": str(diagnostic.get("planner_schema_version", data.get("schema_version", "")) or "unknown"),
        "Planning mode": str(diagnostic.get("planning_mode", "unknown") or "unknown"),
        "Iteration strategy": str(diagnostic.get("iteration_strategy", "unknown") or "unknown"),
        "Contract mode": str(diagnostic.get("contract_mode", data.get("contract_mode_after_validation", "")) or "unknown"),
        "Staging created": _web_yes_no(staging_created),
        "Candidate files present": _web_yes_no(candidate_files_present),
        "Planner validation passed": _web_yes_no(planner_validation),
        "Planner committed": _web_yes_no(planner_committed),
        "Formal outputs exist": _web_yes_no(has_committed_outputs),
        "Approval passed": _web_yes_no(approved),
        "Approval rejected reason": approval_reason,
        "Is Claude/CCR call problem": _web_yes_no(diagnostic.get("is_claude_ccr_call_problem")),
        "Failure category": failure_category,
        "Failure reason": failure_reason,
        "Engineer skipped reason": (
            f"Skipped because Planner was not ready: {failure_category}"
            if not approval_ready
            else ("Skipped due to approval gate" if stopped and not approved else "not skipped by approval gate")
        ),
    }


def _source_status_summary(repo_path: str | Path) -> str:
    repo = Path(repo_path)
    acquisition = _read_json_file(report_path(repo, "source_acquisition"))
    inspection = _read_json_file(report_path(repo, "source_inspection"))
    if not acquisition and not inspection:
        return ""
    source_status = acquisition.get("source_status", "-") if isinstance(acquisition, dict) else "-"
    source_type = acquisition.get("source_type", "-") if isinstance(acquisition, dict) else "-"
    inspection_status = inspection.get("inspection_status", "-") if isinstance(inspection, dict) else "-"
    iteration = _read_json_file(repo / ".r2a" / "ITERATION_STATE.json").get("current_iteration", "-")
    max_iterations = _read_json_file(repo / ".r2a" / "ITERATION_STATE.json").get("max_iterations", "-")
    return f"Source: {source_status} ({source_type}) | Inspection: {inspection_status} | Iteration: {iteration}/{max_iterations}"


def _planner_transaction_issue_text(data: dict) -> str:
    issues = data.get("issues", []) if isinstance(data, dict) else []
    if issues:
        return "; ".join(str(item) for item in issues[:3])
    return ""


def _web_yes_no(value) -> str:
    if value is True:
        return "yes"
    if value is False:
        return "no"
    return "unknown"


def _show_l4_alignment_summary_card(summary_path: str) -> None:
    if not summary_path or summary_path == "L4 alignment package not available yet.":
        st.info("L4 alignment package not available yet.")
        return
    path = Path(summary_path)
    if not path.exists():
        st.warning(f"L4 alignment summary was referenced but is not readable: `{summary_path}`")
        return
    st.info(
        "L4 对齐证据包：用于说明本次 reduced run 与论文设置之间哪些匹配、部分匹配、缺失。"
        "它支撑 L4_reduced_paper_aligned 判定，但不是最终报告，也不替代 Reviewer verdict。"
    )
    st.caption(f"L4 alignment package path: `{path}`")
    text = path.read_text(encoding="utf-8", errors="replace")
    preview = _first_markdown_sections(text, max_sections=5)
    with st.expander("L4 alignment summary preview / L4 对齐摘要预览", expanded=False):
        st.markdown(preview)


def _first_markdown_sections(text: str, max_sections: int = 5) -> str:
    lines = []
    headings_seen = 0
    for line in text.splitlines():
        if line.startswith("## "):
            headings_seen += 1
            if headings_seen > max_sections:
                break
        lines.append(line)
    return "\n".join(lines).strip() or text[:3000]


def _show_iteration_control(auto_iterate: bool, max_iterations: int) -> None:
    repo_path = _repo_path()
    history = read_iteration_history(repo_path) if repo_path else {}
    cols = st.columns(4)
    cols[0].metric("Auto iterate", "On" if auto_iterate else "Off")
    cols[1].metric("Max iterations", max_iterations)
    cols[2].metric("Current iteration", history.get("current_iteration", "-"))
    cols[3].metric("Stop reason", history.get("stop_reason", "-") or "-")


def _show_stage_status() -> None:
    repo_path = _repo_path()
    result = st.session_state.workflow_result or {}
    manifest = read_latest_manifest(repo_path) if repo_path else {}
    record = _active_run_record(repo_path) if repo_path else {}
    stage_model = _active_current_stage_model(repo_path, record=record, manifest=manifest) if repo_path else {}
    runtime = st.session_state.get("stage_runtime") if st.session_state.get("workflow_running") else None
    stages = WORKFLOW_STAGE_ORDER
    if stage_model.get("warning"):
        st.warning(stage_model["warning"])

    failure_summary = _workflow_failure_summary_model(record, manifest)
    if failure_summary:
        st.warning(_workflow_failure_summary_text(failure_summary))

    st.caption("Live Runtime Status")
    live_summary = _live_runtime_status_model(record)
    if live_summary:
        st.table(pd.DataFrame([live_summary]).T.rename(columns={0: "Value"}))
        live_statuses = {stage: (_stage_status_from_run_record(stage, record), None) for stage, _label in stages}
        _show_stage_chip_row(stages, live_statuses, "Live stage chips source: runtime record / active_run.json")
    elif runtime:
        runtime_statuses = {}
        for stage, _label in stages:
            info = runtime.get("stages", {}).get(stage, {})
            runtime_statuses[stage] = (str(info.get("status", "pending")).title(), info.get("iteration"))
        _show_stage_chip_row(stages, runtime_statuses, "Live stage chips source: in-memory stage runtime")
    else:
        st.caption("No active runtime record is currently selected.")

    if manifest:
        st.caption("Artifact Summary")
        artifact_summary = _artifact_summary_model(manifest)
        if artifact_summary:
            st.table(pd.DataFrame([artifact_summary]).T.rename(columns={0: "Value"}))
        artifact_statuses = {stage: (_stage_status_from_manifest(stage, manifest) or "Pending", None) for stage, _label in stages}
        _show_stage_chip_row(stages, artifact_statuses, "Artifact stage chips source: RUN_MANIFEST / FINAL_DECISION / REVIEW_VERDICT")
    elif stage_model.get("source") == "iteration_state":
        iteration_statuses = {stage: (_stage_status_from_iteration_state(stage, stage_model), None) for stage, _label in stages}
        _show_stage_chip_row(stages, iteration_statuses, "Iteration stage chips source: ITERATION_STATE.json")
    elif not record and not runtime:
        fallback_statuses = {}
        for stage, _label in stages:
            key = _stage_report_key(stage)
            fallback_statuses[stage] = (_stage_status(repo_path, key, result), None)
        _show_stage_chip_row(stages, fallback_statuses, "Artifact stage chips source: report files")


def _normalize_stage_status_for_ui(stage_status: str) -> str:
    """Normalize stage status for UI display.

    Maps various status labels to canonical UI states:
    - Done: PASS, SUCCESS, APPROVED, OK, DONE, COMPLETED, COMPLETED_SUCCESS
    - Failed: FAIL, FAILED, FAILURE, ERROR, COMPLETED_WITH_FAILURE,
              REVIEWER_INVALID_VERDICT, REVIEWER_FEEDBACK_VALIDATION_FAILED
    - Needs Fix: NEEDS_FIX
    - Needs Input: NEEDS_INPUT_OR_BUDGET, NEEDS_OFFICIAL_INPUT
    - Input Contract Ready: INPUT_CONTRACT_READY and related input-ready tokens
    - Running: RUNNING, IN_PROGRESS
    - Pending: PENDING, WAITING
    - Skipped: SKIPPED, OMITTED
    - Unknown: anything else
    """
    status = str(stage_status or "").strip().upper()

    if status in {"PASS", "SUCCESS", "APPROVED", "OK", "DONE", "COMPLETED", "COMPLETED_SUCCESS"}:
        return "Done"

    if status in {
        "FAIL",
        "FAILED",
        "FAILURE",
        "ERROR",
        "COMPLETED_WITH_FAILURE",
        "REVIEWER_INVALID_VERDICT",
        "REVIEWER_FEEDBACK_VALIDATION_FAILED",
        "REVIEWER_SAFETY_VALIDATION_FAILED",
        "REVIEWER_INPUT_INTEGRITY_BLOCKED_L3",
    }:
        return "Failed"

    if status == "NEEDS_FIX":
        return "Needs Fix"

    if status in {"NEEDS_INPUT_OR_BUDGET", "NEEDS_OFFICIAL_INPUT", "NEEDS_INPUT"}:
        return "Needs Input"

    if status in {"INPUT_CONTRACT_READY", "INPUT_READY", "CONTRACT_READY", "REVIEW_INPUT_READY"}:
        return "Input Contract Ready"

    if status in {"RUNNING", "IN_PROGRESS"}:
        return "Running"

    if status in {"PENDING", "WAITING"}:
        return "Pending"

    if status in {"SKIPPED", "OMITTED"}:
        return "Skipped"

    return "Unknown"


def _concise_stage_statuses_from_sources(
    *,
    runtime: dict | None = None,
    manifest: dict | None = None,
    record: dict | None = None,
    result: dict | None = None,
) -> dict[str, str]:
    stages = WORKFLOW_STAGE_ORDER
    statuses: dict[str, str] = {}
    runtime = runtime if isinstance(runtime, dict) else {}
    manifest = manifest if isinstance(manifest, dict) else {}
    record = record if isinstance(record, dict) else {}
    result = result if isinstance(result, dict) else {}

    if runtime and isinstance(runtime.get("stages"), dict):
        for stage, _label in stages:
            info = runtime.get("stages", {}).get(stage, {})
            raw_status = str(info.get("status", "pending") if isinstance(info, dict) else "pending")
            statuses[stage] = _normalize_stage_status_for_ui(raw_status)
    elif manifest and isinstance(manifest.get("stages"), dict):
        for stage, _label in stages:
            statuses[stage] = _stage_status_from_manifest(stage, manifest) or "Pending"
    elif record:
        statuses = _concise_stage_statuses_from_record(record)
    else:
        statuses = {stage: "-" for stage, _label in stages}

    current_stage = (
        str(result.get("current_stage", "") or "")
        or str(record.get("current_stage", "") or "")
        or str(manifest.get("current_stage", "") or "")
    )
    statuses = _adjust_concise_statuses_for_current_stage(statuses, current_stage)
    if _workflow_failed_for_concise_bar(manifest=manifest, record=record, result=result):
        statuses = dict(statuses)
        statuses["final"] = "Failed"
    return statuses


def _workflow_failed_for_concise_bar(
    *,
    manifest: dict | None = None,
    record: dict | None = None,
    result: dict | None = None,
) -> bool:
    manifest = manifest if isinstance(manifest, dict) else {}
    record = record if isinstance(record, dict) else {}
    result = result if isinstance(result, dict) else {}
    decision_status = manifest.get("decision_status", {})
    if not isinstance(decision_status, dict):
        decision_status = {}
    final_decision = manifest.get("final_decision", {})
    if not isinstance(final_decision, dict):
        final_decision = {}
    failure_statuses = {
        "cancelled",
        "canceled",
        "completed_with_failure",
        "failed",
        "failed_to_kill",
        "force_killed",
        "stopped",
        "terminal_failed",
    }
    candidates = [
        result.get("final_status"),
        result.get("loop_status"),
        result.get("status"),
        manifest.get("final_status"),
        manifest.get("loop_status"),
        manifest.get("status"),
        decision_status.get("typed_decision"),
        decision_status.get("reason_code") if decision_status.get("typed_decision") == "terminal_failed" else "",
        final_decision.get("final_status"),
        record.get("status"),
    ]
    return any(str(item or "").strip().lower() in failure_statuses for item in candidates)


def _concise_stage_statuses_from_record(record: dict) -> dict[str, str]:
    current_stage = _display_stage_from_current_stage(record.get("current_stage", ""))
    record_status = str(record.get("status", "") or "").lower()
    if current_stage == "prepare_next_iteration":
        return _prepare_next_iteration_concise_statuses()

    statuses: dict[str, str] = {}
    terminal = record_status in {
        "cancelled",
        "completed",
        "completed_success",
        "completed_with_failure",
        "failed",
        "failed_to_kill",
        "force_killed",
        "stopped",
        "terminal_failed",
    }
    for stage, _label in WORKFLOW_STAGE_ORDER:
        if stage == current_stage:
            if record_status in {"running", "stopping"}:
                statuses[stage] = "Running"
            elif record_status in {"failed", "failed_to_kill", "completed_with_failure", "terminal_failed"}:
                statuses[stage] = "Failed"
            elif record_status in {"cancelled", "stopped", "force_killed"}:
                statuses[stage] = record_status.replace("_", " ").title()
            else:
                statuses[stage] = "Running"
        elif current_stage and _stage_before(stage, current_stage):
            statuses[stage] = "Done"
        elif terminal and current_stage and _stage_before(stage, current_stage):
            statuses[stage] = "Done"
        else:
            statuses[stage] = "Pending"
    return statuses


def _adjust_concise_statuses_for_current_stage(statuses: dict[str, str], current_stage: object) -> dict[str, str]:
    display_stage = _display_stage_from_current_stage(current_stage)
    if display_stage != "prepare_next_iteration":
        return statuses
    adjusted = dict(statuses)
    for stage in ("paper", "approval", "engineer", "manager", "reviewer"):
        if adjusted.get(stage) in {"", "-", "Unknown", "Pending"}:
            adjusted[stage] = "Done"
    if adjusted.get("planner") in {"", "-", "Unknown", "Pending"}:
        adjusted["planner"] = "Running"
    if adjusted.get("final") in {"", "-", "Unknown"}:
        adjusted["final"] = "Pending"
    return adjusted


def _prepare_next_iteration_concise_statuses() -> dict[str, str]:
    return {
        "paper": "Done",
        "planner": "Running",
        "approval": "Done",
        "engineer": "Done",
        "manager": "Done",
        "reviewer": "Done",
        "final": "Pending",
    }


def _display_stage_from_current_stage(current_stage: object) -> str:
    text = str(current_stage or "").strip().lower()
    if not text:
        return ""
    if text == "prepare_next_iteration_node":
        return "prepare_next_iteration"
    return NODE_TO_STAGE.get(text, text)


def _show_concise_stage_bar(repo_path: str | None, result: dict) -> None:
    """Show a concise stage status bar on the main UI.

    Displays paper → planner → engineer → manager → reviewer → final stages
    with their current status (pending/running/done/failed/skipped).
    """
    stages = WORKFLOW_STAGE_ORDER
    manifest = read_latest_manifest(repo_path) if repo_path else {}
    record = _active_run_record(repo_path) if repo_path else {}
    runtime = st.session_state.get("stage_runtime") if st.session_state.get("workflow_running") else None
    statuses = _concise_stage_statuses_from_sources(
        runtime=runtime,
        manifest=manifest,
        record=record,
        result=result,
    )

    # Render concise stage bar
    st.caption("阶段进度 / Stage Progress")
    cols = st.columns(len(stages))
    for col, (stage, label) in zip(cols, stages):
        status = statuses.get(stage, "-")
        with col:
            if status == "Done":
                st.success(f"✓ {label}")
            elif status == "Running":
                st.info(f"▶ {label}")
            elif status == "Failed":
                st.error(f"✗ {label}")
            elif status == "Skipped":
                st.warning(f"∅ {label}")
            elif status == "Pending":
                st.caption(f"○ {label}")
            elif status in {"Needs Fix", "Needs Input"}:
                st.warning(f"! {label}: {status}")
            elif status == "Input Contract Ready":
                st.caption(f"○ {label}: {status}")
            else:
                st.caption(f"? {label}")


def _show_stage_chip_row(stages, statuses: dict[str, tuple[str, object]], source_caption: str) -> None:
    st.caption(source_caption)
    cols = st.columns(len(stages))
    for col, (stage, label) in zip(cols, stages):
        status, iteration = statuses.get(stage, ("Pending", None))
        status = str(status or "Pending")
        with col:
            badge_class = status.lower().replace(" ", "-").replace("_", "-")
            st.markdown(f'<span class="badge badge-{badge_class}">{status}</span>', unsafe_allow_html=True)
            st.caption(label)
            if iteration:
                st.caption(f"iter_{int(iteration):03d}")


def _live_runtime_status_model(record: dict) -> dict[str, str]:
    if not record:
        return {}
    keys = (
        ("Runtime status", "status"),
        ("Current stage", "current_stage"),
        ("Stage status", "stage_status"),
        ("Iteration", "iteration"),
        ("Heartbeat", "heartbeat_at"),
        ("Failed stage", "failed_stage"),
        ("Stop reason", "termination_reason"),
        ("Failure category", "error_code"),
    )
    return {label: _compact_display(str(record.get(key, "") or "-"), 120) for label, key in keys}


def _artifact_summary_model(manifest: dict) -> dict[str, str]:
    if not manifest:
        return {}
    keys = (
        ("Workflow final_status", "status"),
        ("Current stage", "current_stage"),
        ("Failed stage", "failed_stage"),
        ("Stop reason", "stop_reason"),
        ("Accepted level", "accepted_level"),
        ("Observed level", "observed_level"),
        ("Reviewer verdict", "reviewer_verdict"),
    )
    summary = {label: _compact_display(str(manifest.get(key, "") or "-"), 120) for label, key in keys}
    failed_stage = summary.get("Failed stage", "-")
    if failed_stage == "-":
        summary["Failed stage"] = _failed_stage_from_manifest(manifest) or "-"
    return summary


def _workflow_failure_summary_model(record: dict, manifest: dict) -> dict[str, str]:
    final_status = str((manifest or {}).get("status", "") or (record or {}).get("status", "") or "")
    if final_status not in {"completed_with_failure", "failed", "force_killed", "stopped", "cancelled", "failed_to_kill"}:
        return {}
    failed_stage = str((manifest or {}).get("failed_stage", "") or (record or {}).get("failed_stage", "") or "")
    if not failed_stage and manifest:
        failed_stage = _failed_stage_from_manifest(manifest)
    stop_reason = str(
        (manifest or {}).get("stop_reason", "")
        or (manifest or {}).get("termination_reason", "")
        or (record or {}).get("termination_reason", "")
        or (record or {}).get("error_code", "")
        or ""
    )
    failure_category = str(
        (manifest or {}).get("failure_category", "")
        or (manifest or {}).get("backend_failure_category", "")
        or (record or {}).get("error_code", "")
        or ""
    )
    error_tail = _first_failure_tail(record, manifest)
    return {
        "workflow final_status": final_status,
        "failed_stage": failed_stage or "-",
        "stop_reason": stop_reason or "-",
        "failure_category": failure_category or "-",
        "backend error tail": _compact_display(error_tail, 220) if error_tail else "-",
    }


def _workflow_failure_summary_text(summary: dict[str, str]) -> str:
    parts = [f"{key}={value}" for key, value in summary.items() if value and value != "-"]
    return "Workflow failure summary: " + " | ".join(parts)


def _first_failure_tail(*sources: dict) -> str:
    keys = (
        "backend_error_tail",
        "stderr_tail",
        "error_tail",
        "failure_detail",
        "failure_reason",
        "backend_user_message",
        "termination_reason",
    )
    for source in sources:
        if not isinstance(source, dict):
            continue
        for key in keys:
            value = str(source.get(key, "") or "").strip()
            if value:
                return value
    return ""


def _show_workflow_data_sources(repo_path: str | Path) -> None:
    rows = _workflow_data_sources_model(repo_path)
    if rows:
        st.caption("Workflow data sources")
        st.table(pd.DataFrame(rows))


def _workflow_data_sources_model(repo_path: str | Path) -> list[dict[str, str]]:
    repo = Path(repo_path)
    sources = [
        ("Run path", repo),
        ("Manifest path", repo / ".r2a" / "latest" / "RUN_MANIFEST.json"),
        ("Final report path", report_path(repo, "final")),
        ("Source acquisition path", report_path(repo, "source_acquisition")),
        ("Source inspection path", report_path(repo, "source_inspection")),
        ("Next planner context path", report_path(repo, "next_planner_context")),
        ("Iteration state path", repo / ".r2a" / "ITERATION_STATE.json"),
        ("Planner transaction path", repo / ".r2a" / "logs" / "planner_transaction.json"),
    ]
    return [{"Source": label, "Path": str(path), "Exists": "yes" if path.exists() else "no"} for label, path in sources]


def _stage_status_from_manifest(stage: str, manifest: dict) -> str:
    if not manifest:
        return ""
    stages = manifest.get("stages", {})
    if not isinstance(stages, dict):
        return ""
    record = stages.get(stage, {})
    raw = str(record.get("status", "") if isinstance(record, dict) else "" or "").strip().upper()
    terminal = str(manifest.get("status", "") or "") in {"failed", "completed_with_failure", "completed_success", "force_killed", "stopped", "cancelled"}
    current = str(manifest.get("current_stage", "") or "")
    manifest_running = str(manifest.get("status", "") or "").upper() == "RUNNING"
    failed_stage = _failed_stage_from_manifest(manifest)
    if not terminal and current in dict(WORKFLOW_STAGE_ORDER):
        if _stage_after(stage, current):
            return "Pending"
        if stage == current and raw in {"", "PENDING"} and manifest_running:
            return "Running"
        if _stage_before(stage, current) and raw in {"", "PENDING"}:
            return "Done"
    if raw in {"PASS", "SUCCESS", "APPROVED", "WARNING"}:  # SUCCESS is valid status from RUN_MANIFEST
        if stage == "final" and str(manifest.get("status", "") or "") == "completed_with_failure":
            return "Failure Report"
        if stage == "final":
            return "Final Report"
        return "Done" if stage != "manager" else ("Capped" if _manifest_manager_capped(manifest) else "Done")
    if raw in {
        "FAIL",
        "FAILED",
        "REVIEWER_INVALID_VERDICT",
        "REVIEWER_FEEDBACK_VALIDATION_FAILED",
        "REVIEWER_SAFETY_VALIDATION_FAILED",
        "REVIEWER_INPUT_INTEGRITY_BLOCKED_L3",
    }:
        return "Failed"
    if raw in {"REJECT", "REJECTED"}:
        return "Rejected"
    if raw in {"NEEDS_FIX", "NEEDS_INPUT", "NEEDS_OFFICIAL_INPUT", "NEEDS_INPUT_OR_BUDGET", "BORDERLINE"}:
        return raw.replace("_", " ").title()
    if raw in {"INPUT_CONTRACT_READY", "INPUT_READY", "CONTRACT_READY", "REVIEW_INPUT_READY"}:
        return "Input Contract Ready"
    if raw in {"SKIPPED", "NOT_RUN", "BLOCKED"}:
        return raw.replace("_", " ").title()
    if raw == "RUNNING":
        return "Running"
    if raw == "PENDING" and terminal and failed_stage and _stage_after(stage, failed_stage) and stage != "final":
        return "Skipped"
    if raw == "PENDING" and terminal and stage == "final":
        return "Pending"
    return "Pending" if raw else ""


def _failed_stage_from_manifest(manifest: dict) -> str:
    stages = manifest.get("stages", {})
    if not isinstance(stages, dict):
        return ""
    for stage, _ in WORKFLOW_STAGE_ORDER:
        item = stages.get(stage, {})
        status = str(item.get("status", "") if isinstance(item, dict) else "").strip().upper()
        if status in {"FAIL", "FAILED", "REJECT", "REJECTED"}:
            return stage
    return ""


def _stage_after(stage: str, other: str) -> bool:
    order = [item[0] for item in WORKFLOW_STAGE_ORDER]
    return stage in order and other in order and order.index(stage) > order.index(other)


def _stage_before(stage: str, other: str) -> bool:
    order = [item[0] for item in WORKFLOW_STAGE_ORDER]
    return stage in order and other in order and order.index(stage) < order.index(other)


def _stage_status_from_iteration_state(stage: str, stage_model: dict[str, str]) -> str:
    current = str(stage_model.get("stage", "") or "")
    if stage == current:
        return "Running"
    if _stage_before(stage, current):
        return "Done"
    return "Pending"


def _manifest_manager_capped(manifest: dict) -> bool:
    manager_status = str(manifest.get("manager_status", "") or "").upper()
    achieved = str(manifest.get("achieved_level", "") or "")
    evidence = manifest.get("evidence", {})
    observed_higher = any(isinstance(item, dict) and item.get("observed") and item.get("status") == "FAIL" for item in (evidence or {}).values())
    return manager_status == "FAIL" and observed_higher and bool(achieved)


def _manifest_has_planner_failure(manifest: dict) -> bool:
    stages = manifest.get("stages", {})
    planner = stages.get("planner", {}) if isinstance(stages, dict) else {}
    return str(planner.get("status", "") if isinstance(planner, dict) else "").strip().upper() in {"FAIL", "FAILED"} and not str(manifest.get("manager_status", "") or "")


def _stage_status_from_run_record(stage: str, record: dict) -> str:
    status = str(record.get("status", "") or "")
    current = str(record.get("current_stage", "") or "")
    if status in {"failed", "completed_with_failure", "force_killed", "stopped", "cancelled", "failed_to_kill"}:
        failed_stage = str(record.get("failed_stage", "") or "")
        if stage == "final" and status == "completed_with_failure" and str(record.get("current_stage", "") or "") == "final":
            return "Failure Report"
        if failed_stage:
            if stage == failed_stage:
                return "Failed" if status in {"failed", "failed_to_kill", "completed_with_failure"} else status.replace("_", " ").title()
            order = [item[0] for item in WORKFLOW_STAGE_ORDER]
            if stage in order and failed_stage in order and order.index(stage) < order.index(failed_stage):
                return "Done"
            if stage in {"engineer", "manager", "reviewer", "approval"} and stage != "final":
                return "Skipped"
        if stage == current or (current == "final" and stage == "final"):
            return "Failed" if status in {"failed", "failed_to_kill"} else status.replace("_", " ").title()
        order = [item[0] for item in WORKFLOW_STAGE_ORDER]
        if current in order and stage in order and order.index(stage) < order.index(current) and current != "final":
            return "Done"
        return "Skipped" if stage in {"engineer", "manager", "reviewer"} else "Pending"
    if status == "completed_success":
        return "Done"
    if status in {"running", "stopping", "force_killing"}:
        if stage == current:
            return "Running"
        order = [item[0] for item in WORKFLOW_STAGE_ORDER]
        if current in order and stage in order and order.index(stage) < order.index(current):
            return "Done"
        return "Pending"
    return "Pending"


def _stage_report_key(stage: str) -> str:
    return {
        "paper": "paper",
        "planner": "task",
        "approval": "approval",
        "engineer": "execution",
        "manager": "check",
        "reviewer": "review",
        "final": "final",
    }.get(stage, stage)


def _stage_status(repo_path: str | None, key: str, result: dict) -> str:
    if key == "approval":
        if result.get("approved"):
            return "PASS"
        if result.get("stopped"):
            return "FAILED"
        return "Pending"
    if result.get("stopped") and key in {"execution", "check", "review"}:
        return "Skipped"
    if not repo_path:
        return "Pending"
    terminal_status = _terminal_stage_status(repo_path, key)
    if terminal_status:
        return terminal_status
    path = report_path(repo_path, key)
    if not path.exists():
        return "Pending"
    text = path.read_text(encoding="utf-8", errors="replace")
    if key == "check" and "\nFAIL" in text.upper():
        return "FAILED"
    if key == "execution" and "- status: failed" in text.lower():
        return "FAILED"
    if "Codex Stage Note" in text and "fallback" in text.lower():
        return "Fallback"
    return "PASS"


def _terminal_stage_status(repo_path: str | Path, key: str) -> str:
    engineer_status = _engineer_done_status(repo_path)
    reproduction_status = _reproduction_status(repo_path)
    iteration_status = _iteration_terminal_status(repo_path)
    if key == "execution":
        return engineer_status or reproduction_status or iteration_status
    terminal = reproduction_status or iteration_status
    if terminal:
        return terminal
    return ""


def _engineer_done_status(repo_path: str | Path) -> str:
    path = Path(repo_path) / ".r2a" / "results" / "ENGINEER_DONE.txt"
    try:
        raw = path.read_text(encoding="utf-8", errors="replace").strip().splitlines()[0]
    except (OSError, IndexError):
        return ""
    return _normalize_stage_status(raw)


def _reproduction_status(repo_path: str | Path) -> str:
    path = Path(repo_path) / ".r2a" / "results" / "reproduction_status.csv"
    result = sanitized_csv_rows(path)
    if result.has_error and not result.rows:
        return ""
    statuses = [_normalize_stage_status(str(row.get("status", ""))) for row in result.rows]
    return _highest_priority_status(status for status in statuses if status)


def _iteration_terminal_status(repo_path: str | Path) -> str:
    try:
        history = read_iteration_history(repo_path)
    except (OSError, json.JSONDecodeError):
        return ""
    candidates = [
        history.get("final_verdict", ""),
        history.get("reviewer_verdict", ""),
        history.get("stop_reason", ""),
        history.get("reproduction_level", ""),
        history.get("state_reproduction_level", ""),
    ]
    for item in reversed(history.get("iterations", []) or []):
        if isinstance(item, dict):
            candidates.extend(
                [
                    item.get("reviewer_verdict", ""),
                    item.get("check_status", ""),
                    item.get("reproduction_level", ""),
                    item.get("state_reproduction_level", ""),
                ]
            )
    statuses = [_normalize_stage_status(str(value)) for value in candidates]
    return _highest_priority_status(status for status in statuses if status)


def _highest_priority_status(statuses) -> str:
    priority = [
        "FAILED",
        "NEEDS_CLARIFICATION",
        "NEEDS_INPUT_OR_BUDGET",
        "NEEDS_OFFICIAL_INPUT",
        "BLOCKED",
        "PARTIAL",
        "PASS",
    ]
    seen = set(statuses)
    for status in priority:
        if status in seen:
            return status
    return ""


def _normalize_stage_status(value: str) -> str:
    text = (value or "").strip().upper()
    if not text:
        return ""
    text = text.replace("-", "_").replace(" ", "_")
    if "NEEDS_INPUT_OR_BUDGET" in text:
        return "NEEDS_INPUT_OR_BUDGET"
    if "NEEDS_OFFICIAL_INPUT" in text:
        return "NEEDS_OFFICIAL_INPUT"
    if "NEEDS_CLARIFICATION" in text:
        return "NEEDS_CLARIFICATION"
    if "FAILED" in text or text == "FAIL":
        return "FAILED"
    if "BLOCKED" in text:
        return "BLOCKED"
    if "PARTIAL" in text:
        return "PARTIAL"
    if text in {"PASS", "PASSED", "DONE", "SUCCESS", "COMPLETE", "COMPLETED"} or text.startswith("PASS_"):
        return "PASS"
    return ""


def _show_iteration_history() -> None:
    repo_path = _repo_path()
    if not repo_path:
        st.caption("No workspace yet.")
        return
    history = read_iteration_history(repo_path)
    iterations = history.get("iterations", [])
    if not iterations:
        st.caption("No iteration history yet.")
        return
    for item in iterations:
        with st.expander(f"iter_{int(item.get('iteration', 0)):03d}", expanded=False):
            st.write(
                {
                    "manager_status": item.get("check_status", ""),
                    "reviewer_verdict": item.get("reviewer_verdict", ""),
                    "suggested_next_action": item.get("suggested_next_action", ""),
                }
            )
            for key in ("task_spec", "execution_report", "check_report", "review_report"):
                st.caption(key)
                st.code(str(item.get(key, "")))


def _show_engineer_results() -> None:
    repo_path = _repo_path()
    if not repo_path:
        st.caption("No workspace yet.")
        return
    include_history = st.toggle("Include archived iteration results", value=False, help="默认只展示当前 `.r2a/results` 和 `results`，历史迭代结果仍可在 Iteration History / 阶段报告中查看。")
    artifacts = _collect_result_artifacts(repo_path, include_history=include_history)
    if not any(artifacts.values()):
        st.caption("Engineer 还没有生成 results 产物。运行后会在这里展示 CSV、图像、JSON、HTML 和 notes。")
        return

    csv_files = artifacts["tables"]
    image_files = artifacts["images"]
    text_files = artifacts["texts"]
    html_files = artifacts["html"]
    important_csv_files = _important_result_tables(csv_files)
    show_all_tables = st.toggle(
        "Show all CSV artifacts",
        value=False,
        help="默认只显示关键 CSV。完整运行会产生许多审计表、manifest、状态表和中间结果；需要排查时再打开全部。",
    )
    show_charts = st.toggle(
        "Show chart previews",
        value=False,
        help="默认关闭自动数值图，避免 command_id、query_count 等审计列生成无意义的大图。",
    )
    _show_engineer_result_summary(csv_files)
    visible_csv_files = _sort_result_tables(csv_files if show_all_tables else important_csv_files)
    tabs = st.tabs(
        [
            f"关键表格 ({len(visible_csv_files)}/{len(csv_files)})",
            f"图像/图表 ({len(image_files)})",
            f"Notes/JSON ({len(text_files)})",
            f"HTML ({len(html_files)})",
        ]
    )
    with tabs[0]:
        _render_result_tables(visible_csv_files, repo_path, show_charts=show_charts)
    with tabs[1]:
        _render_result_images(image_files, repo_path)
    with tabs[2]:
        _render_result_texts(text_files, repo_path)
    with tabs[3]:
        _render_result_html(html_files, repo_path)


def _collect_result_artifacts(repo_path: str | Path, *, include_history: bool = False) -> dict[str, list[Path]]:
    repo = Path(repo_path)
    result_dirs = [
        repo / ".r2a" / "results",
        repo / "results",
    ]
    runs_dir = repo / ".r2a" / "runs"
    if include_history and runs_dir.exists():
        result_dirs.extend(sorted(runs_dir.glob("iter_*/results"), reverse=True))
    buckets: dict[str, list[Path]] = {"tables": [], "images": [], "texts": [], "html": []}
    seen: set[Path] = set()
    for directory in result_dirs:
        if not directory.exists():
            continue
        for path in sorted((item for item in directory.rglob("*") if item.is_file()), key=lambda item: item.stat().st_mtime, reverse=True):
            if path in seen:
                continue
            seen.add(path)
            suffix = path.suffix.lower()
            if suffix in TABLE_EXTENSIONS:
                buckets["tables"].append(path)
            elif suffix in IMAGE_EXTENSIONS:
                buckets["images"].append(path)
            elif suffix in TEXT_RESULT_EXTENSIONS:
                buckets["texts"].append(path)
            elif suffix in HTML_EXTENSIONS:
                buckets["html"].append(path)
    return buckets


def _important_result_tables(paths: list[Path]) -> list[Path]:
    important_names = {
        "reduced_metrics.csv",
        "paper_alignment.csv",
        "input_contract_verification.csv",
        "docker_build.csv",
        "docker_runtime_smoke.csv",
        "runtime_smoke.csv",
        "reproduction_status.csv",
        "command_manifest.csv",
    }
    selected = [path for path in paths if path.name in important_names]
    return selected or paths[:8]


def _sort_result_tables(paths: list[Path]) -> list[Path]:
    priority = {
        "reduced_metrics.csv": 0,
        "paper_alignment.csv": 1,
        "input_contract_verification.csv": 2,
        "docker_runtime_smoke.csv": 3,
        "docker_build.csv": 4,
        "runtime_smoke.csv": 5,
        "reproduction_status.csv": 6,
        "command_manifest.csv": 7,
    }
    return sorted(paths, key=lambda path: (priority.get(path.name, 100), path.name))


def _show_engineer_result_summary(paths: list[Path]) -> None:
    if not paths:
        return
    names = {path.name for path in paths}
    summary_items = []
    if "reduced_metrics.csv" in names:
        summary_items.append("`reduced_metrics.csv`: 真实 reduced 指标，如 recall、qps、latency。")
    if "paper_alignment.csv" in names:
        summary_items.append("`paper_alignment.csv`: reduced run 与论文设置的对应关系和差异。")
    if "input_contract_verification.csv" in names:
        summary_items.append("`input_contract_verification.csv`: 数据集、query、ground truth、metric、命令等输入契约。")
    if "command_manifest.csv" in names:
        summary_items.append("`command_manifest.csv`: 命令、日志、artifact hash 的审计追踪。")
    if summary_items:
        st.caption("Engineer CSV 主要分为结果指标、论文对齐、输入契约和审计追踪。")
        st.markdown("\n".join(f"- {item}" for item in summary_items))


def _render_result_tables(paths: list[Path], repo_path: str | Path, *, show_charts: bool = False) -> None:
    if not paths:
        st.caption("没有找到 CSV/TSV 结果表。")
        return
    for path in paths:
        with st.expander(_relative_label(path, repo_path), expanded=path.name in {"reduced_metrics.csv", "reduced_demo_metrics.csv", "reproduction_status.csv"}):
            try:
                sep = "\t" if path.suffix.lower() == ".tsv" else ","
                if sep == ",":
                    frame, issues = sanitized_csv_frame(path)
                    for issue in issues:
                        if issue.level == "warning":
                            st.warning(issue.message)
                    if frame.empty:
                        raise ValueError("No valid data rows after CSV sanitization.")
                else:
                    frame = pd.read_csv(path, sep=sep)
            except Exception as exc:
                st.error(f"无法读取表格：{exc}")
                st.code(path.read_text(encoding="utf-8", errors="replace")[:5000])
                continue
            st.dataframe(frame, use_container_width=True, hide_index=True)
            if show_charts:
                try:
                    _render_numeric_chart(frame, path.name)
                except Exception as exc:
                    st.warning(f"{path.name} 数值图表预览失败，表格已正常显示：{exc}")


def _render_numeric_chart(frame: pd.DataFrame, name: str) -> None:
    if frame.empty:
        return
    if name == "reduced_metrics.csv" and "efs" in frame.columns:
        chart_columns = [column for column in ("recall", "recall_at_10", "qps", "latency_ms") if column in frame.columns]
        if chart_columns:
            chart = frame[["efs", *chart_columns]].copy()
            chart["efs"] = pd.to_numeric(chart["efs"], errors="coerce")
            for column in chart_columns:
                chart[column] = pd.to_numeric(chart[column], errors="coerce")
            chart = chart.dropna(subset=["efs"]).set_index("efs")[chart_columns].dropna(how="all")
            if not chart.empty:
                st.caption("reduced_metrics.csv 指标趋势预览")
                st.line_chart(chart, use_container_width=True)
            return
    numeric = frame.apply(pd.to_numeric, errors="coerce")
    ignored = {"query_count", "num_queries", "num_items", "repetitions", "k", "command_id", "artifact_hash"}
    numeric_columns = [column for column in numeric.columns if column not in ignored and numeric[column].notna().any()]
    if not numeric_columns:
        return
    chart = numeric[numeric_columns].dropna(how="all")
    if chart.empty:
        return
    index_column = next((column for column in ("method", "component", "dataset", "status", "result_level") if column in frame.columns), None)
    if index_column:
        labels = frame.loc[chart.index, index_column].astype(str)
        if len(labels) == len(chart):
            chart.index = labels
    st.caption(f"{name} 数值列预览")
    st.bar_chart(chart, use_container_width=True)


def _render_result_images(paths: list[Path], repo_path: str | Path) -> None:
    if not paths:
        st.caption("没有找到 Engineer 生成的图像。")
        return
    for path in paths:
        st.caption(_relative_label(path, repo_path))
        st.image(str(path), use_container_width=True)


def _render_result_texts(paths: list[Path], repo_path: str | Path) -> None:
    if not paths:
        st.caption("没有找到 notes/json/log 文本产物。")
        return
    for path in paths:
        with st.expander(_relative_label(path, repo_path), expanded=path.name == "ENGINEER_NOTES.md"):
            text = path.read_text(encoding="utf-8", errors="replace")
            if path.suffix.lower() == ".json":
                try:
                    st.json(json.loads(text))
                except json.JSONDecodeError:
                    st.code(text)
            elif path.suffix.lower() == ".md":
                st.markdown(text)
            else:
                st.code(text[:12000])


def _render_result_html(paths: list[Path], repo_path: str | Path) -> None:
    if not paths:
        st.caption("没有找到 HTML 结果页。")
        return
    for path in paths:
        with st.expander(_relative_label(path, repo_path), expanded=False):
            st.caption("HTML 文件已生成，可从下方路径打开。")
            st.code(str(path))
            st.code(path.read_text(encoding="utf-8", errors="replace")[:12000], language="html")


def _show_reports(*, default_key: str = "final", include_history: bool = True) -> None:
    repo_path = _repo_path()
    labels = [label for label, _ in REPORTS]
    keys = [key for _, key in REPORTS]
    default_index = keys.index(default_key) if default_key in keys else 0
    selected_label = st.selectbox("选择报告", labels, index=default_index, key="workflow_review_report_selector")
    selected_key = dict(REPORTS)[selected_label]
    st.markdown(read_report(repo_path, selected_key) if repo_path else "Not generated yet.")

    if include_history and repo_path:
        runs_path = Path(repo_path) / ".r2a" / "runs"
        if runs_path.exists():
            st.subheader("历史迭代报告")
            for iter_dir in sorted(runs_path.glob("iter_*")):
                with st.expander(iter_dir.name, expanded=False):
                    for filename in ("TASK_SPEC.md", "EXECUTION_REPORT.md", "CHECK_REPORT.md", "REVIEW_REPORT.md"):
                        path = iter_dir / filename
                        st.caption(filename)
                        st.markdown(path.read_text(encoding="utf-8", errors="replace") if path.exists() else "Not generated yet.")


def _quick_report_summary(repo_path: str | Path) -> dict[str, str]:
    review = read_report(repo_path, "review")
    check = read_report(repo_path, "check")
    execution = read_report(repo_path, "execution")
    final = read_report(repo_path, "final")
    final_model = _final_summary_model(final, repo_path=repo_path)
    summary = {
        "Final": f"{final_model['final_verdict']} | {final_model['current_level']}" if final_model["final_verdict"] != "-" else _first_non_empty_line(final),
        "Reviewer verdict": _extract_markdown_section_value(review, "Verdict"),
        "Manager status": _extract_markdown_section_value(check, "Status"),
        "Engineer summary": _extract_markdown_section_value(execution, "Summary"),
    }
    return {key: value for key, value in summary.items() if value and value != "Not generated yet."}


def _extract_markdown_section_value(markdown: str, heading: str) -> str:
    if not markdown or markdown == "Not generated yet.":
        return ""
    match = re_search_heading(markdown, heading)
    if not match:
        return ""
    value = match.strip()
    if value.startswith("- "):
        value = value[2:].strip()
    return _compact_display(value, 300)


def _extract_list_value(markdown: str, heading: str, label: str) -> str:
    section = re_search_heading(markdown, heading)
    prefix = f"- {label}:"
    for line in section.splitlines():
        stripped = line.strip()
        if stripped.startswith(prefix):
            return _compact_display(stripped[len(prefix) :].strip(), 160)
    return ""


def _extract_provenance_value(markdown: str, label: str) -> str:
    section = re_search_heading(markdown, "Provenance")
    for line in section.splitlines():
        stripped = line.strip()
        if stripped.startswith("- "):
            stripped = stripped[2:].strip()
        if stripped.startswith(f"{label}:"):
            return _compact_display(stripped.split(":", 1)[1].strip(), 300)
    return ""


def re_search_heading(markdown: str, heading: str) -> str:
    marker = f"## {heading}"
    start = markdown.find(marker)
    if start < 0:
        return ""
    body_start = start + len(marker)
    next_heading = markdown.find("\n## ", body_start)
    body = markdown[body_start:] if next_heading < 0 else markdown[body_start:next_heading]
    return body.strip()


def _first_non_empty_line(text: str) -> str:
    for line in (text or "").splitlines():
        stripped = line.strip("# \t")
        if stripped:
            return _compact_display(stripped, 300)
    return ""


def _relative_label(path: Path, repo_path: str | Path) -> str:
    try:
        return str(path.relative_to(Path(repo_path)))
    except ValueError:
        return str(path)


def _compact_display(text: str, limit: int) -> str:
    cleaned = " ".join((text or "").split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[:limit].rstrip() + "..."


def _show_logs() -> None:
    repo_path = _repo_path()
    log_names = [
        "paper_stdout.log",
        "paper_stderr.log",
        "planner_stdout.log",
        "planner_stderr.log",
        "codex_stdout.log",
        "codex_stderr.log",
        "manager_stdout.log",
        "manager_stderr.log",
        "reviewer_stdout.log",
        "reviewer_stderr.log",
    ]
    tabs = st.tabs(log_names)
    for tab, name in zip(tabs, log_names):
        with tab:
            text = read_log(repo_path, name) if repo_path else "Not available."
            st.text_area(name, value=text, height=220, disabled=True)


def _repo_path() -> str | None:
    workspace = st.session_state.workspace
    if not workspace:
        return None
    return workspace["repo_path"]

def _show_web_ui_control() -> None:
    import subprocess
    import sys
    import urllib.parse
    from pathlib import Path
    from r2a.tools.web_runtime_registry import check_registry

    project_root = Path(__file__).resolve().parents[1]
    app_path = project_root / "r2a_web" / "app.py"
    run_web_path = project_root / "run_web.py"

    def launch_run_web_control(*args: str, delay_seconds: float = 0.0) -> None:
        if delay_seconds > 0:
            command = [
                sys.executable,
                "-c",
                "import subprocess, sys, time; "
                "time.sleep(float(sys.argv[1])); "
                "raise SystemExit(subprocess.call([sys.executable, sys.argv[2], *sys.argv[3:]]))",
                str(delay_seconds),
                str(run_web_path),
                *args,
            ]
        else:
            command = [sys.executable, str(run_web_path), *args]
        if sys.platform.startswith("win"):
            subprocess.Popen(
                ["cmd", "/c", "start", "", "/min", *command],
                cwd=str(project_root),
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            return
        subprocess.Popen(command, cwd=str(project_root))

    def redirect_to_shutdown_page() -> None:
        html = """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>R2A Web shut down</title>
  <style>
    body {
      margin: 0;
      min-height: 100vh;
      display: grid;
      place-items: center;
      background: #0f1117;
      color: #f5f7fb;
      font-family: Inter, Segoe UI, Arial, sans-serif;
    }
    main {
      width: min(560px, calc(100vw - 48px));
      border: 1px solid #2c3440;
      border-radius: 8px;
      padding: 28px;
      background: #171b22;
    }
    h1 {
      margin: 0 0 10px;
      font-size: 24px;
      font-weight: 700;
    }
    p {
      margin: 0;
      color: #aeb7c4;
      line-height: 1.6;
      font-size: 15px;
    }
  </style>
</head>
<body>
  <main>
    <h1>R2A Web has shut down</h1>
    <p>The local Streamlit service was stopped normally. You can close this tab, or start R2A Web again from PyCharm or <code>python run_web.py</code>.</p>
  </main>
</body>
</html>
"""
        url = "data:text/html;charset=utf-8," + urllib.parse.quote(html)
        st.html(
            f"<script>window.parent.location.replace({json.dumps(url)});</script>",
            unsafe_allow_javascript=True,
        )

    registered = check_registry(app_path)
    if registered.get("valid"):
        pid = registered["pid"]
        port = registered["port"]
        st.caption(f"Web UI is running (pid={pid}, http://127.0.0.1:{port})")

        if st.button("Shutdown UI", type="secondary", use_container_width=True, help="Stop the current web server and release its port. Running workflows receive a normal stop request first."):
            st.warning("Shutting down R2A Web...")
            run_id = st.session_state.get("active_run_id", "")
            workspace = st.session_state.get("workspace")
            if workspace and run_id:
                from r2a.tools.process_manager import request_cancel as stop_workflow
                stop_workflow(workspace["repo_path"], run_id, force=False, reason="ui_shutdown")
            redirect_to_shutdown_page()
            launch_run_web_control("--stop", delay_seconds=1.5)
            st.success("R2A Web has been shut down. You can close this browser tab.")
            st.stop()

        if st.button("Restart UI", type="secondary", use_container_width=True, help="Stop the web server and start a fresh instance."):
            st.warning("Restarting R2A Web...")
            launch_run_web_control("--restart")
            st.success("R2A Web is restarting. Close this tab and reopen after a moment.")
            st.stop()
    else:
        st.caption("Web UI is not running (no registered instance). Use python run_web.py to start.")


def _apply_css() -> None:
    st.markdown(
        """
        <style>
        .block-container {
            max-width: 1180px;
            padding-top: 2rem;
            padding-bottom: 3rem;
        }
        .badge {
            display: inline-block;
            border-radius: 999px;
            padding: 0.18rem 0.62rem;
            font-size: 0.78rem;
            border: 1px solid #d8dde3;
            background: #f6f7f8;
            color: #2f3a45;
        }
        .badge-done {
            background: #eef7f1;
            border-color: #cfe8d7;
            color: #245b37;
        }
        .badge-pass {
            background: #eef7f1;
            border-color: #cfe8d7;
            color: #245b37;
        }
        .badge-running {
            background: #e8f3ff;
            border-color: #93c5fd;
            color: #1d4ed8;
            box-shadow: 0 0 0 2px rgba(59, 130, 246, 0.12);
        }
        .badge-failed {
            background: #fff1f1;
            border-color: #f1c9c9;
            color: #8a2b2b;
        }
        .badge-fallback {
            background: #fff7ed;
            border-color: #fed7aa;
            color: #9a3412;
        }
        .badge-skipped {
            background: #f4f4f5;
            color: #52525b;
        }
        .badge-pending {
            background: #f8fafc;
            color: #64748b;
        }
        div[data-testid="stMetric"] {
            padding: 0.15rem 0;
        }
        div[data-testid="stMetricLabel"] {
            font-size: 0.82rem;
            line-height: 1.15;
        }
        div[data-testid="stMetricValue"] {
            font-size: 1.35rem;
            line-height: 1.2;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }
        div[data-testid="stMetricDelta"] {
            font-size: 0.78rem;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


if __name__ == "__main__":
    main()
