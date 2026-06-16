from __future__ import annotations

import json
from pathlib import Path
import re
import shutil
import time

from r2a.core.paths import artifact_dir, report_path, require_repo_dir
from r2a.core.paper_schema import ArtifactReference, DatasetReference, PaperMetadata, PaperOutput
from r2a.core.state import R2AState
from r2a.tools.markdown_utils import bullet_list
from r2a.tools.paper_structure import (
    build_figures_tables_report,
    build_parse_quality_report,
    build_reproduction_card,
    summarize_structure,
)
from r2a.tools.pdf_extract import extract_pdf_text, extract_pdf_text_structured, pages_to_markdown
from r2a.tools.prompt_loader import load_prompt, render_prompt
from r2a.tools.report_writer import write_report
from r2a.tools.stage_env import build_stage_env
from r2a.tools import claude_stage_runner, openclaw_stage_runner
from r2a.tools.wsl import windows_to_wsl_path

PAPER_TEXT_LIMIT = 200000
PAPER_CONTEXT_EXCERPT_LIMIT = 5000
CAPTION_ONLY_LOW_CONFIDENCE_WARNING = (
    "Some paper figures were parsed as caption-only; exact plotted values were not structurally extracted. "
    "This may affect figure-level numeric alignment evidence, but does not restrict Planner scope."
)
LOCAL_FALLBACK_LOW_CONFIDENCE_WARNING = (
    "Paper quality gate marked LOW_CONFIDENCE because local fallback or incomplete extraction was used; "
    "treat extracted paper facts as verification/discovery context until a structured paper bundle is available."
)


def run_paper_agent(state: R2AState, *, force: bool = True) -> R2AState:
    backend = state.get("paper_backend", "preprocess")
    if backend == "codex":
        raise ValueError("Legacy Paper Codex backend is disabled. Use paper_backend=ai_reader, claude_reader, or local paper preprocess instead.")
    if backend == "claude_reader":
        return run_paper_claude_reader(state, force=force)
    if backend == "openclaw_reader":
        return run_paper_openclaw_reader(state, force=force)
    if backend == "ai_reader":
        return run_paper_ai_reader(state, force=force)
    return generate_paper_brief(state, force=force)


def run_paper_claude_reader(state: R2AState, *, force: bool = True) -> R2AState:
    """Run Paper stage with Claude Code reader backend.

    This function invokes the existing claude_stage_runner to perform
    paper reading via Claude Code, generating the same artifacts as
    the preprocess backend but with potentially higher quality.
    """
    repo = require_repo_dir(state["repo_path"])
    language = state.get("language", "en")
    language_name = "Simplified Chinese" if language == "zh" else "English"
    paper_path = state.get("paper_path", "")
    goal = state.get("goal", "")
    extra_context = state.get("extra_context", "")

    # Prepare paper text extraction for Claude Code to read
    extraction = _preprocess_paper_text(paper_path)
    text_output = report_path(repo, "paper_text")
    pages_output = report_path(repo, "paper_pages")
    sections_output = report_path(repo, "paper_sections")
    captions_output = report_path(repo, "paper_captions")

    # Write pre-extracted text files that Claude Code can read
    _write_structured_paper_inputs(
        pages_output=pages_output,
        sections_output=sections_output,
        captions_output=captions_output,
        paper_path=paper_path or "No paper uploaded.",
        extraction=extraction,
        force=force,
    )
    write_report(
        text_output,
        "PAPER_TEXT.md",
        {
            "paper_path": paper_path or "No paper uploaded.",
            "extraction_status": extraction["status"],
            "extraction_backend": extraction["backend"],
            "pages_checked": extraction["pages_checked"],
            "text_length": extraction["text_length"],
            "truncated": extraction["truncated"],
            "paper_text": extraction["text"] or extraction["excerpt_fallback"],
            "notes": extraction["notes"],
        },
        force=force,
    )

    # Define staging directory for Paper stage outputs
    staging_dir = artifact_dir(repo) / "staging" / "paper"
    staging_dir.mkdir(parents=True, exist_ok=True)

    # Allowed outputs for Paper Claude reader
    allowed_outputs = [
        ".r2a/PAPER_CONTEXT.md",
        ".r2a/PAPER_BRIEF.md",
        ".r2a/PAPER_EVIDENCE.md",
        ".r2a/PAPER_REPRODUCTION_CARD.md",
        ".r2a/PAPER_FIGURES_TABLES.md",
        ".r2a/PAPER_PARSE_QUALITY.md",
        ".r2a/PAPER_ANALYSIS_CN.md",
        ".r2a/logs/",
    ]

    # Build prompt for Paper AI Reader
    prompt = render_prompt(
        "paper_ai_reader",
        {
            "repo_path": str(repo),
            "language": language,
            "language_name": language_name,
            "goal": goal,
            "extra_context": extra_context,
            "original_paper_path": paper_path,
            "accessible_paper_path": paper_path,  # Claude Code can access the same path
            "paper_text_path": str(text_output),
            "paper_pages_path": str(pages_output),
            "paper_sections_path": str(sections_output),
            "paper_captions_path": str(captions_output),
            "paper_parse_quality_path": str(report_path(repo, "paper_parse_quality")),
            "iteration": str(state.get("iteration", 1)),
        },
    )

    # Build stage environment
    env = build_stage_env(
        stage="paper",
        backend="claude_reader",
        stage_api_keys=state.get("stage_api_keys"),
        stage_api_key_env_vars=state.get("stage_api_key_env_vars"),
    )

    # Run Claude Code stage
    result = claude_stage_runner.run_claude_stage(
        repo,
        "paper",
        prompt,
        allowed_outputs,
        iteration=None,
        timeout=int(state.get("codex_stage_timeout", state.get("timeout", 10800))),
        claude_executable_path=state.get("claude_executable_path"),
        language=language,
        env=env,
    )

    warnings = list(state.get("warnings", []))

    # Check for failures
    if not result.get("success"):
        error_msg = result.get("error", "Claude Code Paper reader failed")
        return {
            **state,
            "paper_backend": "claude_reader",
            "paper_quality": "FAILED",
            "warnings": [*warnings, f"Paper Claude reader failed: {error_msg}"],
            "fallback_used": False,
            "paper_claude_reader_failed": True,
            "paper_claude_reader_error": error_msg,
        }

    if result.get("unexpected_modifications"):
        warnings.append(f"Paper Claude reader modified unexpected files: {result['unexpected_modifications']}")

    # Verify required outputs exist
    brief_path = report_path(repo, "paper")
    evidence_path = report_path(repo, "paper_evidence")
    context_path = report_path(repo, "paper_context")

    if not brief_path.exists():
        return {
            **state,
            "paper_backend": "claude_reader",
            "paper_quality": "FAILED",
            "warnings": [*warnings, "Paper Claude reader did not generate PAPER_BRIEF.md"],
            "fallback_used": False,
            "paper_claude_reader_failed": True,
            "paper_claude_reader_error": "Missing PAPER_BRIEF.md output",
        }

    # Parse quality assessment
    parse_quality_path = report_path(repo, "paper_parse_quality")
    parse_quality_text = _read(parse_quality_path) if parse_quality_path.exists() else ""
    paper_quality = "PARTIAL"
    if "LOW_CONFIDENCE" in parse_quality_text or "caption_only" in parse_quality_text:
        paper_quality = "LOW_CONFIDENCE"
        warnings.append(CAPTION_ONLY_LOW_CONFIDENCE_WARNING)

    # Return updated state with Paper outputs
    return {
        **state,
        "paper_backend": "claude_reader",
        "paper_quality": paper_quality,
        "warnings": warnings,
        "fallback_used": False,
        "paper_text_path": str(text_output),
        "paper_pages_path": str(pages_output),
        "paper_sections_path": str(sections_output),
        "paper_captions_path": str(captions_output),
        "paper_context_path": str(context_path),
        "paper_brief_path": str(brief_path),
        "paper_evidence_path": str(evidence_path),
        "paper_text_excerpt": _excerpt(_read(text_output), PAPER_CONTEXT_EXCERPT_LIMIT),
        "paper_context_excerpt": _read(context_path)[:PAPER_CONTEXT_EXCERPT_LIMIT] if context_path.exists() else "",
        "paper_extraction_status": extraction["status"],
        "paper_text_length": extraction["text_length"],
    }


