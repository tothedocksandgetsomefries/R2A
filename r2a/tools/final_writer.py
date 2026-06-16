from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

from r2a.core.paths import artifact_dir, report_path
from r2a.tools import openclaw_stage_runner
from r2a.tools.wsl import windows_to_wsl_path


FINAL_WRITER_MODE = "narrative_only"


def build_template_final_narrative_cn(context: dict[str, Any]) -> str:
    final_decision = _dict(context.get("final_decision"))
    formal_verdict = _text(final_decision.get("formal_verdict"), "UNASSESSED")
    accepted_level = _text(final_decision.get("accepted_level"), "UNASSESSED")
    observed_level = _text(final_decision.get("observed_level"), "UNASSESSED")
    target_level = _text(final_decision.get("target_level"), "unknown")
    target_reached = bool(final_decision.get("target_reached", False))
    final_status = _text(final_decision.get("final_status"), "completed_with_failure")
    stop_reason = _text(final_decision.get("stop_reason"), "unknown")
    warnings = _list(context.get("warnings"))
    limitations = _list(context.get("limitations"))
    reduced_metrics_summary = _text(context.get("reduced_metrics_summary"), "- 未找到 `reduced_metrics.csv`。")
    paper_alignment_summary = _text(context.get("paper_alignment_summary"), "- 未找到 `paper_alignment.csv`。")
    l4_alignment_excerpt = _text(context.get("l4_alignment_excerpt"), "- 未找到 `L4_ALIGNMENT_SUMMARY.md`。")
    command_manifest_summary = _text(context.get("command_manifest_summary"), "- 未找到 `command_manifest.csv`。")
    return "\n".join(
        [
            "# 最终复现报告摘要",
            "",
            "本摘要由 Final Writer 根据结构化 artifacts 整理生成。正式判定以 `FINAL_DECISION.json` 为准；Final Writer 不重新判断 verdict，不修改 accepted_level、observed_level 或 target_reached。",
            "",
            "## 1. 论文与任务概览",
            "",
            "本次报告面向一篇论文复现任务：论文研究问题、核心方法、benchmark 目标、官方源码与数据来源均应以已落盘的论文与源码证据文件为准，报告层不补写未记录的论文结论或实验数字。",
            f"本次目标等级记录为 `{target_level}`，正式等级字段仍来自 `FINAL_DECISION.json`。",
            "",
            "（证据文件：PAPER_CONTEXT.md、PAPER_REPRODUCTION_CARD.md、SOURCE_ACQUISITION.json）",
            "",
            "## 2. 本次运行做了什么",
            "",
            "本次 workflow 的可审计产物按阶段组织：Paper 解析生成论文上下文，源码识别记录候选与选中来源，Planner 生成任务与实验契约，Engineer 在有界范围内执行实验并记录结果，Reviewer / EvidenceDecision / FinalDecision 负责正式判级与终态汇总。若某阶段未运行或被跳过，结构化 diagnostics 和 `RUN_MANIFEST.json` 会保留对应状态。",
            "",
            "（证据文件：RUN_MANIFEST.json、ITERATION_STATE.json、TASK_SPEC.md、EXPERIMENT_CONTRACT.md、EXECUTION_REPORT.md、REVIEW_REPORT.md、FINAL_DECISION.json）",
            "",
            "## 3. 实验范围与当前状态",
            "",
            "本次报告不声明 full reproduction。它只陈述结构化 evidence 已正式接受或已观察到的范围；是否属于 official reduced / L4 reduced paper-aligned 以 `accepted_level`、`observed_level` 和 evidence artifacts 为准。",
            "",
            f"- final_status: `{final_status}`",
            f"- formal_verdict: `{formal_verdict}`",
            f"- accepted_level: `{accepted_level}`",
            f"- observed_level: `{observed_level}`",
            f"- target_level: `{target_level}`",
            f"- target_reached: `{target_reached}`",
            f"- stop_reason: `{stop_reason}`",
            "",
            "源码、数据集和方法名称不由 Final Writer 自由补写；请以 `SOURCE_ACQUISITION.json`、`input_contract_verification.csv` 和 `reduced_metrics.csv` 中的结构化字段为准。若当前等级不是 L5/L6，原因通常是没有正式接受完整数据规模、多次重复实验、完整 baseline 覆盖或 full/near-full reproduction 证据。",
            "",
            "（证据文件：SOURCE_ACQUISITION.json、input_contract_verification.csv、reduced_metrics.csv、paper_alignment.csv、FINAL_DECISION.json）",
            "",
            "## 4. 实验结果摘要",
            "",
            _reduced_metrics_table(reduced_metrics_summary),
            "",
            "以上表格只整理 `reduced_metrics.csv` 或既有 summary 中出现的字段；没有结构化来源的数字不会被补写。",
            "",
            "## 5. 论文对齐情况",
            "",
            _paper_alignment_table(paper_alignment_summary),
            "",
            "`L4_ALIGNMENT_SUMMARY.md` 是 L4 对齐证据包，用于帮助理解 reduced run 与论文设置的匹配情况；它不替代 Reviewer verdict，也不重新判级。",
            "",
            l4_alignment_excerpt,
            "",
            "## 6. 最终结论",
            "",
            _final_conclusion(
                formal_verdict=formal_verdict,
                accepted_level=accepted_level,
                observed_level=observed_level,
                target_level=target_level,
                target_reached=target_reached,
            ),
            "",
            "证据文件：REVIEW_VERDICT.json、EVIDENCE_DECISION.json、FINAL_DECISION.json。",
            "",
            "## 7. 推荐查看文件",
            "",
            "1. FINAL_REPORT.md",
            "2. FINAL_NARRATIVE_CN.md",
            "3. L4_ALIGNMENT_SUMMARY.md",
            "4. REVIEW_REPORT.md",
            "5. paper_alignment.csv",
            "6. reduced_metrics.csv",
            "7. command_manifest.csv",
            "8. SOURCE_ACQUISITION.json",
            "9. PAPER_CONTEXT.md / PAPER_REPRODUCTION_CARD.md",
            "",
            "## 附：命令与局限性提示",
            "",
            command_manifest_summary,
            "",
            "### 当前警告",
            "",
            _bullet(warnings or ["无"]),
            "",
            "### 局限性与后续动作",
            "",
            _bullet(limitations or ["未记录额外局限性。"]),
        ]
    )


