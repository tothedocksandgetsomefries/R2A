"""Reviewer 等级判断协议和校验。

Reviewer 是 L0-L6 正式等级的唯一判断者。

核心原则：
1. Reviewer 基于综合证据语义判断等级
2. 代码层只做最小协议校验
3. 不使用固定文件名/CSV schema 决定等级
4. 支持完整 L0-L6
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


# 完整的 L0-L6 等级定义
REPRODUCTION_LEVELS = (
    "L0_project_health",
    "L1_source_artifact_verified",
    "L2_input_contract_ready",
    "L3_official_reduced_run",
    "L4_reduced_paper_aligned",
    "L5_minimal_baseline_comparison",
    "L6_full_or_near_full_reproduction",
)

LEVEL_INDEX = {level: index for index, level in enumerate(REPRODUCTION_LEVELS)}

LEVEL_LABELS = {
    "L0_project_health": "L0: Paper/repo inspected",
    "L1_source_artifact_verified": "L1: Source/build smoke verified",
    "L2_input_contract_ready": "L2: Runnable demo or input contract ready",
    "L3_official_reduced_run": "L3: Official reduced reproduction",
    "L4_reduced_paper_aligned": "L4: Paper-aligned reduced reproduction",
    "L5_minimal_baseline_comparison": "L5: Minimal baseline comparison",
    "L6_full_or_near_full_reproduction": "L6: Full or near-full reproduction",
}

LEVEL_SEMANTICS = {
    "L0_project_health": "已建立项目、论文或仓库基础信息，但尚无有效执行成果。",
    "L1_source_artifact_verified": "已获得并确认与论文相关的源码、核心实现或可用实现资产。",
    "L2_input_contract_ready": "已建立可执行环境、输入条件、基本运行链路或可运行示例。",
    "L3_official_reduced_run": "已完成有意义的缩减实验，并产生可解释的运行结果或指标。",
    "L4_reduced_paper_aligned": "缩减实验的指标、设置或结论已与论文关键目标进行有效对齐。",
    "L5_minimal_baseline_comparison": "已完成至少一个有意义的 baseline 或对照实验比较。",
    "L6_full_or_near_full_reproduction": "完成完整或接近完整的论文复现，覆盖主要实验、条件、指标和结论。",
}

# 合法的等级集合（用于校验）
VALID_LEVELS = set(REPRODUCTION_LEVELS)

# Reviewer 输出必需字段
REQUIRED_REVIEWER_OUTPUT_FIELDS = {
    "current_reproduction_level",
    "level_reasoning",
}

# Reviewer 输出可选字段
OPTIONAL_REVIEWER_OUTPUT_FIELDS = {
    "supporting_artifacts",
    "remaining_gaps",
    "next_iteration_guidance",
    "review_summary",
    "verdict",
}


def normalize_level(level: str | None, default: str = "L0_project_health") -> str:
    """标准化等级名称。"""
    if not level:
        return default
    level = str(level).strip()
    if level in LEVEL_INDEX:
        return level
    return default


def is_valid_level(level: str | None) -> bool:
    """检查是否为合法等级。"""
    if not level:
        return False
    return str(level).strip() in VALID_LEVELS


def level_index(level: str) -> int:
    """获取等级索引。"""
    normalized = normalize_level(level)
    return LEVEL_INDEX[normalized]


def level_reached(level: str, target: str) -> bool:
    """检查是否达到目标等级。"""
    return level_index(level) >= level_index(target)


def validate_reviewer_output(output: dict[str, Any], repo: Path | None = None) -> dict[str, Any]:
    """校验 Reviewer 输出。

    返回：
    - valid: bool - 是否有效
    - level: str | None - 合法等级或 None
    - reasoning: str - 推理说明
    - errors: list[str] - 错误列表
    - warnings: list[str] - 警告列表
    """
    errors: list[str] = []
    warnings: list[str] = []

    # 检查必需字段
    if "current_reproduction_level" not in output:
        errors.append("Missing required field: current_reproduction_level")

    if "level_reasoning" not in output:
        errors.append("Missing required field: level_reasoning")

    level = None
    reasoning = ""

    # 校验等级
    raw_level = output.get("current_reproduction_level")
    if raw_level is not None:
        if not is_valid_level(raw_level):
            errors.append(f"Invalid level: {raw_level}. Must be one of: {', '.join(REPRODUCTION_LEVELS)}")
        else:
            level = normalize_level(raw_level)

    # 校验 reasoning
    raw_reasoning = output.get("level_reasoning")
    if raw_reasoning is not None:
        reasoning = str(raw_reasoning).strip()
        if not reasoning:
            errors.append("level_reasoning is empty")

    # 校验 supporting_artifacts
    artifacts = output.get("supporting_artifacts")
    if artifacts is not None:
        if not isinstance(artifacts, list):
            errors.append("supporting_artifacts must be a list")
        elif repo is not None:
            # 检查路径是否存在（只生成警告，不阻止）
            for artifact in artifacts:
                if isinstance(artifact, str) and artifact.strip():
                    path = Path(artifact)
                    if not path.is_absolute():
                        path = repo / path
                    if not path.exists():
                        warnings.append(f"Supporting artifact not found: {artifact}")

    # 校验 remaining_gaps
    gaps = output.get("remaining_gaps")
    if gaps is not None and not isinstance(gaps, list):
        warnings.append("remaining_gaps should be a list")

    valid = len(errors) == 0

    return {
        "valid": valid,
        "level": level,
        "reasoning": reasoning,
        "errors": errors,
        "warnings": warnings,
    }


def parse_reviewer_json_output(text: str, repo: Path | None = None) -> dict[str, Any]:
    """解析 Reviewer JSON 输出。

    返回：
    - parsed: bool - 是否成功解析
    - output: dict | None - 解析后的输出
    - validation: dict | None - 校验结果
    - error: str | None - 解析错误
    """
    if not text or not text.strip():
        return {
            "parsed": False,
            "output": None,
            "validation": None,
            "error": "Empty output",
        }

    # 尝试提取 JSON
    json_text = text.strip()

    # 如果不是以 { 开头，尝试提取 JSON 块
    if not json_text.startswith("{"):
        # 尝试提取 ```json ... ``` 块
        json_match = re.search(r"```(?:json)?\s*\n([\s\S]*?)\n```", json_text)
        if json_match:
            json_text = json_match.group(1).strip()
        else:
            # 尝试找到第一个 { 和最后一个 }
            start = json_text.find("{")
            end = json_text.rfind("}")
            if start != -1 and end != -1 and end > start:
                json_text = json_text[start:end + 1]

    try:
        output = json.loads(json_text)
    except json.JSONDecodeError as e:
        return {
            "parsed": False,
            "output": None,
            "validation": None,
            "error": f"JSON parse error: {e}",
        }

    if not isinstance(output, dict):
        return {
            "parsed": False,
            "output": None,
            "validation": None,
            "error": "Output is not a JSON object",
        }

    validation = validate_reviewer_output(output, repo)

    return {
        "parsed": True,
        "output": output,
        "validation": validation,
        "error": None,
    }


def build_reviewer_output(
    level: str,
    reasoning: str,
    *,
    supporting_artifacts: list[str] | None = None,
    remaining_gaps: list[str] | None = None,
    next_iteration_guidance: str | None = None,
    review_summary: str | None = None,
    verdict: str | None = None,
) -> dict[str, Any]:
    """构建 Reviewer 输出。

    注意：此函数只用于代码层构建输出，不是 Reviewer 的判断逻辑。
    Reviewer 的判断逻辑应该由大模型完成。
    """
    output = {
        "current_reproduction_level": normalize_level(level),
        "level_reasoning": reasoning,
    }

    if supporting_artifacts is not None:
        output["supporting_artifacts"] = supporting_artifacts

    if remaining_gaps is not None:
        output["remaining_gaps"] = remaining_gaps

    if next_iteration_guidance is not None:
        output["next_iteration_guidance"] = next_iteration_guidance

    if review_summary is not None:
        output["review_summary"] = review_summary

    if verdict is not None:
        output["verdict"] = verdict

    return output


def collect_evidence_artifacts(repo: Path) -> dict[str, Any]:
    """收集证据产物，供 Reviewer 参考。

    注意：此函数只收集产物信息，不判断等级。
    返回的等级字段是可选的辅助信息，不得作为正式等级。
    """
    from r2a.core.paths import artifact_dir

    artifacts: list[dict[str, Any]] = []

    # 收集 .r2a/results 下的文件
    results_dir = artifact_dir(repo) / "results"
    if results_dir.exists():
        for path in sorted(results_dir.rglob("*")):
            if path.is_file():
                artifact = _summarize_artifact(path)
                if artifact:
                    artifacts.append(artifact)

    # 收集报告文件
    reports_dir = artifact_dir(repo)
    if reports_dir.exists():
        for name in ["REVIEW_REPORT.md", "CHECK_REPORT.md", "EXECUTION_REPORT.md", "TASK_SPEC.md"]:
            path = reports_dir / name
            if path.exists():
                artifact = _summarize_artifact(path)
                if artifact:
                    artifacts.append(artifact)

    return {
        "artifacts": artifacts,
        "count": len(artifacts),
    }


def _summarize_artifact(path: Path) -> dict[str, Any] | None:
    """摘要单个产物文件。"""
    try:
        stat = path.stat()
    except OSError:
        return None

    suffix = path.suffix.lower()

    # 确定类型
    kind = "other"
    if suffix == ".csv":
        kind = "metrics" if any(k in path.name.lower() for k in ["metric", "result", "recall", "qps"]) else "data"
    elif suffix == ".json":
        kind = "data"
    elif suffix == ".md":
        kind = "report"
    elif suffix in [".log", ".txt"]:
        kind = "log"
    elif suffix in [".py", ".sh", ".bash"]:
        kind = "code"
    elif suffix in [".png", ".jpg", ".jpeg", ".pdf", ".svg"]:
        kind = "plot"

    # 读取内容摘要（限制大小）
    summary = ""
    if stat.st_size < 10000:  # 只读取小文件
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
            # 取前 500 字符作为摘要
            summary = text[:500].strip()
            if len(text) > 500:
                summary += "..."
        except Exception:
            pass

    return {
        "path": str(path),
        "name": path.name,
        "kind": kind,
        "readable": True,
        "size_bytes": stat.st_size,
        "summary": summary,
    }


def build_level_semantics_prompt() -> str:
    """构建等级语义说明，用于 Reviewer Prompt。"""
    lines = ["# Reproduction Level Definitions (L0-L6)", ""]
    lines.append("You are the official judge of reproduction level. Your judgment is authoritative.")
    lines.append("")
    lines.append("## Level Definitions")
    lines.append("")

    for level, label in LEVEL_LABELS.items():
        semantics = LEVEL_SEMANTICS[level]
        lines.append(f"### {label}")
        lines.append(f"{semantics}")
        lines.append("")

    lines.append("## Your Responsibilities")
    lines.append("")
    lines.append("1. **Judge based on real evidence**: Read actual file contents, not just check file existence.")
    lines.append("2. **Support full L0-L6**: Do not cap levels at L4.")
    lines.append("3. **Explain your reasoning**: Provide clear reasoning for why the level is achieved.")
    lines.append("4. **Identify gaps**: Explain what's missing for higher levels.")
    lines.append("5. **No automatic downgrade**: Do not downgrade based on missing fixed filenames or schemas.")
    lines.append("6. **No automatic upgrade**: Do not upgrade just because certain files exist.")
    lines.append("")
    lines.append("## Output Format")
    lines.append("")
    lines.append("You must output a JSON object with the following fields:")
    lines.append("")
    lines.append("```json")
    lines.append("{")
    lines.append('  "current_reproduction_level": "L0-L6 level",')
    lines.append('  "level_reasoning": "Why this level is achieved and why higher levels are not yet reached",')
    lines.append('  "supporting_artifacts": ["list of file paths that support this judgment"],')
    lines.append('  "remaining_gaps": ["what is missing for higher levels"],')
    lines.append('  "next_iteration_guidance": "specific actionable suggestions for next iteration",')
    lines.append('  "review_summary": "brief summary of this iteration\'s results"')
    lines.append("}")
    lines.append("```")
    lines.append("")
    lines.append("## Important Rules")
    lines.append("")
    lines.append("- `current_reproduction_level` must be one of: L0_project_health through L6_full_or_near_full_reproduction")
    lines.append("- `level_reasoning` must be non-empty and explain your judgment")
    lines.append("- Do NOT use fixed filename rules to determine level")
    lines.append("- Do NOT cap levels at L4 - support full L0-L6 range")
    lines.append("- If evidence is insufficient, use lower level but do not treat it as system failure")
    lines.append("- If you cannot reliably judge, output current_reproduction_level as null and explain why")

    return "\n".join(lines)