def run_paper_openclaw_reader(state: R2AState, *, force: bool = True) -> R2AState:
    repo = require_repo_dir(state["repo_path"])
    language = state.get("language", "en")
    language_name = "Simplified Chinese" if language == "zh" else "English"
    paper_path = state.get("paper_path", "")
    goal = state.get("goal", "")
    extra_context = state.get("extra_context", "")

    extraction = _preprocess_paper_text(paper_path)
    text_output = report_path(repo, "paper_text")
    pages_output = report_path(repo, "paper_pages")
    sections_output = report_path(repo, "paper_sections")
    captions_output = report_path(repo, "paper_captions")
    parse_quality_path = report_path(repo, "paper_parse_quality")

    _write_structured_paper_inputs(
        pages_output=pages_output,
        sections_output=sections_output,
        captions_output=captions_output,
        paper_path=paper_path or "No paper uploaded.",
        extraction=extraction,
        force=force,
    )
    write_report(
        text_output,
        "PAPER_TEXT.md",
        {
            "paper_path": paper_path or "No paper uploaded.",
            "extraction_status": extraction["status"],
            "extraction_backend": extraction["backend"],
            "pages_checked": extraction["pages_checked"],
            "text_length": extraction["text_length"],
            "truncated": extraction["truncated"],
            "paper_text": extraction["text"] or extraction["excerpt_fallback"],
            "notes": extraction["notes"],
        },
        force=force,
    )

    allowed_outputs = [
        ".r2a/PAPER_CONTEXT.md",
        ".r2a/PAPER_BRIEF.md",
        ".r2a/PAPER_EVIDENCE.md",
        ".r2a/PAPER_REPRODUCTION_CARD.md",
        ".r2a/PAPER_FIGURES_TABLES.md",
        ".r2a/PAPER_PARSE_QUALITY.md",
        ".r2a/PAPER_ANALYSIS_CN.md",
        ".r2a/logs/",
    ]
    prompt = render_prompt(
        "paper_ai_reader",
        {
            "repo_path": windows_to_wsl_path(repo),
            "language": language,
            "language_name": language_name,
            "goal": goal,
            "extra_context": extra_context,
            "original_paper_path": windows_to_wsl_path(paper_path) if paper_path else "",
            "accessible_paper_path": windows_to_wsl_path(paper_path) if paper_path else "",
            "paper_text_path": windows_to_wsl_path(text_output),
            "paper_pages_path": windows_to_wsl_path(pages_output),
            "paper_sections_path": windows_to_wsl_path(sections_output),
            "paper_captions_path": windows_to_wsl_path(captions_output),
            "paper_parse_quality_path": windows_to_wsl_path(parse_quality_path),
            "iteration": str(state.get("iteration", 1)),
        },
    )
    iteration = int(state.get("iteration", 1))
    staging_dir = artifact_dir(repo) / "staging" / "paper" / f"iter_{iteration:03d}" / "attempt_001"
    staging_dir.mkdir(parents=True, exist_ok=True)
    input_path = staging_dir / "OPENCLAW_INPUT.md"
    input_path.write_text(_build_openclaw_paper_input(prompt, state=state), encoding="utf-8")
    env = build_stage_env(
        stage="paper",
        backend="openclaw_reader",
        stage_api_keys=state.get("stage_api_keys"),
        stage_api_key_env_vars=state.get("stage_api_key_env_vars"),
    )
    stage_config = openclaw_stage_runner.openclaw_stage_model_config_from_state(state, "paper")
    result = openclaw_stage_runner.run_openclaw_stage(
        repo,
        "paper",
        input_path,
        allowed_outputs,
        session_key=_openclaw_paper_session_key(state, iteration),
        iteration=iteration,
        timeout=int(state.get("codex_stage_timeout", state.get("timeout", 10800))),
        openclaw_executable_path=state.get("openclaw_executable_path"),
        openclaw_config_path=state.get("openclaw_config_path"),
        wsl_distro=str(state.get("wsl_distro", "Ubuntu")),
        env=env,
        provider=stage_config.get("provider") or state.get("openclaw_provider"),
        model=stage_config.get("model") or state.get("openclaw_model"),
        runner=stage_config.get("runner") or state.get("openclaw_runner"),
        agent=stage_config.get("agent") or state.get("openclaw_agent"),
    )
    warnings = list(state.get("warnings", []))
    if not result.get("success"):
        error_msg = result.get("error", "OpenClaw Paper reader failed")
        return _openclaw_reader_local_fallback(
            state,
            reason=f"Paper OpenClaw reader failed: {error_msg}",
            warnings=warnings,
            force=force,
        )
    if result.get("unexpected_modifications"):
        warnings.append(f"Paper OpenClaw reader modified unexpected files: {result['unexpected_modifications']}")

    required = {
        "paper_context_path": report_path(repo, "paper_context"),
        "paper_brief_path": report_path(repo, "paper"),
        "paper_evidence_path": report_path(repo, "paper_evidence"),
        "paper_reproduction_card_path": report_path(repo, "paper_reproduction_card"),
        "paper_figures_tables_path": report_path(repo, "paper_figures_tables"),
        "paper_parse_quality_path": parse_quality_path,
        "paper_analysis_path": report_path(repo, "paper_analysis"),
    }
    missing = [label for label, path in required.items() if not path.exists() or path.stat().st_size == 0]
    if missing:
        return _openclaw_reader_local_fallback(
            state,
            reason=f"Paper OpenClaw reader missing required output(s): {', '.join(missing)}",
            warnings=warnings,
            force=force,
        )

    parse_quality_text = _read(parse_quality_path)
    paper_quality = "PARTIAL"
    if "LOW_CONFIDENCE" in parse_quality_text or "caption_only" in parse_quality_text:
        paper_quality = "LOW_CONFIDENCE"
        warnings.append(CAPTION_ONLY_LOW_CONFIDENCE_WARNING)

    return {
        **state,
        "paper_backend": "openclaw_reader",
        "paper_quality": paper_quality,
        "warnings": warnings,
        "fallback_used": False,
        "paper_text_path": str(text_output),
        "paper_pages_path": str(pages_output),
        "paper_sections_path": str(sections_output),
        "paper_captions_path": str(captions_output),
        "paper_text_excerpt": _excerpt(_read(text_output), PAPER_CONTEXT_EXCERPT_LIMIT),
        "paper_extraction_status": extraction["status"],
        "paper_text_length": extraction["text_length"],
        **{key: str(path) for key, path in required.items()},
        "paper_context_excerpt": _read(required["paper_context_path"])[:PAPER_CONTEXT_EXCERPT_LIMIT],
    }


