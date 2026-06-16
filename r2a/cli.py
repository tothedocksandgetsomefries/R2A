from __future__ import annotations

from pathlib import Path
import sys

import typer
from rich.console import Console

from r2a.agents.engineer_agent import run_engineer_agent
from r2a.agents.manager_agent import run_manager_agent
from r2a.agents.paper_agent import run_paper_agent
from r2a.agents.planner_agent import run_planner_agent
from r2a.agents.reviewer_agent import run_reviewer_agent
from r2a.core.config import (
    DEFAULT_CLAUDE_EXECUTABLE,
    DEFAULT_CODEX_EXECUTABLE,
    REPORT_FILENAMES,
    SUPPORTED_ENGINEER_EXECUTORS,
    SUPPORTED_MANAGER_BACKENDS,
    SUPPORTED_PAPER_BACKENDS,
    SUPPORTED_PLANNER_BACKENDS,
    SUPPORTED_REVIEWER_BACKENDS,
    SUPPORTED_FINAL_WRITER_BACKENDS,
)
from r2a.core.paths import ensure_artifact_dir, ensure_repo_dir, report_path, require_repo_dir
from r2a.core.state import make_initial_state
from r2a.tools.codex_cli import check_codex_cli, format_codex_cli_error
from r2a.tools.claude_health import health_check_json, render_health_check_table, run_claude_health_check
from r2a.tools.claude_runner import check_claude_code_cli, format_claude_code_cli_error
from r2a.tools.report_writer import write_report
from r2a.tools.openclaw_stage_runner import (
    DEFAULT_OPENCLAW_EXECUTABLE,
    DEFAULT_OPENCLAW_AGENT,
    DEFAULT_OPENCLAW_MODEL,
    DEFAULT_OPENCLAW_PROVIDER,
    DEFAULT_OPENCLAW_RUNNER,
)
from r2a.tools.reproduction_levels import REPRODUCTION_LEVELS
from r2a.tools.wsl import DEFAULT_WSL_CACHE_DIR, DEFAULT_WSL_DISTRO, check_wsl
from r2a.workflow.graph import build_workflow_graph

app = typer.Typer(help="R2A = Research Reproduction Agent.")
console = Console()
MAX_AI_STAGE_TIMEOUT_SECONDS = 10800


def _repo_option(repo_path: str) -> Path:
    return Path(repo_path).expanduser().resolve()