def _reduced_metrics_table(summary: str) -> str:
    rows = _summary_fields(summary)
    methods = rows.get("methods", "见 reduced_metrics.csv")
    datasets = rows.get("datasets", "见 reduced_metrics.csv")
    metrics = rows.get("metrics", "见 reduced_metrics.csv")
    result = rows.get("reduced_metrics_rows", "")
    command_manifest = rows.get("command_manifest.csv present", "")
    key_result = f"reduced_metrics_rows: {result}" if result else "见结构化 reduced metrics summary"
    if command_manifest:
        key_result = f"{key_result}; command_manifest.csv present: {command_manifest}"
    return "\n".join(
        [
            "| 方法 | 数据集 | 指标 | 关键结果 | 证据文件 |",
            "| --- | --- | --- | --- | --- |",
            f"| {_cell(methods)} | {_cell(datasets)} | {_cell(metrics)} | {_cell(key_result)} | reduced_metrics.csv、command_manifest.csv |",
        ]
    )


def _paper_alignment_table(summary: str) -> str:
    counts = _alignment_counts(summary)
    meanings = {
        "MATCH": "与论文设置匹配",
        "PARTIAL_MATCH": "与论文设置部分匹配",
        "MISMATCH": "与论文设置不匹配",
        "NOT_AVAILABLE": "论文或本次 artifacts 中缺少可比信息",
        "NEEDS_HUMAN_VERIFICATION": "需要人工复核",
    }
    lines = ["| 对齐类型 | 数量 | 含义 | 证据文件 |", "| --- | --- | --- | --- |"]
    for status in ("MATCH", "PARTIAL_MATCH", "MISMATCH", "NOT_AVAILABLE", "NEEDS_HUMAN_VERIFICATION"):
        lines.append(f"| {status} | {counts.get(status, 0)} | {meanings[status]} | paper_alignment.csv、L4_ALIGNMENT_SUMMARY.md |")
    return "\n".join(lines)