def _openclaw_reader_local_fallback(
    state: R2AState,
    *,
    reason: str,
    warnings: list[str],
    force: bool = True,
) -> R2AState:
    fallback = generate_paper_brief(
        {
            **state,
            "paper_backend": "local_preprocess_fallback",
            "warnings": [
                *warnings,
                reason,
                "Paper OpenClaw reader did not produce a complete structured bundle; local preprocess fallback generated evidence-limited Paper artifacts for Planner.",
            ],
            "paper_openclaw_reader_failed": True,
            "paper_openclaw_reader_error": reason,
        },
        force=force,
    )
    repo = require_repo_dir(state["repo_path"])
    for path in _paper_output_paths(repo).values():
        if path.suffix.lower() == ".md":
            _append_note(
                path,
                "Paper OpenClaw reader was incomplete or failed; local preprocess fallback generated this evidence-limited artifact. Planner must not treat extracted paper facts as verified source/data evidence.",
            )
    return {
        **fallback,
        "paper_backend": "local_preprocess_fallback",
        "paper_backend_requested": "openclaw_reader",
        "paper_quality": "LOW_CONFIDENCE",
        "fallback_used": True,
        "paper_openclaw_reader_failed": True,
        "paper_openclaw_reader_error": reason,
    }


def _build_openclaw_paper_input(prompt: str, *, state: R2AState) -> str:
    config = openclaw_stage_runner.openclaw_config_from_state(state, stage="paper")
    return (
        "# R2A Paper OpenClaw Stage\n\n"
        "This file is the only long instruction bundle for the OpenClaw Paper reader stage.\n"
        "Use the pre-extracted paper reading aids; do not modify them.\n\n"
        "Backend contract:\n"
        f"- provider: `{config['provider']}`\n"
        f"- model: `{config['model']}`\n"
        f"- runner: `{config['runner']}`\n"
        f"- agent: `{config['agent']}`\n"
        "- fallbackUsed: `false`\n\n"
        "When finished, return raw JSON only, without Markdown fences:\n"
        '{"status":"PASS","stage":"paper"}\n\n'
        "---\n\n"
        f"{prompt}\n"
    )


def _openclaw_paper_session_key(state: R2AState, iteration: int) -> str:
    run_id = str(state.get("run_id", "run") or "run")
    safe_run_id = re.sub(r"[^A-Za-z0-9_.-]+", "-", run_id).strip("-") or "run"
    return f"r2a-paper-{safe_run_id}-{int(iteration)}-{int(time.time())}"


def run_paper_ai_reader(state: R2AState, *, force: bool = True) -> R2AState:
    backend = state.get("paper_backend", "ai_reader")
    warning = (
        f"Paper backend `{backend}` is now text-only; Claude Code/Codex Tool Call reader is disabled. "
        "Local preprocess fallback used; extracted paper facts are verification/discovery context until a structured paper bundle is available."
    )
    fallback = generate_paper_brief(
        {
            **state,
            "paper_backend": "local_preprocess_fallback",
            "warnings": [*state.get("warnings", []), warning],
            "paper_ai_reader_failed": True,
        },
        force=force,
    )
    repo = require_repo_dir(state["repo_path"])
    for path in _paper_output_paths(repo).values():
        if path.suffix.lower() == ".md":
            _append_note(
                path,
                "Paper AI summarizer was not used; local fallback used. Treat extracted paper facts as verification/discovery context until a structured paper bundle is available.",
            )
    return {
        **fallback,
        "paper_backend": "local_preprocess_fallback",
        "paper_quality": "LOW_CONFIDENCE",
        "fallback_used": True,
    }