@app.command()
def init(
    repo: str = typer.Option(..., "--repo", help="Target repository path."),
    force: bool = typer.Option(False, "--force", help="Overwrite existing R2A artifact templates."),
) -> None:
    """Initialize R2A artifacts inside a target repo."""
    repo_path = ensure_repo_dir(_repo_option(repo))
    ensure_artifact_dir(repo_path)
    template_values = {
        "repo_path": repo_path,
        "goal": "",
        "planning_mode": "initialized",
        "paper_source": "Not set.",
        "paper_topic": "Not set.",
        "user_goal": "",
        "problem": "Not set.",
        "method_summary": "Not set.",
        "baselines": "Not set.",
        "datasets": "Not set.",
        "metrics": "Not set.",
        "reproduction_requirements": "Not set.",
        "reproduction_gaps": "Not set.",
        "known_limitations": "- Initialized only",
        "confidence": "Low",
        "prompt_summary": "- Initialized only",
        "extra_context": "",
        "mvp_notes": "Initialized by r2a init.",
        "evidence_sources": "- Not set",
        "extracted_evidence": "- Not set",
        "missing_evidence": "- Not set",
        "notes": "Initialized by r2a init.",
        "paper_brief_path": report_path(repo_path, "paper"),
        "paper_evidence_path": report_path(repo_path, "paper_evidence"),
        "paper_text_path": report_path(repo_path, "paper_text"),
        "paper_context_path": report_path(repo_path, "paper_context"),
        "paper_reproduction_card_path": report_path(repo_path, "paper_reproduction_card"),
        "paper_figures_tables_path": report_path(repo_path, "paper_figures_tables"),
        "paper_parse_quality_path": report_path(repo_path, "paper_parse_quality"),
        "paper_analysis_path": report_path(repo_path, "paper_analysis"),
        "parse_quality_body": "Initialized only. Run Paper stage to generate critical table parse quality.",
        "analysis_body": "Initialized only. Run Paper stage to generate integrated Chinese paper analysis.",
        "paper_path": "No paper uploaded.",
        "extraction_status": "initialized",
        "pages_checked": 0,
        "text_length": 0,
        "truncated": False,
        "paper_text": "Not run yet.",
        "paper_text_excerpt": "Not run yet.",
        "task_spec_path": report_path(repo_path, "task"),
        "iteration": 1,
        "auto_iteration_context": f"auto_iterate=False, max_iterations={DEFAULT_MAX_ITERATIONS}",
        "executor": "codex",
        "execution_environment": "windows",
        "command": "Not run yet.",
        "exit_code": "N/A",
        "status": "initialized",
        "context": "- Initialized only",
        "paper_evidence_used": "- Not planned yet",
        "evidence_gaps": "- Not planned yet",
        "figure_table_verification_tasks": "- Not planned yet",
        "previous_review_summary": "- Not applicable for first iteration",
        "required_fixes_from_previous_iteration": "- Not applicable for first iteration",
        "objective": "Not planned yet.",
        "fix_scope": "- Not applicable for first iteration",
        "what_must_not_change": "- Not planned yet",
        "allowed_files": "- Not planned yet",
        "forbidden_files": "- Not planned yet",
        "experiment_config": "- Not planned yet",
        "required_metrics": "- Not planned yet",
        "expected_outputs": "- Not planned yet",
        "acceptance_criteria": "- Not planned yet",
        "stop_conditions": "- Not planned yet",
        "engineer_instructions": "- Not planned yet",
        "summary": "Not run yet.",
        "modified_files": "- Not run yet",
        "commands_run": "- Not run yet",
        "generated_files": "- Not run yet",
        "result_summary": "Not run yet.",
        "errors_warnings": "Not run yet.",
        "clarification_needed": "No",
        "acceptance_checklist": "- Not run yet",
        "stdout": "(empty)",
        "stderr": "(empty)",
        "skipped": True,
        "strict": False,
        "git_is_repo": "unknown",
        "git_clean": "unknown",
        "git_changes": "- Not checked",
        "csv_checked": "- Not checked",
        "log_checked": "- Not checked",
        "errors": "- None",
        "warnings": "- None",
        "file_checks": "- Not checked",
        "csv_checks": "- Not checked",
        "log_checks": "- Not checked",
        "git_checks": "- Not checked",
        "forbidden_file_checks": "- Not checked",
        "parameter_coverage_checks": "Not checked",
        "final_decision": "initialized",
        "suggested_next_action": "Run r2a plan.",
        "execution_report_path": report_path(repo_path, "execution"),
        "check_report_path": report_path(repo_path, "check"),
        "recommendation": "Not reviewed yet.",
        "verdict": "BORDERLINE",
        "should_iterate": "No",
        "paper_alignment": "- Not reviewed yet",
        "major_issues": "- Not reviewed yet",
        "minor_issues": "- Not reviewed yet",
        "missing_tests": "Not reviewed yet.",
        "risky_changes": "Not reviewed yet.",
        "reproduction_limitations": "- Not reviewed yet",
        "required_fixes": "- Not reviewed yet",
        "final_status": "initialized",
        "total_iterations": 0,
        "stop_reason": "Not run yet.",
        "final_verdict": "Not reviewed yet.",
        "iteration_summary": "- Not run yet",
        "latest_reports": "- Not run yet",
        "limitations": "- Not run yet",
        "final_writer_summary": "- Final Writer: disabled\n- Final Writer backend: template\n- Final Writer model: none",
        "final_narrative_cn": "Final Writer has not run yet. Template report initialization only.",
        "command_manifest_summary": "- command_manifest.csv not generated yet.",
    }
    for key, filename in REPORT_FILENAMES.items():
        if key in {"final_narrative", "final_writer_metadata"}:
            continue
        write_report(
            report_path(repo_path, key),
            filename,
            template_values,
            force=force,
        )
    console.print(f"[green]Initialized R2A artifacts:[/green] {repo_path / '.r2a'}")


