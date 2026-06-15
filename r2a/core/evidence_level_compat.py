"""Evidence level compatibility helpers.

集中处理新旧 Run 的等级读取兼容。

规则：
1. 优先读取 current_reproduction_level（新字段）
2. 只有确认旧 Run 的 Reviewer 已成功执行时，才依次读取兼容字段
3. Reviewer 未执行时返回 None 或 UNASSESSED
4. 不得使用文件推断恢复正式等级
5. 支持完整 L0-L6
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from r2a.core.reviewer_level_judgment import (
    REPRODUCTION_LEVELS,
    LEVEL_INDEX,
    is_valid_level,
    normalize_level,
)


# 新字段：Reviewer 唯一写入的正式等级
CURRENT_LEVEL_KEY = "current_reproduction_level"
CURRENT_LEVEL_ITERATION_KEY = "current_level_iteration"

# 兼容字段（仅用于旧 Run）
COMPAT_LEVEL_KEYS = (
    "achieved_reproduction_level",
    "accepted_evidence_level",
    "reproduction_level",
)

# 不应用于恢复正式等级的字段
FORBIDDEN_FALLBACK_KEYS = (
    "state_reproduction_level",
    "observed_evidence_level",
    "manager_max_level_allowed",
    "max_evidence_level_allowed",
    "max_level_allowed",
    "evidence_observed",
    "evidence_accepted",
)

UNASSESSED = "UNASSESSED"


def read_current_reproduction_level(
    state: dict[str, Any],
    *,
    reviewer_executed: bool | None = None,
) -> str | None:
    """读取当前正式复现等级。

    规则：
    1. 优先读取 current_reproduction_level
    2. 如果新字段存在且非空，直接返回
    3. 如果新字段不存在，检查 Reviewer 是否已执行
    4. 如果 Reviewer 已执行，依次读取兼容字段
    5. 如果 Reviewer 未执行，返回 None
    6. 支持完整 L0-L6

    注意：不使用文件推断恢复等级。
    """
    # 优先读取新字段
    current = str(state.get(CURRENT_LEVEL_KEY, "") or "").strip()
    if current and current != UNASSESSED:
        # 校验是否为合法等级
        if is_valid_level(current):
            return normalize_level(current)

    # 检查 Reviewer 是否已执行
    if reviewer_executed is None:
        reviewer_executed = _check_reviewer_executed(state)

    # Reviewer 未执行，返回 None（新 Run）或 UNASSESSED
    if not reviewer_executed:
        return None if not current else UNASSESSED

    # Reviewer 已执行，读取兼容字段（旧 Run）
    for key in COMPAT_LEVEL_KEYS:
        value = str(state.get(key, "") or "").strip()
        if value and value != UNASSESSED:
            if is_valid_level(value):
                return normalize_level(value)

    # 没有找到有效等级
    return None


def read_current_level_iteration(
    state: dict[str, Any],
    *,
    reviewer_executed: bool | None = None,
) -> int:
    """读取当前等级对应的迭代轮次。

    规则：
    1. 优先读取 current_level_iteration
    2. 如果不存在，返回 0（表示未知）
    """
    iteration = state.get(CURRENT_LEVEL_ITERATION_KEY)
    if iteration is not None:
        try:
            return int(iteration)
        except (TypeError, ValueError):
            pass

    # 检查 Reviewer 是否已执行
    if reviewer_executed is None:
        reviewer_executed = _check_reviewer_executed(state)

    # Reviewer 未执行，返回 0
    if not reviewer_executed:
        return 0

    # 尝试从 iteration 字段读取
    iteration = state.get("iteration")
    if iteration is not None:
        try:
            return int(iteration)
        except (TypeError, ValueError):
            pass

    return 0


def is_reviewer_executed(state: dict[str, Any]) -> bool:
    """检查 Reviewer 是否已成功执行。

    检查条件：
    1. reviewer_executed = True
    2. reviewer_verdict 非空
    3. 或存在 review_report_path / review_feedback_path
    """
    return _check_reviewer_executed(state)


def _check_reviewer_executed(state: dict[str, Any]) -> bool:
    """检查 Reviewer 是否已执行。"""
    # 显式标记
    if state.get("reviewer_executed"):
        return True

    # 有 verdict
    verdict = str(state.get("reviewer_verdict", "") or "").strip()
    if verdict:
        return True

    # 有报告路径
    if state.get("review_report_path") or state.get("latest_review_report_path"):
        return True

    if state.get("review_feedback_path") or state.get("latest_review_feedback_path"):
        return True

    # 有 structured_review_feedback
    if state.get("structured_review_feedback"):
        return True

    return False


def is_new_run(state: dict[str, Any]) -> bool:
    """检查是否为新 Run（Reviewer 尚未执行）。"""
    return not is_reviewer_executed(state)


def get_level_source(state: dict[str, Any]) -> str:
    """获取等级来源。

    返回：
    - "reviewer": Reviewer 已执行并写入等级
    - "legacy": 从兼容字段读取（旧 Run）
    - "unassessed": Reviewer 未执行
    """
    if not is_reviewer_executed(state):
        return "unassessed"

    current = str(state.get(CURRENT_LEVEL_KEY, "") or "").strip()
    if current and current != UNASSESSED:
        return "reviewer"

    # 检查兼容字段
    for key in COMPAT_LEVEL_KEYS:
        value = str(state.get(key, "") or "").strip()
        if value and value != UNASSESSED:
            return "legacy"

    return "unassessed"


def validate_no_forbidden_fallback(state: dict[str, Any]) -> list[str]:
    """验证没有使用禁止的 fallback 字段作为正式等级。

    返回警告列表。
    """
    warnings: list[str] = []

    current = str(state.get(CURRENT_LEVEL_KEY, "") or "").strip()

    for key in FORBIDDEN_FALLBACK_KEYS:
        value = str(state.get(key, "") or "").strip()
        if value and value != UNASSESSED:
            if not current:
                warnings.append(
                    f"Field '{key}' has value '{value}' but should not be used as official level. "
                    f"Use '{CURRENT_LEVEL_KEY}' instead."
                )

    return warnings