def generate_paper_brief(state: R2AState, *, force: bool = True) -> R2AState:
    repo = require_repo_dir(state["repo_path"])
    text_output = report_path(repo, "paper_text")
    pages_output = report_path(repo, "paper_pages")
    sections_output = report_path(repo, "paper_sections")
    captions_output = report_path(repo, "paper_captions")
    context_output = report_path(repo, "paper_context")
    paper_output_json = report_path(repo, "paper_output")
    card_output = report_path(repo, "paper_reproduction_card")
    figures_tables_output = report_path(repo, "paper_figures_tables")
    parse_quality_output = report_path(repo, "paper_parse_quality")
    analysis_output = report_path(repo, "paper_analysis")
    brief_output = report_path(repo, "paper")
    evidence_output = report_path(repo, "paper_evidence")
    prompt = load_prompt("paper_agent")
    language = state.get("language", "en")
    goal = state.get("goal", "")
    paper_path = state.get("paper_path", "")
    extra_context = state.get("extra_context", "")
    extraction = _preprocess_paper_text(paper_path)
    structure = summarize_structure(extraction["text"])
    card_body = build_reproduction_card(
        text=extraction["text"],
        extraction_status=extraction["status"],
        text_length=extraction["text_length"],
        truncated=extraction["truncated"],
        goal=goal,
        paper_path=paper_path,
    )
    figures_tables_body = build_figures_tables_report(extraction["text"])
    parse_quality_body = build_parse_quality_report(extraction["text"])
    paper_output = _build_paper_output(structure, extraction, parse_quality_body, state)
    paper_text_excerpt = _excerpt(extraction["text"], PAPER_CONTEXT_EXCERPT_LIMIT) or extraction["excerpt_fallback"]
    paper_note, extracted_evidence = _extract_available_evidence(paper_path, goal, extra_context, extraction)

    write_report(
        text_output,
        "PAPER_TEXT.md",
        {
            "paper_path": paper_path or "No paper uploaded.",
            "extraction_status": extraction["status"],
            "extraction_backend": extraction["backend"],
            "pages_checked": extraction["pages_checked"],
            "text_length": extraction["text_length"],
            "truncated": extraction["truncated"],
            "paper_text": extraction["text"] or extraction["excerpt_fallback"],
            "notes": extraction["notes"],
        },
        force=force,
    )
    _write_structured_paper_inputs(
        pages_output=pages_output,
        sections_output=sections_output,
        captions_output=captions_output,
        paper_path=paper_path or "No paper uploaded.",
        extraction=extraction,
        force=force,
    )
    write_report(
        card_output,
        "PAPER_REPRODUCTION_CARD.md",
        {"card_body": card_body},
        force=force,
    )
    write_report(
        figures_tables_output,
        "PAPER_FIGURES_TABLES.md",
        {"figures_tables_body": figures_tables_body},
        force=force,
    )
    write_report(
        parse_quality_output,
        "PAPER_PARSE_QUALITY.md",
        {"parse_quality_body": parse_quality_body},
        force=force,
    )
    write_report(
        context_output,
        "PAPER_CONTEXT.md",
        {
            "paper_path": paper_path or "No paper uploaded.",
            "extraction_status": extraction["status"],
            "text_length": extraction["text_length"],
            "truncated": extraction["truncated"],
            "goal": goal or "No user guidance provided.",
            "paper_text_excerpt": _build_context_excerpt(paper_text_excerpt, structure, card_output, figures_tables_output, parse_quality_output),
        },
        force=force,
    )
    paper_output_json.write_text(json.dumps(paper_output.model_dump(), indent=2, ensure_ascii=False), encoding="utf-8")
    write_report(
        brief_output,
        "PAPER_BRIEF.md",
        {
            "repo_path": repo,
            "paper_topic": _topic(goal),
            "user_goal": goal or _t(language, "MVP 中不可用。", "Not available in MVP."),
            "problem": _goal_inferred(goal, _t(language, "只能从本地 PDF 文本抽取和用户上下文保守推断。", "Only conservatively inferred from local PDF text extraction and user context."), language),
            "method_summary": _structure_summary(structure),
            "baselines": _format_named_items(structure["baselines"], "name"),
            "datasets": _format_named_items(structure["datasets"], "name"),
            "metrics": bullet_list(structure["metrics"] or ["Evidence Gap: metrics not found in extracted text."]),
            "reproduction_requirements": _goal_inferred(goal, _t(language, "具体复现要求仍需后续 Planner 保守化。", "Specific reproduction requirements must be conservatively planned downstream."), language),
            "reproduction_gaps": _t(language, "Evidence Gap: 本地 PDF 文本抽取可能遗漏表格、图片、公式、扫描文本和双栏顺序。", "Evidence Gap: local PDF text extraction may miss tables, figures, formulas, scanned text, and two-column ordering."),
            "known_limitations": bullet_list(_paper_limitations(language)),
            "confidence": "Low",
            "prompt_summary": _prompt_summary(prompt),
            "paper_source": _paper_source(paper_path),
            "extra_context": extra_context or _t(language, "MVP 中不可用。", "Not available in MVP."),
            "mvp_notes": "\n".join(
                [
                    paper_note,
                    f"PAPER_REPRODUCTION_CARD.md: {card_output}",
                    f"PAPER_FIGURES_TABLES.md: {figures_tables_output}",
                    f"PAPER_PARSE_QUALITY.md: {parse_quality_output}",
                    _url_summary(structure),
                ]
            ),
        },
        force=force,
    )
    write_report(
        evidence_output,
        "PAPER_EVIDENCE.md",
        {
            "evidence_sources": bullet_list(_evidence_sources(paper_path, goal, extra_context)),
            "extracted_evidence": "\n\n".join([_structured_evidence(structure), extracted_evidence]),
            "missing_evidence": bullet_list(_missing_evidence(language)),
            "notes": paper_note,
        },
        force=force,
    )
    _write_paper_analysis_cn(repo, force=force)
    warnings = list(state.get("warnings", []))
    if paper_output.parse_quality == "LOW_CONFIDENCE":
        warnings.append(
            CAPTION_ONLY_LOW_CONFIDENCE_WARNING
            if _parse_quality_indicates_caption_only(parse_quality_body)
            else LOCAL_FALLBACK_LOW_CONFIDENCE_WARNING
        )
    return {
        **state,
        "warnings": warnings,
        "paper_backend": state.get("paper_backend", "preprocess"),
        "paper_quality": paper_output.parse_quality,
        "paper_output_path": str(paper_output_json),
        "paper_text_path": str(text_output),
        "paper_pages_path": str(pages_output),
        "paper_sections_path": str(sections_output),
        "paper_captions_path": str(captions_output),
        "paper_context_path": str(context_output),
        "paper_reproduction_card_path": str(card_output),
        "paper_figures_tables_path": str(figures_tables_output),
        "paper_parse_quality_path": str(parse_quality_output),
        "paper_analysis_path": str(analysis_output),
        "paper_brief_path": str(brief_output),
        "paper_evidence_path": str(evidence_output),
        "paper_text_excerpt": paper_text_excerpt,
        "paper_context_excerpt": _read(context_output)[:PAPER_CONTEXT_EXCERPT_LIMIT],
        "paper_reproduction_card_excerpt": _read(card_output)[:PAPER_CONTEXT_EXCERPT_LIMIT],
        "paper_figures_tables_excerpt": _read(figures_tables_output)[:PAPER_CONTEXT_EXCERPT_LIMIT],
        "paper_parse_quality_excerpt": _read(parse_quality_output)[:PAPER_CONTEXT_EXCERPT_LIMIT],
        "paper_analysis_excerpt": _read(analysis_output)[:PAPER_CONTEXT_EXCERPT_LIMIT],
        "paper_extraction_status": extraction["status"],
        "paper_text_length": extraction["text_length"],
    }