def _final_conclusion(
    *,
    formal_verdict: str,
    accepted_level: str,
    observed_level: str,
    target_level: str,
    target_reached: bool,
) -> str:
    if accepted_level == "L4_reduced_paper_aligned":
        return (
            "本次运行达到 `L4_reduced_paper_aligned`，表示系统在官方源码和缩减数据范围内，"
            "完成了与论文关键实验设置相对齐的复现实验。但本次没有正式声明完整数据规模、多次重复实验或全部论文方法覆盖，因此不构成 L5/L6。"
        )
    if observed_level == "L4_reduced_paper_aligned" and accepted_level != observed_level:
        return (
            f"本次运行观察到 `L4_reduced_paper_aligned` 候选证据，但正式 accepted_level 为 `{accepted_level}`。"
            "报告层不会把 observed_level 改写为 accepted_level；正式结论仍以 `FINAL_DECISION.json` 为准。"
        )
    return (
        f"本次正式 verdict 为 `{formal_verdict}`，accepted_level 为 `{accepted_level}`，observed_level 为 `{observed_level}`，"
        f"目标等级为 `{target_level}`，target_reached 为 `{target_reached}`。"
        "这说明本报告只总结当前已正式接受的复现证据，不扩展声明 L5/L6 或 full reproduction。"
    )