@app.command()
def plan(
    repo: str = typer.Option(..., "--repo", help="Target repository path."),
    goal: str = typer.Option(..., "--goal", help="Reproduction or experiment goal."),
    paper_path: str = typer.Option("", "--paper-path", help="Optional paper file path, usually a PDF."),
    output_language: str = typer.Option("English", "--output-language", help="Output language: English or Chinese."),
    force: bool = typer.Option(False, "--force", help="Overwrite existing plan artifacts."),
) -> None:
    """Generate PAPER_BRIEF.md and TASK_SPEC.md."""
    repo_path = require_repo_dir(_repo_option(repo))
    language = _language_code(output_language)
    state = make_initial_state(
        repo_path,
        goal=goal,
        paper_path=paper_path or None,
        guidance=goal,
        resolved_goal=goal,
        language=language,
        output_language=output_language,
    )
    state = run_paper_agent(state, force=force)
    state = run_planner_agent(state, force=force)
    console.print(f"[green]Wrote task spec:[/green] {state['task_spec_path']}")


@app.command()
def run(
    repo: str = typer.Option(..., "--repo", help="Target repository path."),
    executor: str = typer.Option("shell", "--executor", help="Executor name: shell, codex, or claude."),
    timeout: int = typer.Option(10800, "--timeout", help="Executor timeout in seconds."),
    codex_executable_path: str = typer.Option(DEFAULT_CODEX_EXECUTABLE, "--codex-executable-path", help="Codex executable path."),
    claude_executable_path: str = typer.Option(DEFAULT_CLAUDE_EXECUTABLE, "--claude-executable-path", help="Claude Code or Claude Code Router executable path. For DeepSeek via Router, use ccr or ccr.cmd."),
    openclaw_executable_path: str = typer.Option("", "--openclaw-executable-path", help=f"OpenClaw executable path. Defaults to env R2A_OPENCLAW_EXECUTABLE_PATH or {DEFAULT_OPENCLAW_EXECUTABLE}."),
    openclaw_config_path: str = typer.Option("", "--openclaw-config-path", help="OpenClaw config/profile path. Defaults to env R2A_OPENCLAW_CONFIG_PATH; leave empty to require explicit UI/CLI config."),
    openclaw_provider: str = typer.Option("", "--openclaw-provider", help=f"OpenClaw provider. Defaults to env R2A_OPENCLAW_PROVIDER or {DEFAULT_OPENCLAW_PROVIDER}."),
    openclaw_model: str = typer.Option("", "--openclaw-model", help=f"OpenClaw model. Defaults to env R2A_OPENCLAW_MODEL or {DEFAULT_OPENCLAW_MODEL}."),
    openclaw_runner: str = typer.Option("", "--openclaw-runner", help=f"OpenClaw runner/mode. Defaults to env R2A_OPENCLAW_RUNNER or {DEFAULT_OPENCLAW_RUNNER}."),
    openclaw_agent: str = typer.Option("", "--openclaw-agent", help=f"OpenClaw agent id for R2A sessions. Defaults to env R2A_OPENCLAW_AGENT or {DEFAULT_OPENCLAW_AGENT}."),
    engineer_execution_environment: str = typer.Option("windows", "--engineer-execution-environment", help="Engineer execution environment: windows or wsl."),
    wsl_distro: str = typer.Option(DEFAULT_WSL_DISTRO, "--wsl-distro", help="WSL distro name when using --engineer-execution-environment wsl."),
    wsl_cache_dir: str = typer.Option(DEFAULT_WSL_CACHE_DIR, "--wsl-cache-dir", help="Windows path for WSL cache exports."),
) -> None:
    """Run Engineer Stage through an external executor wrapper."""
    if executor not in SUPPORTED_ENGINEER_EXECUTORS:
        raise typer.BadParameter("MVP CLI supports --executor shell, codex, or claude.")
    if timeout > MAX_AI_STAGE_TIMEOUT_SECONDS:
        raise typer.BadParameter("--timeout must be <= 10800 seconds.")
    _validate_choice("engineer execution environment", engineer_execution_environment, ("windows", "wsl"))
    if engineer_execution_environment == "wsl":
        wsl_check = check_wsl(wsl_distro)
        if not wsl_check.available:
            console.print(f"[red]WSL unavailable:[/red] {wsl_check.error}\n{wsl_check.hint}")
            raise typer.Exit(code=1)
    repo_path = require_repo_dir(_repo_option(repo))
    state = make_initial_state(
        repo_path,
        executor=executor,
        timeout=timeout,
        codex_stage_timeout=timeout,
        codex_executable_path=codex_executable_path,
        claude_executable_path=claude_executable_path,
        openclaw_executable_path=openclaw_executable_path,
        openclaw_config_path=openclaw_config_path,
        openclaw_provider=openclaw_provider,
        openclaw_model=openclaw_model,
        openclaw_runner=openclaw_runner,
        openclaw_agent=openclaw_agent,
        engineer_execution_environment=engineer_execution_environment,
        wsl_distro=wsl_distro,
        wsl_cache_dir=wsl_cache_dir,
        approved=True,
    )
    state["task_spec_path"] = str(report_path(repo_path, "task"))
    state = run_engineer_agent(state)
    console.print(f"[green]Wrote execution report:[/green] {state['execution_report_path']}")