def _preprocess_paper_text(paper_path: str) -> dict:
    if not paper_path:
        return {
            "status": "no paper uploaded",
            "text": "",
            "pages_checked": 0,
            "text_length": 0,
            "truncated": False,
            "backend": "none",
            "pages_body": "Not available.",
            "sections_body": "Not available.",
            "captions_body": "Not available.",
            "notes": "No paper uploaded. Paper context is generated from user guidance only.",
            "excerpt_fallback": "No paper uploaded.",
        }
    path = Path(paper_path)
    if not path.exists():
        return {
            "status": "paper file missing",
            "text": "",
            "pages_checked": 0,
            "text_length": 0,
            "truncated": False,
            "backend": "missing",
            "pages_body": "Not available.",
            "sections_body": "Not available.",
            "captions_body": "Not available.",
            "notes": f"Paper file does not exist: {path}",
            "excerpt_fallback": f"Paper file does not exist: {path}",
        }
    if path.suffix.lower() == ".pdf":
        structured = extract_pdf_text_structured(path, max_chars=PAPER_TEXT_LIMIT + 1)
        if structured.ok:
            text = structured.text
            truncated = structured.truncated or len(text) > PAPER_TEXT_LIMIT
            if len(text) > PAPER_TEXT_LIMIT:
                text = text[:PAPER_TEXT_LIMIT].rstrip()
            return {
                "status": "extraction succeeded",
                "text": text,
                "pages_checked": structured.pages_checked,
                "text_length": len(text),
                "truncated": truncated,
                "backend": structured.backend,
                "pages_body": pages_to_markdown(structured.pages),
                "sections_body": structured.sections_markdown,
                "captions_body": structured.captions_markdown,
                "notes": f"PDF text extraction succeeded for {structured.pages_checked} page(s) using {structured.backend}.",
                "excerpt_fallback": "",
            }
        extracted = extract_pdf_text(path, max_chars=PAPER_TEXT_LIMIT + 1)
        if extracted.ok:
            text = extracted.text
            truncated = len(text) > PAPER_TEXT_LIMIT
            if truncated:
                text = text[:PAPER_TEXT_LIMIT].rstrip()
            return {
                "status": "extraction succeeded",
                "text": text,
                "pages_checked": extracted.pages_checked,
                "text_length": len(text),
                "truncated": truncated,
                "backend": "pypdf-limited",
                "pages_body": f"### Page 1+\n\n```text\n{text}\n```",
                "sections_body": "Not available.",
                "captions_body": "Not available.",
                "notes": f"PDF text extraction succeeded for {extracted.pages_checked} page(s). Structured extraction unavailable: {structured.error}",
                "excerpt_fallback": "",
            }
        failure_error = extracted.error or structured.error
        return {
            "status": "extraction failed",
            "text": "",
            "pages_checked": structured.pages_checked,
            "text_length": 0,
            "truncated": False,
            "backend": structured.backend or "unavailable",
            "pages_body": "Not available.",
            "sections_body": "Not available.",
            "captions_body": "Not available.",
            "notes": f"PDF extraction failed: {failure_error}",
            "excerpt_fallback": f"Extraction failed: {failure_error}",
        }
    if path.suffix.lower() in {".md", ".txt"}:
        text = path.read_text(encoding="utf-8", errors="replace")
        truncated = len(text) > PAPER_TEXT_LIMIT
        if truncated:
            text = text[:PAPER_TEXT_LIMIT].rstrip()
        return {
            "status": "text file read",
            "text": text,
            "pages_checked": 0,
            "text_length": len(text),
            "truncated": truncated,
            "backend": "text-file",
            "pages_body": f"### Page 1\n\n```text\n{text}\n```",
            "sections_body": text,
            "captions_body": "Not available.",
            "notes": "Plain text paper context was read locally.",
            "excerpt_fallback": "",
        }
    return {
        "status": "unsupported paper file type",
        "text": "",
        "pages_checked": 0,
        "text_length": 0,
        "truncated": False,
        "backend": "unsupported",
        "pages_body": "Not available.",
        "sections_body": "Not available.",
        "captions_body": "Not available.",
        "notes": f"Unsupported paper file type: {path.suffix}",
        "excerpt_fallback": f"Unsupported paper file type: {path.suffix}",
    }


DATASET_STOPWORDS = {
    "the",
    "among",
    "english",
    "each",
    "section",
    "figure",
    "query",
    "gpt",
    "outliers",
    "stella",
    "mteb",
}


def _build_paper_output(structure: dict, extraction: dict, parse_quality_body: str, state: R2AState) -> PaperOutput:
    metadata = structure.get("metadata", {})
    reasons = _paper_quality_reasons(structure, extraction, parse_quality_body, state)
    quality = "LOW_CONFIDENCE" if reasons else "PARTIAL"
    urls = structure.get("source_or_artifact_urls", []) or structure.get("urls", [])
    artifact_refs: list[ArtifactReference] = []
    for item in urls:
        url = str(item.get("url", "") if isinstance(item, dict) else item)
        if not url:
            continue
        artifact_refs.append(
            ArtifactReference(
                url=url,
                kind=_artifact_kind(url),
                confidence="HIGH" if "github.com/spcl/fanns-benchmark" in url or "huggingface.co/datasets/SPCL/arxiv-for-fanns" in url else "LOW",
                evidence_source="local_pdf_text",
            )
        )
    datasets = []
    for item in structure.get("datasets", []) or []:
        name = str(item.get("name", "")).strip()
        if not name or name.lower() in DATASET_STOPWORDS:
            continue
        datasets.append(DatasetReference(name=name, source_url=None, confidence="LOW", notes=str(item.get("notes", ""))[:300]))
    for ref in artifact_refs:
        if ref.kind == "dataset":
            dataset_name = ref.url.rstrip("/").split("/")[-1]
            if dataset_name and not any(existing.name == dataset_name for existing in datasets):
                datasets.append(DatasetReference(name=dataset_name, source_url=ref.url, confidence=ref.confidence))
    paper_url = _trusted_paper_url([ref.url for ref in artifact_refs])
    output = PaperOutput(
        schema_version="2.0",
        metadata=PaperMetadata(
            title=_clean_optional(metadata.get("title")),
            authors=_clean_authors(metadata.get("authors")),
            year=_clean_year(metadata.get("year") or metadata.get("year_or_version")),
            venue=_clean_optional(metadata.get("venue_or_source") or metadata.get("venue")),
            doi=_clean_optional(metadata.get("doi") or metadata.get("arxiv_id_or_doi")),
            paper_url=paper_url,
        ),
        problem_setting=_clean_optional(metadata.get("abstract")) or "",
        method_summary=_structure_summary(structure),
        artifact_references=artifact_refs,
        dataset_references=datasets,
        baselines=[str(item.get("name", "")).strip() for item in structure.get("baselines", []) if str(item.get("name", "")).strip()],
        metrics=[str(item).strip() for item in structure.get("metrics", []) if str(item).strip()],
        evidence_gaps=_paper_evidence_gaps(reasons, parse_quality_body),
        parse_quality=quality,
        quality_reasons=reasons,
    )
    PaperOutput.model_validate(output.model_dump())
    return output


def _paper_quality_reasons(structure: dict, extraction: dict, parse_quality_body: str, state: R2AState) -> list[str]:
    reasons: list[str] = []
    if state.get("paper_ai_reader_failed") or state.get("paper_backend") == "local_preprocess_fallback":
        reasons.append("Paper AI summarizer failed or was disabled; local fallback used.")
    if extraction.get("status") != "extraction succeeded" and extraction.get("status") != "text file read":
        reasons.append(f"Local extraction status is {extraction.get('status')}.")
    metadata = structure.get("metadata", {})
    authors = "; ".join(_clean_authors(metadata.get("authors")))
    title = str(metadata.get("title", "") or "")
    if "algorithm" in authors.lower() or (title and title in authors):
        reasons.append("Authors field appears polluted by title/subtitle text.")
    urls = [str(item.get("url", "")) for item in structure.get("source_or_artifact_urls", []) or structure.get("urls", []) if isinstance(item, dict)]
    if any("huggingface.co/yibinlei/LENS-d8000" in url for url in urls):
        reasons.append("Paper URL candidate appears to be an unrelated in-text/reference URL.")
    dataset_names = [str(item.get("name", "")).lower() for item in structure.get("datasets", []) if isinstance(item, dict)]
    if any(name in DATASET_STOPWORDS for name in dataset_names):
        reasons.append("Dataset extraction contains ordinary words or caption terms.")
    if "raw_text_only" in parse_quality_body or "caption_only" in parse_quality_body:
        reasons.append("Critical tables are not structured.")
    if any(token in extraction.get("text", "") for token in ("鈥", "眉", "铿", "饾")):
        reasons.append("Extracted text contains encoding/order artifacts consistent with difficult PDF extraction.")
    return reasons


def _parse_quality_indicates_caption_only(parse_quality_body: str) -> bool:
    return "caption_only" in str(parse_quality_body or "")


