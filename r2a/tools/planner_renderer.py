from __future__ import annotations

import json

from r2a.core.planner_schema import PlannerOutput
from r2a.tools.csv_schemas import allowed_values_for_csv, csv_header


def render_planner_json(output: PlannerOutput) -> str:
    return output.model_dump_json(indent=2) + "\n"


def render_task_spec(output: PlannerOutput) -> str:
    lines = [
        "# TASK_SPEC",
        "",
        "## Planner V2 Summary",
        "",
        "- Source Of Truth: `.r2a/PLANNER_OUTPUT.json`",
        f"- Schema Version: `{output.schema_version}`",
        f"- Iteration: `{output.iteration}`",
        f"- Planning Mode: `{output.planning_mode}`",
        f"- Iteration Strategy: `{output.iteration_strategy}`",
        f"- Contract Mode: `{output.contract_mode}`",
        f"- Objective: {output.objective}",
        "",
        "## Goal",
        "",
        output.objective,
        "",
        "## Objective",
        "",
        output.objective,
        "",
        "## Experiment Contract",
        "",
        f"See `.r2a/EXPERIMENT_CONTRACT.md`; contract mode is `{output.contract_mode}`.",
        "",
        "## Reproducibility Gate Summary",
        "",
        output.current_status_summary,
        "",
        "## Max Evidence Level Allowed",
        "",
        f"`{output.max_evidence_level_allowed}`",
        "",
        "## Completed Capabilities",
        "",
        *_bullets(output.completed_capabilities or ["None recorded."]),
        "",
        "## Blocking Issues",
        "",
        *_blocking_issue_lines(output),
        "",
        "## Tasks",
        "",
        *_task_lines(output),
        "",
        "## Allowed Files",
        "",
        *_allowed_files(output),
        "",
        "## Forbidden Files",
        "",
        "- Full-scale datasets, benchmark outputs, external baselines, system-level installs, and Docker escalation unless explicitly approved.",
        "- Paper facts, parse artifacts, and prior successful outputs unless the task explicitly says they are stale.",
        "",
        "## Acceptance Criteria",
        "",
        *_acceptance_lines(output),
        "",
        "## Evidence Used",
        "",
        *_evidence_lines(output.evidence_used),
        "",
        "## User Guidance",
        "",
        *_planner_note_lines(output.planner_notes),
        "",
        "## Paper Evidence Used",
        "",
        *_evidence_lines(output.evidence_used),
        "",
        "## Evidence Gaps",
        "",
        *_evidence_lines(output.evidence_gaps),
        "",
        "## Claim Restrictions",
        "",
        *_bullets(output.claim_restrictions),
        "",
        "## Required Labels",
        "",
        "- `PASS`",
        "- `FAIL`",
        "- `NOT_RUN`",
        "- `NEEDS_INPUT`",
        "",
        "## Required Fixes From Previous Iteration",
        "",
        *_previous_fix_lines(output),
        "",
        "## Manual Approval Points",
        "",
        *_bullets(output.manual_approval_points or ["None for this bounded package."]),
        "",
        "## Preserve Outputs",
        "",
        *_bullets(output.preserve_outputs or ["No prior outputs were marked for preservation."]),
        "",
        "## Paper Reproduction Card Summary",
        "",
        *_paper_card_summary(output),
        "",
        "## Paper Parse Quality Summary",
        "",
        "- See `.r2a/PAPER_PARSE_QUALITY.md` when available; caption-only or raw-text-only details remain evidence gaps until verified.",
        "",
        "## Stop Conditions",
        "",
        *_stop_condition_lines(output),
        "",
        "## L3 Entry Criteria",
        "",
        "Official or paper-linked reduced inputs, commands, and measured reduced metrics must be available before any L3 claim.",
        "",
        "## L4 Alignment Criteria",
        "",
        f"Reduced outputs must be mapped back to paper settings and limitations before any L4 claim. `paper_alignment.csv` must use `{csv_header('paper_alignment.csv')}`.",
        f"`paper_alignment.csv.match_status` must use only: {', '.join(allowed_values_for_csv('paper_alignment.csv', 'match_status'))}. Do not use legacy `PARTIAL` or `GAP` as match_status values.",
        "",
        "## Git Provenance Contract",
        "",
        "For any cloned artifact repository, `source_verification.csv` and `command_manifest.csv` must record actual checkout provenance from `git -C <artifact_repo_path> rev-parse HEAD`, `git -C <artifact_repo_path> remote get-url origin`, and `git -C <artifact_repo_path> rev-parse --abbrev-ref HEAD`.",
        "If Planner carries an expected commit from paper text, review feedback, or prior state, label it as expected only; do not present it as actual unless it equals the local checkout HEAD.",
        "",
    ]
    return "\n".join(lines).rstrip() + "\n"