@app.command()
def check(
    repo: str = typer.Option(..., "--repo", help="Target repository path."),
    strict: bool = typer.Option(False, "--strict", help="Treat warnings as check failures."),
) -> None:
    """Run Manager Stage rule checks."""
    repo_path = require_repo_dir(_repo_option(repo))
    state = make_initial_state(repo_path, strict=strict, approved=True)
    state = run_manager_agent(state)
    color = "green" if state["manager_passed"] else "red"
    console.print(f"[{color}]Manager status: {state['manager_passed']}[/{color}]")
    console.print(f"Wrote check report: {state['check_report_path']}")


@app.command("check-claude")
def check_claude(
    repo: str = typer.Option(".", "--repo", help="Workspace path for logs and optional health artifacts."),
    claude_executable_path: str = typer.Option(DEFAULT_CLAUDE_EXECUTABLE, "--claude-executable-path", help="Claude Code or Claude Code Router executable path. For CCR, use ccr or ccr.cmd."),
    run_write_test: bool = typer.Option(False, "--run-write-test", help="Actually invoke Claude/CCR once and require it to write .r2a/health/claude_healthcheck.txt."),
    run_planner_smoke: bool = typer.Option(False, "--run-planner-smoke", help="Actually invoke Claude/CCR Planner on a temporary mock workspace through Planner transaction and approval."),
    run_engineer_noop: bool = typer.Option(False, "--run-engineer-noop", help="Actually invoke Claude/CCR Engineer on an isolated verification-only no-op smoke workspace."),
    run_full_claude_smoke: bool = typer.Option(False, "--run-full-claude-smoke", help="Actually invoke Claude/CCR Paper, Planner, Engineer no-op, and Reviewer on an isolated L2 smoke workflow."),
    timeout: int = typer.Option(120, "--timeout", help="Timeout in seconds for optional real Claude/CCR checks."),
    json_output: bool = typer.Option(False, "--json", help="Print machine-readable JSON."),
) -> None:
    """Diagnose Claude Code / Claude Code Router availability and stage tool boundaries."""
    if timeout > MAX_AI_STAGE_TIMEOUT_SECONDS:
        raise typer.BadParameter("--timeout must be <= 10800 seconds.")
    report = run_claude_health_check(
        _repo_option(repo),
        claude_executable_path=claude_executable_path,
        run_write_test=run_write_test,
        run_planner_smoke=run_planner_smoke,
        run_engineer_noop=run_engineer_noop,
        run_full_claude_smoke=run_full_claude_smoke,
        timeout=timeout,
    )
    console.print(health_check_json(report) if json_output else render_health_check_table(report))
    if report.get("failure_category") and (run_write_test or run_planner_smoke or run_engineer_noop or run_full_claude_smoke):
        raise typer.Exit(code=1)