def _paper_evidence_gaps(reasons: list[str], parse_quality_body: str) -> list[str]:
    gaps = list(reasons)
    if "Critical tables structured: 0" in parse_quality_body:
        gaps.append("Critical table values must be verified from artifact scripts or original PDF before acceptance criteria.")
    if not gaps:
        gaps.append("Exact paper claims still require source/artifact verification before reproduction claims.")
    return gaps


def _artifact_kind(url: str) -> str:
    lowered = url.lower()
    if "github.com" in lowered or lowered.endswith(".git"):
        return "source_repo"
    if "huggingface.co/datasets" in lowered or "kaggle.com/datasets" in lowered:
        return "dataset"
    if "huggingface.co/" in lowered:
        return "weights"
    if "doi.org" in lowered or "arxiv.org" in lowered:
        return "project_page"
    return "unknown"


def _trusted_paper_url(urls: list[str]) -> str | None:
    for url in urls:
        lowered = url.lower()
        if "arxiv.org/abs/" in lowered or "doi.org/" in lowered:
            return url
    return None


def _clean_optional(value: object) -> str | None:
    text = str(value or "").strip()
    return text if text and text != "Not available" else None


def _clean_authors(value: object) -> list[str]:
    if isinstance(value, list):
        raw = [str(item).strip() for item in value]
    else:
        raw = [item.strip() for item in re.split(r";|,|\n", str(value or ""))]
    authors = []
    for item in raw:
        if not item or item == "Not available":
            continue
        if "algorithm" in item.lower() and "embedding" in item.lower():
            continue
        authors.append(item)
    return authors[:20]


def _clean_year(value: object) -> int | None:
    match = re.search(r"\b(19|20)\d{2}\b", str(value or ""))
    return int(match.group(0)) if match else None


def _prepare_paper_for_ai_reader(repo: Path, paper_path: str) -> tuple[Path | None, list[str]]:
    warnings: list[str] = []
    if not paper_path:
        return None, ["Paper AI Reader requested without a paper file."]
    source = Path(paper_path).expanduser()
    if not source.exists():
        return None, [f"Paper AI Reader paper file is missing: {source}"]
    papers_dir = artifact_dir(repo) / "papers"
    papers_dir.mkdir(parents=True, exist_ok=True)
    destination = papers_dir / _safe_paper_filename(source)
    if source.resolve() != destination.resolve():
        try:
            shutil.copy2(source, destination)
        except OSError as exc:
            warnings.append(f"Could not copy paper into .r2a/papers for AI Reader: {exc}")
            return source, warnings
    return destination, warnings


def _safe_paper_filename(path: Path) -> str:
    stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", path.stem).strip("._") or "paper"
    suffix = path.suffix if path.suffix else ".pdf"
    return f"{stem}{suffix}"


def _write_ai_reader_text_seed(repo: Path, paper_path: str, *, force: bool = True) -> dict:
    extraction = _preprocess_paper_text(paper_path)
    write_report(
        report_path(repo, "paper_text"),
        "PAPER_TEXT.md",
        {
            "paper_path": paper_path or "No paper uploaded.",
            "extraction_status": extraction["status"],
            "extraction_backend": extraction["backend"],
            "pages_checked": extraction["pages_checked"],
            "text_length": extraction["text_length"],
            "truncated": extraction["truncated"],
            "paper_text": extraction["text"] or extraction["excerpt_fallback"],
            "notes": (
                extraction["notes"]
                + "\nThis file was generated before Paper AI Reader so Codex can read extracted text instead of parsing the PDF directly."
            ),
        },
        force=force,
    )
    _write_structured_paper_inputs(
        pages_output=report_path(repo, "paper_pages"),
        sections_output=report_path(repo, "paper_sections"),
        captions_output=report_path(repo, "paper_captions"),
        paper_path=paper_path or "No paper uploaded.",
        extraction=extraction,
        force=force,
    )
    warnings = []
    if extraction["status"] in {"extraction failed", "paper file missing", "unsupported paper file type", "no paper uploaded"}:
        warnings.append(f"Paper AI Reader text seed status: {extraction['status']}")
    return {"extraction": extraction, "warnings": warnings}


def _write_structured_paper_inputs(
    *,
    pages_output: Path,
    sections_output: Path,
    captions_output: Path,
    paper_path: str,
    extraction: dict,
    force: bool = True,
) -> None:
    common = {
        "paper_path": paper_path,
        "extraction_status": extraction["status"],
        "extraction_backend": extraction["backend"],
        "pages_checked": extraction["pages_checked"],
        "text_length": extraction["text_length"],
        "truncated": extraction["truncated"],
        "notes": extraction["notes"],
    }
    write_report(
        pages_output,
        "PAPER_PAGES.md",
        {**common, "pages_body": extraction["pages_body"]},
        force=force,
    )
    write_report(
        sections_output,
        "PAPER_SECTIONS.md",
        {**common, "sections_body": extraction["sections_body"]},
        force=force,
    )
    write_report(
        captions_output,
        "PAPER_CAPTIONS.md",
        {**common, "captions_body": extraction["captions_body"]},
        force=force,
    )


def _paper_output_paths(repo: Path) -> dict[str, Path]:
    return {
        "paper_text": report_path(repo, "paper_text"),
        "paper_pages": report_path(repo, "paper_pages"),
        "paper_sections": report_path(repo, "paper_sections"),
        "paper_captions": report_path(repo, "paper_captions"),
        "paper_context": report_path(repo, "paper_context"),
        "paper_reproduction_card": report_path(repo, "paper_reproduction_card"),
        "paper_figures_tables": report_path(repo, "paper_figures_tables"),
        "paper_parse_quality": report_path(repo, "paper_parse_quality"),
        "paper_analysis": report_path(repo, "paper_analysis"),
        "paper": report_path(repo, "paper"),
        "paper_evidence": report_path(repo, "paper_evidence"),
        "paper_output": report_path(repo, "paper_output"),
    }


def _paper_state_from_outputs(state: R2AState, outputs: dict[str, Path], extraction_status: str) -> R2AState:
    paper_text = _read(outputs["paper_text"])
    return {
        **state,
        "paper_text_path": str(outputs["paper_text"]),
        "paper_pages_path": str(outputs["paper_pages"]),
        "paper_sections_path": str(outputs["paper_sections"]),
        "paper_captions_path": str(outputs["paper_captions"]),
        "paper_context_path": str(outputs["paper_context"]),
        "paper_reproduction_card_path": str(outputs["paper_reproduction_card"]),
        "paper_figures_tables_path": str(outputs["paper_figures_tables"]),
        "paper_parse_quality_path": str(outputs["paper_parse_quality"]),
        "paper_analysis_path": str(outputs["paper_analysis"]),
        "paper_brief_path": str(outputs["paper"]),
        "paper_evidence_path": str(outputs["paper_evidence"]),
        "paper_text_excerpt": _excerpt(paper_text, PAPER_CONTEXT_EXCERPT_LIMIT),
        "paper_context_excerpt": _read(outputs["paper_context"])[:PAPER_CONTEXT_EXCERPT_LIMIT],
        "paper_reproduction_card_excerpt": _read(outputs["paper_reproduction_card"])[:PAPER_CONTEXT_EXCERPT_LIMIT],
        "paper_figures_tables_excerpt": _read(outputs["paper_figures_tables"])[:PAPER_CONTEXT_EXCERPT_LIMIT],
        "paper_parse_quality_excerpt": _read(outputs["paper_parse_quality"])[:PAPER_CONTEXT_EXCERPT_LIMIT],
        "paper_analysis_excerpt": _read(outputs["paper_analysis"])[:PAPER_CONTEXT_EXCERPT_LIMIT],
        "paper_extraction_status": extraction_status,
        "paper_text_length": len(paper_text),
    }