def _summary_fields(summary: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for raw_line in str(summary or "").splitlines():
        line = raw_line.strip().lstrip("-").strip()
        if not line or ":" not in line:
            continue
        key, value = line.split(":", 1)
        fields[key.strip()] = value.strip()
    return fields


def _alignment_counts(summary: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    status_line = ""
    for raw_line in str(summary or "").splitlines():
        line = raw_line.strip().lstrip("-").strip()
        if line.startswith("Alignment statuses:"):
            status_line = line.split(":", 1)[1]
            break
    for status, value in re.findall(r"([A-Z_]+)\s*:\s*(\d+)", status_line):
        counts[status] = int(value)
    if counts:
        return counts
    for status, value in re.findall(r"(MATCH|PARTIAL_MATCH|MISMATCH|NOT_AVAILABLE|NEEDS_HUMAN_VERIFICATION)\s+rows:\s*(\d+)", summary):
        counts[status] = int(value)
    return counts


def _cell(value: object) -> str:
    text = str(value or "").strip()
    return text.replace("|", "\\|") or "-"


def run_final_writer(
    state: dict[str, Any],
    context: dict[str, Any],
    *,
    template_narrative: str,
) -> dict[str, Any]:
    repo = Path(str(state["repo_path"]))
    requested_backend = _text(state.get("final_writer_backend"), "template").lower()
    if requested_backend not in {"template", "openclaw"}:
        requested_backend = "template"
    output_path = report_path(repo, "final_narrative")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if requested_backend != "openclaw":
        output_path.write_text(template_narrative, encoding="utf-8")
        metadata = _metadata(
            requested_backend=requested_backend,
            backend="template",
            provider="",
            model="none",
            profile="",
            runner="",
            output_path=output_path,
            enabled=False,
            fallback_reason="Final Writer LLM disabled; template narrative used.",
        )
        _write_metadata(repo, metadata)
        return {"narrative": template_narrative, "metadata": metadata}

    before_decision = _read_bytes(report_path(repo, "final_decision"))
    stage_config = openclaw_stage_runner.openclaw_stage_model_config_from_state(state, "final_writer")
    input_path = _write_openclaw_input(repo, state, context, output_path, stage_config)
    result = openclaw_stage_runner.run_openclaw_stage(
        repo,
        "final_writer",
        input_path,
        [".r2a/FINAL_NARRATIVE_CN.md"],
        session_key=_final_writer_session_key(state),
        iteration=int(state.get("iteration", 1) or 1),
        timeout=int(state.get("codex_stage_timeout", state.get("timeout", 10800)) or 10800),
        openclaw_executable_path=state.get("openclaw_executable_path"),
        openclaw_config_path=state.get("openclaw_config_path"),
        wsl_distro=str(state.get("wsl_distro", "Ubuntu") or "Ubuntu"),
        provider=stage_config.get("provider"),
        model=stage_config.get("model"),
        runner=stage_config.get("runner"),
        agent=stage_config.get("agent"),
    )
    decision_restored = _restore_final_decision_if_changed(repo, before_decision)
    if not result.get("success") or decision_restored or not output_path.exists():
        output_path.write_text(template_narrative, encoding="utf-8")
        reason = str(result.get("error") or result.get("failure_category") or "Final Writer did not produce FINAL_NARRATIVE_CN.md.")
        if decision_restored:
            reason = "Final Writer attempted to alter FINAL_DECISION.json; restored formal decision and used template narrative."
        metadata = _metadata(
            requested_backend="openclaw",
            backend="template",
            provider="",
            model="none",
            profile="",
            runner="",
            output_path=output_path,
            enabled=True,
            fallback_reason=reason,
            configured=stage_config,
        )
        _write_metadata(repo, metadata)
        return {"narrative": template_narrative, "metadata": metadata}

    narrative = output_path.read_text(encoding="utf-8", errors="replace")
    metadata = _metadata(
        requested_backend="openclaw",
        backend="openclaw",
        provider=str(result.get("provider") or result.get("configured_provider") or stage_config.get("provider") or ""),
        model=str(result.get("model") or result.get("configured_model") or stage_config.get("model") or ""),
        profile=str(stage_config.get("profile") or "final_writer"),
        runner=str(result.get("runner") or result.get("configured_runner") or stage_config.get("runner") or ""),
        output_path=output_path,
        enabled=True,
        fallback_reason="",
        configured=stage_config,
        invocation=result,
    )
    _write_metadata(repo, metadata)
    return {"narrative": narrative, "metadata": metadata}


def read_final_writer_metadata(repo_path: str | Path) -> dict[str, Any]:
    path = report_path(repo_path, "final_writer_metadata")
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def final_writer_metadata_markdown(metadata: dict[str, Any]) -> str:
    if not metadata:
        metadata = _metadata(
            requested_backend="template",
            backend="template",
            provider="",
            model="none",
            profile="",
            runner="",
            output_path=Path("FINAL_REPORT.md"),
            enabled=False,
            fallback_reason="Final Writer metadata unavailable; template report assumed.",
        )
    lines = [
        f"Final Writer: {'enabled' if metadata.get('enabled') else 'disabled'}",
        f"Final Writer requested backend: {metadata.get('requested_backend') or 'template'}",
        f"Final Writer backend: {metadata.get('backend') or 'template'}",
        f"Final Writer provider: {metadata.get('provider') or 'none'}",
        f"Final Writer model: {metadata.get('model') or 'none'}",
        f"Final Writer profile: {metadata.get('profile') or 'none'}",
        f"Final Writer mode: {metadata.get('mode') or FINAL_WRITER_MODE}",
        f"Final Writer output path: {metadata.get('output_path') or 'FINAL_REPORT.md'}",
        "Final Writer did not alter formal decision: yes",
    ]
    if metadata.get("fallback_reason"):
        lines.append(f"Final Writer fallback: {metadata['fallback_reason']}")
    return _bullet(lines)


def _write_openclaw_input(
    repo: Path,
    state: dict[str, Any],
    context: dict[str, Any],
    output_path: Path,
    stage_config: dict[str, str],
) -> Path:
    iteration = int(state.get("iteration", 1) or 1)
    staging = artifact_dir(repo) / "staging" / "final_writer" / f"iter_{iteration:03d}" / "attempt_001"
    staging.mkdir(parents=True, exist_ok=True)
    input_path = staging / "OPENCLAW_INPUT.md"
    payload = json.dumps(context, indent=2, ensure_ascii=False)
    input_path.write_text(
        "\n".join(
            [
                "# R2A Final Narrative Writer",
                "",
                "You are the Final Writer / Report Writer. You write Chinese narrative only.",
                "Formal decisions are already made. Do not change or reinterpret them.",
                "",
                "Backend contract:",
                f"- provider: `{stage_config.get('provider', '')}`",
                f"- model: `{stage_config.get('model', '')}`",
                f"- runner/profile: `{stage_config.get('runner', '')}`",
                "- mode: `narrative_only`",
                "- fallbackUsed: `false`",
                "",
                "Strict prohibitions:",
                "- Do not judge a new verdict.",
                "- Do not modify accepted_level.",
                "- Do not modify observed_level.",
                "- Do not modify target_reached.",
                "- Do not write FINAL_DECISION.json.",
                "- Do not rewrite observed_level as accepted_level.",
                "- Do not add conclusions unsupported by the provided artifacts.",
                "",
                "Write boundary:",
                f"- Write only `{windows_to_wsl_path(output_path)}`.",
                "- Do not write any other file or directory.",
                "",
                "Output requirements:",
                "- Write Simplified Chinese Markdown.",
                "- Use Chinese section titles and Chinese explanatory prose.",
                "- Keep machine field names and verdict values unchanged when quoting them, such as `PASS_REDUCED_ALIGNED` and `L4_reduced_paper_aligned`.",
                "- Include the note that FINAL_DECISION.json is the formal decision source.",
                "- Start with `# 最终复现报告摘要`.",
                "- Use sections `## 1. 论文与任务概览` through `## 7. 推荐查看文件`.",
                "- Put diagnostics and raw machine fields after the human-readable summary, not before it.",
                "- For result tables, use only values present in the provided JSON/context or named evidence files.",
                "- Keep the narrative based only on the JSON/context below.",
                "",
                "When finished, return raw JSON only, without Markdown fences:",
                '{"status":"PASS","stage":"final_writer","mode":"narrative_only"}',
                "",
                "---",
                "",
                payload,
                "",
            ]
        ),
        encoding="utf-8",
    )
    return input_path


def _metadata(
    *,
    requested_backend: str,
    backend: str,
    provider: str,
    model: str,
    profile: str,
    runner: str,
    output_path: Path,
    enabled: bool,
    fallback_reason: str,
    configured: dict[str, Any] | None = None,
    invocation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    data = {
        "schema_version": 1,
        "stage": "final_writer",
        "enabled": bool(enabled),
        "requested_backend": requested_backend,
        "backend": backend,
        "provider": provider,
        "model": model or "none",
        "profile": profile,
        "runner": runner,
        "mode": FINAL_WRITER_MODE,
        "output_path": str(output_path),
        "did_alter_formal_decision": False,
        "fallback_reason": fallback_reason,
    }
    if configured:
        data["configured"] = dict(configured)
    if invocation:
        data["invocation_id"] = invocation.get("invocation_id", "")
        data["invocation_manifest_path"] = invocation.get("invocation_manifest_path", "")
        data["token_usage"] = invocation.get("token_usage", {})
    return data


def _write_metadata(repo: Path, metadata: dict[str, Any]) -> None:
    path = report_path(repo, "final_writer_metadata")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8")


def _restore_final_decision_if_changed(repo: Path, before: bytes | None) -> bool:
    path = report_path(repo, "final_decision")
    after = _read_bytes(path)
    if after == before:
        return False
    if before is None:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(before)
    return True


def _read_bytes(path: Path) -> bytes | None:
    if not path.exists():
        return None
    return path.read_bytes()


def _final_writer_session_key(state: dict[str, Any]) -> str:
    run_id = str(state.get("run_id", "run") or "run")
    safe_run_id = re.sub(r"[^A-Za-z0-9_.-]+", "-", run_id).strip("-") or "run"
    return f"r2a-final-writer-{safe_run_id}-{int(time.time())}"


def _dict(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: object) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _text(value: object, default: str = "") -> str:
    text = str(value or "").strip()
    return text or default


def _bullet(items: list[str]) -> str:
    if not items:
        return "- None"
    return "\n".join(f"- {item}" for item in items)