@app.command()
def review(
    repo: str = typer.Option(..., "--repo", help="Target repository path."),
) -> None:
    """Generate REVIEW_REPORT.md from execution and check artifacts."""
    repo_path = require_repo_dir(_repo_option(repo))
    state = make_initial_state(repo_path, approved=True)
    state = run_reviewer_agent(state)
    console.print(f"[green]Wrote review report:[/green] {state['review_report_path']}")


@app.command()
def workflow(
    repo: str = typer.Option(..., "--repo", help="Target repository path."),
    goal: str = typer.Option(..., "--goal", help="Reproduction or experiment goal."),
    paper_path: str = typer.Option("", "--paper-path", help="Optional paper file path, usually a PDF."),
    output_language: str = typer.Option("English", "--output-language", help="Output language: English or Chinese."),
    executor: str = typer.Option("shell", "--executor", help="Backward-compatible alias for --engineer-executor."),
    paper_backend: str = typer.Option("preprocess", "--paper-backend", help="Paper backend: preprocess/template/ai_reader/claude_reader/openclaw_reader. Legacy Paper Codex is disabled."),
    planner_backend: str = typer.Option("template", "--planner-backend", help="Planner backend: template, ccr_text, command, openai_compatible, anthropic, codex, claude, or openclaw."),
    planner_provider: str = typer.Option("", "--planner-provider", help="Planner OpenClaw provider (stage-level override). Defaults to Planner stage profile."),
    planner_model: str = typer.Option("", "--planner-model", help="Planner OpenClaw model (stage-level override). Defaults to Planner stage profile."),
    engineer_executor: str | None = typer.Option(None, "--engineer-executor", help="Engineer executor: shell, codex, claude, or openclaw."),
    engineer_provider: str = typer.Option("", "--engineer-provider", help="Engineer OpenClaw provider (stage-level override). Defaults to --openclaw-provider."),
    engineer_model: str = typer.Option("", "--engineer-model", help="Engineer OpenClaw model (stage-level override). Defaults to --openclaw-model."),
    manager_backend: str = typer.Option("rules", "--manager-backend", help="Manager backend: rules, codex_review, claude_review, or openclaw_review."),
    reviewer_backend: str = typer.Option("rules", "--reviewer-backend", help="Reviewer backend: rules, codex, claude, or openclaw."),
    final_writer_backend: str = typer.Option("template", "--final-writer-backend", help="Final Writer backend: template or openclaw."),
    final_writer_provider: str = typer.Option("", "--final-writer-provider", help="Final Writer OpenClaw provider override. Defaults to Final Writer stage profile."),
    final_writer_model: str = typer.Option("", "--final-writer-model", help="Final Writer OpenClaw model override. Defaults to Final Writer stage profile."),
    final_writer_profile: str = typer.Option("", "--final-writer-profile", help="Final Writer OpenClaw profile/runner label."),
    codex_stage_timeout: int = typer.Option(10800, "--codex-stage-timeout", help="Timeout for each Codex stage in seconds."),
    codex_executable_path: str = typer.Option(DEFAULT_CODEX_EXECUTABLE, "--codex-executable-path", help="Codex executable path. Use this when PATH cannot find codex."),
    claude_executable_path: str = typer.Option(DEFAULT_CLAUDE_EXECUTABLE, "--claude-executable-path", help="Claude Code or Claude Code Router executable path. For DeepSeek via Router, use ccr or ccr.cmd."),
    openclaw_executable_path: str = typer.Option("", "--openclaw-executable-path", help=f"OpenClaw executable path. Defaults to env R2A_OPENCLAW_EXECUTABLE_PATH or {DEFAULT_OPENCLAW_EXECUTABLE}."),
    openclaw_config_path: str = typer.Option("", "--openclaw-config-path", help="OpenClaw config/profile path. Defaults to env R2A_OPENCLAW_CONFIG_PATH; leave empty to require explicit UI/CLI config."),
    openclaw_provider: str = typer.Option("", "--openclaw-provider", help=f"OpenClaw provider. Defaults to env R2A_OPENCLAW_PROVIDER or {DEFAULT_OPENCLAW_PROVIDER}."),
    openclaw_model: str = typer.Option("", "--openclaw-model", help=f"OpenClaw model. Defaults to env R2A_OPENCLAW_MODEL or {DEFAULT_OPENCLAW_MODEL}."),
    openclaw_runner: str = typer.Option("", "--openclaw-runner", help=f"OpenClaw runner/mode. Defaults to env R2A_OPENCLAW_RUNNER or {DEFAULT_OPENCLAW_RUNNER}."),
    openclaw_agent: str = typer.Option("", "--openclaw-agent", help=f"OpenClaw agent id for R2A sessions. Defaults to env R2A_OPENCLAW_AGENT or {DEFAULT_OPENCLAW_AGENT}."),
    auto_approve: bool = typer.Option(False, "--auto-approve", help="Skip Human Approval prompt."),
    auto_iterate: bool = typer.Option(False, "--auto-iterate", help="Enable automatic iteration on Manager FAIL."),
    max_iterations: int = typer.Option(3, "--max-iterations", help="Maximum number of iterations when auto-iterate is enabled."),
    target_reproduction_level: str = typer.Option("L4_reduced_paper_aligned", "--target-reproduction-level", help="Target evidence level: L0_project_health through L6_full_or_near_full_reproduction."),
    download_budget_gb: int = typer.Option(20, "--download-budget-gb", min=0, help="Maximum official data download budget in GB."),
    allow_official_dataset_download: bool = typer.Option(False, "--allow-official-dataset-download", help="Allow bounded official dataset download for L3+ progress."),
    allow_full_benchmark: bool = typer.Option(False, "--allow-full-benchmark", help="Allow full or near-full benchmark planning when explicitly requested."),
    allow_external_baselines: bool = typer.Option(False, "--allow-external-baselines", help="Allow external baseline artifacts for L5 comparison."),
    allow_network: bool = typer.Option(False, "--allow-network", help="Explicitly allow bounded external network operations for algorithm/dependency acquisition."),
    allowed_network_scope: str = typer.Option("external_git_clone_for_algorithm_dependencies", "--allowed-network-scope", help="Comma-separated scope labels used only when --allow-network is set."),
    engineer_execution_environment: str = typer.Option("windows", "--engineer-execution-environment", help="Engineer execution environment: windows or wsl."),
    wsl_distro: str = typer.Option(DEFAULT_WSL_DISTRO, "--wsl-distro", help="WSL distro name when using --engineer-execution-environment wsl."),
    wsl_cache_dir: str = typer.Option(DEFAULT_WSL_CACHE_DIR, "--wsl-cache-dir", help="Windows path for WSL cache exports."),
) -> None:
    """Run the five-stage R2A LangGraph workflow."""
    resolved_engineer_executor = engineer_executor or executor
    if paper_backend == "codex":
        raise typer.BadParameter("Legacy Paper Codex backend is disabled. Use --paper-backend ai_reader, claude_reader, or local paper preprocess instead.")
    _validate_choice("paper backend", paper_backend, SUPPORTED_PAPER_BACKENDS)
    _validate_choice("planner backend", planner_backend, SUPPORTED_PLANNER_BACKENDS)
    _validate_choice("engineer executor", resolved_engineer_executor, SUPPORTED_ENGINEER_EXECUTORS)
    _validate_choice("manager backend", manager_backend, SUPPORTED_MANAGER_BACKENDS)
    _validate_choice("reviewer backend", reviewer_backend, SUPPORTED_REVIEWER_BACKENDS)
    _validate_choice("final writer backend", final_writer_backend, SUPPORTED_FINAL_WRITER_BACKENDS)
    _validate_choice("engineer execution environment", engineer_execution_environment, ("windows", "wsl"))
    _validate_choice("target reproduction level", target_reproduction_level, REPRODUCTION_LEVELS)
    language = _language_code(output_language)
    if codex_stage_timeout > MAX_AI_STAGE_TIMEOUT_SECONDS:
        raise typer.BadParameter("--codex-stage-timeout must be <= 10800 seconds.")
    codex_stages = [
        name
        for name, value in {
            "paper": paper_backend,
            "planner": planner_backend,
            "engineer": resolved_engineer_executor,
            "manager": manager_backend,
            "reviewer": reviewer_backend,
            "final_writer": final_writer_backend,
        }.items()
        if value in {"codex", "codex_review", "ai_reader"}
    ]
    claude_stages = [
        name
        for name, value in {
            "paper": paper_backend,
            "planner": planner_backend,
            "engineer": resolved_engineer_executor,
            "manager": manager_backend,
            "reviewer": reviewer_backend,
            "final_writer": final_writer_backend,
        }.items()
        if value in {"claude", "claude_code", "claude_review", "claude_reader"}
    ]
    if codex_stages:
        console.print("[yellow]Warning:[/yellow] This workflow will start one or more Codex CLI sessions.")
        cli_check = check_codex_cli(codex_executable_path)
        if not cli_check.available:
            console.print(f"[red]{format_codex_cli_error(cli_check)}[/red]")
            raise typer.Exit(code=1)
        codex_executable_path = cli_check.attempted_executable
    if claude_stages:
        console.print("[yellow]Warning:[/yellow] This workflow will start one or more Claude Code or Claude Code Router sessions.")
        claude_check = check_claude_code_cli(claude_executable_path)
        if not claude_check.available:
            console.print(f"[red]{format_claude_code_cli_error(claude_check)}[/red]")
            raise typer.Exit(code=1)
        claude_executable_path = claude_check.attempted_executable
    if engineer_execution_environment == "wsl":
        wsl_check = check_wsl(wsl_distro)
        if not wsl_check.available:
            console.print(f"[red]WSL unavailable:[/red] {wsl_check.error}\n{wsl_check.hint}")
            raise typer.Exit(code=1)
        console.print(f"[green]WSL execution available:[/green] {wsl_distro}; cache dir: {wsl_cache_dir}")
    repo_path = require_repo_dir(_repo_option(repo))
    approved = auto_approve
    if not auto_approve:
        if not sys.stdin.isatty():
            raise typer.BadParameter("Non-interactive workflow requires --auto-approve.")
        approved = typer.confirm("Continue from Planner Stage to Engineer Stage?", default=False)

    state = make_initial_state(
        repo_path,
        goal=goal,
        paper_path=paper_path or None,
        guidance=goal,
        resolved_goal=goal,
        language=language,
        output_language=output_language,
        executor=resolved_engineer_executor,
        paper_backend=paper_backend,
        planner_backend=planner_backend,
        planner_provider=planner_provider,
        planner_model=planner_model,
        engineer_executor=resolved_engineer_executor,
        engineer_provider=engineer_provider,
        engineer_model=engineer_model,
        engineer_execution_environment=engineer_execution_environment,
        wsl_distro=wsl_distro,
        wsl_cache_dir=wsl_cache_dir,
        manager_backend=manager_backend,
        reviewer_backend=reviewer_backend,
        final_writer_backend=final_writer_backend,
        final_writer_provider=final_writer_provider,
        final_writer_model=final_writer_model,
        final_writer_profile=final_writer_profile,
        codex_stage_timeout=codex_stage_timeout,
        codex_executable_path=codex_executable_path or DEFAULT_CODEX_EXECUTABLE,
        claude_executable_path=claude_executable_path or DEFAULT_CLAUDE_EXECUTABLE,
        openclaw_executable_path=openclaw_executable_path,
        openclaw_config_path=openclaw_config_path,
        openclaw_provider=openclaw_provider,
        openclaw_model=openclaw_model,
        openclaw_runner=openclaw_runner,
        openclaw_agent=openclaw_agent,
        timeout=codex_stage_timeout,
        auto_approve=auto_approve,
        auto_iterate=auto_iterate,
        max_iterations=max(1, min(10, max_iterations)),
        target_reproduction_level=target_reproduction_level,
        download_budget_gb=max(0, int(download_budget_gb)),
        allow_official_dataset_download=allow_official_dataset_download,
        allow_full_benchmark=allow_full_benchmark,
        allow_external_baselines=allow_external_baselines,
        allow_network=allow_network,
        allowed_network_scope=_network_scope_list(allowed_network_scope) if allow_network else [],
        approved=approved,
    )
    graph = build_workflow_graph()
    result = graph.invoke(state)
    console.print(_cli_final_summary(result) or result.get("final_report", "R2A workflow finished."))
    if result.get("final_report_path"):
        console.print(f"Wrote final report: {result['final_report_path']}")