def _append_note(path: Path, note: str) -> None:
    if not path.exists():
        return
    text = path.read_text(encoding="utf-8", errors="replace")
    path.write_text(f"{text.rstrip()}\n\n## Paper AI Reader Note\n\n{note}\n", encoding="utf-8")


def _write_paper_analysis_cn(repo: Path, *, force: bool = True) -> Path:
    output = report_path(repo, "paper_analysis")
    if output.exists() and not force:
        return output
    brief = _read(report_path(repo, "paper"))
    context = _read(report_path(repo, "paper_context"))
    evidence = _read(report_path(repo, "paper_evidence"))
    figures_tables = _read(report_path(repo, "paper_figures_tables"))
    parse_quality = _read(report_path(repo, "paper_parse_quality"))
    card = _read(report_path(repo, "paper_reproduction_card"))
    captions = _read(report_path(repo, "paper_captions"))
    analysis_body = "\n\n".join(
        [
            "本文件为 Paper 节点自动生成的中文整合分析，供 Planner / Engineer 使用。"
            "专业术语、路径、URL、status labels 保持原文形式。"
            "所有内容仅来自 Paper 阶段产物和本地 PDF/text 抽取；不得视为已验证 GitHub artifact 或完整复现实验结论。",
            "## 1. 论文元信息、摘要与方法简介\n\n"
            "以下内容整合自 `PAPER_CONTEXT.md`、`PAPER_BRIEF.md` 和 `PAPER_REPRODUCTION_CARD.md`。\n\n"
            f"{_extract_sections(context, ['## 基本信息', '## 论文概览', '## 研究问题', '## 核心动机', '## 主要贡献'])}\n\n"
            f"{_extract_sections(card, ['## 1. Bibliographic Info', '## 2. Problem Setting', '## 3. Core Idea'])}\n\n"
            f"{_excerpt(brief, 2500)}",
            "## 2. 系统架构与算法描述\n\n"
            "以下内容用于指导 Engineer 定位源码和理解算法边界。\n\n"
            f"{_extract_sections(context, ['## 方法 / 系统原则', '## 关键算法 / 架构组件', '## 后续 Engineer 应优先检查的源码面'])}\n\n"
            f"{_extract_sections(card, ['## 4. Method / Algorithm Details'])}",
            "## 3. 数据集、实验设计、Baselines 与指标\n\n"
            "以下内容用于 Planner 生成 reduced reproduction task。完整实验矩阵成本较高，默认不应直接规划全量复现。\n\n"
            f"{_extract_sections(context, ['## URLs / 数据 / 基线 / 指标 / 参数'])}\n\n"
            f"{_extract_sections(card, ['## 6. Baselines', '## 7. Datasets', '## 8. Metrics', '## 9. Experimental Setup'])}\n\n"
            f"{_extract_sections(evidence, ['## URLs 证据', '## 数据集证据', '## Baselines 证据', '## 指标 / 设置证据'])}",
            "## 4. 图表和表格信息\n\n"
            "图表信息来自 caption、附近正文和可见 PDF text。没有做 OCR，也没有解析图像内部曲线。"
            "曲线图的完整点位数据若未在文本中出现，统一标记为 Evidence Gap。\n\n"
            f"{figures_tables or 'Not available.'}\n\n"
            "## Critical Table Parse Quality\n\n"
            f"{parse_quality or 'Not available.'}\n\n"
            "## Caption Index\n\n"
            f"{captions or 'Not available.'}",
            "## 5. 关键结果与证据 ledger\n\n"
            f"{_extract_sections(card, ['## 10. Key Experimental Results'])}\n\n"
            f"{_extract_sections(evidence, ['## 主要事实证据', '## 关键结果证据'])}",
            "## 6. Artifact / 复现要点 / Gaps / 注意事项\n\n"
            f"{_extract_sections(card, ['## 11. Reproduction Resources', '## 12. Reproduction Difficulty Assessment', '## 13. Recommended R2A Reproduction Plan', '## 14. Evidence Quality'])}\n\n"
            f"{_extract_sections(evidence, ['## 缺失证据', '## Inferred from goal', '## Needs human verification'])}\n\n"
            f"{_extract_sections(context, ['## 缺失信息 / 需人工核验'])}",
            "## 7. Planner / Engineer 使用说明\n\n"
            "- Planner 应优先读取本文件，再回看 `PAPER_REPRODUCTION_CARD.md`、`PAPER_FIGURES_TABLES.md`、`PAPER_EVIDENCE.md`。\n"
            "- Planner should read `PAPER_PARSE_QUALITY.md` before turning table values into hard acceptance criteria; `caption_only` critical tables are Evidence Gaps.\n"
            "- 第一轮任务应保守：source verification、build/import smoke test、定位 HNSW / adaptive-local / query operator、一个 reduced metric check。\n"
            "- Engineer 不得把本文件中的论文结果当成已复现实验结果。\n"
            "- 如果 artifact repo、数据、命令或依赖不可确认，应输出 truthful `NEEDS_CLARIFICATION` / `BLOCKED`，不要伪造 results。\n"
            "- Figure 曲线完整数值、artifact commit、完整命令和依赖锁定均属于高优先级证据缺口。",
        ]
    )
    return write_report(output, "PAPER_ANALYSIS_CN.md", {"analysis_body": analysis_body}, force=force)


def _paper_analysis_language_warnings(path: Path) -> list[str]:
    if not path.exists():
        return []
    text = _read(path)
    if len(text.strip()) < 120:
        return []
    letters = [char for char in text if char.isalpha()]
    cjk_count = sum(1 for char in text if "\u4e00" <= char <= "\u9fff")
    if cjk_count < 20 or (letters and cjk_count / max(len(letters), 1) < 0.08):
        return [
            "PAPER_ANALYSIS_CN.md appears to contain too little Simplified Chinese text; Paper AI Reader may have ignored the language contract."
        ]
    return []


def _extract_sections(markdown: str, headings: list[str]) -> str:
    sections = [_extract_section_by_heading(markdown, heading) for heading in headings]
    return "\n\n".join(section for section in sections if section).strip() or "Not available."


def _extract_section_by_heading(markdown: str, heading: str) -> str:
    if not markdown or heading not in markdown:
        return ""
    start = markdown.find(heading)
    next_heading = re.search(r"\n## ", markdown[start + len(heading) :])
    if not next_heading:
        return markdown[start:].strip()
    end = start + len(heading) + next_heading.start()
    return markdown[start:end].strip()


