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
    return "\n".join(
        [
            "# FINAL_NARRATIVE_CN",
            "",
            "本节是 Final Writer 根据结构化判定和 artifacts 生成的中文叙述。正式判定以 `FINAL_DECISION.json` 为准；Final Writer 不重新判断 verdict，不修改 accepted_level、observed_level 或 target_reached。",
            "",
            "## 最终结论",
            "",
            f"- 正式 verdict: `{formal_verdict}`",
            f"- Accepted level: `{accepted_level}`",
            f"- Observed level: `{observed_level}`",
            f"- Target level: `{target_level}`",
            f"- Target reached: `{target_reached}`",
            f"- Final status: `{final_status}`",
            f"- Stop reason: `{stop_reason}`",
            "",
            "## 复现证据摘要",
            "",
            _text(context.get("reduced_metrics_summary"), "- reduced_metrics.csv not present."),
            "",
            _text(context.get("paper_alignment_summary"), "- paper_alignment.csv not present."),
            "",
            _text(context.get("l4_alignment_excerpt"), "- L4_ALIGNMENT_SUMMARY.md not present."),
            "",
            _text(context.get("command_manifest_summary"), "- command_manifest.csv not present."),
            "",
            "## 局限性与后续动作",
            "",
            _bullet(warnings or ["None"]),
            "",
            _bullet(limitations or ["No additional limitations recorded."]),
        ]
    )


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
                "- Include the note that FINAL_DECISION.json is the formal decision source.",
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