def _validate_choice(name: str, value: str, choices) -> None:
    if value not in set(choices):
        raise typer.BadParameter(f"Unsupported {name}: {value}. Supported: {', '.join(choices)}.")


def _cli_final_summary(result: dict) -> str:
    path = result.get("final_report_path")
    if not path:
        return ""
    report_path_value = Path(str(path))
    if not report_path_value.exists():
        return ""
    text = report_path_value.read_text(encoding="utf-8", errors="replace")
    run_summary = _markdown_section(text, "Run Summary")
    executive = _markdown_section(text, "Executive Summary")
    blockers = _markdown_section(text, "Blocking Reasons") or _markdown_section(text, "阻塞原因")
    level = _markdown_section(text, "Reproduction Level")
    if not run_summary and not executive and not level:
        return ""
    parts = ["R2A workflow finished."]
    if run_summary:
        parts.append("\nRun Summary\n" + run_summary)
    if executive:
        parts.append("\nExecutive Summary\n" + executive)
    if level:
        selected = []
        for line in level.splitlines():
            if line.startswith(("- Current:", "- Target:", "- Result Type:", "- Full Reproduction Claim:", "- Next Action:")):
                selected.append(line)
        if selected:
            parts.append("\nReproduction Level\n" + "\n".join(selected))
    if blockers and blockers.strip() != "- None":
        parts.append("\nBlocking Reasons\n" + blockers)
    l4_summary = _provenance_value(text, "L4_ALIGNMENT_SUMMARY.md")
    if l4_summary:
        parts.append(f"\nL4 Alignment Summary\n- {l4_summary}")
    return "\n".join(parts)


def _markdown_section(text: str, heading: str) -> str:
    marker = f"## {heading}"
    start = text.find(marker)
    if start < 0:
        return ""
    body_start = start + len(marker)
    next_heading = text.find("\n## ", body_start)
    return (text[body_start:] if next_heading < 0 else text[body_start:next_heading]).strip()


def _provenance_value(text: str, label: str) -> str:
    section = _markdown_section(text, "Provenance")
    for line in section.splitlines():
        stripped = line.strip()
        if stripped.startswith("- "):
            stripped = stripped[2:].strip()
        if stripped.startswith(f"{label}:"):
            return stripped.split(":", 1)[1].strip()
    return ""


def _language_code(output_language: str) -> str:
    normalized = output_language.strip().lower()
    if normalized in {"chinese", "zh", "zh-cn", "simplified chinese"}:
        return "zh"
    if normalized in {"english", "en"}:
        return "en"
    raise typer.BadParameter("--output-language must be English or Chinese.")


def _network_scope_list(value: str) -> list[str]:
    return [item.strip() for item in str(value or "").replace(";", ",").replace("\n", ",").split(",") if item.strip()]


if __name__ == "__main__":
    app()