def _paper_source(paper_path: str) -> str:
    if not paper_path:
        return "No paper_path provided. This brief is generated from user goal and available context only."
    path = Path(paper_path)
    if not path.exists():
        return f"paper_path provided but file does not exist: {path}"
    return str(path)


def _extract_available_evidence(paper_path: str, goal: str, extra_context: str, extraction: dict) -> tuple[str, str]:
    evidence: list[str] = []
    if extraction["text"]:
        evidence.append(f"- Local paper text excerpt:\n\n```text\n{_excerpt(extraction['text'], 4000)}\n```")
    else:
        evidence.append(f"- {extraction['excerpt_fallback']}")
    if goal:
        evidence.append(f"- User goal: {goal}")
    if extra_context:
        evidence.append(f"- User extra context: {extra_context}")
    return extraction["notes"], "\n".join(evidence)


def _build_context_excerpt(paper_text_excerpt: str, structure: dict, card_output: Path, figures_tables_output: Path, parse_quality_output: Path) -> str:
    metadata = structure["metadata"]
    source_urls = structure["source_or_artifact_urls"]
    lines = [
        "## Compact Reproduction Context",
        "",
        f"- Title: {metadata.get('title', 'Not available')}",
        f"- Venue / Source: {metadata.get('venue_or_source', 'Not available')}",
        f"- arXiv ID / DOI: {metadata.get('arxiv_id_or_doi', 'Not available')}",
        f"- Artifact / Source URLs: {', '.join(item['url'] for item in source_urls) if source_urls else 'Not available'}",
        f"- Baselines: {', '.join(item['name'] for item in structure['baselines']) if structure['baselines'] else 'Not available'}",
        f"- Datasets: {', '.join(item['name'] for item in structure['datasets']) if structure['datasets'] else 'Not available'}",
        f"- Metrics: {', '.join(structure['metrics']) if structure['metrics'] else 'Not available'}",
        f"- Figures found: {len(structure['figures'])}",
        f"- Tables found: {len(structure['tables'])}",
        f"- Reproduction card: {card_output}",
        f"- Figures/tables report: {figures_tables_output}",
        f"- Parse quality report: {parse_quality_output}",
        "",
        "## Paper Text Excerpt",
        "",
        paper_text_excerpt,
    ]
    return "\n".join(lines)


def _structure_summary(structure: dict) -> str:
    parts = []
    metadata = structure["metadata"]
    if metadata.get("abstract") and metadata["abstract"] != "Not available":
        parts.append(f"Abstract excerpt: {metadata['abstract']}")
    if structure["source_or_artifact_urls"]:
        parts.append("Source/artifact URL extracted: " + ", ".join(item["url"] for item in structure["source_or_artifact_urls"]))
    if structure["figures"]:
        parts.append("Important figures detected: " + ", ".join(f"Figure {item['id']}" for item in structure["figures"][:8]))
    if structure["tables"]:
        parts.append("Important tables detected: " + ", ".join(f"Table {item['id']}" for item in structure["tables"][:8]))
    return "\n".join(parts) if parts else "Evidence-limited. Do not infer method details without explicit text evidence."


def _format_named_items(items: list[dict], key: str) -> str:
    if not items:
        return "- Evidence Gap: not found in extracted text."
    return bullet_list([f"{item.get(key, 'Not available')}: {item.get('notes', 'Not available')}" for item in items])


def _url_summary(structure: dict) -> str:
    urls = structure.get("urls", [])
    if not urls:
        return "No source/artifact/data URLs were extracted from paper text."
    return "Extracted URLs:\n" + bullet_list([f"{item['kind']}: {item['url']}" for item in urls])


def _structured_evidence(structure: dict) -> str:
    return "\n".join(
        [
            "## Structured Paper Evidence",
            "",
            f"- Source/artifact URLs: {', '.join(item['url'] for item in structure['source_or_artifact_urls']) if structure['source_or_artifact_urls'] else 'Not available'}",
            f"- Baselines: {', '.join(item['name'] for item in structure['baselines']) if structure['baselines'] else 'Not available'}",
            f"- Datasets: {', '.join(item['name'] for item in structure['datasets']) if structure['datasets'] else 'Not available'}",
            f"- Metrics: {', '.join(structure['metrics']) if structure['metrics'] else 'Not available'}",
            f"- Figures: {len(structure['figures'])} caption/context entries",
            f"- Tables: {len(structure['tables'])} caption/context entries",
        ]
    )


def _evidence_sources(paper_path: str, goal: str, extra_context: str) -> list[str]:
    sources: list[str] = [
        ".r2a/PAPER_REPRODUCTION_CARD.md",
        ".r2a/PAPER_FIGURES_TABLES.md",
        ".r2a/PAPER_CONTEXT.md",
        ".r2a/PAPER_TEXT.md",
    ]
    if paper_path:
        sources.append(f"paper_path: {paper_path}")
    if goal:
        sources.append("user goal")
    if extra_context:
        sources.append("extra_context")
    return sources


def _goal_inferred(goal: str, fallback: str, language: str = "en") -> str:
    if not goal:
        return f"{_t(language, 'MVP 中不可用。', 'Not available in MVP.')} {fallback}"
    return f"{_t(language, '从用户目标推断：', 'Inferred from goal:')} {goal}"


def _topic(goal: str) -> str:
    return f"Inferred from goal: {goal}" if goal else "Not available in MVP."


def _prompt_summary(prompt: str) -> str:
    rules = [line.strip("- ") for line in prompt.splitlines() if line.startswith("- ")]
    return bullet_list(rules[:4])


def _read(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


def _t(language: str, zh: str, en: str) -> str:
    return zh if language == "zh" else en


def _language_name(language: str) -> str:
    return "Simplified Chinese" if language == "zh" else "English"


def _paper_limitations(language: str) -> list[str]:
    if language == "zh":
        return [
            "Paper Agent 只做本地有限 PDF 文本抽取，不等同于完整阅读论文。",
            "表格、图片、公式、扫描文本和双栏顺序可能不完整。",
            "Planner 和 Reviewer 必须把 PAPER_CONTEXT.md / PAPER_EVIDENCE.md 当作 evidence-limited context。",
        ]
    return [
        "Paper Agent only performs local limited PDF text extraction.",
        "Tables, figures, formulas, scanned text, and two-column ordering may be incomplete.",
        "Planner and Reviewer must treat PAPER_CONTEXT.md / PAPER_EVIDENCE.md as evidence-limited context.",
    ]


def _missing_evidence(language: str) -> list[str]:
    if language == "zh":
        return [
            "完整论文理解",
            "可靠表格/图片/公式解析",
            "已验证的 baselines",
            "已验证的数据集",
            "已验证的指标",
            "精确实现细节和超参数",
        ]
    return [
        "Full paper understanding",
        "Reliable table/figure/formula parsing",
        "Verified baselines",
        "Verified datasets",
        "Verified metrics",
        "Exact implementation details and hyperparameters",
    ]


def _excerpt(text: str, limit: int) -> str:
    cleaned = (text or "").strip()
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[:limit].rstrip() + "\n...(truncated)"
