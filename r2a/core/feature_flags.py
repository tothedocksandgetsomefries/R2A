from __future__ import annotations

import os
from typing import Final

# Minimal workflow reset: complex paths stay in code but are off unless explicitly enabled.
MINIMAL_WORKFLOW_ENV = "R2A_MINIMAL_WORKFLOW"

FEATURE_UI_POLLING: Final = "ui_polling"
FEATURE_UI_HEARTBEAT: Final = "ui_heartbeat"
FEATURE_RESTORE_PREVIOUS_RUN: Final = "restore_previous_run"
FEATURE_PLANNER_STAGE_GUARD: Final = "planner_stage_guard"
FEATURE_PAPER_AI_READER: Final = "paper_ai_reader"
FEATURE_PAPER_CLAUDE_READER: Final = "paper_claude_reader"
FEATURE_PLANNER_TOOL_CALL: Final = "planner_tool_call"
FEATURE_REVIEWER_CCR: Final = "reviewer_ccr"
FEATURE_AUTO_ITERATE: Final = "auto_iterate"
FEATURE_SYNTHETIC_DEMO_AUTO_APPROVE: Final = "synthetic_demo_auto_approve"
FEATURE_OFFICIAL_DOWNLOAD_AUTO_APPROVE: Final = "official_download_auto_approve"
FEATURE_DOCKER_RUNNER: Final = "docker_runner"
FEATURE_REAL_ENGINEER: Final = "real_engineer"
FEATURE_PLANNER_SILENT_TEMPLATE_FALLBACK: Final = "planner_silent_template_fallback"
FEATURE_IMPLICIT_BACKEND_FALLBACK: Final = "implicit_backend_fallback"

_DEFAULTS_WHEN_MINIMAL: dict[str, bool] = {
    FEATURE_UI_POLLING: True,
    FEATURE_UI_HEARTBEAT: False,
    FEATURE_RESTORE_PREVIOUS_RUN: False,
    FEATURE_PLANNER_STAGE_GUARD: False,
    FEATURE_PAPER_AI_READER: False,
    FEATURE_PAPER_CLAUDE_READER: False,
    FEATURE_PLANNER_TOOL_CALL: False,
    FEATURE_REVIEWER_CCR: False,
    FEATURE_AUTO_ITERATE: False,
    FEATURE_SYNTHETIC_DEMO_AUTO_APPROVE: False,
    FEATURE_OFFICIAL_DOWNLOAD_AUTO_APPROVE: False,
    FEATURE_DOCKER_RUNNER: False,
    FEATURE_REAL_ENGINEER: False,
    FEATURE_PLANNER_SILENT_TEMPLATE_FALLBACK: False,
    FEATURE_IMPLICIT_BACKEND_FALLBACK: False,
}

_OVERRIDE_ENV = {
    FEATURE_UI_POLLING: "R2A_FEATURE_UI_POLLING",
    FEATURE_UI_HEARTBEAT: "R2A_FEATURE_UI_HEARTBEAT",
    FEATURE_RESTORE_PREVIOUS_RUN: "R2A_FEATURE_RESTORE_PREVIOUS_RUN",
    FEATURE_PLANNER_STAGE_GUARD: "R2A_FEATURE_PLANNER_STAGE_GUARD",
    FEATURE_PAPER_AI_READER: "R2A_FEATURE_PAPER_AI_READER",
    FEATURE_PAPER_CLAUDE_READER: "R2A_FEATURE_PAPER_CLAUDE_READER",
    FEATURE_PLANNER_TOOL_CALL: "R2A_FEATURE_PLANNER_TOOL_CALL",
    FEATURE_REVIEWER_CCR: "R2A_FEATURE_REVIEWER_CCR",
    FEATURE_AUTO_ITERATE: "R2A_FEATURE_AUTO_ITERATE",
    FEATURE_SYNTHETIC_DEMO_AUTO_APPROVE: "R2A_FEATURE_SYNTHETIC_DEMO_AUTO_APPROVE",
    FEATURE_OFFICIAL_DOWNLOAD_AUTO_APPROVE: "R2A_FEATURE_OFFICIAL_DOWNLOAD_AUTO_APPROVE",
    FEATURE_DOCKER_RUNNER: "R2A_FEATURE_DOCKER_RUNNER",
    FEATURE_REAL_ENGINEER: "R2A_FEATURE_REAL_ENGINEER",
    FEATURE_PLANNER_SILENT_TEMPLATE_FALLBACK: "R2A_FEATURE_PLANNER_SILENT_TEMPLATE_FALLBACK",
    FEATURE_IMPLICIT_BACKEND_FALLBACK: "R2A_FEATURE_IMPLICIT_BACKEND_FALLBACK",
}


def minimal_workflow_mode() -> bool:
    raw = os.environ.get(MINIMAL_WORKFLOW_ENV, "1").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def feature_enabled(name: str) -> bool:
    env_key = _OVERRIDE_ENV.get(name)
    if env_key:
        raw = os.environ.get(env_key, "").strip().lower()
        if raw in {"1", "true", "yes", "on"}:
            return True
        if raw in {"0", "false", "no", "off"}:
            return False
    if minimal_workflow_mode():
        return _DEFAULTS_WHEN_MINIMAL.get(name, False)
    return True


def minimal_workflow_defaults() -> dict[str, str | int | bool]:
    return {
        "paper_backend": "openclaw_reader",
        "planner_backend": "openclaw",
        "engineer_executor": "openclaw",
        "manager_backend": "openclaw_review",
        "reviewer_backend": "openclaw",
        "auto_iterate": False,
        "auto_approve": False,
        "max_iterations": 1,
    }