def render_experiment_contract(output: PlannerOutput) -> str:
    labels = ["PASS", "FAIL", "NOT_RUN", "NEEDS_INPUT"]
    lines = [
        "# EXPERIMENT_CONTRACT",
        "",
        "## Contract Mode",
        "",
        output.contract_mode,
        "",
        "## Max Evidence Level Allowed",
        "",
        f"`{output.max_evidence_level_allowed}`",
        "",
        "## Reproducibility Gate",
        "",
        output.current_status_summary,
        "",
        "## Allowed Engineer Task Surface",
        "",
        *_task_contract_lines(output),
        "",
        "## Required Labels",
        "",
        *_bullets(f"`{label}`" for label in labels),
        "",
        "## Claim Restrictions",
        "",
        *_bullets(output.claim_restrictions),
        "- Target level may remain `L4_reduced_paper_aligned`, but this contract does not authorize claiming it.",
        "",
        "## Stop Conditions",
        "",
        *_stop_condition_lines(output),
        "",
        "## Source Of Truth",
        "",
        "`.r2a/PLANNER_OUTPUT.json` is the machine-readable source of truth. This Markdown is a rendered summary.",
        "",
    ]
    return "\n".join(lines).rstrip() + "\n"


def _task_lines(output: PlannerOutput) -> list[str]:
    lines: list[str] = []
    for task in output.tasks:
        lines.extend(
            [
                f"### {task.task_id}: {task.title}",
                "",
                f"- Objective: {task.objective}",
                f"- Rationale: {task.rationale}",
                f"- Depends On: {', '.join(task.depends_on) if task.depends_on else 'none'}",
                f"- Run If: {task.run_if or 'always'}",
                f"- Allow Network: {str(task.allow_network).lower()}",
                f"- Allow Docker: {str(task.allow_docker).lower()}",
                f"- Requires Manual Approval: {str(task.requires_manual_approval).lower()}",
                "- Actions:",
                *_indented(task.actions),
                "- Expected Outputs:",
                *_indented(task.expected_outputs),
                "- Acceptance Criteria:",
                *_indented(task.acceptance_criteria),
                "- Stop Conditions:",
                *_indented(task.stop_conditions),
                "- Allowed Write Paths:",
                *_indented(task.allowed_write_paths or ["No source writes authorized by this task."]),
                "",
            ]
        )
    return lines


def _task_contract_lines(output: PlannerOutput) -> list[str]:
    lines = []
    for task in output.tasks:
        lines.append(f"- `{task.task_id}`: writes={json.dumps(task.allowed_write_paths, ensure_ascii=False)}; network={task.allow_network}; docker={task.allow_docker}; manual_approval={task.requires_manual_approval}")
    lines.append("- Input contract checks include query files, ground truth files, vector index/Kuzu database, config parameters, and benchmark CLI parameters.")
    lines.append("- When the task explicitly allows network acquisition, Engineer may inspect official sources with git ls-remote, git clone, curl, wget, or equivalent low-cost metadata commands.")
    return lines or ["- No executable task authorized."]


def _blocking_issue_lines(output: PlannerOutput) -> list[str]:
    if not output.blocking_issues:
        return ["- None."]
    return [
        f"- `{issue.issue_id}` [{issue.severity}/{issue.category}]: {issue.description} Evidence: {issue.evidence_source}. Resolution: {issue.suggested_resolution or 'not specified'}"
        for issue in output.blocking_issues
    ]


def _evidence_lines(items) -> list[str]:
    if not items:
        return ["- None."]
    return [f"- [{item.status}] {item.claim} (source: {item.source}) {item.notes}".rstrip() for item in items]


def _planner_note_lines(items) -> list[str]:
    lines = [str(item) for item in items if str(item).strip()]
    user_lines = [item for item in lines if "User Guidance" in item or "user_provided_hint" in item or "user_hints" in item]
    if not user_lines:
        return [
            "- User Guidance is optional user-provided context. Treat source/data/model URLs as hints only, not verified paper evidence."
        ]
    return [f"- {item}" for item in user_lines]


def _stop_condition_lines(output: PlannerOutput) -> list[str]:
    seen: list[str] = []
    for task in output.tasks:
        for condition in task.stop_conditions:
            if condition not in seen:
                seen.append(condition)
    return _bullets(seen or ["Stop if required inputs, budget, or manual approval are missing."])


def _allowed_files(output: PlannerOutput) -> list[str]:
    paths: list[str] = []
    for task in output.tasks:
        for path in task.allowed_write_paths:
            if path not in paths:
                paths.append(path)
    return _bullets(paths or [".r2a/results/**", ".r2a/logs/**"])


def _acceptance_lines(output: PlannerOutput) -> list[str]:
    lines: list[str] = []
    for task in output.tasks:
        for criterion in task.acceptance_criteria:
            if criterion not in lines:
                lines.append(criterion)
    return _bullets(lines or ["Task evidence is recorded truthfully."])


def _paper_card_summary(output: PlannerOutput) -> list[str]:
    for item in output.evidence_used:
        if "Paper Reproduction Card Summary:" in item.notes:
            return [item.notes.split("Paper Reproduction Card Summary:", 1)[1].strip()]
    return ["No structured reproduction-card excerpt was available."]


def _previous_fix_lines(output: PlannerOutput) -> list[str]:
    if output.planning_mode != "iterative_progress":
        return ["No previous iteration feedback applies."]
    lines = []
    for issue in output.blocking_issues:
        lines.append(f"- {issue.description}")
    for task in output.tasks:
        for action in task.actions:
            if action not in lines:
                lines.append(f"- {action}")
    return lines or ["- No structured Reviewer fixes were provided."]


def _bullets(items) -> list[str]:
    return [f"- {item}" for item in items]


def _indented(items) -> list[str]:
    return [f"  - {item}" for item in items]
