from __future__ import annotations

from typing import Literal

PROJECT_NAME = "R2A"
PROJECT_FULL_NAME = "Research Reproduction Agent"
ARTIFACT_DIRNAME = ".r2a"
DEFAULT_CODEX_EXECUTABLE = "codex"
DEFAULT_CLAUDE_EXECUTABLE = "ccr"

ExecutorName = Literal["codex", "claude", "openclaw", "mock", "shell"]

SUPPORTED_PAPER_BACKENDS = ("preprocess", "template", "ai_reader", "claude_reader", "openclaw_reader")
SUPPORTED_PLANNER_BACKENDS = ("template", "mock", "ccr_text", "command", "openai_compatible", "anthropic", "codex", "claude", "openclaw")
SUPPORTED_ENGINEER_EXECUTORS = ("shell", "codex", "claude", "openclaw", "mock")
SUPPORTED_MANAGER_BACKENDS = ("rules", "codex_review", "claude_review", "openclaw_review")
SUPPORTED_REVIEWER_BACKENDS = ("rules", "codex", "claude", "openclaw")
SUPPORTED_FINAL_WRITER_BACKENDS = ("template", "openclaw")

REPORT_FILENAMES = {
    "paper_text": "PAPER_TEXT.md",
    "paper_pages": "PAPER_PAGES.md",
    "paper_sections": "PAPER_SECTIONS.md",
    "paper_captions": "PAPER_CAPTIONS.md",
    "paper_context": "PAPER_CONTEXT.md",
    "paper_reproduction_card": "PAPER_REPRODUCTION_CARD.md",
    "paper_figures_tables": "PAPER_FIGURES_TABLES.md",
    "paper_parse_quality": "PAPER_PARSE_QUALITY.md",
    "paper_analysis": "PAPER_ANALYSIS_CN.md",
    "paper": "PAPER_BRIEF.md",
    "paper_evidence": "PAPER_EVIDENCE.md",
    "paper_output": "PAPER_OUTPUT.json",
    "planner_output": "PLANNER_OUTPUT.json",
    "task": "TASK_SPEC.md",
    "experiment_contract": "EXPERIMENT_CONTRACT.md",
    "execution": "EXECUTION_REPORT.md",
    "check": "CHECK_REPORT.md",
    "manager_decision": "MANAGER_DECISION.json",
    "review": "REVIEW_REPORT.md",
    "review_verdict": "REVIEW_VERDICT.json",
    "evidence_decision": "EVIDENCE_DECISION.json",
    "final_decision": "FINAL_DECISION.json",
    "final_narrative": "FINAL_NARRATIVE_CN.md",
    "final_writer_metadata": "FINAL_WRITER_METADATA.json",
    "review_feedback": "REVIEW_FEEDBACK.json",
    "source_acquisition": "SOURCE_ACQUISITION.json",
    "source_inspection": "SOURCE_INSPECTION.json",
    "user_hints": "USER_HINTS.json",
    "next_planner_context": "NEXT_PLANNER_CONTEXT.json",
    "manager_codex_review": "MANAGER_CODEX_REVIEW.md",
    "final": "FINAL_REPORT.md",
    "experiment_state": "EXPERIMENT_STATE.md",
}
